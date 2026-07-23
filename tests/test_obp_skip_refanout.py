#!/usr/bin/env python3
"""Skip OBP TX when peer already has inbound STATUS for the stream."""
import unittest

from bridge_helpers import obp_target_already_has_inbound


class TestObpTargetAlreadyHasInbound(unittest.TestCase):

    def test_empty_status(self):
        self.assertFalse(obp_target_already_has_inbound({}, b'\x01\x02\x03\x04', b'\x00\x00\xeb'))

    def test_outbound_only_does_not_skip(self):
        sid = b'\x01\x02\x03\x04'
        dst = b'\x00\x00\xeb'
        status = {sid: {'1ST': True, 'TGID': dst, '_outbound': True}}
        self.assertFalse(obp_target_already_has_inbound(status, sid, dst))

    def test_inbound_same_tg_skips(self):
        sid = b'\x01\x02\x03\x04'
        dst = b'\x00\x00\xeb'
        status = {sid: {'1ST': True, 'TGID': dst}}
        self.assertTrue(obp_target_already_has_inbound(status, sid, dst))

    def test_inbound_wrong_tg_no_skip(self):
        sid = b'\x01\x02\x03\x04'
        status = {sid: {'1ST': True, 'TGID': b'\x00\x00\x01'}}
        self.assertFalse(obp_target_already_has_inbound(status, sid, b'\x00\x00\xeb'))

    def test_missing_1st_no_skip(self):
        sid = b'\x01\x02\x03\x04'
        dst = b'\x00\x00\xeb'
        status = {sid: {'TGID': dst}}
        self.assertFalse(obp_target_already_has_inbound(status, sid, dst))


class TestSkipRefanoutWired(unittest.TestCase):

    def test_bridge_master_calls_helper(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('obp_target_already_has_inbound', source)
        self.assertGreaterEqual(source.count('obp_target_already_has_inbound('), 2)


if __name__ == '__main__':
    unittest.main()
