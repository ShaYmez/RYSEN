#!/usr/bin/env python3
###############################################################################
#   Hytera IP Multi-site Connect inbound voice to internal DMRD translation
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

import os

from const import (
    DMRD,
    HBPF_DATA_SYNC,
    HBPF_SLT_VHEAD,
    HBPF_SLT_VTERM,
    HBPF_VOICE,
    HBPF_VOICE_SYNC,
)
from hytera_const import (
    CALL_GROUP,
    MEDIA_MIN_LEN,
    MEDIA_PAYLOAD_LEN,
    MEDIA_PAYLOAD_OFFSET,
    MEDIA_SEQUENCE_OFFSET,
    SLOT_HYTERA_SYNC,
    SLOT_VOICE_A,
    SLOT_VOICE_LC_HEADER,
    SLOT_VOICE_LC_TERMINATOR,
    SLOT_WAKEUP,
    VOICE_SLOT_DTYPE,
    media_call_type,
    media_radio_ids,
    media_slot_type,
    media_timeslot,
)


def hytera_payload_to_dmrd(payload):
    """Convert Hytera's 34-byte, pair-swapped burst to a 33-byte DMRD burst."""
    if len(payload) != MEDIA_PAYLOAD_LEN:
        return None
    swapped = bytearray(payload)
    swapped[0::2], swapped[1::2] = payload[1::2], payload[0::2]
    return bytes(swapped[:33])


class HyteraVoiceTranslator:
    """Translate capture-validated RD985 group voice into internal DMRD."""

    def __init__(self, peer_id=0, stream_factory=None):
        self._peer_id = int(peer_id).to_bytes(4, 'big')
        self._stream_factory = stream_factory or (lambda: os.urandom(4))
        self._streams = {1: None, 2: None}
        self._calls = {1: None, 2: None}
        self._last_wire_seq = {1: None, 2: None}
        self._dmrd_seq = 0

    def reset(self):
        self._streams = {1: None, 2: None}
        self._calls = {1: None, 2: None}
        self._last_wire_seq = {1: None, 2: None}
        self._dmrd_seq = 0

    def set_peer_id(self, peer_id):
        self._peer_id = int(peer_id).to_bytes(4, 'big')

    def translate_group(self, data):
        """Return one 55-byte DMRD packet, or None for ignored/invalid input."""
        # Only the field-validated A9 72-byte form is admitted in this phase.
        if len(data) != MEDIA_MIN_LEN:
            return None
        if media_call_type(data) != CALL_GROUP:
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
        call_key = (source, destination, CALL_GROUP)
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
