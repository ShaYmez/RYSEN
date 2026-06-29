#!/usr/bin/env python3
import time
import unittest

from dmr_utils3.utils import bytes_3

from bridge_helpers import (
    reflector_bridge_matches_group_call,
    bridge_transmission_matches_rule,
    reflector_single_mode_wrong_tg,
    touch_reflector_ua_timers,
)


class TestReflectorTimeoutHelpers(unittest.TestCase):

    def test_non_reflector_bridge_always_matches(self):
        self.assertTrue(reflector_bridge_matches_group_call('2350', 2350))

    def test_reflector_bridge_matches_dial_tg(self):
        self.assertTrue(reflector_bridge_matches_group_call('#2350', 9))

    def test_reflector_bridge_matches_linked_tg(self):
        self.assertTrue(reflector_bridge_matches_group_call('#2350', 2350))

    def test_reflector_bridge_skips_unrelated_tg(self):
        self.assertFalse(reflector_bridge_matches_group_call('#2350', 3100))

    def test_transmission_match_linked_tg_not_dial_tgid(self):
        entry = {
            'TS': 2,
            'TGID': bytes_3(9),
            'ON': [bytes_3(2350)],
        }
        self.assertTrue(
            bridge_transmission_matches_rule('#2350', 2350, bytes_3(2350), 2, entry))
        # Dial channel matches entry TGID (9) — separate from linked-TG activity
        self.assertTrue(
            bridge_transmission_matches_rule('#2350', 9, bytes_3(9), 2, entry))

    def test_single_mode_does_not_deactivate_linked_tg(self):
        entry = {'TGID': bytes_3(9), 'ON': [bytes_3(2350)]}
        self.assertFalse(
            reflector_single_mode_wrong_tg(2350, bytes_3(2350), '#2350', entry))
        self.assertTrue(
            reflector_single_mode_wrong_tg(3100, bytes_3(3100), '#2350', entry))

    def test_touch_reflector_timers_on_bridged_traffic(self):
        now = time.time()
        bridges = {
            '#2350': [{
                'SYSTEM': 'SYSTEM-0',
                'TS': 2,
                'TGID': bytes_3(9),
                'ACTIVE': True,
                'TO_TYPE': 'ON',
                'TIMEOUT': 600,
                'TIMER': now,
                'ON': [bytes_3(2350)],
            }],
        }
        touch_reflector_ua_timers(bridges, '#2350', 2350, bytes_3(2350), 2, now + 1)
        self.assertEqual(bridges['#2350'][0]['TIMER'], now + 1 + 600)


if __name__ == '__main__':
    unittest.main()
