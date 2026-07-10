#!/usr/bin/env python3
"""End-to-end scenario tests before field deploy.

Covers:
  1. Private call to 5000 with no link → "not linked" announcement, no unit forward
  2. Two hotspots on one MASTER → private call to 2345875 routes to the other peer
"""
import sys
import unittest
from time import time
from unittest.mock import MagicMock, patch, ANY

sys.modules.setdefault('setproctitle', MagicMock())

from dmr_utils3.utils import bytes_3  # noqa: E402

import bridge_master as bm  # noqa: E402
import bridge_unit_delivery as bud  # noqa: E402
from const import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM  # noqa: E402


def _slot_status():
    return {
        'RX_START': 0,
        'RX_SEQ': 0,
        'RX_RFS': b'\x00',
        'RX_PEER': b'\x00',
        'RX_STREAM_ID': b'\x00',
        'RX_TGID': b'\x00\x00\x00',
        'RX_TIME': 0,
        'RX_TYPE': HBPF_SLT_VTERM,
        'TX_TYPE': HBPF_SLT_VTERM,
        'TX_TIME': 0,
        'packets': 0,
        'loss': 0,
        'crcs': set(),
        'lastData': False,
        'lastSeq': False,
        '_allStarMode': False,
        '_stopTgAnnounce': False,
        '_reflect_announced': None,
    }


def _dmrd_data(slot=2, unit=True, stream=b'\x00\x00\x00\x10'):
    bits = 0x40 | (0x80 if slot == 2 else 0)
    if not unit:
        bits &= ~0x40
    return b'\x00' * 15 + bits.to_bytes(1, 'big') + stream + b'\xcc' * 33 + b'\x00\x00'


def _make_master_router(system_name='GB7NR'):
    router = bm.routerHBP.__new__(bm.routerHBP)
    router._system = system_name
    router._config = bm.CONFIG['SYSTEMS'][system_name]
    router._CONFIG = bm.CONFIG
    router._report = MagicMock()
    router.send_peer = MagicMock()
    router.send_system = MagicMock()
    router._peers = {}
    router._cancel_reflector_timers = MagicMock()
    router._schedule_reflector_fallback = MagicMock()
    router.STATUS = {1: _slot_status(), 2: _slot_status()}
    return router


class TestDial5000NotLinked(unittest.TestCase):
    """Private call to 5000 when no reflector is linked."""

    def setUp(self):
        self._saved = {k: getattr(bm, k, None) for k in ('BRIDGES', 'CONFIG', 'SUB_MAP', 'words')}
        bm.BRIDGES = {}
        bm.SUB_MAP = {}
        bm.words = {
            'en_GB': {
                'silence': 'silence',
                'notlinked': 'not-linked',
                'linkedto': 'linked-to',
                'to': 'to',
                'busy': 'busy',
            },
        }
        bm.CONFIG = {
            'GLOBAL': {'SERVER_ID': b'\x00\x00\x00\x01'},
            'SYSTEMS': {
                'GB7NR': {
                    'MODE': 'MASTER',
                    'ENABLED': True,
                    'DEFAULT_UA_TIMER': 300,
                    'ANNOUNCEMENT_LANGUAGE': 'en_GB',
                },
            },
            'REPORTS': {'REPORT': False},
            'ALLSTAR': {'ENABLED': False},
        }
        self.router = _make_master_router()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                if hasattr(bm, k):
                    delattr(bm, k)
            else:
                setattr(bm, k, v)

    def test_build_announce_say_not_linked_when_no_bridge(self):
        say = self.router._build_reflector_announce_say(5000, 2, 'en_GB')
        self.assertIsNotNone(say)
        self.assertIn('not-linked', say)

    @patch.object(bm.routerHBP, '_play_reflector_announcement')
    @patch.object(bm.routerHBP, '_forward_unit_voice')
    def test_private_5000_vterm_plays_not_linked_not_unit_forward(self, mock_forward, mock_play):
        """Full dmrd path: VHEAD then VTERM on private call to 5000."""
        dst = bytes_3(5000)
        src = bytes_3(2352880)
        peer = bytes_3(2352880)
        stream = b'\x00\x00\x00\x20'

        # Voice header — new stream
        self.router.dmrd_received(
            peer, src, dst, 1, 2, 'unit', HBPF_DATA_SYNC, HBPF_SLT_VHEAD,
            stream, _dmrd_data(2, stream=stream))

        mock_forward.assert_not_called()

        # Simulate active RX so VTERM branch fires
        self.router.STATUS[2]['RX_TYPE'] = HBPF_SLT_VHEAD

        self.router.dmrd_received(
            peer, src, dst, 2, 2, 'unit', HBPF_DATA_SYNC, HBPF_SLT_VTERM,
            stream, _dmrd_data(2, stream=stream))

        mock_forward.assert_not_called()
        mock_play.assert_called_once()
        say_arg = mock_play.call_args[0][0]
        self.assertIn('not-linked', say_arg)


class TestTwoHotspotUnitRoute(unittest.TestCase):
    """Hotspot A private-calls subscriber 2345875 registered on hotspot B (same MASTER)."""

    CALLEE_SUB = 2345875
    CALLEE_PEER = (234587500).to_bytes(4, 'big')
    CALLER_SUB = 2352880
    CALLER_PEER = (235288000).to_bytes(4, 'big')
    SYSTEM = 'GB7NR'

    def setUp(self):
        self._saved = {
            'systems': getattr(bm, 'systems', None),
            'config': getattr(bm, 'CONFIG', None),
            'sub_map': getattr(bm, 'SUB_MAP', None),
            'subscriber_ids': getattr(bm, 'subscriber_ids', None),
            'peer_ids': getattr(bm, 'peer_ids', None),
            'talkgroup_ids': getattr(bm, 'talkgroup_ids', None),
        }
        bm.CONFIG = {
            'GLOBAL': {'SERVER_ID': b'\x00\x00\x00\x01'},
            'SYSTEMS': {
                self.SYSTEM: {
                    'MODE': 'MASTER',
                    'ENABLED': True,
                    'DEFAULT_UA_TIMER': 300,
                    'ANNOUNCEMENT_LANGUAGE': 'en_GB',
                    'PEERS': {
                        self.CALLER_PEER: {},
                        self.CALLEE_PEER: {},
                    },
                },
            },
            'REPORTS': {'REPORT': False},
            'ALLSTAR': {'ENABLED': False},
        }
        bm.SUB_MAP = {}
        bm.BRIDGES = {}
        bm.subscriber_ids = {}
        bm.peer_ids = {}
        bm.talkgroup_ids = {}
        self.router = _make_master_router(self.SYSTEM)
        self.router._peers = {
            self.CALLER_PEER: {'CONNECTION': 'YES', 'SOCKADDR': ('10.0.0.1', 62031)},
            self.CALLEE_PEER: {'CONNECTION': 'YES', 'SOCKADDR': ('10.0.0.2', 62032)},
        }
        bm.systems = {self.SYSTEM: self.router}

    def tearDown(self):
        for key, attr in (
            ('systems', 'systems'), ('config', 'CONFIG'), ('sub_map', 'SUB_MAP'),
            ('subscriber_ids', 'subscriber_ids'), ('peer_ids', 'peer_ids'),
            ('talkgroup_ids', 'talkgroup_ids'),
        ):
            val = self._saved[key]
            if val is None:
                if hasattr(bm, attr):
                    delattr(bm, attr)
            else:
                setattr(bm, attr, val)

    def test_callee_keyup_updates_sub_map_then_caller_routes(self):
        """Callee 2345875 keys up (group PTT) → SUB_MAP → caller private call delivers to callee peer."""
        callee_src = bytes_3(self.CALLEE_SUB)
        caller_src = bytes_3(self.CALLER_SUB)
        dst_callee = bytes_3(self.CALLEE_SUB)
        tg9 = bytes_3(9)
        stream_b = b'\x00\x00\x00\x30'

        # Callee keys up on TG9 (group) — updates SUB_MAP like real RX
        with patch.object(self.router, '_cancel_reflector_timers'), \
             patch.object(self.router, '_schedule_reflector_fallback'):
            self.router.dmrd_received(
                self.CALLEE_PEER, callee_src, tg9, 1, 2, 'group',
                HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream_b,
                _dmrd_data(2, unit=False, stream=stream_b))

        self.assertIn(callee_src, bm.SUB_MAP)
        self.assertEqual(bm.SUB_MAP[callee_src][0], self.SYSTEM)
        self.assertEqual(bm.SUB_MAP[callee_src][4], self.CALLEE_PEER)

        # Caller private-calls 2345875
        stream_a = b'\x00\x00\x00\x31'
        dst_b = bytes_3(self.CALLEE_SUB)
        with patch.object(self.router, '_cancel_reflector_timers'), \
             patch.object(self.router, '_schedule_reflector_fallback'), \
             patch.object(self.router, '_play_reflector_announcement'):
            self.router.dmrd_received(
                self.CALLER_PEER, caller_src, dst_b, 1, 2, 'unit',
                HBPF_DATA_SYNC, HBPF_SLT_VHEAD, stream_a,
                _dmrd_data(2, stream=stream_a))

        self.router.send_peer.assert_called()
        peer_arg = self.router.send_peer.call_args[0][0]
        self.assertEqual(peer_arg, self.CALLEE_PEER)

    def test_login_seed_routes_before_callee_keys_up(self):
        """Peer login seeds SUB_MAP — private call works even if callee has not keyed up yet."""
        bud.seed_sub_map_for_peer(bm.SUB_MAP, self.SYSTEM, self.CALLEE_PEER)
        callee_key = bytes_3(self.CALLEE_SUB)
        self.assertIn(callee_key, bm.SUB_MAP)

        dest = bud.resolve_unit_destination_local(
            callee_key,
            config=bm.CONFIG,
            sub_map=bm.SUB_MAP,
            systems=bm.systems,
            source_system=self.SYSTEM,
            source_mode='MASTER',
            source_peer_id=self.CALLER_PEER,
        )
        self.assertIsNotNone(dest)
        self.assertEqual(dest.peer_id, self.CALLEE_PEER)
        self.assertEqual(dest.slot, 2)

        data = _dmrd_data(2)
        dmrpkt = data[20:53]
        bits = data[15]
        sent = bud.deliver_unit_voice(
            dest,
            systems=bm.systems,
            config=bm.CONFIG,
            source_system=self.SYSTEM,
            slot=2,
            bits=bits,
            data=data,
            dmrpkt=dmrpkt,
            source_peer_id=self.CALLER_PEER,
        )
        self.assertTrue(sent)
        self.router.send_peer.assert_called_once_with(self.CALLEE_PEER, ANY)

    def test_reverse_direction_both_ways(self):
        """After both have keyed up, B can private-call A too."""
        bm.SUB_MAP[bytes_3(self.CALLEE_SUB)] = (
            self.SYSTEM, 2, None, time(), self.CALLEE_PEER)
        bm.SUB_MAP[bytes_3(self.CALLER_SUB)] = (
            self.SYSTEM, 2, None, time(), self.CALLER_PEER)

        dest = bud.resolve_unit_destination_local(
            bytes_3(self.CALLER_SUB),
            config=bm.CONFIG,
            sub_map=bm.SUB_MAP,
            systems=bm.systems,
            source_system=self.SYSTEM,
            source_mode='MASTER',
            source_peer_id=self.CALLEE_PEER,
        )
        self.assertEqual(dest.peer_id, self.CALLER_PEER)


if __name__ == '__main__':
    unittest.main()
