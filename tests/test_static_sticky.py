#!/usr/bin/env python3
"""Sticky TG vs statics, and per-peer UA expiry/mute."""

import time
import unittest

import bridge_master as bm
from bridge_helpers import (
    dest_peer_rx_blocked,
    expire_open_peer_dynamic_tgs,
    overlay_peer_ua_for_report,
    remember_peer_dynamic_tg,
    reset_peer_rx_filters,
    sticky_flag_from_options,
    sticky_tg_enabled,
    ua_timer_minutes_from_options,
)
from dmr_utils3.utils import bytes_3
class TestOptionsParsers(unittest.TestCase):
    def test_sticky_flag_from_options(self):
        self.assertTrue(sticky_flag_from_options('TS1=;STICKY=1;'))
        self.assertFalse(sticky_flag_from_options(b'STICKY=0;'))
        self.assertIsNone(sticky_flag_from_options('TS2=2350;'))

    def test_ua_timer_from_options(self):
        self.assertEqual(ua_timer_minutes_from_options('TIMER=5;'), 5)
        self.assertEqual(ua_timer_minutes_from_options('RelinkTime=15;'), 15)
        self.assertIsNone(ua_timer_minutes_from_options('TS2=2350;'))


class TestStickyTgEnabled(unittest.TestCase):
    def test_statics_disable_sticky(self):
        self.assertFalse(sticky_tg_enabled(
            {'STICKY_TG': True}, {'STICKY': True}, has_static_tgs=True))

    def test_peer_sticky_zero_overrides_system(self):
        self.assertFalse(sticky_tg_enabled(
            {'STICKY_TG': True}, {'STICKY': False}, has_static_tgs=False))

    def test_peer_sticky_one_overrides_system_off(self):
        self.assertTrue(sticky_tg_enabled(
            {'STICKY_TG': False}, {'STICKY': True}, has_static_tgs=False))

    def test_system_sticky_when_peer_omits_flag(self):
        self.assertTrue(sticky_tg_enabled(
            {'STICKY_TG': True}, {}, has_static_tgs=False))
        self.assertFalse(sticky_tg_enabled(
            {'STICKY_TG': False}, {}, has_static_tgs=False))


class TestPeerStickyOnConfig(unittest.TestCase):
    def setUp(self):
        self.prev = getattr(bm, 'CONFIG', None)
        self.peer = (234243404).to_bytes(4, 'big')
        bm.CONFIG = {'SYSTEMS': {
            'SYSTEM-1': {
                'STICKY_TG': True,
                'TS1_STATIC': '',
                'TS2_STATIC': '',
                'PEERS': {self.peer: {}},
            }
        }}

    def tearDown(self):
        bm.CONFIG = self.prev

    def test_empty_ts_lists_do_not_count_as_statics(self):
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['OPTIONS'] = (
            'TS1=;TS2=;STICKY=1;')
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['STICKY'] = True
        self.assertFalse(bm.peer_has_any_static_tgs('SYSTEM-1', self.peer))
        self.assertTrue(bm.peer_sticky_tg_enabled('SYSTEM-1', self.peer))

    def test_peer_statics_disable_sticky(self):
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['OPTIONS'] = (
            'TS2=2350,23516;STICKY=1;')
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['STICKY'] = True
        self.assertTrue(bm.peer_has_any_static_tgs('SYSTEM-1', self.peer))
        self.assertFalse(bm.peer_sticky_tg_enabled('SYSTEM-1', self.peer))

    def test_live_union_statics_do_not_disable_this_peer(self):
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['TS1_STATIC'] = '235'
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['TS2_STATIC'] = '2350,23516'
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['_default_options'] = (
            'TS1_STATIC=;TS2_STATIC=;')
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['OPTIONS'] = (
            'TS1=;TS2=;STICKY=1;')
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['STICKY'] = True
        self.assertFalse(bm.peer_has_any_static_tgs('SYSTEM-1', self.peer))
        self.assertTrue(bm.peer_sticky_tg_enabled('SYSTEM-1', self.peer))

    def test_cfg_default_statics_disable_sticky(self):
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['_default_options'] = (
            'TS2_STATIC=2350;')
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['OPTIONS'] = (
            'STICKY=1;')
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['STICKY'] = True
        self.assertTrue(bm.peer_has_any_static_tgs('SYSTEM-1', self.peer))
        self.assertFalse(bm.peer_sticky_tg_enabled('SYSTEM-1', self.peer))

    def test_options_sticky_zero_overrides_stale_peer_flag(self):
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['OPTIONS'] = (
            'TS1=;TS2=;STICKY=0;')
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['STICKY'] = True
        self.assertFalse(bm.peer_sticky_tg_enabled('SYSTEM-1', self.peer))

    def test_missing_peer_id_is_not_sticky(self):
        self.assertFalse(bm.peer_sticky_tg_enabled('SYSTEM-1', None))

    def test_stanza_statics_without_snapshot_disable_sticky(self):
        cfg = bm.CONFIG['SYSTEMS']['SYSTEM-1']
        cfg['MODE'] = 'MASTER'
        cfg['TS2_STATIC'] = '2350'
        cfg['DEFAULT_UA_TIMER'] = 10
        cfg.pop('_default_options', None)
        cfg['PEERS'][self.peer]['OPTIONS'] = 'STICKY=1;'
        cfg['PEERS'][self.peer]['STICKY'] = True
        self.assertTrue(bm.peer_has_any_static_tgs('SYSTEM-1', self.peer))
        self.assertFalse(bm.peer_sticky_tg_enabled('SYSTEM-1', self.peer))

    def test_remember_uses_peer_timer(self):
        reset_peer_rx_filters()
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['DEFAULT_UA_TIMER'] = 10
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['OPTIONS'] = (
            'TIMER=5;STICKY=0;')
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][self.peer]['STICKY'] = False
        self.assertEqual(bm.peer_ua_timer_minutes('SYSTEM-1', self.peer), 5)
        bm._remember_source_dynamic_tg(
            'SYSTEM-1', self.peer, 2, 91, pkt_time=100.0,
            timeout_s=bm.peer_ua_timer_minutes('SYSTEM-1', self.peer) * 60)
        stream = b'\x00\x00\x00\x0a'
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-1', self.peer, stream, 2, 91, now=350.0))
        self.assertTrue(dest_peer_rx_blocked(
            'SYSTEM-1', self.peer, stream, 2, 91, now=450.0))


class TestStickyMembershipDoesNotMute(unittest.TestCase):
    def setUp(self):
        reset_peer_rx_filters()
        self.peer = b'\x00\x23\xc5\x93'
        self.stream = b'\x00\x00\x00\x0a'

    def tearDown(self):
        reset_peer_rx_filters()

    def test_no_expiry_stays_open_after_relink_time(self):
        remember_peer_dynamic_tg(
            'SYSTEM-1', self.peer, 2, 91, expires_at=None)
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-1', self.peer, self.stream, 2, 91, now=10_000.0))

    def test_exclusive_slot_mutes_previous_tg(self):
        remember_peer_dynamic_tg(
            'SYSTEM-1', self.peer, 2, 91, expires_at=None)
        remember_peer_dynamic_tg(
            'SYSTEM-1', self.peer, 2, 2350, expires_at=None,
            exclusive_slot=True)
        self.assertTrue(dest_peer_rx_blocked(
            'SYSTEM-1', self.peer, self.stream, 2, 91, now=11.0))
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-1', self.peer, self.stream, 2, 2350, now=11.0))

    def test_remember_helper_skips_expiry_when_sticky(self):
        prev = getattr(bm, 'CONFIG', None)
        peer = self.peer
        bm.CONFIG = {'SYSTEMS': {
            'SYSTEM-1': {
                'STICKY_TG': True,
                'TS1_STATIC': '',
                'TS2_STATIC': '',
                'PEERS': {peer: {'OPTIONS': 'TS1=;TS2=;STICKY=1;',
                                 'STICKY': True}},
            }
        }}
        try:
            bm._remember_source_dynamic_tg(
                'SYSTEM-1', peer, 2, 91, pkt_time=100.0, timeout_s=600)
            self.assertFalse(dest_peer_rx_blocked(
                'SYSTEM-1', peer, self.stream, 2, 91, now=800.0))
        finally:
            bm.CONFIG = prev

    def test_remember_helper_expires_when_statics_set(self):
        prev = getattr(bm, 'CONFIG', None)
        peer = self.peer
        bm.CONFIG = {'SYSTEMS': {
            'SYSTEM-1': {
                'STICKY_TG': True,
                'TS1_STATIC': '',
                'TS2_STATIC': '',
                'PEERS': {peer: {'OPTIONS': 'TS2=2350;', 'STICKY': True}},
            }
        }}
        try:
            bm._remember_source_dynamic_tg(
                'SYSTEM-1', peer, 2, 91, pkt_time=100.0, timeout_s=600)
            self.assertTrue(dest_peer_rx_blocked(
                'SYSTEM-1', peer, self.stream, 2, 91, now=800.0))
        finally:
            bm.CONFIG = prev

    def test_expire_open_memberships_only_fills_none(self):
        remember_peer_dynamic_tg(
            'SYSTEM-1', self.peer, 2, 91, expires_at=None)
        remember_peer_dynamic_tg(
            'SYSTEM-1', self.peer, 2, 2350, expires_at=50.0)
        self.assertEqual(
            expire_open_peer_dynamic_tgs('SYSTEM-1', self.peer, 80.0), 1)
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-1', self.peer, self.stream, 2, 91, now=70.0))
        self.assertTrue(dest_peer_rx_blocked(
            'SYSTEM-1', self.peer, self.stream, 2, 91, now=81.0))
        self.assertTrue(dest_peer_rx_blocked(
            'SYSTEM-1', self.peer, self.stream, 2, 2350, now=51.0))

    def test_options_turning_sticky_off_starts_relink_time(self):
        prev = getattr(bm, 'CONFIG', None)
        peer = self.peer
        bm.CONFIG = {'SYSTEMS': {
            'SYSTEM-1': {
                'STICKY_TG': True,
                'DEFAULT_UA_TIMER': 10,
                'TS1_STATIC': '',
                'TS2_STATIC': '',
                'PEERS': {peer: {'OPTIONS': 'TS2=2350;', 'STICKY': False}},
            }
        }}
        try:
            remember_peer_dynamic_tg(
                'SYSTEM-1', peer, 2, 91, expires_at=None)
            before = time.time()
            bm._expire_sticky_membership_if_disabled('SYSTEM-1', peer)
            self.assertFalse(dest_peer_rx_blocked(
                'SYSTEM-1', peer, self.stream, 2, 91, now=before + 10))
            self.assertTrue(dest_peer_rx_blocked(
                'SYSTEM-1', peer, self.stream, 2, 91, now=before + 700))
        finally:
            bm.CONFIG = prev

    def test_selfcare_apply_expires_immediately_with_peer_timer(self):
        prev = getattr(bm, 'CONFIG', None)
        peer = self.peer
        bm.CONFIG = {'SYSTEMS': {
            'SYSTEM-1': {
                'MODE': 'MASTER',
                'STICKY_TG': True,
                'DEFAULT_UA_TIMER': 10,
                'TS1_STATIC': '',
                'TS2_STATIC': '',
                'PEERS': {peer: {'OPTIONS': 'TS1=;TS2=;STICKY=1;',
                                 'STICKY': True}},
            }
        }, 'REPORTS': {'REPORT': False}}
        try:
            remember_peer_dynamic_tg(
                'SYSTEM-1', peer, 2, 91, expires_at=None)
            before = time.time()
            remaining, had_disc = bm.apply_selfcare_options(
                'SYSTEM-1', peer, 'TS2=2350;STICKY=0;TIMER=5;')
            self.assertFalse(had_disc)
            self.assertEqual(remaining, 'TS2=2350;STICKY=0;TIMER=5;')
            self.assertFalse(
                bm.CONFIG['SYSTEMS']['SYSTEM-1']['PEERS'][peer]['STICKY'])
            self.assertEqual(bm.peer_ua_timer_minutes('SYSTEM-1', peer), 5)
            self.assertFalse(dest_peer_rx_blocked(
                'SYSTEM-1', peer, self.stream, 2, 91, now=before + 60))
            self.assertTrue(dest_peer_rx_blocked(
                'SYSTEM-1', peer, self.stream, 2, 91, now=before + 400))
        finally:
            bm.CONFIG = prev


class Test4000ClearsStickyMembership(unittest.TestCase):
    def setUp(self):
        reset_peer_rx_filters()
        self.prev_config = getattr(bm, 'CONFIG', None)
        self.prev_bridges = getattr(bm, 'BRIDGES', None)
        self.prev_sub = getattr(bm, 'SUB_MAP', None)
        self.prev_idx = getattr(bm, 'BRIDGE_IDX', None)
        self.peer = b'\x00\x23\xc5\x93'
        self.radio = bytes_3(2342434)
        self.stream = b'\x00\x00\x00\x0a'
        bm.CONFIG = {
            'SYSTEMS': {'SYSTEM-1': {
                'MODE': 'MASTER',
                'STICKY_TG': True,
                'DEFAULT_UA_TIMER': 10,
                'PEERS': {self.peer: {'OPTIONS': 'TS1=;TS2=;STICKY=1;',
                                      'STICKY': True}},
            }},
            'REPORTS': {'REPORT': False},
        }
        bm.BRIDGES = {'91': [{
            'SYSTEM': 'SYSTEM-1', 'TS': 2, 'TGID': bytes_3(91),
            'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMER': 0,
        }]}
        bm.SUB_MAP = {
            self.radio: ('SYSTEM-1', 2, bytes_3(91), 1.0, self.peer),
        }
        bm.BRIDGE_IDX = {}

    def tearDown(self):
        reset_peer_rx_filters()
        bm.CONFIG = self.prev_config
        bm.BRIDGES = {} if self.prev_bridges is None else self.prev_bridges
        bm.SUB_MAP = {} if self.prev_sub is None else self.prev_sub
        if self.prev_idx is not None:
            bm.BRIDGE_IDX = self.prev_idx

    def test_4000_mutes_and_clears_sticky_ua(self):
        remember_peer_dynamic_tg(
            'SYSTEM-1', self.peer, 2, 91, expires_at=None)
        bm.clear_subscriber_on_disconnect(
            'SYSTEM-1', self.radio, self.peer)
        self.assertNotIn(self.radio, bm.SUB_MAP)
        self.assertFalse(bm.BRIDGES['91'][0]['ACTIVE'])
        self.assertTrue(dest_peer_rx_blocked(
            'SYSTEM-1', self.peer, self.stream, 2, 91))


class TestPeerUaReport(unittest.TestCase):
    def setUp(self):
        reset_peer_rx_filters()
        self.peer_a = b'\x00\x23\x42\x43'
        self.peer_b = b'\x00\x23\x42\x44'

    def tearDown(self):
        reset_peer_rx_filters()

    def test_overlay_publishes_per_peer_ua(self):
        remember_peer_dynamic_tg(
            'SYSTEM-1', self.peer_a, 2, 91, expires_at=None)
        remember_peer_dynamic_tg(
            'SYSTEM-1', self.peer_b, 2, 2350, expires_at=10000)
        systems = {
            'SYSTEM-1': {
                'PEERS': {
                    self.peer_a: {'OPTIONS': 'TS1=;TS2=;'},
                    self.peer_b: {'OPTIONS': 'TS2=23516;'},
                },
            },
        }
        overlay_peer_ua_for_report(systems, now=9000)
        self.assertEqual(
            systems['SYSTEM-1']['PEERS'][self.peer_a]['UA'],
            {2: {'TGID': 91, 'EXPIRES': None}})
        self.assertEqual(
            systems['SYSTEM-1']['PEERS'][self.peer_b]['UA'],
            {2: {'TGID': 2350, 'EXPIRES': 10000}})

    def test_overlay_always_sets_empty_ua(self):
        systems = {'SYSTEM-1': {'PEERS': {self.peer_a: {}}}}
        overlay_peer_ua_for_report(systems, now=1)
        self.assertEqual(systems['SYSTEM-1']['PEERS'][self.peer_a]['UA'], {})

    def test_send_config_overlays_ua(self):
        with open('hblink.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('overlay_peer_ua_for_report(self._config[\'SYSTEMS\'])',
                      source)


if __name__ == '__main__':
    unittest.main()
