#!/usr/bin/env python3
"""dmrd_received integration — unit voice triggers targeted forward, not reflector."""
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.modules.setdefault('setproctitle', MagicMock())

from dmr_utils3.utils import bytes_3  # noqa: E402

import bridge_master as bm  # noqa: E402
from const import HBPF_DATA_SYNC, HBPF_SLT_VHEAD  # noqa: E402


def _slot_status():
    return {
        'RX_START': 0,
        'RX_SEQ': 0,
        'RX_RFS': b'\x00',
        'RX_PEER': b'\x00',
        'RX_STREAM_ID': b'\x00',
        'RX_TGID': b'\x00\x00\x00',
        'RX_TIME': 0,
        'RX_TYPE': 0x2,
        'TX_TYPE': 0x2,
        'TX_TIME': 0,
        'packets': 0,
        'crcs': set(),
        '_allStarMode': False,
        '_stopTgAnnounce': False,
        '_reflect_announced': None,
    }


def _dmrd_payload(slot=1, unit=True):
    bits = 0x40 | (0x80 if slot == 2 else 0)
    if not unit:
        bits = bits & ~0x40
    return b'\x00' * 15 + bits.to_bytes(1, 'big') + b'\x00\x00\x00\x01' + b'\xcc' * 33 + b'\x00\x00'


class TestDmrdUnitVoiceIntegration(unittest.TestCase):

    def setUp(self):
        sys.modules.setdefault('setproctitle', MagicMock())
        self._saved_bridges = getattr(bm, 'BRIDGES', None)
        self._saved_config = getattr(bm, 'CONFIG', None)
        self._saved_sub_map = getattr(bm, 'SUB_MAP', None)
        bm.BRIDGES = {}
        bm.SUB_MAP = {}
        bm.CONFIG = {
            'GLOBAL': {'SERVER_ID': b'\x00\x00\x00\x01'},
            'SYSTEMS': {
                'SYSTEM-5': {
                    'MODE': 'MASTER', 'ENABLED': True, 'DEFAULT_UA_TIMER': 300,
                    'ANNOUNCEMENT_LANGUAGE': 'en',
                },
            },
            'REPORTS': {'REPORT': False},
            'ALLSTAR': {'ENABLED': False},
        }
        self.router = bm.routerHBP.__new__(bm.routerHBP)
        self.router._system = 'SYSTEM-5'
        self.router._config = bm.CONFIG['SYSTEMS']['SYSTEM-5']
        self.router._CONFIG = bm.CONFIG
        self.router._report = MagicMock()
        self.router.STATUS = {1: _slot_status(), 2: _slot_status()}
        self.router._cancel_reflector_timers = MagicMock()
        self.router._schedule_reflector_fallback = MagicMock()
        self.router._build_reflector_announce_say = MagicMock(return_value=None)

    def tearDown(self):
        if self._saved_bridges is None:
            del bm.BRIDGES
        else:
            bm.BRIDGES = self._saved_bridges
        if self._saved_config is None:
            del bm.CONFIG
        else:
            bm.CONFIG = self._saved_config
        if self._saved_sub_map is None:
            del bm.SUB_MAP
        else:
            bm.SUB_MAP = self._saved_sub_map

    @patch.object(bm.routerHBP, '_forward_unit_voice')
    def test_subscriber_private_voice_invokes_forward(self, mock_forward):
        dst = bytes_3(2348831)
        src = bytes_3(235287)
        peer = bytes_3(235288)
        data = _dmrd_payload()
        self.router.dmrd_received(
            peer, src, dst, 1, 1, 'unit', HBPF_DATA_SYNC, HBPF_SLT_VHEAD,
            b'\x00\x00\x00\x02', data)
        mock_forward.assert_called_once()

    @patch.object(bm.routerHBP, '_forward_unit_voice')
    @patch.object(bm, 'make_single_reflector')
    def test_dial_tg_private_does_not_forward(self, mock_make, mock_forward):
        dst = bytes_3(2350)
        src = bytes_3(235287)
        peer = bytes_3(235288)
        data = _dmrd_payload()
        self.router.dmrd_received(
            peer, src, dst, 1, 1, 'unit', HBPF_DATA_SYNC, HBPF_SLT_VHEAD,
            b'\x00\x00\x00\x03', data)
        mock_forward.assert_not_called()


if __name__ == '__main__':
    unittest.main()
