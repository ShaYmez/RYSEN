#!/usr/bin/env python
#
###############################################################################
#   Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
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

'''
This application, in conjunction with it's rule file (rules.py) will
work like a "conference bridge". This is similar to what most hams think of as a
reflector. You define conference bridges and any system joined to that conference
bridge will both receive traffic from, and send traffic to any other system
joined to the same conference bridge. It does not provide end-to-end connectivity
as each end system must individually be joined to a conference bridge (a name
you create in the configuration file) to pass traffic.

This program currently only works with group voice calls.
'''

# Python modules we need
import sys
from bitarray import bitarray
from time import time
import importlib.util

# Twisted is pretty important, so I keep it separate
from twisted.internet.protocol import Factory, Protocol
from twisted.protocols.basic import NetstringReceiver
from twisted.internet import reactor, task

# Things we import from the main hblink module
from hblink import HBSYSTEM, OPENBRIDGE, systems, hblink_handler, reportFactory, REPORT_OPCODES, mk_aliases
from dmr_utils3.utils import bytes_3, int_id, get_alias
from dmr_utils3 import decode, bptc, const
import config
import log
from const import *
from bridge_helpers import (
    dmr_seq_delta,
    earliest_obp_owner,
    harden_obp_stub,
)

# Stuff for socket reporting
import pickle
# The module needs logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)


# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS'
__copyright__  = 'Copyright (c) 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Cort Buffington, N0MJS'
__email__      = 'n0mjs@me.com'

# Module gobal variables
_HBP_STREAM_CLAIMS = {}
_HBP_CLAIM_TIMEOUT_S = 1.0
_OPENBRIDGE_SYSTEMS = set()
_TERM_TOMBSTONES = {}
_TERM_TOMBSTONE_TTL_S = 5.0


def _active_hbp_stream_claim(stream_id, rf_src, now):
    key = (stream_id, rf_src)
    claim = _HBP_STREAM_CLAIMS.get(key)
    if claim is not None and now - claim[2] >= _HBP_CLAIM_TIMEOUT_S:
        _HBP_STREAM_CLAIMS.pop(key, None)
        return None
    return claim


def _set_hbp_stream_claim(stream_id, rf_src, system, slot, now, terminal=False):
    key = (stream_id, rf_src)
    if terminal:
        current = _HBP_STREAM_CLAIMS.get(key)
        if current is not None and current[0] == system:
            _HBP_STREAM_CLAIMS.pop(key, None)
        return
    _HBP_STREAM_CLAIMS[key] = (system, slot, now)


def _is_tombstoned_term(key, data, now, sequence_expected=False):
    if sequence_expected:
        return False
    tombstone = _TERM_TOMBSTONES.get(key)
    if tombstone is None:
        return False
    if now - tombstone[1] > _TERM_TOMBSTONE_TTL_S:
        _TERM_TOMBSTONES.pop(key, None)
        return False
    return tombstone[0] == data


def _remember_term(key, data, now):
    _TERM_TOMBSTONES[key] = (data, now)
    if len(_TERM_TOMBSTONES) > 2048:
        oldest = sorted(
            _TERM_TOMBSTONES,
            key=lambda old_key: _TERM_TOMBSTONES[old_key][1])
        for old_key in oldest[:-2048]:
            _TERM_TOMBSTONES.pop(old_key, None)

# Timed loop used for reporting HBP status
#
# REPORT BASED ON THE TYPE SELECTED IN THE MAIN CONFIG FILE
def config_reports(_config, _factory):
    if True: #_config['REPORTS']['REPORT']:
        def reporting_loop(logger, _server):
            logger.debug('(REPORT) Periodic reporting loop started')
            _server.send_config()
            _server.send_bridge()

        logger.info('(REPORT) HBlink TCP reporting server configured')

        report_server = _factory(_config)
        report_server.clients = []
        reactor.listenTCP(_config['REPORTS']['REPORT_PORT'], report_server)

        reporting = task.LoopingCall(reporting_loop, logger, report_server)
        reporting.start(_config['REPORTS']['REPORT_INTERVAL'])

    return report_server


# Import Bridging rules
# Note: A stanza *must* exist for any MASTER or CLIENT configured in the main
# configuration file and listed as "active". It can be empty,
# but it has to exist.
def make_bridges(_rules):
    # Convert integer GROUP ID numbers from the config into hex strings
    # we need to send in the actual data packets.
    for _bridge in _rules:
        for _system in _rules[_bridge]:
            if _system['SYSTEM'] not in CONFIG['SYSTEMS']:
                sys.exit('ERROR: Conference bridge "{}" references a system named "{}" that is not enabled in the main configuration'.format(_bridge, _system['SYSTEM']))

            _system['TGID']       = bytes_3(_system['TGID'])
            for i, e in enumerate(_system['ON']):
                _system['ON'][i]  = bytes_3(_system['ON'][i])
            for i, e in enumerate(_system['OFF']):
                _system['OFF'][i] = bytes_3(_system['OFF'][i])
            _system['TIMEOUT']    = _system['TIMEOUT']*60
            if _system['ACTIVE'] == True:
                _system['TIMER']  = time() + _system['TIMEOUT']
            else:
                _system['TIMER']  = time()
    return _rules


# Run this every minute for rule timer updates
def rule_timer_loop():
    logger.debug('(ROUTER) routerHBP Rule timer loop started')
    _now = time()

    for _bridge in BRIDGES:
        for _system in BRIDGES[_bridge]:
            if _system['TO_TYPE'] == 'ON':
                if _system['ACTIVE'] == True:
                    if _system['TIMER'] < _now:
                        _system['ACTIVE'] = False
                        logger.info('(ROUTER) Conference Bridge TIMEOUT: DEACTIVATE System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                    else:
                        timeout_in = _system['TIMER'] - _now
                        logger.info('(ROUTER) Conference Bridge ACTIVE (ON timer running): System: %s Bridge: %s, TS: %s, TGID: %s, Timeout in: %.2fs,', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']),  timeout_in)
                elif _system['ACTIVE'] == False:
                    logger.debug('(ROUTER) Conference Bridge INACTIVE (no change): System: %s Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
            elif _system['TO_TYPE'] == 'OFF':
                if _system['ACTIVE'] == False:
                    if _system['TIMER'] < _now:
                        _system['ACTIVE'] = True
                        logger.info('(ROUTER) Conference Bridge TIMEOUT: ACTIVATE System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                    else:
                        timeout_in = _system['TIMER'] - _now
                        logger.info('(ROUTER) Conference Bridge INACTIVE (OFF timer running): System: %s Bridge: %s, TS: %s, TGID: %s, Timeout in: %.2fs,', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']),  timeout_in)
                elif _system['ACTIVE'] == True:
                    logger.debug('(ROUTER) Conference Bridge ACTIVE (no change): System: %s Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
            else:
                logger.debug('(ROUTER) Conference Bridge NO ACTION: System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))

    if CONFIG['REPORTS']['REPORT']:
        report_server.send_clients(b'bridge updated')


## run this every 10 seconds to trim orphaned stream ids
def stream_trimmer_loop():
    logger.debug('(ROUTER) Trimming inactive stream IDs from system lists')
    _now = time()
    for _claim_key, _claim in list(_HBP_STREAM_CLAIMS.items()):
        if _now - _claim[2] >= _HBP_CLAIM_TIMEOUT_S:
            _HBP_STREAM_CLAIMS.pop(_claim_key, None)
    for _term_key, _term in list(_TERM_TOMBSTONES.items()):
        if _now - _term[1] > _TERM_TOMBSTONE_TTL_S:
            _TERM_TOMBSTONES.pop(_term_key, None)

    for system in systems:
        # HBP systems, master and peer
        if CONFIG['SYSTEMS'][system]['MODE'] != 'OPENBRIDGE':
            for slot in range(1,3):
                _slot  = systems[system].STATUS[slot]

                # RX slot check
                if _slot['RX_TYPE'] != HBPF_SLT_VTERM and _slot['RX_TIME'] <  _now - 5:
                    _slot['RX_TYPE'] = HBPF_SLT_VTERM
                    logger.info('(%s) *TIME OUT*  RX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f', \
                        system, int_id(_slot['RX_STREAM_ID']), int_id(_slot['RX_RFS']), int_id(_slot['RX_TGID']), slot, _slot['RX_TIME'] - _slot['RX_START'])
                    if CONFIG['REPORTS']['REPORT']:
                        systems[system]._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(_slot['RX_STREAM_ID']), int_id(_slot['RX_PEER']), int_id(_slot['RX_RFS']), slot, int_id(_slot['RX_TGID']), _slot['RX_TIME'] - _slot['RX_START']).encode(encoding='utf-8', errors='ignore'))
                #Null stream_id - for loop control 
                if _slot['RX_TIME'] < _now - 60:
                    _slot['RX_STREAM_ID'] = b'\x00'

                # TX slot check
                if _slot['TX_TYPE'] != HBPF_SLT_VTERM and _slot['TX_TIME'] <  _now - 5:
                    _slot['TX_TYPE'] = HBPF_SLT_VTERM
                    logger.info('(%s) *TIME OUT*  TX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f', \
                        system, int_id(_slot['TX_STREAM_ID']), int_id(_slot['TX_RFS']), int_id(_slot['TX_TGID']), slot, _slot['TX_TIME'] - _slot['TX_START'])
                    if CONFIG['REPORTS']['REPORT']:
                        systems[system]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(_slot['TX_STREAM_ID']), int_id(_slot['TX_PEER']), int_id(_slot['TX_RFS']), slot, int_id(_slot['TX_TGID']), _slot['TX_TIME'] - _slot['TX_START']).encode(encoding='utf-8', errors='ignore'))

        # OBP systems
        # We can't delete items from a dicationry that's being iterated, so we have to make a temporarly list of entrys to remove later
        if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
            remove_list = []
            fin_list = []
            for stream_id in systems[system].STATUS:
                
                #if stream already marked as finished, just remove it
                if '_fin' in systems[system].STATUS[stream_id] and systems[system].STATUS[stream_id]['LAST'] < _now - 180:
                    logger.info('(%s) *FINISHED STREAM* STREAM ID: %s',system, int_id(stream_id))
                    fin_list.append(stream_id)
                    continue
                
                #try:
                if '_to' not in systems[system].STATUS[stream_id] and '_fin' not in systems[system].STATUS[stream_id] and systems[system].STATUS[stream_id]['LAST'] < _now - 5:
                    _stream = systems[system].STATUS[stream_id]
                    _sysconfig = CONFIG['SYSTEMS'][system]
                    #systems[system].STATUS[stream_id]['_fin'] = True
                    logger.info('(%s) *TIME OUT*   STREAM ID: %s SUB: %s PEER: %s TGID: %s TS 1 Duration: %.2f', \
                        system, int_id(stream_id), get_alias(int_id(_stream['RFS']), subscriber_ids), get_alias(int_id(_sysconfig['NETWORK_ID']), peer_ids), get_alias(int_id(_stream['TGID']), talkgroup_ids), _stream['LAST'] - _stream['START'])
                    if CONFIG['REPORTS']['REPORT']:
                            systems[system]._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(stream_id), int_id(_sysconfig['NETWORK_ID']), int_id(_stream['RFS']), 1, int_id(_stream['TGID']), _stream['LAST'] - _stream['START']).encode(encoding='utf-8', errors='ignore'))
                    systems[system].STATUS[stream_id]['_to'] = True
                    continue
                #except:
                    #logger.warning("(%s) Keyerror - stream trimmer Stream ID: %s",system,stream_id)
                    #systems[system].STATUS[stream_id]['LAST'] = _now
                    #continue

                    
                try:
                    if systems[system].STATUS[stream_id]['LAST'] < _now - 180:
                        remove_list.append(stream_id)
                except:
                    logger.warning("(%s) Keyerror - stream trimmer Stream ID: %s",system,stream_id)
                    systems[system].STATUS[stream_id]['LAST'] = _now
                    continue
                
            #remove finished     
            for stream_id in fin_list:
                removed = systems[system].STATUS.pop(stream_id)
                
            for stream_id in remove_list:
                if stream_id in systems[system].STATUS:
                    _stream = systems[system].STATUS[stream_id]
                    _sysconfig = CONFIG['SYSTEMS'][system]
                    
                    removed = systems[system].STATUS.pop(stream_id)
                
                    try:
                        _bcsq_remove = []
                        for tgid in _sysconfig['_bcsq']:
                            if _sysconfig['_bcsq'][tgid] == stream_id:
                                _bcsq_remove.append(tgid)
                        for bcrm in _bcsq_remove:
                            removed = _sysconfig['_bcsq'].pop(bcrm)
                    except KeyError:
                        pass
                else:
                    logger.error('(%s) Attemped to remove OpenBridge Stream ID %s not in the Stream ID list: %s', system, int_id(stream_id), [id for id in systems[system].STATUS])

class routerOBP(OPENBRIDGE):

    def __init__(self, _name, _config, _report):
        OPENBRIDGE.__init__(self, _name, _config, _report)
        self.STATUS = {}

        #Store last sequence number
        self._lastSeq = False
        

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data,_hash,_hops = b'', _source_server = b'\x00\x00\x00\x00', _ber = b'\x00', _rssi = b'\x00', _source_rptr = b'\x00\x00\x00\x00'):
        pkt_time = time()
        dmrpkt = _data[20:53]
        _bits = _data[15]

        if _call_type == 'group':
            _obp_is_vterm = (
                _frame_type == HBPF_DATA_SYNC
                and _dtype_vseq == HBPF_SLT_VTERM)
            _term_key = (
                self._system, _slot, _stream_id,
                _rf_src, _peer_id, _dst_id)
            _obp_active = self.STATUS.get(_stream_id)
            _obp_sequence_expected = (
                _obp_active is not None
                and dmr_seq_delta(
                    _seq, _obp_active.get('lastSeq')) == 1)
            if (_obp_is_vterm
                    and _is_tombstoned_term(
                        _term_key, _data, pkt_time,
                        _obp_sequence_expected)):
                return
            # Is this a new call stream?
            _obp_previous = self.STATUS.get(_stream_id)
            _obp_idle = (
                _obp_previous is not None
                and pkt_time - _obp_previous.get(
                    'LAST', _obp_previous.get('START', 0)) >= STREAM_TO)
            _obp_new_stream = (
                _obp_previous is None
                or (_frame_type == HBPF_DATA_SYNC
                    and _dtype_vseq == HBPF_SLT_VHEAD
                    and (_obp_previous.get('_fin') or _obp_idle))
                or (_obp_idle and _dtype_vseq != HBPF_SLT_VTERM))
            if _obp_new_stream:
                # This is a new call stream
                self.STATUS[_stream_id] = {
                    'START':     pkt_time,
                    'CONTENTION':False,
                    'RFS':       _rf_src,
                    'TGID':      _dst_id,
                    '1ST':       pkt_time,
                    'lastSeq': False,
                    'lastData': False,
                }

                # If we can, use the LC from the voice header as to keep all options intact
                if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                    decoded = decode.voice_head_term(dmrpkt)
                    self.STATUS[_stream_id]['LC'] = decoded['LC']

                # If we don't have a voice header then don't wait to decode the Embedded LC
                # just make a new one from the HBP header. This is good enough, and it saves lots of time
                else:
                    self.STATUS[_stream_id]['LC'] = LC_OPT + _dst_id + _rf_src


                logger.info('(%s) *CALL START* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                if CONFIG['REPORTS']['REPORT']:
                    self._report.send_bridgeEvent('GROUP VOICE,START,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
            else:
                # A stream first sent outbound creates a partial target STATUS.
                # Complete it before the same stream returns through this OBP.
                harden_obp_stub(
                    self.STATUS[_stream_id], pkt_time,
                    LC_OPT + _dst_id + _rf_src)
                
                #Finished stream handling#
                if '_fin' in self.STATUS[_stream_id]:
                    if '_finlog' not in self.STATUS[_stream_id]:
                        logger.warning("(%s) OBP *LoopControl* STREAM ID: %s ALREADY FINISHED FROM THIS SOURCE, IGNORING",self._system, int_id(_stream_id))
                    self.STATUS[_stream_id]['_finlog'] = True
                    return
                
                #LoopControl#
                _hbp_claim = _active_hbp_stream_claim(
                    _stream_id, _rf_src, pkt_time)
                if _hbp_claim is not None:
                    if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']:
                        logger.warning(
                            '(%s) OBP *LoopControl* FIRST HBP: %s, STREAM ID: %s, '
                            'TG: %s, TS: %s, IGNORE THIS SOURCE',
                            self._system, _hbp_claim[0], int_id(_stream_id),
                            int_id(_dst_id), _hbp_claim[1])
                        self.STATUS[_stream_id]['LOOPLOG'] = True
                    self.STATUS[_stream_id]['LAST'] = pkt_time
                    return
                # Include this ingress in the election. Excluding it lets two
                # mirrored OBP links each select the other and suppress both.
                fi = earliest_obp_owner(
                    _OPENBRIDGE_SYSTEMS, systems, _stream_id,
                    _dst_id, _rf_src, pkt_time, STREAM_TO)
                if fi and self._system != fi:
                    if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']:
                        logger.warning("(%s) OBP *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE",self._system, fi, int_id(_stream_id), int_id(_dst_id))
                        self.STATUS[_stream_id]['LOOPLOG'] = True
                    self.STATUS[_stream_id]['LAST'] = pkt_time

                    if CONFIG['SYSTEMS'][self._system]['ENHANCED_OBP'] and '_bcsq' not in self.STATUS[_stream_id]:
                        systems[self._system].send_bcsq(_dst_id,_stream_id)
                        self.STATUS[_stream_id]['_bcsq'] = True
                    return

                
                #Duplicate handling#
                _seq_delta = dmr_seq_delta(
                    _seq, self.STATUS[_stream_id]['lastSeq'])
                if (_seq_delta is not None and _seq_delta > 127
                        and pkt_time - self.STATUS[_stream_id]['LAST'] >= 1.0):
                    _seq_delta = None
                #Duplicate complete packet
                if self.STATUS[_stream_id]['lastData'] and self.STATUS[_stream_id]['lastData'] == _data and _seq > 1:
                    logger.warning("(%s) *PacketControl* last packet is a complete duplicate of the previous one, disgarding. Stream ID:, %s TGID: %s",self._system,int_id(_stream_id),int_id(_dst_id))
                    return
                #Handle inbound duplicates
                if _seq_delta == 0:
                    logger.warning("(%s) *PacketControl* Duplicate sequence number %s, disgarding. Stream ID:, %s TGID: %s",self._system,_seq,int_id(_stream_id),int_id(_dst_id))
                    return
                #Inbound out-of-order packets
                if _seq_delta is not None and _seq_delta > 127:
                    logger.warning("%s) *PacketControl* Out of order packet - last SEQ: %s, this SEQ: %s,  disgarding. Stream ID:, %s TGID: %s ",self._system,self.STATUS[_stream_id]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id))
                    return
                #Inbound missed packets
                if _seq_delta is not None and 1 < _seq_delta <= 127:
                    logger.warning("(%s) *PacketControl* Missed packet(s) - last SEQ: %s, this SEQ: %s. Stream ID:, %s TGID: %s ",self._system,self.STATUS[_stream_id]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id))
            
                #Save this sequence number 
                self.STATUS[_stream_id]['lastSeq'] = _seq
                #Save this packet
                self.STATUS[_stream_id]['lastData'] = _data

            self.STATUS[_stream_id]['LAST'] = pkt_time


            for _bridge in BRIDGES:
                for _system in BRIDGES[_bridge]:

                    if (_system['SYSTEM'] == self._system and _system['TGID'] == _dst_id and _system['TS'] == _slot and _system['ACTIVE'] == True):

                        for _target in BRIDGES[_bridge]:
                            if (_target['SYSTEM'] != self._system) and (_target['ACTIVE']):
                                _target_status = systems[_target['SYSTEM']].STATUS
                                _target_system = self._CONFIG['SYSTEMS'][_target['SYSTEM']]
                                if _target_system['MODE'] == 'OPENBRIDGE':
                                    # Is this a new call stream on the target?
                                    _target_generation_changed = (
                                        _obp_new_stream
                                        or (_stream_id in _target_status
                                            and (_target_status[_stream_id].get('RFS') != _rf_src
                                                 or _target_status[_stream_id].get('TGID') != _dst_id)))
                                    if (_stream_id not in _target_status or _target_generation_changed):
                                        # This is a new call stream on the target
                                        _target_status[_stream_id] = {
                                            'START':     pkt_time,
                                            'CONTENTION':False,
                                            'RFS':       _rf_src,
                                            'TGID':      _dst_id,
                                            'TARGET_LC': {},
                                        }
                                    _target_lc_map = _target_status[_stream_id].setdefault(
                                        'TARGET_LC', {})
                                    if _target['TGID'] not in _target_lc_map:
                                        dst_lc = b''.join([self.STATUS[_stream_id]['LC'][0:3], _target['TGID'], _rf_src])
                                        _target_lc_map[_target['TGID']] = {
                                            'START': pkt_time,
                                            'H_LC': bptc.encode_header_lc(dst_lc),
                                            'T_LC': bptc.encode_terminator_lc(dst_lc),
                                            'EMB_LC': bptc.encode_emblc(dst_lc),
                                        }
                                        logger.info('(%s) Conference Bridge: %s, Call Bridged to OBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                                        if CONFIG['REPORTS']['REPORT']:
                                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))
                                    _target_lc = _target_lc_map[_target['TGID']]

                                    # Record the time of this packet so we can later identify a stale stream
                                    _target_status[_stream_id]['LAST'] = pkt_time
                                    # Clear the TS bit -- all OpenBridge streams are effectively on TS1
                                    _tmp_bits = _bits & ~(1 << 7)

                                    # Assemble transmit HBP packet header
                                    _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                                    # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                                    # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                                    # if _dst_id != rule['DST_GROUP']:
                                    _tx_dmrpkt = dmrpkt
                                    dmrbits = bitarray(endian='big')
                                    dmrbits.frombytes(_tx_dmrpkt)
                                    # Create a voice header packet (FULL LC)
                                    if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                                        dmrbits = _target_lc['H_LC'][0:98] + dmrbits[98:166] + _target_lc['H_LC'][98:197]
                                    # Create a voice terminator packet (FULL LC)
                                    elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                                        dmrbits = _target_lc['T_LC'][0:98] + dmrbits[98:166] + _target_lc['T_LC'][98:197]
                                        if CONFIG['REPORTS']['REPORT']:
                                            call_duration = pkt_time - _target_lc['START']
                                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                                    # Create a Burst B-E packet (Embedded LC)
                                    elif _dtype_vseq in [1,2,3,4]:
                                        dmrbits = dmrbits[0:116] + _target_lc['EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                                    _tx_dmrpkt = dmrbits.tobytes()
                                    _tmp_data = b''.join([_tmp_data, _tx_dmrpkt])

                                else:
                                    # BEGIN CONTENTION HANDLING
                                    #
                                    # The rules for each of the 4 "ifs" below are listed here for readability. The Frame To Send is:
                                    #   From a different group than last RX from this HBSystem, but it has been less than Group Hangtime
                                    #   From a different group than last TX to this HBSystem, but it has been less than Group Hangtime
                                    #   From the same group as the last RX from this HBSystem, but from a different subscriber, and it has been less than stream timeout
                                    #   From the same group as the last TX to this HBSystem, but from a different subscriber, and it has been less than stream timeout
                                    # The "continue" at the end of each means the next iteration of the for loop that tests for matching rules
                                    #
                                    if ((_target['TGID'] != _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < _target_system['GROUP_HANGTIME'])):
                                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                                            self.STATUS[_stream_id]['CONTENTION'] = True
                                            logger.info('(%s) Call not routed to TGID %s, target active or in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                                        continue
                                    if ((_target['TGID'] != _target_status[_target['TS']]['TX_TGID']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < _target_system['GROUP_HANGTIME'])):
                                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                                            self.STATUS[_stream_id]['CONTENTION'] = True
                                            logger.info('(%s) Call not routed to TGID%s, target in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']))
                                        continue
                                    if ((_target['TGID'] == _target_status[_target['TS']]['RX_TGID'])
                                            and _target_status[_target['TS']]['RX_TYPE'] != HBPF_SLT_VTERM
                                            and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < STREAM_TO)):
                                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                                            self.STATUS[_stream_id]['CONTENTION'] = True
                                            logger.info('(%s) Call not routed to TGID%s, matching call already active on target: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                                        continue
                                    if ((_target['TGID'] == _target_status[_target['TS']]['TX_TGID'])
                                            and _target_status[_target['TS']]['TX_TYPE'] != HBPF_SLT_VTERM
                                            and (_rf_src != _target_status[_target['TS']]['TX_RFS'])
                                            and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < STREAM_TO)):
                                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                                            self.STATUS[_stream_id]['CONTENTION'] = True
                                            logger.info('(%s) Call not routed for subscriber %s, call route in progress on target: HBSystem: %s, TS: %s, TGID: %s, SUB: %s', self._system, int_id(_rf_src), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']), int_id(_target_status[_target['TS']]['TX_RFS']))
                                        continue

                                    # Is this a new call stream?
                                    if (_obp_new_stream
                                            or _target_status[_target['TS']]['TX_STREAM_ID'] != _stream_id
                                            or _target_status[_target['TS']]['TX_TGID'] != _target['TGID']
                                            or _target_status[_target['TS']]['TX_RFS'] != _rf_src
                                            or _target_status[_target['TS']]['TX_PEER'] != _peer_id):
                                        # Record the DST TGID and Stream ID
                                        _target_status[_target['TS']]['TX_START'] = pkt_time
                                        _target_status[_target['TS']]['TX_TGID'] = _target['TGID']
                                        _target_status[_target['TS']]['TX_STREAM_ID'] = _stream_id
                                        _target_status[_target['TS']]['TX_RFS'] = _rf_src
                                        _target_status[_target['TS']]['TX_PEER'] = _peer_id
                                        # Generate LCs (full and EMB) for the TX stream
                                        dst_lc = b''.join([self.STATUS[_stream_id]['LC'][0:3], _target['TGID'], _rf_src])
                                        _target_status[_target['TS']]['TX_H_LC'] = bptc.encode_header_lc(dst_lc)
                                        _target_status[_target['TS']]['TX_T_LC'] = bptc.encode_terminator_lc(dst_lc)
                                        _target_status[_target['TS']]['TX_EMB_LC'] = bptc.encode_emblc(dst_lc)
                                        logger.debug('(%s) Generating TX FULL and EMB LCs for HomeBrew destination: System: %s, TS: %s, TGID: %s', self._system, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                                        logger.info('(%s) Conference Bridge: %s, Call Bridged to HBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                                        if CONFIG['REPORTS']['REPORT']:
                                           systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))

                                    # Set other values for the contention handler to test next time there is a frame to forward
                                    _target_status[_target['TS']]['TX_TIME'] = pkt_time
                                    _target_status[_target['TS']]['TX_TYPE'] = _dtype_vseq

                                    # Handle any necessary re-writes for the destination
                                    if _system['TS'] != _target['TS']:
                                        _tmp_bits = _bits ^ 1 << 7
                                    else:
                                        _tmp_bits = _bits

                                    # Assemble transmit HBP packet header
                                    _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                                    # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                                    # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                                    # if _dst_id != rule['DST_GROUP']:
                                    _tx_dmrpkt = dmrpkt
                                    dmrbits = bitarray(endian='big')
                                    dmrbits.frombytes(_tx_dmrpkt)
                                    # Create a voice header packet (FULL LC)
                                    if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                                        dmrbits = _target_status[_target['TS']]['TX_H_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_H_LC'][98:197]
                                    # Create a voice terminator packet (FULL LC)
                                    elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                                        dmrbits = _target_status[_target['TS']]['TX_T_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_T_LC'][98:197]
                                        if CONFIG['REPORTS']['REPORT']:
                                            call_duration = pkt_time - _target_status[_target['TS']]['TX_START']
                                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                                    # Create a Burst B-E packet (Embedded LC)
                                    elif _dtype_vseq in [1,2,3,4]:
                                        dmrbits = dmrbits[0:116] + _target_status[_target['TS']]['TX_EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                                    _tx_dmrpkt = dmrbits.tobytes()
                                    _tmp_data = b''.join([_tmp_data, _tx_dmrpkt, b'\x00\x00']) # Add two bytes of nothing since OBP doesn't include BER & RSSI bytes #_data[53:55]

                                # Transmit the packet to the destination system
                                systems[_target['SYSTEM']].send_system(_tmp_data,_hops,_ber,_rssi,_source_server,_source_rptr)
                                #logger.debug('(%s) Packet routed by bridge: %s to system: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))



            # Final actions - Is this a voice terminator?
            if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM):
                call_duration = pkt_time - self.STATUS[_stream_id]['START']
                logger.info('(%s) *CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, Duration: %.2f', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration)
                if CONFIG['REPORTS']['REPORT']:
                   self._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))
                self.STATUS[_stream_id]['_fin'] = True
                _remember_term(_term_key, _data, pkt_time)
                #removed = self.STATUS.pop(_stream_id)
                #logger.debug('(%s) OpenBridge sourced call stream end, remove terminated Stream ID: %s', self._system, int_id(_stream_id))
                #if not removed:
                    #selflogger.error('(%s) *CALL END*   STREAM ID: %s NOT IN LIST -- THIS IS A REAL PROBLEM', self._system, int_id(_stream_id))

                #Reset sequence number 
                self._lastSeq = False

class routerHBP(HBSYSTEM):

    def __init__(self, _name, _config, _report):
        HBSYSTEM.__init__(self, _name, _config, _report)

        # Status information for the system, TS1 & TS2
        # 1 & 2 are "timeslot"
        # In TX_EMB_LC, 2-5 are burst B-E
        self.STATUS = {
            1: {
                'RX_START':     time(),
                'TX_START':     time(),
                'RX_SEQ':       0,
                'RX_RFS':       b'\x00',
                'TX_RFS':       b'\x00',
                'RX_PEER':      b'\x00',
                'TX_PEER':      b'\x00',
                'RX_STREAM_ID': b'\x00',
                'TX_STREAM_ID': b'\x00',
                'RX_TGID':      b'\x00\x00\x00',
                'TX_TGID':      b'\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      HBPF_SLT_VTERM,
                'TX_TYPE':      HBPF_SLT_VTERM,
                'RX_LC':        b'\x00',
                'TX_H_LC':      b'\x00',
                'TX_T_LC':      b'\x00',
                'TX_EMB_LC': {
                    1: b'\x00',
                    2: b'\x00',
                    3: b'\x00',
                    4: b'\x00',
                    },
                'lastSeq': False,
                'lastData': False
                
                },
            2: {
                'RX_START':     time(),
                'TX_START':     time(),
                'RX_SEQ':       0,
                'RX_RFS':       b'\x00',
                'TX_RFS':       b'\x00',
                'RX_PEER':      b'\x00',
                'TX_PEER':      b'\x00',
                'RX_STREAM_ID': b'\x00',
                'TX_STREAM_ID': b'\x00',
                'RX_TGID':      b'\x00\x00\x00',
                'TX_TGID':      b'\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      HBPF_SLT_VTERM,
                'TX_TYPE':      HBPF_SLT_VTERM,
                'RX_LC':        b'\x00',
                'TX_H_LC':      b'\x00',
                'TX_T_LC':      b'\x00',
                'TX_EMB_LC': {
                    1: b'\x00',
                    2: b'\x00',
                    3: b'\x00',
                    4: b'\x00',
                    },
                'lastSeq': False,
                'lastData': False
                }
            }

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data,_hops = b''):
        pkt_time = time()
        dmrpkt = _data[20:53]
        _bits = _data[15]
        
        _ber = _data[53:54]
        _rssi = _data[54:55]
        
        _source_server = self._CONFIG['GLOBAL']['SERVER_ID']
        _source_rptr = _peer_id

        if _call_type == 'group':

            _hbp_is_vterm = (
                _frame_type == HBPF_DATA_SYNC
                and _dtype_vseq == HBPF_SLT_VTERM)
            if _hbp_is_vterm:
                _term_key = (
                    self._system, _slot, _stream_id,
                    _rf_src, _peer_id, _dst_id)
                _hbp_sequence_expected = (
                    dmr_seq_delta(
                        _seq, self.STATUS[_slot].get('lastSeq')) == 1)
                if _is_tombstoned_term(
                        _term_key, _data, pkt_time,
                        _hbp_sequence_expected):
                    return
                if self.STATUS[_slot]['RX_TYPE'] == HBPF_SLT_VTERM:
                    return
                _active_identity = (
                    self.STATUS[_slot]['RX_STREAM_ID'],
                    self.STATUS[_slot]['RX_RFS'],
                    self.STATUS[_slot]['RX_PEER'],
                    self.STATUS[_slot]['RX_TGID'])
                if (_stream_id, _rf_src, _peer_id, _dst_id) != _active_identity:
                    logger.debug(
                        '(%s) Ignoring stale VTERM for stream %s while active '
                        'stream is %s on TS%s',
                        self._system, int_id(_stream_id),
                        int_id(self.STATUS[_slot]['RX_STREAM_ID']), _slot)
                    return

            # Is this a new call stream?
            _hbp_new_stream = (
                _stream_id != self.STATUS[_slot]['RX_STREAM_ID']
                or pkt_time - self.STATUS[_slot]['RX_TIME'] >= STREAM_TO
                or (_frame_type == HBPF_DATA_SYNC
                    and _dtype_vseq == HBPF_SLT_VHEAD
                    and self.STATUS[_slot]['RX_TYPE'] == HBPF_SLT_VTERM))
            if _hbp_new_stream:
                if (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM) and (pkt_time < (self.STATUS[_slot]['RX_TIME'] + STREAM_TO)) and (_rf_src != self.STATUS[_slot]['RX_RFS']):
                    logger.warning('(%s) Packet received with STREAM ID: %s <FROM> SUB: %s PEER: %s <TO> TGID %s, SLOT %s collided with existing call', self._system, int_id(_stream_id), int_id(_rf_src), int_id(_peer_id), int_id(_dst_id), _slot)
                    return

                self.STATUS[_slot]['lastSeq'] = False
                self.STATUS[_slot]['lastData'] = False

                # This is a new call stream
                self.STATUS[_slot]['RX_START'] = pkt_time
                logger.info('(%s) *CALL START* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                if CONFIG['REPORTS']['REPORT']:
                    self._report.send_bridgeEvent('GROUP VOICE,START,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))

                # If we can, use the LC from the voice header as to keep all options intact
                if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                    decoded = decode.voice_head_term(dmrpkt)
                    self.STATUS[_slot]['RX_LC'] = decoded['LC']

                # If we don't have a voice header then don't wait to decode it from the Embedded LC
                # just make a new one from the HBP header. This is good enough, and it saves lots of time
                else:
                    self.STATUS[_slot]['RX_LC'] = LC_OPT + _dst_id + _rf_src

            #LoopControl#
            _hbp_claim = _active_hbp_stream_claim(
                _stream_id, _rf_src, pkt_time)
            if _hbp_claim is not None and _hbp_claim[0] != self._system:
                if 'LOOPLOG' not in self.STATUS[_slot] or not self.STATUS[_slot]['LOOPLOG']:
                    logger.warning(
                        '(%s) HBP *LoopControl* FIRST HBP: %s, STREAM ID: %s, '
                        'TG: %s, TS: %s, IGNORE THIS SOURCE',
                        self._system, _hbp_claim[0], int_id(_stream_id),
                        int_id(_dst_id), _hbp_claim[1])
                    self.STATUS[_slot]['LOOPLOG'] = True
                self.STATUS[_slot]['LAST'] = pkt_time
                return
            for system in _OPENBRIDGE_SYSTEMS:
                if system == self._system or system not in systems:
                    continue
                #if _stream_id in systems[system].STATUS and systems[system].STATUS[_stream_id]['START'] <= self.STATUS[_stream_id]['START']:
                if (_stream_id in systems[system].STATUS
                            and '1ST' in systems[system].STATUS[_stream_id]
                            and systems[system].STATUS[_stream_id].get('TGID') == _dst_id
                            and systems[system].STATUS[_stream_id].get('RFS') == _rf_src
                            and not systems[system].STATUS[_stream_id].get('_fin')
                            and pkt_time - systems[system].STATUS[_stream_id].get('LAST', 0) < STREAM_TO):
                    if 'LOOPLOG' not in self.STATUS[_slot] or not self.STATUS[_slot]['LOOPLOG']:
                        logger.warning("(%s) OBP *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE",self._system, system, int_id(_stream_id), int_id(_dst_id))
                        self.STATUS[_slot]['LOOPLOG'] = True
                    self.STATUS[_slot]['LAST'] = pkt_time

                    if CONFIG['SYSTEMS'][self._system]['ENHANCED_OBP'] and '_bcsq' not in self.STATUS[_slot]:
                        systems[self._system].send_bcsq(_dst_id,_stream_id)
                        self.STATUS[_slot]['_bcsq'] = True
                    return
        
            #Duplicate handling#
            _seq_delta = dmr_seq_delta(
                _seq, self.STATUS[_slot]['lastSeq'])
            if (_seq_delta is not None and _seq_delta > 127
                    and pkt_time - self.STATUS[_slot]['RX_TIME'] >= 1.0):
                _seq_delta = None
            #Duplicate complete packet
            if self.STATUS[_slot]['lastData'] and self.STATUS[_slot]['lastData'] == _data and _seq > 1:
                logger.warning("(%s) *PacketControl* last packet is a complete duplicate of the previous one, disgarding. Stream ID:, %s TGID: %s",self._system,int_id(_stream_id),int_id(_dst_id))
                return
            #Handle inbound duplicates
            if _seq_delta == 0:
                logger.warning("(%s) *PacketControl* Duplicate sequence number %s, disgarding. Stream ID:, %s TGID: %s",self._system,_seq,int_id(_stream_id),int_id(_dst_id))
                return
            #Inbound out-of-order packets
            if _seq_delta is not None and _seq_delta > 127:
                logger.warning("%s) *PacketControl* Out of order packet - last SEQ: %s, this SEQ: %s,  disgarding. Stream ID:, %s TGID: %s ",self._system,self.STATUS[_slot]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id))
                return
            #Inbound missed packets
            if _seq_delta is not None and 1 < _seq_delta <= 127:
                logger.warning("(%s) *PacketControl* Missed packet(s) - last SEQ: %s, this SEQ: %s. Stream ID:, %s TGID: %s ",self._system,self.STATUS[_slot]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id))
        
            #Save this sequence number 
            self.STATUS[_slot]['lastSeq'] = _seq
            #Save this packet
            self.STATUS[_slot]['lastData'] = _data
            if not _hbp_is_vterm:
                _set_hbp_stream_claim(
                    _stream_id, _rf_src, self._system, _slot, pkt_time)
      


            for _bridge in BRIDGES:
                for _system in BRIDGES[_bridge]:

                    if (_system['SYSTEM'] == self._system and _system['TGID'] == _dst_id and _system['TS'] == _slot and _system['ACTIVE'] == True):

                        for _target in BRIDGES[_bridge]:
                            if _target['SYSTEM'] != self._system:
                                if _target['ACTIVE']:
                                    _target_status = systems[_target['SYSTEM']].STATUS
                                    _target_system = self._CONFIG['SYSTEMS'][_target['SYSTEM']]

                                    if _target_system['MODE'] == 'OPENBRIDGE':
                                        # Is this a new call stream on the target?
                                        _target_generation_changed = (
                                            _hbp_new_stream
                                            or (_stream_id in _target_status
                                                and (_target_status[_stream_id].get('RFS') != _rf_src
                                                     or _target_status[_stream_id].get('TGID') != _dst_id)))
                                        if (_stream_id not in _target_status or _target_generation_changed):
                                            # This is a new call stream on the target
                                            _target_status[_stream_id] = {
                                                'START':     pkt_time,
                                                'CONTENTION':False,
                                                'RFS':       _rf_src,
                                                'TGID':      _dst_id,
                                                'TARGET_LC': {},
                                            }
                                        _target_lc_map = _target_status[_stream_id].setdefault(
                                            'TARGET_LC', {})
                                        if _target['TGID'] not in _target_lc_map:
                                            dst_lc = b''.join([self.STATUS[_slot]['RX_LC'][0:3], _target['TGID'], _rf_src])
                                            _target_lc_map[_target['TGID']] = {
                                                'START': pkt_time,
                                                'H_LC': bptc.encode_header_lc(dst_lc),
                                                'T_LC': bptc.encode_terminator_lc(dst_lc),
                                                'EMB_LC': bptc.encode_emblc(dst_lc),
                                            }
                                            logger.info('(%s) Conference Bridge: %s, Call Bridged to OBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                                            if CONFIG['REPORTS']['REPORT']:
                                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))
                                        _target_lc = _target_lc_map[_target['TGID']]
                                            
                                        # Record the time of this packet so we can later identify a stale stream
                                        _target_status[_stream_id]['LAST'] = pkt_time
                                        # Clear the TS bit -- all OpenBridge streams are effectively on TS1
                                        _tmp_bits = _bits & ~(1 << 7)

                                        # Assemble transmit HBP packet header
                                        _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                                        # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                                        # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                                        # if _dst_id != rule['DST_GROUP']:
                                        _tx_dmrpkt = dmrpkt
                                        dmrbits = bitarray(endian='big')
                                        dmrbits.frombytes(_tx_dmrpkt)
                                        # Create a voice header packet (FULL LC)
                                        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                                            dmrbits = _target_lc['H_LC'][0:98] + dmrbits[98:166] + _target_lc['H_LC'][98:197]
                                        # Create a voice terminator packet (FULL LC)
                                        elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                                            dmrbits = _target_lc['T_LC'][0:98] + dmrbits[98:166] + _target_lc['T_LC'][98:197]
                                            if CONFIG['REPORTS']['REPORT']:
                                                call_duration = pkt_time - _target_lc['START']
                                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                                        # Create a Burst B-E packet (Embedded LC)
                                        elif _dtype_vseq in [1,2,3,4]:
                                            dmrbits = dmrbits[0:116] + _target_lc['EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                                        _tx_dmrpkt = dmrbits.tobytes()
                                        _tmp_data = b''.join([_tmp_data, _tx_dmrpkt])

                                    else:
                                        # BEGIN STANDARD CONTENTION HANDLING
                                        #
                                        # The rules for each of the 4 "ifs" below are listed here for readability. The Frame To Send is:
                                        #   From a different group than last RX from this HBSystem, but it has been less than Group Hangtime
                                        #   From a different group than last TX to this HBSystem, but it has been less than Group Hangtime
                                        #   From the same group as the last RX from this HBSystem, but from a different subscriber, and it has been less than stream timeout
                                        #   From the same group as the last TX to this HBSystem, but from a different subscriber, and it has been less than stream timeout
                                        # The "continue" at the end of each means the next iteration of the for loop that tests for matching rules
                                        #
                                        if ((_target['TGID'] != _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < _target_system['GROUP_HANGTIME'])):
                                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                                logger.info('(%s) Call not routed to TGID %s, target active or in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                                            continue
                                        if ((_target['TGID'] != _target_status[_target['TS']]['TX_TGID']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < _target_system['GROUP_HANGTIME'])):
                                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                                logger.info('(%s) Call not routed to TGID%s, target in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']))
                                            continue
                                        if ((_target['TGID'] == _target_status[_target['TS']]['RX_TGID'])
                                                and _target_status[_target['TS']]['RX_TYPE'] != HBPF_SLT_VTERM
                                                and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < STREAM_TO)):
                                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                                logger.info('(%s) Call not routed to TGID%s, matching call already active on target: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                                            continue
                                        if ((_target['TGID'] == _target_status[_target['TS']]['TX_TGID'])
                                                and _target_status[_target['TS']]['TX_TYPE'] != HBPF_SLT_VTERM
                                                and (_rf_src != _target_status[_target['TS']]['TX_RFS'])
                                                and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < STREAM_TO)):
                                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                                logger.info('(%s) Call not routed for subscriber %s, call route in progress on target: HBSystem: %s, TS: %s, TGID: %s, SUB: %s', self._system, int_id(_rf_src), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']), int_id(_target_status[_target['TS']]['TX_RFS']))
                                            continue

                                        # Is this a new call stream?
                                        if (_hbp_new_stream
                                                or _target_status[_target['TS']]['TX_STREAM_ID'] != _stream_id
                                                or _target_status[_target['TS']]['TX_TGID'] != _target['TGID']
                                                or _target_status[_target['TS']]['TX_RFS'] != _rf_src
                                                or _target_status[_target['TS']]['TX_PEER'] != _peer_id):
                                             # Record the DST TGID and Stream ID
                                             _target_status[_target['TS']]['TX_START'] = pkt_time
                                             _target_status[_target['TS']]['TX_TGID'] = _target['TGID']
                                             _target_status[_target['TS']]['TX_STREAM_ID'] = _stream_id
                                             _target_status[_target['TS']]['TX_RFS'] = _rf_src
                                             _target_status[_target['TS']]['TX_PEER'] = _peer_id
                                             # Generate LCs (full and EMB) for the TX stream
                                             dst_lc = self.STATUS[_slot]['RX_LC'][0:3] + _target['TGID'] + _rf_src
                                             _target_status[_target['TS']]['TX_H_LC'] = bptc.encode_header_lc(dst_lc)
                                             _target_status[_target['TS']]['TX_T_LC'] = bptc.encode_terminator_lc(dst_lc)
                                             _target_status[_target['TS']]['TX_EMB_LC'] = bptc.encode_emblc(dst_lc)
                                             logger.debug('(%s) Generating TX FULL and EMB LCs for HomeBrew destination: System: %s, TS: %s, TGID: %s', self._system, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                                             logger.info('(%s) Conference Bridge: %s, Call Bridged to HBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                                             if CONFIG['REPORTS']['REPORT']:
                                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))

                                        # Set other values for the contention handler to test next time there is a frame to forward
                                        _target_status[_target['TS']]['TX_TIME'] = pkt_time
                                        _target_status[_target['TS']]['TX_TYPE'] = _dtype_vseq

                                        # Handle any necessary re-writes for the destination
                                        if _system['TS'] != _target['TS']:
                                            _tmp_bits = _bits ^ 1 << 7
                                        else:
                                            _tmp_bits = _bits

                                        # Assemble transmit HBP packet header
                                        _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                                        # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                                        # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                                        # if _dst_id != rule['DST_GROUP']:
                                        _tx_dmrpkt = dmrpkt
                                        dmrbits = bitarray(endian='big')
                                        dmrbits.frombytes(_tx_dmrpkt)
                                        # Create a voice header packet (FULL LC)
                                        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                                            dmrbits = _target_status[_target['TS']]['TX_H_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_H_LC'][98:197]
                                        # Create a voice terminator packet (FULL LC)
                                        elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                                            dmrbits = _target_status[_target['TS']]['TX_T_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_T_LC'][98:197]
                                            if CONFIG['REPORTS']['REPORT']:
                                                call_duration = pkt_time - _target_status[_target['TS']]['TX_START']
                                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                                        # Create a Burst B-E packet (Embedded LC)
                                        elif _dtype_vseq in [1,2,3,4]:
                                            dmrbits = dmrbits[0:116] + _target_status[_target['TS']]['TX_EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                                        _tx_dmrpkt = dmrbits.tobytes()
                                        _tmp_data = b''.join([_tmp_data, _tx_dmrpkt, _data[53:55]])

                                    # Transmit the packet to the destination system
                                    systems[_target['SYSTEM']].send_system(_tmp_data,_hops,_ber,_rssi,_source_server,_source_rptr)
                                    #logger.debug('(%s) Packet routed by bridge: %s to system: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))



            # Final actions - Is this a voice terminator?
            if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM) and (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM):
                _remember_term(_term_key, _data, pkt_time)
                call_duration = pkt_time - self.STATUS[_slot]['RX_START']
                logger.info('(%s) *CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, Duration: %.2f', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration)
                if CONFIG['REPORTS']['REPORT']:
                   self._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))

                #
                # Begin in-band signalling for call end. This has nothign to do with routing traffic directly.
                #

                # Iterate the rules dictionary

                for _bridge in BRIDGES:
                    for _system in BRIDGES[_bridge]:
                        if _system['SYSTEM'] == self._system:

                            # TGID matches a rule source, reset its timer
                            if _slot == _system['TS'] and _dst_id == _system['TGID'] and ((_system['TO_TYPE'] == 'ON' and (_system['ACTIVE'] == True)) or (_system['TO_TYPE'] == 'OFF' and _system['ACTIVE'] == False)):
                                _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                logger.info('(%s) Transmission match for Bridge: %s. Reset timeout to %s', self._system, _bridge, _system['TIMER'])

                            # TGID matches an ACTIVATION trigger
                            if (_dst_id in _system['ON'] or _dst_id in _system['RESET']) and _slot == _system['TS']:
                                # Set the matching rule as ACTIVE
                                if _dst_id in _system['ON']:
                                    if _system['ACTIVE'] == False:
                                        _system['ACTIVE'] = True
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                        # Cancel the timer if we've enabled an "OFF" type timeout
                                        if _system['TO_TYPE'] == 'OFF':
                                            _system['TIMER'] = pkt_time
                                            logger.info('(%s) Bridge: %s set to "OFF" with an on timer rule: timeout timer cancelled', self._system, _bridge)
                                # Reset the timer for the rule
                                if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON':
                                    _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                    logger.info('(%s) Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)

                            # TGID matches an DE-ACTIVATION trigger
                            if (_dst_id in _system['OFF']  or _dst_id in _system['RESET']) and _slot == _system['TS']:
                                # Set the matching rule as ACTIVE
                                if _dst_id in _system['OFF']:
                                    if _system['ACTIVE'] == True:
                                        _system['ACTIVE'] = False
                                        logger.info('(%s) Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                        # Cancel the timer if we've enabled an "ON" type timeout
                                        if _system['TO_TYPE'] == 'ON':
                                            _system['TIMER'] = pkt_time
                                            logger.info('(%s) Bridge: %s set to ON with and "OFF" timer rule: timeout timer cancelled', self._system, _bridge)
                                # Reset the timer for the rule
                                if _system['ACTIVE'] == False and _system['TO_TYPE'] == 'OFF':
                                    _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                    logger.info('(%s) Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)
                                # Cancel the timer if we've enabled an "ON" type timeout
                                if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON' and _dst_group in _system['OFF']:
                                    _system['TIMER'] = pkt_time
                                    logger.info('(%s) Bridge: %s set to ON with and "OFF" timer rule: timeout timer cancelled', self._system, _bridge)

            #
            # END IN-BAND SIGNALLING
            #


            # Mark status variables for use later
            self.STATUS[_slot]['RX_PEER']      = _peer_id
            self.STATUS[_slot]['RX_SEQ']       = _seq
            self.STATUS[_slot]['RX_RFS']       = _rf_src
            self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
            self.STATUS[_slot]['RX_TGID']      = _dst_id
            self.STATUS[_slot]['RX_TIME']      = pkt_time
            self.STATUS[_slot]['RX_STREAM_ID'] = _stream_id
            if _hbp_is_vterm:
                _set_hbp_stream_claim(
                    _stream_id, _rf_src, self._system, _slot, pkt_time,
                    terminal=True)

#
# Socket-based reporting section
#
class bridgeReportFactory(reportFactory):

    def send_bridge(self):
        serialized = pickle.dumps(BRIDGES, protocol=2) #.decode("utf-8", errors='ignore')
        self.send_clients(REPORT_OPCODES['BRIDGE_SND']+serialized)

    def send_bridgeEvent(self, _data):
        if isinstance(_data, str):
            _data = _data.decode('utf-8', error='ignore')
        self.send_clients(REPORT_OPCODES['BRDG_EVENT']+_data)


#************************************************
#      MAIN PROGRAM LOOP STARTS HERE
#************************************************

if __name__ == '__main__':

    import argparse
    import sys
    import os
    import signal

    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually hblink.cfg)')
    parser.add_argument('-r', '--rules', action='store', dest='RULES_FILE', help='/full/path/to/rules.file (usually rules.py)')
    parser.add_argument('-l', '--logging', action='store', dest='LOG_LEVEL', help='Override config file logging level.')
    cli_args = parser.parse_args()

    # Ensure we have a path for the config file, if one wasn't specified, then use the default (top of file)
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/hblink.cfg'

    # Call the external routine to build the configuration dictionary
    CONFIG = config.build_config(cli_args.CONFIG_FILE)

    # Ensure we have a path for the rules file, if one wasn't specified, then use the default (top of file)
    if not cli_args.RULES_FILE:
        cli_args.RULES_FILE = os.path.dirname(os.path.abspath(__file__))+'/rules.py'

    # Start the system logger
    if cli_args.LOG_LEVEL:
        CONFIG['LOGGER']['LOG_LEVEL'] = cli_args.LOG_LEVEL
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('\n\nCopyright (c) 2013, 2014, 2015, 2016, 2018, 2019\n\tThe Regents of the K0USY Group. All rights reserved.\n')
    logger.debug('(GLOBAL) Logging system started, anything from here on gets logged')

    # Set up the signal handler
    def sig_handler(_signal, _frame):
        logger.info('(GLOBAL) SHUTDOWN: CONFBRIDGE IS TERMINATING WITH SIGNAL %s', str(_signal))
        hblink_handler(_signal, _frame)
        logger.info('(GLOBAL) SHUTDOWN: ALL SYSTEM HANDLERS EXECUTED - STOPPING REACTOR')
        reactor.stop()

    # Set signal handers so that we can gracefully exit if need be
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, sig_handler)

    # Create the name-number mapping dictionaries
    peer_ids, subscriber_ids, talkgroup_ids, local_subscriber_ids,server_ids = mk_aliases(CONFIG)
    
    #Add special IDs to DB
    subscriber_ids[900999] = 'D-APRS'
    subscriber_ids[4294967295] = 'SC'
    
    CONFIG['_SUB_IDS'] = subscriber_ids
    CONFIG['_PEER_IDS'] = peer_ids
    CONFIG['_LOCAL_SUBSCRIBER_IDS'] = local_subscriber_ids
    CONFIG['_SERVER_IDS'] = server_ids
    
    # Import the ruiles file as a module, and create BRIDGES from it
    spec = importlib.util.spec_from_file_location("module.name", cli_args.RULES_FILE)
    rules_module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(rules_module)
        logger.info('(ROUTER) Routing bridges file found and bridges imported: %s', cli_args.RULES_FILE)
    except (ImportError, FileNotFoundError):
        sys.exit('(ROUTER) TERMINATING: Routing bridges file not found or invalid: {}'.format(cli_args.RULES_FILE))

    # Build the routing rules file
    BRIDGES = make_bridges(rules_module.BRIDGES)

    # INITIALIZE THE REPORTING LOOP
    if CONFIG['REPORTS']['REPORT']:
        report_server = config_reports(CONFIG, bridgeReportFactory)
    else:
        report_server = None
        logger.info('(REPORT) TCP Socket reporting not configured')

    # HBlink instance creation
    logger.info('(GLOBAL) HBlink \'bridge.py\' -- SYSTEM STARTING...')
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
                _OPENBRIDGE_SYSTEMS.add(system)
                systems[system] = routerOBP(system, CONFIG, report_server)
            else:
                systems[system] = routerHBP(system, CONFIG, report_server)
            reactor.listenUDP(CONFIG['SYSTEMS'][system]['PORT'], systems[system], interface=CONFIG['SYSTEMS'][system]['IP'])
            logger.debug('(GLOBAL) %s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])

    def loopingErrHandle(failure):
        logger.error('(GLOBAL) STOPPING REACTOR TO AVOID MEMORY LEAK: Unhandled error in timed loop.\n %s', failure)
        reactor.stop()

    # Initialize the rule timer -- this if for user activated stuff
    rule_timer_task = task.LoopingCall(rule_timer_loop)
    rule_timer = rule_timer_task.start(60)
    rule_timer.addErrback(loopingErrHandle)

    # Initialize the stream trimmer
    stream_trimmer_task = task.LoopingCall(stream_trimmer_loop)
    stream_trimmer = stream_trimmer_task.start(5)
    stream_trimmer.addErrback(loopingErrHandle)

    reactor.run()
