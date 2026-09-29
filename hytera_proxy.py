#!/usr/bin/env python3
###############################################################################
#   Hytera IP Multi-site Connect UDP proxy — three-port session multiplexer
###############################################################################

import argparse
import configparser
import ipaddress
import json
import logging
import signal

from dataclasses import dataclass, field
from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol

from hytera_const import (
    P2P_COMMAND_REPLY,
    P2P_DMR_STARTUP,
    P2P_RDAC_STARTUP,
    P2P_REDIRECT_ID,
    P2P_REGISTRATION,
    PORTS_PER_SLOT,
    PRCL,
    PRIN,
    p2p_command_type,
    rdac_repeater_id,
)


def is_ipv4_address(ip):
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ValueError:
        return False


@dataclass
class HyteraProxySession:
    public_base: int
    backend_base: int
    p2p_public_port: int
    endpoints: dict = field(default_factory=dict)
    repeater_id: int = 0
    timer: object = None


class HyteraProxy:
    """Route each RD985's P2P, DMR and RDAC flows as one NAT-aware session."""

    _SERVICE_OFFSETS = {'p2p': 0, 'dmr': 1, 'rdac': 2}

    def __init__(self, master, p2p_port, public_slot_start, backend_slot_start,
                 slots, timeout, debug=False, black_list=None,
                 ip_black_list=None, clock=None):
        self.master = master
        self.p2p_port = p2p_port
        self.public_slot_start = public_slot_start
        self.backend_slot_start = backend_slot_start
        self.timeout = timeout
        self.debug = debug
        self.black_list = set(black_list or [])
        self.ip_black_list = dict(ip_black_list or {})
        self.clock = clock or reactor
        self.transports = {}
        self.sessions = {}
        self.by_public_port = {}
        self.by_backend_port = {}
        self.by_repeater_id = {}
        for index in range(slots):
            public_base = public_slot_start + index * PORTS_PER_SLOT
            backend_base = backend_slot_start + index * PORTS_PER_SLOT
            self.sessions[public_base] = None
            for offset in range(PORTS_PER_SLOT):
                self.by_public_port[public_base + offset] = public_base
                self.by_backend_port[backend_base + offset] = public_base

    def set_transport(self, port, transport):
        self.transports[port] = transport

    def _write(self, source_port, data, address):
        transport = self.transports.get(source_port)
        if transport is not None:
            transport.write(data, address)
            return True
        return False

    def _session_for_host(self, host):
        matches = [
            session for session in self.sessions.values()
            if session is not None and session.endpoints.get('p2p', (None,))[0] == host
        ]
        return matches[0] if len(matches) == 1 else None

    def _notify(self, session, closing=False):
        payload = PRCL if closing else (
            PRIN + json.dumps({
                'p2p': session.endpoints.get('p2p'),
                'dmr': session.endpoints.get('dmr'),
                'rdac': session.endpoints.get('rdac'),
                'repeater_id': session.repeater_id,
            }, separators=(',', ':')).encode('utf-8'))
        self._write(
            session.p2p_public_port, payload,
            (self.master, session.backend_base))

    def _release(self, public_base):
        session = self.sessions.get(public_base)
        if session is None:
            return
        if session.timer is not None and session.timer.active():
            session.timer.cancel()
        self._notify(session, closing=True)
        if session.repeater_id:
            self.by_repeater_id.pop(session.repeater_id, None)
        self.sessions[public_base] = None

    def _expire(self, public_base):
        session = self.sessions.get(public_base)
        if session is not None:
            self._release(public_base)

    def _touch(self, session):
        if session.timer is None:
            session.timer = self.clock.callLater(
                self.timeout, self._expire, session.public_base)
        else:
            session.timer.reset(self.timeout)

    def _allocate(self, endpoint, preferred_base=None, p2p_public_port=None):
        available = [
            base for base, session in self.sessions.items()
            if session is None
            and (preferred_base is None or base == preferred_base)]
        if not available:
            return None
        # Keep the first repeater on the familiar 50000/50001/50002 CPS
        # ports; subsequent repeaters take the next consecutive free triple.
        public_base = min(available)
        backend_base = self.backend_slot_start + (
            public_base - self.public_slot_start)
        session = HyteraProxySession(
            public_base=public_base, backend_base=backend_base,
            p2p_public_port=p2p_public_port or self.p2p_port,
            endpoints={'p2p': endpoint})
        self.sessions[public_base] = session
        self._touch(session)
        return session

    def _bind_repeater_id(self, session, data):
        repeater_id = rdac_repeater_id(data)
        if not repeater_id:
            return True
        if repeater_id in self.black_list:
            logging.warning('Rejected blacklisted Hytera repeater %s', repeater_id)
            self._release(session.public_base)
            return False
        owner = self.by_repeater_id.get(repeater_id)
        if owner is not None and owner is not session:
            logging.warning('Rejected duplicate Hytera repeater %s', repeater_id)
            self._release(session.public_base)
            return False
        if session.repeater_id and session.repeater_id != repeater_id:
            self.by_repeater_id.pop(session.repeater_id, None)
        session.repeater_id = repeater_id
        self.by_repeater_id[repeater_id] = session
        self._notify(session)
        return True

    def _rewrite_redirect(self, session, data):
        if (len(data) < 5 or data[3:4] != bytes((P2P_COMMAND_REPLY,))
                or data[4:5] != bytes((P2P_REDIRECT_ID,))):
            return data
        backend_port = int.from_bytes(data[-2:], 'little')
        public_base = self.by_backend_port.get(backend_port)
        if public_base is None:
            return data
        offset = backend_port - session.backend_base
        if offset not in (1, 2):
            return data
        reply = bytearray(data)
        reply[-2:] = (session.public_base + offset).to_bytes(2, 'little')
        return bytes(reply)

    def _from_client_p2p(self, local_port, data, address):
        host, _port = address
        if host in self.ip_black_list:
            return
        command = p2p_command_type(data)
        explicit_base = self.by_public_port.get(local_port)
        if (explicit_base is not None
                and local_port - explicit_base == 0
                and local_port != self.p2p_port):
            session = self.sessions.get(explicit_base)
            if (session is not None
                    and session.endpoints.get('p2p', (None,))[0] != host):
                return
        else:
            explicit_base = None
            session = self._session_for_host(host)
        if session is None:
            if command != P2P_REGISTRATION:
                return
            session = self._allocate(
                address, preferred_base=explicit_base,
                p2p_public_port=local_port)
            if session is None:
                return
            self._notify(session)
        else:
            # The RD985 sends DMR/RDAC startup commands *to* P2P from its
            # service source ports. They must not replace the registered P2P
            # endpoint: redirect replies still belong on the P2P socket.
            if command not in (P2P_DMR_STARTUP, P2P_RDAC_STARTUP):
                if command == P2P_REGISTRATION:
                    if session.repeater_id:
                        self.by_repeater_id.pop(session.repeater_id, None)
                    session.repeater_id = 0
                    session.endpoints.pop('dmr', None)
                    session.endpoints.pop('rdac', None)
                session.endpoints['p2p'] = address
            self._touch(session)
            if command == P2P_REGISTRATION:
                self._notify(session)
        self._write(
            session.p2p_public_port, data,
            (self.master, session.backend_base))

    def _from_client_service(self, local_port, data, address):
        public_base = self.by_public_port.get(local_port)
        session = self.sessions.get(public_base)
        if (session is None or address[0] in self.ip_black_list
                or address[0] != session.endpoints['p2p'][0]):
            return
        offset = local_port - session.public_base
        service = 'dmr' if offset == 1 else 'rdac'
        session.endpoints[service] = address
        self._touch(session)
        if service == 'rdac':
            if not self._bind_repeater_id(session, data):
                return
        self._write(local_port, data, (self.master, session.backend_base + offset))

    def _from_master_p2p(self, data, address):
        public_base = self.by_backend_port.get(address[1])
        session = self.sessions.get(public_base)
        if session is None or 'p2p' not in session.endpoints:
            return
        self._touch(session)
        self._write(
            session.p2p_public_port, self._rewrite_redirect(session, data),
            session.endpoints['p2p'])

    def _from_master_service(self, local_port, data):
        public_base = self.by_public_port.get(local_port)
        session = self.sessions.get(public_base)
        if session is None:
            return
        offset = local_port - session.public_base
        service = 'dmr' if offset == 1 else 'rdac'
        endpoint = session.endpoints.get(service)
        if endpoint is None:
            return
        self._touch(session)
        self._write(local_port, data, endpoint)

    def datagram_received(self, local_port, data, address):
        """Route a datagram received by one of the proxy's UDP listeners."""
        if address[0] == self.master:
            public_base = self.by_public_port.get(local_port)
            if (local_port == self.p2p_port
                    or (public_base is not None
                        and local_port - public_base == 0)):
                self._from_master_p2p(data, address)
            else:
                self._from_master_service(local_port, data)
        elif (local_port == self.p2p_port
              or (local_port in self.by_public_port
                  and local_port - self.by_public_port[local_port] == 0)):
            self._from_client_p2p(local_port, data, address)
        elif local_port in self.by_public_port:
            self._from_client_service(local_port, data, address)


class _ProxyPort(DatagramProtocol):
    def __init__(self, proxy, port):
        self.proxy = proxy
        self.port = port

    def startProtocol(self):
        self.proxy.set_transport(self.port, self.transport)

    def datagramReceived(self, data, addr):
        self.proxy.datagram_received(self.port, data, addr)


def _load_config(config_file):
    config = configparser.ConfigParser()
    if not config.read(config_file):
        raise SystemExit(f"Configuration file '{config_file}' is not valid!")
    section = 'HYTERA_PROXY'
    try:
        return {
            'master': config.get(section, 'MASTER'),
            'listen_ip': config.get(section, 'LISTENIP'),
            'p2p_port': config.getint(section, 'P2P_PORT'),
            'public_slot_start': config.getint(section, 'PUBLIC_SLOT_START'),
            'backend_slot_start': config.getint(section, 'BACKEND_SLOT_START'),
            'slots': config.getint(section, 'SLOTS'),
            'timeout': config.getint(section, 'TIMEOUT'),
            'debug': config.getboolean(section, 'DEBUG'),
            'black_list': json.loads(config.get(section, 'BLACKLIST')),
            'ip_black_list': json.loads(config.get(section, 'IPBLACKLIST')),
        }
    except (configparser.Error, json.JSONDecodeError, ValueError) as err:
        raise SystemExit(f'Error processing configuration file -- {err}') from err


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Hytera three-port UDP proxy')
    parser.add_argument('-c', '--config', required=True, dest='config_file')
    args = parser.parse_args()
    cfg = _load_config(args.config_file)
    master = cfg['master']
    if cfg['listen_ip'] == '::' and is_ipv4_address(master):
        master = '::ffff:' + master
    proxy = HyteraProxy(master=master, **{
        key: value for key, value in cfg.items() if key not in ('master', 'listen_ip')
    })
    ports = {cfg['p2p_port']}
    ports.update(range(
        cfg['public_slot_start'],
        cfg['public_slot_start'] + cfg['slots'] * PORTS_PER_SLOT))
    for port in sorted(ports):
        reactor.listenUDP(port, _ProxyPort(proxy, port), interface=cfg['listen_ip'])
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_args: reactor.stop())
    reactor.run()
