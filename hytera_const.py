#!/usr/bin/env python3
###############################################################################
#   Hytera IP Multi-site Connect protocol constants
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

# Public service defaults used by Hytera CPS.
DEFAULT_P2P_PORT = 50000
DEFAULT_DMR_PORT = 50001
DEFAULT_RDAC_PORT = 50002
PORTS_PER_SLOT = 3

# P2P negotiation packet markers.
P2P_COMMAND = b'P2P'
P2P_COMMAND_REPLY = 0x50
P2P_PING_MARKER = b'\x0a\x00\x00\x00\x14'
P2P_ACK_MARKER = b'\x0c\x00\x00\x00\x14'

P2P_REGISTRATION = 0x10
P2P_DMR_STARTUP = 0x11
P2P_RDAC_STARTUP = 0x12

P2P_MIN_COMMAND_LEN = 21
P2P_COMMAND_TYPE_OFFSET = 20

# The negotiated service redirect is encoded little-endian.
P2P_REDIRECT_ID = 0x0B
P2P_REDIRECT_SUFFIX = b'\xff\x01'

# DMR media packet layout observed on RD6xx/RD9xx IP Multi-site links.
MEDIA_MIN_LEN = 72
MEDIA_SOURCE_PORT_OFFSET = 0
MEDIA_SEQUENCE_OFFSET = 4
MEDIA_PACKET_TYPE_OFFSET = 8
MEDIA_TIMESLOT_OFFSET = 16
MEDIA_SLOT_TYPE_OFFSET = 18
MEDIA_COLOR_CODE_OFFSET = 20
MEDIA_FRAME_TYPE_OFFSET = 22
MEDIA_PAYLOAD_OFFSET = 26
MEDIA_PAYLOAD_LEN = 34
MEDIA_CALL_TYPE_OFFSET = 62
MEDIA_DESTINATION_OFFSET = 63
MEDIA_SOURCE_OFFSET = 67

TIMESLOT_1 = b'\x11\x11'
TIMESLOT_2 = b'\x22\x22'
CALL_PRIVATE = 0x00
CALL_GROUP = 0x01

# Proxy control datagrams. They are private RYSEN control messages, not Hytera.
PRIN = b'PRIN'
PRCL = b'PRCL'


def p2p_command_type(data):
    """Return the P2P negotiation command type, or None for other packets."""
    if len(data) < P2P_MIN_COMMAND_LEN or data[:3] != P2P_COMMAND:
        return None
    return data[P2P_COMMAND_TYPE_OFFSET]


def is_p2p_ping(data):
    return len(data) >= 15 and data[4:9] == P2P_PING_MARKER


def is_p2p_ack(data):
    return len(data) >= 9 and data[4:9] == P2P_ACK_MARKER


def media_timeslot(data):
    """Return DMR timeslot 1/2 for a complete media packet."""
    if len(data) < MEDIA_MIN_LEN:
        return None
    marker = data[MEDIA_TIMESLOT_OFFSET:MEDIA_TIMESLOT_OFFSET + 2]
    if marker == TIMESLOT_1:
        return 1
    if marker == TIMESLOT_2:
        return 2
    return None


def media_radio_ids(data):
    """Return (source, destination) 24-bit DMR IDs from a media packet."""
    if len(data) < MEDIA_MIN_LEN:
        return None
    source = int.from_bytes(
        data[MEDIA_SOURCE_OFFSET:MEDIA_SOURCE_OFFSET + 4], 'little') >> 8
    destination = int.from_bytes(
        data[MEDIA_DESTINATION_OFFSET:MEDIA_DESTINATION_OFFSET + 4], 'little') >> 8
    return source, destination
