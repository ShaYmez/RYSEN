#!/usr/bin/env python3
import unittest

from ipsc_const import (
    GROUP_VOICE, PRIVATE_VOICE, VOICE_HEAD, VOICE_TERM, SLOT1_VOICE, SLOT2_VOICE,
    TS_CALL_MSK, HBPF_UNIT_CALL, HBPF_TGID_TS2,
    GV_BURST_TYPE_OFF, GV_HEAD_LEN, GV_VOICE_LEN,
)
from ipsc_voice import IpscVoiceTranslator


class TestIpscPrivateVoice(unittest.TestCase):

    MASTER_ID = 9999999

    def _make_voice_packet(self, opcode, ts=1, burst_type=VOICE_HEAD):
        peer = b'\x00\x03\x96\x77'
        src = b'\x23\x45\x73'
        dst = b'\x00\x09\x2e'  # destination subscriber / TG
        pkt = bytearray([opcode]) + bytearray(30)
        pkt[1:5] = peer
        pkt[5] = 0x42
        pkt[6:9] = src
        pkt[9:12] = dst
        if ts == 2:
            pkt[17] = TS_CALL_MSK
        pkt[GV_BURST_TYPE_OFF] = burst_type
        return bytes(pkt), peer, src, dst

    def test_inbound_private_sets_unit_flag_ts1(self):
        inbound, peer, src, dst = self._make_voice_packet(PRIVATE_VOICE, ts=1)
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        dmrd = tr.translate(inbound, 1, VOICE_HEAD, private_call=True)
        self.assertIsNotNone(dmrd)
        self.assertEqual(len(dmrd), 55)
        self.assertTrue(dmrd[15] & HBPF_UNIT_CALL)
        self.assertFalse(dmrd[15] & HBPF_TGID_TS2)

    def test_inbound_private_sets_unit_flag_ts2(self):
        inbound, peer, src, dst = self._make_voice_packet(PRIVATE_VOICE, ts=2)
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        dmrd = tr.translate(inbound, 2, VOICE_HEAD, private_call=True)
        self.assertIsNotNone(dmrd)
        self.assertTrue(dmrd[15] & HBPF_UNIT_CALL)
        self.assertTrue(dmrd[15] & HBPF_TGID_TS2)

    def test_inbound_group_no_unit_flag(self):
        inbound, peer, src, dst = self._make_voice_packet(GROUP_VOICE, ts=2)
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        dmrd = tr.translate(inbound, 2, VOICE_HEAD, private_call=False)
        self.assertIsNotNone(dmrd)
        self.assertFalse(dmrd[15] & HBPF_UNIT_CALL)

    def test_encode_private_head_ts1(self):
        inbound, peer, src, dst = self._make_voice_packet(PRIVATE_VOICE, ts=1)
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        dmrd = tr.translate(inbound, 1, VOICE_HEAD, private_call=True)
        outbound = tr.encode(dmrd)
        self.assertIsNotNone(outbound)
        self.assertEqual(outbound[0], PRIVATE_VOICE)
        self.assertEqual(len(outbound), GV_HEAD_LEN)
        self.assertEqual(outbound[GV_BURST_TYPE_OFF], VOICE_HEAD)
        self.assertEqual(outbound[17], 0x00)  # TS1 call_info

    def test_encode_private_head_ts2(self):
        inbound, peer, src, dst = self._make_voice_packet(PRIVATE_VOICE, ts=2)
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        dmrd = tr.translate(inbound, 2, VOICE_HEAD, private_call=True)
        outbound = tr.encode(dmrd)
        self.assertIsNotNone(outbound)
        self.assertEqual(outbound[0], PRIVATE_VOICE)
        self.assertEqual(outbound[17], TS_CALL_MSK)

    def test_encode_private_voice_slot_ts1(self):
        inbound, peer, src, dst = self._make_voice_packet(PRIVATE_VOICE, ts=1)
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        tr.translate(inbound, 1, VOICE_HEAD, private_call=True)

        slot_pkt = bytearray([PRIVATE_VOICE]) + bytearray(51)
        slot_pkt[1:5] = peer
        slot_pkt[5] = 0x42
        slot_pkt[6:9] = src
        slot_pkt[9:12] = dst
        slot_pkt[GV_BURST_TYPE_OFF] = SLOT1_VOICE
        slot_pkt[32] = 0x16
        dmrd = tr.translate(bytes(slot_pkt), 1, SLOT1_VOICE, private_call=True)
        self.assertIsNotNone(dmrd)

        outbound = tr.encode(dmrd)
        self.assertIsNotNone(outbound)
        self.assertEqual(outbound[0], PRIVATE_VOICE)
        self.assertEqual(len(outbound), GV_VOICE_LEN)
        self.assertEqual(outbound[GV_BURST_TYPE_OFF], SLOT1_VOICE)

    def test_encode_private_voice_slot_ts2(self):
        inbound, peer, src, dst = self._make_voice_packet(PRIVATE_VOICE, ts=2)
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        tr.translate(inbound, 2, VOICE_HEAD, private_call=True)

        slot_pkt = bytearray([PRIVATE_VOICE]) + bytearray(51)
        slot_pkt[1:5] = peer
        slot_pkt[5] = 0x42
        slot_pkt[6:9] = src
        slot_pkt[9:12] = dst
        slot_pkt[17] = TS_CALL_MSK
        slot_pkt[GV_BURST_TYPE_OFF] = SLOT2_VOICE
        slot_pkt[32] = 0x16
        dmrd = tr.translate(bytes(slot_pkt), 2, SLOT2_VOICE, private_call=True)
        outbound = tr.encode(dmrd)
        self.assertIsNotNone(outbound)
        self.assertEqual(outbound[0], PRIVATE_VOICE)
        self.assertEqual(outbound[GV_BURST_TYPE_OFF], SLOT2_VOICE)

    def test_group_encode_still_uses_0x80(self):
        inbound, peer, src, dst = self._make_voice_packet(GROUP_VOICE, ts=2)
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        dmrd = tr.translate(inbound, 2, VOICE_HEAD, private_call=False)
        outbound = tr.encode(dmrd)
        self.assertEqual(outbound[0], GROUP_VOICE)

    def test_learn_private_peer_header(self):
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        sample = bytes.fromhex(
            '81' + '00039717' + '14' + '23cb93' + '00092e'
            + '0100002e39' + '20'
            + '80ddc618226cc7f700000000'
            + '01'
        )
        tr.learn_peer_header(sample, private_call=True)
        self.assertEqual(tr._peer_private_call_type, b'\x01')


if __name__ == '__main__':
    unittest.main()
