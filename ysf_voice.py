#!/usr/bin/env python3
###############################################################################
#   DMRD <-> YSF DN voice, and the peer mixin used by both bridge processes.
#
#   DN carries the 49-bit AMBE+2 parameter word (Yaesu spec figure 4-32).
#   DMR carries that same word inside a 72-bit channel frame. The conversion
#   is dmr_utils3's existing channel-code packer, not a vocoder.
#
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

'''Queue DN AMBE words and drive a YSFPEER stanza from routerHBP.'''

import logging
import os
from collections import deque
from time import time

from bitarray import bitarray
from dmr_utils3 import bptc
from dmr_utils3.ambe_utils import convert49BitTo72BitAMBE, convert72BitTo49BitAMBE
from dmr_utils3.const import BS_DATA_SYNC, BS_VOICE_SYNC, EMB, SLOT_TYPE
from dmr_utils3.utils import bytes_3

from const import DMRD, LC_OPT
from ysf_protocol import (
    CSD_COUNT,
    DT_VD_MODE2,
    FI_COMMUNICATION,
    FI_TERMINATOR,
    CALLSIGN_LENGTH,
    decode_dn_frame,
    encode_dn_frame,
    encode_header_frame,
    pack_poll,
    pack_unlink,
    pack_ysfd,
    pad_callsign,
    parse_ysfd,
)

logger = logging.getLogger(__name__)

YSF_FRAME_S = 0.100
DMR_BURST_S = 0.060
# Close the reflector stream when DMR stops without a terminator. OpenBridge
# often does that; pYSF keeps the transmission up until it sees FI=terminator.
YSF_HANG_S = 0.45

_HEAD_BITS = 0b00100001
_TERM_BITS = 0b00100010
_BURST_BITS = (
    0b00010000, 0b00000001, 0b00000010, 0b00000011, 0b00000100, 0b00000101,
)
_NULL_EMB = bitarray(32, endian='big')
_NULL_EMB.setall(0)

_SILENCE_72 = bitarray(endian='big')
_SILENCE_72.frombytes(bytes.fromhex('ACAA40200044408080'))


def ambe72_to_49(bits72):
    """72-bit DMR channel word -> 49 AMBE parameter bits."""
    return [int(bit) for bit in convert72BitTo49BitAMBE(bits72)]


def ambe49_to_72(bits49):
    """49 AMBE parameter bits -> 9-byte DMR channel word."""
    return bytes(convert49BitTo72BitAMBE(list(bits49)))


SILENCE_49 = ambe72_to_49(_SILENCE_72)


def ambe72_from_payload(payload):
    """Three 72-bit channel words from a 33-byte DMR voice payload."""
    burst = bitarray(endian='big')
    burst.frombytes(payload[:33])
    if len(burst) < 264:
        burst.extend([0] * (264 - len(burst)))
    return (
        burst[0:72],
        burst[72:108] + burst[156:192],
        burst[192:264],
    )


def _embed(position, emb_lc):
    if position == 0:
        return BS_VOICE_SYNC
    name = ('BURST_B', 'BURST_C', 'BURST_D', 'BURST_E', 'BURST_F')[position - 1]
    if position == 5 or not emb_lc:
        fragment = _NULL_EMB
    else:
        fragment = emb_lc.get(position, _NULL_EMB)
    return EMB[name][:8] + fragment + EMB[name][-8:]


def voice_payload(ambe_words, embed):
    """33-byte DMR voice payload. ``ambe_words`` is three 9-byte channel words."""
    pieces = []
    for word in ambe_words:
        bits = bitarray(endian='big')
        bits.frombytes(word)
        pieces.append(bits[:72])
    payload = bitarray(264, endian='big')
    payload.setall(0)
    payload[0:72] = pieces[0]
    payload[72:108] = pieces[1][:36]
    payload[108:156] = embed
    payload[156:192] = pieces[1][36:72]
    payload[192:264] = pieces[2]
    return payload.tobytes()


def lc_payload(full_lc, slot_type_name):
    frame = (
        full_lc[0:98]
        + SLOT_TYPE[slot_type_name][:10]
        + BS_DATA_SYNC
        + SLOT_TYPE[slot_type_name][-10:]
        + full_lc[98:]
    )
    return frame.tobytes()


def build_dmrd(seq, src, dst, peer, slot, kind_bits, stream_id, payload):
    """55-byte Homebrew DMRD: header, 33-byte payload, BER, RSSI."""
    slot_bit = 0x80 if int(slot) == 2 else 0
    return b''.join((
        DMRD,
        bytes((int(seq) & 0xFF,)),
        src,
        dst,
        peer,
        bytes((slot_bit | kind_bits,)),
        stream_id,
        payload,
        b'\x00\x00',
    ))


def _alias_key(value):
    if isinstance(value, bytes):
        value = value.decode('ascii', 'ignore')
    return str(value).strip().split()[0].upper()


def callsign_index(config):
    """Callsign -> DMR ID from the alias tables. Local entries win."""
    index = {}
    for key in ('_SUB_IDS', '_LOCAL_SUBSCRIBER_IDS'):
        for ident, name in (config.get(key) or {}).items():
            try:
                ident = int(ident)
            except (TypeError, ValueError):
                continue
            index[_alias_key(name)] = ident
    return index


def callsign_for_id(config, ident, fallback):
    for key in ('_LOCAL_SUBSCRIBER_IDS', '_SUB_IDS'):
        table = config.get(key) or {}
        name = table.get(ident)
        if name is None:
            name = table.get(str(ident))
        if name:
            return _alias_key(name)
    return fallback


def id_for_callsign(index, callsign, fallback):
    return index.get(_alias_key(callsign), fallback)


class YsfPeerMixin(object):
    """Speak YSFP/YSFD on a router that already has HBP ``STATUS`` and ``dmrd_received``."""

    def init_ysf(self):
        self.datagramReceived = self.ysf_datagram_received
        self.maintenance_loop = self.ysf_maintenance_loop
        self.send_system = self.ysf_send_system
        self.dereg = self.ysf_unlink
        self._ysf_immediate = False
        self._tx_q = deque()
        self._tx_ending = False
        self._tx_timer = None
        self._tx_hang = None
        self._tx_stream = None
        self._tx_source = pad_callsign(self._config.get('CALLSIGN'))
        self._tx_fn = 0
        self._tx_seq = 0
        self._rx_q = deque()
        self._rx_ending = False
        self._rx_timer = None
        self._rx_open = False
        self._rx_header_sent = False
        self._rx_stream = None
        self._rx_seq = 0
        self._rx_pos = 0
        self._rx_src = b'\x00\x00\x00'
        self._rx_lc = None
        self._rx_emb = None
        self._last_drop_dt = None
        self._last_drop_dgid = None
        self._callsigns = None
        stats = self._config.setdefault('STATS', {})
        stats.setdefault('CONNECTION', 'NO')
        stats.setdefault('PINGS_SENT', 0)
        stats.setdefault('LAST_PING_ACK_TIME', 0)

    def startProtocol(self):
        self.ysf_send_poll()
        super(YsfPeerMixin, self).startProtocol()

    def _fallback_id(self):
        radio = self._config.get('RADIO_ID', b'\x00\x00\x00\x00')
        if isinstance(radio, bytes):
            return int.from_bytes(radio, 'big')
        return int(radio)

    def _gateway(self):
        return pad_callsign(self._config.get('CALLSIGN'))

    def _peer_id(self):
        radio = self._config.get('RADIO_ID', b'\x00\x00\x00\x01')
        if isinstance(radio, bytes) and len(radio) == 4:
            return radio
        return int(radio).to_bytes(4, 'big')

    def _slot(self):
        # XLX module audio is always slot 2. YSF uses the same rules leg.
        return 2

    def _tgid(self):
        # XLX module audio is always TG 9. YSF uses the same rules leg.
        return bytes_3(9)

    def _index(self):
        if self._callsigns is None:
            self._callsigns = callsign_index(self._CONFIG)
        return self._callsigns

    def _write(self, packet):
        transport = getattr(self, 'transport', None)
        if transport is None:
            return
        transport.write(packet, self._config['MASTER_SOCKADDR'])

    def ysf_send_poll(self):
        self._write(pack_poll(self._gateway()))
        stats = self._config.setdefault('STATS', {})
        stats['PINGS_SENT'] = stats.get('PINGS_SENT', 0) + 1

    def ysf_unlink(self):
        self._write(pack_unlink(self._gateway()))

    def ysf_maintenance_loop(self):
        self._maybe_reresolve()
        self.ysf_send_poll()
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
            logger.info('(%s) YSF reflector timed out', self._system)

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
        if self._ysf_immediate:
            resolver = getattr(self, '_ysf_resolve', None)
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
                '(%s) YSF reflector logged in %s:%s',
                self._system, addr[0], addr[1])
        stats['CONNECTION'] = 'YES'
        stats['LAST_PING_ACK_TIME'] = now

    def _schedule(self, delay, callback):
        if self._ysf_immediate:
            callback()
            return None
        from twisted.internet import reactor
        return reactor.callLater(delay, callback)

    def ysf_send_system(self, packet, *_args, **_kwargs):
        """DMRD from ``to_target`` -> queued DN frames toward the reflector."""
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
            # header and a terminator with nobody talking.
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
        self._tx_fn = 0
        ident = int.from_bytes(src, 'big') if src else 0
        fallback = _alias_key(self._gateway())
        self._tx_source = pad_callsign(
            callsign_for_id(self._CONFIG, ident, fallback))
        # pYSF ignores voice until an FI=header frame opens the stream.
        self._write_header()

    def _wanted_dgid(self):
        return int(self._config.get('DGID') or 0) & 0x7F

    def _write_ysfd(self, frame, end):
        packet = pack_ysfd(
            self._gateway(), self._tx_source, 'ALL',
            self._tx_seq, end, frame)
        self._tx_seq = (self._tx_seq + 1) & 0x7F
        self._write(packet)

    def _write_header(self):
        self._write_ysfd(encode_header_frame(
            'ALL', self._tx_source, self._gateway(), self._tx_source,
            self._wanted_dgid()), False)

    def _write_terminator(self):
        # FI=terminator is what pYSF removes the stream on. The YSFD end bit
        # is what a G4KLX reflector removes the stream on.
        self._write_ysfd(encode_dn_frame(
            [SILENCE_49] * 5, 0, self._wanted_dgid(),
            self._tx_source, self._gateway(), fi=FI_TERMINATOR), True)

    def _write_dn(self, chunk):
        frame = encode_dn_frame(
            chunk, self._tx_fn, self._wanted_dgid(),
            self._tx_source, self._gateway(), fi=FI_COMMUNICATION)
        self._tx_fn = (self._tx_fn + 1) % CSD_COUNT
        self._write_ysfd(frame, False)

    def _finish_tx(self):
        if self._tx_stream is not None:
            self._write_terminator()
        self._tx_ending = False
        self._tx_stream = None
        self._cancel_timer('_tx_hang')

    def _arm_tx_hang(self):
        # Tests run the scheduler inline. A hang fired there would cut the
        # over between bursts, so only the live reactor timer closes it.
        if self._ysf_immediate or self._tx_stream is None:
            return
        self._cancel_timer('_tx_hang')
        self._tx_hang = self._schedule(YSF_HANG_S, self._tx_hang_fired)

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
        while len(self._tx_q) >= 5 or (self._tx_ending and self._tx_q):
            chunk = []
            for _ in range(5):
                chunk.append(self._tx_q.popleft() if self._tx_q else SILENCE_49)
            self._write_dn(chunk)
            if self._tx_ending and not self._tx_q:
                self._finish_tx()
            if not self._ysf_immediate and not force and self._tx_stream is not None:
                self._tx_timer = self._schedule(YSF_FRAME_S, self._tx_fired)
                return
        # A terminator can arrive after the last full group was already sent,
        # or after a header with no voice.
        if self._tx_ending and not self._tx_q:
            self._finish_tx()
        elif self._tx_stream is not None:
            self._arm_tx_hang()

    def _tx_fired(self):
        self._tx_timer = None
        self._pump_tx()

    def ysf_datagram_received(self, data, addr):
        expected = self._config.get('MASTER_IP')
        if expected and addr and addr[0] != expected:
            return
        self._touch()
        parsed = parse_ysfd(data)
        if parsed is None:
            return
        if parsed['gateway'] == self._gateway():
            return
        decoded = decode_dn_frame(parsed['frame'])
        if decoded is None or decoded['fich'] is None:
            return
        wanted = self._wanted_dgid()
        got = int(decoded['fich'].get('dgid') or 0)
        if wanted and got != wanted:
            if got != self._last_drop_dgid:
                logger.info(
                    '(%s) Dropping YSF DGID %s; this stanza is room %s',
                    self._system, got, wanted)
                self._last_drop_dgid = got
            return
        dt = decoded['fich']['dt']
        if dt != DT_VD_MODE2 or not decoded['ambes']:
            if dt != self._last_drop_dt:
                logger.info(
                    '(%s) Dropping YSF data type %s; only DN (V/D mode 2) is bridged',
                    self._system, dt)
                self._last_drop_dt = dt
            # A Voice FR (or other non-DN) end bit belongs to a stream we are
            # not bridging. It must not hang up the DN call already open.
            if parsed['end'] and dt == DT_VD_MODE2 and self._rx_open:
                self._rx_ending = True
                self._pump_rx()
            return
        source = parsed['source']
        if (not self._rx_open) or source != getattr(self, '_rx_ysf_source', None):
            if self._rx_open:
                self._cancel_timer('_rx_timer')
                self._rx_ending = True
                self._pump_rx(force=True)
            self._open_rx(source)
        for word in decoded['ambes']:
            self._rx_q.append(word)
        if parsed['end']:
            self._rx_ending = True
        self._pump_rx()

    def _open_rx(self, source):
        self._rx_open = True
        self._rx_header_sent = False
        self._rx_ending = False
        self._rx_q.clear()
        self._rx_ysf_source = source
        self._rx_pos = 0
        self._rx_seq = 0
        stream = os.urandom(4)
        if stream == b'\x00\x00\x00\x00':
            stream = b'\x00\x00\x00\x01'
        self._rx_stream = stream
        ident = id_for_callsign(self._index(), source, self._fallback_id())
        src = bytes_3(int(ident) & 0xFFFFFF)
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
        self._rx_ysf_source = None

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
                voice_payload(words, _embed(position, self._rx_emb)))
            if not self._ysf_immediate and not force:
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
