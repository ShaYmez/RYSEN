#!/usr/bin/env python3
"""Parrot TG 9990 helpers — never OBP, never dial-a-tg / TG 9."""
import unittest

from dmr_utils3 import decode
from dmr_utils3.utils import bytes_3, bytes_4, int_id

from bridge_helpers import (
    PARROT_TG,
    is_parrot_bridge,
    is_parrot_talkgroup,
    private_call_may_create_reflector,
)
from mk_voice import pkt_gen
from playback import (
    HBP_UNIT_CALL,
    PARROT_SRC,
    build_parrot_echo_packets,
    parrot_echo_addresses,
)
from voice_lib import words


CALLER = bytes_3(2345875)
PEER = bytes_4(234587599)


def _recorded_group_call():
    """Minimal group TG 9990 stream (headers + terminator) as playback records it."""
    gen = pkt_gen(CALLER, bytes_3(9990), PEER, 1, [], private_call=False)
    return list(gen)


class TestParrotHelpers(unittest.TestCase):

    def test_parrot_tg_constant(self):
        self.assertEqual(PARROT_TG, 9990)

    def test_is_parrot_talkgroup(self):
        self.assertTrue(is_parrot_talkgroup(9990))
        self.assertTrue(is_parrot_talkgroup('9990'))
        self.assertFalse(is_parrot_talkgroup(9))
        self.assertFalse(is_parrot_talkgroup(9991))

    def test_is_parrot_bridge(self):
        self.assertTrue(is_parrot_bridge('9990'))
        self.assertTrue(is_parrot_bridge('#9990'))
        self.assertFalse(is_parrot_bridge('2350'))
        self.assertFalse(is_parrot_bridge('#9'))

    def test_parrot_not_dial_reflector(self):
        self.assertFalse(private_call_may_create_reflector(9990, {}))


class TestParrotSourceGuards(unittest.TestCase):

    def test_make_stat_and_reflector_refuse_parrot(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if is_parrot_talkgroup(int_id(_tgid)):', source)
        self.assertIn('Refusing parrot TG 9990 as dial-a-tg reflector', source)
        self.assertIn('is_parrot_bridge(_bridge)', source)
        self.assertIn('_forward_parrot_unit_voice', source)
        self.assertNotIn('deferToThread', source)

    def test_playback_group_echo_for_group_inbound(self):
        with open('playback.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("if _call_type in ('group', 'unit'):", source)
        self.assertIn('PARROT_SRC = bytes_3(9990)', source)
        self.assertIn('build_parrot_echo_packets', source)
        self.assertIn('rewrite_parrot_echo_packet', source)
        self.assertIn('_unit_call = (_call_type == \'unit\')', source)
        self.assertNotIn('_bits_out = i[15] | 0x40', source)
        self.assertIn('parrot_echo_addresses', source)


class TestParrotEchoRewrite(unittest.TestCase):

    def test_group_inbound_echoes_group_on_tg_9990(self):
        src, dst = parrot_echo_addresses(False, CALLER)
        self.assertEqual(src, PARROT_SRC)
        self.assertEqual(dst, PARROT_SRC)
        self.assertEqual(int_id(dst), 9990)

    def test_unit_inbound_echoes_private_to_caller(self):
        src, dst = parrot_echo_addresses(True, CALLER)
        self.assertEqual(src, PARROT_SRC)
        self.assertEqual(dst, CALLER)

    def test_group_echo_header_matches_lc_and_resets_seq(self):
        recorded = _recorded_group_call()
        self.assertGreaterEqual(len(recorded), 2)
        # Recorded inbound is caller → TG 9990 group (the SIP portal case).
        self.assertEqual(recorded[0][5:8], CALLER)
        self.assertEqual(recorded[0][8:11], bytes_3(9990))
        self.assertFalse(recorded[0][15] & HBP_UNIT_CALL)

        stream_id = bytes_4(0xAABBCCDD)
        echoed, out_sid, src, dst = build_parrot_echo_packets(
            recorded, False, CALLER, stream_id=stream_id)
        self.assertEqual(out_sid, stream_id)
        self.assertEqual(len(echoed), len(recorded))
        for seq, pkt in enumerate(echoed):
            self.assertEqual(pkt[4], seq)
            self.assertEqual(pkt[5:8], PARROT_SRC)
            self.assertEqual(pkt[8:11], PARROT_SRC)
            self.assertEqual(pkt[16:20], stream_id)
            self.assertFalse(pkt[15] & HBP_UNIT_CALL)

        decoded = decode.voice_head_term(echoed[0][20:53])
        lc = decoded['LC']
        self.assertEqual(lc[3:6], PARROT_SRC)  # dst TG
        self.assertEqual(lc[6:9], PARROT_SRC)  # src
        self.assertEqual(lc[0], 0x00)  # group FLCO

    def test_echo_resets_hbp_seq_independent_of_recording(self):
        recorded = _recorded_group_call()
        shifted = []
        for i, pkt in enumerate(recorded):
            buf = bytearray(pkt)
            buf[4] = (80 + i) & 0xFF
            shifted.append(bytes(buf))
        echoed, _, _, _ = build_parrot_echo_packets(shifted, False, CALLER)
        for seq, pkt in enumerate(echoed):
            self.assertEqual(pkt[4], seq)

    def test_group_echo_rewrites_embedded_lc_on_voice_bursts(self):
        gen = pkt_gen(CALLER, bytes_3(9990), PEER, 1, [words['0']], private_call=False)
        recorded = list(gen)
        bursts = [p for p in recorded
                  if (p[15] & 0x30) == 0 and (p[15] & 0x0F) in (1, 2, 3, 4)]
        self.assertTrue(bursts)
        echoed, _, _, _ = build_parrot_echo_packets(recorded, False, CALLER)
        echo_bursts = [p for p in echoed
                       if (p[15] & 0x30) == 0 and (p[15] & 0x0F) in (1, 2, 3, 4)]
        self.assertEqual(len(echo_bursts), len(bursts))
        self.assertNotEqual(bursts[0][20:53], echo_bursts[0][20:53])

    def test_unit_echo_header_matches_private_lc(self):
        recorded = _recorded_group_call()
        echoed, _, src, dst = build_parrot_echo_packets(recorded, True, CALLER)
        self.assertEqual(src, PARROT_SRC)
        self.assertEqual(dst, CALLER)
        self.assertTrue(echoed[0][15] & HBP_UNIT_CALL)
        self.assertEqual(echoed[0][5:8], PARROT_SRC)
        self.assertEqual(echoed[0][8:11], CALLER)
        decoded = decode.voice_head_term(echoed[0][20:53])
        lc = decoded['LC']
        self.assertEqual(lc[3:6], CALLER)
        self.assertEqual(lc[6:9], PARROT_SRC)
        self.assertEqual(lc[0], 0x03)  # unit FLCO


if __name__ == '__main__':
    unittest.main()
