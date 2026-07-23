#!/usr/bin/env python3
"""Skip OBP TX when peer already has inbound STATUS for the stream."""
import unittest

from bridge_helpers import (
    HBP_RATE_DROP_ENABLED,
    DMRE_MAX_PACKET_AGE_S,
    obp_target_already_has_inbound,
    group_call_end_bridge_candidates,
)


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


class TestGroupCallEndCandidates(unittest.TestCase):

    def test_numeric_and_hash(self):
        bridges = {'235': [], '#235': [], '999': [], '#9': []}
        c = group_call_end_bridge_candidates(bridges, 235)
        self.assertEqual(c, ['235', '#235'])

    def test_dial_a_tg_all_hash(self):
        bridges = {'235': [], '#235': [], '#91': [], '91': []}
        c = group_call_end_bridge_candidates(bridges, 9)
        self.assertEqual(c, ['#235', '#91'])


class TestAudioSafetyFlags(unittest.TestCase):

    def test_hbp_rate_drop_enabled_freedmr(self):
        self.assertTrue(HBP_RATE_DROP_ENABLED)

    def test_dmre_age_freedmr_adn(self):
        self.assertEqual(DMRE_MAX_PACKET_AGE_S, 5.0)

    def test_bridge_master_gates_hbp(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if HBP_RATE_DROP_ENABLED:', source)
        self.assertIn('obp_target_already_has_inbound', source)
        self.assertIn('group_call_end_bridge_candidates', source)

    def test_hblink_bcsq_on_age_freedmr(self):
        with open('hblink.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('DMRE_MAX_PACKET_AGE_S', source)
        idx = source.find('more than %.0fs old!, discarding')
        self.assertGreater(idx, 0)
        window = source[idx:idx + 250]
        self.assertIn('self.send_bcsq', window)
        self.assertNotIn('Do NOT send_bcsq on age alone', source)


if __name__ == '__main__':
    unittest.main()
