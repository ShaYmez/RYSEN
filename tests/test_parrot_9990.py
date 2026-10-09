#!/usr/bin/env python3
"""Parrot TG 9990 helpers — never OBP, never dial-a-tg / TG 9."""
import unittest
from unittest.mock import MagicMock, patch

import bridge_master as bm
from const import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM
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
        self.assertIn("if (self._system != 'PARROT'", source)
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


class TestParrotSkipsUaTimer(unittest.TestCase):

    def test_peer_parrot_has_no_ua_timer(self):
        from bridge_helpers import (
            should_remember_peer_dynamic_tg,
            ua_timer_minutes,
        )
        parrot = {'MODE': 'PEER', 'ENABLED': True}
        master = {'MODE': 'MASTER', 'DEFAULT_UA_TIMER': 10}
        self.assertIsNone(ua_timer_minutes(parrot))
        self.assertIsNone(ua_timer_minutes({}))
        self.assertEqual(ua_timer_minutes(master), 10)
        self.assertFalse(should_remember_peer_dynamic_tg(parrot, 9990))
        self.assertFalse(should_remember_peer_dynamic_tg(master, 9990))
        self.assertFalse(should_remember_peer_dynamic_tg(master, 9))
        self.assertTrue(should_remember_peer_dynamic_tg(master, 326))

    def test_group_call_path_uses_ua_timer_guard(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('ua_timer_minutes(CONFIG[\'SYSTEMS\'][self._system])', source)
        self.assertIn('should_remember_peer_dynamic_tg(', source)
        self.assertNotIn(
            "CONFIG['SYSTEMS'][self._system]\n                        ['DEFAULT_UA_TIMER'] * 60",
            source)


class TestPlaybackRecording(unittest.TestCase):

    def _player(self):
        import playback as pbmod
        pbmod.subscriber_ids = {}
        pbmod.peer_ids = {}
        pbmod.talkgroup_ids = {}
        player = pbmod.playback.__new__(pbmod.playback)
        player._system = 'PARROT'
        player.CALL_DATA = [b'previous-over']
        player._record_rf_src = bytes_3(1)
        player.send_system = lambda packet: None
        player.STATUS = {
            'RX_START': 0,
            2: {
                'RX_STREAM_ID': b'\x00\x00\x00\x01',
                'RX_TYPE': HBPF_SLT_VTERM,
            },
        }
        return pbmod, player

    def _packet(self, stream, seq, dtype):
        packet = bytearray(55)
        packet[0:4] = b'DMRD'
        packet[4] = seq
        packet[5:8] = CALLER
        packet[8:11] = bytes_3(9990)
        packet[15] = (HBPF_DATA_SYNC << 4) | (dtype & 0x0F) | 0x80
        packet[16:20] = stream
        return bytes(packet)

    def test_new_stream_discards_a_stuck_recording(self):
        pbmod, player = self._player()
        fresh = self._packet(b'\x00\x00\x00\x02', 0, HBPF_SLT_VHEAD)
        player.dmrd_received(
            PEER, CALLER, bytes_3(9990), 0, 2, 'group',
            HBPF_DATA_SYNC, HBPF_SLT_VHEAD, b'\x00\x00\x00\x02', fresh)
        self.assertEqual(player.CALL_DATA, [fresh])
        self.assertEqual(player.STATUS[2]['RX_TYPE'], HBPF_SLT_VHEAD)

    def test_kerchunk_still_plays_when_slot_was_already_idle(self):
        pbmod, player = self._player()
        sent = []
        player.send_system = sent.append
        stream = b'\x00\x00\x00\x03'
        header = self._packet(stream, 0, HBPF_SLT_VHEAD)
        term = self._packet(stream, 1, HBPF_SLT_VTERM)
        with patch.object(pbmod, 'sleep'):
            player.dmrd_received(
                PEER, CALLER, bytes_3(9990), 0, 2, 'group',
                HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream, header)
            player.dmrd_received(
                PEER, CALLER, bytes_3(9990), 1, 2, 'group',
                HBPF_DATA_SYNC, HBPF_SLT_VTERM, stream, term)
        self.assertTrue(sent)
        self.assertEqual(player.CALL_DATA, [])
        self.assertFalse(sent[0][15] & 0x40)


class TestParrotMonitorEvents(unittest.TestCase):

    def setUp(self):
        self._systems = dict(bm.systems)
        bm.systems.clear()
        self._config = getattr(bm, 'CONFIG', None)
        bm.CONFIG = {
            'REPORTS': {'REPORT': True},
            'ALLSTAR': {'ENABLED': False},
            'SYSTEMS': {
                'PARROT': {
                    'MODE': 'PEER',
                    'ENABLED': True,
                    'GROUP_HANGTIME': 5,
                    'ANNOUNCEMENT_LANGUAGE': 'en_US',
                    'PEERS': {},
                },
            },
        }

    def tearDown(self):
        bm.systems.clear()
        bm.systems.update(self._systems)
        if self._config is None:
            if hasattr(bm, 'CONFIG'):
                delattr(bm, 'CONFIG')
        else:
            bm.CONFIG = self._config

    def _router(self):
        router = bm.routerHBP.__new__(bm.routerHBP)
        router._system = 'SYSTEM-A'
        router._report = MagicMock()
        parrot = MagicMock()
        parrot._report = router._report
        parrot.send_system = MagicMock()
        bm.systems['PARROT'] = parrot
        return router

    def _events(self, router):
        return [
            call.args[0].decode('utf-8').split(',')
            for call in router._report.send_bridgeEvent.call_args_list
        ]

    def test_stale_terminator_does_not_end_a_newer_group_stream(self):
        router = self._router()
        older = b'\x00\x00\x00\x01'
        newer = b'\x00\x00\x00\x02'
        router._report_parrot_group_leg(
            newer, PEER, CALLER, b'\x00',
            HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 100.0)
        router._report_parrot_group_leg(
            older, PEER, CALLER, b'\x00',
            HBPF_DATA_SYNC, HBPF_SLT_VTERM, 100.5)
        router._report_parrot_group_leg(
            newer, PEER, CALLER, b'\x00',
            HBPF_DATA_SYNC, HBPF_SLT_VTERM, 101.0)
        events = self._events(router)
        self.assertEqual([event[1] for event in events], ['START', 'END'])
        self.assertEqual(events[0][4], str(int_id(newer)))
        self.assertEqual(events[1][4], str(int_id(newer)))
        self.assertEqual(events[1][9], '1.00')

    def test_repeated_group_terminator_emits_end_once(self):
        router = self._router()
        stream = b'\x00\x00\x00\x03'
        router._report_parrot_group_leg(
            stream, PEER, CALLER, b'\x04',
            HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 50.0)
        router._report_parrot_group_leg(
            stream, PEER, CALLER, b'\x04',
            HBPF_DATA_SYNC, HBPF_SLT_VTERM, 52.0)
        router._report_parrot_group_leg(
            stream, PEER, CALLER, b'\x04',
            HBPF_DATA_SYNC, HBPF_SLT_VTERM, 52.1)
        events = self._events(router)
        self.assertEqual([event[1] for event in events], ['START', 'END'])
        self.assertEqual(events[0][9], '4')
        self.assertEqual(events[1][9], '2.00')

    def test_missing_report_socket_still_forwards_audio(self):
        router = self._router()
        router._report = None
        bm.systems['PARROT']._report = None
        packet = bytearray(55)
        packet[0:4] = b'DMRD'
        router._forward_parrot_unit_voice(
            bytes_3(9990), 2, 0x80, bytes(packet), bytes(packet[20:53]))
        router._report_parrot_group_leg(
            b'\x00\x00\x00\x04', PEER, CALLER, b'\x00',
            HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 1.0)
        bm.systems['PARROT'].send_system.assert_called_once()

    def test_parrot_bridge_skip_and_direct_audio_path_are_unchanged(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn(
            "if _target['SYSTEM'] == 'PARROT' and is_parrot_bridge(_bridge):",
            source)
        self.assertIn('self._forward_parrot_unit_voice(', source)
        self.assertIn('self._report_parrot_group_leg(', source)
        forward = source.split('def _forward_parrot_unit_voice', 1)[1]
        forward = forward.split('def sendDataToOBP', 1)[0]
        self.assertNotIn('send_bridgeEvent', forward)


if __name__ == '__main__':
    unittest.main()
