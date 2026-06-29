#!/usr/bin/env python3
import unittest

from dmr_utils3.utils import bytes_3
from mk_voice import pkt_gen
from voice_lib import words

from ipsc_const import (
    GROUP_VOICE, PRIVATE_VOICE, VOICE_HEAD, VOICE_TERM, SLOT2_VOICE, TS_CALL_MSK,
    HBPF_UNIT_CALL,
    GV_BURST_TYPE_OFF, GV_HEAD_LEN, GV_VOICE_LEN,
)
from ipsc_voice import IpscVoiceTranslator


class TestIpscOutbound(unittest.TestCase):

    MASTER_ID = 9999999

    def _make_head_packet(self):
        peer = b'\x00\x03\x96\x77'
        src = b'\x23\x45\x73'
        dst = b'\x00\x09\x2e'
        pkt = bytearray([GROUP_VOICE]) + bytearray(30)
        pkt[1:5] = peer
        pkt[5] = 0x42
        pkt[6:9] = src
        pkt[9:12] = dst
        pkt[17] = TS_CALL_MSK
        pkt[GV_BURST_TYPE_OFF] = VOICE_HEAD
        return bytes(pkt), peer, src, dst

    def test_encode_head_produces_54_byte_packet(self):
        inbound, peer, src, dst = self._make_head_packet()
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        dmrd = tr.translate(inbound, 2, VOICE_HEAD)
        self.assertIsNotNone(dmrd)
        self.assertEqual(len(dmrd), 55)

        outbound = tr.encode(dmrd)
        self.assertIsNotNone(outbound)
        self.assertEqual(len(outbound), GV_HEAD_LEN)
        self.assertEqual(outbound[0], GROUP_VOICE)
        self.assertEqual(outbound[1:5], self.MASTER_ID.to_bytes(4, 'big'))
        self.assertEqual(outbound[6:9], src)
        self.assertEqual(outbound[9:12], dst)
        self.assertEqual(outbound[GV_BURST_TYPE_OFF], VOICE_HEAD)
        self.assertEqual(outbound[31], 0x80)

    def test_encode_term_after_stream(self):
        inbound, peer, src, dst = self._make_head_packet()
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        dmrd_head = tr.translate(inbound, 2, VOICE_HEAD)
        self.assertIsNotNone(dmrd_head)

        term_in = bytearray([GROUP_VOICE]) + bytearray(30)
        term_in[1:5] = peer
        term_in[5] = 0x42
        term_in[6:9] = src
        term_in[9:12] = dst
        term_in[17] = TS_CALL_MSK
        term_in[GV_BURST_TYPE_OFF] = VOICE_TERM
        dmrd_term = tr.translate(bytes(term_in), 2, VOICE_TERM)
        self.assertIsNotNone(dmrd_term)

        outbound = tr.encode(dmrd_term)
        self.assertIsNotNone(outbound)
        self.assertEqual(len(outbound), GV_HEAD_LEN)
        self.assertEqual(outbound[GV_BURST_TYPE_OFF], VOICE_TERM)
        self.assertEqual(outbound[51], 0x12)

    def test_encode_voice_slot_produces_52_byte_packet(self):
        inbound, peer, src, dst = self._make_head_packet()
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        tr.translate(inbound, 2, VOICE_HEAD)

        slot_pkt = bytearray([GROUP_VOICE]) + bytearray(51)
        slot_pkt[1:5] = peer
        slot_pkt[5] = 0x42
        slot_pkt[6:9] = src
        slot_pkt[9:12] = dst
        slot_pkt[GV_BURST_TYPE_OFF] = SLOT2_VOICE
        slot_pkt[32] = 0x16
        dmrd = tr.translate(bytes(slot_pkt), 2, SLOT2_VOICE)
        self.assertIsNotNone(dmrd)

        outbound = tr.encode(dmrd)
        self.assertIsNotNone(outbound)
        self.assertEqual(len(outbound), GV_VOICE_LEN)
        self.assertEqual(outbound[GV_BURST_TYPE_OFF], SLOT2_VOICE)
        self.assertEqual(outbound[31:33], b'\x14\x40')

    def test_handle_outbound_buffers_voice(self):
        """Voice bursts are jitter-buffered; HEAD is sent immediately."""
        inbound, peer, src, dst = self._make_head_packet()
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        dmrd_head = tr.translate(inbound, 2, VOICE_HEAD)
        self.assertIsNotNone(tr.handle_outbound(dmrd_head))

        slot_pkt = bytearray([GROUP_VOICE]) + bytearray(51)
        slot_pkt[1:5] = peer
        slot_pkt[5] = 0x42
        slot_pkt[6:9] = src
        slot_pkt[9:12] = dst
        slot_pkt[GV_BURST_TYPE_OFF] = SLOT2_VOICE
        slot_pkt[32] = 0x16
        dmrd_voice = tr.translate(bytes(slot_pkt), 2, SLOT2_VOICE)
        self.assertIsNotNone(dmrd_voice)
        self.assertIsNone(tr.handle_outbound(dmrd_voice))

    def test_handle_outbound_skips_duplicate_head(self):
        """pkt_gen-style triple HEAD: only the first becomes an IPSC HEAD."""
        inbound, peer, src, dst = self._make_head_packet()
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        dmrd_head = tr.translate(inbound, 2, VOICE_HEAD)
        self.assertIsNotNone(tr.handle_outbound(dmrd_head))
        self.assertIsNone(tr.handle_outbound(dmrd_head))

    def test_term_flushes_buffered_voice(self):
        """TERM must not discard jitter-buffered AMBE (reflector / canned speech)."""
        sent = []
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        tr.set_send_callback(sent.append)

        inbound, peer, src, dst = self._make_head_packet()
        dmrd_head = tr.translate(inbound, 2, VOICE_HEAD)
        tr.handle_outbound(dmrd_head)

        slot_pkt = bytearray([GROUP_VOICE]) + bytearray(51)
        slot_pkt[1:5] = peer
        slot_pkt[5] = 0x42
        slot_pkt[6:9] = src
        slot_pkt[9:12] = dst
        slot_pkt[GV_BURST_TYPE_OFF] = SLOT2_VOICE
        slot_pkt[32] = 0x16
        dmrd_voice = tr.translate(bytes(slot_pkt), 2, SLOT2_VOICE)
        tr.handle_outbound(dmrd_voice)

        term_in = bytearray([GROUP_VOICE]) + bytearray(30)
        term_in[1:5] = peer
        term_in[5] = 0x42
        term_in[6:9] = src
        term_in[9:12] = dst
        term_in[17] = TS_CALL_MSK
        term_in[GV_BURST_TYPE_OFF] = VOICE_TERM
        dmrd_term = tr.translate(bytes(term_in), 2, VOICE_TERM)
        term_out = tr.handle_outbound(dmrd_term)

        self.assertIsNotNone(term_out)
        self.assertEqual(term_out[GV_BURST_TYPE_OFF], VOICE_TERM)
        voice_sent = [p for p in sent if len(p) == GV_VOICE_LEN]
        self.assertEqual(len(voice_sent), 1)

    def test_pkt_gen_private_call_flag(self):
        caller = bytes_3(2348831)
        speech = pkt_gen(bytes_3(5000), caller, b'\x00\x03\x96\x77', 1,
                         [words['silence']], private_call=True)
        head = next(speech)
        self.assertTrue(head[15] & HBPF_UNIT_CALL)

    def test_encode_reflector_private_speech(self):
        caller = bytes_3(2348831)
        speech = pkt_gen(bytes_3(5000), caller, b'\x00\x03\x96\x77', 1,
                         [words['silence']], private_call=True)
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        sample = bytes.fromhex(
            '81' + '00039717' + '14' + '23cb93' + '00092e'
            + '0200002e39' + '20'
            + '80ddc618226cc7f700000000'
            + '01'
        )
        tr.learn_peer_header(sample, private_call=True)
        head = next(speech)
        out1 = tr.encode(head)
        out2 = tr.encode(head)
        self.assertIsNotNone(out1)
        self.assertIsNone(out2)
        self.assertEqual(out1[0], PRIVATE_VOICE)
        self.assertEqual(out1[6:9], bytes_3(5000))
        self.assertEqual(out1[9:12], caller)

    def test_learn_peer_header(self):
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        sample = bytes.fromhex(
            '80' + '00039717' + '14' + '23cb93' + '00092e'
            + '0200002e39' + '20'
            + '80ddc618226cc7f700000000'
            + '01'
        )
        tr.learn_peer_header(sample)
        self.assertEqual(tr._peer_call_type, b'\x02')
        self.assertEqual(tr._peer_call_ctrl, b'\x00\x00\x2e\x39')


if __name__ == '__main__':
    unittest.main()
