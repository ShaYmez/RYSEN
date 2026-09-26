#!/usr/bin/env python3
###############################################################################
#   Hytera IP Multi-site Connect master (registration-only first milestone)
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

from time import time

from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol

from dmr_utils3.utils import int_id

from hblink import HBSYSTEM, acl_check, build_peer_record, logger
from hytera_const import (
    P2P_COMMAND_REPLY,
    P2P_DMR_STARTUP,
    P2P_RDAC_STARTUP,
    P2P_REDIRECT_ID,
    P2P_REDIRECT_SUFFIX,
    P2P_REGISTRATION,
    is_p2p_ack,
    is_p2p_ping,
    media_radio_ids,
    media_timeslot,
    p2p_command_type,
)


def build_registration_reply(data):
    """Build the master's acceptance response from a slave registration."""
    if len(data) < 21:
        return None
    reply = bytearray(data)
    reply[4] = (reply[4] + 1) & 0xff
    reply[13:16] = b'\x01\x01\x5a'
    reply.append(0x01)
    return bytes(reply)


def build_startup_reply(data):
    """Acknowledge a DMR/RDAC service startup request."""
    if len(data) < 21:
        return None
    reply = bytearray(data)
    reply[4] = (reply[4] + 1) & 0xff
    reply[13:16] = b'\x01\x01\x5a'
    reply.append(0x01)
    return bytes(reply)


def build_service_redirect(data, port):
    """Tell the slave which UDP port hosts the requested service."""
    if len(data) < 17:
        return None
    redirect = bytearray(data[:-1])
    redirect[3] = P2P_COMMAND_REPLY
    redirect[4] = P2P_REDIRECT_ID
    redirect[12:16] = b'\x42\xff\x01\x00'
    redirect.extend(P2P_REDIRECT_SUFFIX)
    redirect.extend(int(port).to_bytes(2, 'little'))
    return bytes(redirect)


def build_ping_reply(data):
    """Acknowledge a P2P keepalive without changing its sequence."""
    if len(data) < 15:
        return None
    reply = bytearray(data)
    reply[12] = 0xff
    reply[14] = 0x01
    return bytes(reply)


class _HyteraServiceProtocol(DatagramProtocol):
    """Forward an auxiliary DMR/RDAC socket into its owning master."""

    def __init__(self, owner, service):
        self.owner = owner
        self.service = service

    def datagramReceived(self, data, addr):
        if self.service == 'dmr':
            self.owner.hytera_dmr_received(data, addr)
        else:
            self.owner.hytera_rdac_received(data, addr)


class HyteraMasterMixin:
    """P2P lifecycle for a Hytera IP Multi-site Connect master."""

    def init_hytera(self):
        self._hytera_registered = False
        self._hytera_addr = None
        self._hytera_last_seen = 0
        self._hytera_listeners = []
        self._hytera_peer_id = int(
            self._config.get('HYTERA_REPEATER_ID', 0)).to_bytes(4, 'big')
        self._hytera_watchdog = self._config.get('KEEPALIVE_WATCHDOG', 60)
        self._hytera_trace = self._config.get('TRACE_PACKETS', False)
        self.datagramReceived = self.hytera_p2p_received
        self.maintenance_loop = self.hytera_maintenance_loop
        self.send_system = self.hytera_send_system
        self.dereg = self.hytera_dereg

    def startProtocol(self):
        HBSYSTEM.startProtocol(self)
        interface = self._config.get('IP', '')
        for port, service in (
                (self._config['DMR_PORT'], 'dmr'),
                (self._config['RDAC_PORT'], 'rdac')):
            listener = reactor.listenUDP(
                port, _HyteraServiceProtocol(self, service),
                interface=interface)
            self._hytera_listeners.append(listener)
        logger.info(
            '(%s) Hytera IP Multi-site master listening P2P=%s DMR=%s RDAC=%s',
            self._system, self._config['PORT'], self._config['DMR_PORT'],
            self._config['RDAC_PORT'])

    def hytera_maintenance_loop(self):
        if (self._hytera_registered and self._hytera_watchdog
                and time() - self._hytera_last_seen > self._hytera_watchdog):
            logger.info('(%s) Hytera slave %s timed out',
                        self._system, self._hytera_addr)
            self._clear_hytera_peer()

    def _registration_allowed(self, host):
        peer_id_int = int_id(self._hytera_peer_id)
        allowed_ips = self._config.get('ALLOWED_PEER_IPS') or []
        if allowed_ips and host not in allowed_ips:
            logger.warning('(%s) Hytera registration denied — IP %s not allowed',
                           self._system, host)
            return False
        if peer_id_int:
            allowed_ids = self._config.get('ALLOWED_PEER_IDS') or []
            if allowed_ids and peer_id_int not in allowed_ids:
                logger.warning(
                    '(%s) Hytera registration denied — repeater ID %s not allowed',
                    self._system, peer_id_int)
                return False
            global_acl = self._CONFIG.get('GLOBAL', {}).get('REG_ACL')
            if global_acl and not acl_check(self._hytera_peer_id, global_acl):
                return False
            if (self._config.get('USE_ACL')
                    and not acl_check(self._hytera_peer_id,
                                      self._config['REG_ACL'])):
                return False
        return True

    def _register_hytera_peer(self, host, port):
        now = time()
        is_new = not self._hytera_registered
        self._hytera_registered = True
        self._hytera_addr = (host, port)
        self._hytera_last_seen = now

        if int_id(self._hytera_peer_id):
            existing = self._peers.get(self._hytera_peer_id)
            record = build_peer_record(
                self._hytera_peer_id, host, port,
                protocol='HYTERA',
                connection='YES',
                existing=existing,
                now=now,
                full_config=self._CONFIG,
            )
            record['PACKAGE_ID'] = 'Hytera IP Multi-site Connect'
            record['SOFTWARE_ID'] = 'Firmware pending RDAC/SNMP discovery'
            self._peers[self._hytera_peer_id] = record
            if self._report is not None:
                self._report.send_config()

        logger.info('(%s) Hytera slave %s from %s:%s',
                    self._system,
                    'registered' if is_new else 're-registered',
                    host, port)

    def _touch_hytera_peer(self, addr, update_p2p_addr=False):
        self._hytera_last_seen = time()
        if update_p2p_addr:
            self._hytera_addr = addr
        if self._hytera_peer_id in self._peers:
            peer = self._peers[self._hytera_peer_id]
            peer['LAST_PING'] = self._hytera_last_seen
            peer['PINGS_RECEIVED'] = peer.get('PINGS_RECEIVED', 0) + 1

    def _clear_hytera_peer(self):
        self._hytera_registered = False
        self._hytera_addr = None
        self._hytera_last_seen = 0
        if self._hytera_peer_id in self._peers:
            del self._peers[self._hytera_peer_id]
            if self._report is not None:
                self._report.send_config()

    def _send_p2p(self, packet, addr):
        if packet is not None:
            self.transport.write(packet, addr)

    def hytera_p2p_received(self, data, addr):
        host, port = addr
        if self._hytera_trace:
            logger.debug('(%s) Hytera P2P RX %s:%s %s',
                         self._system, host, port, data.hex())

        command = p2p_command_type(data)
        if command == P2P_REGISTRATION:
            if not self._registration_allowed(host):
                return
            self._register_hytera_peer(host, port)
            self._send_p2p(build_registration_reply(data), addr)
            return

        if command in (P2P_DMR_STARTUP, P2P_RDAC_STARTUP):
            if not self._hytera_registered:
                self._send_p2p(b'\x00', addr)
                return
            response_addr = self._hytera_addr
            self._touch_hytera_peer(addr)
            self._send_p2p(build_startup_reply(data), response_addr)
            service_port = (
                self._config['DMR_PORT']
                if command == P2P_DMR_STARTUP
                else self._config['RDAC_PORT'])
            self._send_p2p(
                build_service_redirect(
                    build_startup_reply(data), service_port),
                response_addr)
            logger.info('(%s) Hytera %s service redirected to UDP %s',
                        self._system,
                        'DMR' if command == P2P_DMR_STARTUP else 'RDAC',
                        service_port)
            return

        if is_p2p_ping(data):
            if self._hytera_registered:
                self._touch_hytera_peer(addr, update_p2p_addr=True)
                self._send_p2p(build_ping_reply(data), addr)
            else:
                self._send_p2p(b'\x00', addr)
            return

        if is_p2p_ack(data):
            self._touch_hytera_peer(addr)
            return

        logger.debug('(%s) Unknown Hytera P2P packet from %s:%s len=%s data=%s',
                     self._system, host, port, len(data), data.hex())

    def hytera_dmr_received(self, data, addr):
        if self._hytera_registered:
            self._touch_hytera_peer(addr)
        ids = media_radio_ids(data)
        logger.debug(
            '(%s) Hytera DMR packet from %s:%s len=%s slot=%s ids=%s%s',
            self._system, addr[0], addr[1], len(data),
            media_timeslot(data), ids,
            ' data=' + data.hex() if self._hytera_trace else '')

    def hytera_rdac_received(self, data, addr):
        if self._hytera_registered:
            self._touch_hytera_peer(addr)
        logger.debug(
            '(%s) Hytera RDAC packet from %s:%s len=%s%s',
            self._system, addr[0], addr[1], len(data),
            ' data=' + data.hex() if self._hytera_trace else '')

    def hytera_send_system(self, packet, *args, **kwargs):
        """Registration milestone intentionally does not transmit DMR media."""
        logger.trace('(%s) Hytera media TX disabled until capture validation',
                     self._system)
        return False

    def hytera_dereg(self):
        self._clear_hytera_peer()
