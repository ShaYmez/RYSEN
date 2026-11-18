#!/usr/bin/env python3
"""Regression tests for efficient bridge-index and monitor updates."""
import time
import unittest
from types import SimpleNamespace

import bridge_master as bm
from bridge_helpers import STAT_ON_LEG_IDLE_TTL_S, bind_runtime_globals
from dmr_utils3.utils import bytes_3


def _entry(system, slot, tgid):
    return {
        'SYSTEM': system,
        'TS': slot,
        'TGID': bytes_3(tgid),
        'ACTIVE': True,
        'TO_TYPE': 'OFF',
    }


class _NoScanDict(dict):
    """Fail if a targeted index replacement scans the global index."""

    def items(self):
        raise AssertionError('targeted replacement scanned all routing keys')

    def __iter__(self):
        raise AssertionError('targeted replacement iterated all routing keys')


class TestBridgeIndexEfficiency(unittest.TestCase):
    def setUp(self):
        self.old_bridges = getattr(bm, 'BRIDGES', None)
        self.old_idx = bm.BRIDGE_IDX
        self.old_keys = bm._BRIDGE_IDX_KEYS

    def tearDown(self):
        bm.BRIDGES = {} if self.old_bridges is None else self.old_bridges
        bm.BRIDGE_IDX = self.old_idx
        bm._BRIDGE_IDX_KEYS = self.old_keys

    def test_replace_uses_reverse_keys_not_global_scan(self):
        old_key = ('SYSTEM-1', 2, bytes_3(2350))
        new_key = ('SYSTEM-1', 2, bytes_3(2351))
        unrelated = {
            ('SYSTEM-%s' % i, 1, bytes_3(100000 + i)): {'other'}
            for i in range(30000)
        }
        unrelated[old_key] = {'2350'}
        bm.BRIDGE_IDX = _NoScanDict(unrelated)
        bm._BRIDGE_IDX_KEYS = {'2350': {old_key}}
        bm.BRIDGES = {'2350': [_entry('SYSTEM-1', 2, 2351)]}

        bm._idx_replace_bridge('2350')

        self.assertNotIn(old_key, bm.BRIDGE_IDX)
        self.assertEqual(bm.BRIDGE_IDX[new_key], {'2350'})
        self.assertEqual(bm._BRIDGE_IDX_KEYS['2350'], {new_key})
        self.assertEqual(len(bm.BRIDGE_IDX), 30001)

    def test_rebuild_populates_forward_and_reverse_indexes(self):
        shared = _entry('SYSTEM-1', 2, 2350)
        bm.BRIDGES = {
            '2350': [shared],
            'alias': [dict(shared)],
            '2351': [_entry('SYSTEM-2', 1, 2351)],
        }

        bm.rebuild_bridge_index()

        shared_key = ('SYSTEM-1', 2, bytes_3(2350))
        self.assertEqual(bm.BRIDGE_IDX[shared_key], {'2350', 'alias'})
        self.assertEqual(bm._BRIDGE_IDX_KEYS['2350'], {shared_key})
        self.assertEqual(bm._BRIDGE_IDX_KEYS['alias'], {shared_key})
        bm._idx_remove_bridge('2350')
        self.assertEqual(bm.BRIDGE_IDX[shared_key], {'alias'})

    def test_runtime_binding_shares_forward_and_reverse_indexes(self):
        key = ('SYSTEM-1', 2, bytes_3(2350))
        source = SimpleNamespace(
            CONFIG={'SYSTEMS': {}},
            BRIDGES={'2350': [_entry('SYSTEM-1', 2, 2350)]},
            BRIDGE_IDX={key: {'2350'}},
            _BRIDGE_IDX_KEYS={'2350': {key}},
            SUB_MAP={},
            systems={},
        )
        target = SimpleNamespace()

        self.assertTrue(bind_runtime_globals(target=target, source=source))

        self.assertIs(target.BRIDGE_IDX, source.BRIDGE_IDX)
        self.assertIs(target._BRIDGE_IDX_KEYS, source._BRIDGE_IDX_KEYS)
        target._BRIDGE_IDX_KEYS['2350'].add(('SYSTEM-2', 1, bytes_3(2350)))
        self.assertEqual(target._BRIDGE_IDX_KEYS, source._BRIDGE_IDX_KEYS)


class TestLazyStaticBridge(unittest.TestCase):
    def setUp(self):
        self.old_config = getattr(bm, 'CONFIG', None)
        self.old_bridges = getattr(bm, 'BRIDGES', None)
        self.old_idx = bm.BRIDGE_IDX
        self.old_keys = bm._BRIDGE_IDX_KEYS
        self.had_sub_map = hasattr(bm, 'SUB_MAP')
        self.old_sub_map = getattr(bm, 'SUB_MAP', None)
        systems = {
            'SYSTEM-%s' % i: {
                'MODE': 'MASTER',
                'DEFAULT_UA_TIMER': 10,
            }
            for i in range(200)
        }
        systems['OBP-EU'] = {'MODE': 'OPENBRIDGE'}
        systems['OBP-APOLLO'] = {'MODE': 'OPENBRIDGE'}
        bm.CONFIG = {'SYSTEMS': systems, 'REPORTS': {'REPORT': False}}
        bm.BRIDGES = {}
        bm.BRIDGE_IDX = {}
        bm._BRIDGE_IDX_KEYS = {}
        bm.SUB_MAP = {}

    def tearDown(self):
        bm.CONFIG = self.old_config
        bm.BRIDGES = {} if self.old_bridges is None else self.old_bridges
        bm.BRIDGE_IDX = self.old_idx
        bm._BRIDGE_IDX_KEYS = self.old_keys
        if self.had_sub_map:
            bm.SUB_MAP = self.old_sub_map
        elif hasattr(bm, 'SUB_MAP'):
            del bm.SUB_MAP

    def test_new_static_does_not_preallocate_every_master(self):
        bm.make_static_tg(2350, 2, 10, 'SYSTEM-1')

        entries = bm.BRIDGES['2350']
        self.assertEqual(len(entries), 3)
        self.assertEqual(
            {entry['SYSTEM'] for entry in entries},
            {'SYSTEM-1', 'OBP-EU', 'OBP-APOLLO'})
        self.assertNotIn('SYSTEM-150', {entry['SYSTEM'] for entry in entries})
        self.assertEqual(len(bm.BRIDGE_IDX), 3)

    def test_second_master_is_added_only_when_needed(self):
        bm.make_static_tg(2350, 2, 10, 'SYSTEM-1')

        changed = bm._ensure_master_on_legs('2350', 'SYSTEM-150')

        self.assertTrue(changed)
        second_master = [
            entry for entry in bm.BRIDGES['2350']
            if entry['SYSTEM'] == 'SYSTEM-150'
        ]
        self.assertEqual({entry['TS'] for entry in second_master}, {1, 2})
        self.assertEqual(len(bm.BRIDGES['2350']), 5)
        self.assertEqual(len(bm.BRIDGE_IDX), 5)

    def test_static_slot_keyup_does_not_add_duplicate_ua_leg(self):
        bm.make_static_tg(2350, 2, 10, 'SYSTEM-1')

        bm.activate_ua_bridge_source('2350', 'SYSTEM-1', 2, 10)

        source_entries = [
            entry for entry in bm.BRIDGES['2350']
            if entry['SYSTEM'] == 'SYSTEM-1'
        ]
        slot_two = [entry for entry in source_entries if entry['TS'] == 2]
        self.assertEqual(len(slot_two), 1)
        self.assertEqual(slot_two[0]['TO_TYPE'], 'OFF')
        slot_one = [entry for entry in source_entries if entry['TS'] == 1]
        self.assertEqual(len(slot_one), 1)
        self.assertEqual(slot_one[0]['TO_TYPE'], 'ON')
        self.assertEqual(len(bm.BRIDGE_IDX), 4)

    def test_trimmer_removes_idle_lazy_legs_from_static_bridge(self):
        bm.make_static_tg(2350, 2, 10, 'SYSTEM-1')
        bm._ensure_master_on_legs('2350', 'SYSTEM-150')
        stale = time.time() - STAT_ON_LEG_IDLE_TTL_S - 1
        for entry in bm.BRIDGES['2350']:
            if entry.get('LAZY'):
                entry['ACTIVE'] = False
                entry['TIMER'] = stale

        bm.statTrimmer()

        self.assertFalse(any(
            entry['SYSTEM'] == 'SYSTEM-150'
            for entry in bm.BRIDGES['2350']))
        self.assertEqual(len(bm.BRIDGES['2350']), 3)
        self.assertEqual(len(bm.BRIDGE_IDX), 3)

    def test_removed_cold_static_becomes_prunable_lazy_leg(self):
        bm.make_static_tg(2350, 2, 10, 'SYSTEM-1')
        bm.reset_static_tg(2350, 2, 10, 'SYSTEM-1')
        removed = next(
            entry for entry in bm.BRIDGES['2350']
            if entry['SYSTEM'] == 'SYSTEM-1' and entry['TS'] == 2)
        self.assertEqual(removed['TO_TYPE'], 'ON')
        self.assertTrue(removed['LAZY'])
        removed['ACTIVE'] = False
        removed['TIMER'] = time.time() - STAT_ON_LEG_IDLE_TTL_S - 1

        bm.statTrimmer()

        self.assertFalse(any(
            entry['SYSTEM'] == 'SYSTEM-1' and entry['TS'] == 2
            for entry in bm.BRIDGES['2350']))

    def test_explicit_rule_leg_survives_static_add_and_remove(self):
        explicit = _entry('SYSTEM-1', 2, 2350)
        explicit.update({'ACTIVE': False, 'TO_TYPE': 'ON'})
        bm.BRIDGES['2350'] = [explicit]
        bm.rebuild_bridge_index()

        bm.make_static_tg(2350, 2, 10, 'SYSTEM-1')
        static = bm.BRIDGES['2350'][0]
        self.assertFalse(static['LAZY'])
        bm.reset_static_tg(2350, 2, 10, 'SYSTEM-1')
        restored = bm.BRIDGES['2350'][0]
        self.assertFalse(restored['LAZY'])
        restored['ACTIVE'] = False
        restored['TIMER'] = time.time() - STAT_ON_LEG_IDLE_TTL_S - 1

        bm.statTrimmer()

        self.assertIn(restored, bm.BRIDGES['2350'])

    def test_on_demand_bridge_master_legs_are_lazy(self):
        bm.make_single_bridge(bytes_3(2350), 'SYSTEM-1', 2, 10)

        master_entries = [
            entry for entry in bm.BRIDGES['2350']
            if entry['SYSTEM'].startswith('SYSTEM-')
        ]
        self.assertTrue(master_entries)
        self.assertTrue(all(entry.get('LAZY') for entry in master_entries))

    def test_static_apply_collapses_legacy_duplicate_slot_legs(self):
        bm.make_static_tg(2350, 2, 10, 'SYSTEM-1')
        duplicate = dict(next(
            entry for entry in bm.BRIDGES['2350']
            if entry['SYSTEM'] == 'SYSTEM-1' and entry['TS'] == 2))
        bm.BRIDGES['2350'].append(duplicate)
        bm._idx_replace_bridge('2350')

        bm.make_static_tg(2350, 2, 10, 'SYSTEM-1')

        matches = [
            entry for entry in bm.BRIDGES['2350']
            if entry['SYSTEM'] == 'SYSTEM-1' and entry['TS'] == 2
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['TO_TYPE'], 'OFF')

    def test_static_apply_discards_startup_mesh_but_keeps_explicit_rule(self):
        lazy_entries = []
        for i in range(200):
            for slot in (1, 2):
                entry = _entry('SYSTEM-%s' % i, slot, 2350)
                entry.update({'ACTIVE': False, 'TO_TYPE': 'ON', 'LAZY': True})
                lazy_entries.append(entry)
        explicit = _entry('SYSTEM-199', 1, 2350)
        explicit.update({'ACTIVE': False, 'TO_TYPE': 'ON'})
        bm.BRIDGES['2350'] = lazy_entries + [explicit]
        bm.rebuild_bridge_index()

        bm.make_static_tg(2350, 2, 10, 'SYSTEM-1')

        entries = bm.BRIDGES['2350']
        self.assertFalse(any(
            entry.get('LAZY')
            and entry.get('TO_TYPE') == 'ON'
            and not entry.get('ACTIVE')
            for entry in entries))
        self.assertIn(explicit, entries)
        static = [
            entry for entry in entries
            if entry['SYSTEM'] == 'SYSTEM-1' and entry['TS'] == 2
        ]
        self.assertEqual(len(static), 1)
        self.assertEqual(static[0]['TO_TYPE'], 'OFF')
        self.assertEqual(len(entries), 2)
        self.assertEqual(len(bm.BRIDGE_IDX), 2)


class _DelayedCall:
    def __init__(self, callback):
        self.callback = callback
        self.running = True

    def active(self):
        return self.running

    def cancel(self):
        self.running = False

    def fire(self):
        self.running = False
        self.callback()


class _FakeReactor:
    def __init__(self):
        self.calls = []

    def callLater(self, delay, callback):
        call = _DelayedCall(callback)
        self.calls.append((delay, call))
        return call


class _ReportServer:
    def __init__(self):
        self.sends = 0

    def send_bridge(self):
        self.sends += 1


class TestBridgeReportCoalescing(unittest.TestCase):
    def setUp(self):
        self.old_config = getattr(bm, 'CONFIG', None)
        self.old_server = getattr(bm, 'report_server', None)
        self.had_server = hasattr(bm, 'report_server')
        self.old_reactor = bm.reactor
        self.old_pending = bm._BRIDGE_REPORT_PENDING[0]
        bm._BRIDGE_REPORT_PENDING[0] = None
        bm.CONFIG = {'REPORTS': {'REPORT': True}}
        bm.report_server = _ReportServer()
        bm.reactor = _FakeReactor()

    def tearDown(self):
        bm.cancel_pending_bridge_table_update()
        bm._BRIDGE_REPORT_PENDING[0] = self.old_pending
        bm.reactor = self.old_reactor
        bm.CONFIG = self.old_config
        if self.had_server:
            bm.report_server = self.old_server
        else:
            del bm.report_server

    def test_reconnect_burst_queues_one_monitor_snapshot(self):
        for _ in range(100):
            bm.notify_bridge_table_updated()

        self.assertEqual(len(bm.reactor.calls), 1)
        self.assertEqual(bm.report_server.sends, 0)
        delay, call = bm.reactor.calls[0]
        self.assertEqual(delay, bm._BRIDGE_REPORT_DELAY_S)

        call.fire()

        self.assertEqual(bm.report_server.sends, 1)
        self.assertIsNone(bm._BRIDGE_REPORT_PENDING[0])

    def test_periodic_report_can_cancel_queued_duplicate(self):
        bm.notify_bridge_table_updated()
        _, call = bm.reactor.calls[0]

        bm.cancel_pending_bridge_table_update()

        self.assertFalse(call.active())
        self.assertIsNone(bm._BRIDGE_REPORT_PENDING[0])


if __name__ == '__main__':
    unittest.main()
