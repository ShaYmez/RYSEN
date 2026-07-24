#!/usr/bin/env python3
"""Source-level guards for soft-client stretch hot-path fixes."""
import unittest


class TestObpActivateUaCallStartOnly(unittest.TestCase):

    def test_obp_activate_gated_on_new_stream(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        # New-stream flag on OBP group path
        self.assertIn('_obp_new_stream = (_stream_id not in self.STATUS)', source)
        # Activate must be gated — not bare per-packet call after STAT create
        self.assertIn('if _obp_new_stream:', source)
        self.assertIn('activate_ua_bridge_source(str(_int_dst), self._system, _slot, peer_id=_peer_id)', source)
        # Ensure the ungated OBP comment/call pattern is gone
        self.assertNotIn(
            '# Activate this OBP leg on an existing conference bridge (same as HBP on call start)',
            source)


class TestBridgeIdxMissThrottle(unittest.TestCase):

    def test_throttle_helper_and_hot_paths(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('_BRIDGE_IDX_REBUILD_MIN_INTERVAL_S', source)
        self.assertIn('def _maybe_rebuild_bridge_index_on_miss', source)
        self.assertIn('rebuild throttled', source)
        # Both OBP and HBP miss paths use the helper (not raw rebuild alone)
        self.assertGreaterEqual(source.count('_maybe_rebuild_bridge_index_on_miss('), 2)


class TestProxyReaperMstclAndRxTimer(unittest.TestCase):

    def test_sc_reaper_sends_mstcl(self):
        with open('hotspot_proxy_v2_sc.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("self.transport.write(b'MSTCL'", source)
        self.assertGreaterEqual(source.count("b'MSTCL'"), 3)

    def test_master_dmrd_resets_idle_timer(self):
        with open('hotspot_proxy_v2_sc.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('Keep RX-heavy soft clients alive', source)
        self.assertIn('_timer.reset(self.timeout)', source)


class TestReportingErrback(unittest.TestCase):

    def test_reporting_loop_has_errback(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('addErrback(_reporting_errback)', source)
        self.assertIn('(DIAGNOSTICS) reporting_loop took', source)


class TestObpEmptyFiOwner(unittest.TestCase):

    def test_empty_fi_continues_as_owner_not_hard_drop(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('fi is empty; treating this system as owner', source)
        self.assertGreaterEqual(source.count('fi is empty; treating this system as owner'), 2)
        # Hard-drop on empty fi must be gone (group + unit)
        self.assertNotIn('fi is empty for some reason', source)
        self.assertIn('elif self._system != fi:', source)


if __name__ == '__main__':
    unittest.main()
