#!/usr/bin/env python3
"""StartRef/OPTIONS coercion for BlueDV/Peanut (no DMO TX locks)."""
import unittest

from bridge_helpers import (
    is_invalid_dial_reflector,
    normalize_default_reflector,
    normalize_static_tg_csv,
    parse_options_static_fields,
)


# Real PA7LIM BlueDV OPTIONS (comma before TS1_1, empty TS2_2)
BLUEDV_OPTIONS = 'StartRef=4000;RelinkTime=60;Userlink=1,TS1_1=;TS2_1=116;TS2_2=;'


class TestStartRefServiceCodes(unittest.TestCase):

    def test_4000_and_5000_are_invalid_startup_reflectors(self):
        self.assertTrue(is_invalid_dial_reflector(4000))
        self.assertTrue(is_invalid_dial_reflector(5000))
        self.assertTrue(is_invalid_dial_reflector('4000'))
        self.assertTrue(is_invalid_dial_reflector(9))
        self.assertFalse(is_invalid_dial_reflector(2350))

    def test_normalize_default_reflector_coerces_service_codes(self):
        self.assertEqual(normalize_default_reflector(4000), 0)
        self.assertEqual(normalize_default_reflector(5000), 0)
        self.assertEqual(normalize_default_reflector(9), 0)
        self.assertEqual(normalize_default_reflector(0), 0)
        self.assertEqual(normalize_default_reflector(2350), 2350)
        self.assertEqual(normalize_default_reflector('23426'), 23426)
        self.assertEqual(normalize_default_reflector('bad'), 0)

    def test_options_path_uses_normalize(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('normalize_default_reflector', source)
        self.assertIn('StartRef/DEFAULT_REFLECTOR', source)
        self.assertIn('dial service', source)
        self.assertIn('parse_options_static_fields(', source)


class TestBlueDvOptionsParsing(unittest.TestCase):

    def test_bluedv_static_and_startref(self):
        ts1, ts2 = parse_options_static_fields(BLUEDV_OPTIONS)
        self.assertFalse(ts1)
        self.assertEqual(ts2, '116')
        self.assertEqual(normalize_default_reflector(4000), 0)

    def test_empty_ts2_slots_no_trailing_comma(self):
        self.assertEqual(normalize_static_tg_csv('23426,'), '23426')
        self.assertEqual(normalize_static_tg_csv('23426,,116,'), '23426,116')
        self.assertFalse(normalize_static_tg_csv(''))
        self.assertFalse(normalize_static_tg_csv(','))


if __name__ == '__main__':
    unittest.main()
