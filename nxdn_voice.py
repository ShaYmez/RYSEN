#!/usr/bin/env python3
###############################################################################
#   DMRD <-> NXDN voice, and the peer mixin used by both bridge processes.
#
#   One NXDN voice datagram is four 20 ms AMBE words, the same 50 words per
#   second as DMR. The 72-bit DMR channel word and the 49-bit NXDN word are
#   the same AMBE+2 parameter word. The conversion is dmr_utils3's existing
#   channel-code packer, not a vocoder.
#
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

'''Queue AMBE words and drive an NXDNPEER stanza from routerHBP.'''

import logging
import os
from collections import deque
from time import time

from dmr_utils3 import bptc
from dmr_utils3.utils import bytes_3

from const import DMRD, LC_OPT
from nxdn_aliases import dmr_id_for_nxdn, nxdn_id_for_dmr
from nxdn_protocol import (
    FLAG_END,
    FLAG_GROUP,
    FLAG_HEADER,
    MESSAGE_TX_REL,
    MESSAGE_VCALL,
    audio_words,
    is_data,
    pack_data,
    pack_poll,
    pack_unlink,
    pad_callsign,
    parse_data,
    signalling_kind,
    signalling_payload,
    voice_payload,
)
from ysf_voice import (
    DMR_BURST_S,
    SILENCE_49,
    _BURST_BITS,
    _HEAD_BITS,
    _TERM_BITS,
    _embed,
    ambe49_to_72,
    ambe72_from_payload,
    ambe72_to_49,
    build_dmrd,
    lc_payload,
    voice_payload as dmr_voice_payload,
)

logger = logging.getLogger(__name__)

# Four 20 ms words. DMR delivers three words every 60 ms.
NXDN_FRAME_S = 0.080
# Close the reflector stream when DMR stops without a terminator.
NXDN_HANG_S = 0.45


class NxdnPeerMixin(object):
    """Speak NXDNP/NXDND on a router that already has HBP ``STATUS`` and ``dmrd_received``."""

    def init_nxdn(self):
        self.datagramReceived = self.nxdn_datagram_received
        self.maintenance_loop = self.nxdn_maintenance_loop
        self.send_system = self.nxdn_send_system
        self.dereg = self.nxdn_unlink
        self._nxdn_immediate = False
        self._tx_q = deque()
        self._tx_ending = False
        self._tx_timer = None
        self._tx_hang = None
        self._tx_stream = None
        self._tx_nxdn_src = None
        self._tx_part = 0
        self._rx_q = deque()
        self._rx_ending = False
        self._rx_timer = None
        self._rx_open = False
        self._rx_header_sent = False
        self._rx_stream = None
        self._rx_seq = 0
        self._rx_pos = 0
        self._rx_src = b'\x00\x00\x00'
        self._rx_nxdn_src = None
        self._rx_lc = None
        self._rx_emb = None
        stats = self._config.setdefault('STATS', {})
        stats.setdefault('CONNECTION', 'NO')
        stats.setdefault('PINGS_SENT', 0)
        stats.setdefault('LAST_PING_ACK_TIME', 0)

    def startProtocol(self):
        self.nxdn_send_poll()
        super(NxdnPeerMixin, self).startProtocol()

    def _callsign(self):
        return pad_callsign(self._config.get('CALLSIGN'))

    def _talkgroup(self):
        return int(self._config.get('TALKGROUP') or 0) & 0xFFFF

    def _nxdn_id(self):
        return int(self._config.get('NXDN_ID') or 0) & 0xFFFF

    def _mapped_nxdn_id(self, dmr_id):
        return nxdn_id_for_dmr(self._CONFIG, dmr_id, self._nxdn_id())

    def _mapped_dmr_id(self, nxdn_id):
        return dmr_id_for_nxdn(self._CONFIG, nxdn_id, nxdn_id)

    def _ran(self):
        return int(self._config.get('RAN') or 0) & 0x3F

    def _peer_id(self):
        radio = self._config.get('RADIO_ID', b'\x00\x00\x00\x01')
        if isinstance(radio, bytes) and len(radio) == 4:
            return radio
        return int(radio).to_bytes(4, 'big')

    def _slot(self):
        # The rules leg is slot 2, the same as an XLX module and a YSF room.
        return 2

    def _tgid(self):
        # The rules leg is talkgroup 9. The reflector room is TALKGROUP.
        return bytes_3(9)

    def _write(self, packet):
        transport = getattr(self, 'transport', None)
        if transport is None:
            return
        transport.write(packet, self._config['MASTER_SOCKADDR'])

    def nxdn_send_poll(self):
        self._write(pack_poll(self._callsign(), self._talkgroup()))
        stats = self._config.setdefault('STATS', {})
        stats['PINGS_SENT'] = stats.get('PINGS_SENT', 0) + 1

    def nxdn_unlink(self):
        self._write(pack_unlink(self._callsign(), self._talkgroup()))

    def nxdn_maintenance_loop(self):
        self._maybe_reresolve()
        self.nxdn_send_poll()
        stats = self._config.setdefault('STATS', {})
        if stats.get('CONNECTION') != 'YES':
            return
        last = stats.get('LAST_PING_ACK_TIME') or 0
        limit = (
            self._CONFIG['GLOBAL']['PING_TIME']
            * self._CONFIG['GLOBAL']['MAX_MISSED'])
        if last and (time() - last) > limit:
            stats['CONNECTION'] = 'NO'
            stats['CONNECTED'] = None
            logger.info('(%s) NXDN reflector timed out', self._system)

    def _maybe_reresolve(self):
        """Look the reflector up again while disconnected, same as an HBP peer."""
        stats = self._config.setdefault('STATS', {})
        if stats.get('CONNECTION') == 'YES':
            return
        if stats.get('DNS_TIME', 0) >= (time() - 600):
            return
        host = self._config.get('_MASTER_IP')
        if not host:
            return
        stats['DNS_TIME'] = time()
        self._resolve_master(host)

    def _resolve_master(self, host):
        if self._nxdn_immediate:
            resolver = getattr(self, '_nxdn_resolve', None)
            if resolver is None:
                return
            ip = resolver(host)
            if ip:
                self._update_sockaddr(ip)
            return
        from twisted.internet import reactor
        deferred = reactor.resolve(host)
        deferred.addCallback(self._update_sockaddr)
        deferred.addErrback(self._resolve_failed)

    def _update_sockaddr(self, ip):
        if not ip:
            return
        self._config['MASTER_IP'] = ip
        port = self._config.get('MASTER_PORT')
        if port is not None:
            self._config['MASTER_SOCKADDR'] = (ip, port)
        logger.info('(%s) hostname resolution performed: %s', self._system, ip)

    def _resolve_failed(self, failure):
        logger.info('(%s) hostname resolution error: %s', self._system, failure)

    def _touch(self):
        stats = self._config.setdefault('STATS', {})
        now = time()
        if not stats.get('CONNECTED'):
            stats['CONNECTED'] = now
            addr = self._config.get('MASTER_SOCKADDR') or ('', '')
            logger.info(
                '(%s) NXDN reflector logged in %s:%s',
                self._system, addr[0], addr[1])
        stats['CONNECTION'] = 'YES'
        stats['LAST_PING_ACK_TIME'] = now

    def _schedule(self, delay, callback):
        if self._nxdn_immediate:
            callback()
            return None
        from twisted.internet import reactor
        return reactor.callLater(delay, callback)

    def nxdn_send_system(self, packet, *_args, **_kwargs):
        """DMRD from ``to_target`` -> queued NXDN frames toward the reflector."""
        if not packet or packet[:4] != DMRD or len(packet) < 53:
            return
        bits = packet[15]
        frame_type = (bits & 0x30) >> 4
        dtype = bits & 0x0F
        stream = packet[16:20]
        if frame_type == 0x2 and dtype == 0x1:
            self._begin_tx(packet[5:8], stream)
            return
        if frame_type == 0x2 and dtype == 0x2:
            # A terminator must not open a stream. After the hang timer has
            # already closed the over, a late unkey would otherwise emit a
            # header and a trailer with nobody talking.
            if self._tx_stream == stream:
                self._tx_ending = True
                self._pump_tx()
            return
        if frame_type not in (0x0, 0x1):
            return
        self._begin_tx(packet[5:8], stream)
        for word in ambe72_from_payload(packet[20:53]):
            self._tx_q.append(ambe72_to_49(word))
        self._pump_tx()

    def _cancel_timer(self, attr):
        timer = getattr(self, attr, None)
        setattr(self, attr, None)
        if timer is not None and getattr(timer, 'active', lambda: False)():
            timer.cancel()

    def _begin_tx(self, src, stream):
        if self._tx_stream == stream:
            return
        if self._tx_stream is not None:
            self._cancel_timer('_tx_timer')
            self._cancel_timer('_tx_hang')
            self._tx_ending = True
            self._pump_tx(force=True)
        self._tx_stream = stream
        self._tx_ending = False
        self._tx_part = 0
        ident = int.from_bytes(src, 'big') if src else 0
        self._tx_nxdn_src = self._mapped_nxdn_id(ident)
        # A reflector does not open a call on voice alone.
        self._write_header()

    def _write_nxdn(self, flags, payload):
        src = self._tx_nxdn_src if self._tx_nxdn_src is not None else self._nxdn_id()
        self._write(pack_data(src, self._talkgroup(), flags, payload))

    def _write_header(self):
        src = self._tx_nxdn_src if self._tx_nxdn_src is not None else self._nxdn_id()
        self._write_nxdn(FLAG_GROUP | FLAG_HEADER, signalling_payload(
            MESSAGE_VCALL, src, self._talkgroup(), self._ran()))

    def _write_trailer(self):
        src = self._tx_nxdn_src if self._tx_nxdn_src is not None else self._nxdn_id()
        self._write_nxdn(FLAG_GROUP | FLAG_END, signalling_payload(
            MESSAGE_TX_REL, src, self._talkgroup(), self._ran()))

    def _write_voice(self, chunk):
        src = self._tx_nxdn_src if self._tx_nxdn_src is not None else self._nxdn_id()
        payload = voice_payload(
            chunk, src, self._talkgroup(), self._tx_part, self._ran())
        self._tx_part = (self._tx_part + 1) % 4
        self._write_nxdn(FLAG_GROUP, payload)

    def _finish_tx(self):
        if self._tx_stream is not None:
            self._write_trailer()
        self._tx_ending = False
        self._tx_stream = None
        self._cancel_timer('_tx_hang')

    def _arm_tx_hang(self):
        # Tests run the scheduler inline. A hang fired there would cut the
        # over between bursts, so only the live reactor timer closes it.
        if self._nxdn_immediate or self._tx_stream is None:
            return
        self._cancel_timer('_tx_hang')
        self._tx_hang = self._schedule(NXDN_HANG_S, self._tx_hang_fired)

    def _tx_hang_fired(self):
        self._tx_hang = None
        if self._tx_stream is None or self._tx_ending:
            return
        self._tx_ending = True
        self._pump_tx()

    def _pump_tx(self, force=False):
        if (not force and self._tx_timer is not None
                and getattr(self._tx_timer, 'active', lambda: True)()):
            return
        self._cancel_timer('_tx_hang')
        while len(self._tx_q) >= 4 or (self._tx_ending and self._tx_q):
            chunk = []
            for _ in range(4):
                chunk.append(self._tx_q.popleft() if self._tx_q else SILENCE_49)
            self._write_voice(chunk)
            if self._tx_ending and not self._tx_q:
                self._finish_tx()
            if not self._nxdn_immediate and not force and self._tx_stream is not None:
                self._tx_timer = self._schedule(NXDN_FRAME_S, self._tx_fired)
                return
        if self._tx_ending and not self._tx_q:
            self._finish_tx()
        elif self._tx_stream is not None:
            self._arm_tx_hang()

    def _tx_fired(self):
        self._tx_timer = None
        self._pump_tx()

    def nxdn_datagram_received(self, data, addr):
        expected = self._config.get('MASTER_IP')
        if expected and addr and addr[0] != expected:
            return
        self._touch()
        parsed = parse_data(data)
        if parsed is None:
            return
        # Ignore our own TX. Mapped talker IDs are echoed while _tx_stream is open.
        if self._tx_stream is not None:
            return
        if parsed['src'] == self._nxdn_id():
            return
        payload = parsed['payload']
        if is_data(payload, parsed['flags']):
            return
        kind = signalling_kind(payload)
        if kind == 'trailer':
            if self._rx_open:
                self._rx_ending = True
                self._pump_rx()
            return
        if kind == 'header':
            if (not self._rx_open) or parsed['src'] != self._rx_nxdn_src:
                if self._rx_open:
                    self._cancel_timer('_rx_timer')
                    self._rx_ending = True
                    self._pump_rx(force=True)
                self._open_rx(parsed['src'])
            return
        words = audio_words(payload)
        if not words:
            return
        if (not self._rx_open) or parsed['src'] != self._rx_nxdn_src:
            if self._rx_open:
                self._cancel_timer('_rx_timer')
                self._rx_ending = True
                self._pump_rx(force=True)
            self._open_rx(parsed['src'])
        for word in words:
            self._rx_q.append(word)
        if parsed['end']:
            self._rx_ending = True
        self._pump_rx()

    def _open_rx(self, source):
        self._rx_open = True
        self._rx_header_sent = False
        self._rx_ending = False
        self._rx_q.clear()
        self._rx_nxdn_src = source
        self._rx_pos = 0
        self._rx_seq = 0
        stream = os.urandom(4)
        if stream == b'\x00\x00\x00\x00':
            stream = b'\x00\x00\x00\x01'
        self._rx_stream = stream
        src = bytes_3(self._mapped_dmr_id(source) & 0xFFFFFF)
        self._rx_src = src
        lc = b''.join((LC_OPT, self._tgid(), src))
        self._rx_lc = lc
        self._rx_emb = bptc.encode_emblc(lc)

    def _emit(self, kind_bits, payload):
        packet = build_dmrd(
            self._rx_seq, self._rx_src, self._tgid(), self._peer_id(),
            self._slot(), kind_bits, self._rx_stream, payload)
        self._rx_seq = (self._rx_seq + 1) & 0xFF
        # bridge.py accepts an optional hops argument; bridge_master does not.
        self.dmrd_received(
            self._peer_id(), self._rx_src, self._tgid(),
            packet[4], self._slot(), 'group',
            (packet[15] & 0x30) >> 4, packet[15] & 0x0F,
            self._rx_stream, packet)

    def _emit_header(self):
        if self._rx_header_sent:
            return
        full = bptc.encode_header_lc(self._rx_lc)
        self._emit(_HEAD_BITS, lc_payload(full, 'VOICE_LC_HEAD'))
        self._rx_header_sent = True

    def _emit_terminator(self):
        full = bptc.encode_terminator_lc(self._rx_lc)
        self._emit(_TERM_BITS, lc_payload(full, 'VOICE_LC_TERM'))
        self._rx_open = False
        self._rx_header_sent = False
        self._rx_ending = False
        self._rx_nxdn_src = None

    def _pump_rx(self, force=False):
        if (not force and self._rx_timer is not None
                and getattr(self._rx_timer, 'active', lambda: True)()):
            return
        while len(self._rx_q) >= 3 or (self._rx_ending and self._rx_q):
            self._emit_header()
            words = []
            for _ in range(3):
                bits = self._rx_q.popleft() if self._rx_q else SILENCE_49
                words.append(ambe49_to_72(bits))
            position = self._rx_pos % 6
            self._rx_pos += 1
            self._emit(
                _BURST_BITS[position],
                dmr_voice_payload(words, _embed(position, self._rx_emb)))
            if not self._nxdn_immediate and not force:
                self._rx_timer = self._schedule(DMR_BURST_S, self._rx_fired)
                return
        if self._rx_ending and self._rx_open:
            if self._rx_header_sent or self._rx_q:
                self._emit_terminator()
            else:
                self._rx_open = False
                self._rx_ending = False

    def _rx_fired(self):
        self._rx_timer = None
        self._pump_rx()
