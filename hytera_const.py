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

SLOT_VOICE_LC_HEADER = 0x1111
SLOT_VOICE_LC_TERMINATOR = 0x2222
SLOT_VOICE_C = 0x7777
SLOT_VOICE_D = 0x8888
SLOT_VOICE_E = 0x9999
SLOT_VOICE_F = 0xAAAA
SLOT_VOICE_A = 0xBBBB
SLOT_VOICE_B = 0xCCCC
SLOT_WAKEUP = 0xDDDD
SLOT_HYTERA_SYNC = 0xEEEE

# DMRD voice subtype values represented by each Hytera slot marker.
VOICE_SLOT_DTYPE = {
    SLOT_VOICE_A: 0,
    SLOT_VOICE_B: 1,
    SLOT_VOICE_C: 2,
    SLOT_VOICE_D: 3,
    SLOT_VOICE_E: 4,
    SLOT_VOICE_F: 5,
}

# Proxy control datagrams. They are private RYSEN control messages, not Hytera.
PRIN = b'PRIN'
PRCL = b'PRCL'

# The first capture-validated RDAC identity response is emitted after the
# OK-DMR step-1 request. It exposes the repeater's 24-bit DMR ID at 18:21.
RDAC_ID_RESPONSE_PREFIX = b'\x7e\x04\x00\x00'
RDAC_REPEATER_ID_OFFSET = 18
RDAC_STEP0_REQUEST = bytes.fromhex('7e0400fe20100000000c60e1')
RDAC_STEP0_RESPONSE = bytes.fromhex('7e0400fd')
RDAC_STEP1_REQUEST = bytes.fromhex(
    '7e0400002010000100189b6002040005006400000001c403')
RDAC_STEP1_RESPONSE = bytes.fromhex('7e040010')
RDAC_STEP3_REQUEST = bytes.fromhex('7e04001020100001000c61ce')
RDAC_STEP4_REQUEST_1 = bytes.fromhex('7e04001020100002000c61cd')
RDAC_STEP4_REQUEST_2 = bytes.fromhex(
    '7e04000020100002001958a002d4020600640000000200f003')
RDAC_STEP6_REQUEST_1 = bytes.fromhex('7e04001020100003000c61cc')
RDAC_STEP6_REQUEST_2 = bytes.fromhex(
    '7e040000201000030019738402d68206000064000000026e03')
RDAC_STEP7_REQUEST = bytes.fromhex(
    '7e040000201000040019579f02d4020600640000000201ef03')
RDAC_STEP10_REQUEST = bytes.fromhex(
    '7e0400002010001500189c4b02050005006400000001c303')
RDAC_STEP12_REQUEST_1 = bytes.fromhex('7e04001020100015000c61ba')
RDAC_STEP12_REQUEST_2 = bytes.fromhex('7e0400fb20100016000c60ce')
RDAC_STEP12_RESPONSE = bytes.fromhex('7e0400fa')


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


def media_slot_type(data):
    """Return the 16-bit Hytera slot/burst type."""
    if len(data) < MEDIA_MIN_LEN:
        return None
    return int.from_bytes(
        data[MEDIA_SLOT_TYPE_OFFSET:MEDIA_SLOT_TYPE_OFFSET + 2], 'big')


def media_call_type(data):
    """Return the Hytera private/group call marker."""
    if len(data) < MEDIA_MIN_LEN:
        return None
    return data[MEDIA_CALL_TYPE_OFFSET]


def rdac_repeater_id(data):
    """Return the 24-bit repeater ID from the validated RDAC identity response."""
    if (len(data) < RDAC_REPEATER_ID_OFFSET + 3
            or not data.startswith(RDAC_ID_RESPONSE_PREFIX)):
        return None
    repeater_id = int.from_bytes(
        data[RDAC_REPEATER_ID_OFFSET:RDAC_REPEATER_ID_OFFSET + 3], 'little')
    return repeater_id or None
