#!/usr/bin/env python3
"""Topology-aware unit hub validation and health reporting."""

import unittest
from unittest.mock import MagicMock, patch

import unit_hub


class TestUnitHubTopology(unittest.TestCase):
    def test_default_nonparticipant_starts_no_reporter(self):
        state = unit_hub.start_health_reporter(
            {'ALIASES': {'UNIT_SUB_MAP_URL': '',
                         'UNIT_SUB_MAP_TOKEN_FILE': ''}},
            MagicMock(), reactor=MagicMock())
        self.assertIsNone(state)

    def test_reporter_starts_immediately_then_uses_bounded_jitter(self):
        class _Call:
            def active(self):
                return True

            def cancel(self):
                pass

        class _Reactor:
            def __init__(self):
                self.delays = []

            def callLater(self, delay, callback):
                self.delays.append(delay)
                if delay == 0:
                    callback()
                return _Call()

        reactor = _Reactor()
        config = {'ALIASES': {
            'UNIT_SUB_MAP_URL': 'https://api.example/v2/internal/sub-map',
            'UNIT_SUB_MAP_TOKEN_FILE': '/run/secret',
        }}
        with patch.object(
                unit_hub, 'schedule_off_reactor',
                side_effect=lambda _fn, _args, callback, _errback:
                callback({'ok': True})):
            state = unit_hub.start_health_reporter(
                config, MagicMock(), reactor=reactor,
                random_fn=lambda low, high: 60.0)
        self.assertIsNotNone(state)
        self.assertEqual(reactor.delays, [0, 60.0])

    def test_contract_urls_keep_base_and_add_current_master(self):
        config = {'ALIASES': {
            'UNIT_SUB_MAP_URL': 'https://api.example/v2/internal/sub-map/',
        }}
        self.assertEqual(
            unit_hub.unit_lookup_url(config, 2345875, 2381),
            'https://api.example/v2/internal/sub-map/2345875?from_net_id=2381')
        self.assertEqual(
            unit_hub.topology_health_url(config),
            'https://api.example/v2/internal/topology/report')

    def test_health_snapshot_is_live_fleet_only_and_secret_free(self):
        config = {
            'GLOBAL': {'SERVER_ID': (2381).to_bytes(4, 'big')},
            'ALIASES': {'UNIT_SUB_MAP_TOKEN_FILE': '/secret/token'},
            '_SERVER_IDS': {
                '2040': 'Europe',
                '2381': 'UK',
                '3180': 'USA',
            },
            'SYSTEMS': {
                'OBP-EU': {
                    'MODE': 'OPENBRIDGE', 'ENABLED': True,
                    'ENHANCED_OBP': True, 'VER': 5,
                    'NETWORK_ID': (2040).to_bytes(4, 'big'),
                    '_bcka': 980.0, 'PASSPHRASE': b'do-not-leak',
                },
                'OBP-USA-DOWN': {
                    'MODE': 'OPENBRIDGE', 'ENABLED': True,
                    'ENHANCED_OBP': True, 'VER': 5,
                    'NETWORK_ID': (3180).to_bytes(4, 'big'),
                    '_bcka': 900.0,
                },
                'OBP-XPEER': {
                    'MODE': 'OPENBRIDGE', 'ENABLED': True,
                    'ENHANCED_OBP': True, 'VER': 5,
                    'NETWORK_ID': (9999).to_bytes(4, 'big'),
                    '_bcka': 999.0,
                },
            },
        }
        snapshot = unit_hub.enhanced_obp_health_snapshot(config, now=1000.0)
        self.assertEqual(snapshot['opb_net_id'], 2381)
        self.assertEqual(
            [(row['opb_net_id'], row['healthy']) for row in snapshot['peers']],
            [(2040, True), (3180, False)])
        self.assertNotIn('secret', repr(snapshot).lower())
        self.assertNotIn('PASSPHRASE', repr(snapshot))

    def test_registry_identity_mismatch_cannot_join_topology(self):
        config = {
            'GLOBAL': {'SERVER_ID': (2355).to_bytes(4, 'big')},
            '_SERVER_IDS': {'2354': 'Scotland'},
            'SYSTEMS': {},
        }
        snapshot = unit_hub.enhanced_obp_health_snapshot(
            config, now=1000.0)
        self.assertIsNone(snapshot['opb_net_id'])

    def test_strict_path_selects_only_immediate_configured_hop(self):
        route = unit_hub.validate_topology_route({
            'current_master': 2020,
            'home_net_id': 3180,
            'next_hop_net_id': 2040,
            'path': [2020, 2040, 3180],
            'topology_version': 'v7',
            'route_expires_at': 1100,
        }, 2020, {2020, 2040, 3180}, {2040}, now=1000)
        self.assertEqual(route['home'], 3180)
        self.assertEqual(route['next_hop'], 2040)
        self.assertEqual(route['path'], (2020, 2040, 3180))
        self.assertEqual(route['topology_version'], 'v7')

    def test_path_rejects_ingress_loops_expiry_and_third_parties(self):
        base = {
            'current_master': 2020,
            'home_net_id': 3180,
            'next_hop_net_id': 2040,
            'path': [2020, 2040, 3180],
            'route_expires_at': 1100,
        }
        self.assertIsNone(unit_hub.validate_topology_route(
            base, 2020, {2020, 2040, 3180}, {2040},
            ingress_master=2040, now=1000))
        self.assertIsNone(unit_hub.validate_topology_route(
            dict(base, path=[2020, 2040, 2020], current_master=2020),
            2020, {2020, 2040}, {2040}, now=1000))
        self.assertIsNone(unit_hub.validate_topology_route(
            dict(base, route_expires_at=999), 2020, {2020, 2040, 3180}, {2040},
            now=1000))
        self.assertIsNone(unit_hub.validate_topology_route({
            'current_master': 2020, 'home_net_id': 3180,
            'next_hop_net_id': 9999,
            'path': [2020, 9999, 3180],
        }, 2020, {2020, 2040, 3180}, {2040}, now=1000))

    def test_legacy_home_response_remains_directly_compatible(self):
        route = unit_hub.validate_topology_route(
            {'opb_net_id': 2040}, 2381, {2381, 2040}, {2040}, now=1000)
        self.assertEqual(route['path'], (2381, 2040))
        self.assertEqual(route['next_hop'], 2040)


if __name__ == '__main__':
    unittest.main()
