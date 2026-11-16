#!/usr/bin/env python3
"""Tests for multi-static TG bridge legs (make_static_tg)."""
import unittest
from types import SimpleNamespace

import bridge_master as bm
from dmr_utils3.utils import bytes_3


class TestMakeStaticTg(unittest.TestCase):

    def test_make_static_tg_appends_missing_leg(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if not matched:', source)
        self.assertIn('Added static TG', source)

    def test_obp_activates_bridge_before_routing(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn(
            'activate_ua_bridge_source(str(_int_dst), self._system, _slot, peer_id=_peer_id)',
            source)
        self.assertIn("CONFIG['SYSTEMS'][system].get('DEFAULT_UA_TIMER')", source)

    def test_options_strips_whitespace_in_static_tg(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('_options[\'TS1_STATIC\'] = re.sub(r"\\s", "", str(_options[\'TS1_STATIC\']))',
                      source)

    def test_vterm_skips_always_on_static_legs(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn(
            'if static_tg_survives_single_mode_vterm(_bridge, _system):',
            source)
        static = {
            'TO_TYPE': 'OFF', 'ACTIVE': True, 'TGID': bytes_3(2350),
        }
        self.assertTrue(bm.static_tg_survives_single_mode_vterm('2350', static))
        self.assertFalse(
            bm.static_tg_survives_single_mode_vterm('#2350', static))
        ua = {'TO_TYPE': 'ON', 'ACTIVE': True, 'TGID': bytes_3(91)}
        self.assertFalse(bm.static_tg_survives_single_mode_vterm('91', ua))
        self.assertIn(
            'if sticky_ua_survives_single_mode_vterm(', source)

    def test_timer_change_updates_live_bridge_timeout(self):
        prev = getattr(bm, 'BRIDGES', None)
        bm.BRIDGES = {
            '91': [{
                'SYSTEM': 'SYSTEM-1', 'TS': 2, 'TGID': bytes_3(91),
                'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMEOUT': 600,
            }],
            '2350': [{
                'SYSTEM': 'SYSTEM-1', 'TS': 2, 'TGID': bytes_3(2350),
                'ACTIVE': True, 'TO_TYPE': 'OFF', 'TIMEOUT': 600,
            }],
            '9990': [{
                'SYSTEM': 'SYSTEM-1', 'TS': 1, 'TGID': bytes_3(9990),
                'ACTIVE': True, 'TO_TYPE': 'NONE', 'TIMEOUT': 15,
            }],
            '91-other': [{
                'SYSTEM': 'SYSTEM-2', 'TS': 2, 'TGID': bytes_3(91),
                'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMEOUT': 600,
            }],
        }
        try:
            self.assertEqual(bm.update_system_bridge_timeouts('SYSTEM-1', 5), 2)
            self.assertEqual(bm.BRIDGES['91'][0]['TIMEOUT'], 300)
            self.assertEqual(bm.BRIDGES['2350'][0]['TIMEOUT'], 300)
            self.assertEqual(bm.BRIDGES['9990'][0]['TIMEOUT'], 15)
            self.assertEqual(bm.BRIDGES['91-other'][0]['TIMEOUT'], 600)
        finally:
            bm.BRIDGES = {} if prev is None else prev


def _leg(bridge, system, slot):
    for entry in bm.BRIDGES.get(bridge, ()):
        if entry.get('SYSTEM') == system and entry.get('TS') == slot:
            return entry
    return None


class TestImmediateStaticSubscribe(unittest.TestCase):
    def setUp(self):
        self.peer = (234587550).to_bytes(4, 'big')
        self.other = (235211730).to_bytes(4, 'big')
        self.prev_config = getattr(bm, 'CONFIG', None)
        self.prev_bridges = getattr(bm, 'BRIDGES', None)
        self.prev_systems = getattr(bm, 'systems', None)
        self.had_systems = hasattr(bm, 'systems')
        self.prev_sub_map = getattr(bm, 'SUB_MAP', None)
        self.had_sub_map = hasattr(bm, 'SUB_MAP')
        bm.BRIDGES = {}
        bm.BRIDGE_IDX.clear()
        bm.SUB_MAP = {}
        self.slot = {
            'RX_TIME': 12345,
            'TX_TIME': 12345,
            'RX_TGID': bytes_3(23516),
            'TX_TGID': bytes_3(23516),
        }
        bm.systems = {'SYSTEM-95': SimpleNamespace(STATUS={2: self.slot})}
        bm.CONFIG = {
            'SYSTEMS': {
                'SYSTEM-95': {
                    'MODE': 'MASTER',
                    'STICKY_TG': False,
                    'DEFAULT_UA_TIMER': 10,
                    'TS1_STATIC': False,
                    'TS2_STATIC': False,
                    'PEERS': {
                        self.peer: {
                            'CONNECTION': 'YES',
                            'OPTIONS': '',
                        },
                    },
                },
            },
            'REPORTS': {'REPORT': False},
        }

    def tearDown(self):
        bm.CONFIG = self.prev_config
        if self.prev_bridges is None:
            bm.BRIDGES = {}
        else:
            bm.BRIDGES = self.prev_bridges
        bm.BRIDGE_IDX.clear()
        if self.had_systems:
            bm.systems = self.prev_systems
        else:
            del bm.systems
        if self.had_sub_map:
            bm.SUB_MAP = self.prev_sub_map
        elif hasattr(bm, 'SUB_MAP'):
            del bm.SUB_MAP

    def test_ts2_replacement_is_active_before_options_timer(self):
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=2350;')
        first = _leg('2350', 'SYSTEM-95', 2)
        self.assertIsNotNone(first)
        self.assertTrue(first['ACTIVE'])
        self.assertEqual(first['TO_TYPE'], 'OFF')
        self.assertEqual(self.slot['RX_TIME'], 0)
        self.assertEqual(self.slot['TX_TIME'], 0)

        self.slot['RX_TIME'] = 50
        self.slot['TX_TIME'] = 50
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=31320;')
        moved = _leg('31320', 'SYSTEM-95', 2)
        old = _leg('2350', 'SYSTEM-95', 2)
        self.assertTrue(moved['ACTIVE'])
        self.assertEqual(moved['TO_TYPE'], 'OFF')
        self.assertFalse(old['ACTIVE'])
        self.assertEqual(old['TO_TYPE'], 'ON')
        self.assertEqual(self.slot['RX_TIME'], 0)

    def test_single_peer_timer_is_used_immediately(self):
        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=2350;TIMER=60;')
        leg = _leg('2350', 'SYSTEM-95', 2)
        self.assertEqual(leg['TIMEOUT'], 3600)
        self.assertEqual(
            bm.CONFIG['SYSTEMS']['SYSTEM-95']['DEFAULT_UA_TIMER'], 60)

    def test_shared_master_keeps_another_peers_static(self):
        peers = bm.CONFIG['SYSTEMS']['SYSTEM-95']['PEERS']
        peers[self.other] = {'CONNECTION': 'YES', 'OPTIONS': 'TS2_1=2350;'}
        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=31320;TIMER=60;')
        kept = _leg('2350', 'SYSTEM-95', 2)
        added = _leg('31320', 'SYSTEM-95', 2)
        self.assertTrue(kept['ACTIVE'])
        self.assertEqual(kept['TO_TYPE'], 'OFF')
        self.assertTrue(added['ACTIVE'])
        self.assertEqual(self.slot['RX_TIME'], 12345)
        self.assertEqual(added['TIMEOUT'], 600)
        self.assertEqual(
            bm.CONFIG['SYSTEMS']['SYSTEM-95']['DEFAULT_UA_TIMER'], 10)


if __name__ == '__main__':
    unittest.main()
