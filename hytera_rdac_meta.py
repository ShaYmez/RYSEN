#!/usr/bin/env python3
###############################################################################
#   Capture-validated metadata parsers for Hytera RDAC responses
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

import struct

from hytera_const import RDAC_ID_RESPONSE_PREFIX, rdac_repeater_id

RDAC_IDENTITY_MIN_LEN = 216
RDAC_CHANNEL_MIN_LEN = 37

# Live poll body from the GB7NR/IPSC2 capture. The identification step-4
# request uses a different RCP tail and does not return slot RSSI.
RDAC_RSSI_REQUEST_BODY = bytes.fromhex('02d40206006400000001807103')
RDAC_RSSI_REPLY_MARK = bytes.fromhex('02d482')
RDAC_RSSI_REPLY_LEN = 34
RDAC_RSSI_TS1_OFFSET = 26
RDAC_RSSI_TS2_OFFSET = 30
# IPSC2 keeps a sample only inside this window. -200 is the idle-slot value.
RDAC_RSSI_DBM_MIN = -150.0
RDAC_RSSI_DBM_MAX = -30.0
HYTERA_RSSI_POLL_INTERVAL = 1.0


def _utf16le_field(data, start, end):
    """Decode a fixed-width NUL-terminated UTF-16LE RDAC field safely."""
    field = data[start:end]
    try:
        return field.decode('utf-16le').split('\x00', 1)[0].strip()
    except UnicodeDecodeError:
        return ''


def parse_rdac_identity(data):
    """Parse the state-6 RDAC identity response, or return an empty mapping."""
    if (len(data) < RDAC_IDENTITY_MIN_LEN
            or not data.startswith(RDAC_ID_RESPONSE_PREFIX)):
        return {}
    repeater_id = rdac_repeater_id(data)
    return {
        'repeater_id': repeater_id,
        'firmware': _utf16le_field(data, 56, 88),
        'callsign': _utf16le_field(data, 88, 108),
        'hardware': _utf16le_field(data, 120, 184),
        'serial': _utf16le_field(data, 184, 216),
        'identity_raw': data.hex(),
    }


def hrnp_checksum(packet):
    """16-bit one's-complement sum of the HRNP datagram, checksum field zeroed."""
    data = bytearray(packet)
    if len(data) < 12:
        return None
    data[10] = 0
    data[11] = 0
    if len(data) % 2:
        data.append(0)
    total = 0
    for offset in range(0, len(data), 2):
        total += int.from_bytes(data[offset:offset + 2], 'big')
    while total > 0xffff:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def build_rdac_rssi_request(sequence):
    """Master-to-repeater RSSI poll. Sequence is the HRNP packet number."""
    packet = bytearray(12 + len(RDAC_RSSI_REQUEST_BODY))
    packet[0] = 0x7e
    packet[1] = 0x04
    packet[4] = 0x20
    packet[5] = 0x10
    packet[6:8] = (sequence & 0xffff).to_bytes(2, 'big')
    packet[8:10] = len(packet).to_bytes(2, 'big')
    packet[12:] = RDAC_RSSI_REQUEST_BODY
    packet[10:12] = hrnp_checksum(packet).to_bytes(2, 'big')
    return bytes(packet)


def _rdac_slot_dbm(packet, offset):
    """The two bytes are the high half of a little-endian float32, in dBm."""
    word = int.from_bytes(packet[offset:offset + 2], 'little')
    return struct.unpack('<f', struct.pack('<I', word << 16))[0]


def rdac_rssi_byte(dbm):
    """Map a dBm reading onto Homebrew's integer RSSI byte, or 0 to hide it."""
    if dbm < RDAC_RSSI_DBM_MIN or dbm > RDAC_RSSI_DBM_MAX:
        return 0
    return min(255, max(1, int(round(-dbm))))


def parse_rdac_rssi(packet):
    """Return per-slot RSSI bytes from a 34-byte poll reply, or None."""
    if (len(packet) != RDAC_RSSI_REPLY_LEN
            or not packet.startswith(RDAC_ID_RESPONSE_PREFIX)
            or packet[12:15] != RDAC_RSSI_REPLY_MARK):
        return None
    if hrnp_checksum(packet) != int.from_bytes(packet[10:12], 'big'):
        return None
    repeater_id = rdac_repeater_id(packet)
    if not repeater_id:
        return None
    return {
        'repeater_id': repeater_id,
        1: rdac_rssi_byte(_rdac_slot_dbm(packet, RDAC_RSSI_TS1_OFFSET)),
        2: rdac_rssi_byte(_rdac_slot_dbm(packet, RDAC_RSSI_TS2_OFFSET)),
    }


def parse_rdac_channel(data):
    """Parse the state-10 RDAC channel response, or return an empty mapping."""
    if (len(data) < RDAC_CHANNEL_MIN_LEN
            or not data.startswith(RDAC_ID_RESPONSE_PREFIX)):
        return {}
    return {
        'mode_raw': data[26],
        'tx_frequency': int.from_bytes(data[29:33], 'little'),
        'rx_frequency': int.from_bytes(data[33:37], 'little'),
        'channel_raw': data.hex(),
    }
