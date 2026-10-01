#!/usr/bin/env python3
"""RSSI stays on the paths that already carry Homebrew byte 54."""
import unittest
from types import SimpleNamespace

from bridge_helpers import (
    changed_stream_rssi,
    group_voice_event,
    rssi_byte,
    seed_stream_rssi,
)
from const import DMRD, DMRE, DMRF
from hblink import HBSYSTEM, OPENBRIDGE


def dmrd_packet():
    """53-byte group voice header on slot 1, talkgroup 235."""
    return b''.join([
        DMRD,
        b'\x00',
        (2344512).to_bytes(3, 'big'),
        (235).to_bytes(3, 'big'),
        (235240).to_bytes(4, 'big'),
        b'\x21',
        b'\x01\x02\x03\x04',
        b'\x00' * 33,
    ])


class CaptureTransport:
    def __init__(self):
        self.packets = []

    def write(self, packet, addr):
        self.packets.append((packet, addr))


class RecordingOpenBridge(OPENBRIDGE):
    def __init__(self, name, config, report):
        super().__init__(name, config, report)
        self.heard = []

    def dmrd_received(self, *args, **kwargs):
        self.heard.append(args[14] if len(args) > 14 else kwargs.get('_rssi'))


def openbridge(version):
    server = (2365).to_bytes(4, 'big')
    config = {
        'SYSTEMS': {
            'OBP': {
                'PASSPHRASE': b'passphrase',
                'TARGET_IP': '127.0.0.1',
                'TARGET_PORT': 62031,
                'TARGET_SOCK': ('127.0.0.1', 62031),
                'VER': version,
                'ENHANCED_OBP': version >= 4,
                'NETWORK_ID': server,
                'RELAX_CHECKS': True,
                'USE_ACL': False,
            },
        },
        'GLOBAL': {
            'SERVER_ID': server,
            'USE_ACL': False,
            'VALIDATE_SERVER_IDS': False,
        },
    }
    bridge = RecordingOpenBridge('OBP', config, None)
    bridge.transport = CaptureTransport()
    return bridge


class TestRssiHelpers(unittest.TestCase):

    def test_event_appends_raw_byte(self):
        event = group_voice_event(
            'START', 'RX', 'HYTERA', 7, 235240, 2344512, 1, 235, b'\x57')
        self.assertEqual(
            event,
            b'GROUP VOICE,START,RX,HYTERA,7,235240,2344512,1,235,87')
        self.assertEqual(rssi_byte(b''), 0)
        self.assertEqual(rssi_byte(None), 0)

    def test_in_call_update_is_throttled_until_the_value_changes(self):
        state = {}
        seed_stream_rssi(state, b'\x50', 10.0)
        self.assertIsNone(changed_stream_rssi(state, b'\x50', 10.2))
        self.assertIsNone(changed_stream_rssi(state, b'\x57', 10.4))
        self.assertEqual(changed_stream_rssi(state, b'\x57', 11.0), 87)
        self.assertIsNone(changed_stream_rssi(state, b'\x57', 11.2))
        self.assertEqual(changed_stream_rssi(state, b'\x00', 12.0), 0)


class TestOpenBridgeRssi(unittest.TestCase):

    def test_v5_round_trip_keeps_rssi(self):
        bridge = openbridge(5)
        bridge.send_system(
            dmrd_packet(), _rssi=b'\x5a', _ber=b'\x01',
            _source_server=(2365).to_bytes(4, 'big'))
        packet = bridge.transport.packets[0][0]
        self.assertEqual(packet[:4], DMRE)
        self.assertEqual(len(packet), 89)
        self.assertEqual(packet[53], 1)
        self.assertEqual(packet[54], 0x5a)

        bridge.datagramReceived(packet, ('127.0.0.1', 62031))
        self.assertEqual(bridge.heard, [b'\x5a'])

    def test_v4_send_keeps_rssi(self):
        bridge = openbridge(4)
        bridge.send_system(
            dmrd_packet(), _rssi=b'\x5a',
            _source_server=(2365).to_bytes(4, 'big'))
        packet = bridge.transport.packets[0][0]
        self.assertEqual(packet[:4], DMRE)
        self.assertEqual(len(packet), 85)
        self.assertEqual(packet[54], 0x5a)

    def test_classic_v3_has_no_rssi_field(self):
        bridge = openbridge(3)
        bridge.send_system(
            dmrd_packet(), _rssi=b'\x5a',
            _source_server=(2365).to_bytes(4, 'big'))
        packet = bridge.transport.packets[0][0]
        self.assertEqual(packet[:4], DMRF)
        self.assertEqual(len(packet), 78)
        self.assertEqual(packet[20:53], b'\x00' * 33)


class TestHomebrewPeerRssi(unittest.TestCase):

    def test_master_to_peer_forwards_existing_byte(self):
        peer = (235287).to_bytes(4, 'big')
        transport = CaptureTransport()
        master = SimpleNamespace(
            _system='MASTER',
            _peers={peer: {'SOCKADDR': ('10.0.0.8', 50001)}},
            transport=transport,
        )
        master.send_peer = lambda peer_id, packet: HBSYSTEM.send_peer(
            master, peer_id, packet)
        packet = dmrd_packet() + b'\x00\x5a'
        HBSYSTEM.send_peers(master, packet)
        sent = transport.packets[0][0]
        self.assertEqual(len(sent), 55)
        self.assertEqual(sent[54], 0x5a)
        self.assertEqual(sent[11:15], peer)

    def test_master_to_peer_appends_short_packet_rssi(self):
        peer = (235287).to_bytes(4, 'big')
        transport = CaptureTransport()
        master = SimpleNamespace(
            _system='MASTER',
            _peers={peer: {'SOCKADDR': ('10.0.0.8', 50001)}},
            transport=transport,
        )
        master.send_peer = lambda peer_id, packet: HBSYSTEM.send_peer(
            master, peer_id, packet)
        HBSYSTEM.send_peers(master, dmrd_packet(), _rssi=b'\x5a', _ber=b'\x02')
        sent = transport.packets[0][0]
        self.assertEqual(sent[53], 2)
        self.assertEqual(sent[54], 0x5a)


if __name__ == '__main__':
    unittest.main()
