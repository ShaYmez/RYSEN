#!/usr/bin/env python3
"""Source-level guards for soft-client stretch hot-path fixes."""
import unittest


class TestObpActivateUaCallStartOnly(unittest.TestCase):

    def test_obp_activate_gated_on_new_stream_or_stub_claim(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        # New-stream flag on OBP group path
        self.assertIn('_obp_previous = self.STATUS.get(_stream_id)', source)
        self.assertIn('_obp_new_stream = (', source)
        # Activate on first inbound claim (new stream or stub missing 1ST) — not every frame
        self.assertIn('_obp_ua_arm', source)
        self.assertIn('if _obp_ua_arm:', source)
        self.assertIn('activate_ua_bridge_source(str(_int_dst), self._system, _slot, peer_id=_peer_id)', source)
        # Must not gate solely on brand-new STATUS (outbound stub race)
        self.assertNotIn('if _obp_new_stream:\n                _int_dst = int_id(_dst_id)', source)
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
        # Miss paths + stale hit paths use the helper
        self.assertGreaterEqual(source.count('_maybe_rebuild_bridge_index_on_miss('), 4)


class TestProxyReaperMstclAndRxTimer(unittest.TestCase):

    def test_sc_reaper_sends_mstcl(self):
        with open('hotspot_proxy_v2_sc.py', encoding='utf-8') as fh:
            source = fh.read()
        # HBP logout must include radio ID (bare MSTCL is ignored by many gateways)
        self.assertIn("_mstcl = b'MSTCL' + _peer_id", source)
        self.assertGreaterEqual(source.count('self.transport.write(_mstcl,'), 3)
        self.assertNotIn("self.transport.write(b'MSTCL', (_peer['shost'], _peer['sport']))", source)
        self.assertIn('notify_client=True', source)
        self.assertIn('notify_client=False', source)

    def test_orphan_rptping_silent_no_slot(self):
        for path in ('hotspot_proxy_v2.py', 'hotspot_proxy_v2_sc.py'):
            with open(path, encoding='utf-8') as fh:
                source = fh.read()
            self.assertIn('Orphan keepalive after master/proxy restart', source)
            self.assertIn('(no reply).', source)
            # Must not synthesize MSTNAK/MSTCL on unknown RPTPING
            orphan = source[source.index('Orphan keepalive after master/proxy restart'):
                            source.index('# Make a list with the available ports')]
            self.assertNotIn('MSTNAK', orphan)
            self.assertNotIn('MSTCL', orphan)

    def test_hblink_orphan_ping_silent(self):
        with open('hblink.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('Ping from Radio ID that is not logged in (no reply)', source)
        orphan = source[source.index('elif _command == RPTP:'):
                        source.index('elif _command == DMRA:')]
        self.assertIn('no MSTPONG / MSTNAK', orphan)
        self.assertNotIn("join([MSTNAK, _peer_id])", orphan)
        self.assertNotIn("join([MSTCL, _peer_id])", orphan)
        self.assertNotIn("join([MSTPONG, _peer_id])", orphan.split('else:')[1])

    def test_proxy_forwards_mstnak_before_drop(self):
        for path in ('hotspot_proxy_v2.py', 'hotspot_proxy_v2_sc.py'):
            with open(path, encoding='utf-8') as fh:
                source = fh.read()
            self.assertIn('notify_client=False', source)
            self.assertIn('forward MSTNAK', source)

    def test_master_dmrd_resets_idle_timer(self):
        with open('hotspot_proxy_v2_sc.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('Keep RX-heavy soft clients alive', source)
        self.assertIn('_timer.reset(self.timeout)', source)

    def test_mid_login_mstnak_skips_cleanup(self):
        with open('hotspot_proxy_v2_sc.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("('RPTL_SENT', 'CHALLENGE_SENT', 'WAITING_CONFIG',", source)
        self.assertIn("'AUTH_ACKED', 'CONFIG_SENT')", source)
        self.assertIn('Mid-login NAKs', source)
        self.assertIn("['CONNECTION'] = 'RPTL_SENT'", source)
        self.assertIn("['CONNECTION'] = 'WAITING_CONFIG'", source)
        self.assertIn("['CONNECTION'] = 'CHALLENGE_SENT'", source)
        self.assertIn("['CONNECTION'] = 'CONFIG_SENT'", source)

    def test_looping_errback_no_reactor_stop(self):
        with open('hotspot_proxy_v2_sc.py', encoding='utf-8') as fh:
            source = fh.read()
        # Signal handler may still stop reactor; LoopingCall errback must not
        self.assertIn('def loopingErrHandle(failure):', source)
        # Extract errback body between def and next top-level-ish block
        start = source.index('def loopingErrHandle(failure):')
        block = source[start:start + 200]
        self.assertNotIn('reactor.stop()', block)
        self.assertIn('Unhandled error in timed loop', block)


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
        # Empty-fi log must not reference unbound _sysslot
        self.assertNotIn('treating this system as owner. STREAM ID: %s, TG: %s, TS: %s",self._system, int_id(_stream_id), int_id(_dst_id),_sysslot)', source)
        self.assertIn('treating this system as owner. STREAM ID: %s, TG: %s, TS: %s",self._system, int_id(_stream_id), int_id(_dst_id),_slot)', source)

    def test_obp_stub_hardens_lc_1st_counters(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("setdefault('LC', b''.join([LC_OPT,_dst_id,_rf_src]))", source)
        self.assertIn("setdefault('1ST', perf_counter())", source)
        self.assertIn('Outbound OBP stubs may lack inbound LC/1ST/counters', source)
        # Catch-up bursts must not be dropped after reactor stalls.
        self.assertNotIn('*PacketControl* RATE DROP!', source)
        # Outbound OBP STATUS stubs carry packet counters
        self.assertGreaterEqual(source.count("'packets': 0,"), 4)

    def test_call_end_guards_zero_packets(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if call_duration and _obp_pkts:', source)

    def test_rule_timer_on_running_is_debug(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn(
            "logger.debug('(ROUTER) Conference Bridge ACTIVE (ON timer running):",
            source)
        self.assertNotIn(
            "logger.info('(ROUTER) Conference Bridge ACTIVE (ON timer running):",
            source)


if __name__ == '__main__':
    unittest.main()
