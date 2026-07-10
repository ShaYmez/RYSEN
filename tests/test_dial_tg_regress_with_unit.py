#!/usr/bin/env python3
"""Dial-a-tg regression with unit routing classifiers."""
import unittest

from bridge_helpers import (
    is_dial_a_tg_link_target,
    is_reflector_private_destination,
    private_call_may_create_reflector,
)


class TestDialTgRegressWithUnit(unittest.TestCase):

    def test_subscriber_not_reflector_destination(self):
        self.assertFalse(is_reflector_private_destination(2348831))
        self.assertFalse(private_call_may_create_reflector(2348831, {}))

    def test_link_target_is_reflector_not_forward(self):
        self.assertTrue(is_reflector_private_destination(2350))
        self.assertTrue(is_dial_a_tg_link_target(2350))
        self.assertTrue(private_call_may_create_reflector(2350, {}))

    def test_service_codes_remain_reflector(self):
        for code in (4000, 5000, 9995):
            self.assertTrue(is_reflector_private_destination(code))

    def test_reserved_ids_neither_path(self):
        for code in (8, 9):
            self.assertFalse(is_reflector_private_destination(code))


if __name__ == '__main__':
    unittest.main()
