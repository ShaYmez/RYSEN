#!/usr/bin/env python3
"""Unit tests for STAT bridge trimmer helpers (ON-leg prune + idle bridge expiry)."""
import unittest

from dmr_utils3.utils import bytes_3

from bridge_helpers import (
    STAT_BRIDGE_IDLE_TTL_S,
    STAT_ON_LEG_IDLE_TTL_S,
    STAT_TRIMMER_INTERVAL_S,
    bridge_has_stat_legs,
    prune_idle_stat_on_legs,
    stat_bridge_in_active_use,
    stat_bridge_last_activity,
    stat_bridge_should_remove,
    touch_stat_bridge_activity,
)


def _stat_leg(timer=1000.0):
    return {
        'SYSTEM': 'OBP-USA', 'TS': 1, 'TGID': bytes_3(67498),
        'ACTIVE': True, 'TIMEOUT': '', 'TO_TYPE': 'STAT',
        'OFF': [], 'ON': [], 'RESET': [], 'TIMER': timer,
    }


def _on_leg(system='SYSTEM-1', active=False, timer=500.0, ts=1):
    return {
        'SYSTEM': system, 'TS': ts, 'TGID': bytes_3(67498),
        'ACTIVE': active, 'TIMEOUT': 600, 'TO_TYPE': 'ON',
        'OFF': [], 'ON': [bytes_3(67498)], 'RESET': [], 'TIMER': timer,
    }


class TestStatTrimmerConstants(unittest.TestCase):

    def test_intervals(self):
        self.assertEqual(STAT_TRIMMER_INTERVAL_S, 120)
        self.assertEqual(STAT_ON_LEG_IDLE_TTL_S, 3600)
        self.assertEqual(STAT_BRIDGE_IDLE_TTL_S, 600)


class TestBridgeHasStatLegs(unittest.TestCase):

    def test_stat_bridge(self):
        self.assertTrue(bridge_has_stat_legs([_stat_leg()]))

    def test_ua_bridge(self):
        self.assertFalse(bridge_has_stat_legs([_on_leg(active=True)]))


class TestStatBridgeInActiveUse(unittest.TestCase):

    def test_active_on_blocks(self):
        self.assertTrue(stat_bridge_in_active_use([_stat_leg(), _on_leg(active=True)]))

    def test_idle_on_only(self):
        self.assertFalse(stat_bridge_in_active_use([_stat_leg(), _on_leg(active=False)]))


class TestPruneIdleStatOnLegs(unittest.TestCase):

    def test_prunes_stale_idle_on(self):
        now = 5000.0
        entries = [_stat_leg(), _on_leg(timer=now - STAT_ON_LEG_IDLE_TTL_S - 1)]
        pruned, removed = prune_idle_stat_on_legs(entries, now)
        self.assertEqual(removed, 1)
        self.assertEqual(len(pruned), 1)
        self.assertEqual(pruned[0]['TO_TYPE'], 'STAT')

    def test_keeps_recent_idle_on(self):
        now = 5000.0
        entries = [_stat_leg(), _on_leg(timer=now - 60)]
        pruned, removed = prune_idle_stat_on_legs(entries, now)
        self.assertEqual(removed, 0)
        self.assertEqual(len(pruned), 2)

    def test_keeps_active_on(self):
        now = 5000.0
        entries = [_stat_leg(), _on_leg(active=True, timer=now + 300)]
        pruned, removed = prune_idle_stat_on_legs(entries, now)
        self.assertEqual(removed, 0)
        self.assertEqual(len(pruned), 2)


class TestStatBridgeShouldRemove(unittest.TestCase):

    def test_hot_bridge_retained(self):
        now = 10_000.0
        entries = [_stat_leg(timer=now - 60)]
        self.assertFalse(stat_bridge_should_remove(entries, now))

    def test_cold_bridge_removed(self):
        now = 10_000.0
        entries = [_stat_leg(timer=now - STAT_BRIDGE_IDLE_TTL_S - 1)]
        self.assertTrue(stat_bridge_should_remove(entries, now))

    def test_active_on_blocks_removal(self):
        now = 10_000.0
        entries = [
            _stat_leg(timer=now - STAT_BRIDGE_IDLE_TTL_S - 1),
            _on_leg(active=True, timer=now + 60),
        ]
        self.assertFalse(stat_bridge_should_remove(entries, now))


class TestTouchStatBridgeActivity(unittest.TestCase):

    def test_bumps_stat_timer_only(self):
        entries = [_stat_leg(timer=1.0), _on_leg(timer=2.0)]
        touch_stat_bridge_activity(entries, 999.0)
        self.assertEqual(stat_bridge_last_activity(entries), 999.0)
        self.assertEqual(entries[1]['TIMER'], 2.0)


class TestStatTrimmerSource(unittest.TestCase):

    def test_obp_hot_path_touches_on_call_start_only(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('touch_stat_bridge_activity(BRIDGES[str(_int_dst)], pkt_time)', source)
        self.assertNotIn('touch_stat_bridge_activity(BRIDGES', source.replace(
            'touch_stat_bridge_activity(BRIDGES[str(_int_dst)], pkt_time)', ''))


if __name__ == '__main__':
    unittest.main()
