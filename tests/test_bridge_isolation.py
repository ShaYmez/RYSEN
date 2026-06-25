#!/usr/bin/env python3
import copy
import unittest

from bridge_helpers import (
    activate_linked_bridge_legs,
    linked_ipsc_slots,
    parse_ipsc_link_from_options,
)


def _sample_config():
    return {
        'SYSTEM-5': {
            'MODE': 'MASTER',
            'OPTIONS': 'TS1=;TS2=2350;IPSC=IPSC-198',
            'PEERS': {
                b'\x00\x23\x45\x01': {
                    'OPTIONS': 'LINK_IPSC=IPSC-199',
                },
            },
        },
        'SYSTEM-6': {'MODE': 'MASTER', 'OPTIONS': 'TS1=;TS2='},
        'IPSC-198': {'MODE': 'IPSC'},
        'IPSC-199': {'MODE': 'IPSC'},
        'IPSC-113': {'MODE': 'IPSC'},
    }


def _sample_bridge():
    return {
        '2350': [
            {'SYSTEM': 'IPSC-198', 'TS': 2, 'TGID': b'\x00\x09\x2e', 'ACTIVE': False,
             'TO_TYPE': 'ON', 'TIMER': 0},
            {'SYSTEM': 'IPSC-113', 'TS': 2, 'TGID': b'\x00\x09\x2e', 'ACTIVE': False,
             'TO_TYPE': 'ON', 'TIMER': 0},
            {'SYSTEM': 'SYSTEM-5', 'TS': 2, 'TGID': b'\x00\x09\x2e', 'ACTIVE': False,
             'TO_TYPE': 'ON', 'TIMER': 0},
            {'SYSTEM': 'SYSTEM-6', 'TS': 2, 'TGID': b'\x00\x09\x2e', 'ACTIVE': False,
             'TO_TYPE': 'ON', 'TIMER': 0},
        ],
    }


class TestBridgeIsolation(unittest.TestCase):

    def test_parse_ipsc_link_from_options(self):
        self.assertEqual(parse_ipsc_link_from_options('TS2=2350;IPSC=IPSC-198'), 'IPSC-198')
        self.assertEqual(parse_ipsc_link_from_options('LINK_IPSC=IPSC-57'), 'IPSC-57')
        self.assertIsNone(parse_ipsc_link_from_options('TS2=2350'))

    def test_linked_ipsc_slots_from_system_options(self):
        cfg = _sample_config()
        self.assertEqual(linked_ipsc_slots(cfg, 'SYSTEM-5'), ('IPSC-198',))

    def test_linked_ipsc_slots_from_peer_options(self):
        cfg = _sample_config()
        peer_id = b'\x00\x23\x45\x01'
        self.assertEqual(linked_ipsc_slots(cfg, 'SYSTEM-5', peer_id), ('IPSC-198', 'IPSC-199'))

    def test_ipsc_source_has_no_linked_activation_targets(self):
        cfg = _sample_config()
        self.assertEqual(linked_ipsc_slots(cfg, 'IPSC-198'), ())

    def test_ipsc_key_does_not_wake_peer_legs(self):
        bridges = copy.deepcopy(_sample_bridge())
        bridges['2350'][0]['ACTIVE'] = True  # source IPSC-198
        activated = activate_linked_bridge_legs(
            bridges, _sample_config(), '2350', 'IPSC-198', 2, 600, now=1000)
        self.assertEqual(activated, [])
        self.assertFalse(bridges['2350'][1]['ACTIVE'])  # IPSC-113 stays cold

    def test_hotspot_without_link_only_activates_self(self):
        bridges = copy.deepcopy(_sample_bridge())
        activated = activate_linked_bridge_legs(
            bridges, _sample_config(), '2350', 'SYSTEM-6', 2, 600, now=1000)
        self.assertEqual(activated, [])
        self.assertFalse(bridges['2350'][0]['ACTIVE'])
        self.assertFalse(bridges['2350'][1]['ACTIVE'])

    def test_hotspot_with_link_wakes_only_linked_ipsc(self):
        bridges = copy.deepcopy(_sample_bridge())
        activated = activate_linked_bridge_legs(
            bridges, _sample_config(), '2350', 'SYSTEM-5', 2, 600, now=1000)
        self.assertEqual(activated, ['IPSC-198'])
        self.assertTrue(bridges['2350'][0]['ACTIVE'])
        self.assertFalse(bridges['2350'][1]['ACTIVE'])  # other repeater untouched
        self.assertEqual(bridges['2350'][0]['TIMER'], 1600)


if __name__ == '__main__':
    unittest.main()
