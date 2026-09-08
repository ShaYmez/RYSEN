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
        bm.CONFIG = {
            'SYSTEMS': {
                'SYSTEM-1': {
                    'MODE': 'MASTER',
                    'DEFAULT_UA_TIMER': 10,
                    'OPTIONS': '',
                    'PEERS': {},
                },
                'IPSC-198': {'MODE': 'IPSC'},
            },
            'REPORTS': {'REPORT': True},
        }
        bm.BRIDGES = {
            '326': [
                _leg('SYSTEM-1', 1, active=True, timer=100.0),
                _leg('SYSTEM-1', 2, active=False, timer=0.0),
                _leg('IPSC-198', 1, active=False, timer=0.0),
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


if __name__ == '__main__':
    unittest.main()
