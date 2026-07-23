#!/usr/bin/env python3
"""Hot-path tests for OBP outbound collision handling (no reclaim)."""
import unittest

from bridge_helpers import (
    OBP_OUTBOUND_ECHO,
    OBP_OUTBOUND_REPLACE,
    classify_obp_outbound_collision,
    ensure_obp_inbound_status_keys,
    reclaim_obp_inbound_stream,
)


class TestClassifyObpOutboundCollision(unittest.TestCase):

    def test_none_when_missing(self):
        self.assertIsNone(classify_obp_outbound_collision(None, b'\x00\x09\x26'))
        self.assertIsNone(classify_obp_outbound_collision({}, b'\x00\x09\x26'))
        self.assertIsNone(classify_obp_outbound_collision(
            {'TGID': b'\x00\x09\x26'}, b'\x00\x09\x26'))

    def test_echo_same_tgid(self):
        st = {'_outbound': True, 'TGID': b'\x00\x09\x26'}
        self.assertEqual(
            classify_obp_outbound_collision(st, b'\x00\x09\x26'),
            OBP_OUTBOUND_ECHO)

    def test_replace_different_tgid(self):
        st = {'_outbound': True, 'TGID': b'\x00\x09\x26'}
        self.assertEqual(
            classify_obp_outbound_collision(st, b'\x00\x00\x5f'),
            OBP_OUTBOUND_REPLACE)


class TestEnsureObpInboundStatusKeys(unittest.TestCase):

    def test_backfills_outbound_shaped_status(self):
        st = {
            '_outbound': True,
            'START': 1.0,
            'TGID': b'\x00\x09\x26',
            'packets': 0,
            'loss': 0,
        }
        ensure_obp_inbound_status_keys(st, perf_counter_fn=lambda: 42.0)
        self.assertEqual(st['lastSeq'], False)
        self.assertEqual(st['lastData'], False)
        self.assertIsInstance(st['crcs'], set)
        self.assertEqual(st['1ST'], 42.0)
        st['loss'] += 1
        st['crcs'].add(b'hash')
        st['lastSeq'] = 3
        st['lastData'] = b'pkt'
        self.assertEqual(st['packets'], 0)

    def test_preserves_existing_keys(self):
        st = {
            'packets': 5,
            'loss': 1,
            'lastSeq': 2,
            'lastData': b'x',
            'crcs': {b'a'},
            '1ST': 9.0,
        }
        ensure_obp_inbound_status_keys(st, perf_counter_fn=lambda: 99.0)
        self.assertEqual(st['packets'], 5)
        self.assertEqual(st['1ST'], 9.0)
        self.assertEqual(st['crcs'], {b'a'})


class TestReclaimRemainsUnwired(unittest.TestCase):

    def test_docstring_warns_unwired(self):
        doc = reclaim_obp_inbound_stream.__doc__ or ''
        self.assertIn('unwired', doc.lower())

    def test_bridge_master_does_not_call_reclaim(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            src = fh.read()
        self.assertNotIn('reclaim_obp_inbound_stream(', src)


class TestCollisionReplaceSemantics(unittest.TestCase):

    def test_replace_deletes_then_fresh_inbound(self):
        """Different-TGID collision: delete outbound, create fresh inbound (not reclaim)."""
        sid = b'\x01\x02\x03\x04'
        status = {
            sid: {
                '_outbound': True,
                'TGID': b'\x00\x09\x26',
                'packets': 12,
                'H_LC': b'lc',
            },
        }
        action = classify_obp_outbound_collision(status[sid], b'\x00\x00\x5f')
        self.assertEqual(action, OBP_OUTBOUND_REPLACE)
        del status[sid]
        status[sid] = {
            'START': 100.0,
            'CONTENTION': False,
            'RFS': b'\xaa\xbb\xcc',
            'TGID': b'\x00\x00\x5f',
            '1ST': 1.0,
            'lastSeq': False,
            'lastData': False,
            'RX_PEER': b'\x11\x22\x33',
            'packets': 1,
            'loss': 0,
            'crcs': set(),
        }
        self.assertNotIn('_outbound', status[sid])
        self.assertEqual(status[sid]['TGID'], b'\x00\x00\x5f')
        self.assertEqual(status[sid]['packets'], 1)


class TestMeshHygieneWiring(unittest.TestCase):

    def test_bridge_master_wires_keeps(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('classify_obp_outbound_collision', source)
        self.assertIn('OBP_OUTBOUND_ECHO', source)
        self.assertIn('obp_target_already_has_inbound', source)
        self.assertIn("'_outbound': True", source)
        self.assertIn('refresh_obp_system_list()', source)
        self.assertIn('_obp_loop_hr_times', source)
        self.assertIn('ensure_obp_inbound_status_keys', source)


if __name__ == '__main__':
    unittest.main()
