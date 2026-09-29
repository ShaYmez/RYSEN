#!/usr/bin/env python3
"""UA activate: BRIDGE_SND only on real ACTIVE/topology change, not timer refresh."""
import unittest
from unittest import mock

import bridge_master as bm


def _leg(system, ts, active, to_type='ON', timer=0.0):
    return {
        'SYSTEM': system,
        'TS': ts,
        'TGID': b'\x00\x01\x46',  # 326
        'ACTIVE': active,
        'TIMEOUT': 600,
        'TO_TYPE': to_type,
        'OFF': [],
        'ON': [b'\x00\x01\x46'],
        'RESET': [],
        'TIMER': timer,
    }


class TestActivateUaNotify(unittest.TestCase):

    def setUp(self):
        self._prev_bridges = getattr(bm, 'BRIDGES', None)
        self._prev_config = getattr(bm, 'CONFIG', None)
        self._prev_late_join = dict(bm._LATE_JOIN_TARGETS)
        bm._LATE_JOIN_TARGETS.clear()
        bm.CONFIG = {
            'SYSTEMS': {
                'SYSTEM-1': {
                    'MODE': 'MASTER',
                    'DEFAULT_UA_TIMER': 10,
                    'OPTIONS': '',
                    'PEERS': {},
                },
                'IPSC-198': {'MODE': 'IPSC'},
                'HYTERA': {
                    'MODE': 'HYTERA',
                    'DEFAULT_UA_TIMER': 10,
                },
            },
            'REPORTS': {'REPORT': True},
        }
        bm.BRIDGES = {
            '326': [
                _leg('SYSTEM-1', 1, active=True, timer=100.0),
                _leg('SYSTEM-1', 2, active=False, timer=0.0),
                _leg('IPSC-198', 1, active=False, timer=0.0),
                _leg('HYTERA', 1, active=False, timer=0.0),
            ],
        }

    def tearDown(self):
        if self._prev_bridges is None:
            delattr(bm, 'BRIDGES')
        else:
            bm.BRIDGES = self._prev_bridges
        if self._prev_config is None:
            delattr(bm, 'CONFIG')
        else:
            bm.CONFIG = self._prev_config
        bm._LATE_JOIN_TARGETS.clear()
        bm._LATE_JOIN_TARGETS.update(self._prev_late_join)

    def test_already_active_refreshes_timer_without_notify(self):
        before = bm.BRIDGES['326'][0]['TIMER']
        with mock.patch.object(bm, 'notify_bridge_table_updated') as notify:
            changed = bm.activate_ua_bridge_source('326', 'SYSTEM-1', 1)
        self.assertFalse(changed)
        self.assertGreater(bm.BRIDGES['326'][0]['TIMER'], before)
        self.assertTrue(bm.BRIDGES['326'][0]['ACTIVE'])
        notify.assert_not_called()

    def test_idle_to_active_notifies(self):
        bm.BRIDGES['326'][0]['ACTIVE'] = False
        with mock.patch.object(bm, 'notify_bridge_table_updated') as notify:
            changed = bm.activate_ua_bridge_source('326', 'SYSTEM-1', 1)
        self.assertTrue(changed)
        self.assertTrue(bm.BRIDGES['326'][0]['ACTIVE'])
        notify.assert_called_once()

    def test_linked_ipsc_new_activate_notifies(self):
        # Source already active; OPTIONS links IPSC so wake counts as change.
        bm.CONFIG['SYSTEMS']['SYSTEM-1']['OPTIONS'] = 'IPSC=IPSC-198'
        self.assertFalse(bm.BRIDGES['326'][2]['ACTIVE'])
        with mock.patch.object(bm, 'notify_bridge_table_updated') as notify:
            changed = bm.activate_ua_bridge_source('326', 'SYSTEM-1', 1)
        self.assertTrue(changed)
        self.assertTrue(bm.BRIDGES['326'][2]['ACTIVE'])
        notify.assert_called_once()

    def test_hytera_activation_arms_midstream_late_join(self):
        now = bm.time()
        self.assertEqual(bm._HYTERA_LATE_JOIN_ARM_S, 0.120)
        with mock.patch.object(bm, 'notify_bridge_table_updated'):
            changed = bm.activate_ua_bridge_source('326', 'HYTERA', 1)

        self.assertTrue(changed)
        self.assertTrue(bm.BRIDGES['326'][3]['ACTIVE'])
        self.assertFalse(
            bm._late_join_active(
                'HYTERA', 1, b'\x00\x01\x46', now))
        self.assertTrue(
            bm._late_join_active(
                'HYTERA', 1, b'\x00\x01\x46',
                now + bm._HYTERA_LATE_JOIN_ARM_S + 0.001))

    def test_late_join_arm_expires_after_stream_timeout(self):
        bm._arm_late_join_target(
            'HYTERA', 1, b'\x00\x01\x46', 100.0)

        self.assertFalse(
            bm._late_join_active(
                'HYTERA', 1, b'\x00\x01\x46',
                100.001 + bm._LATE_JOIN_TIMEOUT_S))

    def test_late_join_is_hytera_only_and_stream_bound(self):
        tgid = b'\x00\x01\x46'
        first_stream = b'\x01\x02\x03\x04'
        other_stream = b'\x05\x06\x07\x08'

        self.assertFalse(
            bm._arm_late_join_target('SYSTEM-1', 1, tgid, 100.0))
        self.assertFalse(
            bm._late_join_active(
                'SYSTEM-1', 1, tgid, 101.0, first_stream))

        self.assertTrue(
            bm._arm_late_join_target('HYTERA', 1, tgid, 100.0))
        self.assertFalse(
            bm._late_join_active(
                'HYTERA', 1, tgid, 100.01, first_stream))
        self.assertTrue(
            bm._late_join_active(
                'HYTERA', 1, tgid,
                100.0 + bm._HYTERA_LATE_JOIN_ARM_S + 0.001,
                first_stream))
        self.assertFalse(
            bm._late_join_active(
                'HYTERA', 1, tgid,
                100.0 + bm._HYTERA_LATE_JOIN_ARM_S + 0.001,
                other_stream))

    def test_late_join_reconstructs_full_hytera_sequence(self):
        self.assertEqual(bm._late_join_wire_sequence(0x54, 341), 340)
        self.assertEqual(bm._late_join_wire_sequence(0xe8, 745), 744)

    def test_deferred_header_survives_hangtime_release_boundary(self):
        hangtime = 5.0
        self.assertTrue(
            bm._hytera_deferred_vhead_valid(100.0, 105.0, hangtime))
        self.assertTrue(
            bm._hytera_deferred_vhead_valid(
                100.0, 105.0 + bm._HYTERA_DEFERRED_VHEAD_GRACE_S,
                hangtime))
        self.assertFalse(
            bm._hytera_deferred_vhead_valid(
                100.0,
                105.0 + bm._HYTERA_DEFERRED_VHEAD_GRACE_S + 0.001,
                hangtime))

    def test_late_join_waits_for_rf_dekey(self):
        tgid = b'\x00\x5b\xdc'
        status = {
            2: {
                'RX_TGID': tgid,
                'RX_TYPE': bm.HBPF_SLT_VHEAD,
                'RX_TIME': 100.0,
            },
        }
        self.assertTrue(
            bm._target_rx_call_active(status, 2, tgid, 100.5))
        self.assertTrue(
            bm._target_rx_call_active(status, 2, tgid, 110.0))

        status[2]['RX_TYPE'] = bm.HBPF_SLT_VTERM
        self.assertFalse(
            bm._target_rx_call_active(status, 2, tgid, 100.6))

    def test_both_routing_paths_guard_active_rf_late_join(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertEqual(source.count('if _target_rx_call_active('), 2)

    def test_source_guard_no_ua_refreshed_notify(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertNotIn('_ua_refreshed', source)
        self.assertIn(
            'do not BRIDGE_SND on timer-only refresh',
            source,
        )


class TestResetStaticKeepsUaMembers(unittest.TestCase):

    def setUp(self):
        self._prev_bridges = getattr(bm, 'BRIDGES', None)
        self._prev_sub = getattr(bm, 'SUB_MAP', None)
        self._prev_idx = getattr(bm, 'BRIDGE_IDX', None)
        bm.SUB_MAP = {}
        bm.BRIDGE_IDX = {}
        bm.BRIDGES = {
            '91': [_leg('SYSTEM-1', 2, active=True, to_type='OFF')],
        }

    def tearDown(self):
        if self._prev_bridges is None:
            delattr(bm, 'BRIDGES')
        else:
            bm.BRIDGES = self._prev_bridges
        if self._prev_sub is None:
            try:
                delattr(bm, 'SUB_MAP')
            except AttributeError:
                pass
        else:
            bm.SUB_MAP = self._prev_sub
        if self._prev_idx is None:
            bm.BRIDGE_IDX = {}
        else:
            bm.BRIDGE_IDX = self._prev_idx

    def test_keeps_live_when_ua_member_remains(self):
        peer = b'\x00\x23\xc5\x93'
        bm.SUB_MAP = {peer: ('SYSTEM-1', 2, b'\x00\x00\x5b', 1, peer)}  # 91
        bm.reset_static_tg(91, 2, 10, 'SYSTEM-1')
        leg = bm.BRIDGES['91'][0]
        self.assertTrue(leg['ACTIVE'])
        self.assertEqual(leg['TO_TYPE'], 'ON')

    def test_drops_live_when_no_ua_members(self):
        bm.SUB_MAP = {}
        bm.reset_static_tg(91, 2, 10, 'SYSTEM-1')
        leg = bm.BRIDGES['91'][0]
        self.assertFalse(leg['ACTIVE'])
        self.assertEqual(leg['TO_TYPE'], 'ON')

    def test_hytera_drops_stale_subscriber_map_membership(self):
        peer = b'\x00\x03\x97\x17'
        bm.SUB_MAP = {
            b'\x00#\xcb#': ('HYTERA-0', 1, b'\x00\x00[', 1, peer),
        }
        bm.BRIDGES = {
            '91': [_leg('HYTERA-0', 1, active=True, to_type='OFF')],
        }
        with mock.patch.object(
                bm, 'CONFIG',
                {'SYSTEMS': {'HYTERA-0': {'MODE': 'HYTERA'}}},
                create=True):
            bm.reset_static_tg(91, 1, 10, 'HYTERA-0')
        leg = bm.BRIDGES['91'][0]
        self.assertFalse(leg['ACTIVE'])
        self.assertEqual(leg['TO_TYPE'], 'ON')


if __name__ == '__main__':
    unittest.main()
