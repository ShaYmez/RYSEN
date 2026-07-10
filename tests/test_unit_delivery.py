#!/usr/bin/env python3
"""Tests for bridge_unit_delivery resolver and delivery helpers."""
import sys
import unittest
from unittest.mock import MagicMock

sys.modules.setdefault('setproctitle', MagicMock())

from dmr_utils3.utils import bytes_3  # noqa: E402

import bridge_unit_delivery as bud  # noqa: E402


class TestUnitDeliveryResolver(unittest.TestCase):

    def setUp(self):
        self.config = {
            'SYSTEMS': {
                'SYS-A': {'MODE': 'MASTER', 'ENABLED': True, 'PEERS': {}},
                'SYS-B': {'MODE': 'MASTER', 'ENABLED': True, 'PEERS': {}},
                'IPSC-1': {'MODE': 'IPSC', 'ENABLED': True, 'PEERS': {}},
            },
        }
        self.systems = {'SYS-A': object(), 'SYS-B': object(), 'IPSC-1': object()}
        self.sub_map = {}

    def test_sub_map_with_peer_id(self):
        dst = bytes_3(2348831)
        peer = (234883100).to_bytes(4, 'big')
        self.sub_map[dst] = ('SYS-B', 2, None, 0, peer)
        dest = bud.resolve_unit_destination_local(
            dst, config=self.config, sub_map=self.sub_map, systems=self.systems,
            source_system='SYS-A', source_mode='MASTER')
        self.assertEqual(dest.system, 'SYS-B')
        self.assertEqual(dest.slot, 2)
        self.assertEqual(dest.peer_id, peer)
        self.assertEqual(dest.source, 'sub_map')

    def test_peer_prefix_uses_slot_two(self):
        dst = bytes_3(2348831)
        peer = bytes_3(2348831)
        self.config['SYSTEMS']['SYS-B']['PEERS'] = {peer: {}}
        dest = bud.resolve_unit_destination_local(
            dst, config=self.config, sub_map=self.sub_map, systems=self.systems,
            source_system='SYS-A', source_mode='MASTER')
        self.assertEqual(dest.slot, bud.HOTSPOT_DEFAULT_SLOT)
        self.assertEqual(dest.source, 'peer_prefix')

    def test_same_peer_on_same_system_returns_none(self):
        dst = bytes_3(2348831)
        peer = bytes_3(2348831)
        self.config['SYSTEMS']['SYS-A']['PEERS'] = {peer: {}}
        dest = bud.resolve_unit_destination_local(
            dst, config=self.config, sub_map=self.sub_map, systems=self.systems,
            source_system='SYS-A', source_mode='MASTER', source_peer_id=peer)
        self.assertIsNone(dest)

    def test_same_system_different_peer_allowed(self):
        dst = bytes_3(2348831)
        caller = bytes_3(235287)
        callee = bytes_3(2348831)
        self.config['SYSTEMS']['SYS-A']['PEERS'] = {caller: {}, callee: {}}
        dest = bud.resolve_unit_destination_local(
            dst, config=self.config, sub_map=self.sub_map, systems=self.systems,
            source_system='SYS-A', source_mode='MASTER', source_peer_id=caller)
        self.assertEqual(dest.system, 'SYS-A')
        self.assertEqual(dest.peer_id, callee)

    def test_disabled_system_skipped(self):
        dst = bytes_3(2348831)
        peer = bytes_3(2348831)
        self.config['SYSTEMS']['SYS-B']['PEERS'] = {peer: {}}
        self.config['SYSTEMS']['SYS-B']['ENABLED'] = False
        dest = bud.resolve_unit_destination_local(
            dst, config=self.config, sub_map=self.sub_map, systems=self.systems,
            source_system='SYS-A', source_mode='MASTER')
        self.assertIsNone(dest)

    def test_seed_sub_map_for_peer(self):
        peer = (234883100).to_bytes(4, 'big')
        bud.seed_sub_map_for_peer(self.sub_map, 'SYS-A', peer)
        self.assertIn(bytes_3(2348831), self.sub_map)
        entry = self.sub_map[bytes_3(2348831)]
        self.assertEqual(entry[0], 'SYS-A')
        self.assertEqual(entry[1], 2)
        self.assertEqual(entry[4], peer)


if __name__ == '__main__':
    unittest.main()
