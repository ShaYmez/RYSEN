#!/usr/bin/env python3
"""DMO HBP TX lock: idle-only static takeover, sysIgnore, no ON beside OFF."""
import unittest

from dmr_utils3.utils import bytes_3

from bridge_helpers import (
    hbp_tx_stream_locked,
    is_permanent_static_leg,
    obp_allows_static_stream_takeover,
    system_slot_has_off_leg,
)
from const import STREAM_TO


class TestHbpTxStreamLock(unittest.TestCase):

    def test_idle_tx_not_locked(self):
        st = {'TX_STREAM_ID': b'\x00', 'TX_TIME': 0.0}
        self.assertFalse(hbp_tx_stream_locked(st, b'\x00\x00\x01', 10.0, STREAM_TO))

    def test_same_stream_not_locked(self):
        sid = b'\x12\x34\x56\x78'
        st = {'TX_STREAM_ID': sid, 'TX_TIME': 9.9}
        self.assertFalse(hbp_tx_stream_locked(st, sid, 10.0, STREAM_TO))

    def test_different_stream_within_stream_to_locked(self):
        st = {'TX_STREAM_ID': b'\x11\x11\x11\x11', 'TX_TIME': 9.9}
        self.assertTrue(
            hbp_tx_stream_locked(st, b'\x22\x22\x22\x22', 10.0, STREAM_TO))

    def test_different_stream_after_stream_to_unlocked(self):
        st = {'TX_STREAM_ID': b'\x11\x11\x11\x11', 'TX_TIME': 1.0}
        self.assertFalse(
            hbp_tx_stream_locked(st, b'\x22\x22\x22\x22', 10.0, STREAM_TO))

    def test_static_takeover_idle_only(self):
        static = {'TO_TYPE': 'OFF', 'ACTIVE': True}
        live = {
            'TX_STREAM_ID': b'\x11\x11\x11\x11',
            'TX_TIME': 9.9,
        }
        self.assertTrue(obp_allows_static_stream_takeover(static))
        self.assertTrue(obp_allows_static_stream_takeover(
            static, {'TX_STREAM_ID': b'\x00', 'TX_TIME': 0}, b'\x01\x00\x00\x00',
            10.0, STREAM_TO))
        self.assertFalse(obp_allows_static_stream_takeover(
            static, live, b'\x22\x22\x22\x22', 10.0, STREAM_TO))
        self.assertFalse(obp_allows_static_stream_takeover(
            {'TO_TYPE': 'ON', 'ACTIVE': True}))


class TestHbpSysIgnoreAndOffOnly(unittest.TestCase):

    def test_obp_and_hbp_append_sysignore_after_hbp_send(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertGreaterEqual(
            source.count(
                "_sysIgnore.append((_target['SYSTEM'], _target['TS']))"),
            4)  # OBP targets + both HBP sends
        self.assertIn('Dedupe HBP/MASTER legs on this packet', source)
        self.assertIn('hbp_tx_stream_locked', source)
        self.assertIn('TX stream locked', source)

    def test_system_slot_has_off_leg(self):
        bridges = {
            '23426': [
                {'SYSTEM': 'SYSTEM-1', 'TS': 2, 'TO_TYPE': 'OFF', 'ACTIVE': True},
                {'SYSTEM': 'SYSTEM-1', 'TS': 1, 'TO_TYPE': 'ON', 'ACTIVE': False},
            ]
        }
        self.assertTrue(system_slot_has_off_leg(bridges, '23426', 'SYSTEM-1', 2))
        self.assertFalse(system_slot_has_off_leg(bridges, '23426', 'SYSTEM-1', 1))

    def test_ensure_master_skips_on_beside_off(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('_off_slots', source)
        self.assertIn('if _ts in _off_slots:', source)
        self.assertIn("if _has_off and _entry.get('TO_TYPE') == 'ON':", source)
        self.assertIn('system_slot_has_off_leg', source)


if __name__ == '__main__':
    unittest.main()
