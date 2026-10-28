#!/usr/bin/env python3
"""Talker Alias encode, DMRA ingest, and embedded-LC rotation."""
import unittest

from bitarray import bitarray
from dmr_utils3 import bptc

import talker_alias as ta
from const import DMRA, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_VOICE


def _decode_emb(emb):
    return bptc.decode_emblc(emb[1] + emb[2] + emb[3] + emb[4])


def _voice_payload(frag):
    bits = bitarray(264, endian='big')
    bits.setall(0)
    bits[116:148] = frag
    return bits.tobytes()


class TalkerAliasEncodeTests(unittest.TestCase):
    def setUp(self):
        ta.set_enabled(True)
        ta.set_tables()
        ta._heard.clear()
        ta._assemblers.clear()
        ta._announced.clear()
        ta._repeat.clear()

    def tearDown(self):
        self.setUp()

    def test_round_trip_short_and_long(self):
        for text in ('M0VUB', 'M0VUB Shane', 'A' * 31):
            blocks = ta.encode_7bit(text)
            buf = bytearray(28)
            for index, block in enumerate(blocks):
                buf[index * 7:(index + 1) * 7] = block
            decoded = ta._decode_buffer(buf, len(blocks) * 7)
            self.assertEqual(decoded, text)

    def test_embedded_lc_preserves_block(self):
        lcs = ta.encode_lc_blocks('M0VUB')
        self.assertEqual(len(lcs), 1)
        self.assertEqual(lcs[0][0], 0x04)
        recovered = _decode_emb(ta.encode_emblc(lcs[0]))
        self.assertEqual(bytes(recovered[:9]), lcs[0])

    def test_dmra_15_and_19(self):
        blocks = ta.encode_7bit('OpenGD')
        short = b''.join([
            DMRA,
            (2308105).to_bytes(3, 'big'),
            bytes([0]),
            blocks[0],
        ])
        self.assertEqual(len(short), 15)
        self.assertEqual(ta.note_dmra(short), 'OpenGD')
        self.assertEqual(ta.heard_alias(2308105), 'OpenGD')

        ta._heard.clear()
        ta._assemblers.clear()
        long = b''.join([
            DMRA,
            (2308125).to_bytes(4, 'big'),
            (2308105).to_bytes(3, 'big'),
            bytes([0]),
            blocks[0],
        ])
        self.assertEqual(len(long), 19)
        self.assertEqual(ta.note_dmra(long), 'OpenGD')

    def test_embedded_voice_beats_blank_database(self):
        lcs = ta.encode_lc_blocks('DO1KBL')
        emb = ta.encode_emblc(lcs[0])
        src = (2623266).to_bytes(3, 'big')
        seen = ''
        for vseq in (1, 2, 3, 4):
            seen = ta.note_embedded(src, HBPF_VOICE, vseq, _voice_payload(emb[vseq]))
        self.assertEqual(seen, 'DO1KBL')
        self.assertEqual(ta.resolve(2623266), 'DO1KBL')

    def test_data_sync_is_not_alias(self):
        self.assertEqual(
            ta.note_embedded(b'\x00\x01\x02', HBPF_DATA_SYNC, 3, b'\x00' * 33),
            '')

    def test_database_fallback_and_radio_priority(self):
        ta.set_tables({2623266: 'DO1KBL'}, {2623266: 'OTHER'})
        self.assertEqual(ta.resolve(2623266), 'DO1KBL')
        blocks = ta.encode_7bit('DO1KBL Mobile')
        packet = b''.join([DMRA, (2623266).to_bytes(3, 'big'), bytes([0]), blocks[0]])
        # Header alone is not the whole alias.
        self.assertEqual(ta.note_dmra(packet), '')
        packet2 = b''.join([DMRA, (2623266).to_bytes(3, 'big'), bytes([1]), blocks[1]])
        self.assertEqual(ta.note_dmra(packet2), 'DO1KBL Mobile')
        self.assertEqual(ta.resolve(2623266), 'DO1KBL Mobile')

    def test_numeric_id_is_not_an_alias(self):
        ta.set_tables({100: 100})
        self.assertEqual(ta.resolve(100), '')

    def test_schedule_starts_with_voice_then_alias(self):
        voice = bptc.encode_emblc(b'\x00\x00\x20' + b'\x00\x00\x01' + b'\x00\x01\x02')
        sched = ta.AliasSchedule(2623266)
        ta.set_tables({2623266: 'DO1KBL'})
        first = [sched.fragment(HBPF_VOICE, vseq, voice) for vseq in (1, 2, 3, 4)]
        self.assertEqual(bytes(_decode_emb({1: first[0], 2: first[1], 3: first[2], 4: first[3]})[:1]), b'\x00')
        second = [sched.fragment(HBPF_VOICE, vseq, voice) for vseq in (1, 2, 3, 4)]
        recovered = _decode_emb({1: second[0], 2: second[1], 3: second[2], 4: second[3]})
        self.assertEqual(recovered[0] & 0x3F, 0x04)
        self.assertEqual(ta._decode_buffer(recovered[2:9] + bytes(21), 7), 'DO1KBL')
        third = [sched.fragment(HBPF_VOICE, vseq, voice) for vseq in (1, 2, 3, 4)]
        self.assertEqual(third[0], voice[1])

    def test_alias_arriving_after_first_superframe_is_not_delayed(self):
        voice = bptc.encode_emblc(b'\x00\x00\x20' + b'\x00\x00\x01' + b'\x00\x01\x02')
        sched = ta.AliasSchedule(2623266)
        for vseq in (1, 2, 3, 4):
            sched.fragment(HBPF_VOICE, vseq, voice)
        ta.set_tables({2623266: 'DO1KBL'})
        second = [sched.fragment(HBPF_VOICE, vseq, voice) for vseq in (1, 2, 3, 4)]
        recovered = _decode_emb({1: second[0], 2: second[1], 3: second[2], 4: second[3]})
        self.assertEqual(recovered[0] & 0x3F, 0x04)

    def test_disabled_keeps_voice_lc(self):
        voice = bptc.encode_emblc(b'\x00\x00\x20' + b'\x00\x00\x01' + b'\x00\x01\x02')
        ta.set_tables({2623266: 'DO1KBL'})
        ta.set_enabled(False)
        sched = ta.AliasSchedule(2623266)
        for _ in range(3):
            for vseq in (1, 2, 3, 4):
                self.assertEqual(sched.fragment(HBPF_VOICE, vseq, voice), voice[vseq])
        self.assertEqual(ta.note_dmra(DMRA + b'\x00' * 11), '')

    def test_local_repeat_keeps_voice_then_inserts_alias(self):
        ta.set_tables({2623266: 'DO1KBL'})
        stream = b'\x11\x22\x33\x44'

        def packet(frame_type, vseq):
            raw = bytearray(55)
            raw[0:4] = b'DMRD'
            raw[5:8] = (2623266).to_bytes(3, 'big')
            raw[8:11] = (91).to_bytes(3, 'big')
            raw[15] = (frame_type << 4) | vseq
            raw[16:20] = stream
            return bytes(raw)

        head = packet(HBPF_DATA_SYNC, HBPF_SLT_VHEAD)
        self.assertEqual(ta.embed_repeat(head, 2623266, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream), head)
        for vseq in (1, 2, 3, 4):
            original = packet(HBPF_VOICE, vseq)
            self.assertEqual(
                ta.embed_repeat(original, 2623266, HBPF_VOICE, vseq, stream),
                original)
        frags = []
        for vseq in (1, 2, 3, 4):
            original = packet(HBPF_VOICE, vseq)
            edited = ta.embed_repeat(original, 2623266, HBPF_VOICE, vseq, stream)
            self.assertEqual(edited[:20], original[:20])
            self.assertEqual(edited[53:], original[53:])
            bits = bitarray(endian='big')
            bits.frombytes(edited[20:53])
            self.assertEqual(bits[0:116], bitarray(116, endian='big'))
            frags.append(bits[116:148])
        lc = bptc.decode_emblc(frags[0] + frags[1] + frags[2] + frags[3])
        self.assertEqual(lc[0] & 0x3F, 0x04)


if __name__ == '__main__':
    unittest.main()
