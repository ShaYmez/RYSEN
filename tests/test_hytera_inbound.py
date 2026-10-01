#!/usr/bin/env python3
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from const import DMRD
from hytera_const import (
    PRIN,
    RDAC_STEP0_REQUEST,
    RDAC_STEP1_REQUEST,
    RDAC_STEP3_REQUEST,
)
from hytera_master import HyteraMasterMixin
from hytera_rdac_meta import (
    build_hrnp_ack,
    build_rdac_rssi_request,
    parse_rdac_channel,
    parse_rdac_identity,
    parse_rdac_rssi,
)
from hytera_voice import (
    HyteraOutboundPacer,
    HyteraVoiceTranslator,
    dmrd_payload_to_hytera,
    hytera_payload_to_dmrd,
    hytera_uplink_rssi,
)
from twisted.internet.defer import succeed
from twisted.internet.task import Clock


TS1_FIXTURES = {
    'header': (
        '5a5a5a5a1c0000004100050101000000111111111111000040990103b8091805'
        '28358067c1b26d4457ff5dd7def510328804803e2094c143038c005bb8090100'
        'eb0000001fd72300'),
    'c': (
        '5a5a5a5a1f000000410005010100000011117777111100004052a9cd1080a780'
        '6c49adc6c4c8e732fd55f77d765f8ccf5b3cc8bd32c4cfe63d8c005b10800100'
        'eb0000001fd72300'),
    'd': (
        '5a5a5a5a20000000410005010100000011118888111100004034c8bf54e4cbe6'
        '3f88cf5981a9e176a030c030145969c0c05eadeb30876ac0566d000c54e40100'
        'eb0000001fd72300'),
    'e': (
        '5a5a5a5a21000000410005010100000011119999111100004076adeb30876ac05'
        '66dfb0c87adc130c07030f040676d680c668ff9348448c4753d000e30870100'
        'eb0000001fd72300'),
    'f': (
        '5a5a5a5a2200000041000501010000001111aaaa1111000040768ef934844ac4'
        '473df91d86afe116b171817246273d5b0843afd936a47bc6433d002e34840100'
        'eb0000001fd72300'),
    'hytera_sync': (
        '5a5a5a5a0000000042000501010000001111eeee111111114054000000000000'
        '00000000eb002300d7001f00d45513103456131094561310ffffef082b8f0100'
        'eb0000001fd72300'),
    'a': (
        '5a5a5a5a2300000041000501010000001111bbbb111100004011e8b952817361'
        '2a00b96b81e861529350423271e000736b2ae8b9528173612a00006b52810100'
        'eb0000001fd72300'),
    'b': (
        '5a5a5a5a2400000041000501010000001111cccc111100004013e8b952817361'
        '2a00b96b81e86152a21d429121ce00736b2ae8b9528173612a00006b52810100'
        'eb0000001fd72300'),
    'term': (
        '5a5a5a5a250000004300050101000000111122221111000040996e036c09a805'
        '5035f06761b2ad4457ff5dd7d9f50467b007e0396098c14b039500686c090100'
        'eb0000001fd72300'),
}

TS2_HEADER = bytes.fromhex(
    '5a5a5a5a16000000410005010200000022221111111100004099c30fde09b405'
    '7820100741bb6dc457ff5dd7def5a432d007e039611c005783900028de090100'
    '2e0900001fd72300')
FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'hytera'


def fixture(name):
    return bytes.fromhex(TS1_FIXTURES[name])


def wire_fixture(name):
    """Load redacted, one-packet-per-line native Hytera capture data."""
    return [
        bytes.fromhex(line)
        for line in (FIXTURE_DIR / name).read_text().splitlines()
        if line and not line.startswith('#')
    ]


def dmrd(flags, payload, stream=b'\x10\x20\x30\x40',
         source=2344669, destination=235, sequence=1):
    return (
        DMRD + bytes((sequence,)) + source.to_bytes(3, 'big')
        + destination.to_bytes(3, 'big') + (235287).to_bytes(4, 'big')
        + bytes((flags,)) + stream + payload + b'\x00\x00'
    )


class TestHyteraInboundVoice(unittest.TestCase):

    def setUp(self):
        self.translator = HyteraVoiceTranslator(
            peer_id=235287, stream_factory=lambda: b'\x01\x02\x03\x04')

    def test_captured_ts1_header_to_dmrd(self):
        dmrd = self.translator.translate_group(fixture('header'))

        self.assertEqual(len(dmrd), 55)
        self.assertEqual(dmrd[:4], b'DMRD')
        self.assertEqual(dmrd[5:8], (2348831).to_bytes(3, 'big'))
        self.assertEqual(dmrd[8:11], (235).to_bytes(3, 'big'))
        self.assertEqual(dmrd[11:15], (235287).to_bytes(4, 'big'))
        self.assertEqual(dmrd[15], 0x21)
        self.assertEqual(dmrd[16:20], b'\x01\x02\x03\x04')
        self.assertEqual(
            dmrd[20:53],
            bytes.fromhex(
                '030109b8051835286780b2c1446dff57d75df5de321004883e80942043c18c035b'))

    def test_captured_voice_cycle_and_terminator_flags(self):
        self.assertIsNotNone(self.translator.translate_group(fixture('header')))
        expected = {
            'c': 0x10,
            'd': 0x01,
            'e': 0x02,
            'f': 0x03,
            'a': 0x04,
            'b': 0x05,
            'term': 0x22,
        }
        for name in ('c', 'd', 'e', 'f'):
            self.assertEqual(self.translator.translate_group(fixture(name))[15],
                             expected[name])
        self.assertIsNone(
            self.translator.translate_group(fixture('hytera_sync')))
        for name in ('a', 'b', 'term'):
            self.assertEqual(self.translator.translate_group(fixture(name))[15],
                             expected[name])
        self.assertIsNone(self.translator._streams[1])

    def test_captured_ts2_header_sets_slot_flag(self):
        dmrd = self.translator.translate_group(TS2_HEADER)
        self.assertEqual(dmrd[15], 0xa1)
        self.assertEqual(dmrd[8:11], (2350).to_bytes(3, 'big'))

    def test_duplicate_wire_sequence_is_dropped(self):
        packet = fixture('header')
        self.assertIsNotNone(self.translator.translate_group(packet))
        self.assertIsNone(self.translator.translate_group(packet))

    def test_voice_without_header_is_dropped(self):
        self.assertIsNone(self.translator.translate_group(fixture('c')))

    def test_private_call_sets_unit_flag(self):
        private = bytearray(fixture('header'))
        private[62] = 0
        dmrd = self.translator.translate_voice(private)
        self.assertIsNotNone(dmrd)
        self.assertEqual(dmrd[15], 0x61)

    def test_unknown_length_is_gated(self):
        self.assertIsNone(self.translator.translate_group(b'\x00' * 103))

    def test_payload_conversion_requires_exact_length(self):
        self.assertIsNone(hytera_payload_to_dmrd(b'\x00' * 33))
        converted = hytera_payload_to_dmrd(bytes(range(34)))
        self.assertEqual(len(converted), 33)
        self.assertEqual(converted[:6], b'\x01\x00\x03\x02\x05\x04')

    def test_oracle_uplink_rssi_stays_zero(self):
        dmrd = self.translator.translate_group(fixture('header'))
        self.assertEqual(hytera_uplink_rssi(fixture('header')), 0)
        self.assertEqual(dmrd[53], 0)
        self.assertEqual(dmrd[54], 0)

    def test_non_zero_uplink_quality_byte_is_dmrd_rssi(self):
        self.assertIsNotNone(self.translator.translate_group(fixture('header')))
        voice = bytearray(fixture('c'))
        voice[58] = 87
        dmrd = self.translator.translate_group(bytes(voice))
        self.assertEqual(dmrd[53], 0)
        self.assertEqual(dmrd[54], 87)


class TestHyteraOutboundVoice(unittest.TestCase):

    def setUp(self):
        self.translator = HyteraVoiceTranslator(peer_id=235287)
        self.payload = bytes(range(33))

    def test_header_carries_voice_lc_to_rd985(self):
        encoded = self.translator.encode_group(
            dmrd(0xa1, self.payload, destination=2350))

        self.assertEqual(encoded[0], 2)
        self.assertFalse(encoded[2])
        packet = encoded[1]
        self.assertEqual(
            packet[:26],
            bytes.fromhex(
                '00000000000000000100050102000000'
                '22221111111100001000'))
        self.assertEqual(packet[26:60],
                         dmrd_payload_to_hytera(self.payload))
        self.assertEqual(packet[63:67], (2350 << 8).to_bytes(4, 'little'))

    def test_voice_and_terminator_use_outbound_packet_types(self):
        stream = b'\x10\x20\x30\x40'
        self.assertIsNotNone(
            self.translator.encode_group(dmrd(0x21, self.payload, stream)))

        ts, voice, paced = self.translator.encode_group(
            dmrd(0x02, self.payload, stream))
        self.assertEqual(ts, 1)
        self.assertTrue(paced)
        self.assertEqual(voice[4], 1)
        self.assertEqual(voice[8], 0x01)
        self.assertEqual(voice[:4], b'\x5a\x5a\x5a\x5a')
        self.assertEqual(voice[18:20], b'\x99\x99')
        self.assertEqual(voice[20:26], b'\x11\x11\x00\x00\x10\x00')
        self.assertEqual(hytera_payload_to_dmrd(voice[26:60]), self.payload)

        _, term, paced = self.translator.encode_group(
            dmrd(0x22, self.payload, stream))
        self.assertTrue(paced)
        self.assertEqual(term[4], 2)
        self.assertEqual(term[8], 0x03)
        self.assertEqual(term[:4], b'\x5a\x5a\x5a\x5a')
        self.assertEqual(term[18:20], b'\x22\x22')
        self.assertEqual(term[20:26], b'\x00' * 6)

    def test_ts2_and_private_call_gate(self):
        ts, packet, _ = self.translator.encode_group(
            dmrd(0xa1, self.payload, destination=2350))
        self.assertEqual(ts, 2)
        self.assertEqual(packet[8], 0x01)
        self.assertEqual(packet[12], 2)
        self.assertEqual(packet[16:18], b'\x22\x22')
        self.assertEqual(packet[63:67], (2350 << 8).to_bytes(4, 'little'))
        self.assertIsNone(
            self.translator.encode_group(dmrd(0x61, self.payload)))

    def test_private_call_uses_captured_private_marker(self):
        stream = b'\x12\x34\x56\x78'
        encoded = self.translator.encode_voice(
            dmrd(0xe1, self.payload, stream, source=9990,
                 destination=2348831))

        ts, header, paced = encoded
        self.assertEqual(ts, 2)
        self.assertFalse(paced)
        self.assertEqual(header[62], 0x00)
        self.assertEqual(header[63:67],
                         (2348831 << 8).to_bytes(4, 'little'))
        self.assertEqual(header[67:71], (9990 << 8).to_bytes(4, 'little'))

        _, voice, paced = self.translator.encode_voice(
            dmrd(0xc2, self.payload, stream, source=9990,
                 destination=2348831))
        self.assertTrue(paced)
        self.assertEqual(voice[62], 0x00)

    def test_voice_cycle_uses_captured_envelope_prefixes(self):
        stream = b'\x10\x20\x30\x40'
        _, header, _ = self.translator.encode_group(
            dmrd(0x21, self.payload, stream))
        self.assertEqual(header[:4], b'\x00\x00\x00\x00')
        self.assertEqual(header[18:20], b'\x11\x11')
        _, sync, _ = self.translator.encode_group(
            dmrd(0x10, self.payload, stream))
        self.assertEqual(sync[:4], b'\x5a\x5a\x5a\x5a')
        self.assertEqual(sync[18:20], b'\x77\x77')
        _, e_burst, _ = self.translator.encode_group(
            dmrd(0x04, self.payload, stream))
        self.assertEqual(e_burst[:4], b'\xee\xee\x11\x11')
        self.assertEqual(e_burst[18:20], b'\xbb\xbb')
        _, ongoing, paced = self.translator.encode_group(
            dmrd(0x02, self.payload, stream))
        self.assertTrue(paced)
        self.assertEqual(ongoing[:4], b'\x5a\x5a\x5a\x5a')
        self.assertEqual(ongoing[18:20], b'\x99\x99')

    def test_outbound_burst_phase_matches_ipsc2_downlink(self):
        stream = b'\x50\x60\x70\x80'
        self.translator.encode_group(dmrd(0x21, self.payload, stream))
        expected = (
            (0x10, b'\x77\x77', b'\x5a\x5a\x5a\x5a'),
            (0x01, b'\x88\x88', b'\x5a\x5a\x5a\x5a'),
            (0x02, b'\x99\x99', b'\x5a\x5a\x5a\x5a'),
            (0x03, b'\xaa\xaa', b'\x5a\x5a\x5a\x5a'),
            (0x04, b'\xbb\xbb', b'\xee\xee\x11\x11'),
            (0x05, b'\xcc\xcc', b'\x5a\x5a\x5a\x5a'),
        )
        for flags, envelope, prefix in expected:
            _, packet, _ = self.translator.encode_group(
                dmrd(flags, self.payload, stream))
            self.assertEqual(packet[18:20], envelope)
            self.assertEqual(packet[:4], prefix)

    def test_headerless_and_replaced_streams_get_call_start(self):
        first = self.translator.encode_group(
            dmrd(0x02, self.payload, stream=b'\x01\x01\x01\x01',
                 source=2341980, destination=235))
        self.assertFalse(first[2])
        self.assertEqual(first[1][8], 0x02)
        self.assertEqual(first[1][:4], b'\x5a\x5a\x5a\x5a')
        self.assertEqual(first[1][22:26], b'\x11\x11\x00\x00')
        self.assertEqual(
            first[1][26:44],
            b'\x00' * 6
            + b'\x00\x00\x00\x00\xeb\x00'
            + b'\x23\x00\xbc\x00\x5c\x00')
        voice = self.translator.encode_group(
            dmrd(0x03, self.payload, stream=b'\x01\x01\x01\x01'))
        self.assertTrue(voice[2])
        self.assertEqual(voice[1][8], 0x01)
        self.assertEqual(voice[1][:4], b'\x5a\x5a\x5a\x5a')

        replacement = self.translator.encode_group(
            dmrd(0x04, self.payload, stream=b'\x02\x02\x02\x02'))
        self.assertFalse(replacement[2])
        self.assertEqual(replacement[1][4], 0)
        self.assertEqual(replacement[1][8], 0x02)

    def test_midstream_join_starts_with_current_voice_burst(self):
        packet = self.translator.encode_group(
            dmrd(0x04, self.payload, stream=b'\x03\x03\x03\x03',
                 source=2340189, destination=23426, sequence=84),
            late_join=True, late_join_sequence=340)

        ts, voice, paced = packet
        self.assertEqual(ts, 1)
        self.assertFalse(paced)
        self.assertEqual(voice[4:8], b'\x54\x01\x00\x00')
        self.assertEqual(voice[8], 0x01)
        self.assertEqual(voice[18:20], b'\xbb\xbb')
        self.assertEqual(voice[:4], b'\xee\xee\x11\x11')
        self.assertEqual(int.from_bytes(voice[63:67], 'little') >> 8, 23426)
        self.assertEqual(int.from_bytes(voice[67:71], 'little') >> 8, 2340189)

        _, next_voice, paced = self.translator.encode_group(
            dmrd(0x05, self.payload, stream=b'\x03\x03\x03\x03',
                 source=2340189, destination=23426, sequence=85),
            late_join=True)
        self.assertTrue(paced)
        self.assertEqual(next_voice[4:8], b'\x55\x01\x00\x00')
        self.assertEqual(next_voice[8], 0x01)
        self.assertEqual(next_voice[:4], b'\x5a\x5a\x5a\x5a')
        self.assertEqual(next_voice[18:20], b'\xcc\xcc')

        _, sync_voice, paced = self.translator.encode_group(
            dmrd(0x10, self.payload, stream=b'\x03\x03\x03\x03',
                 source=2340189, destination=23426, sequence=86),
            late_join=True)
        self.assertTrue(paced)
        self.assertEqual(sync_voice[18:20], b'\x77\x77')
        self.assertEqual(sync_voice[:4], b'\x5a\x5a\x5a\x5a')

        ts2 = HyteraVoiceTranslator(peer_id=235287)
        _, ts2_voice, _ = ts2.encode_group(
            dmrd(0x84, self.payload, stream=b'\x04\x04\x04\x04',
                 source=2340189, destination=2352, sequence=84),
            late_join=True, late_join_sequence=1029)
        self.assertEqual(ts2_voice[8], 0x01)
        self.assertEqual(ts2_voice[16:18], b'\x22\x22')
        self.assertEqual(ts2_voice[18:20], b'\xbb\xbb')

    def test_midstream_join_reacquires_an_existing_normal_stream_once(self):
        stream = b'\x04\x04\x04\x04'
        self.translator.encode_group(
            dmrd(0xa1, self.payload, stream, source=5301034,
                 destination=23516))
        _, normal, paced = self.translator.encode_group(
            dmrd(0x82, self.payload, stream, source=5301034,
                 destination=23516, sequence=84))
        self.assertTrue(paced)
        self.assertEqual(normal[8], 0x01)

        _, resumed, paced = self.translator.encode_group(
            dmrd(0x83, self.payload, stream, source=5301034,
                 destination=23516, sequence=85),
            late_join=True, late_join_sequence=341)
        self.assertFalse(paced)
        self.assertEqual(resumed[:4], b'\x5a\x5a\x5a\x5a')
        self.assertEqual(resumed[8], 0x01)
        self.assertEqual(int.from_bytes(resumed[4:8], 'little'), 341)

        _, continued, paced = self.translator.encode_group(
            dmrd(0x84, self.payload, stream, source=5301034,
                 destination=23516, sequence=86),
            late_join=True)
        self.assertTrue(paced)
        self.assertEqual(continued[8], 0x01)
        self.assertEqual(int.from_bytes(continued[4:8], 'little'), 342)

    def test_midstream_join_preserves_missing_wire_sequence(self):
        stream = b'\x03\x03\x03\x03'
        _, first, _ = self.translator.encode_group(
            dmrd(0x03, self.payload, stream=stream, sequence=84),
            late_join=True, late_join_sequence=340)
        _, after_gap, _ = self.translator.encode_group(
            dmrd(0x05, self.payload, stream=stream, sequence=86),
            late_join=True)

        self.assertEqual(int.from_bytes(first[4:8], 'little'), 340)
        self.assertEqual(int.from_bytes(after_gap[4:8], 'little'), 342)

    def test_midstream_join_sequence_wrap_remains_contiguous(self):
        stream = b'\x04\x04\x04\x04'
        _, first, _ = self.translator.encode_group(
            dmrd(0x03, self.payload, stream=stream, sequence=255),
            late_join=True, late_join_sequence=511)
        _, wrapped, _ = self.translator.encode_group(
            dmrd(0x04, self.payload, stream=stream, sequence=0),
            late_join=True)

        self.assertEqual(int.from_bytes(first[4:8], 'little'), 511)
        self.assertEqual(int.from_bytes(wrapped[4:8], 'little'), 512)

    def test_unknown_voice_subtype_cannot_replace_active_stream(self):
        malformed = self.translator.encode_group(
            dmrd(0x1f, self.payload, stream=b'\x09\x09\x09\x09'))
        self.assertIsNone(malformed)

        valid = self.translator.encode_group(
            dmrd(0x10, self.payload, stream=b'\x01\x01\x01\x01'))
        self.assertEqual(valid[1][18:20], b'\xee\xee')

    def test_payload_round_trip_and_quality_byte(self):
        wire = dmrd_payload_to_hytera(self.payload, quality=0x58)
        self.assertEqual(len(wire), 34)
        self.assertEqual(wire[32], 0x58)
        self.assertEqual(hytera_payload_to_dmrd(wire), self.payload)
        self.assertIsNone(dmrd_payload_to_hytera(b'\x00' * 32))

    def test_pacer_buffers_three_bursts_then_sends_at_sixty_ms(self):
        clock = Clock()
        sent = []
        pacer = HyteraOutboundPacer(sent.append, clock=clock)
        for packet in (b'a', b'b', b'c', b'd'):
            self.assertTrue(pacer.enqueue(1, packet))

        clock.advance(0.179)
        self.assertEqual(sent, [])
        clock.advance(0.001)
        self.assertEqual(sent, [b'a'])
        clock.advance(0.060)
        self.assertEqual(sent, [b'a', b'b'])
        clock.advance(0.060)
        self.assertEqual(sent, [b'a', b'b', b'c'])
        clock.advance(0.060)
        self.assertEqual(sent, [b'a', b'b', b'c', b'd'])

    def test_pacer_does_not_rebuffer_after_midcall_underflow(self):
        clock = Clock()
        sent = []
        pacer = HyteraOutboundPacer(sent.append, clock=clock)

        pacer.enqueue(1, b'a')
        clock.advance(0.180)
        self.assertEqual(sent, [b'a'])

        # Model one missing 60 ms upstream burst after the queue drains. The
        # next available burst is due immediately, not after a new 180 ms
        # startup buffer.
        clock.advance(0.120)
        pacer.enqueue(1, b'b')
        clock.advance(0)
        self.assertEqual(sent, [b'a', b'b'])

    def test_pacer_never_shortens_cadence_after_underflow(self):
        clock = Clock()
        sent = []
        pacer = HyteraOutboundPacer(sent.append, clock=clock)

        pacer.enqueue(1, b'a')
        clock.advance(0.180)
        clock.advance(0.030)
        pacer.enqueue(1, b'b')
        clock.advance(0.029)
        self.assertEqual(sent, [b'a'])
        clock.advance(0.001)
        self.assertEqual(sent, [b'a', b'b'])

    def test_pacer_terminator_restores_startup_buffer(self):
        clock = Clock()
        sent = []
        pacer = HyteraOutboundPacer(sent.append, clock=clock)
        terminator = bytearray(72)
        terminator[18:20] = b'\x22\x22'

        pacer.enqueue(1, b'a')
        clock.advance(0.180)
        pacer.enqueue(1, bytes(terminator))
        pacer.enqueue(1, b'new-call')
        clock.advance(0.059)
        self.assertEqual(sent, [b'a'])
        clock.advance(0.001)
        self.assertEqual(sent, [b'a', bytes(terminator)])
        clock.advance(0.179)
        self.assertEqual(sent, [b'a', bytes(terminator)])
        clock.advance(0.001)
        self.assertEqual(sent, [b'a', bytes(terminator), b'new-call'])

    def test_ts1_normal_call_capture_has_complete_native_cycle(self):
        packets = wire_fixture('normal_ts1.hex')
        self.assertEqual(len(packets), 8)
        self.assertTrue(all(len(packet) == 72 for packet in packets))
        self.assertEqual(
            [packet[18:20] for packet in packets],
            [b'\x11\x11', b'\x77\x77', b'\x88\x88', b'\x99\x99',
             b'\xaa\xaa', b'\xbb\xbb', b'\xcc\xcc', b'\x22\x22'])
        self.assertEqual(
            [int.from_bytes(packet[4:8], 'little') for packet in packets],
            [0x1c, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25])
        self.assertEqual(packets[0][8] & 0x3f, 0x01)
        self.assertEqual(packets[-1][8] & 0x3f, 0x03)
        self.assertTrue(all(packet[62] == 0x01 for packet in packets))
        self.assertTrue(all(
            int.from_bytes(packet[63:67], 'little') >> 8 == 235
            for packet in packets))


class TestHyteraInboundDispatch(unittest.TestCase):

    def setUp(self):
        self.master = object.__new__(HyteraMasterMixin)
        self.master._system = 'HYTERA'
        self.master._hytera_registered = True
        self.master._hytera_addr = ('203.0.113.9', 50000)
        self.master._hytera_last_seen = 0
        self.master._hytera_peer_id = (235287).to_bytes(4, 'big')
        self.master._peers = {}
        self.master._config = {'USE_ACL': False}
        self.master._CONFIG = {'GLOBAL': {'USE_ACL': False}}
        self.master._hytera_trace = False
        self.master._hytera_zero_peer_warned = False
        self.master._hytera_voice = HyteraVoiceTranslator(
            peer_id=235287, stream_factory=lambda: b'\x01\x02\x03\x04')
        self.master._hytera_dmr_addr = None
        self.received = []
        self.master.dmrd_received = lambda *args: self.received.append(args)

    def test_registered_dmr_endpoint_dispatches_group_voice(self):
        self.master.hytera_dmr_received(
            fixture('header'), ('203.0.113.9', 50001))

        self.assertEqual(len(self.received), 1)
        peer, source, destination, seq, slot, call_type = self.received[0][:6]
        self.assertEqual(peer, (235287).to_bytes(4, 'big'))
        self.assertEqual(source, (2348831).to_bytes(3, 'big'))
        self.assertEqual(destination, (235).to_bytes(3, 'big'))
        self.assertEqual(seq, 0)
        self.assertEqual(slot, 1)
        self.assertEqual(call_type, 'group')

    def test_registered_dmr_endpoint_dispatches_private_voice(self):
        private = bytearray(fixture('header'))
        private[62] = 0
        private[63:67] = (4400 << 8).to_bytes(4, 'little')
        self.master.hytera_dmr_received(
            bytes(private), ('203.0.113.9', 50001))

        self.assertEqual(len(self.received), 1)
        peer, source, destination, seq, slot, call_type = self.received[0][:6]
        self.assertEqual(peer, (235287).to_bytes(4, 'big'))
        self.assertEqual(source, (2348831).to_bytes(3, 'big'))
        self.assertEqual(destination, (4400).to_bytes(3, 'big'))
        self.assertEqual(seq, 0)
        self.assertEqual(slot, 1)
        self.assertEqual(call_type, 'unit')

    def test_unregistered_source_ip_is_rejected(self):
        self.master.hytera_dmr_received(
            fixture('header'), ('198.51.100.7', 50001))
        self.assertEqual(self.received, [])

    def test_malformed_dmr_does_not_replace_endpoint_or_touch_watchdog(self):
        self.master._hytera_dmr_addr = ('203.0.113.9', 50001)
        self.master._hytera_last_seen = 123

        self.master.hytera_dmr_received(
            b'\x01\x02\x03', ('203.0.113.9', 59999))

        self.assertEqual(
            self.master._hytera_dmr_addr, ('203.0.113.9', 50001))
        self.assertEqual(self.master._hytera_last_seen, 123)

    def test_expected_hytera_sync_is_ignored_without_malformed_warning(self):
        sync = bytearray(fixture('header'))
        sync[18:20] = b'\xee\xee'

        with self.assertNoLogs('hblink', level='WARNING'):
            self.master.hytera_dmr_received(
                bytes(sync), ('203.0.113.9', 50001))

        self.assertEqual(self.received, [])


class TestHyteraProxyControl(unittest.TestCase):

    class Transport:
        def __init__(self):
            self.writes = []

        def write(self, data, address):
            self.writes.append((data, address))

    def setUp(self):
        self.master = object.__new__(HyteraMasterMixin)
        self.master._system = 'HYTERA-0'
        self.master._config = {
            'PROXY_CONTROL': True,
            'HYTERA_REPEATER_ID': 0,
        }
        self.master._hytera_proxy_enabled = True
        self.master._hytera_proxy_control_ip = '172.16.238.31'
        self.master._hytera_proxy_info = None
        self.master._hytera_rdac_step = 0
        self.master._hytera_registered = False
        self.master._hytera_addr = None
        self.master._hytera_peer_id = b'\x00\x00\x00\x00'
        self.master._hytera_voice = HyteraVoiceTranslator()
        self.master._hytera_outbound = HyteraOutboundPacer(lambda _: None)
        self.master._hytera_dmr_addr = None
        self.master._hytera_rdac_addr = None
        self.master._peers = {}
        self.master._report = None
        self.master._CONFIG = {'GLOBAL': {}}
        self.master._hytera_services = {
            'rdac': type('Protocol', (), {'transport': self.Transport()})(),
        }

    def test_proxy_identity_sets_voice_peer_id(self):
        control = PRIN + json.dumps({'repeater_id': 235287}).encode()

        self.assertTrue(self.master._proxy_control_received(
            control, ('172.16.238.31', 50003)))
        self.assertEqual(self.master._hytera_peer_id,
                         (235287).to_bytes(4, 'big'))
        self.assertEqual(self.master._hytera_voice._peer_id,
                         (235287).to_bytes(4, 'big'))

    def test_proxy_control_rejects_untrusted_or_invalid_payload(self):
        valid = PRIN + json.dumps({'repeater_id': 235287}).encode()
        self.assertTrue(self.master._proxy_control_received(
            valid, ('198.51.100.7', 50003)))
        self.assertEqual(self.master._hytera_peer_id, b'\x00\x00\x00\x00')

        invalid = PRIN + json.dumps({
            'repeater_id': 0x1000000,
            'p2p': ['198.51.100.7', 50000],
        }).encode()
        self.assertTrue(self.master._proxy_control_received(
            invalid, ('172.16.238.31', 50003)))
        self.assertEqual(self.master._hytera_peer_id, b'\x00\x00\x00\x00')

    def test_proxy_close_clears_dynamic_discovered_identity(self):
        discovered = (235287).to_bytes(4, 'big')
        self.master._hytera_peer_id = discovered
        self.master._hytera_voice.set_peer_id(235287)
        self.master._hytera_registered = True
        self.master._hytera_addr = ('172.16.238.31', 50003)
        self.master._hytera_last_seen = 1
        self.master._hytera_service_negotiated_at = 1
        self.master._hytera_rdac_meta = {'firmware': 'A9'}
        self.master._hytera_rdac_addr = ('172.16.238.31', 50005)
        self.master._peers[discovered] = {}

        self.master._clear_hytera_peer()

        self.assertEqual(self.master._hytera_peer_id, b'\x00\x00\x00\x00')
        self.assertEqual(
            self.master._hytera_voice._peer_id, b'\x00\x00\x00\x00')
        self.assertNotIn(discovered, self.master._peers)

    def test_rdac_identity_sequence_requests_capture_validated_steps(self):
        addr = ('172.16.238.31', 50005)
        self.master._advance_rdac_identification(b'\x00', addr)
        writes = self.master._hytera_services['rdac'].transport.writes
        self.assertEqual(writes[-1], (RDAC_STEP0_REQUEST, addr))

        self.master._advance_rdac_identification(b'\x7e\x04\x00\xfd', addr)
        self.assertEqual(writes[-1], (RDAC_STEP1_REQUEST, addr))
        self.master._advance_rdac_identification(b'\x7e\x04\x00\x10', addr)
        identity = bytearray(b'\x7e\x04\x00\x00' + b'\x00' * 20)
        identity[18:21] = (235287).to_bytes(3, 'little')
        self.master._advance_rdac_identification(bytes(identity), addr)
        self.assertEqual(writes[-1], (RDAC_STEP3_REQUEST, addr))

    def test_rdac_keepalive_does_not_restart_active_exchange(self):
        addr = ('172.16.238.31', 50005)
        self.master._hytera_rdac_step = 6
        writes = self.master._hytera_services['rdac'].transport.writes

        self.master._advance_rdac_identification(b'\x00', addr)

        self.assertEqual(self.master._hytera_rdac_step, 6)
        self.assertEqual(writes, [])

    def test_rdac_rssi_poll_matches_ipsc2_and_stamps_the_busy_slot(self):
        request = bytes.fromhex(
            '7e040000201000060019d91b02d40206006400000001807103')
        reply = bytes.fromhex(
            '7e040000102000060022b92f02d4820f0000179703000180000048c300008ec24003')
        later = bytes.fromhex(
            '7e040000102000180022b91d02d4820f0000179703000180000048c30000a8c22603')
        call_start = bytes.fromhex(
            '7e040000102000050019d12402d6020600179703000201a003')

        self.assertEqual(build_rdac_rssi_request(6), request)
        self.assertEqual(
            build_hrnp_ack(6),
            bytes.fromhex('7e04001020100006000c61c9'))
        self.assertEqual(parse_rdac_rssi(reply), {
            'repeater_id': 235287, 1: 0, 2: 71,
        })
        self.assertEqual(parse_rdac_rssi(later)[2], 84)
        broken = bytearray(reply)
        broken[30] ^= 0xff
        self.assertIsNone(parse_rdac_rssi(bytes(broken)))

        self.master._hytera_registered = True
        self.master._hytera_addr = ('172.16.238.31', 50000)
        self.master._hytera_peer_id = (235287).to_bytes(4, 'big')
        self.master._hytera_rssi = {1: 0, 2: 0}
        self.master._hytera_rdac_step = 14
        self.master._hytera_rdac_seq = 17
        self.master._hytera_rdac_last_poll = 16
        self.master._hytera_rssi_ready = False
        self.master._hytera_rssi_polled_at = 0.0
        self.master._hytera_rdac_addr = ('172.16.238.31', 50005)
        self.master._hytera_rdac_enabled = False
        self.master._hytera_trace = False
        self.master._hytera_last_seen = 0
        self.master._peers = {}

        repeater_ack = bytes.fromhex('7e04001010200005000c71ba')
        self.master.hytera_rdac_received(
            repeater_ack, ('172.16.238.31', 50005))
        self.assertEqual(self.master._hytera_rdac_seq, 5)
        self.master._hytera_rdac_seq = 6

        self.master.hytera_rdac_received(call_start, ('172.16.238.31', 50005))
        self.assertTrue(self.master._hytera_rssi_ready)
        self.assertEqual(
            self.master._hytera_services['rdac'].transport.writes[-1][0],
            build_hrnp_ack(5))

        self.master.hytera_rdac_received(reply, ('172.16.238.31', 50005))
        self.assertEqual(self.master._hytera_rssi, {1: 0, 2: 71})
        self.assertEqual(
            self.master._hytera_services['rdac'].transport.writes[-1][0],
            build_hrnp_ack(6))

        self.master._hytera_voice = HyteraVoiceTranslator(
            peer_id=235287, stream_factory=lambda: b'\x01\x02\x03\x04')
        self.master._config = {'USE_ACL': False}
        self.master._CONFIG = {'GLOBAL': {'USE_ACL': False}}
        self.master._hytera_zero_peer_warned = False
        self.master._system = 'HYTERA-1'
        received = []
        self.master.dmrd_received = lambda *args: received.append(args)
        header = bytearray(bytes.fromhex(
            '5a5a5a5a1c0000004100050101000000111111111111000040990103b8091805'
            '28358067c1b26d4457ff5dd7def510328804803e2094c143038c005bb8090100'
            'eb0000001fd72300'))
        header[16:18] = b'\x22\x22'
        writes = self.master._hytera_services['rdac'].transport.writes

        self.master.hytera_dmr_received(bytes(header), ('172.16.238.31', 50001))

        self.assertEqual(writes[-1][0], request)
        self.assertEqual(received[0][9][54], 71)
        header[4] = (header[4] + 1) & 0xff
        self.master.hytera_dmr_received(bytes(header), ('172.16.238.31', 50001))
        self.assertEqual(len(writes), 3)

    def test_rdac_metadata_parsers_reject_short_packets(self):
        self.assertEqual(parse_rdac_identity(b'\x7e\x04\x00\x00'), {})
        self.assertEqual(parse_rdac_channel(b'\x7e\x04\x00\x00'), {})

    def test_rdac_metadata_is_published_after_full_exchange(self):
        class Report:
            def __init__(self):
                self.calls = 0

            def send_config(self):
                self.calls += 1

        addr = ('172.16.238.31', 50005)
        self.master._hytera_peer_id = (235287).to_bytes(4, 'big')
        self.master._hytera_rdac_meta = {}
        self.master._peers = {
            self.master._hytera_peer_id: {
                'CALLSIGN': '235287',
                'SOFTWARE_ID': '',
            },
        }
        self.master._report = Report()
        identity = bytearray(b'\x7e\x04\x00\x00' + b'\x00' * 212)
        identity[18:21] = (235287).to_bytes(3, 'little')
        identity[56:88] = 'A9.02.03.009'.encode('utf-16le').ljust(32, b'\x00')
        identity[88:108] = 'GB7TEST'.encode('utf-16le').ljust(20, b'\x00')
        identity[120:184] = 'RD985'.encode('utf-16le').ljust(64, b'\x00')
        identity[184:216] = '12345678'.encode('utf-16le').ljust(32, b'\x00')
        channel = bytearray(b'\x7e\x04\x00\x00' + b'\x00' * 40)
        channel[26] = 3
        channel[29:33] = (439500000).to_bytes(4, 'little')
        channel[33:37] = (430500000).to_bytes(4, 'little')

        self.assertEqual(parse_rdac_identity(bytes(identity))['hardware'], 'RD985')
        self.assertEqual(parse_rdac_channel(bytes(channel))['mode_raw'], 3)

        for response in (
                b'\x00',
                b'\x7e\x04\x00\xfd',
                b'\x7e\x04\x00\x10',
                bytes(identity),
                b'\x7e\x04\x00\x00',
                b'\x7e\x04\x00\x10',
                bytes(identity),
                b'\x7e\x04\x00\x10',
                b'\x7e\x04\x00\x10',
                bytes(channel),
                b'\x7e\x04\x00\x10',
                bytes(channel),
                b'\x7e\x04\x00\xfa'):
            self.master._advance_rdac_identification(response, addr)

        peer = self.master._peers[self.master._hytera_peer_id]
        self.assertEqual(self.master._hytera_rdac_step, 14)
        self.assertEqual(peer['SOFTWARE_ID'], 'A9.02.03.009')
        self.assertEqual(peer['DESCRIPTION'], 'RD985')
        self.assertEqual(peer['SERIAL'], '12345678')
        self.assertEqual(peer['CALLSIGN'], 'GB7TEST')
        self.assertEqual(peer['HYTERA_MODE'], 3)
        self.assertEqual(peer['TX_FREQ'], 439500000)
        self.assertEqual(peer['RX_FREQ'], 430500000)
        self.assertEqual(self.master._report.calls, 1)

    def test_rdac_metadata_creates_hytera_selfcare_client(self):
        db = MagicMock()
        db.upsert_hytera_client.return_value = succeed(None)
        db.upsert_hytera_metadata.return_value = succeed(None)
        db.mark_hytera_options_pending.return_value = succeed(None)
        self.master._CONFIG = {
            'SELF SERVICE': {'ENABLED': True},
            '_SELF_SERVICE_DB': db,
        }
        self.master._config.update({
            'TS1_STATIC': '235',
            'TS2_STATIC': '2350',
        })
        self.master._hytera_addr = ('198.51.100.8', 50000)
        self.master._hytera_peer_id = (235287).to_bytes(4, 'big')
        self.master._peers = {
            self.master._hytera_peer_id: {'CALLSIGN': 'GB7NR'},
        }
        self.master._hytera_rdac_meta = {
            'firmware': 'A9.02.03.009',
            'hardware': 'RD985-00000000-000000-U1-0-F',
            'serial': '14218D0441',
            'callsign': 'GB7NR',
            'mode_raw': 0,
            'tx_frequency': 439437500,
            'rx_frequency': 430437500,
        }

        self.master._sync_hytera_selfcare_register()

        db.upsert_hytera_client.assert_called_once_with(
            235287, self.master._hytera_peer_id, 'GB7NR',
            '198.51.100.8', 'TS1=235;TS2=2350;')
        db.upsert_hytera_metadata.assert_called_once_with(
            235287, self.master._hytera_rdac_meta)


class TestHyteraOutboundDispatch(unittest.TestCase):

    def setUp(self):
        class Transport:
            def __init__(self):
                self.writes = []

            def write(self, packet, addr):
                self.writes.append((packet, addr))

        class Protocol:
            def __init__(self):
                self.transport = Transport()

        self.clock = Clock()
        self.master = object.__new__(HyteraMasterMixin)
        self.master._system = 'HYTERA'
        self.master._config = {'REPEAT': True}
        self.master._hytera_registered = True
        self.master._hytera_dmr_addr = ('203.0.113.9', 50001)
        self.master._hytera_services = {'dmr': Protocol()}
        self.master._hytera_trace = False
        self.master._hytera_voice = HyteraVoiceTranslator(peer_id=235287)
        self.master._hytera_outbound = HyteraOutboundPacer(
            self.master._send_hytera_media, clock=self.clock)
        self.payload = bytes(range(33))

    def test_lc_header_immediate_then_voice_is_paced_to_dmr_endpoint(self):
        stream = b'\x10\x20\x30\x40'
        self.assertTrue(self.master.hytera_send_system(
            dmrd(0x21, self.payload, stream)))
        writes = self.master._hytera_services['dmr'].transport.writes
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1], ('203.0.113.9', 50001))
        self.assertEqual(writes[0][0][8], 0x01)
        self.assertEqual(writes[0][0][18:20], b'\x11\x11')

        self.assertTrue(self.master.hytera_send_system(
            dmrd(0x02, self.payload, stream)))
        self.assertEqual(len(writes), 1)
        self.clock.advance(0.179)
        self.assertEqual(len(writes), 1)
        self.clock.advance(0.001)
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[1][0][8], 0x01)

    def test_private_transmit_and_unregistered_transmit_gate(self):
        self.assertTrue(self.master.hytera_send_system(
            dmrd(0x61, self.payload, source=9990, destination=2348831)))
        writes = self.master._hytera_services['dmr'].transport.writes
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][0][62], 0x00)
        self.master._hytera_registered = False
        self.assertFalse(self.master.hytera_send_system(
            dmrd(0x21, self.payload)))
        self.assertEqual(len(writes), 1)

    def test_midstream_join_replaces_stale_queue_and_buffers_three_bursts(self):
        self.master._hytera_outbound.enqueue(1, b'stale')
        self.assertTrue(self.master.hytera_send_system(
            dmrd(0x04, self.payload, stream=b'\x03\x03\x03\x03',
                 source=2340189, destination=23426),
            _late_join=True))
        self.assertTrue(self.master.hytera_send_system(
            dmrd(0x05, self.payload, stream=b'\x03\x03\x03\x03',
                 source=2340189, destination=23426, sequence=2),
            _late_join=True))
        self.assertTrue(self.master.hytera_send_system(
            dmrd(0x10, self.payload, stream=b'\x03\x03\x03\x03',
                 source=2340189, destination=23426, sequence=3),
            _late_join=True))

        writes = self.master._hytera_services['dmr'].transport.writes
        self.assertEqual(writes, [])
        self.clock.advance(0.059)
        self.assertEqual(writes, [])
        self.clock.advance(0.001)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][0][8], 0x01)
        self.assertEqual(writes[0][0][18:20], b'\xbb\xbb')
        self.clock.advance(0.060)
        self.assertEqual(len(writes), 2)
        self.clock.advance(0.060)
        self.assertEqual(len(writes), 3)


if __name__ == '__main__':
    unittest.main()
