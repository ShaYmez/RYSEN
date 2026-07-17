#!/usr/bin/env python3
"""Tests for statTrimmer static-leg protection and static TG repair helpers."""
import unittest

from bridge_helpers import (
    bridge_has_active_static_leg,
    parse_options_static_fields,
    parse_static_tg_list,
)
from dmr_utils3.utils import bytes_3


class TestParseStaticTgList(unittest.TestCase):

    def test_empty_and_false(self):
        self.assertEqual(parse_static_tg_list(False), [])
        self.assertEqual(parse_static_tg_list(''), [])
        self.assertEqual(parse_static_tg_list('0'), [])
        self.assertEqual(parse_static_tg_list('False'), [])

    def test_normal_list(self):
        self.assertEqual(parse_static_tg_list('116,235,2350'), [116, 235, 2350])

    def test_strips_whitespace(self):
        self.assertEqual(parse_static_tg_list('116, 235 , 2350'), [116, 235, 2350])

    def test_skips_invalid(self):
        self.assertEqual(parse_static_tg_list('116,abc,0,16777215'), [116])


class TestParseOptionsStaticFields(unittest.TestCase):

    def test_ts1_ts2_selfcare_style(self):
        opt = 'TS1=;TS2=116,235,2350;SINGLE=0;TIMER=10'
        ts1, ts2 = parse_options_static_fields(opt)
        self.assertFalse(ts1)
        self.assertEqual(ts2, '116,235,2350')

    def test_dmr_plus_ts2_segments(self):
        opt = 'TS2_1=116;TS2_2=235;TS2_3=2350'
        ts1, ts2 = parse_options_static_fields(opt)
        self.assertFalse(ts1)
        self.assertEqual(ts2, '116,235,2350')


class TestBridgeHasActiveStaticLeg(unittest.TestCase):

    def _leg(self, system, ts, tg, active=True, to_type='OFF'):
        return {
            'SYSTEM': system,
            'TS': ts,
            'TGID': bytes_3(tg),
            'ACTIVE': active,
            'TO_TYPE': to_type,
        }

    def test_permanent_static_counts(self):
        bridges = {'235': [self._leg('SYSTEM-165', 2, 235)]}
        self.assertTrue(bridge_has_active_static_leg(bridges, 'SYSTEM-165', 2, 235))

    def test_inactive_off_not_static(self):
        bridges = {'235': [self._leg('SYSTEM-165', 2, 235, active=False, to_type='ON')]}
        self.assertFalse(bridge_has_active_static_leg(bridges, 'SYSTEM-165', 2, 235))

    def test_wrong_system(self):
        bridges = {'235': [self._leg('OTHER', 2, 235)]}
        self.assertFalse(bridge_has_active_static_leg(bridges, 'SYSTEM-165', 2, 235))


class TestStatTrimmerSourcePatterns(unittest.TestCase):

    def test_permanent_off_active_counts_in_use(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("elif _system['TO_TYPE'] == 'OFF' and _system['ACTIVE']:", source)
        self.assertIn('_in_use = True', source)

    def test_repair_after_stat_trimmer(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('repair_static_tgs_all_systems()', source)
        idx = source.find('def statTrimmer():')
        block = source[idx:idx + 2500]
        self.assertIn('repair_static_tgs_all_systems()', block)

    def test_ensure_static_after_options(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('ensure_static_tgs_for_system(_system, _tmout)', source)


class TestResetLifecycleSourcePatterns(unittest.TestCase):

    def test_reset_clears_static_legs(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('preserve_static_legs=False)', source)
        self.assertNotIn(
            'remove_bridge_system(_system)\n            reapply_static_tgs_for_system(_system)',
            source)

    def test_preserve_static_default_on_timer_change(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('def remove_bridge_system(system, new_timeout_s=None, preserve_static_legs=True):',
                      source)


if __name__ == '__main__':
    unittest.main()
