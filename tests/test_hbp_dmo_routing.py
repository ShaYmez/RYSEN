#!/usr/bin/env python3
"""HBP DMO (PA7LIM/BlueDV) routing: StartRef 4000, OPTIONS, STAT+static, OBP takeover."""
import unittest

from dmr_utils3.utils import bytes_3

from bridge_helpers import (
    bridge_has_active_static_leg,
    is_invalid_dial_reflector,
    is_permanent_static_leg,
    normalize_default_reflector,
    normalize_static_tg_csv,
    obp_allows_static_stream_takeover,
    parse_options_static_fields,
)


# Real PA7LIM BlueDV OPTIONS (comma before TS1_1, empty TS2_2)
BLUEDV_OPTIONS = 'StartRef=4000;RelinkTime=60;Userlink=1,TS1_1=;TS2_1=23426;TS2_2=;'


class TestStartRefServiceCodes(unittest.TestCase):

    def test_4000_and_5000_are_invalid_startup_reflectors(self):
        self.assertTrue(is_invalid_dial_reflector(4000))
        self.assertTrue(is_invalid_dial_reflector(5000))
        self.assertTrue(is_invalid_dial_reflector('4000'))

    def test_normalize_default_reflector_coerces_service_codes(self):
        self.assertEqual(normalize_default_reflector(4000), 0)
        self.assertEqual(normalize_default_reflector(5000), 0)
        self.assertEqual(normalize_default_reflector(9), 0)
        self.assertEqual(normalize_default_reflector(0), 0)
        self.assertEqual(normalize_default_reflector(2350), 2350)
        self.assertEqual(normalize_default_reflector('23426'), 23426)

    def test_make_default_reflector_guards_service_codes(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if is_invalid_dial_reflector(reflector):', source)
        self.assertIn('normalize_default_reflector', source)
        self.assertIn('StartRef/DEFAULT_REFLECTOR', source)
        self.assertIn('dial service', source)


class TestBlueDvOptionsParsing(unittest.TestCase):

    def test_bluedv_static_and_startref(self):
        ts1, ts2 = parse_options_static_fields(BLUEDV_OPTIONS)
        self.assertFalse(ts1)
        self.assertEqual(ts2, '23426')
        self.assertEqual(normalize_default_reflector(4000), 0)

    def test_empty_ts2_slots_no_trailing_comma(self):
        self.assertEqual(
            normalize_static_tg_csv('23426,'),
            '23426')
        self.assertEqual(
            normalize_static_tg_csv('23426,,116,'),
            '23426,116')
        self.assertFalse(normalize_static_tg_csv(''))
        self.assertFalse(normalize_static_tg_csv(','))

    def test_options_config_uses_split_maxsplit(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("k,v = x.split('=', 1)", source)
        self.assertIn("if 'Userlink' in _options:", source)
        self.assertIn('parse_options_static_fields(', source)


class TestStatMergeStatic(unittest.TestCase):

    def test_make_stat_bridge_repairs_statics(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        # make_stat_bridge body must call repair immediately after indexing
        start = source.index('def make_stat_bridge(_tgid):')
        end = source.index('\ndef ', start + 1)
        body = source[start:end]
        self.assertIn('repair_static_tgs_all_systems()', body)

    def test_bridge_has_active_static_leg_detects_off(self):
        tg = 23426
        tgid_b = bytes_3(tg)
        bridges = {
            str(tg): [
                {
                    'SYSTEM': 'OBP-1', 'TS': 1, 'TGID': tgid_b,
                    'ACTIVE': True, 'TO_TYPE': 'STAT',
                },
                {
                    'SYSTEM': 'SYSTEM-1', 'TS': 2, 'TGID': tgid_b,
                    'ACTIVE': True, 'TO_TYPE': 'OFF',
                },
            ]
        }
        self.assertTrue(bridge_has_active_static_leg(bridges, 'SYSTEM-1', 2, tg))
        self.assertFalse(bridge_has_active_static_leg(bridges, 'SYSTEM-1', 1, tg))
        self.assertFalse(bridge_has_active_static_leg(bridges, 'OBP-1', 1, tg))


class TestObpStaticContention(unittest.TestCase):

    def test_permanent_static_allows_idle_takeover(self):
        from const import STREAM_TO
        from bridge_helpers import hbp_tx_stream_locked
        static = {'TO_TYPE': 'OFF', 'ACTIVE': True, 'TGID': bytes_3(23426)}
        ua = {'TO_TYPE': 'ON', 'ACTIVE': True, 'TGID': bytes_3(23426)}
        inactive = {'TO_TYPE': 'OFF', 'ACTIVE': False, 'TGID': bytes_3(23426)}
        self.assertTrue(is_permanent_static_leg(static))
        self.assertTrue(obp_allows_static_stream_takeover(static))
        self.assertFalse(obp_allows_static_stream_takeover(ua))
        self.assertFalse(obp_allows_static_stream_takeover(inactive))
        live = {'TX_STREAM_ID': b'\x11\x11\x11\x11', 'TX_TIME': 9.9}
        self.assertTrue(hbp_tx_stream_locked(live, b'\x22\x22\x22\x22', 10.0, STREAM_TO))
        self.assertFalse(obp_allows_static_stream_takeover(
            static, live, b'\x22\x22\x22\x22', 10.0, STREAM_TO))

    def test_obp_to_target_locks_live_static_tx(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('hbp_tx_stream_locked', source)
        self.assertIn('TX stream locked', source)


if __name__ == '__main__':
    unittest.main()
