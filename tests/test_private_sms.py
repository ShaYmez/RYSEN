#!/usr/bin/env python3
"""Private SMS stays on the UNIT DATA path and reaches the mapped radio."""
import unittest
from unittest.mock import MagicMock

import bridge_master as bm
from const import HBPF_DATA_SYNC, HBPF_SLT_VHEAD
from dmr_utils3.utils import bytes_3
from hytera_voice import HyteraVoiceTranslator

from tests.test_hytera_inbound import wire_fixture
from tests.test_unit_calls import (
    PEER,
    UnitCallFixture,
    _Target,
)


HOTSPOT_RADIO = 2348831
HOTSPOT_PEER = 234883150
REPEATER_RADIO = 2345875
SMS_STREAM = b'\x00\x00\x00\x41'


def _unit_data_packet(src, dst, stream, slot, seq, dtype):
    bits = 0x40 | (HBPF_DATA_SYNC << 4) | (dtype & 0x0F)
    if slot == 2:
        bits |= 0x80
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[4] = seq
    packet[5:8] = bytes_3(src)
    packet[8:11] = bytes_3(dst)
    packet[11:15] = PEER
    packet[15] = bits
    packet[16:20] = stream
    packet[20:53] = bytes([seq]) * 33
    return bytes(packet)


class PrivateSmsFixture(UnitCallFixture):
    def setUp(self):
        super().setUp()
        bm.CONFIG['GLOBAL'] = {
            'DATA_GATEWAY': False,
            'SERVER_ID': b'\x00\x00\x00\x01',
        }
        for name in (
                'subscriber_ids', 'talkgroup_ids', 'peer_ids',
                'local_subscriber_ids'):
            if not hasattr(bm, name):
                setattr(bm, name, {})

    def _receive(self, router, seq, dtype, src=REPEATER_RADIO,
                 dst=HOTSPOT_RADIO, stream=SMS_STREAM, slot=2):
        packet = _unit_data_packet(src, dst, stream, slot, seq, dtype)
        router.dmrd_received(
            PEER, bytes_3(src), bytes_3(dst), seq, slot, 'unit',
            HBPF_DATA_SYNC, dtype, stream, packet)
        return packet


class TestPrivateSmsRouting(PrivateSmsFixture):
    def test_sub_map_delivers_csbk_header_and_blocks(self):
        self._system('HYTERA-0', 'HYTERA')
        self._system('SYSTEM-17', 'MASTER', peers=self._peer(HOTSPOT_PEER))
        target = _Target()
        bm.systems['SYSTEM-17'] = target
        bm.SUB_MAP[bytes_3(HOTSPOT_RADIO)] = (
            'SYSTEM-17', 2, None, 50.0, HOTSPOT_PEER.to_bytes(4, 'big'))
        router = self._router('HYTERA-0')

        for seq, dtype in ((0, 3), (1, 3), (2, 3), (3, 3), (4, 6), (5, 7), (6, 7)):
            self._receive(router, seq, dtype)

        self.assertEqual(len(target.sent), 7)
        self.assertTrue(all(extra == (None,) for extra in target.extra))
        self.assertEqual(target.sent[0][5:8], bytes_3(REPEATER_RADIO))
        self.assertEqual(target.sent[0][8:11], bytes_3(HOTSPOT_RADIO))
        self.assertTrue(target.sent[0][15] & 0x40)
        self.assertTrue(target.sent[0][15] & 0x80)
        self.assertEqual([pkt[15] & 0x0F for pkt in target.sent],
                         [3, 3, 3, 3, 6, 7, 7])
        self.assertEqual(router.STATUS[2]['RX_STREAM_ID'], b'\x00\x00\x00\x00')

    def test_hotspot_prefix_match_when_radio_not_in_sub_map(self):
        self._system('HYTERA-0', 'HYTERA')
        self._system('SYSTEM-17', 'MASTER', peers=self._peer(HOTSPOT_PEER))
        target = _Target()
        bm.systems['SYSTEM-17'] = target
        router = self._router('HYTERA-0')
        self._receive(router, 0, 3)
        self.assertEqual(len(target.sent), 1)
        self.assertEqual(target.sent[0][8:11], bytes_3(HOTSPOT_RADIO))
        self.assertTrue(target.sent[0][15] & 0x80)

    def test_busy_target_is_skipped(self):
        self._system('HYTERA-0', 'HYTERA')
        self._system('SYSTEM-17', 'MASTER', peers=self._peer(HOTSPOT_PEER))
        target = _Target()
        target.STATUS[2]['TX_TYPE'] = HBPF_SLT_VHEAD
        target.STATUS[2]['TX_TIME'] = 1_000_000.0
        bm.systems['SYSTEM-17'] = target
        bm.SUB_MAP[bytes_3(HOTSPOT_RADIO)] = (
            'SYSTEM-17', 2, None, 50.0, HOTSPOT_PEER.to_bytes(4, 'big'))
        router = self._router('HYTERA-0')
        self._receive(router, 0, 3)
        self.assertEqual(target.sent, [])

    def test_pd685g_34_rate_reaches_hotspot(self):
        self._system('HYTERA-0', 'HYTERA')
        self._system('SYSTEM-17', 'MASTER', peers=self._peer(HOTSPOT_PEER))
        target = _Target()
        bm.systems['SYSTEM-17'] = target
        bm.SUB_MAP[bytes_3(HOTSPOT_RADIO)] = (
            'SYSTEM-17', 2, None, 50.0, HOTSPOT_PEER.to_bytes(4, 'big'))
        router = self._router('HYTERA-0')
        self._receive(router, 0, 6)
        self._receive(router, 1, 8)
        self.assertEqual(len(target.sent), 2)
        self.assertEqual(target.sent[1][15] & 0x0F, 8)
        self.assertEqual(router.STATUS[2]['RX_STREAM_ID'], b'\x00\x00\x00\x00')

    def test_captured_hello_routes_to_mapped_radio(self):
        self._system('HYTERA-0', 'HYTERA')
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(234587501))
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        bm.SUB_MAP[bytes_3(REPEATER_RADIO)] = (
            'SYSTEM-B', 2, None, 50.0, (234587501).to_bytes(4, 'big'))
        router = self._router('HYTERA-0')
        translator = HyteraVoiceTranslator(
            peer_id=235287, stream_factory=lambda: SMS_STREAM)
        packets = [
            translator.translate_voice(packet)
            for packet in wire_fixture('sms_private_hello_ts1.hex')
        ]
        self.assertTrue(all(item is not None for item in packets))
        for seq, dmrd in enumerate(packets):
            bits = dmrd[15]
            router.dmrd_received(
                dmrd[11:15], dmrd[5:8], dmrd[8:11], dmrd[4],
                2 if bits & 0x80 else 1,
                'unit' if bits & 0x40 else 'group',
                (bits & 0x30) >> 4, bits & 0x0F, dmrd[16:20], dmrd)
        self.assertEqual(len(target.sent), 10)
        self.assertEqual(target.sent[0][5:8], bytes_3(HOTSPOT_RADIO))
        self.assertEqual(target.sent[0][8:11], bytes_3(REPEATER_RADIO))
        self.assertEqual([pkt[15] & 0x0F for pkt in target.sent],
                         [3, 3, 3, 3, 6, 7, 7, 7, 7, 7])
        self.assertEqual(router.STATUS[1]['RX_STREAM_ID'], b'\x00\x00\x00\x00')

    def test_confirmed_read_is_a_new_unit_data_stream(self):
        self._system('HYTERA-0', 'HYTERA')
        self._system('SYSTEM-17', 'MASTER', peers=self._peer(HOTSPOT_PEER))
        target = _Target()
        bm.systems['SYSTEM-17'] = target
        bm.SUB_MAP[bytes_3(HOTSPOT_RADIO)] = (
            'SYSTEM-17', 2, None, 50.0, HOTSPOT_PEER.to_bytes(4, 'big'))
        bm.SUB_MAP[bytes_3(REPEATER_RADIO)] = (
            'HYTERA-0', 2, None, 50.0, PEER)
        hytera = _Target()
        bm.systems['HYTERA-0'] = hytera
        hotspot = self._router('SYSTEM-17')
        self._receive(self._router('HYTERA-0'), 0, 3)
        self._receive(
            hotspot, 1, 3, src=HOTSPOT_RADIO, dst=REPEATER_RADIO,
            stream=b'\x00\x00\x00\x42')
        self.assertEqual(len(target.sent), 1)
        self.assertEqual(len(hytera.sent), 1)
        self.assertEqual(hytera.sent[0][5:8], bytes_3(HOTSPOT_RADIO))
        self.assertEqual(hytera.sent[0][8:11], bytes_3(REPEATER_RADIO))

    def test_openbridge_private_sms_reaches_hytera(self):
        self._system('HYTERA-0', 'HYTERA')
        target = _Target()
        bm.systems['HYTERA-0'] = target
        bm.CONFIG['SYSTEMS']['OBP-UK'] = {
            'MODE': 'OPENBRIDGE',
            'ENABLED': True,
            'VER': 2,
            'ENHANCED_OBP': False,
            'GROUP_HANGTIME': 5,
            'ANNOUNCEMENT_LANGUAGE': 'en_US',
        }
        bm.CONFIG['GLOBAL']['SERVER_ID'] = (2040).to_bytes(4, 'big')
        bm.SUB_MAP[bytes_3(REPEATER_RADIO)] = (
            'HYTERA-0', 2, None, 50.0, PEER)
        router = bm.routerOBP.__new__(bm.routerOBP)
        router._system = 'OBP-UK'
        router._CONFIG = bm.CONFIG
        router._report = MagicMock()
        router.STATUS = {}
        packet = _unit_data_packet(
            HOTSPOT_RADIO, REPEATER_RADIO, SMS_STREAM, 2, 0, 3)
        router.dmrd_received(
            PEER, bytes_3(HOTSPOT_RADIO), bytes_3(REPEATER_RADIO),
            0, 2, 'unit', HBPF_DATA_SYNC, 3, SMS_STREAM, packet,
            b'', b'', (2342).to_bytes(4, 'big'))
        self.assertEqual(len(target.sent), 1)
        self.assertEqual(target.sent[0][8:11], bytes_3(REPEATER_RADIO))
        self.assertEqual(router.STATUS[SMS_STREAM]['packets'], 1)


if __name__ == '__main__':
    unittest.main()
