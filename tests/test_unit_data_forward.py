#!/usr/bin/env python3
"""Unit data forward path — OBP SUB_MAP busy gate and peer fallback."""
import sys
import unittest
from time import time
from unittest.mock import MagicMock

sys.modules.setdefault('setproctitle', MagicMock())

from dmr_utils3.utils import bytes_3  # noqa: E402

import bridge_master as bm  # noqa: E402
from const import HBPF_SLT_VTERM  # noqa: E402


def _idle_slot():
    return {
        'RX_TYPE': HBPF_SLT_VTERM,
        'TX_TYPE': HBPF_SLT_VTERM,
        'TX_TIME': 0,
        'RX_STREAM_ID': b'\x00',
    }


def _busy_slot():
    return {
        'RX_TYPE': 0x1,
        'TX_TYPE': HBPF_SLT_VTERM,
        'TX_TIME': 0,
        'RX_STREAM_ID': b'\x00',
    }


class TestUnitDataForward(unittest.TestCase):

    def setUp(self):
        self._saved = {
            'systems': getattr(bm, 'systems', None),
            'config': getattr(bm, 'CONFIG', None),
            'sub_map': getattr(bm, 'SUB_MAP', None),
            'subscriber_ids': getattr(bm, 'subscriber_ids', None),
            'peer_ids': getattr(bm, 'peer_ids', None),
            'talkgroup_ids': getattr(bm, 'talkgroup_ids', None),
        }
        self.router = bm.routerOBP.__new__(bm.routerOBP)
        self.router._system = 'OBP-1'
        self.router._report = MagicMock()
        self.router.get_rptr = MagicMock(return_value='TEST')
        self.router.sendDataToHBP = MagicMock()
        self.dst_router = MagicMock()
        self.dst_router.STATUS = {1: _idle_slot(), 2: _idle_slot()}
        bm.systems = {'OBP-1': self.router, 'SYSTEM-6': self.dst_router}
        bm.CONFIG = {
            'GLOBAL': {'DATA_GATEWAY': False},
            'SYSTEMS': {
                'OBP-1': {'MODE': 'OPENBRIDGE', 'VER': 2, 'GROUP_HANGTIME': 1},
                'SYSTEM-6': {'MODE': 'MASTER', 'ENABLED': True, 'GROUP_HANGTIME': 1, 'PEERS': {}},
            },
            'REPORTS': {'REPORT': False},
        }
        bm.SUB_MAP = {}
        bm.subscriber_ids = {}
        bm.peer_ids = {}
        bm.talkgroup_ids = {}
        self.router.STATUS = {}

    def tearDown(self):
        for key, val in self._saved.items():
            attr = {
                'systems': 'systems',
                'config': 'CONFIG',
                'sub_map': 'SUB_MAP',
                'subscriber_ids': 'subscriber_ids',
                'peer_ids': 'peer_ids',
                'talkgroup_ids': 'talkgroup_ids',
            }[key]
            if val is None:
                if hasattr(bm, attr):
                    delattr(bm, attr)
            else:
                setattr(bm, attr, val)

    def _run_sub_map_path(self, busy=False):
        dst = bytes_3(2348831)
        bm.SUB_MAP[dst] = ('SYSTEM-6', 2, None, time(), None)
        if busy:
            self.dst_router.STATUS[2] = _busy_slot()
        slot = 1
        bits = 0x40
        data = b'\x00' * 15 + bits.to_bytes(1, 'big') + b'\x00' * 4 + b'\x00' * 33
        dmrpkt = b'\xaa' * 33
        stream_id = b'\x00\x00\x00\x01'
        peer_id = bytes_3(235287)
        rf_src = bytes_3(235288)
        self.router.STATUS = {}
        # OBP unit data branch (dtype 6 = data header)
        self.router.dmrd_received(
            peer_id, rf_src, dst, 1, slot, 'unit', 0x2, 6, stream_id,
            data[:20] + dmrpkt + b'\x00\x00', b'\x00' * 16)

    def test_sub_map_forwards_when_slot_idle(self):
        self._run_sub_map_path(busy=False)
        self.router.sendDataToHBP.assert_called_once()

    def test_sub_map_skips_when_slot_busy(self):
        self._run_sub_map_path(busy=True)
        self.router.sendDataToHBP.assert_not_called()


if __name__ == '__main__':
    unittest.main()
