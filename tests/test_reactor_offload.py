#!/usr/bin/env python3
"""Source tests: ADN-style deferToThread loops + coalesced selfcare options_config + parrot trim."""
import re
import unittest

from bridge_helpers import OPTIONS_CONFIG_COALESCE_S, is_parrot_bridge


class TestReactorOffloadWiring(unittest.TestCase):

    def test_coalesce_constant(self):
        self.assertEqual(OPTIONS_CONFIG_COALESCE_S, 5.0)

    def test_rule_timer_stat_ka_use_defer_to_thread(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('from twisted.internet import reactor, task, threads', source)
        self.assertIn('threads.deferToThread(rule_timer_loop)', source)
        self.assertIn('threads.deferToThread(statTrimmer)', source)
        self.assertIn('threads.deferToThread(kaReporting)', source)
        self.assertIn('task.LoopingCall(_rule_timer_in_thread)', source)
        self.assertIn('task.LoopingCall(_stat_trimmer_in_thread)', source)
        self.assertIn('task.LoopingCall(_ka_reporting_in_thread)', source)
        self.assertNotIn('task.LoopingCall(rule_timer_loop)', source)
        self.assertNotIn('task.LoopingCall(statTrimmer)', source)
        self.assertNotIn('task.LoopingCall(kaReporting)', source)

    def test_report_notify_uses_call_from_thread(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn(
            "reactor.callFromThread(report_server.send_clients, b'bridge updated')",
            source)

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
