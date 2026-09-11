#!/usr/bin/env python3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bridge_master as bm
from ipsc_const import (
    GROUP_VOICE,
    GV_BURST_TYPE_OFF,
    GV_CALL_INFO_OFF,
    GV_DST_GROUP_OFF,
    TS_CALL_MSK,
    VOICE_HEAD,
    VOICE_TERM,
)
from ipsc_master import IpscMasterMixin
from selfcare_db import (
    effective_master_options,
    ensure_master_default_options,
    ts_lists_from_options,
    union_peer_static_lists,
)


def master_config(peers=None):
    return {
        'MODE': 'MASTER',
        'ENABLED': True,
        'TS1_STATIC': '91',
        'TS2_STATIC': '2350',
        'SINGLE_MODE': False,
        'DEFAULT_UA_TIMER': 10,
        'DEFAULT_REFLECTOR': 0,
        'VOICE_IDENT': False,
        'ANNOUNCEMENT_LANGUAGE': 'en_GB',
        'OVERRIDE_IDENT_TG': 0,
        'PEERS': peers or {},
    }


class TestLegacyMasterOptions(unittest.TestCase):

    def test_single_peer_keeps_legacy_rpto_policy_without_stanza_options(self):
        peer_options = (
            'TS1_1=91;TS2_1=235;RelinkTime=30;StartRef=4400;'
            'SINGLE=1;VOICE=1;LANG=en_US'
        )
        cfg = master_config({
            b'\x00\x00\x00\x01': {
                'CONNECTION': 'YES',
                'OPTIONS': peer_options.encode(),
            },
        })

        effective = effective_master_options(cfg)

        self.assertIn('DEFAULT_UA_TIMER=10', effective)
        self.assertIn(peer_options, effective)
        self.assertNotIn('OPTIONS', cfg)

    def test_shared_master_keeps_cfg_policy_and_unions_peer_statics(self):
        cfg = master_config({
            b'\x00\x00\x00\x01': {
                'CONNECTION': 'YES',
                'OPTIONS': 'TS1=9;TS2=235;RelinkTime=30;SINGLE=1',
            },
            b'\x00\x00\x00\x02': {
                'CONNECTION': 'YES',
                'OPTIONS': 'TS1_1=91;TS2_1=3100;RelinkTime=60;SINGLE=0',
            },
        })

        effective = effective_master_options(cfg)
        ts1, ts2 = union_peer_static_lists(cfg)

        self.assertNotIn('RelinkTime=', effective)
        self.assertEqual(ts1, ['91', '9'])
        self.assertEqual(ts2, ['2350', '235', '3100'])

    def test_default_policy_is_immutable_after_live_values_change(self):
        cfg = master_config()
        baseline = ensure_master_default_options(cfg)
        cfg['DEFAULT_UA_TIMER'] = 99
        cfg['SINGLE_MODE'] = True

        self.assertEqual(ensure_master_default_options(cfg), baseline)
        self.assertIn('DEFAULT_UA_TIMER=10', baseline)
        self.assertIn('SINGLE=0', baseline)
        self.assertIn('OVERRIDE_IDENT_TG=0', baseline)

    def test_standard_and_dmrplus_static_formats_remain_supported(self):
        self.assertEqual(
            ts_lists_from_options('TS1=91,9;TS2=235,2350'),
            (['91', '9'], ['235', '2350']),
        )
        self.assertEqual(
            ts_lists_from_options('TS1_1=91;TS1_2=9;TS2_1=235;TS2_2=2350'),
            (['91', '9'], ['235', '2350']),
        )

    def test_rpto_statics_activate_without_preexisting_stanza_options(self):
        cfg = master_config({
            b'\x00\x00\x00\x01': {
                'CONNECTION': 'YES',
                'OPTIONS': 'TS1=91,9;TS2_1=235;RelinkTime=10',
            },
        })
        runtime_config = {
            'SYSTEMS': {'MASTER-1': cfg},
            '_OPTIONS_DIRTY': True,
        }
        with (
            patch.object(bm, 'CONFIG', runtime_config, create=True),
            patch.object(bm, 'BRIDGES', {}, create=True),
            patch.object(bm, 'BRIDGE_IDX', {}, create=True),
            patch.object(bm, 'words', {'en_GB': {}}, create=True),
            patch.object(bm, 'make_static_tg') as make_static,
            patch.object(bm, 'reset_static_tg'),
        ):
            bm.options_config()

        self.assertEqual(cfg['TS1_STATIC'], '91,9')
        self.assertEqual(cfg['TS2_STATIC'], '2350,235')
        self.assertIn(
            unittest.mock.call(9, 1, 10, 'MASTER-1'),
            make_static.call_args_list,
        )
        self.assertIn(
            unittest.mock.call(235, 2, 10, 'MASTER-1'),
            make_static.call_args_list,
        )

    def test_removed_peer_policy_clears_sticky_link_and_ident_tg(self):
        peer = {
            'CONNECTION': 'YES',
            'OPTIONS': (
                'TS2=235;STICKY=1;IPSC=IPSC-1;IDENTTG=123;RelinkTime=10'
            ),
        }
        cfg = master_config({b'\x00\x00\x00\x01': peer})
        runtime_config = {
            'SYSTEMS': {
                'MASTER-1': cfg,
                'IPSC-1': {'MODE': 'IPSC', 'ENABLED': False},
            },
            '_OPTIONS_DIRTY': True,
        }
        patches = (
            patch.object(bm, 'CONFIG', runtime_config, create=True),
            patch.object(bm, 'BRIDGES', {}, create=True),
            patch.object(bm, 'BRIDGE_IDX', {}, create=True),
            patch.object(bm, 'words', {'en_GB': {}}, create=True),
            patch.object(bm, 'make_static_tg'),
            patch.object(bm, 'reset_static_tg'),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            bm.options_config()
            self.assertTrue(peer['STICKY'])
            self.assertEqual(peer['LINK_IPSC'], 'IPSC-1')
            self.assertEqual(cfg['OVERRIDE_IDENT_TG'], 123)
            self.assertNotIn('LINK_IPSC', cfg)

            peer['OPTIONS'] = 'TS2=235;RelinkTime=10'
            runtime_config['_OPTIONS_DIRTY'] = True
            bm.options_config()

        self.assertNotIn('STICKY', peer)
        self.assertNotIn('LINK_IPSC', peer)
        self.assertEqual(cfg['OVERRIDE_IDENT_TG'], 0)

    def test_voice_ident_uses_connected_count_not_capacity(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        ident = source[source.index('def ident():'):source.index('def options_config():')]
        self.assertIn("_connected_peers = [", ident)
        self.assertNotIn("['MAX_PEERS'] > 1", ident)


class IpscSendHarness(IpscMasterMixin):

    def __init__(self):
        self._system = 'IPSC-1'
        self._ipsc_peers = {
            b'\x00\x00\x00\x01': {'host': '192.0.2.1', 'port': 50000},
            b'\x00\x00\x00\x02': {'host': '192.0.2.2', 'port': 50001},
        }
        self._voice = SimpleNamespace(
            _del_hbp_stream={1: None, 2: b'\x01\x02\x03\x04'})
        self.sent = []

    def _ipsc_send(self, packet, host, port):
        self.sent.append((packet, host, port))


def ipsc_voice_packet(burst_type=VOICE_HEAD):
    packet = bytearray(54)
    packet[0] = GROUP_VOICE
    packet[GV_CALL_INFO_OFF] = TS_CALL_MSK
    packet[GV_DST_GROUP_OFF:GV_DST_GROUP_OFF + 3] = b'\x00\x00\x5b'
    packet[GV_BURST_TYPE_OFF] = burst_type
    return bytes(packet)


class TestIpscDestinationControls(unittest.TestCase):

    @patch('ipsc_master.dest_peer_rx_blocked')
    def test_ipsc_fanout_skips_only_blocked_peer(self, blocked):
        blocked.side_effect = lambda _system, peer_id, *_args, **_kwargs: (
            peer_id == b'\x00\x00\x00\x01')
        harness = IpscSendHarness()

        harness._ipsc_send_voice(ipsc_voice_packet())

        self.assertEqual(
            [(host, port) for _packet, host, port in harness.sent],
            [('192.0.2.2', 50001)],
        )
        self.assertEqual(blocked.call_count, 2)
        self.assertEqual(blocked.call_args_list[0].args[2], b'\x01\x02\x03\x04')
        self.assertEqual(blocked.call_args_list[0].args[3:5], (2, 91))

    @patch('ipsc_master.release_dropped_stream')
    def test_ipsc_term_releases_target_stream(self, release):
        harness = IpscSendHarness()
        stream_id = b'\x10\x20\x30\x40'

        harness._ipsc_send_voice(
            ipsc_voice_packet(VOICE_TERM),
            (stream_id, 2, 2, 2),
        )

        release.assert_called_once_with('IPSC-1', stream_id, slot=2)


if __name__ == '__main__':
    unittest.main()
