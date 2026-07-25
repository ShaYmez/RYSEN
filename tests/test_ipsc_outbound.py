#!/usr/bin/env python3
import unittest
from unittest.mock import patch

from dmr_utils3.utils import bytes_3
from mk_voice import pkt_gen
from twisted.internet.task import Clock
from voice_lib import words

from ipsc_const import (
    GROUP_VOICE, PRIVATE_VOICE, VOICE_HEAD, VOICE_TERM, SLOT2_VOICE, TS_CALL_MSK,
    HBPF_UNIT_CALL,
    GV_BURST_TYPE_OFF, GV_HEAD_LEN, GV_VOICE_LEN,
)
from ipsc_voice import IpscVoiceTranslator, _paced_next_deadline


class TestIpscOutbound(unittest.TestCase):

    MASTER_ID = 9999999

    def test_paced_deadline_does_not_accumulate_callback_cost(self):
        self.assertAlmostEqual(_paced_next_deadline(1.000, 1.005), 1.060)
        self.assertAlmostEqual(_paced_next_deadline(1.000, 1.121), 1.181)

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

    def test_inbound_stream_identity_includes_ipsc_peer(self):
        inbound, peer, src, dst = self._make_head_packet()
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        first = tr.translate(inbound, 2, VOICE_HEAD)

        second_head = bytearray(inbound)
        second_head[1:5] = b'\x01\x02\x03\x04'
        second = tr.translate(bytes(second_head), 2, VOICE_HEAD)

        self.assertNotEqual(first[16:20], second[16:20])
        self.assertEqual(tr._out_ipsc_peer_id[2], b'\x01\x02\x03\x04')

        stale_term = bytearray(inbound)
        stale_term[GV_BURST_TYPE_OFF] = VOICE_TERM
        self.assertIsNone(tr.translate(bytes(stale_term), 2, VOICE_TERM))
        self.assertEqual(tr._out_ipsc_peer_id[2], b'\x01\x02\x03\x04')

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

    def test_term_drains_buffered_voice_at_cadence(self):
        """TERM waits behind buffered AMBE instead of bursting it immediately."""
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

        self.assertIsNone(term_out)
        self.assertEqual(sent, [])

        # Simulate the two paced callbacks: queued voice, then terminator.
        tr._cancel_delivery_timer(2)
        tr._deliver_slot(2)
        voice_sent = [p for p in sent if len(p) == GV_VOICE_LEN]
        self.assertEqual(len(voice_sent), 1)
        self.assertFalse(any(p[GV_BURST_TYPE_OFF] == VOICE_TERM for p in sent))

        tr._cancel_delivery_timer(2)
        tr._deliver_slot(2)
        self.assertEqual(sent[-1][GV_BURST_TYPE_OFF], VOICE_TERM)

    def test_jitter_queue_preserves_wrapped_superframe_order(self):
        """Positions from consecutive generations must not overwrite each other."""
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        for pos in (4, 5, 0, 1, 2, 3, 4):
            tr._del_buf[2].append((pos, bytes([pos]) * 19))

        self.assertEqual(
            [pos for pos, _ambe in tr._del_buf[2]],
            [4, 5, 0, 1, 2, 3, 4],
        )
        self.assertEqual(len(tr._del_buf[2]), 7)

    def test_jitter_queue_bounds_latency_at_superframe_boundary(self):
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        for index in range(30):
            pos = index % 6
            tr._del_buf[2].append((pos, bytes([pos]) * 19))
            tr._trim_delivery_backlog(2)

        self.assertLessEqual(len(tr._del_buf[2]), 12)
        self.assertEqual(tr._del_buf[2][0][0], 0)
        self.assertEqual(tr._del_burst_pos[2], 0)

    def test_new_head_closes_prior_deferred_stream(self):
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
        self.assertIsNone(tr.handle_outbound(dmrd_term))

        new_head = bytearray(dmrd_head)
        new_head[16:20] = b'\x12\x34\x56\x78'
        new_head_out = tr.handle_outbound(bytes(new_head))

        self.assertIsNotNone(new_head_out)
        self.assertEqual(sent[-1][GV_BURST_TYPE_OFF], VOICE_TERM)
        self.assertEqual(tr._del_hbp_stream[2], b'\x12\x34\x56\x78')
        self.assertIsNone(tr._del_pending_term[2])

    def test_headerless_new_voice_closes_prior_deferred_stream(self):
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
        self.assertIsNone(tr.handle_outbound(dmrd_term))

        new_voice = bytearray(dmrd_voice)
        new_voice[16:20] = b'\x87\x65\x43\x21'
        self.assertIsNone(tr.handle_outbound(bytes(new_voice)))

        self.assertEqual(sent[-1][GV_BURST_TYPE_OFF], VOICE_TERM)
        self.assertEqual(tr._del_hbp_stream[2], b'\x87\x65\x43\x21')
        self.assertIsNone(tr._del_pending_term[2])
        tr._cancel_delivery_timer(2)

    def test_stale_term_cannot_close_replacement_stream(self):
        sent = []
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        tr.set_send_callback(sent.append)

        inbound, peer, src, dst = self._make_head_packet()
        old_head = tr.translate(inbound, 2, VOICE_HEAD)
        tr.handle_outbound(old_head)

        term_in = bytearray([GROUP_VOICE]) + bytearray(30)
        term_in[1:5] = peer
        term_in[5] = 0x42
        term_in[6:9] = src
        term_in[9:12] = dst
        term_in[17] = TS_CALL_MSK
        term_in[GV_BURST_TYPE_OFF] = VOICE_TERM
        old_term = tr.translate(bytes(term_in), 2, VOICE_TERM)

        new_head = bytearray(old_head)
        new_head[16:20] = b'\xaa\xbb\xcc\xdd'
        self.assertIsNotNone(tr.handle_outbound(bytes(new_head)))
        sent_before_stale = len(sent)

        self.assertIsNone(tr.handle_outbound(old_term))
        self.assertEqual(len(sent), sent_before_stale)
        self.assertEqual(tr._del_hbp_stream[2], b'\xaa\xbb\xcc\xdd')
        self.assertIsNone(tr._del_pending_term[2])

    def test_delivery_does_not_catch_up_in_a_burst_after_reactor_stall(self):
        clock = Clock()
        sent_at = []
        with patch('ipsc_voice.reactor', clock):
            tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
            tr.set_send_callback(lambda packet: sent_at.append(
                (clock.seconds(), packet)))

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
            for _ in range(5):
                dmrd_voice = tr.translate(bytes(slot_pkt), 2, SLOT2_VOICE)
                tr.handle_outbound(dmrd_voice)

            clock.advance(0.12)
            self.assertEqual(len(sent_at), 1)

            # A delayed reactor callback emits one slot, then schedules the
            # next slot 60 ms from now instead of draining overdue callbacks.
            clock.advance(0.50)
            self.assertEqual(len(sent_at), 2)
            clock.advance(0.059)
            self.assertEqual(len(sent_at), 2)
            clock.advance(0.001)
            self.assertEqual(len(sent_at), 3)

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

    def test_begin_reflector_encode_session_keeps_call_seq(self):
        tr = IpscVoiceTranslator(master_id=self.MASTER_ID)
        tr._del_stream_ctr = 5
        tr._del_rtp_seq[2] = 100
        tr.begin_reflector_encode_session()
        self.assertEqual(tr._del_stream_ctr, 5)
        self.assertEqual(tr._del_rtp_seq[2], 100)
        self.assertEqual(tr._del_stream_id[2], 0)

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
