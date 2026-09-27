#!/usr/bin/env python3
"""Capture-validated metadata parsers for Hytera RDAC responses."""

from hytera_const import RDAC_ID_RESPONSE_PREFIX, rdac_repeater_id

RDAC_IDENTITY_MIN_LEN = 216
RDAC_CHANNEL_MIN_LEN = 37


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
