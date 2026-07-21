#!/usr/bin/env python3
"""Sticky mid-QSO stretch: HBP LC regen gate + soft-peer TX mute."""
import re
import time
import unittest

from bridge_helpers import (
    HBP_PEER_TX_MUTE_S,
    hbp_peer_is_slot_rx_owner,
)


class TestHbpLcRegenGate(unittest.TestCase):
    """HBP must regen TX LCs when target TX_STREAM_ID changes (match OBP)."""

    def test_bridge_master_uses_target_tx_stream_id(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        # The buggy source-RX gate must be gone from the HBP LC regen site.
        self.assertIsNone(re.search(
            r"if \(_stream_id != self\.STATUS\[_slot\]\['RX_STREAM_ID'\]\):\s*\n"
            r"\s+cancel_generated_voice\(_target_status",
            source,
        ))
        self.assertIsNotNone(re.search(
            r"if \(_target_status\[_target\['TS'\]\]\['TX_STREAM_ID'\] != _stream_id\):\s*\n"
            r"\s+cancel_generated_voice\(_target_status",
            source,
        ))

    def test_regen_predicate_independent_of_source_rx(self):
        # Simulate: source RX_STREAM_ID already equals new stream (post-assign),
        # but target still holds an older TX_STREAM_ID → must regenerate.
        source_rx = (0xAABBCCDD).to_bytes(4, 'big')
        new_stream = source_rx
        old_tx = (0x11223344).to_bytes(4, 'big')
        self.assertEqual(new_stream, source_rx)
        self.assertNotEqual(old_tx, new_stream)
        # OBP/HBP fixed gate:
        self.assertTrue(old_tx != new_stream)


class TestHbpPeerTxMute(unittest.TestCase):

    def _slot(self, peer, rfs=None, rx_type=0x0, rx_time=None):
        return {
            'RX_PEER': peer,
            'RX_RFS': rfs or peer[-3:],
            'RX_TYPE': rx_type,
            'RX_TIME': rx_time if rx_time is not None else time.time(),
        }

    def test_active_rx_owner_muted(self):
        peer = (234587567).to_bytes(4, 'big')
        other = (234587568).to_bytes(4, 'big')
        slot = self._slot(peer, rx_type=0x0)
        self.assertTrue(hbp_peer_is_slot_rx_owner(slot, peer))
        self.assertFalse(hbp_peer_is_slot_rx_owner(slot, other))

    def test_vterm_within_mute_window(self):
        peer = (234587567).to_bytes(4, 'big')
        now = 1000.0
        slot = self._slot(peer, rx_type=0x2, rx_time=now - 0.1)
        self.assertTrue(hbp_peer_is_slot_rx_owner(slot, peer, now=now))
        slot['RX_TIME'] = now - (HBP_PEER_TX_MUTE_S + 0.05)
        self.assertFalse(hbp_peer_is_slot_rx_owner(slot, peer, now=now))

    def test_rfs_match_on_3byte_tail(self):
        peer = (234587567).to_bytes(4, 'big')
        slot = {
            'RX_PEER': b'\x00\x00\x00\x00',
            'RX_RFS': peer[-3:],
            'RX_TYPE': 0x1,
            'RX_TIME': time.time(),
        }
        self.assertTrue(hbp_peer_is_slot_rx_owner(slot, peer))

    def test_send_peers_imports_mute_helper(self):
        with open('hblink.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('hbp_peer_is_slot_rx_owner', source)
        self.assertIn('hbp_peer_is_slot_rx_owner(_slot_status, _peer, _now)', source)


if __name__ == '__main__':
    unittest.main()
