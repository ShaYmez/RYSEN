#!/usr/bin/env python3
import unittest
from unittest.mock import MagicMock
from time import time

from hblink import build_peer_record
from ipsc_master import IpscMasterMixin
from ipsc_const import MASTER_REG_REQ, MASTER_ALIVE_REQ, DE_REG_REQ
from config import acl_build
import const


PEER_ID = (235287).to_bytes(4, 'big')  # GB7NR
HOST = '92.40.63.118'
PORT = 56002


class TestBuildPeerRecord(unittest.TestCase):

    def test_ipsc_shape_matches_hbp_keys(self):
        rec = build_peer_record(PEER_ID, HOST, PORT, protocol='IPSC', peer_mode=b'\x6a')
        self.assertEqual(rec['CONNECTION'], 'YES')
        self.assertEqual(rec['IP'], HOST)
        self.assertEqual(rec['PORT'], PORT)
        self.assertEqual(rec['SOCKADDR'], (HOST, PORT))
        self.assertEqual(rec['RADIO_ID'], '235287')
        self.assertIsInstance(rec['RADIO_ID'], str)
        self.assertEqual(rec['PROTOCOL'], 'IPSC')
        self.assertEqual(rec['IPSC_MODE'], 0x6A)
        self.assertIn('RX_FREQ', rec)
        self.assertIn('LAST_PING', rec)

    def test_re_registration_preserves_connected_time(self):
        first = build_peer_record(PEER_ID, HOST, PORT, protocol='IPSC', now=100.0)
        second = build_peer_record(
            PEER_ID, HOST, PORT, protocol='IPSC', existing=first, now=200.0,
        )
        self.assertEqual(second['CONNECTED'], 100.0)
        self.assertEqual(second['LAST_PING'], 200.0)


class _StubIpsc(IpscMasterMixin):
    """Minimal object for mixin peer/report tests (no Twisted transport)."""

    def __init__(self):
        self._system = 'IPSC-0'
        self._peers = {}
        self._ipsc_peers = {}
        self._CONFIG = {
            '_PEER_IDS': {235287: 'GB7NR'},
            'GLOBAL': {'REG_ACL': acl_build('PERMIT:ALL', const.PEER_MAX)},
        }
        self._config = {
            'USE_ACL': False,
            'REG_ACL': acl_build('PERMIT:ALL', const.PEER_MAX),
            'MAX_PEERS': 1,
            'IPSC_MASTER_ID': 235287,
            'AUTH_ENABLED': False,
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


class TestIpscPeerReporting(unittest.TestCase):

    def _reg_packet(self):
        return (bytes([MASTER_REG_REQ]) + PEER_ID + bytes([0x6a])
                + b'\x00\x00\x00\x05' + b'\x04\x02\x04\x01')

    def test_registration_populates_config_peers(self):
        stub = _StubIpsc()
        stub._on_reg_req(self._reg_packet(), HOST, PORT)
        self.assertIn(PEER_ID, stub._peers)
        self.assertEqual(stub._peers[PEER_ID]['IP'], HOST)
        self.assertEqual(stub._peers[PEER_ID]['RADIO_ID'], '235287')
        self.assertEqual(stub._peers[PEER_ID]['CALLSIGN'].decode().rstrip(), 'GB7NR')
        self.assertEqual(stub._peers[PEER_ID]['PROTOCOL'], 'IPSC')
        self.assertTrue(stub._peers[PEER_ID]['SOFTWARE_ID'])
        stub._report.send_config.assert_called_once()

    def test_re_registration_sends_config(self):
        stub = _StubIpsc()
        stub._on_reg_req(self._reg_packet(), HOST, PORT)
        stub._report.send_config.reset_mock()
        stub._on_reg_req(self._reg_packet(), HOST, PORT)
        stub._report.send_config.assert_called_once()

    def test_dereg_sends_config(self):
        stub = _StubIpsc()
        stub._on_reg_req(self._reg_packet(), HOST, PORT)
        stub._report.send_config.reset_mock()
        stub._on_de_reg_req(bytes([DE_REG_REQ]) + PEER_ID, HOST, PORT)
        self.assertNotIn(PEER_ID, stub._peers)
        stub._report.send_config.assert_called_once()

    def test_timeout_removal_sends_config(self):
        stub = _StubIpsc()
        stub._on_reg_req(self._reg_packet(), HOST, PORT)
        stub._report.send_config.reset_mock()
        stub._remove_ipsc_peer(PEER_ID)
        stub._report.send_config.assert_called_once()

    def test_alive_updates_last_ping_without_config_push(self):
        stub = _StubIpsc()
        stub._on_reg_req(self._reg_packet(), HOST, PORT)
        before = stub._peers[PEER_ID]['LAST_PING']
        stub._report.send_config.reset_mock()
        stub._on_alive_req(bytes([MASTER_ALIVE_REQ]) + PEER_ID, HOST, PORT)
        self.assertGreater(stub._peers[PEER_ID]['LAST_PING'], before)
        self.assertEqual(stub._peers[PEER_ID]['PINGS_RECEIVED'], 1)
        stub._report.send_config.assert_not_called()


if __name__ == '__main__':
    unittest.main()
