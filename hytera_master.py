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

import json
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
    PRCL,
    PRIN,
    RDAC_STEP0_REQUEST,
    RDAC_STEP0_RESPONSE,
    RDAC_STEP1_REQUEST,
    RDAC_STEP1_RESPONSE,
    RDAC_STEP3_REQUEST,
    RDAC_STEP4_REQUEST_1,
    RDAC_STEP4_REQUEST_2,
    RDAC_STEP6_REQUEST_1,
    RDAC_STEP6_REQUEST_2,
    RDAC_STEP7_REQUEST,
    RDAC_STEP10_REQUEST,
    RDAC_STEP12_REQUEST_1,
    RDAC_STEP12_REQUEST_2,
    RDAC_STEP12_RESPONSE,
    rdac_repeater_id,
    is_p2p_ack,
    is_p2p_ping,
    media_radio_ids,
    media_timeslot,
    p2p_command_type,
)
from hytera_rdac_meta import parse_rdac_channel, parse_rdac_identity
from hytera_voice import HyteraOutboundPacer, HyteraVoiceTranslator
from selfcare_db import build_ipsc_seed_options


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
        if data == b'\x00':
            self.transport.write(b'\x41', addr)
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
        self._hytera_services = {}
        self._hytera_dmr_addr = None
        self._hytera_rdac_addr = None
        self._hytera_peer_id = int(
            self._config.get('HYTERA_REPEATER_ID', 0)).to_bytes(4, 'big')
        self._hytera_watchdog = self._config.get('KEEPALIVE_WATCHDOG', 60)
        self._hytera_trace = self._config.get('TRACE_PACKETS', False)
        self._hytera_proxy_enabled = self._config.get('PROXY_CONTROL', False)
        self._hytera_proxy_control_ip = self._config.get(
            'PROXY_CONTROL_IP', '')
        self._hytera_rdac_enabled = (
            self._hytera_proxy_enabled
            or self._config.get('RDAC_DISCOVERY', False))
        self._hytera_proxy_info = None
        self._hytera_rdac_step = 0
        self._hytera_rdac_meta = {}
        self._hytera_voice = HyteraVoiceTranslator(
            self._config.get('HYTERA_REPEATER_ID', 0))
        self._hytera_outbound = HyteraOutboundPacer(
            self._send_hytera_media)
        self._hytera_zero_peer_warned = False
        self._hytera_outbound_not_ready_reason = None
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
            protocol = _HyteraServiceProtocol(self, service)
            listener = reactor.listenUDP(
                port, protocol, interface=interface)
            self._hytera_services[service] = protocol
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

    def _register_hytera_peer(self, host, port, reset_session=False):
        now = time()
        is_new = not self._hytera_registered
        if reset_session:
            old_peer_id = self._hytera_peer_id
            self._hytera_voice.reset()
            self._hytera_outbound.reset()
            self._hytera_dmr_addr = None
            self._hytera_rdac_addr = None
            self._hytera_rdac_step = 0
            self._hytera_rdac_meta = {}
            if (not self._hytera_proxy_enabled
                    and not int(self._config.get('HYTERA_REPEATER_ID', 0))):
                self._hytera_peer_id = b'\x00\x00\x00\x00'
                self._hytera_voice.set_peer_id(0)
                self._peers.pop(old_peer_id, None)
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
            if self._hytera_rdac_meta:
                self._apply_hytera_rdac_metadata(record)
                self._log_hytera_rdac_metadata()
            elif not record['SOFTWARE_ID']:
                record['SOFTWARE_ID'] = 'RDAC metadata pending'
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
        self._sync_hytera_selfcare_logout()
        self._hytera_registered = False
        self._hytera_addr = None
        self._hytera_last_seen = 0
        self._hytera_voice.reset()
        self._hytera_outbound.reset()
        self._hytera_dmr_addr = None
        self._hytera_rdac_addr = None
        self._hytera_proxy_info = None
        self._hytera_rdac_step = 0
        self._hytera_rdac_meta = {}
        if self._hytera_peer_id in self._peers:
            del self._peers[self._hytera_peer_id]
            if self._report is not None:
                self._report.send_config()

    def _send_p2p(self, packet, addr):
        if packet is not None:
            self.transport.write(packet, addr)

    def _proxy_control_received(self, data, addr=None):
        """Apply sidecar lifecycle data; never accept it in direct mode."""
        if not getattr(self, '_hytera_proxy_enabled', False):
            return False
        trusted_ip = getattr(self, '_hytera_proxy_control_ip', '')
        if not trusted_ip or addr is None or addr[0] != trusted_ip:
            if data == PRCL or data.startswith(PRIN):
                logger.warning(
                    '(%s) Ignored Hytera proxy control from untrusted IP %s',
                    self._system, addr[0] if addr else '<unknown>')
                return True
            return False
        if data == PRCL:
            self._clear_hytera_peer()
            return True
        if not data.startswith(PRIN):
            return False
        try:
            info = json.loads(data[len(PRIN):].decode('utf-8'))
            if not isinstance(info, dict):
                raise ValueError('proxy control payload is not an object')
            repeater_id = info.get('repeater_id') or 0
            if isinstance(repeater_id, bool):
                raise ValueError('invalid repeater ID type')
            repeater_id = int(repeater_id)
            if not 0 <= repeater_id <= 0xffffff:
                raise ValueError('repeater ID out of range')
            for name in ('p2p', 'dmr', 'rdac'):
                endpoint = info.get(name)
                if endpoint is not None and (
                        not isinstance(endpoint, (list, tuple))
                        or len(endpoint) != 2
                        or not isinstance(endpoint[0], str)
                        or isinstance(endpoint[1], bool)
                        or not isinstance(endpoint[1], int)
                        or not 1 <= endpoint[1] <= 65535):
                    raise ValueError(f'invalid {name} endpoint')
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            logger.warning('(%s) Ignored malformed Hytera proxy control packet',
                           self._system)
            return True
        self._hytera_proxy_info = info
        configured_id = int(self._config.get('HYTERA_REPEATER_ID', 0))
        if repeater_id:
            if configured_id and repeater_id != configured_id:
                logger.warning(
                    '(%s) Hytera proxy ID %s does not match configured ID %s',
                    self._system, repeater_id, configured_id)
                self._clear_hytera_peer()
                return True
            old_peer_id = self._hytera_peer_id
            self._hytera_peer_id = repeater_id.to_bytes(4, 'big')
            self._hytera_voice.set_peer_id(repeater_id)
            if not self._registration_allowed(
                    (info.get('p2p') or (addr[0], 0))[0]):
                self._clear_hytera_peer()
                return True
            if old_peer_id != self._hytera_peer_id:
                self._peers.pop(old_peer_id, None)
                if self._hytera_registered and self._hytera_addr:
                    self._register_hytera_peer(*self._hytera_addr)
        return True

    def hytera_p2p_received(self, data, addr):
        host, port = addr
        if self._hytera_trace:
            logger.debug('(%s) Hytera P2P RX %s:%s %s',
                         self._system, host, port, data.hex())

        if self._proxy_control_received(data, addr):
            return
        command = p2p_command_type(data)
        if command == P2P_REGISTRATION:
            if not self._registration_allowed(host):
                return
            self._register_hytera_peer(host, port, reset_session=True)
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
        if not self._hytera_registered:
            return
        if not self._hytera_addr or addr[0] != self._hytera_addr[0]:
            logger.warning('(%s) Hytera DMR packet rejected from unregistered IP %s',
                           self._system, addr[0])
            return
        if data == b'\x00':
            self._hytera_dmr_addr = addr
            self._touch_hytera_peer(addr)
            return
        dmrd = self._hytera_voice.translate_voice(data)
        if dmrd is None:
            logger.warning(
                '(%s) Ignored malformed Hytera DMR packet from %s:%s len=%s',
                self._system, addr[0], addr[1], len(data))
            return
        self._hytera_dmr_addr = addr
        self._touch_hytera_peer(addr)
        ids = media_radio_ids(data)
        logger.debug(
            '(%s) Hytera DMR packet from %s:%s len=%s slot=%s ids=%s%s',
            self._system, addr[0], addr[1], len(data),
            media_timeslot(data), ids,
            ' data=' + data.hex() if self._hytera_trace else '')
        self._dispatch_hytera_dmrd(dmrd, addr)

    def _dispatch_hytera_dmrd(self, data, addr):
        peer_id = data[11:15]
        if not int_id(peer_id):
            if not self._hytera_zero_peer_warned:
                logger.warning(
                    '(%s) Hytera media dropped — configure HYTERA_REPEATER_ID',
                    self._system)
                self._hytera_zero_peer_warned = True
            return
        if not self._hytera_addr or addr[0] != self._hytera_addr[0]:
            return

        seq = data[4]
        rf_src = data[5:8]
        dst_id = data[8:11]
        bits = data[15]
        slot = 2 if bits & 0x80 else 1
        call_type = 'unit' if bits & 0x40 else 'group'
        frame_type = (bits & 0x30) >> 4
        dtype_vseq = bits & 0x0f
        stream_id = data[16:20]

        global_config = self._CONFIG.get('GLOBAL', {})
        if global_config.get('USE_ACL'):
            if not acl_check(rf_src, global_config.get('SUB_ACL', '')):
                return
            if call_type == 'group':
                tg_acl = (
                    global_config.get('TG2_ACL', '')
                    if slot == 2 else global_config.get('TG1_ACL', ''))
                if not acl_check(dst_id, tg_acl):
                    return

        if (self._config.get('USE_ACL')
                and not acl_check(rf_src, self._config['SUB_ACL'])):
            return
        if self._config.get('USE_ACL') and call_type == 'group':
            tg_acl = (
                self._config['TG2_ACL']
                if slot == 2 else self._config['TG1_ACL'])
            if not acl_check(dst_id, tg_acl):
                return

        self.dmrd_received(
            peer_id, rf_src, dst_id, seq, slot, call_type,
            frame_type, dtype_vseq, stream_id, data)

    def hytera_rdac_received(self, data, addr):
        if (not self._hytera_registered or not self._hytera_addr
                or addr[0] != self._hytera_addr[0]):
            return
        self._hytera_rdac_addr = addr
        self._touch_hytera_peer(addr)
        logger.debug(
            '(%s) Hytera RDAC packet from %s:%s len=%s%s',
            self._system, addr[0], addr[1], len(data),
            ' data=' + data.hex() if self._hytera_trace else '')
        if self._hytera_rdac_enabled:
            self._advance_rdac_identification(data, addr)

    def _send_rdac(self, packet, addr):
        protocol = self._hytera_services.get('rdac')
        if protocol is not None and hasattr(protocol, 'transport'):
            protocol.transport.write(packet, addr)

    def _advance_rdac_identification(self, data, addr):
        """Run the capture-validated RDAC exchange and collect peer metadata."""
        step = self._hytera_rdac_step
        # The service socket can send keepalive polls during an identity
        # exchange. Only an idle state may use one to begin the sequence;
        # restarting in-flight RDAC loses metadata and selfcare identity.
        if data == b'\x00' and step == 0:
            self._hytera_rdac_step = 1
            self._send_rdac(RDAC_STEP0_REQUEST, addr)
        elif step == 1 and data.startswith(RDAC_STEP0_RESPONSE):
            self._hytera_rdac_step = 2
            self._send_rdac(RDAC_STEP1_REQUEST, addr)
        elif step == 2 and data.startswith(RDAC_STEP1_RESPONSE):
            self._hytera_rdac_step = 3
        elif step == 3 and rdac_repeater_id(data):
            repeater_id = rdac_repeater_id(data)
            if not int_id(self._hytera_peer_id):
                self._hytera_peer_id = repeater_id.to_bytes(4, 'big')
                self._hytera_voice.set_peer_id(repeater_id)
                if not self._registration_allowed(addr[0]):
                    self._clear_hytera_peer()
                    return
                if self._hytera_registered and self._hytera_addr:
                    self._register_hytera_peer(*self._hytera_addr)
            self._hytera_rdac_step = 4
            self._send_rdac(RDAC_STEP3_REQUEST, addr)
        elif step == 4 and data.startswith(b'\x7e\x04\x00\x00'):
            self._hytera_rdac_step = 5
            self._send_rdac(RDAC_STEP4_REQUEST_1, addr)
            self._send_rdac(RDAC_STEP4_REQUEST_2, addr)
        elif step == 5 and data.startswith(RDAC_STEP1_RESPONSE):
            self._hytera_rdac_step = 6
        elif step == 6 and data.startswith(b'\x7e\x04\x00\x00'):
            self._hytera_rdac_meta.update(parse_rdac_identity(data))
            self._hytera_rdac_step = 7
            self._send_rdac(RDAC_STEP6_REQUEST_1, addr)
            self._send_rdac(RDAC_STEP6_REQUEST_2, addr)
        elif step == 7 and data.startswith(RDAC_STEP1_RESPONSE):
            self._hytera_rdac_step = 8
            self._send_rdac(RDAC_STEP7_REQUEST, addr)
        elif step == 8 and data.startswith(RDAC_STEP1_RESPONSE):
            self._hytera_rdac_step = 10
        elif step == 10 and data.startswith(b'\x7e\x04\x00\x00'):
            self._hytera_rdac_meta.update(parse_rdac_channel(data))
            self._hytera_rdac_step = 11
            self._send_rdac(RDAC_STEP10_REQUEST, addr)
        elif step == 11 and data.startswith(RDAC_STEP1_RESPONSE):
            self._hytera_rdac_step = 12
        elif step == 12 and data.startswith(b'\x7e\x04\x00\x00'):
            self._hytera_rdac_step = 13
            self._send_rdac(RDAC_STEP12_REQUEST_1, addr)
            self._send_rdac(RDAC_STEP12_REQUEST_2, addr)
        elif step == 13 and data.startswith(RDAC_STEP12_RESPONSE):
            self._hytera_rdac_step = 14
            self._publish_hytera_rdac_metadata()
            logger.info('(%s) Hytera RDAC identity exchange completed',
                        self._system)

    def _apply_hytera_rdac_metadata(self, peer):
        """Merge validated RDAC fields into an existing monitor peer record."""
        meta = self._hytera_rdac_meta
        firmware = meta.get('firmware')
        hardware = meta.get('hardware')
        serial = meta.get('serial')
        callsign = meta.get('callsign')
        if firmware:
            peer['SOFTWARE_ID'] = firmware
        if hardware:
            peer['DESCRIPTION'] = hardware
            peer['HYTERA_HARDWARE'] = hardware
        if serial:
            peer['SERIAL'] = serial
            peer['HYTERA_SERIAL'] = serial
        if callsign:
            peer['HYTERA_CALLSIGN'] = callsign
            current = peer.get('CALLSIGN', '')
            if isinstance(current, bytes):
                current = current.decode('utf-8', errors='ignore').rstrip()
            if not current or current == str(int_id(self._hytera_peer_id)):
                peer['CALLSIGN'] = callsign
        for source, target in (
                ('mode_raw', 'HYTERA_MODE'),
                ('tx_frequency', 'TX_FREQ'),
                ('rx_frequency', 'RX_FREQ')):
            if source in meta:
                peer[target] = meta[source]

    def _publish_hytera_rdac_metadata(self):
        if not int_id(self._hytera_peer_id):
            return
        peer = self._peers.get(self._hytera_peer_id)
        if peer is None and self._hytera_registered and self._hytera_addr:
            self._register_hytera_peer(*self._hytera_addr)
            peer = self._peers.get(self._hytera_peer_id)
        if peer is None:
            return
        self._apply_hytera_rdac_metadata(peer)
        self._sync_hytera_selfcare_register()
        if self._report is not None:
            self._report.send_config()
        self._log_hytera_rdac_metadata()

    def _sync_hytera_selfcare_register(self):
        """Make validated Hytera identity available to the IPSC-style selfcare UI."""
        full_config = getattr(self, '_CONFIG', {})
        ss = full_config.get('SELF SERVICE', {})
        if not ss.get('ENABLED'):
            return
        db = full_config.get('_SELF_SERVICE_DB')
        if db is None or not int_id(self._hytera_peer_id):
            return
        peer = self._peers.get(self._hytera_peer_id, {})
        callsign = peer.get('CALLSIGN') or self._hytera_rdac_meta.get('callsign')
        if isinstance(callsign, bytes):
            callsign = callsign.decode('utf-8', errors='ignore').strip()
        else:
            callsign = str(callsign or '').strip()
        repeater_id = int_id(self._hytera_peer_id)
        if not callsign:
            callsign = str(repeater_id)
        host = (self._hytera_addr or ('', 0))[0]
        seed = build_ipsc_seed_options(self._config)
        d = db.upsert_hytera_client(
            repeater_id, self._hytera_peer_id, callsign, host, seed)

        def _store_metadata(_):
            metadata = db.upsert_hytera_metadata(
                repeater_id, self._hytera_rdac_meta)
            metadata.addErrback(
                lambda f: logger.error(
                    '(%s) Hytera selfcare metadata cache failed for %s: %s',
                    self._system, repeater_id, f.getErrorMessage()))
            return metadata

        def _mark_pending(_):
            pending = db.mark_hytera_options_pending(repeater_id)
            pending.addErrback(
                lambda f: logger.error(
                    '(%s) Hytera selfcare mark pending failed for %s: %s',
                    self._system, repeater_id, f.getErrorMessage()))
            return pending

        d.addCallback(_store_metadata)
        d.addCallback(_mark_pending)
        d.addErrback(
            lambda f: logger.error(
                '(%s) Hytera selfcare upsert failed for %s: %s',
                self._system, repeater_id, f.getErrorMessage()))

    def _sync_hytera_selfcare_logout(self):
        full_config = getattr(self, '_CONFIG', {})
        ss = full_config.get('SELF SERVICE', {})
        if not ss.get('ENABLED'):
            return
        db = full_config.get('_SELF_SERVICE_DB')
        if db is None or not int_id(self._hytera_peer_id):
            return
        repeater_id = int_id(self._hytera_peer_id)
        d = db.logout_hytera_client(repeater_id)
        d.addErrback(
            lambda f: logger.error(
                '(%s) Hytera selfcare logout failed for %s: %s',
                self._system, repeater_id, f.getErrorMessage()))

    def _log_hytera_rdac_metadata(self):
        logger.info(
            '(%s) Hytera RDAC metadata published: firmware=%r hardware=%r '
            'serial=%r callsign=%r mode=%r tx_frequency=%r rx_frequency=%r',
            self._system,
            self._hytera_rdac_meta.get('firmware'),
            self._hytera_rdac_meta.get('hardware'),
            self._hytera_rdac_meta.get('serial'),
            self._hytera_rdac_meta.get('callsign'),
            self._hytera_rdac_meta.get('mode_raw'),
            self._hytera_rdac_meta.get('tx_frequency'),
            self._hytera_rdac_meta.get('rx_frequency'),
        )

    def _send_hytera_media(self, packet):
        protocol = self._hytera_services.get('dmr')
        if (protocol is None or not hasattr(protocol, 'transport')
                or self._hytera_dmr_addr is None):
            return False
        protocol.transport.write(packet, self._hytera_dmr_addr)
        if self._hytera_trace:
            logger.debug(
                '(%s) Hytera DMR TX %s:%s len=%s data=%s',
                self._system, self._hytera_dmr_addr[0],
                self._hytera_dmr_addr[1], len(packet), packet.hex())
        return True

    def hytera_send_system(self, packet, _hops=b'', _ber=b'\x00',
                           _rssi=b'\x00', _source_server=b'\x00\x00\x00\x00',
                           _source_rptr=b'\x00\x00\x00\x00',
                           _late_join=False, _late_join_sequence=None):
        """Bridge outbound group or private DMRD voice to the RD985."""
        if not self._config.get('REPEAT', True):
            reason = 'repeater output disabled'
        elif not self._hytera_registered:
            reason = 'repeater is not registered'
        elif self._hytera_dmr_addr is None:
            reason = 'DMR service endpoint has not been negotiated'
        else:
            reason = None
        if reason is not None:
            if getattr(self, '_hytera_outbound_not_ready_reason', None) != reason:
                logger.warning('(%s) Hytera media suppressed: %s',
                               self._system, reason)
                self._hytera_outbound_not_ready_reason = reason
            return False
        self._hytera_outbound_not_ready_reason = None
        quality = _rssi[0] if _rssi else 0
        encoded = self._hytera_voice.encode_voice(
            packet, quality, late_join=_late_join,
            late_join_sequence=_late_join_sequence)
        if encoded is None:
            return False
        ts, wire_packet, paced = encoded
        if (_late_join and not paced
                and wire_packet[18:20] != b'\x11\x11'):
            self._hytera_outbound.reset_slot(ts)
            # IPSC2 waits one 60 ms slot after the RD985's activation headers,
            # then continues the live voice stream. A longer prebuffer misses
            # the active transmission instead of joining it.
            return self._hytera_outbound.enqueue(
                ts, wire_packet, jitter_depth=1)
        if paced:
            return self._hytera_outbound.enqueue(ts, wire_packet)
        if (_late_join
                or (wire_packet[8] & 0x3f) == 0x02
                or wire_packet[18:20] == b'\x11\x11'):
            self._hytera_outbound.reset_slot(ts)
        self._hytera_outbound.send_control(wire_packet)
        return True

    def hytera_dereg(self):
        self._clear_hytera_peer()
