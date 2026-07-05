#!/usr/bin/env python3
"""Tests for selfcare DISC=1 immediate disconnect helpers."""
import copy
import unittest

from bridge_helpers import (
    deactivate_linked_ipsc_bridge_legs,
    selfcare_disconnect_requested,
    strip_disc_from_options,
)
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


if __name__ == '__main__':
    unittest.main()
