#!/usr/bin/env python3
###############################################################################
#   IPSC peer metadata helpers (registration packet layout per node-dmr-lib)
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

from dmr_utils3.utils import int_id

# PeerMode slot / signaling values (node-dmr-lib PeerMode.js)
_SLOT_LABELS = {
    0: 'disabled',
    1: 'local',
    2: 'IPSC',
    3: 'reserved',
}
_SIGNALING_LABELS = {
    0: 'none',
    1: 'analog',
    2: 'digital',
    3: 'mixed',
}
_PROTOCOL_TYPE_LABELS = {
    1: 'IPSC',
    2: 'Capacity+',
    3: 'Application',
    4: 'Linked Capacity+',
}


def parse_ipsc_peer_status(data, offset=5):
    """Parse mode + flags + protocol bytes from an IPSC management packet body.

    Layout after the 5-byte header (opcode + peer_id), per
    https://github.com/rick51231/node-dmr-lib — MasterRegReq / MasterAliveReq.
    Returns None if the packet is too short for a mode byte.
    """
    if len(data) < offset + 1:
        return None
    body = data[offset:]
    mode = body[0]
    flags = body[1:5] if len(body) >= 5 else b'\x00\x00\x00\x00'
    protocol = body[5:9] if len(body) >= 9 else b'\x00\x00\x00\x00'
    return {'mode': mode, 'flags': flags, 'protocol': protocol}


def decode_peer_mode(mode_byte):
    return {
        'status': (mode_byte >> 6) & 0b11,
        'signaling': (mode_byte >> 4) & 0b11,
        'slot1': (mode_byte >> 2) & 0b11,
        'slot2': mode_byte & 0b11,
    }


def format_protocol_version(protocol_bytes):
    """Human-readable firmware/protocol quad from PeerProtocol bytes."""
    if not protocol_bytes or len(protocol_bytes) < 4:
        return ''
    return '.'.join('{:02x}'.format(b) for b in protocol_bytes[:4])


def format_protocol_type(protocol_bytes):
    if not protocol_bytes or len(protocol_bytes) < 1:
        return 'IPSC'
    main_type = (protocol_bytes[0] >> 2) & 0x3F
    return _PROTOCOL_TYPE_LABELS.get(main_type, 'IPSC')


def describe_peer_mode(mode_byte):
    parts = decode_peer_mode(mode_byte)
    signaling = _SIGNALING_LABELS.get(parts['signaling'], 'unknown')
    ts1 = _SLOT_LABELS.get(parts['slot1'], '?')
    ts2 = _SLOT_LABELS.get(parts['slot2'], '?')
    return '{} TS1:{} TS2:{}'.format(signaling.capitalize(), ts1, ts2)


def describe_peer_capabilities(flags_bytes):
    """Short capability summary from PeerFlags (4 bytes)."""
    if not flags_bytes or len(flags_bytes) < 4:
        return ''
    caps = []
    if flags_bytes[3] & 0x08:
        caps.append('voice')
    if flags_bytes[3] & 0x04:
        caps.append('data')
    if flags_bytes[3] & 0x10:
        caps.append('auth')
    if flags_bytes[3] & 0x80:
        caps.append('XNL')
    if flags_bytes[2] & 0x80:
        caps.append('CSBK')
    if flags_bytes[2] & 0x40:
        caps.append('RPTMon')
    return ', '.join(caps) if caps else 'standard'


def _pad_field(text, length):
    if isinstance(text, bytes):
        raw = text
    else:
        raw = str(text).encode('utf-8', errors='ignore')
    return raw.ljust(length)[:length]


def ipsc_peer_display_fields(mode_byte, flags_bytes, protocol_bytes):
    """Map IPSC registration status into HBP-shaped PEERS display fields."""
    mode_desc = describe_peer_mode(mode_byte)
    proto_type = format_protocol_type(protocol_bytes)
    proto_ver = format_protocol_version(protocol_bytes)
    caps = describe_peer_capabilities(flags_bytes)

    software = 'Motorola {} {}'.format(proto_type, proto_ver).strip()
    if caps:
        software = '{} ({})'.format(software, caps)

    parts = decode_peer_mode(mode_byte)
    slot_count = sum(1 for k in ('slot1', 'slot2') if parts[k] != 0)
    slots = str(max(slot_count, 1))

    return {
        'DESCRIPTION': _pad_field(mode_desc, 19),
        'SOFTWARE_ID': _pad_field(software, 40),
        'PACKAGE_ID': _pad_field('Motorola IPSC Repeater', 40),
        'SLOTS': _pad_field(slots, 1),
        'IPSC_FLAGS': flags_bytes[:4] if flags_bytes else b'',
        'IPSC_PROTOCOL': protocol_bytes[:4] if protocol_bytes else b'',
    }


def lookup_peer_alias(full_config, peer_id):
    """Resolve repeater callsign/name from alias tables (peer_ids.json etc.)."""
    if not full_config:
        return None
    try:
        int_pid = int(int_id(peer_id))
    except (TypeError, ValueError):
        return None

    for key in ('_LOCAL_SUBSCRIBER_IDS', '_SUB_IDS', '_PEER_IDS'):
        table = full_config.get(key) or {}
        if int_pid in table:
            name = table[int_pid]
            if name is None:
                continue
            text = str(name).strip()
            if text and text != str(int_pid):
                return text
    return None


def callsign_bytes(name, peer_id):
    text = name if name else str(int_id(peer_id))
    return text.encode('utf-8', errors='ignore').ljust(8)[:8]
