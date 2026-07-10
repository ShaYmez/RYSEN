#!/usr/bin/env python3
"""Phase 4A — _forward_unit_voice() targeted routing (SUB_MAP, peer prefix, IPSC)."""
import sys
import unittest
from unittest.mock import MagicMock, ANY

# bridge_master imports setproctitle at module load
sys.modules.setdefault('setproctitle', MagicMock())

from dmr_utils3.utils import bytes_3  # noqa: E402

import bridge_master as bm  # noqa: E402


def _fake_voice_data(slot=1):
    bits = 0x40 | (0x80 if slot == 2 else 0)
    return b'\x00' * 15 + bits.to_bytes(1, 'big') + b'\x00' * 4


def _make_router(system_name):
    router = bm.routerHBP.__new__(bm.routerHBP)
    router._system = system_name
    router.send_peer = MagicMock()
    router.send_system = MagicMock()
    return router


def _make_ipsc_router(system_name):
    router = _make_router(system_name)
    router._voice = MagicMock()
    router._voice.handle_outbound = MagicMock(side_effect=lambda pkt: b'IPSC' + pkt)
    router._ipsc_send_voice_to_peer = MagicMock(return_value=True)
    return router


class TestForwardUnitVoice(unittest.TestCase):

    def setUp(self):
        self._saved_systems = getattr(bm, 'systems', None)
        self._saved_config = getattr(bm, 'CONFIG', None)
        self._saved_sub_map = getattr(bm, 'SUB_MAP', None)

        self.src = _make_router('SYSTEM-5')
        self.master_dst = _make_router('SYSTEM-6')
        self.ipsc_dst = _make_ipsc_router('IPSC-57')

        bm.systems = {
            'SYSTEM-5': self.src,
            'SYSTEM-6': self.master_dst,
            'IPSC-57': self.ipsc_dst,
        }
        bm.CONFIG = {
            'SYSTEMS': {
                'SYSTEM-5': {'MODE': 'MASTER', 'ENABLED': True},
                'SYSTEM-6': {'MODE': 'MASTER', 'ENABLED': True, 'PEERS': {}},
                'IPSC-57': {'MODE': 'IPSC', 'ENABLED': True, 'PEERS': {}},
            },
        }
        bm.SUB_MAP = {}
        for name, router in bm.systems.items():
            router.STATUS = {
                1: {'RX_TYPE': bm.HBPF_SLT_VTERM, 'TX_TYPE': bm.HBPF_SLT_VTERM},
                2: {'RX_TYPE': bm.HBPF_SLT_VTERM, 'TX_TYPE': bm.HBPF_SLT_VTERM},
            }

    def tearDown(self):
        if self._saved_systems is None:
            del bm.systems
        else:
            bm.systems = self._saved_systems
        if self._saved_config is None:
            del bm.CONFIG
        else:
            bm.CONFIG = self._saved_config
        if self._saved_sub_map is None:
            del bm.SUB_MAP
        else:
            bm.SUB_MAP = self._saved_sub_map

    def _forward(self, dst_id, slot=1, src_system='SYSTEM-5', peer_id=None):
        dst_b = bytes_3(dst_id)
        data = _fake_voice_data(slot)
        dmrpkt = b'\xab' * 33
        peer_id = peer_id or bytes_3(235287)
        stream_id = b'\x00\x00\x00\x01'
        bits = data[15]
        router = bm.systems[src_system]
        router._forward_unit_voice(
            dst_b, slot, bits, data, dmrpkt, stream_id, peer_id)
        return dmrpkt, data, bits, peer_id

    def test_sub_map_forwards_to_target_peer(self):
        dst = bytes_3(2348831)
        target_peer = (234883100).to_bytes(4, 'big')
        bm.SUB_MAP[dst] = ('SYSTEM-6', 2, None, 0, target_peer)
        dmrpkt, data, bits, _ = self._forward(2348831, slot=1)

        self.master_dst.send_peer.assert_called_once()
        peer_arg, payload = self.master_dst.send_peer.call_args[0]
        self.assertEqual(peer_arg, target_peer)
        self.assertEqual(payload[15], bits ^ (1 << 7))
        self.assertEqual(payload[20:], dmrpkt)
        self.master_dst.send_system.assert_not_called()
        self.ipsc_dst._ipsc_send_voice_to_peer.assert_not_called()

    def test_sub_map_invalid_entry_falls_through_to_peer(self):
        dst = bytes_3(2348831)
        bm.SUB_MAP[dst] = ('bad',)
        peer_id = bytes_3(2348831)
        bm.CONFIG['SYSTEMS']['SYSTEM-6']['PEERS'] = {peer_id: {}}

        self._forward(2348831)

        self.master_dst.send_peer.assert_called_once_with(peer_id, ANY)
        self.ipsc_dst._ipsc_send_voice_to_peer.assert_not_called()

    def test_master_peer_prefix_match_uses_slot_two(self):
        peer_id = bytes_3(2348831)
        bm.CONFIG['SYSTEMS']['SYSTEM-6']['PEERS'] = {peer_id: {}}

        dmrpkt, data, bits, _ = self._forward(2348831, slot=1)

        self.master_dst.send_peer.assert_called_once()
        payload = self.master_dst.send_peer.call_args[0][1]
        self.assertEqual(payload[15], bits ^ (1 << 7))

    def test_nine_digit_hotspot_matches_seven_digit_destination(self):
        peer_id = (234883100).to_bytes(4, 'big')
        bm.CONFIG['SYSTEMS']['SYSTEM-6']['PEERS'] = {peer_id: {}}

        self._forward(2348831)

        self.master_dst.send_peer.assert_called_once_with(peer_id, ANY)

    def test_ipsc_peer_match_from_master_source(self):
        peer_id = bytes_3(2348831)
        bm.CONFIG['SYSTEMS']['IPSC-57']['PEERS'] = {peer_id: {}}

        self._forward(2348831)

        self.ipsc_dst._ipsc_send_voice_to_peer.assert_called_once()
        self.master_dst.send_peer.assert_not_called()

    def test_ipsc_source_skips_second_ipsc_scan(self):
        bm.systems['IPSC-198'] = _make_ipsc_router('IPSC-198')
        bm.CONFIG['SYSTEMS']['IPSC-198'] = {'MODE': 'IPSC', 'ENABLED': True, 'PEERS': {}}
        peer_id = bytes_3(2348831)
        bm.CONFIG['SYSTEMS']['IPSC-57']['PEERS'] = {peer_id: {}}

        dst_b = bytes_3(2348831)
        data = _fake_voice_data(1)
        dmrpkt = b'\xcd' * 33
        bm.systems['IPSC-198']._forward_unit_voice(
            dst_b, 1, data[15], data, dmrpkt, b'\x00\x00\x00\x02', bytes_3(235287))

        self.ipsc_dst._ipsc_send_voice_to_peer.assert_called_once()
        bm.systems['IPSC-198']._ipsc_send_voice_to_peer.assert_not_called()

    def test_no_forward_when_no_route(self):
        self._forward(2348831)
        self.master_dst.send_peer.assert_not_called()
        self.ipsc_dst._ipsc_send_voice_to_peer.assert_not_called()

    def test_no_forward_to_same_peer(self):
        dst = bytes_3(2348831)
        peer = bytes_3(2348831)
        bm.SUB_MAP[dst] = ('SYSTEM-5', 1, None, 0, peer)

        self._forward(2348831, peer_id=peer)

        self.src.send_peer.assert_not_called()
        self.master_dst.send_peer.assert_not_called()

    def test_same_master_different_peer_forwarded(self):
        dst = bytes_3(2348831)
        caller = bytes_3(235287)
        callee = bytes_3(2348831)
        bm.CONFIG['SYSTEMS']['SYSTEM-5']['PEERS'] = {caller: {}, callee: {}}

        self._forward(2348831, peer_id=caller)

        self.src.send_peer.assert_called_once_with(callee, ANY)

    def test_sub_map_preferred_over_peer(self):
        dst = bytes_3(2348831)
        target_peer = (234883100).to_bytes(4, 'big')
        bm.SUB_MAP[dst] = ('SYSTEM-6', 1, None, 0, target_peer)
        bm.CONFIG['SYSTEMS']['IPSC-57']['PEERS'] = {dst: {}}

        self._forward(2348831)

        self.master_dst.send_peer.assert_called_once()
        self.ipsc_dst._ipsc_send_voice_to_peer.assert_not_called()

    def test_disabled_ipsc_not_used_from_master_source(self):
        peer_id = bytes_3(2348831)
        bm.CONFIG['SYSTEMS']['IPSC-57']['PEERS'] = {peer_id: {}}
        bm.CONFIG['SYSTEMS']['IPSC-57']['ENABLED'] = False

        self._forward(2348831)

        self.ipsc_dst._ipsc_send_voice_to_peer.assert_not_called()

    def test_disabled_master_not_used_in_peer_scan(self):
        peer_id = bytes_3(2348831)
        bm.CONFIG['SYSTEMS']['SYSTEM-6']['PEERS'] = {peer_id: {}}
        bm.CONFIG['SYSTEMS']['SYSTEM-6']['ENABLED'] = False

        self._forward(2348831)

        self.master_dst.send_peer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
