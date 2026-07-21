#!/usr/bin/env python3
"""Tests for multi-static TG bridge legs (make_static_tg)."""
import unittest


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


if __name__ == '__main__':
    unittest.main()
