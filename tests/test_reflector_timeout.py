#!/usr/bin/env python3
import unittest

from bridge_helpers import reflector_bridge_matches_group_call


class TestReflectorTimeoutHelpers(unittest.TestCase):

    def test_non_reflector_bridge_always_matches(self):
        self.assertTrue(reflector_bridge_matches_group_call('2350', 2350))

    def test_reflector_bridge_matches_dial_tg(self):
        self.assertTrue(reflector_bridge_matches_group_call('#2350', 9))

    def test_reflector_bridge_matches_linked_tg(self):
        self.assertTrue(reflector_bridge_matches_group_call('#2350', 2350))

    def test_reflector_bridge_skips_unrelated_tg(self):
        self.assertFalse(reflector_bridge_matches_group_call('#2350', 3100))


if __name__ == '__main__':
    unittest.main()
