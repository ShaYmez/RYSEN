#!/usr/bin/env python3
"""Phase 4A — dial-a-tg vs unit-to-unit private-call destination classifier."""
import unittest

from bridge_helpers import (
    is_dial_a_tg_link_target,
    is_dial_service_code,
    is_reflector_private_destination,
    private_call_may_create_reflector,
)


class TestDialATgLinkTarget(unittest.TestCase):

    def test_link_targets_are_five_digit_talkgroups(self):
        self.assertTrue(is_dial_a_tg_link_target(2350))
        self.assertTrue(is_dial_a_tg_link_target(99999))
        self.assertTrue(is_dial_a_tg_link_target(5))

    def test_reserved_and_service_ids_not_link_targets(self):
        self.assertFalse(is_dial_a_tg_link_target(8))
        self.assertFalse(is_dial_a_tg_link_target(9))
        self.assertFalse(is_dial_a_tg_link_target(4000))
        self.assertFalse(is_dial_a_tg_link_target(5000))
        for code in range(9991, 10000):
            self.assertFalse(is_dial_a_tg_link_target(code))

    def test_repeater_and_subscriber_ids_not_link_targets(self):
        self.assertFalse(is_dial_a_tg_link_target(235287))
        self.assertFalse(is_dial_a_tg_link_target(2348831))
        self.assertFalse(is_dial_a_tg_link_target(1234567890))


class TestReflectorPrivateDestination(unittest.TestCase):

    def test_service_codes_are_reflector_destinations(self):
        self.assertTrue(is_reflector_private_destination(4000))
        self.assertTrue(is_reflector_private_destination(5000))
        self.assertTrue(is_reflector_private_destination(9991))
        self.assertTrue(is_reflector_private_destination(9999))

    def test_link_talkgroups_are_reflector_destinations(self):
        self.assertTrue(is_reflector_private_destination(2350))
        self.assertTrue(is_reflector_private_destination(9990))

    def test_subscriber_and_repeater_ids_are_not_reflector_destinations(self):
        self.assertFalse(is_reflector_private_destination(235287))
        self.assertFalse(is_reflector_private_destination(2348831))
        self.assertFalse(is_reflector_private_destination(1234567890))

    def test_ignored_ids_are_not_reflector_destinations(self):
        self.assertFalse(is_reflector_private_destination(8))
        self.assertFalse(is_reflector_private_destination(9))
        self.assertFalse(is_reflector_private_destination(4))


class TestPrivateCallMayCreateReflector(unittest.TestCase):

    def test_link_target_may_create_when_missing(self):
        self.assertTrue(private_call_may_create_reflector(2350, {}))

    def test_subscriber_may_not_create(self):
        self.assertFalse(private_call_may_create_reflector(2348831, {}))

    def test_repeater_may_not_create(self):
        self.assertFalse(private_call_may_create_reflector(235287, {}))

    def test_service_codes_may_not_create(self):
        self.assertFalse(private_call_may_create_reflector(4000, {}))
        self.assertFalse(private_call_may_create_reflector(5000, {}))
        self.assertFalse(private_call_may_create_reflector(9995, {}))

    def test_existing_bridge_may_not_create(self):
        self.assertFalse(private_call_may_create_reflector(2350, {'#2350': []}))


class TestClassifierRegression(unittest.TestCase):
    """Dial-a-tg behaviour that must not regress."""

    def test_dial_service_codes_unchanged(self):
        self.assertTrue(is_dial_service_code(9))
        self.assertTrue(is_dial_service_code(4000))
        self.assertTrue(is_dial_service_code(5000))
        self.assertFalse(is_dial_service_code(2350))


if __name__ == '__main__':
    unittest.main()
