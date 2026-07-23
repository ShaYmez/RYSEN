#!/usr/bin/env python3
"""Source tests: reactor-safe bridge timers + KA offload + coalesced selfcare + parrot trim.

Bridge mutators (rule_timer_loop / statTrimmer) MUST run on the Twisted reactor.
deferToThread raced BRIDGE_IDX/BRIDGES with UDP handlers and crashed hosts with:
  RuntimeError: dictionary changed size during iteration → STOPPING REACTOR.
"""
import re
import unittest

from bridge_helpers import OPTIONS_CONFIG_COALESCE_S, is_parrot_bridge


class TestReactorOffloadWiring(unittest.TestCase):

    def test_coalesce_constant(self):
        self.assertEqual(OPTIONS_CONFIG_COALESCE_S, 5.0)

    def test_bridge_mutators_run_on_reactor(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        # On-reactor LoopingCall — no thread wrappers for mutators
        self.assertIn('task.LoopingCall(rule_timer_loop)', source)
        self.assertIn('task.LoopingCall(statTrimmer)', source)
        self.assertNotIn('deferToThread(rule_timer_loop)', source)
        self.assertNotIn('deferToThread(statTrimmer)', source)
        self.assertNotIn('_rule_timer_in_thread', source)
        self.assertNotIn('_stat_trimmer_in_thread', source)

    def test_ka_reporting_stays_off_reactor(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('from twisted.internet import reactor, task, threads', source)
        self.assertIn('threads.deferToThread(kaReporting)', source)
        self.assertIn('task.LoopingCall(_ka_reporting_in_thread)', source)
        self.assertNotIn('task.LoopingCall(kaReporting)', source)

    def test_mutator_report_notify_is_direct(self):
        """On-reactor timers must not use callFromThread for send_clients."""
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        rule_idx = source.find('def rule_timer_loop():')
        stat_idx = source.find('def statTrimmer():')
        ka_idx = source.find('def kaReporting():')
        self.assertGreater(rule_idx, 0)
        self.assertGreater(stat_idx, rule_idx)
        self.assertGreater(ka_idx, stat_idx)
        rule_block = source[rule_idx:stat_idx]
        stat_block = source[stat_idx:ka_idx]
        self.assertIn("report_server.send_clients(b'bridge updated')", rule_block)
        self.assertIn("report_server.send_clients(b'bridge updated')", stat_block)
        self.assertNotIn('callFromThread(report_server.send_clients', rule_block)
        self.assertNotIn('callFromThread(report_server.send_clients', stat_block)

    def test_index_helpers_snapshot_before_iterate(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        rem = source[source.find('def _idx_remove_bridge'):source.find('def _idx_replace_bridge')]
        reb = source[source.find('def rebuild_bridge_index'):source.find('def reactorLagCheck')]
        self.assertIn('list(BRIDGE_IDX.items())', rem)
        self.assertIn('list(BRIDGES.items())', reb)

    def test_selfcare_schedules_options_config(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('def schedule_options_config():', source)
        self.assertIn('def options_config_loop():', source)
        self.assertIn('task.LoopingCall(options_config_loop)', source)
        self.assertNotIn('task.LoopingCall(options_config)', source)
        self.assertEqual(source.count('schedule_options_config()'), 4)  # def + 3 call sites
        bare = re.findall(r'^\s+options_config\(\)', source, re.M)
        self.assertEqual(len(bare), 2, bare)  # _flush + options_config_loop



    def test_looping_err_handle_logs_only(self):
        """Soft timer failures must not take down the reactor (TG blackouts)."""
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        start = source.find('def loopingErrHandle(failure):')
        self.assertGreater(start, 0)
        end = source.find('\n    # Initialize the rule timer', start)
        block = source[start:end]
        self.assertIn('logger.error', block)
        self.assertNotIn('reactor.stop()', block)


class TestParrotBridgeTrim(unittest.TestCase):

    def test_augment_skips_parrot_fanout(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if is_parrot_bridge(_bridge):', source)
        self.assertIn('Do not fan out inactive UA slots to every MASTER/IPSC', source)
        self.assertIn('Parrot: calling system UA leg + PARROT peer only', source)
        self.assertTrue(is_parrot_bridge('9990'))


if __name__ == '__main__':
    unittest.main()
