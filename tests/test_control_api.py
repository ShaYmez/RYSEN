#!/usr/bin/env python3
"""Unit tests for the FreeSTAR loopback control dispatch (no reactor)."""

import unittest

from control_api import (
    handle_control_request,
    parse_control_path,
    slot_from_body,
    static_slot,
)
from selfcare_db import (
    AmbiguousPeerError,
    find_connected_dmr_peer,
    live_static_required,
    merge_ts_into_options,
    other_peer_has_static,
    peer_own_options,
    radio_id_core,
    radio_ids_match,
    store_peer_options,
    ts_lists_from_options,
)


class TestControlDispatch(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def find_peer(radio_id):
            if radio_id in (2345875, 234587501):
                return 'MASTER-1', b'\x00\x23\xc5\x93'
            return None, None

        def disconnect(system, peer_id):
            self.calls.append(('disconnect', system, peer_id))

        def drop_dynamic(system, peer_id=None):
            self.calls.append(('drop-dynamic', system, peer_id))

        def drop_call(system, peer_id=None):
            self.calls.append(('drop-call', system, peer_id))

        def activate_tg(system, tgid, slot, peer_id):
            self.calls.append(('activate', system, tgid, slot))

        def deactivate_tg(system, tgid, slot, peer_id=None):
            self.calls.append(('deactivate', system, tgid, slot))

        def list_peer(system, peer_id, radio_id):
            self.calls.append(('list', system, radio_id))
            return {
                'ok': True,
                'connected': True,
                'system': system,
                'radio_id': 2345875,
                'statics': [{'slot': 2, 'group': 2350}],
                'dynamics': [],
            }

        def add_static(system, tgid, slot, peer_id):
            self.calls.append(('add-static', system, tgid, slot))

        def remove_static(system, tgid, slot, peer_id):
            self.calls.append(('remove-static', system, tgid, slot))

        def persist_disc(system, peer_id):
            self.calls.append(('persist-disc', system, peer_id))

        def kick_peer(system, peer_id):
            self.calls.append(('kick', system, peer_id))
            return True

        def ban_radio(radio_id, duration, reason):
            self.calls.append(('ban', radio_id, duration, reason))
            return {'expires_at': 1234}

        def unban_radio(radio_id):
            self.calls.append(('unban', radio_id))
            return True

        self.kw = dict(
            find_peer=find_peer,
            disconnect=disconnect,
            drop_dynamic=drop_dynamic,
            activate_tg=activate_tg,
            deactivate_tg=deactivate_tg,
            drop_call=drop_call,
            list_peer=list_peer,
            add_static=add_static,
            remove_static=remove_static,
            persist_disc=persist_disc,
            kick_peer=kick_peer,
            ban_radio=ban_radio,
            unban_radio=unban_radio,
        )

    def test_ops_disconnect_does_not_queue_device_disc(self):
        code, body = handle_control_request('disconnect', 'POST', {'radio_id': 2345875}, **self.kw)
        self.assertEqual(code, 200)
        self.assertEqual(body['system'], 'MASTER-1')
        self.assertEqual(self.calls[0][0], 'disconnect')
        self.assertNotIn('persist-disc', [call[0] for call in self.calls])

    def test_device_disconnect_queues_one_shot_disc(self):
        code, body = handle_control_request(
            'device:disconnect', 'POST', {'radio_id': 2345875}, **self.kw)
        self.assertEqual(code, 200)
        self.assertEqual(body['scope'], 'device')
        self.assertEqual([call[0] for call in self.calls],
                         ['disconnect', 'persist-disc'])

    def test_drop_call_is_not_disconnect(self):
        code, body = handle_control_request('drop-call', 'POST', {'radio_id': 2345875}, **self.kw)
        self.assertEqual(code, 200)
        self.assertEqual(self.calls[0][0], 'drop-call')
        self.assertNotIn('disconnect', [c[0] for c in self.calls])
        self.assertNotIn('persist-disc', [c[0] for c in self.calls])

    def test_drop_call_without_callback_does_not_disconnect(self):
        kw = dict(self.kw)
        kw.pop('drop_call')
        code, body = handle_control_request('drop-call', 'POST', {'radio_id': 2345875}, **kw)
        self.assertEqual(code, 200)
        self.assertNotIn('disconnect', [c[0] for c in self.calls])
        self.assertNotIn('persist-disc', [c[0] for c in self.calls])

    def test_unknown_peer(self):
        code, body = handle_control_request('disconnect', 'POST', {'radio_id': 1}, **self.kw)
        self.assertEqual(code, 404)
        self.assertIn('error', body)

    def test_ambiguous_owner_id_returns_conflict(self):
        kw = dict(self.kw)

        def ambiguous(_radio_id):
            raise AmbiguousPeerError(
                'Multiple connected peers match; use the exact connected radio ID')

        kw['find_peer'] = ambiguous
        code, body = handle_control_request(
            'device:disconnect', 'POST', {'radio_id': 2345875}, **kw)
        self.assertEqual(code, 409)
        self.assertIn('exact connected radio ID', body['error'])

    def test_talkgroup_add_and_remove(self):
        code, body = handle_control_request(
            'talkgroup', 'POST', {'radio_id': 2345875, 'talkgroup': 2350}, **self.kw)
        self.assertEqual(code, 200)
        self.assertEqual(body['action'], 'talkgroup-add')
        self.assertEqual(self.calls[-1], ('activate', 'MASTER-1', 2350, 2))
        code, body = handle_control_request(
            'talkgroup', 'DELETE', {'radio_id': 2345875, 'group': 2350, 'slot': 1}, **self.kw)
        self.assertEqual(code, 200)
        self.assertEqual(self.calls[-1], ('deactivate', 'MASTER-1', 2350, 1))

    def test_talkgroup_get_rejected(self):
        code, body = handle_control_request(
            'talkgroup', 'GET', {'radio_id': 2345875, 'talkgroup': 2350}, **self.kw)
        self.assertEqual(code, 405)
        self.assertFalse(any(c[0] in ('activate', 'deactivate') for c in self.calls))

    def test_static_talkgroup_slot_zero_is_ts2(self):
        code, body = handle_control_request(
            'device:static-talkgroup',
            'POST',
            {'radio_id': 2345875, 'group': 91, 'slot': 0},
            **self.kw,
        )
        self.assertEqual(code, 200)
        self.assertEqual(body['slot'], 2)
        self.assertEqual(self.calls[-1], ('add-static', 'MASTER-1', 91, 2))

    def test_static_talkgroup_delete_path(self):
        code, body = handle_control_request(
            'device:static-talkgroup',
            'DELETE',
            {'radio_id': 2345875},
            path_parts=['1', '9'],
            **self.kw,
        )
        self.assertEqual(code, 200)
        self.assertEqual(self.calls[-1], ('remove-static', 'MASTER-1', 9, 1))

    def test_static_talkgroup_delete_ignores_radio_in_path(self):
        code, body = handle_control_request(
            'device:static-talkgroup',
            'DELETE',
            {'radio_id': 2345875},
            path_parts=['2345875', '1', '9'],
            **self.kw,
        )
        self.assertEqual(code, 200)
        self.assertEqual(body['slot'], 1)
        self.assertEqual(self.calls[-1], ('remove-static', 'MASTER-1', 9, 1))

    def test_get_peer(self):
        code, body = handle_control_request(
            'device:peer', 'GET', {}, path_parts=['2345875'], **self.kw)
        self.assertEqual(code, 200)
        self.assertTrue(body['connected'])
        self.assertEqual(body['statics'][0]['group'], 2350)

    def test_slot_default(self):
        self.assertEqual(slot_from_body({}), 2)
        self.assertEqual(slot_from_body({'slot': 0}), 2)
        self.assertEqual(slot_from_body({'slot': 1}), 1)
        self.assertEqual(static_slot(0), 2)
        self.assertEqual(static_slot(1), 1)
        self.assertEqual(static_slot(2), 2)

    def test_parse_path(self):
        self.assertEqual(parse_control_path('/peer/2340189'), ('peer', ['2340189']))
        self.assertEqual(parse_control_path('/static-talkgroup/2/2350'), ('static-talkgroup', ['2', '2350']))
        self.assertEqual(
            parse_control_path('/device/peer/2340189'),
            ('device:peer', ['2340189']))
        self.assertEqual(
            parse_control_path('/device/static-talkgroup/2/2350'),
            ('device:static-talkgroup', ['2', '2350']))

    def test_device_cannot_invoke_ops_talkgroup(self):
        code, _body = handle_control_request(
            'device:talkgroup', 'POST',
            {'radio_id': 2345875, 'group': 91}, **self.kw)
        self.assertEqual(code, 404)
        self.assertFalse(any(call[0] == 'activate' for call in self.calls))

    def test_ops_kick_ban_and_offline_unban(self):
        code, body = handle_control_request(
            'kick', 'POST', {'radio_id': 2345875}, **self.kw)
        self.assertEqual(code, 200)
        self.assertTrue(body['kicked'])
        code, body = handle_control_request(
            'ban', 'POST',
            {'radio_id': 2345875, 'duration_seconds': 600, 'reason': 'loop'},
            **self.kw)
        self.assertEqual(code, 200)
        self.assertEqual(body['expires_at'], 1234)
        code, body = handle_control_request(
            'unban', 'POST', {'radio_id': 1}, **self.kw)
        self.assertEqual(code, 200)
        self.assertTrue(body['removed'])
        self.assertEqual(
            [call[0] for call in self.calls], ['kick', 'ban', 'unban'])

    def test_ban_duration_is_bounded(self):
        code, _body = handle_control_request(
            'ban', 'POST',
            {'radio_id': 2345875, 'duration_seconds': 0}, **self.kw)
        self.assertEqual(code, 400)

    def test_missing_radio(self):
        code, _body = handle_control_request('disconnect', 'POST', {}, **self.kw)
        self.assertEqual(code, 400)

    def test_runtime_bridge_uses_main_when_config_present(self):
        import sys
        from control_api import _runtime_bridge
        main = sys.modules['__main__']
        main.CONFIG = {'SYSTEMS': {}}
        try:
            self.assertIs(_runtime_bridge(), main)
        finally:
            delattr(main, 'CONFIG')


class TestRadioIdCore(unittest.TestCase):
    def test_essid_strip(self):
        self.assertEqual(radio_id_core(235287), '235287')
        self.assertEqual(radio_id_core(2340189), '2340189')
        self.assertEqual(radio_id_core(234018901), '2340189')
        self.assertEqual(radio_id_core(234018999), '2340189')
        self.assertEqual(radio_id_core(23401890), '23401890')
        self.assertTrue(radio_ids_match(2340189, 234018901))
        self.assertFalse(radio_ids_match(2340189, 2340190))
        self.assertFalse(radio_ids_match(2340189, 23401890))

    def test_merge_options(self):
        out = merge_ts_into_options('VOICE=1;TS2=9;', '9,10', '2350')
        self.assertIn('VOICE=1', out)
        self.assertIn('TS1=9,10', out)
        self.assertIn('TS2=2350', out)
        self.assertNotIn('TS2=9', out)
        disc = merge_ts_into_options('TS2=2350;', False, '2350', disc=True)
        self.assertIn('DISC=1', disc)
        from_lists = merge_ts_into_options('VOICE=1;', ['9', '10'], ['2350'])
        self.assertIn('TS1=9,10', from_lists)
        self.assertIn('TS2=2350', from_lists)
        self.assertIn('VOICE=1', from_lists)

    def test_peer_own_options_ignores_master_last_writer(self):
        peer_a = b'\x00\x23\xc5\x93'
        peer_b = b'\x00\x23\xc5\x94'
        cfg = {
            'MODE': 'MASTER',
            'OPTIONS': 'TS2=9;',
            'PEERS': {
                peer_a: {'CONNECTION': 'YES', 'OPTIONS': 'TS2=2350;'},
                peer_b: {'CONNECTION': 'YES', 'OPTIONS': 'TS1=91;'},
            },
        }
        self.assertEqual(peer_own_options(cfg, peer_a), 'TS2=2350;')
        ts1, ts2 = ts_lists_from_options(peer_own_options(cfg, peer_a))
        self.assertEqual(ts1, [])
        self.assertEqual(ts2, ['2350'])
        self.assertFalse(other_peer_has_static(cfg, 2, 2350, except_peer_id=peer_a))
        self.assertTrue(other_peer_has_static(cfg, 1, 91, except_peer_id=peer_a))
        self.assertEqual(peer_own_options(cfg, b'\x00\x00\x00\x01'), '')
        self.assertTrue(store_peer_options(cfg, peer_a, 'TS2=91;'))
        self.assertEqual(peer_own_options(cfg, peer_a), 'TS2=91;')
        self.assertEqual(cfg['OPTIONS'], 'TS2=9;')
        self.assertFalse(store_peer_options(cfg, b'\x00\x00\x00\x01', 'TS2=1;'))
        self.assertEqual(cfg['OPTIONS'], 'TS2=9;')

    def test_union_peer_statics_ignores_master_last_writer(self):
        from selfcare_db import master_has_peer_options, union_peer_static_lists
        peer_a = b'\x00\x23\xc5\x93'
        peer_b = b'\x00\x23\xc5\x94'
        cfg = {
            'MODE': 'MASTER',
            '_default_options': 'TS2=9;',
            'OPTIONS': 'TS2=999;',
            'PEERS': {
                peer_a: {'CONNECTION': 'YES', 'OPTIONS': 'TS2=2350;'},
                peer_b: {'CONNECTION': 'YES', 'OPTIONS': 'TS1=91;TS2=2351;'},
            },
        }
        self.assertTrue(master_has_peer_options(cfg))
        ts1, ts2 = union_peer_static_lists(cfg)
        self.assertEqual(ts1, ['91'])
        self.assertEqual(ts2, ['9', '2350', '2351'])

    def test_other_peer_has_static_skips_offline_peers(self):
        peer_a = b'\x00\x23\xc5\x93'
        peer_b = b'\x00\x23\xc5\x94'
        cfg = {
            'MODE': 'MASTER',
            'PEERS': {
                peer_a: {'CONNECTION': 'YES', 'OPTIONS': 'TS2=2350;'},
                peer_b: {'CONNECTION': 'WAITING_CONFIG', 'OPTIONS': 'TS2=2350;'},
            },
        }
        self.assertFalse(other_peer_has_static(cfg, 2, 2350, except_peer_id=peer_a))

    def test_config_default_keeps_live_static(self):
        cfg = {
            'MODE': 'MASTER',
            '_default_options': 'TS1=91;TS2=2350;',
            'PEERS': {},
        }
        self.assertTrue(live_static_required(cfg, 1, 91))
        self.assertTrue(live_static_required(cfg, 2, 2350))
        self.assertFalse(live_static_required(cfg, 2, 91))

    def test_control_lookup_rejects_multiple_online_essids(self):
        peer_a = (234018901).to_bytes(4, 'big')
        peer_b = (234018902).to_bytes(4, 'big')
        cfg = {
            'MASTER-1': {
                'MODE': 'MASTER',
                'ENABLED': True,
                'PEERS': {
                    peer_a: {'CONNECTION': 'YES', 'RADIO_ID': '234018901'},
                    peer_b: {'CONNECTION': 'YES', 'RADIO_ID': '234018902'},
                },
            },
        }
        with self.assertRaises(AmbiguousPeerError):
            find_connected_dmr_peer(cfg, 2340189, require_unique=True)
        self.assertEqual(
            find_connected_dmr_peer(cfg, 234018901, require_unique=True),
            ('MASTER-1', peer_a))

    def test_empty_peer_options_still_uses_union_not_last_writer(self):
        from selfcare_db import master_has_peer_options, union_peer_static_lists
        peer_a = b'\x00\x23\xc5\x93'
        cfg = {
            'MODE': 'MASTER',
            '_default_options': 'TS2=9;',
            'OPTIONS': 'TS2=999;',
            'PEERS': {
                peer_a: {'CONNECTION': 'YES', 'OPTIONS': ''},
            },
        }
        self.assertTrue(master_has_peer_options(cfg))
        ts1, ts2 = union_peer_static_lists(cfg)
        self.assertEqual(ts1, [])
        self.assertEqual(ts2, ['9'])

    def test_ipsc_falls_back_to_slot_options(self):
        peer = (235287).to_bytes(4, 'big')
        cfg = {
            'MODE': 'IPSC',
            'OPTIONS': 'TS1=9;TS2=2350;',
            'PEERS': {peer: {'CONNECTION': 'YES'}},
        }
        self.assertEqual(peer_own_options(cfg, peer), 'TS1=9;TS2=2350;')
        self.assertTrue(store_peer_options(cfg, peer, 'TS2=91;'))
        self.assertEqual(cfg['OPTIONS'], 'TS2=91;')
        self.assertEqual(peer_own_options(cfg, peer), 'TS2=91;')


    def test_queue_client_disc_does_not_rewrite_options(self):
        from selfcare_db import SelfcareDB
        import inspect
        source = inspect.getsource(SelfcareDB.queue_client_disc)
        self.assertIn('DISC=1', source)
        self.assertIn('modified = 1', source)
        self.assertNotIn('TS1=', source)
        self.assertNotIn('TS2=', source)


if __name__ == '__main__':
    unittest.main()
