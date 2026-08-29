#!/usr/bin/env python3
"""Unit tests for the FreeSTAR loopback control dispatch (no reactor)."""

import unittest

from control_api import handle_control_request, slot_from_body


class TestControlDispatch(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def find_peer(radio_id):
            if radio_id == 2345875:
                return 'MASTER-1', b'\x00\x23\xc5\x93'
            return None, None

        def disconnect(system, peer_id):
            self.calls.append(('disconnect', system, peer_id))

        def drop_dynamic(system, peer_id=None):
            self.calls.append(('drop-dynamic', system, peer_id))

        def activate_tg(system, tgid, slot, peer_id):
            self.calls.append(('activate', system, tgid, slot))

        def deactivate_tg(system, tgid, slot, peer_id=None):
            self.calls.append(('deactivate', system, tgid, slot))

        self.kw = dict(
            find_peer=find_peer,
            disconnect=disconnect,
            drop_dynamic=drop_dynamic,
            activate_tg=activate_tg,
            deactivate_tg=deactivate_tg,
        )

    def test_disconnect(self):
        code, body = handle_control_request('disconnect', 'POST', {'radio_id': 2345875}, **self.kw)
        self.assertEqual(code, 200)
        self.assertEqual(body['system'], 'MASTER-1')
        self.assertEqual(self.calls[0][0], 'disconnect')

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

    def test_slot_default(self):
        self.assertEqual(slot_from_body({}), 2)
        self.assertEqual(slot_from_body({'slot': 0}), 2)
        self.assertEqual(slot_from_body({'slot': 1}), 1)

    def test_missing_radio(self):
        code, _body = handle_control_request('disconnect', 'POST', {}, **self.kw)
        self.assertEqual(code, 400)


if __name__ == '__main__':
    unittest.main()
