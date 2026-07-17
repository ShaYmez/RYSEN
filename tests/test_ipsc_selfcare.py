#!/usr/bin/env python3
import unittest
from unittest.mock import MagicMock

from twisted.internet.defer import succeed

from selfcare_db import (
    IPSC_CLIENT_MODE,
    build_ipsc_seed_options,
    find_ipsc_peer_for_radio_id,
)
from ipsc_master import IpscMasterMixin
from ipsc_const import MASTER_REG_REQ
from config import acl_build
import const


PEER_ID = (235287).to_bytes(4, 'big')
HOST = '92.40.63.118'
PORT = 56002


class TestSelfcareHelpers(unittest.TestCase):

    def test_ipsc_client_mode_is_zero(self):
        self.assertEqual(IPSC_CLIENT_MODE, 0)

    def test_build_ipsc_seed_options(self):
        cfg = {'TS1_STATIC': '9,10', 'TS2_STATIC': '2350'}
        self.assertEqual(build_ipsc_seed_options(cfg), 'TS1=9,10;TS2=2350;')

    def test_build_ipsc_seed_options_empty(self):
        self.assertIsNone(build_ipsc_seed_options({'TS1_STATIC': '', 'TS2_STATIC': ''}))

    def test_find_ipsc_slot_for_radio_id(self):
        systems = {
            'IPSC-0': {
                'MODE': 'IPSC',
                'ENABLED': True,
                'PEERS': {
                    PEER_ID: {'RADIO_ID': '235287', 'CONNECTION': 'YES'},
                },
            },
            'IPSC-1': {
                'MODE': 'IPSC',
                'ENABLED': True,
                'PEERS': {},
            },
        }
        slot, _ = find_ipsc_peer_for_radio_id(systems, 235287)
        self.assertEqual(slot, 'IPSC-0')
        slot, _ = find_ipsc_peer_for_radio_id(systems, 999999)
        self.assertIsNone(slot)

    def test_find_ipsc_slot_matches_peer_id_when_radio_id_missing(self):
        systems = {
            'IPSC-0': {
                'MODE': 'IPSC',
                'ENABLED': True,
                'PEERS': {
                    PEER_ID: {'CONNECTION': 'YES'},
                },
            },
        }
        slot, _ = find_ipsc_peer_for_radio_id(systems, 235287)
        self.assertEqual(slot, 'IPSC-0')

    def test_find_ipsc_peer_for_radio_id(self):
        systems = {
            'IPSC-0': {
                'MODE': 'IPSC',
                'ENABLED': True,
                'PEERS': {
                    PEER_ID: {'RADIO_ID': '235287', 'CONNECTION': 'YES'},
                },
            },
        }
        from selfcare_db import find_ipsc_peer_for_radio_id
        slot, peer_id = find_ipsc_peer_for_radio_id(systems, 235287)
        self.assertEqual(slot, 'IPSC-0')
        self.assertEqual(peer_id, PEER_ID)

    def test_find_ipsc_slot_skips_disabled(self):
        systems = {
            'IPSC-0': {
                'MODE': 'IPSC',
                'ENABLED': False,
                'PEERS': {PEER_ID: {'RADIO_ID': '235287'}},
            },
        }
        slot, _ = find_ipsc_peer_for_radio_id(systems, 235287)
        self.assertIsNone(slot)


class _StubIpscSelfcare(IpscMasterMixin):

    def __init__(self, selfcare_enabled=False):
        self._system = 'IPSC-0'
        self._peers = {}
        self._ipsc_peers = {}
        db = MagicMock()
        db.upsert_ipsc_client.return_value = succeed(None)
        db.mark_ipsc_options_pending.return_value = succeed(None)
        self._CONFIG = {
            'SELF SERVICE': {'ENABLED': selfcare_enabled},
            '_SELF_SERVICE_DB': db,
            '_PEER_IDS': {235287: 'GB7NR'},
            'GLOBAL': {'REG_ACL': acl_build('PERMIT:ALL', const.PEER_MAX)},
        }
        self._config = {
            'USE_ACL': False,
            'REG_ACL': acl_build('PERMIT:ALL', const.PEER_MAX),
            'MAX_PEERS': 1,
            'IPSC_MASTER_ID': 9999999,
            'AUTH_ENABLED': False,
            'TS1_STATIC': '9',
            'TS2_STATIC': '2350',
        }
        self._report = MagicMock()
        self._max_peers = 1
        self._keepalive_watchdog = 60
        self._master_id = b'\x00\x00\x00\x00'
        self._ts_flags = b'\x00\x00\x00\x00\x00'
        self._ipsc_version = b'\x04\x02\x04\x01'
        self._alive_reply = b'\x97'
        self._dereg_reply = b'\x9b'
        self._auth_enabled = False
        self._auth_key = b''
        self.sent = []
        self._voice = MagicMock()

    def _ipsc_send(self, packet, host, port):
        self.sent.append((packet, host, port))


class TestIpscSelfcareHooks(unittest.TestCase):

    def _reg_packet(self):
        return bytes([MASTER_REG_REQ]) + PEER_ID + bytes([0x6a]) + b'\x00\x00\x00\x05' + b'\x04\x02\x04\x01'

    def test_register_calls_upsert_when_enabled(self):
        stub = _StubIpscSelfcare(selfcare_enabled=True)
        stub._on_reg_req(self._reg_packet(), HOST, PORT)
        db = stub._CONFIG['_SELF_SERVICE_DB']
        db.upsert_ipsc_client.assert_called_once()
        args = db.upsert_ipsc_client.call_args[0]
        self.assertEqual(args[0], 235287)
        self.assertEqual(args[1], PEER_ID)
        self.assertEqual(args[2], 'GB7NR')
        self.assertEqual(args[3], HOST)
        self.assertEqual(args[4], 'TS1=9;TS2=2350;')

    def test_register_skips_upsert_when_disabled(self):
        stub = _StubIpscSelfcare(selfcare_enabled=False)
        stub._on_reg_req(self._reg_packet(), HOST, PORT)
        stub._CONFIG['_SELF_SERVICE_DB'].upsert_ipsc_client.assert_not_called()

    def test_deregister_calls_logout_when_enabled(self):
        stub = _StubIpscSelfcare(selfcare_enabled=True)
        stub._on_reg_req(self._reg_packet(), HOST, PORT)
        stub._on_de_reg_req(bytes([0x9a]) + PEER_ID, HOST, PORT)
        db = stub._CONFIG['_SELF_SERVICE_DB']
        db.logout_ipsc_client.assert_called_once_with(235287)

    def test_reregister_passes_no_seed(self):
        stub = _StubIpscSelfcare(selfcare_enabled=True)
        pkt = self._reg_packet()
        stub._on_reg_req(pkt, HOST, PORT)
        stub._on_reg_req(pkt, HOST, PORT)
        second_call = stub._CONFIG['_SELF_SERVICE_DB'].upsert_ipsc_client.call_args_list[1]
        self.assertIsNone(second_call[0][4])

    def test_register_marks_options_pending_on_new_session(self):
        stub = _StubIpscSelfcare(selfcare_enabled=True)
        stub._on_reg_req(self._reg_packet(), HOST, PORT)
        db = stub._CONFIG['_SELF_SERVICE_DB']
        db.mark_ipsc_options_pending.assert_called_once_with(235287)

    def test_reregister_while_connected_does_not_remark_options(self):
        stub = _StubIpscSelfcare(selfcare_enabled=True)
        pkt = self._reg_packet()
        stub._on_reg_req(pkt, HOST, PORT)
        stub._on_reg_req(pkt, HOST, PORT)
        db = stub._CONFIG['_SELF_SERVICE_DB']
        db.mark_ipsc_options_pending.assert_called_once_with(235287)

    def test_reconnect_after_deregister_remarks_options(self):
        stub = _StubIpscSelfcare(selfcare_enabled=True)
        pkt = self._reg_packet()
        stub._on_reg_req(pkt, HOST, PORT)
        stub._on_de_reg_req(bytes([0x9a]) + PEER_ID, HOST, PORT)
        stub._on_reg_req(pkt, HOST, PORT)
        db = stub._CONFIG['_SELF_SERVICE_DB']
        self.assertEqual(db.mark_ipsc_options_pending.call_count, 2)
        db.mark_ipsc_options_pending.assert_any_call(235287)

    def test_mark_ipsc_options_pending_sql(self):
        with open('selfcare_db.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('def mark_ipsc_options_pending', source)
        self.assertIn('SET modified = 1', source)
        self.assertIn("AND options NOT LIKE '%%DISC=1%%'", source)
        self.assertIn('def save_client_options', source)


class TestOptionsConfigIpscMode(unittest.TestCase):

    def test_ipsc_mode_allowed_in_options_config(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("if _mode not in ('MASTER', 'IPSC'):", source)
        self.assertIn("if _mode == 'MASTER' and 'PEERS' in CONFIG['SYSTEMS'][_system]:", source)


if __name__ == '__main__':
    unittest.main()
