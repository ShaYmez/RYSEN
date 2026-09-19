#!/usr/bin/env python3
"""OpenBridge same-system dual-TG translator (stream-ID rewrite + hairpin)."""
import unittest
from unittest.mock import patch

from bridge_helpers import (
    OBP_TX_STREAM_CACHE_MAX,
    OBP_TX_STREAM_CACHE_TTL_S,
    allow_bridge_target,
    originated_obp_hairpin,
    reset_obp_tx_stream_cache,
    translated_obp_stream_id,
)


def _tgt(system, tgid, active=True):
    return {
        'SYSTEM': system,
        'TGID': tgid,
        'ACTIVE': active,
        'TS': 1,
    }


class TestAllowBridgeTarget(unittest.TestCase):

    def setUp(self):
        self.src_tg = b'\x00\x00t'
        self.dst_tg = b'\x03\x96\x8c'

    def test_same_system_openbridge_different_tgid_allows(self):
        self.assertTrue(allow_bridge_target(
            'OBP-UK', self.src_tg,
            _tgt('OBP-UK', self.dst_tg), 'OPENBRIDGE'))

    def test_same_system_openbridge_same_tgid_denies(self):
        self.assertFalse(allow_bridge_target(
            'OBP-UK', self.src_tg,
            _tgt('OBP-UK', self.src_tg), 'OPENBRIDGE'))

    def test_same_system_peer_different_tgid_denies(self):
        self.assertFalse(allow_bridge_target(
            'SYSTEM-80', self.src_tg,
            _tgt('SYSTEM-80', self.dst_tg), 'PEER'))
        self.assertFalse(allow_bridge_target(
            'SYSTEM-80', self.src_tg,
            _tgt('SYSTEM-80', self.dst_tg), 'MASTER'))

    def test_different_system_allows_if_active(self):
        self.assertTrue(allow_bridge_target(
            'TGIF-116', self.src_tg,
            _tgt('OBP-UK', self.src_tg), 'OPENBRIDGE'))
        self.assertTrue(allow_bridge_target(
            'SYSTEM-80', self.src_tg,
            _tgt('TGIF', self.dst_tg), 'OPENBRIDGE'))

    def test_inactive_denies(self):
        self.assertFalse(allow_bridge_target(
            'TGIF-116', self.src_tg,
            _tgt('OBP-UK', self.dst_tg, active=False), 'OPENBRIDGE'))
        self.assertFalse(allow_bridge_target(
            'OBP-UK', self.src_tg,
            _tgt('OBP-UK', self.dst_tg, active=False), 'OPENBRIDGE'))


class TestTranslatedObpStreamId(unittest.TestCase):

    def setUp(self):
        reset_obp_tx_stream_cache()
        self.orig = b'\x0a\x0b\x0c\x0d'
        self.src_tg = b'\x00\x00t'
        self.dst_tg = b'\x03\x96\x8c'

    def tearDown(self):
        reset_obp_tx_stream_cache()

    def test_rewrite_is_distinct_stable_and_never_zero(self):
        first = translated_obp_stream_id(
            self.orig, self.dst_tg, self.src_tg, now=10.0)
        second = translated_obp_stream_id(
            self.orig, self.dst_tg, self.src_tg, now=11.0)
        self.assertEqual(len(first), 4)
        self.assertEqual(first, second)
        self.assertNotEqual(first, self.orig)
        self.assertNotEqual(first, b'\x00\x00\x00\x00')

    def test_same_tgid_returns_original(self):
        self.assertIs(
            translated_obp_stream_id(
                self.orig, self.src_tg, self.src_tg, now=10.0),
            self.orig)

    def test_urandom_skips_zero_and_original(self):
        with patch('bridge_helpers.os.urandom') as urandom:
            urandom.side_effect = [
                b'\x00\x00\x00\x00',
                self.orig,
                b'\x11\x22\x33\x44',
            ]
            result = translated_obp_stream_id(
                self.orig, self.dst_tg, self.src_tg, now=10.0)
        self.assertEqual(result, b'\x11\x22\x33\x44')
        self.assertEqual(urandom.call_count, 3)

    def test_cache_eviction_still_returns_four_bytes(self):
        now = 100.0
        for i in range(OBP_TX_STREAM_CACHE_MAX + 8):
            dest = i.to_bytes(3, 'big')
            sid = translated_obp_stream_id(
                self.orig, dest, self.src_tg, now=now)
            self.assertEqual(len(sid), 4)
            self.assertNotEqual(sid, b'\x00\x00\x00\x00')
        expired = translated_obp_stream_id(
            self.orig, b'\xff\xff\xfe', self.src_tg,
            now=now + OBP_TX_STREAM_CACHE_TTL_S + 1)
        self.assertEqual(len(expired), 4)
        self.assertNotEqual(expired, b'\x00\x00\x00\x00')


class TestOriginatedObpHairpin(unittest.TestCase):

    def test_active_originated_stub_is_a_hairpin(self):
        status = {
            '_originated': True,
            'LAST': 10.0,
            'START': 9.0,
        }
        self.assertTrue(originated_obp_hairpin(status, 10.1, 0.360))

    def test_remote_or_idle_or_finished_is_not_a_hairpin(self):
        self.assertFalse(originated_obp_hairpin(None, 10.0, 0.360))
        self.assertFalse(originated_obp_hairpin(
            {'LAST': 10.0, 'START': 9.0}, 10.1, 0.360))
        self.assertFalse(originated_obp_hairpin(
            {'_originated': True, '_fin': True, 'LAST': 10.0},
            10.1, 0.360))
        self.assertFalse(originated_obp_hairpin(
            {'_originated': True, 'LAST': 9.0, 'START': 9.0},
            10.0, 0.360))


class TestLegacyBridgeSourceGuards(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open('bridge.py', encoding='utf-8') as fh:
            cls.legacy = fh.read()
        with open('bridge_master.py', encoding='utf-8') as fh:
            cls.master = fh.read()
        with open('bridge_helpers.py', encoding='utf-8') as fh:
            cls.helpers = fh.read()

    def test_helpers_are_stdlib_only_no_twisted(self):
        self.assertNotIn('from twisted', self.helpers)
        self.assertNotIn('import twisted', self.helpers)
        self.assertIn('import os', self.helpers)
        self.assertIn('from collections import OrderedDict', self.helpers)

    def test_legacy_bridge_imports_and_uses_helpers(self):
        self.assertIn('allow_bridge_target,', self.legacy)
        self.assertIn('originated_obp_hairpin,', self.legacy)
        self.assertIn('translated_obp_stream_id,', self.legacy)
        self.assertEqual(self.legacy.count('allow_bridge_target('), 2)
        self.assertEqual(self.legacy.count('translated_obp_stream_id('), 2)
        self.assertEqual(self.legacy.count('originated_obp_hairpin('), 1)

    def test_obp_packet_uses_translated_stream_id(self):
        self.assertEqual(
            self.legacy.count(
                "_tmp_bits.to_bytes(1, 'big'), _tx_stream_id]"),
            2)
        self.assertEqual(self.legacy.count('_data[16:20]'), 2)
        self.assertIn(
            "_target_status[_tx_stream_id]['_originated'] = True",
            self.legacy)

    def test_originated_hairpin_updates_last_and_returns(self):
        hairpin = self.legacy[
            self.legacy.index('if originated_obp_hairpin('):
            self.legacy.index('_obp_idle = (')
        ]
        self.assertIn("_obp_previous['LAST'] = pkt_time", hairpin)
        self.assertIn('return', hairpin)
        self.assertIn('STREAM_TO', hairpin)

    def test_bridge_master_is_unchanged(self):
        self.assertNotIn('allow_bridge_target(', self.master)
        self.assertNotIn('translated_obp_stream_id(', self.master)
        self.assertNotIn('originated_obp_hairpin(', self.master)
        self.assertIn(
            "if (_target['SYSTEM'] != self._system) and (_target['ACTIVE']):",
            self.master)


if __name__ == '__main__':
    unittest.main()
