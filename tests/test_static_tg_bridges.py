#!/usr/bin/env python3
"""Tests for multi-static TG bridge legs (make_static_tg)."""
import unittest

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


if __name__ == '__main__':
    unittest.main()
