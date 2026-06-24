#!/usr/bin/env python3
import unittest

from bridge_helpers import iter_routing_master_systems


class TestIpscBridgeParity(unittest.TestCase):

    def test_iter_routing_master_systems_includes_ipsc(self):
        systems = {
            'SYSTEM-0': {'MODE': 'MASTER'},
            'IPSC-57': {'MODE': 'IPSC'},
            'OBP-1': {'MODE': 'OPENBRIDGE'},
            'ECHO': {'MODE': 'PEER'},
        }
        names = list(iter_routing_master_systems(systems))
        self.assertEqual(names, ['SYSTEM-0', 'IPSC-57'])


if __name__ == '__main__':
    unittest.main()
