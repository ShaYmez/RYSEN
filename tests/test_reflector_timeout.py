#!/usr/bin/env python3
import unittest

from dmr_utils3.utils import bytes_3

from bridge_helpers import (
    reflector_bridge_matches_group_call,
    bridge_transmission_matches_rule,
    reflector_single_mode_wrong_tg,
    reflector_timer_reset_allowed,
    set_reflector_link_owner,
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
        self.assertTrue(
            bridge_transmission_matches_rule('#2350', 9, bytes_3(9), 2, entry))

    def test_single_mode_does_not_deactivate_linked_tg(self):
        entry = {'TGID': bytes_3(9), 'ON': [bytes_3(2350)]}
        self.assertFalse(
            reflector_single_mode_wrong_tg(2350, bytes_3(2350), '#2350', entry))
        self.assertTrue(
            reflector_single_mode_wrong_tg(3100, bytes_3(3100), '#2350', entry))

    def test_timer_reset_only_for_link_owner(self):
        entry = {
            'TS': 2,
            'TGID': bytes_3(9),
            'ON': [bytes_3(2350)],
        }
        owner = bytes_3(2348831)
        other = bytes_3(2345875)
        peer = bytes_3(1234567)
        set_reflector_link_owner(entry, owner, peer)

        self.assertTrue(
            reflector_timer_reset_allowed('#2350', entry, owner, peer))
        self.assertFalse(
            reflector_timer_reset_allowed('#2350', entry, other, peer))
        self.assertFalse(
            reflector_timer_reset_allowed('#2350', entry, owner, bytes_3(9999999)))

    def test_timer_reset_without_linker_denied(self):
        entry = {'TS': 2, 'TGID': bytes_3(9), 'ON': [bytes_3(2350)]}
        self.assertFalse(
            reflector_timer_reset_allowed(
                '#2350', entry, bytes_3(2348831), bytes_3(1234567)))

    def test_normal_bridge_timer_reset_not_restricted(self):
        entry = {'TS': 2, 'TGID': bytes_3(2350), 'ON': [bytes_3(2350)]}
        self.assertTrue(
            reflector_timer_reset_allowed(
                '2350', entry, bytes_3(9999999), bytes_3(1234567)))


if __name__ == '__main__':
    unittest.main()
