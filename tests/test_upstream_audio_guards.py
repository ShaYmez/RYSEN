#!/usr/bin/env python3
"""Regression guards for audio-critical divergences from FreeDMR."""
import re
import unittest

from bridge_helpers import (
    dmr_seq_delta,
    earliest_obp_owner,
    harden_obp_stub,
)


class TestWrapSafeDmrSequence(unittest.TestCase):

    def test_first_packet_has_no_ordering_delta(self):
        self.assertIsNone(dmr_seq_delta(0, False))
        self.assertIsNone(dmr_seq_delta(42, None))

    def test_wrap_is_forward_order(self):
        self.assertEqual(dmr_seq_delta(255, 254), 1)
        self.assertEqual(dmr_seq_delta(0, 255), 1)
        self.assertEqual(dmr_seq_delta(1, 0), 1)

    def test_duplicate_gap_and_stale_packet(self):
        self.assertEqual(dmr_seq_delta(7, 7), 0)
        self.assertEqual(dmr_seq_delta(12, 9), 3)
        self.assertGreater(dmr_seq_delta(250, 1), 127)


class TestObpOwnerElection(unittest.TestCase):

    class _System:
        def __init__(self, status):
            self.STATUS = status

    def test_local_earliest_claim_wins_mirrored_ingress(self):
        stream = b'\x00\x00\x00\x01'
        tg = b'\x00\x00W'
        source = b'\x01\x02\x03'
        systems = {
            'OBP-A': self._System({
                stream: {
                    '1ST': 10.0, 'TGID': tg, 'RFS': source,
                    'LAST': 100.0,
                },
            }),
            'OBP-B': self._System({
                stream: {
                    '1ST': 11.0, 'TGID': tg, 'RFS': source,
                    'LAST': 100.0,
                },
            }),
        }

        owner = earliest_obp_owner(
            {'OBP-A', 'OBP-B'}, systems, stream,
            tg, source, 100.1, 5.0)

        self.assertEqual(owner, 'OBP-A')

    def test_finished_and_stale_claims_cannot_win(self):
        stream = b'\x00\x00\x00\x02'
        tg = b'\x00\x00W'
        source = b'\x01\x02\x03'
        systems = {
            'FINISHED': self._System({
                stream: {
                    '1ST': 1.0, 'TGID': tg, 'RFS': source,
                    'LAST': 100.0, '_fin': True,
                },
            }),
            'STALE': self._System({
                stream: {
                    '1ST': 2.0, 'TGID': tg, 'RFS': source,
                    'LAST': 90.0,
                },
            }),
            'ACTIVE': self._System({
                stream: {
                    '1ST': 3.0, 'TGID': tg, 'RFS': source,
                    'LAST': 100.0,
                },
            }),
        }

        owner = earliest_obp_owner(
            set(systems), systems, stream,
            tg, source, 100.1, 5.0)

        self.assertEqual(owner, 'ACTIVE')


class TestObpStubHardening(unittest.TestCase):

    def test_outbound_stub_is_completed_for_inbound_packet_control(self):
        existing_lc = b'existing-lc'
        status = {
            'START': 1.0,
            'LAST': 2.0,
            'RFS': b'\x01\x02\x03',
            'TGID': b'\x00\x00W',
            'LC': existing_lc,
            'TARGET_LC': {},
        }

        result = harden_obp_stub(status, 3.0, b'fallback-lc')

        self.assertIs(result, status)
        self.assertEqual(status['1ST'], 3.0)
        self.assertIs(status['lastSeq'], False)
        self.assertIs(status['lastData'], False)
        self.assertEqual(status['LC'], existing_lc)


class TestPacketControlSourceGuards(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open('bridge_master.py', encoding='utf-8') as fh:
            cls.bridge_source = fh.read()
        with open('hblink.py', encoding='utf-8') as fh:
            cls.hblink_source = fh.read()
        with open('hotspot_proxy_v2.py', encoding='utf-8') as fh:
            cls.proxy_source = fh.read()
        with open('bridge.py', encoding='utf-8') as fh:
            cls.legacy_bridge_source = fh.read()
        with open('ipsc_proxy.py', encoding='utf-8') as fh:
            cls.ipsc_proxy_source = fh.read()
        with open('ipsc_master.py', encoding='utf-8') as fh:
            cls.ipsc_master_source = fh.read()

    def test_hbp_new_stream_resets_duplicate_state(self):
        hbp_start = self.bridge_source.index('class routerHBP')
        new_stream = self.bridge_source[
            self.bridge_source.index('# Is this a new call stream?', hbp_start):
            self.bridge_source.index('#Create default bridge for unknown TG', hbp_start)
        ]
        self.assertIn("self.STATUS[_slot]['lastSeq'] = False", new_stream)
        self.assertIn("self.STATUS[_slot]['lastData'] = False", new_stream)

    def test_hbp_and_obp_use_modular_sequence_delta(self):
        self.assertEqual(self.bridge_source.count('_seq_delta = dmr_seq_delta('), 2)
        self.assertNotIn("_seq < self.STATUS[_slot]['lastSeq']", self.bridge_source)
        self.assertNotIn("_seq < self.STATUS[_stream_id]['lastSeq']", self.bridge_source)
        self.assertGreaterEqual(self.bridge_source.count("_seq_delta = None"), 2)

    def test_lifetime_packet_fingerprint_dedup_is_removed(self):
        self.assertNotIn("['crcs'].add(_pkt_crc)", self.bridge_source)
        self.assertNotIn('_pkt_crc in self.STATUS', self.bridge_source)

    def test_loopcontrol_requires_fresh_active_source_identity(self):
        self.assertIn(
            'def _active_hbp_stream_claim(stream_id, rf_src, now):',
            self.bridge_source)
        self.assertIn('key = (stream_id, rf_src)', self.bridge_source)
        self.assertGreaterEqual(
            self.bridge_source.count('_active_hbp_stream_claim('), 3)

    def test_standard_proxy_preserves_mid_login_session(self):
        self.assertIn("'CHALLENGE_SENT'", self.proxy_source)
        self.assertIn("'WAITING_CONFIG'", self.proxy_source)
        self.assertIn("'AUTH_ACKED'", self.proxy_source)
        self.assertIn("'CONFIG_SENT'", self.proxy_source)
        self.assertIn("elif _conn == 'CONFIG_SENT':", self.proxy_source)
        self.assertIn(
            "elif _command == RPTC and data[:5] != RPTCL:",
            self.proxy_source)

    def test_standard_proxy_rx_voice_refreshes_idle_timer(self):
        self.assertIn("if _command == DMRD:\n"
                      "                    _timer = self.peerTrack[_peer_id].get('timer')",
                      self.proxy_source)
        self.assertIn('_timer.reset(self.timeout)', self.proxy_source)

    def test_ipsc_proxy_rx_traffic_refreshes_idle_timer(self):
        self.assertIn("timer = peer.get('timer')", self.ipsc_proxy_source)
        self.assertIn('timer.reset(self.timeout)', self.ipsc_proxy_source)

    def test_hbp_voice_refreshes_peer_liveness(self):
        self.assertIn(
            "self._peers[_peer_id]['LAST_PING'] = time()",
            self.hblink_source)

    def test_raw_repeat_has_order_gate(self):
        self.assertIn('self._repeat_seq = {}', self.hblink_source)
        self.assertIn("and _repeat_ok:", self.hblink_source)
        self.assertIn('_repeat_vhead_restart', self.hblink_source)
        self.assertIn('if len(self._repeat_seq) > 1024:', self.hblink_source)
        self.assertIn('and _repeat_ok\n', self.hblink_source)

    def test_generation_restarts_propagate_to_target_state(self):
        self.assertGreaterEqual(
            self.bridge_source.count('_new_generation=False'), 2)
        self.assertGreaterEqual(
            self.bridge_source.count('_target_generation_changed'), 2)
        self.assertGreaterEqual(
            self.bridge_source.count(', _hbp_new_stream)'), 4)
        self.assertGreaterEqual(
            self.bridge_source.count(', _obp_new_stream)'), 2)
        self.assertGreaterEqual(
            self.bridge_source.count("'TARGET_LC': {}"), 2)
        self.assertGreaterEqual(
            self.bridge_source.count(
                "_target_lc_map[_target['TGID']]"), 2)

    def test_stale_hbp_terminators_are_rejected_before_routing(self):
        stale_guard = self.bridge_source.index('Ignoring stale VTERM')
        route = self.bridge_source.index(
            '# --- OPTIMISED ROUTING:', stale_guard)
        self.assertLess(stale_guard, route)
        self.assertIn(
            "if self.STATUS[_slot]['RX_TYPE'] == HBPF_SLT_VTERM:",
            self.bridge_source)
        self.assertIn('_is_tombstoned_term(', self.bridge_source)
        self.assertGreaterEqual(
            self.bridge_source.count('_remember_term('), 3)
        self.assertIn(
            'def _is_tombstoned_term(key, data, now, sequence_expected=False):',
            self.bridge_source)
        self.assertIn('if sequence_expected:', self.bridge_source)
        self.assertIn('_hbp_sequence_expected', self.bridge_source)
        self.assertIn('_obp_sequence_expected', self.bridge_source)

    def test_claim_is_set_only_after_sequence_acceptance(self):
        packet_control = self.bridge_source.index(
            '#Duplicate handling#',
            self.bridge_source.index('class routerHBP'))
        claim_update = self.bridge_source.index(
            '_set_hbp_stream_claim(', packet_control)
        last_data = self.bridge_source.index(
            "self.STATUS[_slot]['lastData'] = _data", packet_control)
        self.assertGreater(claim_update, last_data)
        self.assertIn(
            'if _now - _claim[2] >= _HBP_CLAIM_TIMEOUT_S:',
            self.bridge_source)

    def test_every_successful_target_is_deduplicated(self):
        self.assertEqual(
            self.bridge_source.count('_sysIgnore.append(_ignore_key)'), 2)
        self.assertEqual(
            self.bridge_source.count(
                "_ignore_key = (_target['SYSTEM'], _target['TS'])"), 2)
        self.assertNotIn(
            "_ignore_key = (_target['SYSTEM'], _target['TS'], "
            "_target['TGID'])",
            self.bridge_source)
        self.assertNotIn(
            "if _target_system['MODE'] == 'OPENBRIDGE':\n"
            "                    _sysIgnore.append(_ignore_key)",
            self.bridge_source)

    def test_obp_completion_does_not_depend_on_reporting(self):
        self.assertEqual(
            self.bridge_source.count(
                "self.STATUS[_stream_id]['_fin'] = True"), 1)

    def test_legacy_bridge_entrypoint_has_audio_parity(self):
        self.assertEqual(
            self.legacy_bridge_source.count('_seq_delta = dmr_seq_delta('), 2)
        self.assertGreaterEqual(
            self.legacy_bridge_source.count('_tx_dmrpkt = dmrpkt'), 4)
        self.assertIn(
            "_target_status[_target['TS']]['TX_STREAM_ID'] != _stream_id",
            self.legacy_bridge_source)
        self.assertGreaterEqual(
            self.legacy_bridge_source.count('_target_generation_changed'), 2)
        self.assertIn('_OPENBRIDGE_SYSTEMS = set()',
                      self.legacy_bridge_source)
        self.assertIn(
            'fi = earliest_obp_owner(',
            self.legacy_bridge_source)
        self.assertNotIn(
            "CONFIG['SYSTEMS'][self._system]['ENHANCED_OBP']",
            self.legacy_bridge_source)
        self.assertEqual(
            self.legacy_bridge_source.count(
                "CONFIG['SYSTEMS'][self._system].get("
                "'ENHANCED_OBP', False)"), 2)
        self.assertIn(
            'for _claim_key, _claim in list(_HBP_STREAM_CLAIMS.items()):',
            self.legacy_bridge_source)
        self.assertIsNone(re.search(
            r'(?<!_tx_)dmrpkt = dmrbits\.tobytes\(\)',
            self.legacy_bridge_source))

    def test_receive_paths_reject_short_dmrd(self):
        self.assertGreaterEqual(self.hblink_source.count('if len(_data) < 53:'), 2)
        self.assertGreaterEqual(self.proxy_source.count('len(data) < 53'), 2)

    def test_dmre_version_changes_only_after_authentication(self):
        dmre_block = self.hblink_source[
            self.hblink_source.index("elif _packet[:4] == DMRE:"):
            self.hblink_source.index("elif _packet[:4] == DMRF:")
        ]
        auth = 'if compare_digest(_hash, _ckhs)'
        upgrade = "if _embedded_version > self._config['VER']:"
        self.assertLess(dmre_block.index(auth), dmre_block.index(upgrade))
        self.assertIn(
            'if _embedded_version < 4 or _embedded_version > VER:',
            dmre_block)

    def test_dmre_sender_tags_negotiated_wire_version(self):
        self.assertGreaterEqual(
            self.hblink_source.count(
                "_ver = self._config['VER'].to_bytes(1,'big')"),
            2,
        )
        self.assertNotIn('_ver = VER.to_bytes', self.hblink_source)

    def test_dmrf_uses_full_timestamp_and_stale_guard(self):
        self.assertIn("_timestamp = _packet[53:61]", self.hblink_source)
        self.assertIn("Stale DMRF packet discarded", self.hblink_source)

    def test_canned_audio_uses_reactor_backpressure(self):
        self.assertIn('from threading import Event', self.bridge_source)
        self.assertIn("if not getattr(reactor, 'running', False):",
                      self.bridge_source)
        self.assertIn('return done.wait(1.0) and sent[0]',
                      self.bridge_source)
        self.assertNotIn(
            'reactor.callFromThread(sendVoicePacket', self.bridge_source)
        self.assertIn('_bounded_reactor_call(', self.ipsc_master_source)
        self.assertNotIn('blockingCallFromThread', self.ipsc_master_source)


if __name__ == '__main__':
    unittest.main()
