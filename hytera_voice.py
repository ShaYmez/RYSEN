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
    DATA_DTYPE_SLOT,
    DATA_SLOT_DTYPE,
    MEDIA_MIN_LEN,
    MEDIA_PAYLOAD_LEN,
    MEDIA_PAYLOAD_OFFSET,
    MEDIA_SEQUENCE_OFFSET,
    SLOT_DATA_BLOCK,
    SLOT_DATA_BLOCK_34,
    SLOT_DATA_CSBK,
    SLOT_DATA_HEADER,
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
# ETSI DMR: one occupied timeslot is 30 ms; voice on a slot repeats every
# 60 ms (one burst per 60 ms frame). Hytera IP Multi-site docs describe a
# repeater "Jitter Buffer Length" in 60 ms steps (default 1, up to 8) for
# poor IP paths — extra length trades delay for continuity.
OUTBOUND_INTERVAL = 0.060
# Three frames (~180 ms) at call start: between Hytera's default 1×60 ms
# buffer and a deep 3×60 ms IP jitter setting, without living at max delay.
OUTBOUND_JITTER_DEPTH = 3
# When depth exceeds the jitter target, schedule slightly faster than 60 ms
# but never faster than this minimum spacing from the last RF burst sent.
OUTBOUND_CATCHUP_INTERVAL = 0.050
# Stall recovery only (matches Hytera's 8×60 ms upper jitter-buffer setting).
# Shed down to OUTBOUND_JITTER_DEPTH in one step; do not trim every arrival.
# SMS/data is not trimmed — a bunched preamble can exceed eight.
OUTBOUND_MAX_QUEUE = 8
_DATA_SLOT_TYPES = frozenset({
    SLOT_DATA_CSBK,
    SLOT_DATA_HEADER,
    SLOT_DATA_BLOCK,
    SLOT_DATA_BLOCK_34,
})


def hytera_payload_to_dmrd(payload):
    """Convert Hytera's 34-byte, pair-swapped burst to a 33-byte DMRD burst."""
    if len(payload) != MEDIA_PAYLOAD_LEN:
        return None
    swapped = bytearray(payload)
    swapped[0::2], swapped[1::2] = payload[1::2], payload[0::2]
    return bytes(swapped[:33])


def dmr_burst_dtype(payload):
    """Return the 4-bit ETSI slot-type data type from a 33-byte DMR burst."""
    if len(payload) != 33:
        return None
    value = int.from_bytes(payload, 'big')

    def bits(start, length):
        shift = 264 - (start + length)
        return (value >> shift) & ((1 << length) - 1)

    slot_type = (bits(98, 10) << 10) | bits(156, 10)
    return (slot_type >> 12) & 0x0F


def hytera_uplink_rssi(packet):
    """Raw byte at the voice-payload quality offset.

    IPSC2 oracle uplinks from the RD985 leave this byte at 0. The same offset
    is where outbound voice writes Homebrew RSSI toward the repeater. A
    non-zero uplink value is copied into DMRD byte 54 unchanged.
    """
    offset = MEDIA_PAYLOAD_OFFSET + 32
    if len(packet) <= offset:
        return 0
    return packet[offset]


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
                 jitter_depth=OUTBOUND_JITTER_DEPTH,
                 max_queue=OUTBOUND_MAX_QUEUE):
        self._send_cb = send_cb
        self._clock = clock or reactor
        self._interval = interval
        self._jitter_depth = jitter_depth
        self._catchup_interval = OUTBOUND_CATCHUP_INTERVAL
        self._max_queue = max(1, int(max_queue))
        self._queues = {1: deque(), 2: deque()}
        self._timers = {1: None, 2: None}
        self._grid = {1: 0.0, 2: 0.0}
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
        self._grid[ts] = 0.0
        self._next[ts] = 0.0
        self._last_sent[ts] = None
        self._active[ts] = False

    def send_control(self, packet):
        self._send_cb(packet)

    def mark_active(self, ts):
        """Treat an immediate control send as the start of RF playout."""
        if ts not in self._active:
            return
        self._active[ts] = True
        self._last_sent[ts] = self._clock.seconds()

    def is_active(self, ts):
        return bool(self._active.get(ts))

    def _step_for_depth(self, ts):
        if len(self._queues[ts]) > self._jitter_depth:
            return self._catchup_interval
        return self._interval

    def _plan_next_fire(self, ts, terminated=False):
        """Schedule the next burst without sub-interval back-to-back delivery."""
        now = self._clock.seconds()
        if terminated:
            self._active[ts] = False
            self._grid[ts] = 0.0
            if not self._queues[ts]:
                self._next[ts] = 0.0
                return
            self._grid[ts] = now + self._jitter_depth * self._interval
            self._next[ts] = self._grid[ts]
            return
        step = self._step_for_depth(ts)
        if self._grid[ts] <= 0.0:
            self._grid[ts] = now
        self._grid[ts] += step
        floor = (self._last_sent[ts] or now) + step
        scheduled = max(self._grid[ts], floor)
        if scheduled < now:
            scheduled = now if floor <= now else floor
        self._next[ts] = scheduled

    @staticmethod
    def _is_voice_media(packet):
        if len(packet) < 20:
            return True
        slot = int.from_bytes(packet[18:20], 'big')
        return slot not in _DATA_SLOT_TYPES

    def enqueue(self, ts, packet, jitter_depth=None):
        if ts not in self._queues:
            return False
        queue = self._queues[ts]
        queue.append(packet)
        if self._is_voice_media(packet):
            if len(queue) > self._max_queue:
                while len(queue) > self._jitter_depth:
                    queue.popleft()
        if self._timers[ts] is None:
            now = self._clock.seconds()
            # Buffer only at call acquisition. Once RF playout is active, a
            # missing upstream burst must not trigger another 180 ms startup
            # delay in the middle of the call. IPSC2 forwards the next
            # available burst immediately after such a gap.
            if self._active[ts]:
                step = self._interval
                self._grid[ts] = (self._last_sent[ts] or now) + step
                self._next[ts] = self._grid[ts] if self._grid[ts] >= now else now
            else:
                depth = self._jitter_depth
                if jitter_depth is not None:
                    depth = max(0, jitter_depth)
                self._grid[ts] = now + depth * self._interval
                self._next[ts] = self._grid[ts]
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
        if self._queues[ts]:
            self._plan_next_fire(ts, terminated=terminated)
            self._arm(ts)
        else:
            self._grid[ts] = 0.0
            self._next[ts] = 0.0
            if terminated:
                self._active[ts] = False


class HyteraVoiceTranslator:
    """Translate capture-validated RD985 voice and private SMS/data."""

    def __init__(self, peer_id=0, stream_factory=None):
        self._peer_id = int(peer_id).to_bytes(4, 'big')
        self._stream_factory = stream_factory or (lambda: os.urandom(4))
        self._streams = {1: None, 2: None}
        self._calls = {1: None, 2: None}
        self._last_data_slot = {1: None, 2: None}
        self._last_wire_seq = {1: None, 2: None}
        self._dmrd_seq = 0
        self._out_streams = {1: None, 2: None}
        self._out_seq = {1: 0, 2: 0}
        self._out_last_dmrd_seq = {1: None, 2: None}
        self._out_late_join = {1: False, 2: False}
        self._out_private = {1: False, 2: False}
        self._out_last_data_dtype = {1: None, 2: None}

    def reset(self):
        self._streams = {1: None, 2: None}
        self._calls = {1: None, 2: None}
        self._last_data_slot = {1: None, 2: None}
        self._last_wire_seq = {1: None, 2: None}
        self._dmrd_seq = 0
        self._out_streams = {1: None, 2: None}
        self._out_seq = {1: 0, 2: 0}
        self._out_last_dmrd_seq = {1: None, 2: None}
        self._out_late_join = {1: False, 2: False}
        self._out_private = {1: False, 2: False}
        self._out_last_data_dtype = {1: None, 2: None}

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

        payload = hytera_payload_to_dmrd(
            data[MEDIA_PAYLOAD_OFFSET:MEDIA_PAYLOAD_OFFSET + MEDIA_PAYLOAD_LEN])
        if payload is None:
            return None

        if slot_type == SLOT_VOICE_LC_HEADER:
            if stream_id is None or self._calls[ts] != call_key:
                stream_id = self._stream_factory()
                if len(stream_id) != 4 or not any(stream_id):
                    return None
                self._streams[ts] = stream_id
                self._calls[ts] = call_key
            flags = (HBPF_DATA_SYNC << 4) | HBPF_SLT_VHEAD
            self._last_data_slot[ts] = None
        elif slot_type == SLOT_VOICE_LC_TERMINATOR:
            if stream_id is None or self._calls[ts] != call_key:
                return None
            flags = (HBPF_DATA_SYNC << 4) | HBPF_SLT_VTERM
            self._last_data_slot[ts] = None
        elif slot_type in VOICE_SLOT_DTYPE:
            if stream_id is None or self._calls[ts] != call_key:
                return None
            dtype = VOICE_SLOT_DTYPE[slot_type]
            frame_type = (
                HBPF_VOICE_SYNC if slot_type == SLOT_VOICE_A else HBPF_VOICE)
            flags = (frame_type << 4) | dtype
        elif slot_type in DATA_SLOT_DTYPE:
            # SMS has no VTERM. A later preamble after header/blocks is a
            # new message, even when the same radios talk again.
            start_new = (
                stream_id is None
                or self._calls[ts] != call_key
                or (slot_type == SLOT_DATA_CSBK
                    and self._last_data_slot[ts] not in (None, SLOT_DATA_CSBK)))
            if start_new:
                stream_id = self._stream_factory()
                if len(stream_id) != 4 or not any(stream_id):
                    return None
                self._streams[ts] = stream_id
                self._calls[ts] = call_key
            burst_dtype = dmr_burst_dtype(payload)
            dtype = (
                burst_dtype if burst_dtype in (3, 6, 7, 8)
                else DATA_SLOT_DTYPE[slot_type])
            flags = (HBPF_DATA_SYNC << 4) | dtype
            self._last_data_slot[ts] = slot_type
        else:
            return None

        rssi = hytera_uplink_rssi(data)

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
            + bytes([0, rssi])
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
        # The field-proven RYSEN 1111 header is zero-prefixed. IPSC2 does not
        # send that header form: its master downlink uses 5A5A5A5A on EEEE,
        # ordinary voice and terminators. The BBBB envelope (DMR Voice E)
        # uses EEEE1111.
        if slot_type == SLOT_VOICE_E:
            packet[:4] = b'\xee\xee\x11\x11'
        elif slot_type == SLOT_VOICE_LC_HEADER:
            packet[:4] = b'\x00' * 4
        else:
            packet[:4] = b'\x5a' * 4
        packet[4:8] = self._out_seq[ts].to_bytes(4, 'little')
        # IPSC2 master-to-repeater traffic uses 01/02/03 on both slots. The
        # 0x40 bit is present in repeater-to-master traffic, not TS2 downlink.
        packet[8] = packet_type
        packet[9:16] = b'\x00\x05\x01' + bytes((ts,)) + b'\x00\x00\x00'
        packet[16:18] = b'\x11\x11' if ts == 1 else b'\x22\x22'
        packet[18:20] = slot_type.to_bytes(2, 'big')
        if slot_type == SLOT_VOICE_LC_TERMINATOR:
            packet[20:26] = b'\x00' * 6
        else:
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
            self._out_last_data_dtype[ts] = None
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
                self._out_last_data_dtype[ts] = None
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

        if frame_type == HBPF_DATA_SYNC and dtype in DATA_DTYPE_SLOT:
            slot_type = DATA_DTYPE_SLOT[dtype]
            payload = dmrd_payload_to_hytera(dmrd[20:53], quality)
            if payload is None or not any(stream):
                return None
            last_dtype = self._out_last_data_dtype[ts]
            # MMDVM often uses one stream for the CSBK preamble and another
            # for the header and blocks. OpenBridge peers can go further and
            # give every preamble CSBK its own stream ID. That is still one
            # RF SMS. A CSBK after header/blocks is a later text, not a join.
            join_body = (
                self._out_streams[ts] is not None
                and (
                    (dtype in (6, 7, 8) and last_dtype in (3, 6, 7, 8))
                    or (dtype == 3 and last_dtype == 3)))
            new_stream = self._out_streams[ts] != stream and not join_body
            if new_stream:
                self._out_streams[ts] = stream
                self._out_seq[ts] = 0
                self._out_last_dmrd_seq[ts] = None
                self._out_late_join[ts] = False
                self._out_private[ts] = private_call
            else:
                self._out_streams[ts] = stream
            packet = self._build_outbound(
                ts, 0x01, slot_type, source, destination, payload,
                private_call=private_call or self._out_private[ts])
            self._out_seq[ts] = (self._out_seq[ts] + 1) & 0xffffffff
            self._out_last_data_dtype[ts] = dtype
            return ts, packet, not new_stream

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
            self._out_last_data_dtype[ts] = None
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
