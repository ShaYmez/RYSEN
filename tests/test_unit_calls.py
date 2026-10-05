#!/usr/bin/env python3
"""Local subscriber private calls: reflector isolation, delivery, and ACLs."""
import unittest
from unittest.mock import MagicMock

import bridge_master as bm
from const import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, HBPF_VOICE
from dmr_utils3.utils import bytes_3
from hblink import HBSYSTEM


CALLER = 2341111
CALLEE = 2345875
ESSID = 234587501
STREAM = b'\x00\x00\x00\x11'
STREAM2 = b'\x00\x00\x00\x22'
PEER = (234111101).to_bytes(4, 'big')


def _idle_slot():
    return {
        'RX_STREAM_ID': b'\x00\x00\x00\x00',
        'RX_TYPE': HBPF_SLT_VTERM,
        'TX_TYPE': HBPF_SLT_VTERM,
        'TX_TIME': 0,
        'TX_STREAM_ID': None,
        'TX_TGID': b'\x00\x00\x00',
        'TX_RFS': b'\x00\x00\x00',
        'RX_TIME': 0,
        'RX_PEER': b'\x00',
        'RX_SEQ': 0,
        'RX_RFS': b'\x00',
        'RX_TGID': b'\x00\x00\x00',
        'packets': 0,
        'crcs': set(),
        '_allStarMode': False,
        'VOICE_STREAM': False,
    }


def _voice_packet(src, dst, stream, slot, seq, dtype, payload=None,
                  frame_type=HBPF_DATA_SYNC):
    bits = 0x40 | (frame_type << 4) | (dtype & 0x0F)
    if slot == 2:
        bits |= 0x80
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[4] = seq
    packet[5:8] = bytes_3(src)
    packet[8:11] = bytes_3(dst)
    packet[11:15] = PEER
    packet[15] = bits
    packet[16:20] = stream
    if payload:
        packet[20:20 + len(payload)] = payload
    return bytes(packet)


class _Target:
    def __init__(self):
        self.sent = []
        self.extra = []
        self.STATUS = {1: _idle_slot(), 2: _idle_slot()}

    def send_system(self, packet, *args, **kwargs):
        self.sent.append(packet)
        self.extra.append(args)


class UnitCallFixture(unittest.TestCase):
    def setUp(self):
        self._systems = dict(bm.systems)
        bm.systems.clear()
        self._config = getattr(bm, 'CONFIG', None)
        self._sub_map = getattr(bm, 'SUB_MAP', None)
        self._bridges = getattr(bm, 'BRIDGES', None)
        bm.SUB_MAP = {}
        bm.BRIDGES = {}
        bm._UNIT_HOMES.clear()
        bm._UNIT_TRANSIT_STREAMS.clear()
        bm.CONFIG = {
            'REPORTS': {'REPORT': False},
            'ALLSTAR': {'ENABLED': False},
            'SYSTEMS': {},
            '_SERVER_IDS': {
                '2020': 'Greece',
                '2040': 'Europe',
                '2342': 'UK',
                '2381': 'UK',
                '3180': 'USA',
            },
        }

    def tearDown(self):
        bm.systems.clear()
        bm.systems.update(self._systems)
        if self._config is None:
            if hasattr(bm, 'CONFIG'):
                delattr(bm, 'CONFIG')
        else:
            bm.CONFIG = self._config
        bm._UNIT_HOMES.clear()
        bm._UNIT_TRANSIT_STREAMS.clear()
        if self._sub_map is None:
            delattr(bm, 'SUB_MAP')
        else:
            bm.SUB_MAP = self._sub_map
        if self._bridges is None:
            if hasattr(bm, 'BRIDGES'):
                delattr(bm, 'BRIDGES')
        else:
            bm.BRIDGES = self._bridges

    def _system(self, name, mode, peers=None, hang=5):
        bm.CONFIG['SYSTEMS'][name] = {
            'MODE': mode,
            'ENABLED': True,
            'GROUP_HANGTIME': hang,
            'ANNOUNCEMENT_LANGUAGE': 'en_US',
            'DEFAULT_UA_TIMER': 15,
            'PEERS': peers or {},
        }

    def _router(self, name):
        router = bm.routerHBP.__new__(bm.routerHBP)
        router._system = name
        router._CONFIG = {'GLOBAL': {'SERVER_ID': b'\x00\x00\x00\x01'}}
        router._report = MagicMock()
        router.STATUS = {1: _idle_slot(), 2: _idle_slot()}
        router._unit_voice_routes = {}
        return router

    def _peer(self, radio_id):
        return {
            (radio_id).to_bytes(4, 'big'): {'CONNECTION': 'YES'},
        }


class TestUnitVoiceDelivery(UnitCallFixture):
    def test_cross_slot_forwards_once_and_keeps_ambe(self):
        self._system('SYSTEM-A', 'MASTER')
        essid = (ESSID).to_bytes(4, 'big')
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        router = self._router('SYSTEM-A')
        resolves = []
        original = router._resolve_unit_target

        def _count(*args, **kwargs):
            resolves.append(1)
            return original(*args, **kwargs)

        router._resolve_unit_target = _count
        payload = b'\xAB' * 33
        dst = bytes_3(CALLEE)
        for seq in (0, 1):
            packet = _voice_packet(
                CALLER, CALLEE, STREAM, 1, seq, HBPF_SLT_VHEAD, payload)
            router._forward_unit_voice(
                dst, 1, packet[15], packet, packet[20:53], STREAM, PEER,
                bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 100.0)

        self.assertEqual(len(resolves), 1)
        self.assertEqual(len(target.sent), 2)
        self.assertTrue(target.sent[0][15] & 0x80)
        self.assertTrue(target.sent[0][15] & 0x40)
        self.assertEqual(target.sent[0][20:53], payload)
        self.assertEqual(target.sent[1][20:53], payload)

    def test_sub_map_beats_another_essid_and_repeater_keeps_heard_slot(self):
        repeater_peer = (235287).to_bytes(4, 'big')
        self._system('SYSTEM-A', 'MASTER')
        self._system('IPSC-1', 'IPSC', peers={repeater_peer: {'CONNECTION': 'YES'}})
        self._system('SYSTEM-C', 'MASTER', peers=self._peer(ESSID))
        bm.SUB_MAP[bytes_3(CALLEE)] = (
            'IPSC-1', 1, None, 50.0, repeater_peer)
        target = _Target()
        other = _Target()
        bm.systems['IPSC-1'] = target
        bm.systems['SYSTEM-C'] = other
        router = self._router('SYSTEM-A')
        packet = _voice_packet(CALLER, CALLEE, STREAM, 2, 0, HBPF_SLT_VHEAD)
        router._forward_unit_voice(
            bytes_3(CALLEE), 2, packet[15], packet, packet[20:53], STREAM,
            PEER, bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 100.0)
        self.assertEqual(len(target.sent), 1)
        self.assertFalse(target.sent[0][15] & 0x80)
        self.assertEqual(other.sent, [])

    def test_stale_sub_map_falls_through_to_a_live_hotspot(self):
        gone = (CALLEE).to_bytes(4, 'big')
        self._system('SYSTEM-A', 'MASTER')
        self._system('SYSTEM-OLD', 'MASTER', peers={})
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        bm.SUB_MAP[bytes_3(CALLEE)] = ('SYSTEM-OLD', 2, None, 10.0, gone)
        target = _Target()
        bm.systems['SYSTEM-OLD'] = _Target()
        bm.systems['SYSTEM-B'] = target
        router = self._router('SYSTEM-A')
        packet = _voice_packet(CALLER, CALLEE, STREAM, 2, 0, HBPF_SLT_VHEAD)
        router._forward_unit_voice(
            bytes_3(CALLEE), 2, packet[15], packet, packet[20:53], STREAM,
            PEER, bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 100.0)
        self.assertEqual(len(target.sent), 1)

    def test_missing_subscriber_is_a_clean_drop(self):
        self._system('SYSTEM-A', 'MASTER')
        router = self._router('SYSTEM-A')
        bm.CONFIG['REPORTS']['REPORT'] = True
        order = []
        router._report.send_bridgeEvent.side_effect = lambda _event: order.append('hear')
        router._resolve_unit_target = lambda *_args: order.append('resolve') or None
        packet = _voice_packet(CALLER, CALLEE, STREAM, 2, 0, HBPF_SLT_VHEAD)
        router._forward_unit_voice(
            bytes_3(CALLEE), 2, packet[15], packet, packet[20:53], STREAM,
            PEER, bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 100.0)
        self.assertFalse(router._unit_voice_routes[STREAM]['send'])
        self.assertEqual(order[:2], ['hear', 'resolve'])

    def test_busy_slot_drops_and_a_later_stream_can_proceed(self):
        self._system('SYSTEM-A', 'MASTER', hang=5)
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID), hang=5)
        target = _Target()
        target.STATUS[2]['TX_TYPE'] = HBPF_SLT_VHEAD
        target.STATUS[2]['TX_TIME'] = 100.0
        bm.systems['SYSTEM-B'] = target
        router = self._router('SYSTEM-A')
        packet = _voice_packet(CALLER, CALLEE, STREAM, 2, 0, HBPF_SLT_VHEAD)
        router._forward_unit_voice(
            bytes_3(CALLEE), 2, packet[15], packet, packet[20:53], STREAM,
            PEER, bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 100.0)
        self.assertEqual(target.sent, [])
        self.assertFalse(router._unit_voice_routes[STREAM]['send'])

        router._unit_voice_routes.clear()
        target.STATUS[2]['TX_TYPE'] = HBPF_SLT_VTERM
        target.STATUS[2]['TX_TIME'] = 100.0
        later = b'\x00\x00\x00\x22'
        router._forward_unit_voice(
            bytes_3(CALLEE), 2, packet[15], packet, packet[20:53], later,
            PEER, bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 106.0)
        self.assertEqual(len(target.sent), 1)

    def test_voice_burst_c_stays_busy_and_the_answer_does_not_wait_out_hangtime(self):
        self._system('SYSTEM-A', 'MASTER', hang=5)
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID), hang=5)
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        router = self._router('SYSTEM-A')
        burst = _voice_packet(CALLER, CALLEE, STREAM, 1, 2, 2)
        router._forward_unit_voice(
            bytes_3(CALLEE), 1, burst[15], burst, burst[20:53], STREAM, PEER,
            bytes_3(CALLER), HBPF_VOICE, 2, 100.0)
        self.assertEqual(target.STATUS[2]['TX_TYPE'], HBPF_SLT_VHEAD)
        other = b'\x00\x00\x00\x33'
        router._forward_unit_voice(
            bytes_3(CALLEE), 1, burst[15], burst, burst[20:53], other, PEER,
            bytes_3(CALLER), HBPF_VOICE, 2, 100.1)
        self.assertEqual(len(target.sent), 1)

        router._unit_voice_routes.clear()
        target.STATUS[2]['TX_TYPE'] = HBPF_SLT_VTERM
        target.STATUS[2]['TX_TGID'] = bytes_3(CALLEE)
        target.STATUS[2]['TX_TIME'] = 100.0
        target.STATUS[2]['RX_TYPE'] = HBPF_SLT_VTERM
        target.STATUS[2]['RX_TGID'] = bytes_3(CALLEE)
        target.STATUS[2]['RX_TIME'] = 104.5
        answer = b'\x00\x00\x00\x44'
        router._forward_unit_voice(
            bytes_3(CALLEE), 1, burst[15], burst, burst[20:53], answer, PEER,
            bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 105.0)
        self.assertEqual(len(target.sent), 2)

        router._unit_voice_routes.clear()
        target.sent.clear()
        target.STATUS[2]['RX_TGID'] = bytes_3(2350)
        target.STATUS[2]['RX_TIME'] = 104.5
        target.STATUS[2]['TX_TGID'] = bytes_3(2350)
        target.STATUS[2]['TX_TIME'] = 104.5
        target.STATUS[2]['TX_TYPE'] = HBPF_SLT_VTERM
        blocked = b'\x00\x00\x00\x55'
        router._forward_unit_voice(
            bytes_3(CALLEE), 1, burst[15], burst, burst[20:53], blocked, PEER,
            bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 105.0)
        self.assertEqual(target.sent, [])

    def test_ber_and_rssi_are_kept(self):
        self._system('SYSTEM-A', 'MASTER')
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        router = self._router('SYSTEM-A')
        packet = bytearray(_voice_packet(CALLER, CALLEE, STREAM, 1, 0, HBPF_SLT_VHEAD))
        packet[53] = 0x3C
        packet[54] = 0x80
        packet = bytes(packet)
        router._forward_unit_voice(
            bytes_3(CALLEE), 1, packet[15], packet, packet[20:53], STREAM, PEER,
            bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 100.0)
        self.assertEqual(target.sent[0][53:55], b'\x3c\x80')
        self.assertEqual(target.extra[0][1:3], (b'\x3c', b'\x80'))

    def test_end_reports_once_and_releases_the_slot_after_hangtime(self):
        self._system('SYSTEM-A', 'MASTER', hang=5)
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID), hang=5)
        bm.CONFIG['REPORTS']['REPORT'] = True
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        router = self._router('SYSTEM-A')
        head = _voice_packet(CALLER, CALLEE, STREAM, 2, 0, HBPF_SLT_VHEAD)
        term = _voice_packet(CALLER, CALLEE, STREAM, 2, 1, HBPF_SLT_VTERM)
        router._forward_unit_voice(
            bytes_3(CALLEE), 2, head[15], head, head[20:53], STREAM, PEER,
            bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 100.0)
        router._forward_unit_voice(
            bytes_3(CALLEE), 2, term[15], term, term[20:53], STREAM, PEER,
            bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VTERM, 101.0)
        self.assertNotIn(STREAM, router._unit_voice_routes)
        self.assertEqual(target.STATUS[2]['TX_TYPE'], HBPF_SLT_VTERM)
        kinds = [
            call.args[0].split(b',')[1]
            for call in router._report.send_bridgeEvent.call_args_list
        ]
        self.assertEqual(kinds, [b'START', b'END'])


class TestSubscriberPrivateCallSkipsReflector(UnitCallFixture):
    def _receive(self, router, dst, stream, seq, dtype, slot=2):
        packet = _voice_packet(CALLER, dst, stream, slot, seq, dtype)
        router.dmrd_received(
            PEER, bytes_3(CALLER), bytes_3(dst), seq, slot, 'unit',
            HBPF_DATA_SYNC, dtype, stream, packet)

    def test_subscriber_call_leaves_the_reflector_and_does_not_announce(self):
        self._system('SYSTEM-A', 'MASTER')
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        router = self._router('SYSTEM-A')
        router._build_reflector_announce_say = MagicMock(
            side_effect=AssertionError('subscriber call announced'))
        router._cancel_reflector_timers = MagicMock()
        bm.SUB_MAP[bytes_3(CALLER)] = (
            'SYSTEM-A', 2, bytes_3(2350), 1.0, PEER)
        bm.BRIDGES = {
            '#2350': [{
                'SYSTEM': 'SYSTEM-A',
                'TS': 2,
                'TO_TYPE': 'ON',
                'ACTIVE': True,
                'TIMER': 0,
                'LINKER_PEER': PEER,
                'OFF': [],
                'RESET': [],
                'ON': [bytes_3(2350)],
            }],
        }
        with unittest.mock.patch.object(bm, 'make_single_reflector') as make:
            self._receive(router, CALLEE, STREAM, 0, HBPF_SLT_VHEAD)
            self._receive(router, CALLEE, STREAM, 1, HBPF_SLT_VTERM)
        make.assert_not_called()
        router._build_reflector_announce_say.assert_not_called()
        self.assertTrue(bm.BRIDGES['#2350'][0]['ACTIVE'])
        self.assertEqual(len(target.sent), 2)
        stored = bm.SUB_MAP[bytes_3(CALLER)]
        self.assertEqual(stored[2], bytes_3(2350))

    def test_dial_link_disconnect_and_parrot_still_take_their_own_paths(self):
        self._system('SYSTEM-A', 'MASTER')
        router = self._router('SYSTEM-A')
        router._forward_parrot_unit_voice = MagicMock()
        router._forward_unit_voice = MagicMock()
        router._cancel_reflector_timers = MagicMock()
        bm.BRIDGES = {
            '#91': [{
                'SYSTEM': 'SYSTEM-A',
                'TS': 2,
                'TGID': bytes_3(9),
                'TO_TYPE': 'ON',
                'ACTIVE': True,
                'TIMER': 50,
                'TIMEOUT': 60,
                'LINKER_PEER': PEER,
                'OFF': [],
                'RESET': [],
                'ON': [bytes_3(91)],
            }],
        }
        with unittest.mock.patch.object(bm, 'make_single_reflector') as make, \
                unittest.mock.patch.object(bm, 'disconnect_dial_reflectors') as disc:
            self._receive(router, 2350, b'\x00\x00\x00\x31', 0, HBPF_SLT_VHEAD)
            make.assert_called()
            self._receive(router, 4000, b'\x00\x00\x00\x32', 0, HBPF_SLT_VHEAD)
            disc.assert_called_with('SYSTEM-A')
            self._receive(router, 9990, b'\x00\x00\x00\x33', 0, HBPF_SLT_VHEAD)
        router._forward_parrot_unit_voice.assert_called()
        router._forward_unit_voice.assert_not_called()


class TestHomebrewUnitAcl(unittest.TestCase):
    def _master(self):
        peer = (CALLER).to_bytes(4, 'big')
        sockaddr = ('10.0.0.8', 50000)
        master = HBSYSTEM.__new__(HBSYSTEM)
        master._system = 'SYSTEM-0'
        master._peers = {
            peer: {'CONNECTION': 'YES', 'SOCKADDR': sockaddr, 'LAST_PING': 0},
        }
        master._laststrid = {1: b'', 2: b''}
        master._repeat_seq = {}
        allow = (False, [])
        deny_tg = (True, [])
        master._CONFIG = {'GLOBAL': {
            'USE_ACL': True,
            'SUB_ACL': allow,
            'TG1_ACL': deny_tg,
            'TG2_ACL': deny_tg,
        }}
        master._config = {
            'USE_ACL': True,
            'SUB_ACL': allow,
            'TG1_ACL': deny_tg,
            'TG2_ACL': deny_tg,
            'REPEAT': False,
        }
        master.received = []
        master.dmrd_received = lambda *args, **kwargs: master.received.append(args)
        return master, peer, sockaddr

    def _packet(self, peer, unit):
        packet = bytearray(55)
        packet[0:4] = b'DMRD'
        packet[4] = 1
        packet[5:8] = bytes_3(CALLER)
        packet[8:11] = bytes_3(CALLEE if unit else 2350)
        packet[11:15] = peer
        bits = 0x80 | (HBPF_DATA_SYNC << 4) | HBPF_SLT_VHEAD
        if unit:
            bits |= 0x40
        packet[15] = bits
        packet[16:20] = STREAM
        return bytes(packet)

    def test_talkgroup_acl_does_not_drop_a_unit_call(self):
        master, peer, sockaddr = self._master()
        master.master_datagramReceived(self._packet(peer, True), sockaddr)
        master.master_datagramReceived(self._packet(peer, False), sockaddr)
        self.assertEqual(len(master.received), 1)
        self.assertEqual(master.received[0][5], 'unit')


class TestGlobalUnitVoice(UnitCallFixture):
    def _obp(self, name, net_id, host):
        self._system(name, 'OPENBRIDGE')
        bm.CONFIG['SYSTEMS'][name].update({
            'ENHANCED_OBP': True,
            'VER': 5,
            'NETWORK_ID': int(net_id).to_bytes(4, 'big'),
            'TARGET_IP': host,
            '_bcka': 100.0,
        })

    def _origin(self):
        self._system('SYSTEM-A', 'MASTER')
        router = self._router('SYSTEM-A')
        router._CONFIG['SYSTEMS'] = bm.CONFIG['SYSTEMS']
        router._unit_hub_inline = True
        return router

    def _send(self, router, slot=2):
        return self._send_at(router, STREAM, 100.0, slot)

    def _send_at(self, router, stream, when, slot=2):
        packet = _voice_packet(CALLER, CALLEE, stream, slot, 0, HBPF_SLT_VHEAD)
        router._forward_unit_voice(
            bytes_3(CALLEE), slot, packet[15], packet, packet[20:53], stream,
            PEER, bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, when)
        return packet

    def _home_here(self):
        bm.CONFIG['GLOBAL'] = {'SERVER_ID': (2342).to_bytes(4, 'big')}
        bm.CONFIG['ALIASES'] = {'UNIT_SUB_MAP_URL': 'https://hub.example/sub-map'}

    def test_legacy_hub_net_id_parser_remains_available(self):
        router = self._origin()
        self.assertEqual(router._hub_net_id({'opb_net_id': '2040'}), 2040)
        self.assertIsNone(router._hub_net_id({'miss': True}))
        self.assertIsNone(router._hub_net_id({'opb_net_id': 'bad'}))

    def test_local_hit_without_a_hub_does_not_look_up(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        local = _Target()
        bm.systems['SYSTEM-B'] = local
        router = self._origin()

        def _boom(_radio):
            raise AssertionError('hub lookup without a hub url')

        router._unit_hub_lookup = _boom
        self._send(router)
        self.assertEqual(len(local.sent), 1)

    def test_hub_home_on_this_master_stays_local(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        local = _Target()
        remote = _Target()
        bm.systems['SYSTEM-B'] = local
        bm.systems['OBP-EU'] = remote
        self._home_here()
        router = self._origin()
        seen = []

        def _lookup(radio):
            seen.append(radio)
            return {'opb_net_id': 2342, 'peer_id': ESSID}

        router._unit_hub_lookup = _lookup
        self._send_at(router, STREAM, 100.0)
        end = _voice_packet(CALLER, CALLEE, STREAM, 2, 1, HBPF_SLT_VTERM)
        router._forward_unit_voice(
            bytes_3(CALLEE), 2, end[15], end, end[20:53], STREAM,
            PEER, bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VTERM, 101.0)
        self._send_at(router, STREAM2, 140.0)
        self.assertEqual(seen, [CALLEE])
        self.assertEqual(len(local.sent), 3)
        self.assertEqual(remote.sent, [])

    def test_later_over_reuses_the_home_without_another_lookup(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        local = _Target()
        remote = _Target()
        bm.systems['SYSTEM-B'] = local
        bm.systems['OBP-EU'] = remote
        self._home_here()
        router = self._origin()
        seen = []
        router._unit_hub_lookup = lambda _radio: seen.append(_radio) or {
            'opb_net_id': 2040,
        }
        self._send_at(router, STREAM, 100.0)
        self._send_at(router, STREAM2, 130.0)
        self.assertEqual(seen, [CALLEE])
        self.assertEqual(local.sent, [])
        self.assertEqual(len(remote.sent), 2)

    def test_recent_local_transmission_does_not_ask_the_hub(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        local = _Target()
        remote = _Target()
        bm.systems['SYSTEM-B'] = local
        bm.systems['OBP-EU'] = remote
        bm.SUB_MAP[bytes_3(CALLEE)] = (
            'SYSTEM-B', 2, None, 90.0, (ESSID).to_bytes(4, 'big'))
        self._home_here()
        router = self._origin()

        def _boom(_radio):
            raise AssertionError('hub lookup after a local transmission')

        router._unit_hub_lookup = _boom
        self._send(router)
        self.assertEqual(len(local.sent), 1)
        self.assertEqual(remote.sent, [])

    def test_newer_local_transmission_overrides_a_remote_home(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        local = _Target()
        remote = _Target()
        bm.systems['SYSTEM-B'] = local
        bm.systems['OBP-EU'] = remote
        self._home_here()
        router = self._origin()
        seen = []
        router._unit_hub_lookup = lambda _radio: seen.append(_radio) or {
            'opb_net_id': 2040,
        }
        self._send_at(router, STREAM, 100.0)
        bm.SUB_MAP[bytes_3(CALLEE)] = (
            'SYSTEM-B', 2, None, 150.0, (ESSID).to_bytes(4, 'big'))
        self._send_at(router, STREAM2, 160.0)
        self.assertEqual(seen, [CALLEE])
        self.assertEqual(len(remote.sent), 1)
        self.assertEqual(len(local.sent), 1)

    def test_last_heard_master_beats_a_local_login(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        local = _Target()
        remote = _Target()
        bm.systems['SYSTEM-B'] = local
        bm.systems['OBP-EU'] = remote
        self._home_here()
        router = self._origin()
        router._unit_hub_lookup = lambda _radio: {
            'opb_net_id': 2040,
            'source_host': 'europe.freestar.network',
        }
        self._send(router)
        self.assertEqual(local.sent, [])
        self.assertEqual(len(remote.sent), 1)
        self.assertTrue(remote.sent[0][15] & 0x40)

    def test_hub_miss_keeps_the_local_login(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        local = _Target()
        remote = _Target()
        bm.systems['SYSTEM-B'] = local
        bm.systems['OBP-EU'] = remote
        self._home_here()
        router = self._origin()
        router._unit_hub_lookup = lambda _radio: {'miss': True}
        self._send(router)
        self.assertEqual(len(local.sent), 1)
        self.assertEqual(remote.sent, [])

    def test_busy_local_callee_is_not_sent_to_the_hub(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID), hang=5)
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        local = _Target()
        local.STATUS[2]['TX_TYPE'] = HBPF_SLT_VHEAD
        local.STATUS[2]['TX_TIME'] = 100.0
        remote = _Target()
        bm.systems['SYSTEM-B'] = local
        bm.systems['OBP-EU'] = remote
        bm.CONFIG['GLOBAL'] = {
            'SERVER_ID': (2342).to_bytes(4, 'big'),
            'UNIT_OBP_FLOOD': True,
        }
        router = self._origin()

        def _boom(_radio):
            raise AssertionError('hub lookup on a busy local callee')

        router._unit_hub_lookup = _boom
        self._send(router)
        self.assertEqual(local.sent, [])
        self.assertEqual(remote.sent, [])
        self.assertTrue(router._unit_voice_routes[STREAM].get('local'))

    def test_busy_slot_drops_when_the_hub_says_this_master(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID), hang=5)
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        local = _Target()
        local.STATUS[2]['TX_TYPE'] = HBPF_SLT_VHEAD
        local.STATUS[2]['TX_TIME'] = 100.0
        remote = _Target()
        bm.systems['SYSTEM-B'] = local
        bm.systems['OBP-EU'] = remote
        self._home_here()
        router = self._origin()
        router._unit_hub_lookup = lambda _radio: {'opb_net_id': 2342}
        self._send(router)
        self.assertEqual(local.sent, [])
        self.assertEqual(remote.sent, [])

    def test_busy_local_login_does_not_block_the_heard_master(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID), hang=5)
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        local = _Target()
        local.STATUS[2]['TX_TYPE'] = HBPF_SLT_VHEAD
        local.STATUS[2]['TX_TIME'] = 100.0
        remote = _Target()
        bm.systems['SYSTEM-B'] = local
        bm.systems['OBP-EU'] = remote
        self._home_here()
        router = self._origin()
        router._unit_hub_lookup = lambda _radio: {'opb_net_id': 2040}
        self._send(router)
        self.assertEqual(local.sent, [])
        self.assertEqual(len(remote.sent), 1)

    def test_mocked_hub_selects_destination_master_and_not_xpeer(self):
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        self._obp('OBP-XPEER', 9999, 'xpeer.freestar.network')
        europe = _Target()
        bridge = _Target()
        bm.systems['OBP-EU'] = europe
        bm.systems['OBP-XPEER'] = bridge
        bm.CONFIG['ALIASES'] = {
            'UNIT_SUB_MAP_URL': 'https://hub.example/sub-map',
            'UNIT_SUB_MAP_TOKEN_FILE': 'token',
        }
        bm.CONFIG['GLOBAL'] = {
            'SERVER_ID': (2342).to_bytes(4, 'big'),
            'UNIT_OBP_FLOOD': True,
        }
        router = self._origin()
        seen = []

        def _lookup(radio):
            seen.append(radio)
            return {
                'source_host': 'europe.freestar.network',
                'opb_net_id': 2040,
                'peer_id': ESSID,
                'slot': 2,
            }

        router._unit_hub_lookup = _lookup
        self._send(router)
        later = _voice_packet(CALLER, CALLEE, STREAM, 2, 1, HBPF_VOICE)
        router._forward_unit_voice(
            bytes_3(CALLEE), 2, later[15], later, later[20:53], STREAM,
            PEER, bytes_3(CALLER), HBPF_VOICE, 0, 100.1)
        self.assertEqual(seen, [CALLEE])
        self.assertEqual(len(europe.sent), 2)
        self.assertEqual(bridge.sent, [])
        self.assertTrue(europe.sent[0][15] & 0x40)
        self.assertFalse(europe.sent[0][15] & 0x80)
        self.assertEqual(europe.sent[0][20:53], later[20:53])

    def test_unhealthy_next_hop_retries_with_bounded_buffer(self):
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        self._obp('OBP-GR', 2020, 'gr.freestar.network')
        bm.CONFIG['SYSTEMS']['OBP-EU']['_bcka'] = 0.0
        europe = _Target()
        greece = _Target()
        bm.systems['OBP-EU'] = europe
        bm.systems['OBP-GR'] = greece
        self._home_here()
        router = self._origin()
        answers = iter((
            {'home': 3180, 'current_master': 2342, 'next_hop': 2040,
             'path': [2342, 2040, 3180]},
            {'home': 3180, 'current_master': 2342, 'next_hop': 2020,
             'path': [2342, 2020, 3180]},
        ))
        router._unit_hub_lookup = lambda _radio: next(answers)
        self._send(router)
        self.assertEqual(europe.sent, [])
        self.assertEqual(len(greece.sent), 1)
        self.assertLessEqual(
            len(router._unit_voice_routes[STREAM].get('buffer', [])), 48)

    def test_flood_off_sends_nothing_and_a_404_does_not_flood(self):
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        europe = _Target()
        bm.systems['OBP-EU'] = europe
        bm.CONFIG['GLOBAL'] = {'UNIT_OBP_FLOOD': False}
        router = self._origin()
        self._send(router)
        self.assertEqual(europe.sent, [])

        bm.CONFIG['ALIASES'] = {'UNIT_SUB_MAP_URL': 'https://hub.example/sub-map'}
        bm.CONFIG['GLOBAL'] = {'UNIT_OBP_FLOOD': True}
        router = self._origin()
        router._unit_voice_routes = {}
        router._unit_hub_lookup = lambda _radio: {'miss': True}
        self._send(router)
        self.assertEqual(europe.sent, [])

    def test_legacy_flood_flag_does_not_relay(self):
        self._obp('OBP-EU', 2040, 'europe.freestar.network')
        self._obp('OBP-XPEER', 9999, 'xpeer.freestar.network')
        europe = _Target()
        bridge = _Target()
        bm.systems['OBP-EU'] = europe
        bm.systems['OBP-XPEER'] = bridge
        bm.CONFIG['GLOBAL'] = {'UNIT_OBP_FLOOD': True}
        router = self._origin()
        self._send(router)
        self.assertEqual(europe.sent, [])
        self.assertEqual(bridge.sent, [])

    def test_missing_token_fails_closed(self):
        bm.CONFIG['ALIASES'] = {
            'UNIT_SUB_MAP_URL': 'https://hub.example/sub-map',
            'UNIT_SUB_MAP_TOKEN_FILE': '',
        }
        self.assertEqual(bm.fetch_unit_sub_map(CALLEE), {'error': True})


class TestInboundOpenBridgeUnitVoice(UnitCallFixture):
    def _transit_router(self):
        self._obp_config('OBP-INGRESS', 3180, 100.0)
        router = bm.routerOBP.__new__(bm.routerOBP)
        router._system = 'OBP-INGRESS'
        router._CONFIG = bm.CONFIG
        router.STATUS = {}
        router._unit_voice_routes = {}
        router._unit_hub_inline = True
        router._report = MagicMock()
        return router

    def _obp_config(self, name, net_id, bcka):
        self._system(name, 'OPENBRIDGE')
        bm.CONFIG['SYSTEMS'][name].update({
            'ENHANCED_OBP': True,
            'VER': 5,
            'NETWORK_ID': int(net_id).to_bytes(4, 'big'),
            '_bcka': bcka,
        })

    def test_unit_voice_is_delivered_and_does_not_open_a_stat_bridge(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        bm.CONFIG['GLOBAL'] = {
            'SERVER_ID': (2040).to_bytes(4, 'big'),
            'GEN_STAT_BRIDGES': True,
        }
        bm.CONFIG['ALIASES'] = {
            'UNIT_SUB_MAP_URL': 'https://hub.example/sub-map',
        }
        router = bm.routerOBP.__new__(bm.routerOBP)
        router._system = 'OBP-UK'
        router.STATUS = {}
        router._unit_voice_routes = {}
        router._fresh_unit_home = MagicMock(
            side_effect=AssertionError(
                'inbound OpenBridge voice consulted the global home cache'))
        packet = _voice_packet(CALLER, CALLEE, STREAM, 1, 0, HBPF_SLT_VHEAD)
        router.dmrd_received(
            PEER, bytes_3(CALLER), bytes_3(CALLEE), 0, 1, 'unit',
            HBPF_DATA_SYNC, HBPF_SLT_VHEAD, STREAM, packet, b'',
            b'\x01', (3180).to_bytes(4, 'big'))
        self.assertEqual(len(target.sent), 1)
        self.assertTrue(target.sent[0][15] & 0x40)
        self.assertTrue(target.sent[0][15] & 0x80)
        self.assertEqual(bm.BRIDGES, {})

    def test_first_voice_burst_c_is_voice_and_learns_the_callers_master(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        bm.CONFIG['GLOBAL'] = {
            'SERVER_ID': (2040).to_bytes(4, 'big'),
            'GEN_STAT_BRIDGES': True,
        }
        router = bm.routerOBP.__new__(bm.routerOBP)
        router._system = 'OBP-USA'
        router.STATUS = {}
        router._unit_voice_routes = {}
        packet = _voice_packet(
            CALLER, CALLEE, STREAM, 1, 0, 3, frame_type=HBPF_VOICE)

        router.dmrd_received(
            PEER, bytes_3(CALLER), bytes_3(CALLEE), 0, 1, 'unit',
            HBPF_VOICE, 3, STREAM, packet, b'',
            b'\x01', (3180).to_bytes(4, 'big'))

        self.assertIn(STREAM, router._unit_voice_routes)
        self.assertEqual(len(target.sent), 1)
        self.assertEqual(bm._UNIT_HOMES[CALLER]['net_id'], 3180)
        self.assertFalse(bm._UNIT_HOMES[CALLER]['local'])

    def test_own_server_echo_is_not_delivered(self):
        self._system('SYSTEM-B', 'MASTER', peers=self._peer(ESSID))
        target = _Target()
        bm.systems['SYSTEM-B'] = target
        server = (2040).to_bytes(4, 'big')
        bm.CONFIG['GLOBAL'] = {'SERVER_ID': server, 'GEN_STAT_BRIDGES': True}
        router = bm.routerOBP.__new__(bm.routerOBP)
        router._system = 'OBP-UK'
        router.STATUS = {}
        packet = _voice_packet(CALLER, CALLEE, STREAM, 1, 0, HBPF_SLT_VHEAD)
        router.dmrd_received(
            PEER, bytes_3(CALLER), bytes_3(CALLEE), 0, 1, 'unit',
            HBPF_DATA_SYNC, HBPF_SLT_VHEAD, STREAM, packet, b'',
            b'\x01', server)
        self.assertEqual(target.sent, [])

    def test_transit_uses_one_next_hop_and_preserves_v5_origin(self):
        self._obp_config('OBP-EU', 2040, 9999999999.0)
        target = _Target()
        bm.systems['OBP-EU'] = target
        bm.CONFIG['GLOBAL'] = {'SERVER_ID': (2381).to_bytes(4, 'big')}
        bm.CONFIG['ALIASES'] = {'UNIT_SUB_MAP_URL': 'https://hub.example/sub-map'}
        bm.CONFIG['REPORTS']['REPORT'] = True
        router = self._transit_router()
        router._unit_hub_lookup = lambda _radio: {
            'current_master': 2381,
            'home_net_id': 2020,
            'next_hop_net_id': 2040,
            'path': [2381, 2040, 2020],
        }
        packet = _voice_packet(CALLER, CALLEE, STREAM, 1, 0, HBPF_SLT_VHEAD)
        origin = (3180).to_bytes(4, 'big')
        rptr = (234111101).to_bytes(4, 'big')
        router.dmrd_received(
            PEER, bytes_3(CALLER), bytes_3(CALLEE), 0, 1, 'unit',
            HBPF_DATA_SYNC, HBPF_SLT_VHEAD, STREAM, packet, b'',
            b'\x02', origin, b'\x05', b'\x90', rptr)
        terminal = _voice_packet(
            CALLER, CALLEE, STREAM, 1, 1, HBPF_SLT_VTERM)
        router.dmrd_received(
            PEER, bytes_3(CALLER), bytes_3(CALLEE), 1, 1, 'unit',
            HBPF_DATA_SYNC, HBPF_SLT_VTERM, STREAM, terminal, b'',
            b'\x02', origin, b'\x05', b'\x90', rptr)
        self.assertEqual(len(target.sent), 2)
        self.assertEqual(
            target.extra[0],
            (b'\x02', b'\x05', b'\x90', origin, rptr))
        router._report.send_bridgeEvent.assert_not_called()

    def test_transit_never_returns_to_ingress(self):
        target = _Target()
        bm.systems['OBP-INGRESS'] = target
        bm.CONFIG['GLOBAL'] = {'SERVER_ID': (2381).to_bytes(4, 'big')}
        bm.CONFIG['ALIASES'] = {'UNIT_SUB_MAP_URL': 'https://hub.example/sub-map'}
        router = self._transit_router()
        bm.systems['OBP-INGRESS'] = target
        router._unit_hub_lookup = lambda _radio: {
            'current_master': 2381,
            'home_net_id': 2020,
            'next_hop_net_id': 3180,
            'path': [2381, 3180, 2020],
        }
        packet = _voice_packet(CALLER, CALLEE, STREAM, 1, 0, HBPF_SLT_VHEAD)
        router.dmrd_received(
            PEER, bytes_3(CALLER), bytes_3(CALLEE), 0, 1, 'unit',
            HBPF_DATA_SYNC, HBPF_SLT_VHEAD, STREAM, packet, b'',
            b'\x02', (3180).to_bytes(4, 'big'))
        self.assertEqual(target.sent, [])

    def test_returning_transit_stream_on_another_ingress_is_dropped(self):
        bm.CONFIG['GLOBAL'] = {'SERVER_ID': (2381).to_bytes(4, 'big')}
        bm.CONFIG['ALIASES'] = {
            'UNIT_SUB_MAP_URL': 'https://hub.example/sub-map',
        }
        packet = _voice_packet(
            CALLER, CALLEE, STREAM, 1, 0, HBPF_SLT_VHEAD)
        first = self._transit_router()
        first._unit_hub_lookup = lambda _radio: {'miss': True}
        first._forward_unit_voice(
            bytes_3(CALLEE), 1, packet[15], packet, packet[20:53],
            STREAM, PEER, bytes_3(CALLER), HBPF_DATA_SYNC,
            HBPF_SLT_VHEAD, 100.0, origin_local=False,
            ingress_system='OBP-INGRESS')
        second = self._transit_router()
        second._system = 'OBP-RETURN'
        second._unit_hub_lookup = MagicMock(
            side_effect=AssertionError('loop consulted the hub'))
        second._forward_unit_voice(
            bytes_3(CALLEE), 1, packet[15], packet, packet[20:53],
            STREAM, PEER, bytes_3(CALLER), HBPF_DATA_SYNC,
            HBPF_SLT_VHEAD, 100.1, origin_local=False,
            ingress_system='OBP-RETURN')
        second._unit_hub_lookup.assert_not_called()


class TestOpenBridgeUnitAcl(unittest.TestCase):
    def test_talkgroup_acl_does_not_drop_an_obp_unit_call(self):
        from hashlib import blake2b
        from time import time_ns
        from hblink import OPENBRIDGE

        bridge = OPENBRIDGE.__new__(OPENBRIDGE)
        bridge._system = 'OBP-UK'
        bridge._laststrid = []
        allow = (False, [])
        deny = (True, [])
        bridge._CONFIG = {'GLOBAL': {
            'USE_ACL': True,
            'SUB_ACL': allow,
            'TG1_ACL': deny,
            'VALIDATE_SERVER_IDS': False,
            'SERVER_ID': (2040).to_bytes(4, 'big'),
        }}
        bridge._config = {
            'USE_ACL': True,
            'SUB_ACL': allow,
            'TG1_ACL': deny,
            'NETWORK_ID': (2040).to_bytes(4, 'big'),
            'VER': 5,
            'RELAX_CHECKS': True,
            'TARGET_SOCK': ('10.1.1.1', 62031),
            'TARGET_IP': '',
            'PASSPHRASE': b'secret',
            'MAX_PACKET_AGE': 15,
            'ENHANCED_OBP': True,
        }
        bridge.received = []
        bridge.dmrd_received = lambda *args, **kwargs: bridge.received.append(args)

        def _packet(unit):
            data = bytearray(53)
            data[0:4] = b'DMRE'
            data[4] = 1
            data[5:8] = bytes_3(CALLER)
            data[8:11] = bytes_3(CALLEE if unit else 2350)
            data[11:15] = (2040).to_bytes(4, 'big')
            bits = (HBPF_DATA_SYNC << 4) | HBPF_SLT_VHEAD
            if unit:
                bits |= 0x40
            data[15] = bits
            data[16:20] = STREAM
            packet = bytearray(89)
            packet[:53] = data
            packet[55] = 5
            packet[56:64] = time_ns().to_bytes(8, 'big')
            packet[64:68] = (3180).to_bytes(4, 'big')
            packet[72] = 1
            digest = blake2b(key=b'secret', digest_size=16)
            digest.update(bytes(packet[:73]))
            packet[73:89] = digest.digest()
            return bytes(packet)

        bridge.datagramReceived(_packet(True), ('10.1.1.1', 62031))
        bridge.datagramReceived(_packet(False), ('10.1.1.1', 62031))
        self.assertEqual(len(bridge.received), 1)
        self.assertEqual(bridge.received[0][5], 'unit')
