#!/usr/bin/env python3
"""Talker Alias encode, DMRA ingest, and embedded-LC rotation."""
import os
import tempfile
import time
import unittest

from bitarray import bitarray
from dmr_utils3 import bptc

import talker_alias as ta
from const import DMRA, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, HBPF_VOICE
from hblink import HBSYSTEM
from ipsc_const import VOICE_HEAD
from ipsc_voice import IpscVoiceTranslator


def _decode_emb(emb):
    return bptc.decode_emblc(emb[1] + emb[2] + emb[3] + emb[4])


def _header_payload(src=2623266, dst=91):
    lc = bytes([0x00, 0x00, 0x00]) + int(dst).to_bytes(3, 'big') + int(src).to_bytes(3, 'big')
    header = bptc.encode_header_lc(lc)
    bits = bitarray(264, endian='big')
    bits.setall(0)
    bits[0:98] = header[0:98]
    bits[166:264] = header[98:197]
    return bits.tobytes()


def _repeat_packet(stream, frame_type, vseq, src=2623266, dst=91):
    raw = bytearray(55)
    raw[0:4] = b'DMRD'
    raw[5:8] = int(src).to_bytes(3, 'big')
    raw[8:11] = int(dst).to_bytes(3, 'big')
    raw[15] = (frame_type << 4) | vseq
    raw[16:20] = stream
    if frame_type == HBPF_DATA_SYNC and vseq == HBPF_SLT_VHEAD:
        raw[20:53] = _header_payload(src, dst)
    return bytes(raw)


def _ipsc_head(seq, src=2623266):
    data = bytearray(31)
    data[1:5] = b'\x00\x00\x00\x01'
    data[5] = seq
    data[6:9] = int(src).to_bytes(3, 'big')
    data[9:12] = (91).to_bytes(3, 'big')
    return bytes(data)


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
        ta._voice_lc.clear()

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

    def test_new_superframe_discards_partial_embedded(self):
        src = (2623266).to_bytes(3, 'big')
        torn = ta.encode_emblc(ta.encode_lc_blocks('DO1KBL')[0])
        whole = ta.encode_emblc(ta.encode_lc_blocks('M0VUB')[0])
        for vseq in (2, 3, 4):
            self.assertEqual(
                ta.note_embedded(src, HBPF_VOICE, vseq, _voice_payload(torn[vseq])),
                '')
        self.assertEqual(set(ta._assemblers[2623266].frags), {2, 3, 4})
        self.assertEqual(
            ta.note_embedded(src, HBPF_VOICE, 1, _voice_payload(whole[1])),
            '')
        self.assertEqual(set(ta._assemblers[2623266].frags), {1})
        seen = ''
        for vseq in (2, 3, 4):
            seen = ta.note_embedded(src, HBPF_VOICE, vseq, _voice_payload(whole[vseq]))
        self.assertEqual(seen, 'M0VUB')
        self.assertEqual(ta.resolve(2623266), 'M0VUB')

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

        head = bytearray(packet(HBPF_DATA_SYNC, HBPF_SLT_VHEAD))
        head[20:53] = _header_payload()
        head = bytes(head)
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

    def test_duplicate_header_does_not_restart_repeat(self):
        ta.set_tables({2623266: 'DO1KBL'})
        stream = b'\x11\x22\x33\x44'
        head = _repeat_packet(stream, HBPF_DATA_SYNC, HBPF_SLT_VHEAD)
        ta.embed_repeat(head, 2623266, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream)
        for vseq in (1, 2, 3, 4):
            original = _repeat_packet(stream, HBPF_VOICE, vseq)
            self.assertEqual(
                ta.embed_repeat(original, 2623266, HBPF_VOICE, vseq, stream),
                original)
        started = _repeat_packet(stream, HBPF_VOICE, 1)
        self.assertNotEqual(
            ta.embed_repeat(started, 2623266, HBPF_VOICE, 1, stream),
            started)
        sched = ta._repeat[('', stream)]
        self.assertEqual(
            ta.embed_repeat(head, 2623266, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream),
            head)
        self.assertIs(ta._repeat[('', stream)], sched)
        self.assertGreater(sched._index, 0)
        continued = _repeat_packet(stream, HBPF_VOICE, 2)
        self.assertNotEqual(
            ta.embed_repeat(continued, 2623266, HBPF_VOICE, 2, stream),
            continued)

    def test_header_from_another_source_starts_a_new_schedule(self):
        ta.set_tables({2623266: 'DO1KBL', 100: 'M0VUB'})
        stream = b'\x11\x22\x33\x44'
        head = _repeat_packet(stream, HBPF_DATA_SYNC, HBPF_SLT_VHEAD)
        ta.embed_repeat(head, 2623266, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream)
        for vseq in (1, 2, 3, 4, 1):
            original = _repeat_packet(stream, HBPF_VOICE, vseq)
            ta.embed_repeat(original, 2623266, HBPF_VOICE, vseq, stream)
        other = _repeat_packet(stream, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, src=100)
        ta.embed_repeat(other, 100, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream)
        self.assertEqual(ta._repeat[('', stream)].src, 100)
        for vseq in (2, 3, 4):
            original = _repeat_packet(stream, HBPF_VOICE, vseq, src=100)
            self.assertEqual(
                ta.embed_repeat(original, 100, HBPF_VOICE, vseq, stream),
                original)

    def test_voice_before_header_is_not_rewritten(self):
        ta.set_tables({2623266: 'DO1KBL'})
        stream = b'\xaa\xbb\xcc\xdd'
        for vseq in (1, 2, 3, 4):
            original = _repeat_packet(stream, HBPF_VOICE, vseq)
            self.assertEqual(
                ta.embed_repeat(original, 2623266, HBPF_VOICE, vseq, stream),
                original)
        self.assertNotIn(('', stream), ta._repeat)
        head = _repeat_packet(stream, HBPF_DATA_SYNC, HBPF_SLT_VHEAD)
        ta.embed_repeat(head, 2623266, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream)
        for vseq in (1, 2, 3, 4):
            original = _repeat_packet(stream, HBPF_VOICE, vseq)
            self.assertEqual(
                ta.embed_repeat(original, 2623266, HBPF_VOICE, vseq, stream),
                original)
        alias = _repeat_packet(stream, HBPF_VOICE, 1)
        self.assertNotEqual(
            ta.embed_repeat(alias, 2623266, HBPF_VOICE, 1, stream),
            alias)

    def test_missing_terminator_expires(self):
        ta.set_tables({2623266: 'DO1KBL'})
        stream = b'\x11\x22\x33\x44'
        other = b'\x55\x66\x77\x88'
        head = _repeat_packet(stream, HBPF_DATA_SYNC, HBPF_SLT_VHEAD)
        ta.embed_repeat(head, 2623266, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream)
        self.assertIn(('', stream), ta._repeat)
        ta._repeat[('', stream)].seen = time.monotonic() - (ta._REPEAT_IDLE + 1)
        other_head = _repeat_packet(other, HBPF_DATA_SYNC, HBPF_SLT_VHEAD)
        ta.embed_repeat(other_head, 2623266, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, other)
        self.assertNotIn(('', stream), ta._repeat)
        self.assertIn(('', other), ta._repeat)
        term = _repeat_packet(other, HBPF_DATA_SYNC, HBPF_SLT_VTERM)
        ta.embed_repeat(term, 2623266, HBPF_DATA_SYNC, HBPF_SLT_VTERM, other)
        self.assertNotIn(('', other), ta._repeat)

    def test_config_file_toggles_without_restart(self):
        handle, path = tempfile.mkstemp(suffix='.cfg')
        try:
            os.write(handle, b'[GLOBAL]\nTALKER_ALIAS: False\n')
            os.close(handle)
            self.assertIs(ta.refresh_from_file(path), False)
            self.assertFalse(ta.enabled())
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('[GLOBAL]\nTALKER_ALIAS: True\n')
            self.assertIs(ta.refresh_from_file(path), True)
            self.assertTrue(ta.enabled())
            self.assertIsNone(ta.refresh_from_file(path + '.missing'))
            self.assertTrue(ta.enabled())
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_ipsc_duplicate_head_keeps_schedule(self):
        translator = IpscVoiceTranslator()
        self.assertIsNotNone(translator.translate(_ipsc_head(7), 1, VOICE_HEAD))
        sched = translator._ta_rx[1]
        sched._sent_voice = True
        sched._index = 2
        sched._open_pos = 3
        self.assertIsNotNone(translator.translate(_ipsc_head(7), 1, VOICE_HEAD))
        self.assertIs(translator._ta_rx[1], sched)
        self.assertTrue(sched._sent_voice)
        self.assertEqual(sched._index, 2)
        self.assertEqual(sched._open_pos, 3)
        self.assertIsNotNone(translator.translate(_ipsc_head(8), 1, VOICE_HEAD))
        self.assertIsNot(translator._ta_rx[1], sched)
        kept = translator._ta_rx[1]
        self.assertIsNotNone(translator.translate(_ipsc_head(8, src=100), 1, VOICE_HEAD))
        self.assertIsNot(translator._ta_rx[1], kept)
        self.assertEqual(translator._ta_rx[1].src, 100)

    def test_alias_option_selects_the_destination(self):
        self.assertIsNone(ta.parse_alias_option('TS2=2350;'))
        self.assertIs(ta.parse_alias_option('TS2=2350;ALIAS=0;'), False)
        self.assertIs(ta.parse_alias_option(b'ALIAS=1;STICKY=1'), True)
        self.assertIsNone(ta.parse_alias_option('ALIAS=banana'))
        self.assertTrue(ta.destination_enabled({'MODE': 'MASTER'}))
        self.assertFalse(ta.destination_enabled(
            {'MODE': 'MASTER'}, {'OPTIONS': 'ALIAS=0'}))
        self.assertTrue(ta.destination_enabled(
            {'MODE': 'MASTER', '_default_options': 'ALIAS=0'},
            {'OPTIONS': 'TS2=2350;ALIAS=1'}))
        self.assertFalse(ta.destination_enabled(
            {'MODE': 'HYTERA', 'OPTIONS': 'TS2=2350;ALIAS=0;'}))
        self.assertFalse(ta.destination_enabled(
            {'MODE': 'IPSC', 'OPTIONS': 'ALIAS=0'}))
        self.assertTrue(ta.destination_enabled(
            {'MODE': 'OPENBRIDGE', 'OPTIONS': 'ALIAS=0'}))
        ta.set_enabled(False)
        self.assertFalse(ta.destination_enabled(
            {'MODE': 'HYTERA', 'OPTIONS': 'ALIAS=1'}))
        ta.set_enabled(True)
        self.assertFalse(ta.destination_enabled(
            {'MODE': 'IPSC', 'ALIAS': False}))
        self.assertFalse(ta.destination_enabled(
            {'MODE': 'HYTERA', 'ALIAS': False}))
        self.assertFalse(ta.destination_enabled(
            {'MODE': 'MASTER', 'ALIAS': False}))
        self.assertTrue(ta.destination_enabled(
            {'MODE': 'MASTER', 'ALIAS': False},
            {'OPTIONS': 'ALIAS=1'}))
        self.assertFalse(ta.destination_enabled(
            {'MODE': 'IPSC', 'ALIAS': False, 'OPTIONS': 'TS2=2350;'}))

    def test_strip_embedded_alias_restores_voice_lc(self):
        voice = ta.encode_emblc(b'\x00' * 9)
        alias = ta.encode_emblc(bytes([0x04, 0x00, 0x47]) + b'\x00' * 6)
        payload = bitarray(264, endian='big')
        payload.setall(0)
        payload[116:148] = alias[1]
        packet = b'DMRD' + bytes(11) + bytes([0x01]) + b'\x11\x22\x33\x44' + payload.tobytes()
        plain = ta.strip_embedded_alias(packet, voice)
        bits = bitarray(endian='big')
        bits.frombytes(plain[20:53])
        self.assertEqual(bits[116:148], voice[1])
        self.assertEqual(ta.strip_embedded_alias(packet[:20], voice), packet[:20])

    def test_repeat_schedules_stay_on_their_own_master(self):
        stream = b'\x11\x22\x33\x44'
        one = _repeat_packet(stream, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, src=2623266)
        two = _repeat_packet(stream, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, src=100)
        ta.embed_repeat(one, 2623266, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream, 'MASTER-1')
        ta.embed_repeat(two, 100, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream, 'MASTER-2')
        self.assertEqual(ta._repeat[('MASTER-1', stream)].src, 2623266)
        self.assertEqual(ta._repeat[('MASTER-2', stream)].src, 100)

    def test_missing_voice_lc_is_not_rewritten(self):
        ta.set_tables({2623266: 'DO1KBL'})
        stream = b'\x11\x22\x33\x44'
        head = _repeat_packet(stream, HBPF_DATA_SYNC, HBPF_SLT_VHEAD)
        ta.embed_repeat(head, 2623266, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream)
        ta._repeat[('', stream)].voice_emb = {}
        for _ in range(2):
            for vseq in (1, 2, 3, 4):
                original = _repeat_packet(stream, HBPF_VOICE, vseq)
                self.assertEqual(
                    ta.embed_repeat(original, 2623266, HBPF_VOICE, vseq, stream),
                    original)

    def test_alias_off_peer_is_stripped_without_slot_tx_state(self):
        from types import SimpleNamespace
        voice = ta.encode_emblc(b'\x00' * 9)
        alias = ta.encode_emblc(bytes([0x04, 0x00, 0x47]) + b'\x00' * 6)
        payload = bitarray(264, endian='big')
        payload.setall(0)
        payload[116:148] = alias[1]
        stream = b'\x11\x22\x33\x44'
        packet = b'DMRD' + bytes(11) + bytes([0x01]) + stream + payload.tobytes()
        ta.remember_voice('MASTER', stream, voice)
        peer = b'\x00\x00\x00\x01'
        sent = []
        master = SimpleNamespace(
            _system='MASTER',
            _config={'MODE': 'MASTER'},
            _peers={peer: {'OPTIONS': 'ALIAS=0', 'SOCKADDR': ('10.0.0.8', 50001)}},
            transport=SimpleNamespace(write=lambda pkt, addr: sent.append(pkt)),
            STATUS={1: {'TX_STREAM_ID': b'\x99\x99\x99\x99', 'TX_EMB_LC': voice}},
        )
        master.send_peer = lambda peer_id, pkt: HBSYSTEM.send_peer(master, peer_id, pkt)
        HBSYSTEM.send_peers(master, packet)
        bits = bitarray(endian='big')
        bits.frombytes(sent[0][20:53])
        self.assertEqual(bits[116:148], voice[1])


if __name__ == '__main__':
    unittest.main()
