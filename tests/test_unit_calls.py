#!/usr/bin/env python3
"""Local subscriber private calls: reflector isolation, delivery, and ACLs."""
import unittest
from unittest.mock import MagicMock

import bridge_master as bm
from const import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM
from dmr_utils3.utils import bytes_3
from hblink import HBSYSTEM


CALLER = 2341111
CALLEE = 2345875
ESSID = 234587501
STREAM = b'\x00\x00\x00\x11'
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


def _voice_packet(src, dst, stream, slot, seq, dtype, payload=None):
    bits = 0x40 | (HBPF_DATA_SYNC << 4) | (dtype & 0x0F)
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
        self.STATUS = {1: _idle_slot(), 2: _idle_slot()}

    def send_system(self, packet, *args, **kwargs):
        self.sent.append(packet)


class UnitCallFixture(unittest.TestCase):
    def setUp(self):
        self._systems = dict(bm.systems)
        bm.systems.clear()
        self._config = getattr(bm, 'CONFIG', None)
        self._sub_map = getattr(bm, 'SUB_MAP', None)
        self._bridges = getattr(bm, 'BRIDGES', None)
        bm.SUB_MAP = {}
        bm.BRIDGES = {}
        bm.CONFIG = {
            'REPORTS': {'REPORT': False},
            'ALLSTAR': {'ENABLED': False},
            'SYSTEMS': {},
        }

    def tearDown(self):
        bm.systems.clear()
        bm.systems.update(self._systems)
        if self._config is None:
            if hasattr(bm, 'CONFIG'):
                delattr(bm, 'CONFIG')
        else:
            bm.CONFIG = self._config
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
        packet = _voice_packet(CALLER, CALLEE, STREAM, 2, 0, HBPF_SLT_VHEAD)
        router._forward_unit_voice(
            bytes_3(CALLEE), 2, packet[15], packet, packet[20:53], STREAM,
            PEER, bytes_3(CALLER), HBPF_DATA_SYNC, HBPF_SLT_VHEAD, 100.0)
        self.assertFalse(router._unit_voice_routes[STREAM]['send'])

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
