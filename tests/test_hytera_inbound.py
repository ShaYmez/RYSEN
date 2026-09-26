#!/usr/bin/env python3
import unittest

from hytera_master import HyteraMasterMixin
from hytera_voice import HyteraVoiceTranslator, hytera_payload_to_dmrd


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


def fixture(name):
    return bytes.fromhex(TS1_FIXTURES[name])


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
            'c': 0x02,
            'd': 0x03,
            'e': 0x04,
            'f': 0x05,
            'a': 0x10,
            'b': 0x01,
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

    def test_private_and_unknown_length_are_gated(self):
        private = bytearray(fixture('header'))
        private[62] = 0
        self.assertIsNone(self.translator.translate_group(private))
        self.assertIsNone(self.translator.translate_group(b'\x00' * 103))

    def test_payload_conversion_requires_exact_length(self):
        self.assertIsNone(hytera_payload_to_dmrd(b'\x00' * 33))
        converted = hytera_payload_to_dmrd(bytes(range(34)))
        self.assertEqual(len(converted), 33)
        self.assertEqual(converted[:6], b'\x01\x00\x03\x02\x05\x04')


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

    def test_unregistered_source_ip_is_rejected(self):
        self.master.hytera_dmr_received(
            fixture('header'), ('198.51.100.7', 50001))
        self.assertEqual(self.received, [])


if __name__ == '__main__':
    unittest.main()
