#!/usr/bin/env python3
"""Unit tests for the FreeSTAR loopback control dispatch (no reactor)."""

import unittest

from control_api import (
    handle_control_request,
    parse_control_path,
    slot_from_body,
    static_slot,
)
from selfcare_db import merge_ts_into_options, radio_id_core, radio_ids_match


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
        )

    def test_disconnect(self):
        code, body = handle_control_request('disconnect', 'POST', {'radio_id': 2345875}, **self.kw)
        self.assertEqual(code, 200)
        self.assertEqual(body['system'], 'MASTER-1')
        self.assertEqual(self.calls[0][0], 'disconnect')
        self.assertEqual(self.calls[1][0], 'persist-disc')

    def test_drop_call_is_not_disconnect(self):
        code, body = handle_control_request('drop-call', 'POST', {'radio_id': 2345875}, **self.kw)
        self.assertEqual(code, 200)
        self.assertEqual(self.calls[0][0], 'drop-call')
        self.assertNotIn('disconnect', [c[0] for c in self.calls])
        self.assertNotIn('persist-disc', [c[0] for c in self.calls])

    def test_unknown_peer(self):
        code, body = handle_control_request('disconnect', 'POST', {'radio_id': 1}, **self.kw)
        self.assertEqual(code, 404)
        self.assertIn('error', body)

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

    def test_static_talkgroup_slot_zero_is_ts2(self):
        code, body = handle_control_request(
            'static-talkgroup',
            'POST',
            {'radio_id': 2345875, 'group': 91, 'slot': 0},
            **self.kw,
        )
        self.assertEqual(code, 200)
        self.assertEqual(body['slot'], 2)
        self.assertEqual(self.calls[-1], ('add-static', 'MASTER-1', 91, 2))

    def test_static_talkgroup_delete_path(self):
        code, body = handle_control_request(
            'static-talkgroup',
            'DELETE',
            {'radio_id': 2345875},
            path_parts=['1', '9'],
            **self.kw,
        )
        self.assertEqual(code, 200)
        self.assertEqual(self.calls[-1], ('remove-static', 'MASTER-1', 9, 1))

    def test_get_peer(self):
        code, body = handle_control_request(
            'peer', 'GET', {}, path_parts=['2345875'], **self.kw)
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
        self.assertEqual(radio_id_core(2340189), '2340189')
        self.assertEqual(radio_id_core(234018901), '2340189')
        self.assertEqual(radio_id_core(234018999), '2340189')
        self.assertTrue(radio_ids_match(2340189, 234018901))
        self.assertFalse(radio_ids_match(2340189, 2340190))

    def test_merge_options(self):
        out = merge_ts_into_options('VOICE=1;TS2=9;', '9,10', '2350')
        self.assertIn('VOICE=1', out)
        self.assertIn('TS1=9,10', out)
        self.assertIn('TS2=2350', out)
        self.assertNotIn('TS2=9', out)
        disc = merge_ts_into_options('TS2=2350;', False, '2350', disc=True)
        self.assertIn('DISC=1', disc)


if __name__ == '__main__':
    unittest.main()
