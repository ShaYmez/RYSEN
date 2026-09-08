#!/usr/bin/env python3
"""Tests for selfcare DISC=1 immediate disconnect helpers."""
import copy
import unittest

from bridge_helpers import (
    deactivate_linked_ipsc_bridge_legs,
    deactivate_peer_dynamic_bridges,
    peer_dynamic_groups,
    selfcare_disconnect_requested,
    strip_disc_from_options,
)
from dmr_utils3.utils import bytes_3, int_id
from selfcare_db import find_hotspot_master_peer
from tests.test_bridge_isolation import _sample_bridge, _sample_config


class TestSelfcareDisconnectOptions(unittest.TestCase):

    def test_disc_flag_detected(self):
        self.assertTrue(selfcare_disconnect_requested('TS2=2350;DISC=1;'))
        self.assertTrue(selfcare_disconnect_requested('DISC=1;'))
        self.assertFalse(selfcare_disconnect_requested('TS2=2350;'))
        self.assertFalse(selfcare_disconnect_requested('DISC=0;'))

    def test_strip_disc_leaves_other_options(self):
        self.assertEqual(strip_disc_from_options('TS2=2350;DISC=1;'), 'TS2=2350;')
        self.assertEqual(strip_disc_from_options('DISC=1;'), '')


class TestDeactivateLinkedIpscBridgeLegs(unittest.TestCase):

    def test_clears_linked_ipsc_on_active_ua_bridge(self):
        bridges = copy.deepcopy(_sample_bridge())
        bridges['2350'][2]['ACTIVE'] = True   # SYSTEM-5 hotspot leg
        bridges['2350'][0]['ACTIVE'] = True     # linked IPSC-198 leg
        changed = deactivate_linked_ipsc_bridge_legs(
            bridges, _sample_config(), 'SYSTEM-5')
        self.assertTrue(changed)
        self.assertFalse(bridges['2350'][0]['ACTIVE'])
        self.assertTrue(bridges['2350'][2]['ACTIVE'])

    def test_skips_unlinked_ipsc(self):
        bridges = copy.deepcopy(_sample_bridge())
        bridges['2350'][2]['ACTIVE'] = True
        bridges['2350'][1]['ACTIVE'] = True     # IPSC-113 not linked to SYSTEM-5
        changed = deactivate_linked_ipsc_bridge_legs(
            bridges, _sample_config(), 'SYSTEM-5')
        self.assertFalse(changed)
        self.assertTrue(bridges['2350'][1]['ACTIVE'])


class TestFindHotspotMasterPeer(unittest.TestCase):

    def test_finds_connected_hotspot_peer(self):
        radio_id = 235287
        peer_id = bytes_3(radio_id)
        cfg = {
            'MASTER-1': {
                'MODE': 'MASTER',
                'ENABLED': True,
                'PEERS': {
                    peer_id: {
                        'RADIO_ID': peer_id,
                        'CONNECTION': 'YES',
                    },
                },
            },
        }
        system, found_peer = find_hotspot_master_peer(cfg, radio_id)
        self.assertEqual(system, 'MASTER-1')
        self.assertEqual(found_peer, peer_id)

    def test_skips_offline_peer(self):
        cfg = {
            'MASTER-1': {
                'MODE': 'MASTER',
                'ENABLED': True,
                'PEERS': {
                    b'\x00\x23\x45\x01': {
                        'RADIO_ID': b'\x00\x23\x45\x01',
                        'CONNECTION': 'NO',
                    },
                },
            },
        }
        self.assertEqual(find_hotspot_master_peer(cfg, 234554801), (None, None))

    def test_finds_peer_by_peer_id_when_radio_id_missing(self):
        radio_id = 235287
        peer_id = (235287).to_bytes(4, 'big')
        cfg = {
            'MASTER-1': {
                'MODE': 'MASTER',
                'ENABLED': True,
                'PEERS': {
                    peer_id: {
                        'CONNECTION': 'YES',
                    },
                },
            },
        }
        system, found_peer = find_hotspot_master_peer(cfg, radio_id)
        self.assertEqual(system, 'MASTER-1')
        self.assertEqual(found_peer, peer_id)


class TestHotspotProxyDiscSkip(unittest.TestCase):

    def test_send_opts_skips_disc_rows(self):
        with open('hotspot_proxy_v2_sc.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("if 'DISC=1' in options:", source)
        self.assertIn('Disc request sent for:', source)
        self.assertNotIn("if 'DISC=1' in options:\n                    continue", source)


class TestDeactivatePeerDynamicBridges(unittest.TestCase):

    def test_drops_only_this_peer_ua(self):
        peer_a = b'\x00\x23\xc5\x93'
        peer_b = b'\x00\x23\xc5\x94'
        tg91 = bytes_3(91)
        tg2350 = bytes_3(2350)
        bridges = {
            '91': [{'SYSTEM': 'MASTER-1', 'TS': 2, 'TGID': tg91, 'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMER': 0}],
            '2350': [{'SYSTEM': 'MASTER-1', 'TS': 2, 'TGID': tg2350, 'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMER': 0}],
        }
        sub_map = {
            b'\x00\x23\xc5\x01': ('MASTER-1', 2, tg91, 1, peer_a),
            b'\x00\x23\xc5\x02': ('MASTER-1', 2, tg2350, 1, peer_b),
        }
        changed, dropped = deactivate_peer_dynamic_bridges(bridges, sub_map, 'MASTER-1', peer_a)
        self.assertTrue(changed)
        self.assertIn('91', dropped)
        self.assertFalse(bridges['91'][0]['ACTIVE'])
        self.assertTrue(bridges['2350'][0]['ACTIVE'])

    def test_keeps_ua_if_other_peer_still_on_tg(self):
        peer_a = b'\x00\x23\xc5\x93'
        peer_b = b'\x00\x23\xc5\x94'
        tg91 = bytes_3(91)
        bridges = {
            '91': [{'SYSTEM': 'MASTER-1', 'TS': 2, 'TGID': tg91, 'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMER': 0}],
        }
        sub_map = {
            b'\x00\x23\xc5\x01': ('MASTER-1', 2, tg91, 1, peer_a),
            b'\x00\x23\xc5\x02': ('MASTER-1', 2, tg91, 1, peer_b),
        }
        changed, dropped = deactivate_peer_dynamic_bridges(bridges, sub_map, 'MASTER-1', peer_a)
        self.assertFalse(changed)
        self.assertTrue(bridges['91'][0]['ACTIVE'])
        self.assertNotIn('91', dropped)

    def test_drops_owned_dial_only(self):
        peer_a = b'\x00\x23\xc5\x93'
        peer_b = b'\x00\x23\xc5\x94'
        bridges = {
            '#2351': [{
                'SYSTEM': 'MASTER-1', 'TS': 2, 'TGID': bytes_3(9),
                'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMER': 0, 'LINKER_PEER': peer_a,
            }],
            '#91': [{
                'SYSTEM': 'MASTER-1', 'TS': 2, 'TGID': bytes_3(9),
                'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMER': 0, 'LINKER_PEER': peer_b,
            }],
        }
        changed, dropped = deactivate_peer_dynamic_bridges(bridges, {}, 'MASTER-1', peer_a)
        self.assertTrue(changed)
        self.assertFalse(bridges['#2351'][0]['ACTIVE'])
        self.assertTrue(bridges['#91'][0]['ACTIVE'])
        self.assertEqual(dropped, {'#2351'})

    def test_does_not_drop_static_leg(self):
        peer_a = b'\x00\x23\xc5\x93'
        tg91 = bytes_3(91)
        bridges = {
            '91': [{'SYSTEM': 'MASTER-1', 'TS': 2, 'TGID': tg91, 'ACTIVE': True, 'TO_TYPE': 'OFF', 'TIMER': 0}],
        }
        sub_map = {b'\x00\x23\xc5\x01': ('MASTER-1', 2, tg91, 1, peer_a)}
        changed, dropped = deactivate_peer_dynamic_bridges(bridges, sub_map, 'MASTER-1', peer_a)
        self.assertFalse(changed)
        self.assertTrue(bridges['91'][0]['ACTIVE'])

    def test_peer_dynamic_groups_are_this_radio_only(self):
        peer_a = b'\x00\x23\xc5\x93'
        peer_b = b'\x00\x23\xc5\x94'
        groups = peer_dynamic_groups(
            {
                b'\x00\x23\xc5\x01': ('MASTER-1', 2, bytes_3(91), 1, peer_a),
                b'\x00\x23\xc5\x02': ('MASTER-1', 2, bytes_3(2350), 1, peer_b),
            },
            {},
            'MASTER-1',
            peer_a,
        )
        self.assertEqual(groups, [{'slot': 2, 'group': 91}])


if __name__ == '__main__':
    unittest.main()
