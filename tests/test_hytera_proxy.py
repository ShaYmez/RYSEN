#!/usr/bin/env python3
import unittest

from twisted.internet.task import Clock

from hytera_const import (
    P2P_COMMAND_REPLY,
    P2P_DMR_STARTUP,
    P2P_REDIRECT_ID,
    P2P_REGISTRATION,
    PRCL,
    PRIN,
)
from hytera_proxy import HyteraProxy


class Transport:
    def __init__(self):
        self.writes = []

    def write(self, data, address):
        self.writes.append((data, address))


def p2p(command):
    packet = bytearray(21)
    packet[:3] = b'P2P'
    packet[3] = 0x38
    packet[4] = 0x20
    packet[20] = command
    return bytes(packet)


class TestHyteraProxy(unittest.TestCase):
    MASTER = '172.16.238.10'
    REPEATER = ('198.51.100.7', 62004)

    def setUp(self):
        self.clock = Clock()
        self.proxy = HyteraProxy(
            master=self.MASTER, p2p_port=50000,
            public_slot_start=50003, backend_slot_start=61000,
            slots=1, timeout=60, clock=self.clock)
        self.p2p = Transport()
        self.dmr = Transport()
        self.rdac = Transport()
        self.proxy.set_transport(50000, self.p2p)
        self.proxy.set_transport(50004, self.dmr)
        self.proxy.set_transport(50005, self.rdac)

    def _register(self):
        self.proxy.datagram_received(
            50000, p2p(P2P_REGISTRATION), self.REPEATER)
        return self.proxy.sessions[50003]

    def test_registration_allocates_triple_and_notifies_backend(self):
        session = self._register()

        self.assertEqual(session.backend_base, 61000)
        self.assertEqual(session.endpoints['p2p'], self.REPEATER)
        self.assertTrue(self.p2p.writes[0][0].startswith(PRIN))
        self.assertEqual(self.p2p.writes[0][1], (self.MASTER, 61000))
        self.assertEqual(self.p2p.writes[1],
                         (p2p(P2P_REGISTRATION), (self.MASTER, 61000)))

    def test_redirect_and_media_follow_one_assigned_triple(self):
        self._register()
        dmr_endpoint = ('198.51.100.7', 62006)
        self.proxy.datagram_received(
            50000, p2p(P2P_DMR_STARTUP), dmr_endpoint)
        redirect = bytearray(p2p(P2P_DMR_STARTUP))
        redirect[3] = P2P_COMMAND_REPLY
        redirect[4] = P2P_REDIRECT_ID
        redirect.extend(b'\xff\x01')
        redirect.extend((61001).to_bytes(2, 'little'))
        self.proxy.datagram_received(50000, bytes(redirect), (self.MASTER, 61000))

        reply, address = self.p2p.writes[-1]
        self.assertEqual(address, self.REPEATER)
        self.assertEqual(int.from_bytes(reply[-2:], 'little'), 50004)

        self.proxy.datagram_received(50004, b'media', dmr_endpoint)
        self.assertEqual(self.dmr.writes[-1], (b'media', (self.MASTER, 61001)))

        self.proxy.datagram_received(50004, b'return', (self.MASTER, 61001))
        self.assertEqual(self.dmr.writes[-1], (b'return', dmr_endpoint))

    def test_rdac_identity_binds_session_then_timeout_releases_it(self):
        session = self._register()
        identity = bytearray(b'\x7e\x04\x00\x00' + b'\x00' * 20)
        identity[18:21] = (235287).to_bytes(3, 'little')
        self.proxy.datagram_received(
            50005, bytes(identity), ('198.51.100.7', 62007))

        self.assertEqual(session.repeater_id, 235287)
        self.assertIs(self.proxy.by_repeater_id[235287], session)
        self.assertTrue(self.p2p.writes[-1][0].startswith(PRIN))

        self.clock.advance(60)
        self.assertIsNone(self.proxy.sessions[50003])
        self.assertEqual(self.p2p.writes[-1], (PRCL, (self.MASTER, 61000)))

    def test_non_registration_cannot_allocate_a_slot(self):
        self.proxy.datagram_received(
            50000, p2p(P2P_DMR_STARTUP), self.REPEATER)
        self.assertIsNone(self.proxy.sessions[50003])
        self.assertEqual(self.p2p.writes, [])

    def test_first_slot_can_share_the_public_p2p_entry_port(self):
        proxy = HyteraProxy(
            master=self.MASTER, p2p_port=50000,
            public_slot_start=50000, backend_slot_start=50003,
            slots=2, timeout=60, clock=self.clock)
        p2p_transport = Transport()
        proxy.set_transport(50000, p2p_transport)

        proxy.datagram_received(50000, p2p(P2P_REGISTRATION), self.REPEATER)

        self.assertIsNotNone(proxy.sessions[50000])
        self.assertEqual(p2p_transport.writes[-1],
                         (p2p(P2P_REGISTRATION), (self.MASTER, 50003)))
        proxy.datagram_received(
            50000, p2p(P2P_REGISTRATION), ('203.0.113.8', 62004))
        self.assertIsNotNone(proxy.sessions[50003])

    def test_blacklisted_rdac_identity_releases_the_slot(self):
        self.proxy.black_list.add(235287)
        self._register()
        identity = bytearray(b'\x7e\x04\x00\x00' + b'\x00' * 20)
        identity[18:21] = (235287).to_bytes(3, 'little')

        self.proxy.datagram_received(
            50005, bytes(identity), ('198.51.100.7', 62007))

        self.assertIsNone(self.proxy.sessions[50003])
        self.assertEqual(self.p2p.writes[-1], (PRCL, (self.MASTER, 61000)))

    def test_reregistration_clears_stale_service_endpoints_and_identity(self):
        session = self._register()
        session.endpoints['dmr'] = ('198.51.100.7', 62006)
        session.endpoints['rdac'] = ('198.51.100.7', 62007)
        session.repeater_id = 235287
        self.proxy.by_repeater_id[235287] = session

        replacement = ('198.51.100.7', 63004)
        self.proxy.datagram_received(
            50000, p2p(P2P_REGISTRATION), replacement)

        self.assertEqual(session.endpoints, {'p2p': replacement})
        self.assertEqual(session.repeater_id, 0)
        self.assertNotIn(235287, self.proxy.by_repeater_id)

    def test_expected_post_redirect_registration_preserves_session(self):
        session = self._register()
        dmr_endpoint = ('198.51.100.7', 62006)
        rdac_endpoint = ('198.51.100.7', 62007)
        session.endpoints['dmr'] = dmr_endpoint
        session.endpoints['rdac'] = rdac_endpoint
        session.repeater_id = 235287
        self.proxy.by_repeater_id[235287] = session

        self.proxy.datagram_received(
            50000, p2p(P2P_DMR_STARTUP), dmr_endpoint)
        self.clock.advance(1)
        self.proxy.datagram_received(
            50000, p2p(P2P_REGISTRATION), self.REPEATER)

        self.assertEqual(session.endpoints['dmr'], dmr_endpoint)
        self.assertEqual(session.endpoints['rdac'], rdac_endpoint)
        self.assertEqual(session.repeater_id, 235287)
        self.assertIs(self.proxy.by_repeater_id[235287], session)

    def test_explicit_public_triples_separate_same_nat_repeaters(self):
        proxy = HyteraProxy(
            master=self.MASTER, p2p_port=50000,
            public_slot_start=50000, backend_slot_start=61000,
            slots=2, timeout=60, clock=self.clock)
        first_transport = Transport()
        second_transport = Transport()
        proxy.set_transport(50000, first_transport)
        proxy.set_transport(50003, second_transport)

        proxy.datagram_received(
            50000, p2p(P2P_REGISTRATION), ('198.51.100.7', 62004))
        proxy.datagram_received(
            50003, p2p(P2P_REGISTRATION), ('198.51.100.7', 63004))

        self.assertEqual(
            proxy.sessions[50000].endpoints['p2p'],
            ('198.51.100.7', 62004))
        self.assertEqual(
            proxy.sessions[50003].endpoints['p2p'],
            ('198.51.100.7', 63004))
        self.assertEqual(
            second_transport.writes[-1],
            (p2p(P2P_REGISTRATION), (self.MASTER, 61003)))


if __name__ == '__main__':
    unittest.main()
