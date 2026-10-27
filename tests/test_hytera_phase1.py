#!/usr/bin/env python3
import unittest
from unittest.mock import MagicMock

from twisted.internet.defer import succeed

from hytera_const import (
    P2P_DMR_STARTUP,
    P2P_REGISTRATION,
    TIMESLOT_2,
    media_radio_ids,
    media_timeslot,
    p2p_command_type,
    unwrap_hmtp_media,
    wrap_hmtp_media,
)
from hytera_master import (
    HyteraMasterMixin,
    _HyteraServiceProtocol,
    build_ping_reply,
    build_registration_reply,
    build_service_redirect,
    build_startup_reply,
    is_expected_service_followup_registration,
)
from hytera_voice import HyteraOutboundPacer, HyteraVoiceTranslator
from repeater_modes import (
    is_generated_master,
    is_repeater_protocol,
    is_routing_master,
)


def p2p_packet(command):
    packet = bytearray(21)
    packet[:3] = b'P2P'
    packet[3] = 0x38
    packet[4] = 0x20
    packet[20] = command
    return bytes(packet)


class TestHyteraModeGating(unittest.TestCase):

    def test_hytera_is_generated_and_routed_after_inbound_voice_phase(self):
        self.assertTrue(is_repeater_protocol('HYTERA'))
        self.assertTrue(is_generated_master('HYTERA'))
        self.assertTrue(is_routing_master('HYTERA'))
        self.assertTrue(is_routing_master('IPSC'))


class TestHyteraP2P(unittest.TestCase):

    def test_command_type(self):
        self.assertEqual(p2p_command_type(p2p_packet(P2P_REGISTRATION)),
                         P2P_REGISTRATION)
        self.assertIsNone(p2p_command_type(b'\x00'))

    def test_registration_accept(self):
        reply = build_registration_reply(p2p_packet(P2P_REGISTRATION))
        self.assertEqual(len(reply), 22)
        self.assertEqual(reply[3], 0x38)
        self.assertEqual(reply[4], 0x21)
        self.assertEqual(reply[13:16], b'\x01\x01\x5a')
        self.assertEqual(reply[-1], 0x01)

    def test_dmr_startup_and_redirect(self):
        request = p2p_packet(P2P_DMR_STARTUP)
        accept = build_startup_reply(request)
        redirect = build_service_redirect(accept, 50001)
        self.assertEqual(accept[4], 0x21)
        self.assertEqual(accept[13:16], b'\x01\x01\x5a')
        self.assertEqual(redirect[3], 0x50)
        self.assertEqual(redirect[4], 0x0b)
        self.assertEqual(redirect[12:16], b'\x42\xff\x01\x00')
        self.assertEqual(redirect[-4:-2], b'\xff\x01')
        self.assertEqual(int.from_bytes(redirect[-2:], 'little'), 50001)

    def test_a90203_registration_matches_ipsc2_capture(self):
        request = bytes.fromhex(
            '50325038010000001400000001ff11420000000010000000')
        expected = bytes.fromhex(
            '5032503802000000140000000101015a000000001000000001')
        self.assertEqual(build_registration_reply(request), expected)

    def test_a90203_dmr_redirect_matches_ipsc2_capture(self):
        request = bytes.fromhex(
            '503250380100000014000000020111020000000011000000')
        accept = build_startup_reply(request)
        expected = bytes.fromhex(
            '503250500b0000001400000042ff01000000000011000000ff0136f2')
        self.assertEqual(build_service_redirect(accept, 62006), expected)

    def test_ping_accept(self):
        ping = bytearray(b'ZZZZ\x0a\x00\x00\x00\x14' + b'\x00' * 11)
        reply = build_ping_reply(bytes(ping))
        self.assertEqual(reply[12], 0xff)
        self.assertEqual(reply[14], 0x01)

    def test_expected_second_registration_preserves_negotiated_session(self):
        self.assertTrue(is_expected_service_followup_registration(
            True, ('203.0.113.9', 50000), '203.0.113.9', 100.0, 105.0))
        self.assertFalse(is_expected_service_followup_registration(
            True, ('203.0.113.9', 50000), '203.0.113.9', None, 105.0))
        self.assertFalse(is_expected_service_followup_registration(
            True, ('203.0.113.9', 50000), '203.0.113.9', 100.0, 111.0))

    def test_service_negotiation_replies_to_registered_p2p_port(self):
        class Transport:
            def __init__(self):
                self.writes = []

            def write(self, packet, addr):
                self.writes.append((packet, addr))

        master = object.__new__(HyteraMasterMixin)
        master._system = 'HYTERA'
        master._hytera_registered = True
        master._hytera_addr = ('203.0.113.9', 50000)
        master._hytera_last_seen = 0
        master._hytera_peer_id = b'\x00\x00\x00\x00'
        master._peers = {}
        master._config = {'DMR_PORT': 50001, 'RDAC_PORT': 50002}
        master._hytera_trace = False
        master.transport = Transport()

        master.hytera_p2p_received(
            p2p_packet(P2P_DMR_STARTUP), ('203.0.113.9', 50001))

        self.assertEqual(len(master.transport.writes), 2)
        self.assertEqual(
            [addr for _, addr in master.transport.writes],
            [('203.0.113.9', 50000), ('203.0.113.9', 50000)])
        self.assertEqual(master._hytera_addr, ('203.0.113.9', 50000))

    def test_registered_ping_refreshes_hytera_selfcare(self):
        class Transport:
            def write(self, packet, addr):
                return None

        db = MagicMock()
        db.touch_hytera_seen.return_value = succeed(None)
        master = object.__new__(HyteraMasterMixin)
        master._system = 'HYTERA'
        master._CONFIG = {
            'SELF SERVICE': {'ENABLED': True},
            '_SELF_SERVICE_DB': db,
        }
        master._hytera_registered = True
        master._hytera_addr = ('203.0.113.9', 50000)
        master._hytera_last_seen = 0
        master._hytera_peer_id = (235287).to_bytes(4, 'big')
        master._peers = {master._hytera_peer_id: {}}
        master._hytera_trace = False
        master.transport = Transport()

        master.hytera_p2p_received(
            b'ZZZZ\x0a\x00\x00\x00\x14' + b'\x00' * 11,
            ('203.0.113.9', 50000))

        db.touch_hytera_seen.assert_called_once_with(235287, '203.0.113.9')

    def test_reregistration_resets_stale_service_and_voice_state(self):
        master = object.__new__(HyteraMasterMixin)
        master._system = 'HYTERA'
        master._config = {
            'HYTERA_REPEATER_ID': 0,
            'PROXY_CONTROL': False,
        }
        master._hytera_proxy_enabled = False
        master._hytera_registered = True
        master._hytera_addr = ('203.0.113.9', 50000)
        master._hytera_last_seen = 1
        master._hytera_peer_id = (235287).to_bytes(4, 'big')
        master._hytera_voice = HyteraVoiceTranslator(peer_id=235287)
        master._hytera_outbound = HyteraOutboundPacer(lambda _packet: None)
        master._hytera_dmr_addr = ('203.0.113.9', 50001)
        master._hytera_hmtp_addr = ('203.0.113.9', 51000)
        master._hytera_rdac_addr = ('203.0.113.9', 50002)
        master._hytera_rdac_step = 9
        master._hytera_rdac_meta = {'firmware': 'stale'}
        master._peers = {master._hytera_peer_id: {}}
        master._report = None

        master._register_hytera_peer(
            '203.0.113.9', 51000, reset_session=True)

        self.assertIsNone(master._hytera_dmr_addr)
        self.assertIsNone(master._hytera_hmtp_addr)
        self.assertIsNone(master._hytera_rdac_addr)
        self.assertEqual(master._hytera_rdac_step, 0)
        self.assertEqual(master._hytera_rdac_meta, {})
        self.assertEqual(master._hytera_peer_id, b'\x00\x00\x00\x00')
        self.assertEqual(master._hytera_addr, ('203.0.113.9', 51000))


class TestHyteraServices(unittest.TestCase):

    def test_one_byte_keepalive_is_acknowledged_on_service_socket(self):
        class Owner:
            def __init__(self):
                self.received = []

            def hytera_dmr_received(self, data, addr):
                self.received.append((data, addr))

        class Transport:
            def __init__(self):
                self.writes = []

            def write(self, data, addr):
                self.writes.append((data, addr))

        owner = Owner()
        protocol = _HyteraServiceProtocol(owner, 'dmr')
        protocol.transport = Transport()
        addr = ('203.0.113.9', 50001)

        protocol.datagramReceived(b'\x00', addr)

        self.assertEqual(protocol.transport.writes, [(b'\x41', addr)])
        self.assertEqual(owner.received, [(b'\x00', addr)])

    def test_proxy_mode_keepalive_is_also_acknowledged(self):
        class Owner:
            _hytera_proxy_enabled = True

            def hytera_dmr_received(self, _data, _addr):
                pass

        class Transport:
            def __init__(self):
                self.writes = []

            def write(self, data, addr):
                self.writes.append((data, addr))

        protocol = _HyteraServiceProtocol(Owner(), 'dmr')
        protocol.transport = Transport()
        addr = ('172.16.238.31', 61001)

        protocol.datagramReceived(b'\x00', addr)

        self.assertEqual(protocol.transport.writes, [(b'\x41', addr)])


class TestHyteraMediaLayout(unittest.TestCase):

    def test_timeslot_and_ids(self):
        packet = bytearray(72)
        packet[16:18] = TIMESLOT_2
        packet[63:67] = (2350 << 8).to_bytes(4, 'little')
        packet[67:71] = (2345875 << 8).to_bytes(4, 'little')
        self.assertEqual(media_timeslot(packet), 2)
        self.assertEqual(media_radio_ids(packet), (2345875, 2350))

    def test_short_media_packet_is_rejected(self):
        self.assertIsNone(media_timeslot(b'\x00' * 20))
        self.assertIsNone(media_radio_ids(b'\x00' * 20))

    def test_hr1065_hmtp_envelopes_carry_the_standard_media_body(self):
        plain = bytes.fromhex(
            '484d5450bebebebe52000000a100050101000000111111115555000040985304'
            '3e000802e813e00c01d45d9457ff5dd7d8f5305560063042a01e80140607000'
            'cb0f301000604000015000000')
        routed = bytes.fromhex(
            '484d545050325031a16151d850c3bebebebef2020000a1000501020000002222'
            'aaaa555500004098c81cf871417d29203afd71bca52d30607102a5b633707d41'
            'bf5a0307dc5d582c00d7a0f40100100400001e000000')

        plain_body = unwrap_hmtp_media(plain)
        routed_body = unwrap_hmtp_media(routed)
        self.assertEqual(len(plain_body), 72)
        self.assertEqual(media_timeslot(plain_body), 1)
        self.assertEqual(media_radio_ids(plain_body), (21, 1030))
        self.assertEqual(len(routed_body), 72)
        self.assertEqual(media_timeslot(routed_body), 2)
        self.assertEqual(media_radio_ids(routed_body), (30, 1040))
        self.assertEqual(wrap_hmtp_media(routed_body), b'HMTP' + routed_body)
        self.assertIsNone(unwrap_hmtp_media(b'HMTP' + b'\x00' * 71))
        self.assertIsNone(wrap_hmtp_media(b'\x00' * 71))


if __name__ == '__main__':
    unittest.main()
