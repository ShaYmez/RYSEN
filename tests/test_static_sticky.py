#!/usr/bin/env python3
"""Tests for static vs sticky TG interaction and static bridge protection."""
import unittest

from bridge_helpers import system_has_static_tgs


class TestSystemHasStaticTgs(unittest.TestCase):

    def test_empty_statics(self):
        self.assertFalse(system_has_static_tgs({}))
        self.assertFalse(system_has_static_tgs({'TS1_STATIC': '', 'TS2_STATIC': ''}))
        self.assertFalse(system_has_static_tgs({'TS1_STATIC': False, 'TS2_STATIC': False}))
        self.assertFalse(system_has_static_tgs({'TS1_STATIC': '0', 'TS2_STATIC': 'False'}))

    def test_ts1_static(self):
        self.assertTrue(system_has_static_tgs({'TS1_STATIC': '235,23426'}))
        self.assertFalse(system_has_static_tgs({'TS2_STATIC': ''}))

    def test_ts2_static(self):
        self.assertTrue(system_has_static_tgs({'TS2_STATIC': '2350'}))

    def test_whitespace_ignored_as_empty(self):
        self.assertFalse(system_has_static_tgs({'TS1_STATIC': '   '}))


class TestStaticStickySourcePatterns(unittest.TestCase):

    def test_sticky_gated_when_statics_configured(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('not system_has_static_tgs(CONFIG[\'SYSTEMS\'][_system[\'SYSTEM\']])',
                      source)

    def test_single_mode_skips_active_off_legs(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn(
            "if (_system['TO_TYPE'] == 'OFF' and _system['ACTIVE'] == True):",
            source)
        self.assertIn('static / default reflector — never torn down by wrong-TG traffic',
                      source)

    def test_reapply_statics_after_timer_change(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertNotIn('reapply_static_tgs_for_system(_system, _tmout)', source)
        self.assertIn('def reapply_static_tgs_for_system(system, tmout=None):', source)


if __name__ == '__main__':
    unittest.main()
