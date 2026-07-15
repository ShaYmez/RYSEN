#!/usr/bin/env python3
"""Tests for selfcare hardening: DISC persist, ping-timeout OPTIONS reset, poll apply."""
import time as time_mod
import unittest
from unittest.mock import MagicMock

from hblink import HBSYSTEM


PEER_ID = (235287).to_bytes(4, 'big')


class TestHblinkPingTimeout(unittest.TestCase):

    def _make_master_stub(self):
        stub = HBSYSTEM.__new__(HBSYSTEM)
        stub._system = 'MASTER-0'
        stub._CONFIG = {
            'GLOBAL': {'PING_TIME': 30, 'MAX_MISSED': 3},
            'SYSTEMS': {
                'MASTER-0': {
                    'MODE': 'MASTER',
                    'PEERS': {
                        PEER_ID: {
                            'LAST_PING': 0,
                            'CALLSIGN': b'TEST',
                            'RADIO_ID': PEER_ID,
                            'SOCKADDR': ('127.0.0.1', 62030),
                        },
                    },
                    'OPTIONS': 'TS2=2350;',
                    '_default_options': 'TS1=9;',
                },
            },
        }
        stub._config = stub._CONFIG['SYSTEMS']['MASTER-0']
        stub._peers = stub._CONFIG['SYSTEMS']['MASTER-0']['PEERS']
        stub.transport = MagicMock()
        stub.master_maintenance_loop = HBSYSTEM.master_maintenance_loop.__get__(stub, HBSYSTEM)
        return stub

    def test_timeout_resets_options_when_last_peer_removed(self):
        stub = self._make_master_stub()
        stub.master_maintenance_loop()
        self.assertNotIn(PEER_ID, stub._peers)
        sys_cfg = stub._CONFIG['SYSTEMS']['MASTER-0']
        self.assertEqual(sys_cfg['OPTIONS'], 'TS1=9;')
        self.assertTrue(sys_cfg.get('_reset'))

    def test_timeout_keeps_options_while_peers_remain(self):
        stub = self._make_master_stub()
        other = (235288).to_bytes(4, 'big')
        stub._peers[other] = {
            'LAST_PING': time_mod.time(),
            'CALLSIGN': b'TEST2',
            'RADIO_ID': other,
            'SOCKADDR': ('127.0.0.1', 62031),
        }
        stub.master_maintenance_loop()
        self.assertNotIn(PEER_ID, stub._peers)
        self.assertIn(other, stub._peers)
        self.assertEqual(stub._CONFIG['SYSTEMS']['MASTER-0']['OPTIONS'], 'TS2=2350;')
        self.assertNotIn('_reset', stub._CONFIG['SYSTEMS']['MASTER-0'])


class TestSelfcareDbHardening(unittest.TestCase):

    def test_mark_ipsc_options_pending_excludes_disc(self):
        with open('selfcare_db.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('def save_client_options', source)
        self.assertIn("AND options NOT LIKE '%DISC=1%'", source)

    def test_strip_disc_for_db_persist(self):
        from bridge_helpers import strip_disc_from_options
        self.assertEqual(strip_disc_from_options('TS2=2350;DISC=1;'), 'TS2=2350;')
        self.assertEqual(strip_disc_from_options('DISC=1;'), '')


class TestIpscSelfcarePoll(unittest.TestCase):

    def test_disc_apply_saves_stripped_options(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('yield _selfcare_db.save_client_options(int_id_val, remaining)', source)
        self.assertIn("CONFIG['SYSTEMS'][slot]['OPTIONS'] = remaining", source)
        self.assertIn("CONFIG['SYSTEMS'][system]['OPTIONS'] = remaining", source)


class TestHotspotProxyHardening(unittest.TestCase):

    def test_login_opt_uses_self_db_proxy(self):
        with open('hotspot_proxy_v2_sc.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('yield self.db_proxy.slct_opt(_peer_id)', source)
        self.assertIn('yield self.db_proxy.slct_db()', source)
        self.assertIn(".get('opt_timer')", source)


class TestRouterHbpTimeoutCleanup(unittest.TestCase):

    def test_router_hbp_overrides_maintenance_loop(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('def master_maintenance_loop(self):', source)
        self.assertIn('HBSYSTEM.master_maintenance_loop(self)', source)
        self.assertIn('clear_sub_map_for_peer(_peer_id)', source)


if __name__ == '__main__':
    unittest.main()
