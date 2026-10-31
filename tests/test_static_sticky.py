#!/usr/bin/env python3
"""Sticky TG vs statics, and per-peer UA expiry/mute."""

import unittest

import bridge_master as bm
from bridge_helpers import (
    dest_peer_rx_blocked,
    remember_peer_dynamic_tg,
    reset_peer_rx_filters,
    sticky_tg_enabled,
)
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


if __name__ == '__main__':
    unittest.main()
