#!/usr/bin/env python3
"""Describe Hytera 72-byte UDP media from PCAP or PCAPNG captures.

This deliberately has no RYSEN runtime dependency. It is intended for offline
capture comparison and emits one deterministic JSON object per media packet.
"""

import argparse
from datetime import datetime, timezone
import ipaddress
import json
import struct
import sys


MEDIA_LEN = 72
LINK_ETHERNET = 1
LINK_LINUX_SLL = 113
LINK_LINUX_SLL2 = 276

SLOT_NAMES = {
    0x1111: 'voice_lc_header',
    0x2222: 'voice_lc_terminator',
    0x7777: 'voice_c',
    0x8888: 'voice_d',
    0x9999: 'voice_e',
    0xAAAA: 'voice_f',
    0xBBBB: 'voice_a',
    0xCCCC: 'voice_b',
    0xDDDD: 'wakeup',
    0xEEEE: 'hytera_sync',
}


def _read_ipv4_udp(frame, link_type):
    """Return source, destination, UDP payload or None."""
    if link_type == LINK_ETHERNET:
        if len(frame) < 14:
            return None
        offset = 14
        ether_type = struct.unpack('!H', frame[12:14])[0]
        if ether_type == 0x8100 and len(frame) >= 18:
            ether_type = struct.unpack('!H', frame[16:18])[0]
            offset = 18
        if ether_type != 0x0800:
            return None
    elif link_type == LINK_LINUX_SLL:
        if len(frame) < 16 or struct.unpack('!H', frame[14:16])[0] != 0x0800:
            return None
        offset = 16
    elif link_type == LINK_LINUX_SLL2:
        if len(frame) < 20 or struct.unpack('!H', frame[:2])[0] != 0x0800:
            return None
        offset = 20
    else:
        return None

    if len(frame) < offset + 20:
        return None
    version_ihl = frame[offset]
    if version_ihl >> 4 != 4:
        return None
    ip_len = (version_ihl & 0x0F) * 4
    if ip_len < 20 or len(frame) < offset + ip_len + 8:
        return None
    if frame[offset + 9] != 17:
        return None
    source = str(ipaddress.IPv4Address(frame[offset + 12:offset + 16]))
    destination = str(ipaddress.IPv4Address(frame[offset + 16:offset + 20]))
    udp_offset = offset + ip_len
    source_port, destination_port, udp_len = struct.unpack(
        '!HHH', frame[udp_offset:udp_offset + 6])
    if udp_len < 8 or len(frame) < udp_offset + udp_len:
        return None
    return source, source_port, destination, destination_port, (
        frame[udp_offset + 8:udp_offset + udp_len])


def _pcap_records(handle):
    header = handle.read(24)
    magic = header[:4]
    formats = {
        b'\xd4\xc3\xb2\xa1': ('<', 1000000.0),
        b'\xa1\xb2\xc3\xd4': ('>', 1000000.0),
        b'\x4d\x3c\xb2\xa1': ('<', 1000000000.0),
        b'\xa1\xb2\x3c\x4d': ('>', 1000000000.0),
    }
    capture_format = formats.get(magic)
    if capture_format is None or len(header) != 24:
        raise ValueError('not a supported PCAP capture')
    endian, timestamp_divisor = capture_format
    link_type = struct.unpack(endian + 'I', header[20:24])[0]
    while True:
        record = handle.read(16)
        if not record:
            return
        if len(record) != 16:
            raise ValueError('truncated PCAP record')
        seconds, fraction, captured, _ = struct.unpack(endian + 'IIII', record)
        data = handle.read(captured)
        if len(data) != captured:
            raise ValueError('truncated PCAP frame')
        yield seconds + fraction / timestamp_divisor, link_type, data


def _pcapng_records(handle):
    interface_links = {}
    endian = '<'
    while True:
        block_header = handle.read(8)
        if not block_header:
            return
        if len(block_header) != 8:
            raise ValueError('truncated PCAPNG block')
        block_type, length = struct.unpack(endian + 'II', block_header)
        body = handle.read(length - 12)
        trailer = handle.read(4)
        if len(body) != length - 12 or len(trailer) != 4:
            raise ValueError('truncated PCAPNG block')
        if block_type == 0x0A0D0D0A:
            if body[:4] == b'\x4d\x3c\x2b\x1a':
                endian = '<'
            elif body[:4] == b'\x1a\x2b\x3c\x4d':
                endian = '>'
            else:
                raise ValueError('unknown PCAPNG byte order')
            interface_links = {}
        elif block_type == 1 and len(body) >= 8:
            interface_links[len(interface_links)] = struct.unpack(
                endian + 'H', body[:2])[0]
        elif block_type == 6 and len(body) >= 20:
            interface_id, high, low, captured, _ = struct.unpack(
                endian + 'IIIII', body[:20])
            frame = body[20:20 + captured]
            timestamp = ((high << 32) | low) / 1000000.0
            yield timestamp, interface_links.get(interface_id), frame


def _records(path):
    with open(path, 'rb') as handle:
        magic = handle.read(4)
        handle.seek(0)
        if magic == b'\x0a\x0d\x0d\x0a':
            yield from _pcapng_records(handle)
        else:
            yield from _pcap_records(handle)


def _redact(address):
    parts = address.split('.')
    return '.'.join(parts[:2] + ['x', 'x'])


def _describe(timestamp, previous, udp, redact):
    source, source_port, destination, destination_port, packet = udp
    if len(packet) != MEDIA_LEN:
        return None
    slot = int.from_bytes(packet[18:20], 'big')
    entry = {
        'timestamp': datetime.fromtimestamp(
            timestamp, timezone.utc).isoformat(timespec='microseconds'),
        'delta_ms': None if previous is None else round(
            (timestamp - previous) * 1000, 3),
        'source': _redact(source) if redact else source,
        'source_port': source_port,
        'destination': _redact(destination) if redact else destination,
        'destination_port': destination_port,
        'sequence': int.from_bytes(packet[4:8], 'little'),
        'packet_type': f'0x{packet[8]:02x}',
        'timeslot': 2 if packet[16:18] == b'\x22\x22' else 1,
        'slot_type': SLOT_NAMES.get(slot, f'0x{slot:04x}'),
        'call_type': 'private' if packet[62] == 0 else 'group',
        'destination_id': int.from_bytes(packet[63:67], 'little') >> 8,
        'source_id': int.from_bytes(packet[67:71], 'little') >> 8,
        'prefix': packet[:4].hex(),
        'packet_hex': packet.hex(),
    }
    return entry


def _parse_timestamp(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', help='PCAP or PCAPNG input')
    parser.add_argument('--port', type=int, default=50001,
                        help='UDP port to inspect (default: 50001)')
    parser.add_argument('--redact-addresses', action='store_true',
                        help='replace the final two IPv4 octets in output')
    parser.add_argument('--start',
                        help='include packets at/after this ISO-8601 timestamp')
    parser.add_argument('--end',
                        help='include packets before this ISO-8601 timestamp')
    args = parser.parse_args()

    start = _parse_timestamp(args.start)
    end = _parse_timestamp(args.end)
    previous = None
    for timestamp, link_type, frame in _records(args.capture):
        if start is not None and timestamp < start:
            continue
        if end is not None and timestamp >= end:
            continue
        udp = _read_ipv4_udp(frame, link_type)
        if udp is None or args.port not in udp[1:4:2]:
            continue
        entry = _describe(timestamp, previous, udp, args.redact_addresses)
        if entry is not None:
            print(json.dumps(entry, sort_keys=True))
            previous = timestamp


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(2)
