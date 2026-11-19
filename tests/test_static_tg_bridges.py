#!/usr/bin/env python3
"""Tests for multi-static TG bridge legs (make_static_tg)."""
import unittest
from types import SimpleNamespace
from unittest import mock

import bridge_master as bm
from bridge_helpers import (
    dest_peer_rx_blocked,
    peer_remembered_dynamic_tgs,
    remember_peer_dynamic_tg,
    reset_peer_rx_filters,
)
from dmr_utils3.utils import bytes_3


class TestMakeStaticTg(unittest.TestCase):

    def test_make_static_tg_appends_missing_leg(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if not matched:', source)
        self.assertIn('Added static TG', source)

    def test_obp_activates_bridge_before_routing(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn(
            'activate_ua_bridge_source(str(_int_dst), self._system, _slot, peer_id=_peer_id)',
            source)
        self.assertIn("CONFIG['SYSTEMS'][system].get('DEFAULT_UA_TIMER')", source)

    def test_options_strips_whitespace_in_static_tg(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('_options[\'TS1_STATIC\'] = re.sub(r"\\s", "", str(_options[\'TS1_STATIC\']))',
                      source)

    def test_vterm_skips_always_on_static_legs(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn(
            'if static_tg_survives_single_mode_vterm(_bridge, _system):',
            source)
        static = {
            'TO_TYPE': 'OFF', 'ACTIVE': True, 'TGID': bytes_3(2350),
        }
        self.assertTrue(bm.static_tg_survives_single_mode_vterm('2350', static))
        self.assertFalse(
            bm.static_tg_survives_single_mode_vterm('#2350', static))
        ua = {'TO_TYPE': 'ON', 'ACTIVE': True, 'TGID': bytes_3(91)}
        self.assertFalse(bm.static_tg_survives_single_mode_vterm('91', ua))
        self.assertIn(
            'if sticky_ua_survives_single_mode_vterm(', source)

    def test_timer_change_updates_live_bridge_timeout(self):
        prev = getattr(bm, 'BRIDGES', None)
        bm.BRIDGES = {
            '91': [{
                'SYSTEM': 'SYSTEM-1', 'TS': 2, 'TGID': bytes_3(91),
                'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMEOUT': 600,
            }],
            '2350': [{
                'SYSTEM': 'SYSTEM-1', 'TS': 2, 'TGID': bytes_3(2350),
                'ACTIVE': True, 'TO_TYPE': 'OFF', 'TIMEOUT': 600,
            }],
            '9990': [{
                'SYSTEM': 'SYSTEM-1', 'TS': 1, 'TGID': bytes_3(9990),
                'ACTIVE': True, 'TO_TYPE': 'NONE', 'TIMEOUT': 15,
            }],
            '91-other': [{
                'SYSTEM': 'SYSTEM-2', 'TS': 2, 'TGID': bytes_3(91),
                'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMEOUT': 600,
            }],
        }
        try:
            self.assertEqual(bm.update_system_bridge_timeouts('SYSTEM-1', 5), 2)
            self.assertEqual(bm.BRIDGES['91'][0]['TIMEOUT'], 300)
            self.assertEqual(bm.BRIDGES['2350'][0]['TIMEOUT'], 300)
            self.assertEqual(bm.BRIDGES['9990'][0]['TIMEOUT'], 15)
            self.assertEqual(bm.BRIDGES['91-other'][0]['TIMEOUT'], 600)
        finally:
            bm.BRIDGES = {} if prev is None else prev


def _leg(bridge, system, slot):
    for entry in bm.BRIDGES.get(bridge, ()):
        if entry.get('SYSTEM') == system and entry.get('TS') == slot:
            return entry
    return None


class TestImmediateStaticSubscribe(unittest.TestCase):
    def setUp(self):
        # Production Z3DMR client observed replacing numbered RPTO statics on UK.
        self.peer = (234587587).to_bytes(4, 'big')
        self.other = (235211730).to_bytes(4, 'big')
        self.prev_config = getattr(bm, 'CONFIG', None)
        self.prev_bridges = getattr(bm, 'BRIDGES', None)
        self.prev_systems = getattr(bm, 'systems', None)
        self.had_systems = hasattr(bm, 'systems')
        self.prev_sub_map = getattr(bm, 'SUB_MAP', None)
        self.had_sub_map = hasattr(bm, 'SUB_MAP')
        bm.BRIDGES = {}
        bm.BRIDGE_IDX.clear()
        bm.SUB_MAP = {}
        reset_peer_rx_filters()
        self.slot = {
            'RX_TIME': 12345,
            'TX_TIME': 12345,
            'RX_TGID': bytes_3(23516),
            'TX_TGID': bytes_3(23516),
        }
        self.slot1 = dict(self.slot)
        bm.systems = {
            'SYSTEM-95': SimpleNamespace(
                STATUS={1: self.slot1, 2: self.slot})
        }
        bm.CONFIG = {
            'SYSTEMS': {
                'SYSTEM-95': {
                    'MODE': 'MASTER',
                    'STICKY_TG': False,
                    'DEFAULT_UA_TIMER': 10,
                    'TS1_STATIC': False,
                    'TS2_STATIC': False,
                    'PEERS': {
                        self.peer: {
                            'CONNECTION': 'YES',
                            'OPTIONS': '',
                        },
                    },
                },
            },
            'REPORTS': {'REPORT': False},
        }

    def tearDown(self):
        bm.CONFIG = self.prev_config
        if self.prev_bridges is None:
            bm.BRIDGES = {}
        else:
            bm.BRIDGES = self.prev_bridges
        bm.BRIDGE_IDX.clear()
        reset_peer_rx_filters()
        if self.had_systems:
            bm.systems = self.prev_systems
        else:
            del bm.systems
        if self.had_sub_map:
            bm.SUB_MAP = self.prev_sub_map
        elif hasattr(bm, 'SUB_MAP'):
            del bm.SUB_MAP

    def test_ts2_replacement_is_active_before_options_timer(self):
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=2350;')
        first = _leg('2350', 'SYSTEM-95', 2)
        self.assertIsNotNone(first)
        self.assertTrue(first['ACTIVE'])
        self.assertEqual(first['TO_TYPE'], 'OFF')
        self.assertEqual(self.slot['RX_TIME'], 12345)
        self.assertEqual(self.slot['TX_TIME'], 12345)
        self.assertEqual(
            set(self.slot['_STATIC_HANGTIME_BYPASS']), {bytes_3(2350)})

        self.slot['RX_TIME'] = 50
        self.slot['TX_TIME'] = 50
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=31320;')
        moved = _leg('31320', 'SYSTEM-95', 2)
        old = _leg('2350', 'SYSTEM-95', 2)
        self.assertTrue(moved['ACTIVE'])
        self.assertEqual(moved['TO_TYPE'], 'OFF')
        self.assertFalse(old['ACTIVE'])
        self.assertEqual(old['TO_TYPE'], 'ON')
        self.assertEqual(self.slot['RX_TIME'], 50)
        self.assertEqual(self.slot['TX_TIME'], 50)
        self.assertEqual(
            set(self.slot['_STATIC_HANGTIME_BYPASS']), {bytes_3(31320)})

    def test_z3dmr_rapid_replacements_are_atomic_and_indexed(self):
        """Every RPTO wins even when all replacements fit inside one 26s tick."""
        key_2350 = ('SYSTEM-95', 2, bytes_3(2350))
        key_23516 = ('SYSTEM-95', 2, bytes_3(23516))

        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=2350;')
        self.assertEqual(bm.BRIDGE_IDX.get(key_2350), {'2350'})
        self.assertTrue(_leg('2350', 'SYSTEM-95', 2)['ACTIVE'])

        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=23516;')
        old = _leg('2350', 'SYSTEM-95', 2)
        current = _leg('23516', 'SYSTEM-95', 2)
        self.assertFalse(old['ACTIVE'])
        self.assertEqual(old['TO_TYPE'], 'ON')
        self.assertTrue(current['ACTIVE'])
        self.assertEqual(current['TO_TYPE'], 'OFF')
        self.assertEqual(bm.BRIDGE_IDX.get(key_23516), {'23516'})

        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=2350;')
        current = _leg('2350', 'SYSTEM-95', 2)
        old = _leg('23516', 'SYSTEM-95', 2)
        self.assertTrue(current['ACTIVE'])
        self.assertEqual(current['TO_TYPE'], 'OFF')
        self.assertFalse(old['ACTIVE'])
        self.assertEqual(old['TO_TYPE'], 'ON')
        self.assertEqual(bm.BRIDGE_IDX.get(key_2350), {'2350'})
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, bytes_3(103), 2, 2350))

        for bridge in ('2350', '23516'):
            matches = [
                entry for entry in bm.BRIDGES[bridge]
                if entry['SYSTEM'] == 'SYSTEM-95' and entry['TS'] == 2
            ]
            self.assertEqual(len(matches), 1)

    def test_replacement_stops_old_audio_after_peer_keyed_old_static(self):
        """The reporting peer's stale UA/SUB_MAP row must not retain its old TG."""
        stream = bytes_3(101)
        radio = bytes_3(2345875)
        other_radio = bytes_3(2345876)
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=2350;')
        remember_peer_dynamic_tg(
            'SYSTEM-95', self.peer, 2, 2350, expires_at=bm.time() + 600)
        bm.SUB_MAP[radio] = [
            'SYSTEM-95', 2, bytes_3(2350), bm.time(), self.peer]
        bm.SUB_MAP[other_radio] = [
            'SYSTEM-95', 1, bytes_3(91), bm.time(), self.peer]

        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, stream, 2, 2350))
        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=23516;')

        old = _leg('2350', 'SYSTEM-95', 2)
        current = _leg('23516', 'SYSTEM-95', 2)
        self.assertFalse(old['ACTIVE'])
        self.assertEqual(old['TO_TYPE'], 'ON')
        self.assertTrue(current['ACTIVE'])
        self.assertNotIn(
            (2, 2350),
            peer_remembered_dynamic_tgs('SYSTEM-95', self.peer))
        self.assertNotIn(radio, bm.SUB_MAP)
        self.assertIn(other_radio, bm.SUB_MAP)
        self.assertTrue(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, stream, 2, 2350))
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, stream, 2, 23516))
        remember_peer_dynamic_tg(
            'SYSTEM-95', self.peer, 2, 2350, expires_at=bm.time() + 600)
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, stream, 2, 2350))

    def test_stale_sub_map_cannot_reactivate_old_static_for_third_peer(self):
        third = (235211731).to_bytes(4, 'big')
        radio = bytes_3(2345875)
        peers = bm.CONFIG['SYSTEMS']['SYSTEM-95']['PEERS']
        peers[self.other] = {
            'CONNECTION': 'YES', 'OPTIONS': 'TS2_1=2350;'}
        peers[third] = {'CONNECTION': 'YES', 'OPTIONS': ''}
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=2350;')
        bm.SUB_MAP[radio] = [
            'SYSTEM-95', 2, bytes_3(2350), bm.time(), self.peer]

        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=23516;')
        self.assertNotIn(radio, bm.SUB_MAP)
        self.assertTrue(_leg('2350', 'SYSTEM-95', 2)['ACTIVE'])

        bm.apply_selfcare_options(
            'SYSTEM-95', self.other, 'TS2_1=31320;')

        old = _leg('2350', 'SYSTEM-95', 2)
        self.assertFalse(old['ACTIVE'])
        self.assertEqual(old['TO_TYPE'], 'ON')
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-95', third, bytes_3(104), 2, 2350))

    def test_replacement_mutes_only_changed_peer_on_shared_old_static(self):
        """Another peer may retain 2350 without leaking it to the peer on 23516."""
        stream = bytes_3(102)
        peers = bm.CONFIG['SYSTEMS']['SYSTEM-95']['PEERS']
        peers[self.other] = {
            'CONNECTION': 'YES', 'OPTIONS': 'TS2_1=2350;'}
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=2350;')
        remember_peer_dynamic_tg(
            'SYSTEM-95', self.peer, 2, 2350, expires_at=bm.time() + 600)
        remember_peer_dynamic_tg(
            'SYSTEM-95', self.other, 2, 2350, expires_at=bm.time() + 600)

        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=23516;')

        retained = _leg('2350', 'SYSTEM-95', 2)
        self.assertTrue(retained['ACTIVE'])
        self.assertEqual(retained['TO_TYPE'], 'OFF')
        self.assertTrue(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, stream, 2, 2350))
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-95', self.other, stream, 2, 2350))
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, stream, 2, 23516))

    def test_ts1_numbered_replacement_has_same_immediate_semantics(self):
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS1_1=2350;')
        first = _leg('2350', 'SYSTEM-95', 1)
        self.assertTrue(first['ACTIVE'])
        self.assertEqual(first['TO_TYPE'], 'OFF')

        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS1_1=23516;')
        old = _leg('2350', 'SYSTEM-95', 1)
        current = _leg('23516', 'SYSTEM-95', 1)
        self.assertFalse(old['ACTIVE'])
        self.assertEqual(old['TO_TYPE'], 'ON')
        self.assertTrue(current['ACTIVE'])
        self.assertEqual(current['TO_TYPE'], 'OFF')
        self.assertEqual(
            bm.BRIDGE_IDX.get(('SYSTEM-95', 1, bytes_3(23516))),
            {'23516'})

    def test_hangtime_bypass_is_one_shot_and_keeps_stream_clock(self):
        before = (self.slot['RX_TIME'], self.slot['TX_TIME'])
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=2350;')

        self.assertTrue(
            bm._static_hangtime_bypass(self.slot, bytes_3(2350)))
        self.assertFalse(
            bm._static_hangtime_bypass(self.slot, bytes_3(23516)))
        self.assertEqual(
            (self.slot['RX_TIME'], self.slot['TX_TIME']), before)

        bm._consume_static_hangtime_bypass(self.slot, bytes_3(2350))
        self.assertFalse(
            bm._static_hangtime_bypass(self.slot, bytes_3(2350)))

    def test_hangtime_bypass_expires_and_never_preempts_active_call(self):
        now = bm.time()
        self.slot.update({
            # Voice burst sequence 2 numerically equals HBPF_SLT_VTERM.
            # Recency must still identify this as an active call.
            'RX_TYPE': bm.HBPF_SLT_VTERM,
            'TX_TYPE': bm.HBPF_SLT_VTERM,
            'RX_TIME': now,
            'TX_TIME': now - 10,
            '_STATIC_HANGTIME_BYPASS': {bytes_3(2350): now + 5},
        })
        self.assertFalse(
            bm._static_hangtime_bypass(
                self.slot, bytes_3(2350), now=now))

        self.slot['RX_TIME'] = now - bm.STREAM_TO - 0.01
        self.assertTrue(
            bm._static_hangtime_bypass(
                self.slot, bytes_3(2350), now=now))
        self.assertFalse(
            bm._static_hangtime_bypass(
                self.slot, bytes_3(2350), now=now + 6))
        self.assertNotIn('_STATIC_HANGTIME_BYPASS', self.slot)

    def test_hangtime_bypass_consumes_only_the_routed_static(self):
        deadline = bm.time() + 5
        self.slot['_STATIC_HANGTIME_BYPASS'] = {
            bytes_3(2350): deadline,
            bytes_3(23516): deadline,
        }

        bm._consume_static_hangtime_bypass(self.slot, bytes_3(2350))

        self.assertNotIn(
            bytes_3(2350), self.slot['_STATIC_HANGTIME_BYPASS'])
        self.assertIn(
            bytes_3(23516), self.slot['_STATIC_HANGTIME_BYPASS'])

    def test_single_peer_timer_is_used_immediately(self):
        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=2350;TIMER=60;')
        leg = _leg('2350', 'SYSTEM-95', 2)
        self.assertEqual(leg['TIMEOUT'], 3600)
        self.assertEqual(
            bm.CONFIG['SYSTEMS']['SYSTEM-95']['DEFAULT_UA_TIMER'], 60)

    def test_timer_only_updates_live_static_timeout(self):
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=2350;')
        leg = _leg('2350', 'SYSTEM-95', 2)
        self.assertEqual(leg['TIMEOUT'], 600)
        self.slot['RX_TIME'] = 77
        self.slot['TX_TIME'] = 77
        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=2350;RelinkTime=15;')
        self.assertEqual(leg['TIMEOUT'], 900)
        self.assertTrue(leg['ACTIVE'])
        self.assertEqual(leg['TO_TYPE'], 'OFF')
        self.assertEqual(
            bm.CONFIG['SYSTEMS']['SYSTEM-95']['DEFAULT_UA_TIMER'], 15)
        self.assertEqual(self.slot['RX_TIME'], 77)
        self.assertEqual(self.slot['TX_TIME'], 77)

    def test_removing_peer_timer_restores_stanza_timer_immediately(self):
        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=2350;TIMER=60;')
        self.assertEqual(
            bm.CONFIG['SYSTEMS']['SYSTEM-95']['DEFAULT_UA_TIMER'], 60)

        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=2350;')

        leg = _leg('2350', 'SYSTEM-95', 2)
        self.assertEqual(leg['TIMEOUT'], 600)
        self.assertEqual(
            bm.CONFIG['SYSTEMS']['SYSTEM-95']['DEFAULT_UA_TIMER'], 10)

    def test_shared_master_transition_restores_stanza_timer(self):
        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=2350;TIMER=60;')
        peers = bm.CONFIG['SYSTEMS']['SYSTEM-95']['PEERS']
        peers[self.other] = {
            'CONNECTION': 'YES',
            'OPTIONS': 'TS2_1=23516;TIMER=45;',
        }

        bm.publish_master_static_bridges('SYSTEM-95')

        self.assertEqual(
            bm.CONFIG['SYSTEMS']['SYSTEM-95']['DEFAULT_UA_TIMER'], 10)
        self.assertEqual(_leg('2350', 'SYSTEM-95', 2)['TIMEOUT'], 600)
        self.assertEqual(_leg('23516', 'SYSTEM-95', 2)['TIMEOUT'], 600)

    def test_shared_master_keeps_another_peers_static(self):
        peers = bm.CONFIG['SYSTEMS']['SYSTEM-95']['PEERS']
        peers[self.other] = {'CONNECTION': 'YES', 'OPTIONS': 'TS2_1=2350;'}
        self.slot['RX_TGID'] = bytes_3(2350)
        self.slot['TX_TGID'] = bytes_3(2350)
        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=31320;TIMER=60;')
        kept = _leg('2350', 'SYSTEM-95', 2)
        added = _leg('31320', 'SYSTEM-95', 2)
        self.assertTrue(kept['ACTIVE'])
        self.assertEqual(kept['TO_TYPE'], 'OFF')
        self.assertTrue(added['ACTIVE'])
        self.assertEqual(self.slot['RX_TIME'], 12345)
        self.assertNotIn('_STATIC_HANGTIME_BYPASS', self.slot)
        self.assertEqual(added['TIMEOUT'], 600)
        self.assertEqual(
            bm.CONFIG['SYSTEMS']['SYSTEM-95']['DEFAULT_UA_TIMER'], 10)

    def test_shared_master_drops_hangtime_for_a_removed_talkgroup(self):
        peers = bm.CONFIG['SYSTEMS']['SYSTEM-95']['PEERS']
        peers[self.peer]['OPTIONS'] = 'TS2_1=23426;'
        peers[self.other] = {'CONNECTION': 'YES', 'OPTIONS': 'TS2_1=2350;'}
        system = bm.CONFIG['SYSTEMS']['SYSTEM-95']
        system['_default_options'] = 'TS1_STATIC=;TS2_STATIC=;'
        system['TS2_STATIC'] = '2350,23426'
        bm.BRIDGES['23426'] = [{
            'SYSTEM': 'SYSTEM-95', 'TS': 2, 'TGID': bytes_3(23426),
            'ACTIVE': True, 'TIMEOUT': 600, 'TO_TYPE': 'OFF',
            'OFF': [], 'ON': [bytes_3(23426)], 'RESET': [], 'TIMER': 0,
        }]
        bm.BRIDGES['2350'] = [{
            'SYSTEM': 'SYSTEM-95', 'TS': 2, 'TGID': bytes_3(2350),
            'ACTIVE': True, 'TIMEOUT': 600, 'TO_TYPE': 'OFF',
            'OFF': [], 'ON': [bytes_3(2350)], 'RESET': [], 'TIMER': 0,
        }]
        self.slot['RX_TGID'] = bytes_3(23426)
        self.slot['TX_TGID'] = bytes_3(23426)
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=31320;')
        self.assertEqual(self.slot['RX_TIME'], 12345)
        self.assertEqual(self.slot['TX_TIME'], 12345)
        self.assertEqual(
            set(self.slot['_STATIC_HANGTIME_BYPASS']), {bytes_3(31320)})
        self.assertTrue(_leg('2350', 'SYSTEM-95', 2)['ACTIVE'])
        self.assertTrue(_leg('31320', 'SYSTEM-95', 2)['ACTIVE'])

    def test_disconnect_reconciles_departed_peers_unique_static(self):
        peers = bm.CONFIG['SYSTEMS']['SYSTEM-95']['PEERS']
        peers[self.other] = {
            'CONNECTION': 'YES', 'OPTIONS': 'TS2_1=23516;'}
        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=2350;')
        self.assertTrue(_leg('2350', 'SYSTEM-95', 2)['ACTIVE'])

        del peers[self.peer]
        bm.publish_master_static_bridges('SYSTEM-95')

        departed = _leg('2350', 'SYSTEM-95', 2)
        retained = _leg('23516', 'SYSTEM-95', 2)
        self.assertFalse(departed['ACTIVE'])
        self.assertEqual(departed['TO_TYPE'], 'ON')
        self.assertTrue(retained['ACTIVE'])
        self.assertEqual(retained['TO_TYPE'], 'OFF')

    def test_control_kick_reconciles_departed_static_immediately(self):
        peers = bm.CONFIG['SYSTEMS']['SYSTEM-95']['PEERS']
        peers[self.other] = {
            'CONNECTION': 'YES', 'OPTIONS': 'TS2_1=23516;'}
        bm.apply_selfcare_options(
            'SYSTEM-95', self.peer, 'TS2_1=2350;')
        bm.systems['SYSTEM-95'].send_peer = mock.Mock()

        with mock.patch.object(
                bm, '_block_peer_active_streams'), mock.patch.object(
                bm, 'selfcare_disconnect'), mock.patch.object(
                bm, 'forget_peer_rx_state'):
            self.assertTrue(
                bm.kick_hotspot_peer('SYSTEM-95', self.peer))

        self.assertNotIn(self.peer, peers)
        departed = _leg('2350', 'SYSTEM-95', 2)
        retained = _leg('23516', 'SYSTEM-95', 2)
        self.assertFalse(departed['ACTIVE'])
        self.assertEqual(departed['TO_TYPE'], 'ON')
        self.assertTrue(retained['ACTIVE'])
        self.assertEqual(retained['TO_TYPE'], 'OFF')

    def test_static_9990_indexes_parrot_return_leg(self):
        """Z3DMR group parrot subscribes before it keys, so the shell needs PARROT."""
        bm.CONFIG['SYSTEMS']['PARROT'] = {
            'MODE': 'PEER', 'ENABLED': True,
        }
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=9990;')

        parrot = _leg('9990', 'PARROT', 2)
        hotspot = _leg('9990', 'SYSTEM-95', 2)
        self.assertIsNotNone(parrot)
        self.assertTrue(parrot['ACTIVE'])
        self.assertEqual(parrot['TO_TYPE'], 'NONE')
        self.assertEqual(parrot['TGID'], bytes_3(9990))
        self.assertTrue(hotspot['ACTIVE'])
        self.assertEqual(hotspot['TO_TYPE'], 'OFF')
        self.assertIn('9990', bm.BRIDGE_IDX[('PARROT', 2, bytes_3(9990))])

        bm.BRIDGES['9990'] = [
            entry for entry in bm.BRIDGES['9990']
            if entry['SYSTEM'] != 'PARROT'
        ]
        bm._idx_replace_bridge('9990')
        bm.activate_ua_bridge_source('9990', 'SYSTEM-95', 2, peer_id=self.peer)
        self.assertIn('9990', bm.BRIDGE_IDX[('PARROT', 2, bytes_3(9990))])

    def test_switching_static_drops_talkgroup_the_peer_keyed(self):
        """A keyed TG is not the old static, and it still has to go quiet."""
        stream = bytes_3(234)
        radio = bytes_3(2345875)
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=9990;')
        bm.BRIDGES['23426'] = [{
            'SYSTEM': 'SYSTEM-95', 'TS': 2, 'TGID': bytes_3(23426),
            'ACTIVE': True, 'TIMEOUT': 600, 'TO_TYPE': 'ON',
            'OFF': [], 'ON': [bytes_3(23426)], 'RESET': [],
            'TIMER': bm.time() + 600,
        }]
        bm._idx_add_bridge('23426')
        remember_peer_dynamic_tg(
            'SYSTEM-95', self.peer, 2, 23426, expires_at=bm.time() + 600)
        bm.SUB_MAP[radio] = [
            'SYSTEM-95', 2, bytes_3(23426), bm.time(), self.peer]

        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=235;')

        keyed = _leg('23426', 'SYSTEM-95', 2)
        current = _leg('235', 'SYSTEM-95', 2)
        self.assertFalse(keyed['ACTIVE'])
        self.assertTrue(current['ACTIVE'])
        self.assertEqual(current['TO_TYPE'], 'OFF')
        self.assertNotIn(radio, bm.SUB_MAP)
        self.assertNotIn(
            (2, 23426), peer_remembered_dynamic_tgs('SYSTEM-95', self.peer))
        self.assertTrue(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, stream, 2, 23426))
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, stream, 2, 235))

    def test_repeat_of_same_static_keeps_keyed_talkgroup(self):
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=235;')
        remember_peer_dynamic_tg(
            'SYSTEM-95', self.peer, 2, 23426, expires_at=bm.time() + 600)
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=235;')
        self.assertIn(
            (2, 23426), peer_remembered_dynamic_tgs('SYSTEM-95', self.peer))
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, bytes_3(235), 2, 23426))

    def test_other_peer_keeps_keyed_talkgroup_when_one_hotspot_switches(self):
        stream = bytes_3(236)
        peers = bm.CONFIG['SYSTEMS']['SYSTEM-95']['PEERS']
        peers[self.other] = {'CONNECTION': 'YES', 'OPTIONS': ''}
        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=9990;')
        bm.BRIDGES['23426'] = [{
            'SYSTEM': 'SYSTEM-95', 'TS': 2, 'TGID': bytes_3(23426),
            'ACTIVE': True, 'TIMEOUT': 600, 'TO_TYPE': 'ON',
            'OFF': [], 'ON': [bytes_3(23426)], 'RESET': [],
            'TIMER': bm.time() + 600,
        }]
        bm._idx_add_bridge('23426')
        remember_peer_dynamic_tg(
            'SYSTEM-95', self.peer, 2, 23426, expires_at=bm.time() + 600)
        remember_peer_dynamic_tg(
            'SYSTEM-95', self.other, 2, 23426, expires_at=bm.time() + 600)

        bm.apply_selfcare_options('SYSTEM-95', self.peer, 'TS2_1=235;')

        self.assertTrue(_leg('23426', 'SYSTEM-95', 2)['ACTIVE'])
        self.assertTrue(dest_peer_rx_blocked(
            'SYSTEM-95', self.peer, stream, 2, 23426))
        self.assertFalse(dest_peer_rx_blocked(
            'SYSTEM-95', self.other, stream, 2, 23426))


if __name__ == '__main__':
    unittest.main()
