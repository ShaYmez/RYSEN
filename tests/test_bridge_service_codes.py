#!/usr/bin/env python3
"""Tests for dial service code bridge exclusion and static leg preservation."""
import unittest

from bridge_helpers import (
    is_dial_service_code,
    is_valid_talkgroup_bridge,
)


class TestIsValidTalkgroupBridge(unittest.TestCase):

    def test_service_codes_rejected(self):
        for code in (9, 4000, 5000, '9', '4000', '5000'):
            self.assertFalse(is_valid_talkgroup_bridge(str(code)))

    def test_parrot_range_rejected(self):
        self.assertFalse(is_valid_talkgroup_bridge('9991'))
        self.assertFalse(is_valid_talkgroup_bridge('9999'))

    def test_normal_talkgroups_accepted(self):
        self.assertTrue(is_valid_talkgroup_bridge('23426'))
        self.assertTrue(is_valid_talkgroup_bridge('235'))
        self.assertTrue(is_valid_talkgroup_bridge('#2350'))

    def test_hash_nine_reflector_rejected(self):
        self.assertFalse(is_valid_talkgroup_bridge('#9'))

    def test_dial_service_code_helper(self):
        self.assertTrue(is_dial_service_code(4000))
        self.assertFalse(is_dial_service_code(23426))


class TestBridgeServiceCodeSourcePatterns(unittest.TestCase):

    def test_obp_gen_stat_excludes_service_codes(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('not is_dial_service_code(int_id(_dst_id))', source)
        self.assertIn('is_valid_talkgroup_bridge(str(int_id(_dst_id)))', source)

    def test_timer_rebuild_skips_invalid_bridges(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if not is_valid_talkgroup_bridge(_bridge):', source)

    def test_purge_invalid_bridges_defined(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('def purge_invalid_bridges():', source)

    def test_remove_bridge_preserves_static_off(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("if (_bridgesystem['TO_TYPE'] == 'OFF' and _bridgesystem['ACTIVE'] == True):", source)

    def test_static_timer_zero_in_make_static_tg(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("'TIMEOUT': 0, 'TO_TYPE': 'OFF'", source)
        self.assertIn("'TIMER': 0", source)

    def test_monitor_static_off_reports_zero_timer(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("if _to_type == 'OFF' and _active:", source)

    def test_obp_stat_leg_on_static_create(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('def _ensure_obp_stat_leg(tg_s, tgid_b):', source)

    def test_reset_reapplies_statics(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('remove_bridge_system(_system)\n            reapply_static_tgs_for_system(_system)', source)

    def test_routing_skips_dial_service_codes(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if not is_dial_service_code(_int_dst):', source)
        self.assertIn('if not is_dial_service_code(int_id(_dst_id)):', source)
        self.assertIn('if int_id(_dst_id) == 4000:\n                    disconnect_dial_reflectors(self._system)', source)


if __name__ == '__main__':
    unittest.main()
