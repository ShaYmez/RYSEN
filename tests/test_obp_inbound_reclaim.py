#!/usr/bin/env python3
"""Tests for OBP outbound-to-inbound stream reclaim (LoopControl hardening)."""
import unittest

from bridge_helpers import reclaim_obp_inbound_stream


class TestReclaimObpInboundStream(unittest.TestCase):

    def test_reclaims_outbound_only_stream(self):
        sid = b'\x01\x02\x03\x04'
        status = {
            sid: {
                '_outbound': True,
                'START': 0,
                'TGID': b'\x00\x09\x26',
                'packets': 12,
                'LOOPLOG': True,
                'H_LC': b'lc',
            },
        }
        self.assertTrue(reclaim_obp_inbound_stream(
            status, sid, 100.0, b'\xaa\xbb\xcc', b'\x00\x09\x26', b'\x11\x22\x33'))
        st = status[sid]
        self.assertNotIn('_outbound', st)
        self.assertIn('1ST', st)
        self.assertEqual(st['packets'], 0)
        self.assertEqual(st['START'], 100.0)
        self.assertNotIn('LOOPLOG', st)
        self.assertNotIn('H_LC', st)

    def test_noop_when_not_outbound(self):
        sid = b'\x01\x02\x03\x04'
        status = {sid: {'1ST': 1.0, 'packets': 3, 'TGID': b'\x00\x09\x26'}}
        self.assertFalse(reclaim_obp_inbound_stream(
            status, sid, 100.0, b'\xaa\xbb\xcc', b'\x00\x09\x26', b'\x11\x22\x33'))
        self.assertEqual(status[sid]['packets'], 3)

    def test_noop_when_stream_missing(self):
        self.assertFalse(reclaim_obp_inbound_stream(
            {}, b'\x01\x02\x03\x04', 100.0, b'\xaa', b'\xbb', b'\xcc'))


class TestObpInboundReclaimHelper(unittest.TestCase):

    def test_helper_still_available(self):
        from bridge_helpers import reclaim_obp_inbound_stream
        self.assertTrue(callable(reclaim_obp_inbound_stream))


if __name__ == '__main__':
    unittest.main()
