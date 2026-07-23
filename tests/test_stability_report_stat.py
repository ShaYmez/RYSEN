#!/usr/bin/env python3
"""Tests for STAT trim interval, report payload slim, and lazy make_stat_bridge."""
import unittest

from dmr_utils3.utils import bytes_3

from bridge_helpers import (
    STAT_TRIMMER_INTERVAL_S,
    report_include_bridge_leg,
    clean_report_trigger_list,
    build_report_bridge_leg,
    sanitize_invalid_default_reflector_options,
)


class TestStatTrimmerInterval(unittest.TestCase):

    def test_interval_is_10_minutes(self):
        self.assertEqual(STAT_TRIMMER_INTERVAL_S, 600)

    def test_bridge_master_uses_constant(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('stat_trimmer_task.start(STAT_TRIMMER_INTERVAL_S)', source)
        self.assertNotIn('stat_trimmer_task.start(3600)', source)


class TestReportIncludeBridgeLeg(unittest.TestCase):

    def test_omit_idle_on(self):
        self.assertFalse(report_include_bridge_leg('ON', False))

    def test_keep_active_on(self):
        self.assertTrue(report_include_bridge_leg('ON', True))

    def test_keep_static_off_active(self):
        self.assertTrue(report_include_bridge_leg('OFF', True))

    def test_keep_stat(self):
        self.assertTrue(report_include_bridge_leg('STAT', True))
        self.assertTrue(report_include_bridge_leg('STAT', False))

    def test_keep_none(self):
        self.assertTrue(report_include_bridge_leg('NONE', True))


class TestBuildReportBridgeLeg(unittest.TestCase):

    def test_idle_on_omitted(self):
        leg = {
            'SYSTEM': 'SYSTEM-1', 'TS': 2, 'TGID': bytes_3(235),
            'ACTIVE': False, 'TO_TYPE': 'ON', 'TIMEOUT': 600,
            'OFF': [], 'ON': [bytes_3(235)], 'RESET': [], 'TIMER': 1.0,
        }
        self.assertIsNone(build_report_bridge_leg(leg, now_fn=lambda: 99.0))

    def test_static_off_active_retained_zero_timer(self):
        tgid = bytes_3(235)
        leg = {
            'SYSTEM': 'SYSTEM-1', 'TS': 2, 'TGID': tgid,
            'ACTIVE': True, 'TO_TYPE': 'OFF', 'TIMEOUT': 999,
            'OFF': [], 'ON': [tgid], 'RESET': [], 'TIMER': 42.0,
        }
        out = build_report_bridge_leg(leg, now_fn=lambda: 99.0)
        self.assertIsNotNone(out)
        self.assertEqual(out['TO_TYPE'], 'OFF')
        self.assertTrue(out['ACTIVE'])
        self.assertEqual(out['TIMEOUT'], 0)
        self.assertEqual(out['TIMER'], 0)
        self.assertEqual(out['ON'], [tgid])
        self.assertNotIn('OFF', out)
        self.assertNotIn('RESET', out)

    def test_stat_leg_omits_empty_triggers(self):
        leg = {
            'SYSTEM': 'OBP-EU', 'TS': 1, 'TGID': bytes_3(235),
            'ACTIVE': True, 'TO_TYPE': 'STAT', 'TIMEOUT': '',
            'OFF': [], 'ON': [], 'RESET': [], 'TIMER': 1.0,
        }
        out = build_report_bridge_leg(leg, now_fn=lambda: 99.0)
        self.assertEqual(out['TO_TYPE'], 'STAT')
        self.assertNotIn('ON', out)
        self.assertNotIn('OFF', out)
        self.assertNotIn('RESET', out)

    def test_clean_trigger_list(self):
        self.assertEqual(clean_report_trigger_list(None), [])
        self.assertEqual(clean_report_trigger_list([1, 2]), [1, 2])
        self.assertEqual(clean_report_trigger_list(5), [5])


class TestLazyMakeStatBridgeSource(unittest.TestCase):

    def test_make_stat_bridge_skips_master_mesh(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        idx = source.find('def make_stat_bridge(_tgid):')
        self.assertGreater(idx, 0)
        block = source[idx:idx + 1200]
        self.assertIn("TO_TYPE': 'STAT'", block)
        self.assertNotIn('iter_routing_master_systems()', block)
        self.assertIn('def _ensure_master_on_legs', source)
        self.assertIn('_ensure_master_on_legs(bridge_name, system)', source)


class TestStatTrimmerStaticProtection(unittest.TestCase):

    def test_off_active_still_in_use(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        idx = source.find('def statTrimmer():')
        block = source[idx:idx + 1500]
        self.assertIn("elif _system['TO_TYPE'] == 'OFF' and _system['ACTIVE']:", block)
        self.assertIn('repair_static_tgs_all_systems()', block)


class TestSanitizeInvalidDefaultReflector(unittest.TestCase):

    def test_dial_nine_rewritten(self):
        out, changed = sanitize_invalid_default_reflector_options(
            'TS2_STATIC=235;DIAL=9;SINGLE=1;')
        self.assertTrue(changed)
        self.assertIn('DIAL=0', out)
        self.assertNotIn('DIAL=9', out)

    def test_startref_and_default_reflector(self):
        out, changed = sanitize_invalid_default_reflector_options(
            'StartRef=9;DEFAULT_REFLECTOR=9;TS1_STATIC=;')
        self.assertTrue(changed)
        self.assertIn('StartRef=0', out)
        self.assertIn('DEFAULT_REFLECTOR=0', out)

    def test_second_pass_unchanged(self):
        first, _ = sanitize_invalid_default_reflector_options('DIAL=9;TS2_STATIC=235;')
        second, changed = sanitize_invalid_default_reflector_options(first)
        self.assertFalse(changed)
        self.assertEqual(first, second)

    def test_valid_reflector_untouched(self):
        opt = 'DEFAULT_REFLECTOR=2350;TS2_STATIC=116;'
        out, changed = sanitize_invalid_default_reflector_options(opt)
        self.assertFalse(changed)
        self.assertEqual(out, opt if opt.endswith(';') else opt)


if __name__ == '__main__':
    unittest.main()
