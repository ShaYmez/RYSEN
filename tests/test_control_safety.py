#!/usr/bin/env python3
"""Operator kick/ban/drop-call and shared-MASTER isolation regressions."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import bridge_master as bm
import control_api
from control_bans import ControlBanStore, radio_id_core
from dmr_utils3.utils import bytes_3
from ipsc_master import IpscMasterMixin


class TestControlBanStore(unittest.TestCase):
    def test_essid_ban_persists_and_expires(self):
        now = [1000.0]
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + '/bans.json'
            store = ControlBanStore(path=path, now_fn=lambda: now[0])
            entry = store.ban(234018901, 60, 'loop')
            self.assertEqual(radio_id_core(234018901), '2340189')
            self.assertEqual(
                radio_id_core((234018901).to_bytes(4, 'big')), '2340189')
            self.assertEqual(entry['radio_id'], 2340189)
            self.assertTrue(store.is_banned(234018902))

            reloaded = ControlBanStore(path=path, now_fn=lambda: now[0])
            self.assertTrue(reloaded.is_banned(2340189))
            now[0] = 1061.0
            self.assertFalse(reloaded.is_banned(2340189))

    def test_unban_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlBanStore(path=tmp + '/bans.json')
            store.ban(2340189, 60)
            self.assertTrue(store.unban(234018901))
            self.assertFalse(store.unban(2340189))


class TestDropPeerCall(unittest.TestCase):
    def setUp(self):
        self.prev_systems = dict(bm.systems)
        self.prev_sub_map = getattr(bm, 'SUB_MAP', None)
        self.prev_config = getattr(bm, 'CONFIG', None)
        self.prev_dropped = dict(bm._CONTROL_DROPPED_STREAMS)
        self.peer = bytes_3(2340189).rjust(4, b'\x00')
        self.stream = b'\x00\x00\x00\x07'
        bm.CONFIG = {'REPORTS': {'REPORT': False}}
        bm.SUB_MAP = {
            bytes_3(2340189): (
                'MASTER-1', 2, bytes_3(91), 1, self.peer),
        }
        bm.systems.clear()
        bm.systems['MASTER-1'] = SimpleNamespace(STATUS={
            1: {'RX_PEER': b'\x00', 'RX_STREAM_ID': b'\x00',
                'RX_TYPE': bm.HBPF_SLT_VTERM},
            2: {'RX_PEER': self.peer, 'RX_STREAM_ID': self.stream,
                'RX_TYPE': bm.HBPF_SLT_VHEAD, 'RX_TIME': 1},
        })
        bm._CONTROL_DROPPED_STREAMS.clear()

    def tearDown(self):
        bm.systems.clear()
        bm.systems.update(self.prev_systems)
        bm._CONTROL_DROPPED_STREAMS.clear()
        bm._CONTROL_DROPPED_STREAMS.update(self.prev_dropped)
        if self.prev_sub_map is None:
            delattr(bm, 'SUB_MAP')
        else:
            bm.SUB_MAP = self.prev_sub_map
        if self.prev_config is None:
            delattr(bm, 'CONFIG')
        else:
            bm.CONFIG = self.prev_config

    def test_drop_call_blocks_active_stream_and_clears_rf_route(self):
        self.assertEqual(bm.drop_peer_call('MASTER-1', self.peer), 1)
        self.assertTrue(bm._control_stream_is_dropped(
            'MASTER-1', self.peer, self.stream))
        self.assertEqual(bm.SUB_MAP, {})
        self.assertEqual(
            bm.systems['MASTER-1'].STATUS[2]['RX_TYPE'],
            bm.HBPF_SLT_VTERM)


class TestPeerReflectorIsolation(unittest.TestCase):
    def setUp(self):
        self.prev_bridges = getattr(bm, 'BRIDGES', None)

    def tearDown(self):
        if self.prev_bridges is None:
            delattr(bm, 'BRIDGES')
        else:
            bm.BRIDGES = self.prev_bridges

    def test_new_reflector_only_replaces_same_peers_reflector(self):
        peer_a = b'\x00\x23\xc5\x93'
        peer_b = b'\x00\x23\xc5\x94'
        bm.BRIDGES = {
            '#91': [{
                'SYSTEM': 'MASTER-1', 'TS': 2, 'TO_TYPE': 'ON',
                'ACTIVE': True, 'TIMER': 0, 'LINKER_PEER': peer_a,
            }],
            '#92': [{
                'SYSTEM': 'MASTER-1', 'TS': 2, 'TO_TYPE': 'ON',
                'ACTIVE': True, 'TIMER': 0, 'LINKER_PEER': peer_b,
            }],
            '#93': [],
        }
        with unittest.mock.patch.object(bm, 'rebuild_bridge_index'):
            bm.deactivate_other_dynamic_reflectors(
                'MASTER-1', '#93', 2, peer_a)
        self.assertFalse(bm.BRIDGES['#91'][0]['ACTIVE'])
        self.assertTrue(bm.BRIDGES['#92'][0]['ACTIVE'])


class TestOpsTalkgroupRuntime(unittest.TestCase):
    def setUp(self):
        self.previous = {
            'CONFIG': getattr(bm, 'CONFIG', None),
            'BRIDGES': getattr(bm, 'BRIDGES', None),
            'SUB_MAP': getattr(bm, 'SUB_MAP', None),
        }
        self.peer = (234018901).to_bytes(4, 'big')
        tg = bytes_3(91)
        bm.CONFIG = {
            'REPORTS': {'REPORT': False},
            'SYSTEMS': {
                'MASTER-1': {
                    'MODE': 'MASTER', 'ENABLED': True,
                    'DEFAULT_UA_TIMER': 10, 'LINK_IPSC': 'IPSC-1',
                    'PEERS': {
                        self.peer: {
                            'CONNECTION': 'YES',
                            'OPTIONS': 'LINK_IPSC=IPSC-OTHER;',
                        },
                    },
                },
                'IPSC-1': {'MODE': 'IPSC', 'ENABLED': True},
                'IPSC-OTHER': {'MODE': 'IPSC', 'ENABLED': True},
            },
        }
        bm.SUB_MAP = {b'rf': ('MASTER-1', 2, tg, 1, self.peer)}
        bm.BRIDGES = {
            '91': [
                {
                    'SYSTEM': 'MASTER-1', 'TS': 2, 'TGID': tg,
                    'ACTIVE': False, 'TO_TYPE': 'ON', 'TIMEOUT': 600,
                    'TIMER': 0,
                },
                {
                    'SYSTEM': 'IPSC-1', 'TS': 2, 'TGID': tg,
                    'ACTIVE': False, 'TO_TYPE': 'ON', 'TIMEOUT': 600,
                    'TIMER': 0,
                },
                {
                    'SYSTEM': 'IPSC-OTHER', 'TS': 2, 'TGID': tg,
                    'ACTIVE': False, 'TO_TYPE': 'ON', 'TIMEOUT': 600,
                    'TIMER': 0,
                },
                {
                    'SYSTEM': 'IPSC-1', 'TS': 2, 'TGID': tg,
                    'ACTIVE': True, 'TO_TYPE': 'OFF', 'TIMEOUT': 0,
                    'TIMER': 0,
                },
            ],
        }

    def tearDown(self):
        for name, value in self.previous.items():
            if value is None:
                try:
                    delattr(bm, name)
                except AttributeError:
                    pass
            else:
                setattr(bm, name, value)

    def test_ops_activate_is_stanza_scoped_and_delete_keeps_statics(self):
        original_sub_map = dict(bm.SUB_MAP)
        with unittest.mock.patch.object(
                control_api, '_runtime_bridge', return_value=bm):
            handlers = control_api._wire_handlers()
        with unittest.mock.patch.object(bm, 'notify_bridge_table_updated'):
            handlers['activate_tg']('MASTER-1', 91, 2, self.peer)
            self.assertTrue(bm.BRIDGES['91'][0]['ACTIVE'])
            self.assertTrue(bm.BRIDGES['91'][1]['ACTIVE'])
            self.assertFalse(bm.BRIDGES['91'][2]['ACTIVE'])
            self.assertEqual(bm.SUB_MAP, original_sub_map)

            handlers['deactivate_tg']('MASTER-1', 91, 2, self.peer)
            self.assertFalse(bm.BRIDGES['91'][0]['ACTIVE'])
            self.assertFalse(bm.BRIDGES['91'][1]['ACTIVE'])
            self.assertTrue(bm.BRIDGES['91'][3]['ACTIVE'])


class TestBannedLogin(unittest.TestCase):
    def test_router_rejects_banned_radio_before_hbp_login(self):
        peer = (2340189).to_bytes(4, 'big')
        previous = bm.CONTROL_BANS
        with tempfile.TemporaryDirectory() as tmp:
            bm.CONTROL_BANS = ControlBanStore(path=tmp + '/bans.json')
            bm.CONTROL_BANS.ban(2340189, 60, 'loop')
            router = bm.routerHBP.__new__(bm.routerHBP)
            router._system = 'MASTER-1'
            router.transport = MagicMock()
            try:
                router.master_datagramReceived(
                    b'RPTL' + peer, ('127.0.0.1', 62031))
                router.transport.write.assert_called_once_with(
                    b'MSTNAK' + peer, ('127.0.0.1', 62031))
            finally:
                bm.CONTROL_BANS = previous

    def test_ipsc_registration_uses_same_ban_store(self):
        peer = (2340189).to_bytes(4, 'big')
        router = IpscMasterMixin.__new__(IpscMasterMixin)
        router._system = 'IPSC-1'
        router._ipsc_peers = {}
        checked = []

        def check(radio_id):
            checked.append(radio_id)
            return {'reason': 'loop'}

        router.control_radio_banned = check
        router._on_reg_req(b'\x90' + peer + b'\x00', '127.0.0.1', 50000)
        self.assertEqual(checked, [2340189])
        self.assertEqual(router._ipsc_peers, {})

    def test_ban_kicks_every_connected_essid(self):
        previous_config = getattr(bm, 'CONFIG', None)
        previous_store = bm.CONTROL_BANS
        peer_a = (234018901).to_bytes(4, 'big')
        peer_b = (234018902).to_bytes(4, 'big')
        with tempfile.TemporaryDirectory() as tmp:
            bm.CONTROL_BANS = ControlBanStore(path=tmp + '/bans.json')
            bm.CONFIG = {
                'SYSTEMS': {
                    'MASTER-1': {
                        'MODE': 'MASTER',
                        'PEERS': {
                            peer_a: {'CONNECTION': 'YES'},
                            peer_b: {'CONNECTION': 'YES'},
                        },
                    },
                },
            }
            kicked = []
            with unittest.mock.patch.object(
                    bm, 'kick_hotspot_peer',
                    side_effect=lambda system, peer: kicked.append(
                        (system, peer)) or True):
                result = bm.ban_hotspot_radio(2340189, 60, 'loop')
            self.assertEqual(result['kicked'], 2)
            self.assertEqual(
                kicked, [('MASTER-1', peer_a), ('MASTER-1', peer_b)])
        bm.CONTROL_BANS = previous_store
        if previous_config is None:
            delattr(bm, 'CONFIG')
        else:
            bm.CONFIG = previous_config


if __name__ == '__main__':
    unittest.main()
