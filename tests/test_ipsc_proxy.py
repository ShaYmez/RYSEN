#!/usr/bin/env python3
import unittest

from ipsc_const import PRCL, PRIN, MASTER_REG_REQ, MASTER_REG_REPLY, peer_id_from_packet


class TestIpscProxyConstants(unittest.TestCase):

    def test_proxy_control_tokens(self):
        self.assertEqual(PRIN, b'PRIN')
        self.assertEqual(PRCL, b'PRCL')
        self.assertNotEqual(PRCL[0], MASTER_REG_REQ)

    def test_peer_id_from_reg_req(self):
        peer_id = b'\x00\x39\x46\x07'
        pkt = bytes([MASTER_REG_REQ]) + peer_id + b'\x00' * 6
        self.assertEqual(peer_id_from_packet(pkt), peer_id)

    def test_reg_reply_has_no_repeater_peer_id(self):
        pkt = bytes([MASTER_REG_REPLY, 0x00, 0x98, 0x96, 0x7f]) + b'\x00' * 12
        self.assertEqual(peer_id_from_packet(pkt), b'\x00\x98\x96\x7f')
        self.assertNotEqual(peer_id_from_packet(pkt), b'\x00\x39\x46\x07')


if __name__ == '__main__':
    unittest.main()
