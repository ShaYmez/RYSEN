#!/usr/bin/env python3
"""Tests for dial service code bridge exclusion and static leg preservation."""
import unittest

from bridge_helpers import (
    is_dial_service_code,
    is_valid_talkgroup_bridge,
    skip_bridge_idx_routing,
)


class TestIsValidTalkgroupBridge(unittest.TestCase):

    def test_service_codes_rejected(self):
        for code in (9, 4000, 5000, '9', '4000', '5000'):
            self.assertFalse(is_valid_talkgroup_bridge(str(code)))

    def test_parrot_range_rejected(self):
        self.assertFalse(is_valid_talkgroup_bridge('9991'))
        self.assertFalse(is_valid_talkgroup_bridge('9999'))

    def test_parrot_talkgroup_accepted(self):
        from bridge_helpers import is_parrot_talkgroup, PARROT_TG
        self.assertTrue(is_valid_talkgroup_bridge('9990'))
        self.assertTrue(is_parrot_talkgroup(PARROT_TG))
        self.assertFalse(is_parrot_talkgroup(9991))

    def test_normal_talkgroups_accepted(self):
        self.assertTrue(is_valid_talkgroup_bridge('23426'))
        self.assertTrue(is_valid_talkgroup_bridge('235'))
        self.assertTrue(is_valid_talkgroup_bridge('#2350'))

    def test_hash_nine_reflector_rejected(self):
        self.assertFalse(is_valid_talkgroup_bridge('#9'))

    def test_dial_service_code_helper(self):
        self.assertTrue(is_dial_service_code(4000))
        self.assertFalse(is_dial_service_code(23426))


class TestSkipBridgeIdxRouting(unittest.TestCase):

    def test_disconnect_and_status_skipped(self):
        self.assertTrue(skip_bridge_idx_routing(4000))
        self.assertTrue(skip_bridge_idx_routing(5000))
        self.assertTrue(skip_bridge_idx_routing('4000'))

    def test_tg9_dial_voice_not_skipped(self):
        """TG 9 is the dial-a-tg voice channel after PC link — must enter BRIDGE_IDX."""
        self.assertFalse(skip_bridge_idx_routing(9))
        self.assertFalse(skip_bridge_idx_routing('9'))

    def test_normal_tg_not_skipped(self):
        self.assertFalse(skip_bridge_idx_routing(2350))
        self.assertFalse(skip_bridge_idx_routing(235))


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
        self.assertIn('preserve_static_legs', source)
        self.assertIn("_bridgesystem['TO_TYPE'] == 'OFF' and _bridgesystem['ACTIVE'] == True", source)

    def test_static_timer_zero_in_make_static_tg(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("'TIMEOUT': 0, 'TO_TYPE': 'OFF'", source)
        self.assertIn("'TIMER': 0", source)

    def test_monitor_static_off_reports_zero_timer(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        # Slim report path lives in bridge_helpers.build_report_bridge_leg
        self.assertIn('build_report_bridge_leg(bridge_system)', source)
        with open('bridge_helpers.py', encoding='utf-8') as fh:
            helpers = fh.read()
        self.assertIn("if _to_type == 'OFF' and _active:", helpers)

    def test_obp_stat_leg_on_static_create(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('def _ensure_obp_stat_leg(tg_s, tgid_b):', source)

    def test_reset_clears_statics_until_reconnect(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('preserve_static_legs=False)', source)
        self.assertIn('parse_options_static_fields(_opt_str)', source)
        self.assertNotIn(
            'remove_bridge_system(_system)\n            reapply_static_tgs_for_system(_system)',
            source)

    def test_routing_skips_only_4000_5000_not_tg9(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('skip_bridge_idx_routing', source)
        self.assertIn('if not skip_bridge_idx_routing(_int_dst):', source)
        self.assertIn('if not skip_bridge_idx_routing(int_id(_dst_id)):', source)
        # Must not reintroduce the over-broad gate that blocked dial-a-tg TG9 voice
        self.assertNotIn('if not is_dial_service_code(_int_dst):', source)
        self.assertNotIn('if not is_dial_service_code(int_id(_dst_id)):', source)
        self.assertIn(
            'if int_id(_dst_id) == 4000:\n                    disconnect_dial_reflectors(self._system)',
            source)

    def test_parrot_never_routes_obp(self):
        from bridge_helpers import is_parrot_bridge, PARROT_TG
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertTrue(is_parrot_bridge(str(PARROT_TG)))
        self.assertTrue(is_parrot_bridge('#9990'))
        self.assertIn('is_parrot_bridge(_bridge)', source)
        self.assertIn('Parrot (TG 9990) is HBP/PEER only', source)
        with open('hblink.py', encoding='utf-8') as fh:
            hblink = fh.read()
        self.assertIn('_int_dst_id >= 9990 and _int_dst_id <= 9999', hblink)


if __name__ == '__main__':
    unittest.main()
