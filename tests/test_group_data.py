#!/usr/bin/env python3
"""Group SMS/data stays off the voice path and fans out on the TG bridge."""
import unittest
from unittest.mock import patch

import bridge_master as bm
from const import HBPF_DATA_SYNC, HBPF_SLT_VHEAD
from dmr_utils3.utils import bytes_3

from tests.test_unit_calls import (
    CALLER,
    PEER,
    UnitCallFixture,
    _Target,
)


STREAM = b'\x00\x00\x00\x31'
TG = 235
TG_BYTES = bytes_3(TG)


def _group_data_packet(src, dst, stream, slot, seq, dtype):
    bits = (HBPF_DATA_SYNC << 4) | (dtype & 0x0F)
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


class GroupDataFixture(UnitCallFixture):
    def setUp(self):
        super().setUp()
        for name in ('subscriber_ids', 'talkgroup_ids', 'peer_ids'):
            if not hasattr(bm, name):
                setattr(bm, name, {})

    def _bridge(self, name, ts=1, tgid=TG):
        return {
            'SYSTEM': name,
            'TS': ts,
            'TGID': bytes_3(tgid),
            'ACTIVE': True,
            'TO_TYPE': 'NONE',
        }

    def _receive(self, router, seq, dtype, tgid=TG, stream=STREAM, slot=1):
        packet = _group_data_packet(CALLER, tgid, stream, slot, seq, dtype)
        router.dmrd_received(
            PEER, bytes_3(CALLER), bytes_3(tgid), seq, slot, 'group',
            HBPF_DATA_SYNC, dtype, stream, packet)
        return packet


class TestGroupDataRouting(GroupDataFixture):
    def test_csbk_header_and_blocks_fan_out_without_opening_voice(self):
        self._system('HYTERA-0', 'HYTERA')
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(234587501))
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        bm.BRIDGES[str(TG)] = [
            self._bridge('HYTERA-0'),
            self._bridge('SYSTEM-B', ts=2),
        ]
        router = self._router('HYTERA-0')

        for seq, dtype in ((0, 3), (1, 3), (2, 3), (3, 3), (4, 6), (5, 7), (6, 7)):
            self._receive(router, seq, dtype)

        self.assertEqual(len(target.sent), 7)
        self.assertTrue(all(extra == (None,) for extra in target.extra))
        self.assertEqual(router.STATUS[1]['RX_STREAM_ID'], b'\x00\x00\x00\x00')
        self.assertEqual(target.sent[0][8:11], TG_BYTES)
        self.assertTrue(target.sent[0][15] & 0x80)
        self.assertFalse(target.sent[0][15] & 0x40)
        self.assertEqual(target.sent[4][15] & 0x0F, 6)
        self.assertEqual(target.sent[5][15] & 0x0F, 7)

    def test_unknown_tg_does_not_start_voice_or_invent_a_bridge(self):
        self._system('HYTERA-0', 'HYTERA')
        self._system('SYSTEM-B', 'MASTER')
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        router = self._router('HYTERA-0')
        self._receive(router, 0, 3, tgid=2)
        self.assertEqual(target.sent, [])
        self.assertEqual(bm.BRIDGES, {})
        self.assertEqual(router.STATUS[1]['RX_STREAM_ID'], b'\x00\x00\x00\x00')

    def test_busy_target_is_skipped(self):
        self._system('HYTERA-0', 'HYTERA')
        self._system('SYSTEM-B', 'MASTER')
        target = _Target()
        target.STATUS[1]['TX_TYPE'] = HBPF_SLT_VHEAD
        target.STATUS[1]['TX_TIME'] = 1_000_000.0
        bm.systems['SYSTEM-B'] = target
        bm.BRIDGES[str(TG)] = [
            self._bridge('HYTERA-0'),
            self._bridge('SYSTEM-B'),
        ]
        router = self._router('HYTERA-0')
        self._receive(router, 0, 3)
        self.assertEqual(target.sent, [])

    def test_openbridge_ver2_gets_group_data(self):
        self._system('HYTERA-0', 'HYTERA')
        bm.CONFIG['SYSTEMS']['OBP-EU'] = {
            'MODE': 'OPENBRIDGE',
            'ENABLED': True,
            'VER': 2,
            'ENHANCED_OBP': False,
            'GROUP_HANGTIME': 5,
        }
        target = _Target()
        target.STATUS = {}
        bm.systems['OBP-EU'] = target
        bm.CONFIG['GLOBAL'] = {'SERVER_ID': b'\x00\x00\x00\x01'}
        bm.BRIDGES[str(TG)] = [
            self._bridge('HYTERA-0'),
            self._bridge('OBP-EU'),
        ]
        router = self._router('HYTERA-0')
        router._CONFIG = bm.CONFIG
        self._receive(router, 0, 6)
        self.assertEqual(len(target.sent), 1)
        self.assertEqual(target.sent[0][8:11], TG_BYTES)
        self.assertGreater(len(target.extra[0]), 1)

    def test_group_voice_header_still_opens_a_call(self):
        self._system('HYTERA-0', 'HYTERA')
        self._system('SYSTEM-B', 'MASTER')
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        bm.BRIDGES[str(TG)] = [
            self._bridge('HYTERA-0'),
            self._bridge('SYSTEM-B'),
        ]
        bm.CONFIG['GLOBAL'] = {'SERVER_ID': b'\x00\x00\x00\x01'}
        router = self._router('HYTERA-0')
        router._CONFIG = bm.CONFIG
        packet = _group_data_packet(CALLER, TG, STREAM, 1, 0, HBPF_SLT_VHEAD)
        with patch.object(bm.decode, 'voice_head_term', return_value={'LC': b'\x00' * 9}):
            router.dmrd_received(
                PEER, bytes_3(CALLER), TG_BYTES, 0, 1, 'group',
                HBPF_DATA_SYNC, HBPF_SLT_VHEAD, STREAM, packet)
        self.assertEqual(router.STATUS[1]['RX_STREAM_ID'], STREAM)
        self.assertTrue(target.sent)
        self.assertNotEqual(target.extra[0], (None,))


if __name__ == '__main__':
    unittest.main()
