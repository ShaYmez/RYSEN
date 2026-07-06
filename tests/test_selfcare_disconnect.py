#!/usr/bin/env python3
"""Tests for selfcare DISC=1 immediate disconnect helpers."""
import copy
import unittest

from bridge_helpers import (
    deactivate_linked_ipsc_bridge_legs,
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
        self.assertIn('continue', source)


if __name__ == '__main__':
    unittest.main()
