#!/usr/bin/env python3
import unittest

from ipsc_peer_meta import (
    callsign_bytes,
    describe_peer_mode,
    format_protocol_version,
    ipsc_peer_display_fields,
    lookup_peer_alias,
    parse_ipsc_peer_status,
)
from hblink import build_peer_record


PEER_ID = (235287).to_bytes(4, 'big')
HOST = '92.40.63.118'
PORT = 56002


class TestIpscPeerMeta(unittest.TestCase):

    def test_parse_master_reg_req(self):
        # mode 0x6a, flags, protocol 04.02.04.01 style
        pkt = bytes([0x90]) + PEER_ID + bytes([
            0x6a, 0x00, 0x00, 0x00, 0x05, 0x04, 0x02, 0x04, 0x01,
        ])
        status = parse_ipsc_peer_status(pkt)
        self.assertEqual(status['mode'], 0x6A)
        self.assertEqual(status['protocol'], b'\x04\x02\x04\x01')

    def test_lookup_peer_alias(self):
        cfg = {'_PEER_IDS': {235287: 'GB7NR'}}
        self.assertEqual(lookup_peer_alias(cfg, PEER_ID), 'GB7NR')
        self.assertIsNone(lookup_peer_alias(cfg, b'\x00\x00\x00\x01'))

    def test_display_fields_from_registration(self):
        fields = ipsc_peer_display_fields(
            0x6a, b'\x00\x00\x00\x05', b'\x04\x02\x04\x01')
        self.assertIn(b'Motorola', fields['SOFTWARE_ID'])
        self.assertIn(b'IPSC', fields['PACKAGE_ID'])
        self.assertIn(b'Digital', fields['DESCRIPTION'])

    def test_build_peer_record_uses_alias(self):
        cfg = {'_PEER_IDS': {235287: 'GB7NR'}}
        status = {'mode': 0x6a, 'flags': b'\x00\x00\x00\x05',
                  'protocol': b'\x04\x02\x04\x01'}
        rec = build_peer_record(
            PEER_ID, HOST, PORT, protocol='IPSC', peer_mode=b'\x6a',
            full_config=cfg, ipsc_status=status)
        self.assertEqual(rec['CALLSIGN'].decode().rstrip(), 'GB7NR')
        self.assertEqual(rec['RADIO_ID'], '235287')
        self.assertTrue(rec['SOFTWARE_ID'])
        self.assertTrue(rec['PACKAGE_ID'])


if __name__ == '__main__':
    unittest.main()
