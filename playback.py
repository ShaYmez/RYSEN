#!/usr/bin/env python
#
###############################################################################
#   Copyright (C) 2016-2019  Cortney T. Buffington, N0MJS <n0mjs@me.com> (and Mike Zingman N4IRR)
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software Foundation,
#   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
###############################################################################


# Python modules we need
import sys
from bitarray import bitarray
from time import time, sleep
from importlib import import_module
from random import randint
from setproctitle import setproctitle

# Twisted is pretty important, so I keep it separate
from twisted.internet.protocol import Factory, Protocol
from twisted.protocols.basic import NetstringReceiver
from twisted.internet import reactor, task

# Things we import from the main hblink module
from hblink import HBSYSTEM, systems, hblink_handler, reportFactory, REPORT_OPCODES, config_reports, mk_aliases
from dmr_utils3.utils import bytes_3, bytes_4, int_id, get_alias
from dmr_utils3 import bptc
import config
import log
import const
from const import LC_OPT, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM

# The module needs logging logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)


# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS and Mike Zingman, N4IRR'
__copyright__  = 'Copyright (c) 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Cort Buffington, N0MJS'
__email__      = 'n0mjs@me.com'
__status__     = 'pre-alpha'

# Module gobal variables

# Parrot identity (TG 9990). Group inbound echoes as group 9990→TG9990 so
# MMDVM_Bridge clients tuned to 9990 see a consistent group stream. Private
# inbound still echoes as unit 9990→caller, with LC rewritten to match.
PARROT_SRC = bytes_3(9990)
HBP_UNIT_CALL = 0x40
UNIT_LC_OPT = b'\x03\x00\x20'
PLAYBACK_FRAME_S = 0.06
# MMDVM / hotspots mint a new stream ID about every 10s of one PTT.
# Keep recording when the same radio stays on 9990 across that rollover.
PARROT_STREAM_CONTINUE_S = 1.0
# Play even if the terminator never arrives (TOT, dropped VTERM).
PARROT_IDLE_PLAY_S = 1.25


def parrot_echo_lc_blocks(src, dst, unit_call):
    """FULL + embedded LC for a parrot echo whose HBP header is src→dst."""
    lc = (UNIT_LC_OPT if unit_call else LC_OPT) + dst + src
    return {
        'H_LC': bptc.encode_header_lc(lc),
        'T_LC': bptc.encode_terminator_lc(lc),
        'EMB_LC': bptc.encode_emblc(lc),
    }


def parrot_echo_addresses(unit_call, caller_id):
    """Return (src, dst) for parrot playback. Group echo stays on TG 9990."""
    if unit_call:
        return PARROT_SRC, caller_id
    return PARROT_SRC, PARROT_SRC


def rewrite_parrot_echo_packet(packet, stream_id, seq, src, dst, lc_blocks, unit_call):
    """Rewrite one recorded DMRD frame for parrot playback.

    New stream ID, reset HBP sequence, matching src/dst, group/unit bit, and
    voice LC / embedded LC aligned with the HBP header (MMDVM_Bridge rejects
    mismatched private headers vs group LC and stretches the stream ~2×).
    """
    if not packet or len(packet) < 53:
        return packet
    pkt = bytearray(packet)
    pkt[4] = seq & 0xFF
    pkt[5:8] = src
    pkt[8:11] = dst
    pkt[16:20] = stream_id
    bits = pkt[15]
    if unit_call:
        bits |= HBP_UNIT_CALL
    else:
        bits &= ~HBP_UNIT_CALL
    pkt[15] = bits
    frame_type = (bits & 0x30) >> 4
    dtype_vseq = bits & 0x0F
    dmrbits = bitarray(endian='big')
    dmrbits.frombytes(bytes(pkt[20:53]))
    if len(dmrbits) < 264:
        dmrbits.extend([0] * (264 - len(dmrbits)))
    if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
        dmrbits = lc_blocks['H_LC'][0:98] + dmrbits[98:166] + lc_blocks['H_LC'][98:197]
    elif frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
        dmrbits = lc_blocks['T_LC'][0:98] + dmrbits[98:166] + lc_blocks['T_LC'][98:197]
    elif dtype_vseq in (1, 2, 3, 4):
        dmrbits = dmrbits[0:116] + lc_blocks['EMB_LC'][dtype_vseq] + dmrbits[148:264]
    rewritten = dmrbits.tobytes()
    pkt[20:53] = rewritten[:33]
    return bytes(pkt)


def ensure_parrot_terminator(packets):
    """Add a voice terminator so an idle or rolled-over recording still closes."""
    if not packets:
        return packets
    last = packets[-1]
    if len(last) < 16:
        return packets
    bits = last[15]
    frame_type = (bits & 0x30) >> 4
    dtype_vseq = bits & 0x0F
    if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
        return packets
    term = bytearray(last)
    term[15] = (bits & 0xC0) | (HBPF_DATA_SYNC << 4) | HBPF_SLT_VTERM
    return list(packets) + [bytes(term)]


def build_parrot_echo_packets(packets, unit_call, caller_id, stream_id=None):
    """Return rewritten parrot frames plus the stream/src/dst used."""
    if stream_id is None:
        stream_id = bytes_4(randint(0x00, 0xFFFFFFFF))
    src, dst = parrot_echo_addresses(unit_call, caller_id)
    lc_blocks = parrot_echo_lc_blocks(src, dst, unit_call)
    echoed = [
        rewrite_parrot_echo_packet(pkt, stream_id, seq % 256, src, dst, lc_blocks, unit_call)
        for seq, pkt in enumerate(packets)
    ]
    return echoed, stream_id, src, dst


class playback(HBSYSTEM):

    def __init__(self, _name, _config, _report):
        HBSYSTEM.__init__(self, _name, _config, _report)

        # Status information for the system, TS1 & TS2
        # 1 & 2 are "timeslot"
        # In TX_EMB_LC, 2-5 are burst B-E
        self.STATUS = {
            1: {
                'RX_START':     time(),
                'RX_SEQ':       '\x00',
                'RX_RFS':       '\x00',
                'TX_RFS':       '\x00',
                'RX_STREAM_ID': '\x00',
                'TX_STREAM_ID': '\x00',
                'RX_TGID':      '\x00\x00\x00',
                'TX_TGID':      '\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      const.HBPF_SLT_VTERM,
                'RX_LC':        '\x00',
                'TX_H_LC':      '\x00',
                'TX_T_LC':      '\x00',
                'TX_EMB_LC': {
                    1: '\x00',
                    2: '\x00',
                    3: '\x00',
                    4: '\x00',
                }
                },
            2: {
                'RX_START':     time(),
                'RX_SEQ':       '\x00',
                'RX_RFS':       '\x00',
                'TX_RFS':       '\x00',
                'RX_STREAM_ID': '\x00',
                'TX_STREAM_ID': '\x00',
                'RX_TGID':      '\x00\x00\x00',
                'TX_TGID':      '\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      const.HBPF_SLT_VTERM,
                'RX_LC':        '\x00',
                'TX_H_LC':      '\x00',
                'TX_T_LC':      '\x00',
                'TX_EMB_LC': {
                    1: '\x00',
                    2: '\x00',
                    3: '\x00',
                    4: '\x00',
                }
            }
        }
        self.CALL_DATA = []
        self._record_rf_src = None
        self._record_dst = None
        self._record_unit = False
        self._record_peer = None
        self._record_slot = 2
        self._record_streams = set()
        self._idle_play = None

    def _cancel_idle_play(self):
        idle = getattr(self, '_idle_play', None)
        if idle is not None:
            try:
                if idle.active():
                    idle.cancel()
            except Exception:
                pass
        self._idle_play = None

    def _arm_idle_play(self):
        self._cancel_idle_play()
        try:
            if not reactor.running:
                return
        except Exception:
            return
        self._idle_play = reactor.callLater(PARROT_IDLE_PLAY_S, self._play_idle)

    def _is_same_parrot_over(self, rf_src, dst_id, pkt_time, slot):
        if not self.CALL_DATA:
            return False
        if getattr(self, '_record_rf_src', None) != rf_src:
            return False
        record_dst = getattr(self, '_record_dst', None)
        if record_dst is not None and record_dst != dst_id:
            return False
        last = self.STATUS.get(slot, {}).get('RX_TIME')
        if last is None:
            return False
        return (pkt_time - last) < PARROT_STREAM_CONTINUE_S

    def _seen_parrot_stream(self, stream_id):
        return stream_id in (getattr(self, '_record_streams', None) or ())

    def _mark_recording(self, peer_id, rf_src, dst_id, slot, stream_id,
                        dtype_vseq, pkt_time, call_type):
        self._record_rf_src = rf_src
        self._record_dst = dst_id
        self._record_unit = (call_type == 'unit')
        self._record_peer = peer_id
        self._record_slot = slot
        streams = getattr(self, '_record_streams', None)
        if streams is None:
            streams = set()
            self._record_streams = streams
        streams.add(stream_id)
        self.STATUS[slot]['RX_RFS'] = rf_src
        self.STATUS[slot]['RX_TYPE'] = dtype_vseq
        self.STATUS[slot]['RX_TGID'] = dst_id
        self.STATUS[slot]['RX_TIME'] = pkt_time
        self.STATUS[slot]['RX_STREAM_ID'] = stream_id

    def _play_recording(self, packets, unit_call, echo_dst, peer_id, slot, duration):
        self._cancel_idle_play()
        packets = ensure_parrot_terminator(packets)
        sleep(2)
        echoed, new_stream_id, echo_src, echo_tg = build_parrot_echo_packets(
            packets, unit_call, echo_dst)
        logger.info(
            '(%s) *START  PLAYBACK* STREAM ID: %s SUB: %s (%s) REPEATER: %s (%s) '
            'TGID %s (%s), TS %s, Duration: %s, %s',
            self._system, int_id(new_stream_id),
            get_alias(echo_src, subscriber_ids), int_id(echo_src),
            get_alias(peer_id, peer_ids), int_id(peer_id),
            get_alias(echo_tg, talkgroup_ids), int_id(echo_tg),
            slot, duration, 'unit' if unit_call else 'group')
        for frame in echoed:
            self.send_system(frame)
            sleep(PLAYBACK_FRAME_S)
        logger.info('(%s) *END    PLAYBACK* STREAM ID: %s',
                    self._system, int_id(new_stream_id))

    def _finish_recording(self, extra=None, reason='terminator'):
        packets = list(self.CALL_DATA or [])
        if extra:
            packets.append(extra)
        if not packets:
            return
        duration = time() - self.STATUS.get('RX_START', time())
        slot = getattr(self, '_record_slot', None) or 2
        stream_id = self.STATUS.get(slot, {}).get(
            'RX_STREAM_ID', b'\x00\x00\x00\x00')
        logger.info('(%s) *END   RECORDING* STREAM ID: %s (%s)',
                    self._system, int_id(stream_id), reason)
        echo_dst = getattr(self, '_record_rf_src', None)
        peer_id = getattr(self, '_record_peer', None) or b'\x00\x00\x00\x00'
        unit_call = bool(getattr(self, '_record_unit', False))
        self.CALL_DATA = []
        self._record_rf_src = None
        self._record_dst = None
        self._record_streams = set()
        self._play_recording(packets, unit_call, echo_dst, peer_id, slot, duration)

    def _play_idle(self):
        self._idle_play = None
        if self.CALL_DATA:
            self._finish_recording(reason='idle')

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):
        pkt_time = time()
        _bits = _data[15]
        _is_vterm = (
            _frame_type == const.HBPF_DATA_SYNC
            and _dtype_vseq == const.HBPF_SLT_VTERM)

        if _call_type not in ('group', 'unit'):
            return

        if _stream_id != self.STATUS[_slot]['RX_STREAM_ID']:
            if self._is_same_parrot_over(_rf_src, _dst_id, pkt_time, _slot):
                if _is_vterm and self._seen_parrot_stream(_stream_id):
                    logger.debug(
                        '(%s) Ignoring stale VTERM for stream %s while recording %s',
                        self._system, int_id(_stream_id),
                        int_id(self.STATUS[_slot]['RX_STREAM_ID']))
                    return
                logger.info(
                    '(%s) *CONTINUE RECORDING* STREAM ID: %s (was %s) SUB: %s',
                    self._system, int_id(_stream_id),
                    int_id(self.STATUS[_slot]['RX_STREAM_ID']), int_id(_rf_src))
                self.CALL_DATA.append(_data)
                self._mark_recording(
                    _peer_id, _rf_src, _dst_id, _slot, _stream_id,
                    _dtype_vseq, pkt_time, _call_type)
                if _is_vterm:
                    self._finish_recording()
                else:
                    self._arm_idle_play()
                return

            # A missed terminator used to leave the previous over in
            # CALL_DATA, so the next unkey played every stuck recording.
            if self.CALL_DATA:
                try:
                    if reactor.running:
                        self._finish_recording(reason='new-stream')
                except Exception:
                    pass
            self._cancel_idle_play()
            self.CALL_DATA = []
            self._record_streams = set()
            self.STATUS['RX_START'] = pkt_time
            self._record_rf_src = _rf_src
            self._record_dst = _dst_id
            logger.info('(%s) *START RECORDING* STREAM ID: %s SUB: %s (%s) REPEATER: %s (%s) TGID %s (%s), TS %s', \
                              self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
            self.CALL_DATA.append(_data)
            self._mark_recording(
                _peer_id, _rf_src, _dst_id, _slot, _stream_id,
                _dtype_vseq, pkt_time, _call_type)
            # Slot RX_TYPE starts as (and returns to) VTERM. Leave it set
            # or a header-plus-terminator kerchunk never satisfies the
            # playback check below.
            if _is_vterm:
                # This branch just opened CALL_DATA with one frame. A lone
                # terminator is a late VTERM after a finished over, not a
                # kerchunk — those arrive as header then terminator.
                logger.debug(
                    '(%s) Ignoring lone VTERM for stream %s with no recording',
                    self._system, int_id(_stream_id))
                self.CALL_DATA = []
                self._record_rf_src = None
                self._record_dst = None
                self._cancel_idle_play()
                return
            self._arm_idle_play()
            return

        # Final actions - Is this a voice terminator?
        if _is_vterm and (self.STATUS[_slot]['RX_TYPE'] != const.HBPF_SLT_VTERM) and (self.CALL_DATA):
            self.CALL_DATA.append(_data)
            self._mark_recording(
                _peer_id, _rf_src, _dst_id, _slot, _stream_id,
                _dtype_vseq, pkt_time, _call_type)
            self._finish_recording()
            return

        if self.CALL_DATA:
            self.CALL_DATA.append(_data)
        self._mark_recording(
            _peer_id, _rf_src, _dst_id, _slot, _stream_id,
            _dtype_vseq, pkt_time, _call_type)
        self._arm_idle_play()


#************************************************
#      MAIN PROGRAM LOOP STARTS HERE
#************************************************

if __name__ == '__main__':
    
    import argparse
    import sys
    import os
    import signal
    from dmr_utils3.utils import try_download, mk_id_dict
    
    #Set process title early
    setproctitle(__file__)
    
    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually hblink.cfg)')
    parser.add_argument('-l', '--logging', action='store', dest='LOG_LEVEL', help='Override config file logging level.')
    cli_args = parser.parse_args()

    # Ensure we have a path for the config file, if one wasn't specified, then use the default (top of file)
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/hblink.cfg'

    # Call the external routine to build the configuration dictionary
    CONFIG = config.build_config(cli_args.CONFIG_FILE)
    
    # Start the system logger
    if cli_args.LOG_LEVEL:
        CONFIG['LOGGER']['LOG_LEVEL'] = cli_args.LOG_LEVEL
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('\n\nCopyright (c) 2013, 2014, 2015, 2016, 2018, 2019\n\tThe Founding Members of the K0USY Group. All rights reserved.\n')
    logger.debug('Logging system started, anything from here on gets logged')
    
    # Set up the signal handler
    def sig_handler(_signal, _frame):
        logger.info('SHUTDOWN: HBROUTER IS TERMINATING WITH SIGNAL %s', str(_signal))
        hblink_handler(_signal, _frame)
        logger.info('SHUTDOWN: ALL SYSTEM HANDLERS EXECUTED - STOPPING REACTOR')
        reactor.stop()
        
    # Set signal handers so that we can gracefully exit if need be
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, sig_handler)
    
    #ID ALIAS CREATION
    #Download
    #if CONFIG['ALIASES']['TRY_DOWNLOAD'] == True:
    #    Try updating peer aliases file
    #    result = try_download(CONFIG['ALIASES']['PATH'], CONFIG['ALIASES']['PEER_FILE'], CONFIG['ALIASES']['PEER_URL'], #CONFIG['ALIASES']['STALE_TIME'])
    #    logger.info(result)
    #    Try updating subscriber aliases file
    #    result = try_download(CONFIG['ALIASES']['PATH'], CONFIG['ALIASES']['SUBSCRIBER_FILE'], CONFIG['ALIASES']['SUBSCRIBER_URL'], CONFIG['ALIASES']['STALE_TIME'])
    #    logger.info(result)
        
    # Create the name-number mapping dictionaries
    #peer_ids, subscriber_ids, talkgroup_ids = mk_aliases(CONFIG)
    
    peer_ids = {}
    subscriber_ids = {}
    talkgroup_ids = {}
        
    # INITIALIZE THE REPORTING LOOP
    report_server = config_reports(CONFIG, reportFactory)    
    
    # HBlink instance creation
    logger.info('HBlink \'playback.py\' (c) 2017-2019 Cort Buffington, N0MJS & Mike Zingman, N4IRR -- SYSTEM STARTING...')
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
                logger.critical('%s FATAL: Instance is mode \'OPENBRIDGE\', \n\t\t...Which would be tragic for playback, since it carries multiple call\n\t\tstreams simultaneously. playback.py onlyl works with MMDVM-based systems', system)
                sys.exit('playback.py cannot function with systems that are not MMDVM devices. System {} is configured as an OPENBRIDGE'.format(system))
            else:
                systems[system] = playback(system, CONFIG, report_server)
            reactor.listenUDP(CONFIG['SYSTEMS'][system]['PORT'], systems[system], interface=CONFIG['SYSTEMS'][system]['IP'])
            logger.debug('%s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])

    reactor.run()
