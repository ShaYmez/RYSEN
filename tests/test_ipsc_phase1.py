#!/usr/bin/env python3
import unittest

from ipsc_const import (
    peer_id_from_packet, MASTER_REG_REQ, GROUP_VOICE, is_routing_master,
)
from ipsc_voice import IpscVoiceTranslator


class TestIpscConst(unittest.TestCase):

    def test_peer_id_from_reg_packet(self):
        pkt = bytes([MASTER_REG_REQ, 0x00, 0x2F, 0xB0, 0x40]) + b'\x00' * 10
        self.assertEqual(peer_id_from_packet(pkt), b'\x00\x2f\xb0\x40')

    def test_routing_master_modes(self):
        self.assertTrue(is_routing_master('MASTER'))
        self.assertTrue(is_routing_master('IPSC'))
        self.assertFalse(is_routing_master('PEER'))


class TestIpscVoice(unittest.TestCase):

    def test_translator_reset(self):
        tr = IpscVoiceTranslator()
        tr._out_stream_id[1] = b'\x01\x02\x03\x04'
        tr.reset()
        self.assertIsNone(tr._out_stream_id[1])

    def test_short_group_voice_returns_none(self):
        tr = IpscVoiceTranslator()
        pkt = bytes([GROUP_VOICE]) + b'\x00' * 20
        self.assertIsNone(tr.translate(pkt, 1, 0x0A))


if __name__ == '__main__':
    unittest.main()
