#!/usr/bin/env python3
###############################################################################
#   Hytera IP Multi-site Connect voice ↔ internal DMRD translation
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

import os
from collections import deque

from twisted.internet import reactor

from const import (
    DMRD,
    HBPF_DATA_SYNC,
    HBPF_SLT_VHEAD,
    HBPF_SLT_VTERM,
    HBPF_VOICE,
    HBPF_VOICE_SYNC,
)
from hytera_const import (
    CALL_PRIVATE,
    CALL_GROUP,
    MEDIA_MIN_LEN,
    MEDIA_PAYLOAD_LEN,
    MEDIA_PAYLOAD_OFFSET,
    MEDIA_SEQUENCE_OFFSET,
    SLOT_HYTERA_SYNC,
    SLOT_VOICE_A,
    SLOT_VOICE_B,
    SLOT_VOICE_C,
    SLOT_VOICE_D,
    SLOT_VOICE_E,
    SLOT_VOICE_F,
    SLOT_VOICE_LC_HEADER,
    SLOT_VOICE_LC_TERMINATOR,
    SLOT_WAKEUP,
    VOICE_SLOT_DTYPE,
    media_call_type,
    media_radio_ids,
    media_slot_type,
    media_timeslot,
)

OUTBOUND_SLOT_TYPE = {
    0: SLOT_VOICE_A,
    1: SLOT_VOICE_B,
    2: SLOT_VOICE_C,
    3: SLOT_VOICE_D,
    4: SLOT_VOICE_E,
    5: SLOT_VOICE_F,
}
OUTBOUND_INTERVAL = 0.060
# OpenBridge ingress can arrive in short bursts.  Three queued bursts provide
# 180 ms of playout cover while preserving a prompt normal call start.
OUTBOUND_JITTER_DEPTH = 3


def hytera_payload_to_dmrd(payload):
    """Convert Hytera's 34-byte, pair-swapped burst to a 33-byte DMRD burst."""
    if len(payload) != MEDIA_PAYLOAD_LEN:
        return None
    swapped = bytearray(payload)
    swapped[0::2], swapped[1::2] = payload[1::2], payload[0::2]
    return bytes(swapped[:33])


def dmrd_payload_to_hytera(payload, quality=0):
    """Convert a 33-byte DMRD burst to Hytera's pair-swapped 34-byte form."""
    if len(payload) != 33:
        return None
    wire = bytearray(34)
    for offset in range(0, 32, 2):
        wire[offset] = payload[offset + 1]
        wire[offset + 1] = payload[offset]
    wire[32] = quality & 0xff
    wire[33] = payload[32]
    return bytes(wire)


def _wire_id(dmr_id):
    return (int(dmr_id) << 8).to_bytes(4, 'little')


def _sync_payload(source, destination):
    """Build the capture-validated outbound EEEE call-start payload."""
    ids = destination.to_bytes(3, 'big') + source.to_bytes(3, 'big')
    interleaved_ids = b''.join(bytes((value, 0)) for value in ids)
    return b'\x00' * 6 + interleaved_ids + b'\x00' * 16


class HyteraOutboundPacer:
    """Deliver queued media at the RD985's observed 60 ms cadence."""

    def __init__(self, send_cb, clock=None, interval=OUTBOUND_INTERVAL,
                 jitter_depth=OUTBOUND_JITTER_DEPTH):
        self._send_cb = send_cb
        self._clock = clock or reactor
        self._interval = interval
        self._jitter_depth = jitter_depth
        self._queues = {1: deque(), 2: deque()}
        self._timers = {1: None, 2: None}
        self._next = {1: 0.0, 2: 0.0}
        self._last_sent = {1: None, 2: None}
        self._active = {1: False, 2: False}

    def reset(self):
        for ts in (1, 2):
            self.reset_slot(ts)

    def reset_slot(self, ts):
        timer = self._timers[ts]
        if timer is not None and timer.active():
            timer.cancel()
        self._queues[ts].clear()
        self._timers[ts] = None
        self._next[ts] = 0.0
        self._last_sent[ts] = None
        self._active[ts] = False

    def send_control(self, packet):
        self._send_cb(packet)

    def enqueue(self, ts, packet, jitter_depth=None):
        if ts not in self._queues:
            return False
        self._queues[ts].append(packet)
        if self._timers[ts] is None:
            now = self._clock.seconds()
            # Buffer only at call acquisition. Once RF playout is active, a
            # missing upstream burst must not trigger another 180 ms startup
            # delay in the middle of the call. IPSC2 forwards the next
            # available burst immediately after such a gap.
            if self._active[ts]:
                depth = 0
                self._next[ts] = max(
                    now, (self._last_sent[ts] or now) + self._interval)
            else:
                depth = self._jitter_depth
                self._next[ts] = now + depth * self._interval
            if jitter_depth is not None and not self._active[ts]:
                depth = max(0, jitter_depth)
                self._next[ts] = now + depth * self._interval
            self._arm(ts)
        return True

    def _arm(self, ts):
        delay = max(0.0, self._next[ts] - self._clock.seconds())
        self._timers[ts] = self._clock.callLater(delay, self._deliver, ts)

    def _deliver(self, ts):
        self._timers[ts] = None
        if not self._queues[ts]:
            self._next[ts] = 0.0
            return
        packet = self._queues[ts].popleft()
        self._send_cb(packet)
        self._last_sent[ts] = self._clock.seconds()
        self._active[ts] = True
        terminated = (
            len(packet) >= 20
            and packet[18:20]
            == SLOT_VOICE_LC_TERMINATOR.to_bytes(2, 'big'))
        if terminated:
            self._active[ts] = False
        if self._queues[ts]:
            now = self._clock.seconds()
            if terminated:
                self._next[ts] = now + self._jitter_depth * self._interval
            else:
                self._next[ts] = max(
                    self._next[ts] + self._interval, now + self._interval)
            self._arm(ts)
        else:
            self._next[ts] = 0.0


class HyteraVoiceTranslator:
    """Translate capture-validated RD985 group and private voice."""

    def __init__(self, peer_id=0, stream_factory=None):
        self._peer_id = int(peer_id).to_bytes(4, 'big')
        self._stream_factory = stream_factory or (lambda: os.urandom(4))
        self._streams = {1: None, 2: None}
        self._calls = {1: None, 2: None}
        self._last_wire_seq = {1: None, 2: None}
        self._dmrd_seq = 0
        self._out_streams = {1: None, 2: None}
        self._out_seq = {1: 0, 2: 0}
        self._out_last_dmrd_seq = {1: None, 2: None}
        self._out_late_join = {1: False, 2: False}
        self._out_private = {1: False, 2: False}

    def reset(self):
        self._streams = {1: None, 2: None}
        self._calls = {1: None, 2: None}
        self._last_wire_seq = {1: None, 2: None}
        self._dmrd_seq = 0
        self._out_streams = {1: None, 2: None}
        self._out_seq = {1: 0, 2: 0}
        self._out_last_dmrd_seq = {1: None, 2: None}
        self._out_late_join = {1: False, 2: False}
        self._out_private = {1: False, 2: False}

    def set_peer_id(self, peer_id):
        self._peer_id = int(peer_id).to_bytes(4, 'big')

    def translate_voice(self, data):
        """Return one 55-byte DMRD packet, or None for ignored/invalid input."""
        # Only the field-validated A9 72-byte form is admitted in this phase.
        if len(data) != MEDIA_MIN_LEN:
            return None
        call_type = media_call_type(data)
        if call_type not in (CALL_GROUP, CALL_PRIVATE):
            return None

        ts = media_timeslot(data)
        ids = media_radio_ids(data)
        slot_type = media_slot_type(data)
        if ts is None or ids is None or slot_type is None:
            return None
        if slot_type in (SLOT_WAKEUP, SLOT_HYTERA_SYNC):
            return None

        wire_seq = data[MEDIA_SEQUENCE_OFFSET]
        if self._last_wire_seq[ts] == wire_seq:
            return None

        source, destination = ids
        call_key = (source, destination, call_type)
        stream_id = self._streams[ts]

        if slot_type == SLOT_VOICE_LC_HEADER:
            if stream_id is None or self._calls[ts] != call_key:
                stream_id = self._stream_factory()
                if len(stream_id) != 4 or not any(stream_id):
                    return None
                self._streams[ts] = stream_id
                self._calls[ts] = call_key
            flags = (HBPF_DATA_SYNC << 4) | HBPF_SLT_VHEAD
        elif slot_type == SLOT_VOICE_LC_TERMINATOR:
            if stream_id is None or self._calls[ts] != call_key:
                return None
            flags = (HBPF_DATA_SYNC << 4) | HBPF_SLT_VTERM
        elif slot_type in VOICE_SLOT_DTYPE:
            if stream_id is None or self._calls[ts] != call_key:
                return None
            dtype = VOICE_SLOT_DTYPE[slot_type]
            frame_type = (
                HBPF_VOICE_SYNC if slot_type == SLOT_VOICE_A else HBPF_VOICE)
            flags = (frame_type << 4) | dtype
        else:
            return None

        payload = hytera_payload_to_dmrd(
            data[MEDIA_PAYLOAD_OFFSET:MEDIA_PAYLOAD_OFFSET + MEDIA_PAYLOAD_LEN])
        if payload is None:
            return None

        if ts == 2:
            flags |= 0x80
        if call_type == CALL_PRIVATE:
            flags |= 0x40
        dmrd = (
            DMRD
            + bytes([self._dmrd_seq])
            + source.to_bytes(3, 'big')
            + destination.to_bytes(3, 'big')
            + self._peer_id
            + bytes([flags])
            + stream_id
            + payload
            + b'\x00\x00'
        )
        self._dmrd_seq = (self._dmrd_seq + 1) & 0xff
        self._last_wire_seq[ts] = wire_seq

        if slot_type == SLOT_VOICE_LC_TERMINATOR:
            self._streams[ts] = None
            self._calls[ts] = None

        return dmrd

    def translate_group(self, data):
        """Compatibility wrapper for callers predating private voice support."""
        return self.translate_voice(data)

    def _build_outbound(self, ts, packet_type, slot_type, source, destination,
                        payload, call_start=False, private_call=False):
        packet = bytearray(72)
        # IPSC2's master downlink keeps 5A5A5A5A on every burst except Voice
        # A, for the whole call, including the voice LC header. Voice A is
        # EEEE1111. A zero prefix does not appear on that downlink.
        if slot_type == SLOT_VOICE_A:
            packet[:4] = b'\xee\xee\x11\x11'
        else:
            packet[:4] = b'\x5a' * 4
        packet[4:8] = self._out_seq[ts].to_bytes(4, 'little')
        # Normal TS2 traffic uses the 0x40 packet-type bit. IPSC2's
        # capture-validated late entry uses ordinary master voice type 0x01
        # on both slots, while retaining the slot marker at bytes 16:18.
        packet[8] = packet_type | (
            0x40 if ts == 2 and not self._out_late_join[ts] else 0)
        packet[9:16] = b'\x00\x05\x01' + bytes((ts,)) + b'\x00\x00\x00'
        packet[16:18] = b'\x11\x11' if ts == 1 else b'\x22\x22'
        packet[18:20] = slot_type.to_bytes(2, 'big')
        packet[20:22] = b'\x11\x11'
        packet[22:24] = b'\x11\x11' if call_start else b'\x00\x00'
        packet[24:26] = b'\x00\x00' if call_start else b'\x10\x00'
        packet[26:60] = payload
        packet[62] = CALL_PRIVATE if private_call else CALL_GROUP
        packet[63:67] = _wire_id(destination)
        packet[67:71] = _wire_id(source)
        return bytes(packet)

    def encode_voice(self, dmrd, quality=0, late_join=False,
                     late_join_sequence=None):
        """Encode capture-validated group or private DMRD voice to Hytera."""
        if len(dmrd) < 53 or dmrd[:4] != DMRD:
            return None

        flags = dmrd[15]
        private_call = bool(flags & 0x40)
        ts = 2 if flags & 0x80 else 1
        frame_type = (flags & 0x30) >> 4
        dtype = flags & 0x0f
        stream = dmrd[16:20]
        source = int.from_bytes(dmrd[5:8], 'big')
        destination = int.from_bytes(dmrd[8:11], 'big')

        if frame_type == HBPF_DATA_SYNC and dtype == HBPF_SLT_VHEAD:
            if self._out_streams[ts] == stream:
                return None
            self._out_streams[ts] = stream
            self._out_seq[ts] = 0
            self._out_last_dmrd_seq[ts] = None
            self._out_late_join[ts] = False
            self._out_private[ts] = private_call
            payload = dmrd_payload_to_hytera(dmrd[20:53], quality)
            if payload is None:
                return None
            packet = self._build_outbound(
                ts, 0x01, SLOT_VOICE_LC_HEADER, source, destination, payload,
                private_call=private_call)
            self._out_seq[ts] = 1
            return ts, packet, False

        if frame_type in (HBPF_VOICE, HBPF_VOICE_SYNC):
            slot_type = OUTBOUND_SLOT_TYPE.get(dtype)
            if slot_type is None or not any(stream):
                return None
            # A local RF activation can interrupt a network stream that this
            # encoder had already started.  Once routing releases that stream
            # after RF de-key, treat the first resumed burst as a new
            # capture-compatible late entry.  This reset is deliberately
            # one-shot: the branch below marks the stream as late-join, so
            # subsequent bursts retain sequence and normal 60 ms pacing.
            if (late_join and not private_call
                    and self._out_streams[ts] == stream
                    and not self._out_late_join[ts]):
                self._out_streams[ts] = None
                self._out_last_dmrd_seq[ts] = None
            if self._out_streams[ts] != stream:
                self._out_streams[ts] = stream
                self._out_late_join[ts] = late_join and not private_call
                self._out_private[ts] = private_call
                self._out_seq[ts] = (
                    late_join_sequence
                    if late_join and late_join_sequence is not None
                    else dmrd[4] if late_join else 0)
                self._out_last_dmrd_seq[ts] = dmrd[4]
                if late_join and not private_call:
                    payload = dmrd_payload_to_hytera(
                        dmrd[20:53], quality)
                    if slot_type is None or payload is None:
                        return None
                    packet = self._build_outbound(
                        ts, 0x01, slot_type, source, destination,
                        payload, private_call=private_call)
                    self._out_seq[ts] = (
                        self._out_seq[ts] + 1) & 0xffffffff
                    # IPSC2 joins a running call with the current voice burst,
                    # without synthesizing a new LC header or EEEE call start.
                    return ts, packet, False
                packet = self._build_outbound(
                    ts, 0x02, SLOT_HYTERA_SYNC, source, destination,
                    _sync_payload(source, destination), call_start=True,
                    private_call=private_call)
                self._out_seq[ts] = 1
                # OpenBridge streams often begin with voice rather than VHEAD.
                # Acquire the call first; losing this one 60 ms burst is safer
                # than transmitting media before the RD985 has call context.
                return ts, packet, False
            if self._out_late_join[ts]:
                if late_join_sequence is not None:
                    self._out_seq[ts] = late_join_sequence
                elif self._out_last_dmrd_seq[ts] is not None:
                    delta = (dmrd[4] - self._out_last_dmrd_seq[ts]) & 0xff
                    self._out_seq[ts] = (
                        self._out_seq[ts] - 1 + delta) & 0xffffffff
                self._out_last_dmrd_seq[ts] = dmrd[4]
            payload = dmrd_payload_to_hytera(dmrd[20:53], quality)
            if slot_type is None or payload is None:
                return None
            packet = self._build_outbound(
                ts, 0x01, slot_type, source, destination, payload,
                private_call=self._out_private[ts])
            self._out_seq[ts] = (self._out_seq[ts] + 1) & 0xffffffff
            return ts, packet, True

        if frame_type == HBPF_DATA_SYNC and dtype == HBPF_SLT_VTERM:
            if self._out_streams[ts] != stream:
                return None
            payload = dmrd_payload_to_hytera(dmrd[20:53], quality)
            if payload is None:
                return None
            packet = self._build_outbound(
                ts, 0x03, SLOT_VOICE_LC_TERMINATOR, source, destination,
                payload, private_call=self._out_private[ts])
            self._out_seq[ts] = (self._out_seq[ts] + 1) & 0xffffffff
            self._out_streams[ts] = None
            self._out_last_dmrd_seq[ts] = None
            self._out_late_join[ts] = False
            self._out_private[ts] = False
            return ts, packet, True

        return None

    def encode_group(self, dmrd, quality=0, late_join=False,
                     late_join_sequence=None):
        """Encode group DMRD voice; retain the historical private-call gate."""
        if len(dmrd) < 16 or dmrd[15] & 0x40:
            return None
        return self.encode_voice(
            dmrd, quality, late_join=late_join,
            late_join_sequence=late_join_sequence)
