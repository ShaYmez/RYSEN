#!/usr/bin/env python3
"""Per-radio drop-dynamic / drop-call RX mute (WPSD SystemX Manager)."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import bridge_master as bm
from bridge_helpers import (
    deactivate_peer_dynamic_bridges,
    dest_peer_rx_blocked,
    drop_peer_stream,
    other_peer_has_dynamic_tg,
    remember_peer_dynamic_tg,
    remember_peer_reflector,
    reset_peer_rx_filters,
    stream_is_dropped,
)
from const import DMRD, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM
from dmr_utils3.utils import bytes_3
from hblink import HBSYSTEM


def _dmrd_packet(dst, stream_id, slot=2, dtype=HBPF_SLT_VHEAD, rf_src=b'\x00\x00\x01'):
    bits = (0x80 if slot == 2 else 0) | (HBPF_DATA_SYNC << 4) | dtype
    return b''.join([
        DMRD,
        b'\x00',
        rf_src,
        bytes_3(dst),
        b'\x00\x00\x00\x00',
        bytes([bits]),
        stream_id,
        b'\x00' * 33,
    ])


class TestDropDynamicAfterUnkey(unittest.TestCase):
    def setUp(self):
        reset_peer_rx_filters()
        self.peer_a = b'\x00\x23\xc5\x93'
        self.peer_b = b'\x00\x23\xc5\x94'
        self.stream = b'\x00\x00\x00\x0a'
        self.bridges = {
            '89134': [{
                'SYSTEM': 'MASTER-1', 'TS': 2, 'TGID': bytes_3(89134),
                'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMER': 0,
            }],
        }

    def tearDown(self):
        reset_peer_rx_filters()

    def test_drop_dynamic_mutes_this_peer_after_unkey_keeps_stanza(self):
        remember_peer_dynamic_tg('MASTER-1', self.peer_a, 2, 89134)
        remember_peer_dynamic_tg('MASTER-1', self.peer_b, 2, 89134)
        changed, dropped = deactivate_peer_dynamic_bridges(
            self.bridges, {}, 'MASTER-1', self.peer_a)
        self.assertFalse(changed)
        self.assertNotIn('89134', dropped)
        self.assertTrue(self.bridges['89134'][0]['ACTIVE'])
        self.assertTrue(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 89134))
        self.assertFalse(dest_peer_rx_blocked(
            'MASTER-1', self.peer_b, self.stream, 2, 89134))

    def test_drop_dynamic_last_member_tears_stanza_and_mutes(self):
        remember_peer_dynamic_tg('MASTER-1', self.peer_a, 2, 89134)
        changed, dropped = deactivate_peer_dynamic_bridges(
            self.bridges, {}, 'MASTER-1', self.peer_a)
        self.assertTrue(changed)
        self.assertIn('89134', dropped)
        self.assertFalse(self.bridges['89134'][0]['ACTIVE'])
        self.assertTrue(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 89134))

    def test_shared_static_leg_still_mutes_dynamic_peer(self):
        self.bridges['89134'][0]['TO_TYPE'] = 'OFF'
        remember_peer_dynamic_tg('MASTER-1', self.peer_a, 2, 89134)
        changed, dropped = deactivate_peer_dynamic_bridges(
            self.bridges, {}, 'MASTER-1', self.peer_a)
        self.assertFalse(changed)
        self.assertEqual(dropped, set())
        self.assertTrue(self.bridges['89134'][0]['ACTIVE'])
        self.assertTrue(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 89134))

    def test_rekey_clears_mute(self):
        remember_peer_dynamic_tg('MASTER-1', self.peer_a, 2, 89134)
        deactivate_peer_dynamic_bridges(
            self.bridges, {}, 'MASTER-1', self.peer_a)
        self.assertTrue(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 89134))
        remember_peer_dynamic_tg('MASTER-1', self.peer_a, 2, 89134)
        self.assertFalse(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 89134))

    def test_essid_drop_does_not_mute_sibling(self):
        remember_peer_dynamic_tg('MASTER-1', self.peer_a, 2, 89134)
        remember_peer_dynamic_tg('MASTER-1', self.peer_b, 2, 89134)
        deactivate_peer_dynamic_bridges(
            self.bridges, {}, 'MASTER-1', self.peer_a)
        self.assertFalse(dest_peer_rx_blocked(
            'MASTER-1', self.peer_b, self.stream, 2, 89134))

    def test_expired_membership_does_not_keep_shared_leg_alive(self):
        remember_peer_dynamic_tg(
            'MASTER-1', self.peer_a, 2, 89134, expires_at=10.0)
        self.assertFalse(other_peer_has_dynamic_tg(
            'MASTER-1', 2, 89134, except_peer_id=self.peer_b))
        self.assertTrue(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 89134, now=11.0))

    def test_reflector_drop_keeps_other_members_shared_leg(self):
        reflectors = {
            '#89134': [{
                'SYSTEM': 'MASTER-1', 'TS': 2, 'TGID': bytes_3(9),
                'ACTIVE': True, 'TO_TYPE': 'ON', 'TIMER': 0,
                'LINKER_PEER': self.peer_a,
            }],
        }
        remember_peer_reflector('MASTER-1', self.peer_a, 2, 89134)
        remember_peer_reflector('MASTER-1', self.peer_b, 2, 89134)
        changed, dropped = deactivate_peer_dynamic_bridges(
            reflectors, {}, 'MASTER-1', self.peer_a)
        self.assertFalse(changed)
        self.assertEqual(dropped, set())
        self.assertTrue(reflectors['#89134'][0]['ACTIVE'])
        self.assertTrue(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 9))
        self.assertFalse(dest_peer_rx_blocked(
            'MASTER-1', self.peer_b, self.stream, 2, 9))

        changed, dropped = deactivate_peer_dynamic_bridges(
            reflectors, {}, 'MASTER-1', self.peer_b)
        self.assertTrue(changed)
        self.assertEqual(dropped, {'#89134'})
        self.assertFalse(reflectors['#89134'][0]['ACTIVE'])


class TestDropCallIncoming(unittest.TestCase):
    def setUp(self):
        reset_peer_rx_filters()
        self.prev_systems = dict(bm.systems)
        self.prev_sub_map = getattr(bm, 'SUB_MAP', None)
        self.prev_config = getattr(bm, 'CONFIG', None)
        self.peer_a = (234018901).to_bytes(4, 'big')
        self.peer_b = (234018902).to_bytes(4, 'big')
        self.stream = b'\x00\x00\x00\x07'
        self.next_stream = b'\x00\x00\x00\x08'
        bm.CONFIG = {'REPORTS': {'REPORT': False}}
        bm.SUB_MAP = {}
        bm.systems.clear()
        bm.systems['MASTER-1'] = SimpleNamespace(STATUS={
            1: {'RX_PEER': b'\x00', 'RX_STREAM_ID': b'\x00',
                'RX_TYPE': bm.HBPF_SLT_VTERM,
                'TX_STREAM_ID': b'\x00', 'TX_TYPE': bm.HBPF_SLT_VTERM},
            2: {'RX_PEER': self.peer_b, 'RX_STREAM_ID': self.stream,
                'RX_TYPE': bm.HBPF_SLT_VHEAD, 'RX_TIME': 1,
                'TX_STREAM_ID': self.stream, 'TX_TYPE': bm.HBPF_SLT_VHEAD},
        })

    def tearDown(self):
        reset_peer_rx_filters()
        bm.systems.clear()
        bm.systems.update(self.prev_systems)
        if self.prev_sub_map is None:
            try:
                delattr(bm, 'SUB_MAP')
            except AttributeError:
                pass
        else:
            bm.SUB_MAP = self.prev_sub_map
        if self.prev_config is None:
            try:
                delattr(bm, 'CONFIG')
            except AttributeError:
                pass
        else:
            bm.CONFIG = self.prev_config

    def test_drop_call_mutes_listener_not_talker(self):
        self.assertEqual(bm.drop_peer_call('MASTER-1', self.peer_a), 1)
        self.assertTrue(stream_is_dropped(
            'MASTER-1', self.peer_a, self.stream))
        self.assertFalse(stream_is_dropped(
            'MASTER-1', self.peer_b, self.stream))
        self.assertEqual(
            bm.systems['MASTER-1'].STATUS[2]['RX_TYPE'],
            bm.HBPF_SLT_VHEAD)
        self.assertTrue(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 89134))
        self.assertFalse(dest_peer_rx_blocked(
            'MASTER-1', self.peer_b, self.stream, 2, 89134))

    def test_new_stream_after_drop_call_is_forwarded(self):
        bm.drop_peer_call('MASTER-1', self.peer_a)
        self.assertFalse(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.next_stream, 2, 89134))

    def test_vterm_releases_mute_so_next_over_returns(self):
        bm.drop_peer_call('MASTER-1', self.peer_a)
        bm.release_dropped_stream('MASTER-1', self.stream)
        self.assertFalse(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 89134))
        self.assertFalse(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.next_stream, 2, 89134))

    def test_same_stream_id_new_vhead_after_gap_returns(self):
        drop_peer_stream(
            'MASTER-1', self.peer_a, self.stream, slot=2, now=10.0)
        self.assertTrue(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 89134,
            frame_type=HBPF_DATA_SYNC, dtype_vseq=0, now=10.2))
        self.assertFalse(dest_peer_rx_blocked(
            'MASTER-1', self.peer_a, self.stream, 2, 89134,
            frame_type=HBPF_DATA_SYNC, dtype_vseq=HBPF_SLT_VHEAD,
            now=11.3))

    def test_same_stream_id_on_both_slots_is_muted_independently(self):
        bm.systems['MASTER-1'].STATUS[1] = {
            'RX_PEER': self.peer_b,
            'RX_STREAM_ID': self.stream,
            'RX_TYPE': bm.HBPF_SLT_VHEAD,
            'RX_TIME': 1,
            'TX_STREAM_ID': self.stream,
            'TX_TYPE': bm.HBPF_SLT_VHEAD,
        }
        self.assertEqual(
            bm.drop_peer_call('MASTER-1', self.peer_a), 2)
        self.assertTrue(stream_is_dropped(
            'MASTER-1', self.peer_a, self.stream, slot=1))
        self.assertTrue(stream_is_dropped(
            'MASTER-1', self.peer_a, self.stream, slot=2))


class TestSendPeersRespectsMute(unittest.TestCase):
    def setUp(self):
        reset_peer_rx_filters()
        self.peer_a = b'\x00\x23\xc5\x93'
        self.peer_b = b'\x00\x23\xc5\x94'
        self.stream = b'\x00\x00\x00\x0b'
        config = {
            'GLOBAL': {'PING_TIME': 5},
            'SYSTEMS': {
                'MASTER-1': {
                    'MODE': 'MASTER',
                    'REPEAT': True,
                    'PEERS': {
                        self.peer_a: {
                            'SOCKADDR': ('10.0.0.1', 62031),
                            'RADIO_ID': '234018901',
                        },
                        self.peer_b: {
                            'SOCKADDR': ('10.0.0.2', 62031),
                            'RADIO_ID': '234018902',
                        },
                    },
                },
            },
        }
        self.master = HBSYSTEM('MASTER-1', config, MagicMock())
        self.master.transport = MagicMock()

    def tearDown(self):
        reset_peer_rx_filters()

    def test_send_peers_skips_muted_listener(self):
        drop_peer_stream(
            'MASTER-1', self.peer_a, self.stream, slot=2)
        pkt = _dmrd_packet(89134, self.stream, dtype=HBPF_SLT_VHEAD,
                           rf_src=bytes_3(2340189))
        self.master.send_peers(pkt)
        dests = [call.args[1] for call in self.master.transport.write.call_args_list]
        self.assertNotIn(('10.0.0.1', 62031), dests)
        self.assertIn(('10.0.0.2', 62031), dests)

    def test_send_peers_new_stream_reaches_previously_muted_peer(self):
        drop_peer_stream(
            'MASTER-1', self.peer_a, self.stream, slot=2)
        nxt = b'\x00\x00\x00\x0c'
        pkt = _dmrd_packet(89134, nxt, dtype=HBPF_SLT_VHEAD,
                           rf_src=bytes_3(2340189))
        self.master.send_peers(pkt)
        dests = [call.args[1] for call in self.master.transport.write.call_args_list]
        self.assertIn(('10.0.0.1', 62031), dests)
        self.assertIn(('10.0.0.2', 62031), dests)

    def test_raw_repeat_honours_listener_mute_and_vterm_releases(self):
        self.master._config['USE_ACL'] = False
        self.master._config['REPEAT'] = True
        self.master._CONFIG['GLOBAL']['USE_ACL'] = False
        for peer, sockaddr in (
                (self.peer_a, ('10.0.0.1', 62031)),
                (self.peer_b, ('10.0.0.2', 62031))):
            self.master._peers[peer].update({
                'CONNECTION': 'YES',
                'LAST_PING': 0,
                'SOCKADDR': sockaddr,
            })
        drop_peer_stream(
            'MASTER-1', self.peer_a, self.stream, slot=2)

        def packet(dtype, stream_id):
            pkt = _dmrd_packet(
                89134, stream_id, dtype=dtype,
                rf_src=bytes_3(2340189))
            pkt = b''.join([pkt[:4], bytes([dtype]), pkt[5:]])
            return b''.join([pkt[:11], self.peer_b, pkt[15:]])

        self.master.master_datagramReceived(
            packet(HBPF_SLT_VHEAD, self.stream),
            ('10.0.0.2', 62031))
        self.assertEqual(self.master.transport.write.call_count, 0)

        self.master.master_datagramReceived(
            packet(HBPF_SLT_VTERM, self.stream),
            ('10.0.0.2', 62031))
        destinations = [
            call.args[1]
            for call in self.master.transport.write.call_args_list
        ]
        self.assertIn(('10.0.0.1', 62031), destinations)


if __name__ == '__main__':
    unittest.main()
