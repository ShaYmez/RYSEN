#!/usr/bin/env python3
import unittest

from hytera_const import (
    P2P_DMR_STARTUP,
    P2P_REGISTRATION,
    TIMESLOT_2,
    media_radio_ids,
    media_timeslot,
    p2p_command_type,
)
from hytera_master import (
    HyteraMasterMixin,
    build_ping_reply,
    build_registration_reply,
    build_service_redirect,
    build_startup_reply,
)
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

    def test_hytera_is_generated_but_not_routed_before_voice_phase(self):
        self.assertTrue(is_repeater_protocol('HYTERA'))
        self.assertTrue(is_generated_master('HYTERA'))
        self.assertFalse(is_routing_master('HYTERA'))
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


if __name__ == '__main__':
    unittest.main()
