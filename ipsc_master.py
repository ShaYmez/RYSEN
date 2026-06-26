#!/usr/bin/env python3
###############################################################################
#   RYSEN Motorola IPSC master mixin (MODE: IPSC) — Phase 1
#   Copyright (C) 2026 Shane Daley, M0VUB
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

import hmac as hmac_mod
import socket
import struct
from collections import deque
from hashlib import sha1
from time import time

from dmr_utils3.utils import int_id

from hblink import HBSYSTEM, logger, acl_check, build_peer_record
from const import DMRD
from ipsc_const import (
    GROUP_VOICE, MASTER_REG_REQ, MASTER_REG_REPLY,
    PEER_LIST_REQ, PEER_LIST_REPLY,
    MASTER_ALIVE_REQ, MASTER_ALIVE_REPLY,
    DE_REG_REQ, DE_REG_REPLY, XCMP_XNL,
    VOICE_HEAD, VOICE_TERM,
    GV_BURST_TYPE_OFF, GV_CALL_INFO_OFF, GV_MIN_LEN, TS_CALL_MSK,
    AUTH_DIGEST_LEN, IPSC_VER,
    DEFAULT_IPSC_MODE_BYTE, DEFAULT_IPSC_FLAGS_BYTES,
    PRCL, PRIN,
    opcode_name, peer_id_from_packet,
)
from ipsc_peer_meta import parse_ipsc_peer_status, ipsc_peer_display_fields
from ipsc_voice import IpscVoiceTranslator
from selfcare_db import build_ipsc_seed_options


class IpscMasterMixin:
    """IPSC master behaviour mixed into routerIPSC (bridge_master.routerHBP subclass)."""

    def init_ipsc(self):
        self._ipsc_peers = {}
        self._master_id = self._config['IPSC_MASTER_ID'].to_bytes(4, 'big')
        self._ts_flags = self._config.get('IPSC_MODE_BYTE', DEFAULT_IPSC_MODE_BYTE) + self._config.get(
            'IPSC_FLAGS_BYTES', DEFAULT_IPSC_FLAGS_BYTES)
        self._ipsc_version = self._config.get('IPSC_VERSION', IPSC_VER)
        self._auth_enabled = self._config.get('AUTH_ENABLED', False)
        self._auth_key = self._config.get('AUTH_KEY', b'')
        self._keepalive_watchdog = self._config.get('KEEPALIVE_WATCHDOG', 60)
        self._max_peers = self._config.get('MAX_PEERS', 1)
        self._voice = IpscVoiceTranslator(
            master_id=self._config['IPSC_MASTER_ID'],
            ts_prefer_call_info=self._config.get('TS_PREFER_CALL_INFO', False),
        )
        self._voice.set_send_callback(self._ipsc_send_voice)
        self._alive_reply = (
            bytes([MASTER_ALIVE_REPLY]) + self._master_id + self._ts_flags + self._ipsc_version
        )
        self._dereg_reply = bytes([DE_REG_REPLY]) + self._master_id
        self.datagramReceived = self.ipsc_datagramReceived
        self.maintenance_loop = self.ipsc_maintenance_loop
        self.send_system = self.ipsc_send_system

    def startProtocol(self):
        HBSYSTEM.startProtocol(self)

    def ipsc_maintenance_loop(self):
        now = time()
        remove_list = deque()
        for peer_id, peer in self._ipsc_peers.items():
            if now - peer['last_ka'] > self._keepalive_watchdog:
                remove_list.append(peer_id)
        for peer_id in remove_list:
            peer = self._ipsc_peers[peer_id]
            logger.info('(%s) IPSC peer %s (%s:%s) timed out',
                        self._system, int_id(peer_id), peer['host'], peer['port'])
            self._remove_ipsc_peer(peer_id)

    def ipsc_datagramReceived(self, data, addr):
        host, port = addr

        if len(data) >= 4 and data[:4] == PRCL:
            if len(data) >= 8:
                self._remove_ipsc_peer(data[4:8])
            return

        if len(data) >= 5 and data[:4] == PRIN:
            logger.info('(%s) *ProxyInfo* Connection from IP:Port: %s',
                        self._system, data[4:].decode('utf-8', errors='replace'))
            return

        if self._auth_enabled:
            if not self._check_auth(data):
                logger.warning('(%s) IPSC auth failure from %s:%s', self._system, host, port)
                return
            data = data[:-AUTH_DIGEST_LEN]

        if not data:
            return

        opcode = data[0]
        if opcode == XCMP_XNL:
            logger.debug('(%s) XCMP/XNL from %s:%s ignored', self._system, host, port)
            return

        peer_id = peer_id_from_packet(data)
        if peer_id and peer_id in self._ipsc_peers and self._ipsc_peers[peer_id]['host'] == host:
            self._touch_ipsc_peer(peer_id)

        if opcode == MASTER_REG_REQ:
            self._on_reg_req(data, host, port)
        elif opcode == MASTER_ALIVE_REQ:
            self._on_alive_req(data, host, port)
        elif opcode == PEER_LIST_REQ:
            self._on_peer_list_req(host, port)
        elif opcode == DE_REG_REQ:
            self._on_de_reg_req(data, host, port)
        elif opcode == GROUP_VOICE:
            self._on_group_voice(data, host, port)
        elif opcode in (MASTER_REG_REPLY, PEER_LIST_REPLY, MASTER_ALIVE_REPLY, DE_REG_REPLY):
            return
        else:
            logger.debug('(%s) IPSC %s from %s:%s len=%d',
                         self._system, opcode_name(opcode), host, port, len(data))

    def _on_reg_req(self, data, host, port):
        if len(data) < 6:
            logger.warning('(%s) MASTER_REG_REQ too short from %s:%s', self._system, host, port)
            return

        peer_id = data[1:5]
        peer_id_int = int_id(peer_id)
        ipsc_status = parse_ipsc_peer_status(data)
        peer_mode = bytes([ipsc_status['mode']]) if ipsc_status else data[5:6]
        is_new = peer_id not in self._ipsc_peers

        if self._config.get('USE_ACL') and not acl_check(peer_id, self._config['REG_ACL']):
            logger.warning('(%s) IPSC registration denied for %s from %s (REG_ACL)',
                           self._system, peer_id_int, host)
            return

        allowed_ips = self._config.get('ALLOWED_PEER_IPS') or []
        if allowed_ips and host not in allowed_ips:
            logger.warning('(%s) IPSC registration denied for %s — IP %s not allowed',
                           self._system, peer_id_int, host)
            return

        allowed_ids = self._config.get('ALLOWED_PEER_IDS') or []
        if allowed_ids and peer_id_int not in allowed_ids:
            logger.warning('(%s) IPSC registration denied for %s — ID not in allow list',
                           self._system, peer_id_int)
            return

        if not is_new and self._ipsc_peers[peer_id]['host'] != host:
            logger.warning('(%s) IPSC registration rejected — ID %s already registered from %s',
                           self._system, peer_id_int, self._ipsc_peers[peer_id]['host'])
            return

        if is_new and len(self._ipsc_peers) >= self._max_peers:
            logger.warning('(%s) IPSC registration rejected — MAX_PEERS (%s) reached',
                           self._system, self._max_peers)
            return

        now = time()
        self._ipsc_peers[peer_id] = {
            'host': host,
            'port': port,
            'mode': peer_mode,
            'last_ka': now,
        }
        if ipsc_status:
            self._ipsc_peers[peer_id]['flags'] = ipsc_status['flags']
            self._ipsc_peers[peer_id]['protocol'] = ipsc_status['protocol']
        self._register_hbp_peer(
            peer_id, host, port, peer_mode=peer_mode, ipsc_status=ipsc_status,
            is_new=is_new, now=now)
        self._send_peers_config()

        reg_reply = (
            bytes([MASTER_REG_REPLY])
            + self._master_id
            + self._ts_flags
            + struct.pack('>H', len(self._ipsc_peers))
            + self._ipsc_version
        )
        self._ipsc_send(reg_reply, host, port)
        self._send_peer_list(host, port)

        if is_new:
            logger.info('(%s) IPSC peer registered: ID %s from %s:%s (%d/%d)',
                        self._system, peer_id_int, host, port,
                        len(self._ipsc_peers), self._max_peers)
        else:
            logger.info('(%s) IPSC peer re-registered: ID %s from %s:%s',
                        self._system, peer_id_int, host, port)

    def _register_hbp_peer(self, peer_id, host, port, peer_mode=None, ipsc_status=None,
                           is_new=True, now=None):
        existing = None if is_new else self._peers.get(peer_id)
        self._peers[peer_id] = build_peer_record(
            peer_id, host, port,
            protocol='IPSC',
            connection='YES',
            peer_mode=peer_mode,
            existing=existing,
            now=now,
            full_config=self._CONFIG,
            ipsc_status=ipsc_status,
        )
        self._sync_ipsc_selfcare_register(peer_id, host, is_new)

    def _sync_ipsc_selfcare_register(self, peer_id, host, is_new):
        ss = self._CONFIG.get('SELF SERVICE', {})
        if not ss.get('ENABLED'):
            return
        db = self._CONFIG.get('_SELF_SERVICE_DB')
        if db is None:
            return
        peer_rec = self._peers.get(peer_id, {})
        callsign = peer_rec.get('CALLSIGN', b'')
        if isinstance(callsign, bytes):
            callsign = callsign.decode('utf-8', errors='ignore').strip()
        else:
            callsign = str(callsign).strip()
        if not callsign:
            callsign = str(int_id(peer_id))
        seed = build_ipsc_seed_options(self._config) if is_new else None
        rid = int(int_id(peer_id))
        d = db.upsert_ipsc_client(rid, peer_id, callsign, host, seed)
        d.addErrback(
            lambda f, _rid=rid: logger.error(
                '(%s) IPSC selfcare upsert failed for %s: %s',
                self._system, _rid, f.getErrorMessage()))

    def _sync_ipsc_selfcare_logout(self, peer_id):
        ss = self._CONFIG.get('SELF SERVICE', {})
        if not ss.get('ENABLED'):
            return
        db = self._CONFIG.get('_SELF_SERVICE_DB')
        if db is None:
            return
        rid = int(int_id(peer_id))
        d = db.logout_ipsc_client(rid)
        d.addErrback(
            lambda f, _rid=rid: logger.error(
                '(%s) IPSC selfcare logout failed for %s: %s',
                self._system, _rid, f.getErrorMessage()))

    def _touch_ipsc_peer(self, peer_id):
        now = time()
        self._ipsc_peers[peer_id]['last_ka'] = now
        if peer_id in self._peers:
            self._peers[peer_id]['LAST_PING'] = now

    def _send_peers_config(self):
        if self._report is not None:
            self._report.send_config()

    def _on_alive_req(self, data, host, port):
        if len(data) < 5:
            return
        peer_id = data[1:5]
        if peer_id not in self._ipsc_peers:
            return
        ipsc_status = parse_ipsc_peer_status(data)
        if ipsc_status:
            self._ipsc_peers[peer_id]['mode'] = bytes([ipsc_status['mode']])
            self._ipsc_peers[peer_id]['flags'] = ipsc_status['flags']
            self._ipsc_peers[peer_id]['protocol'] = ipsc_status['protocol']
            if peer_id in self._peers:
                display = ipsc_peer_display_fields(
                    ipsc_status['mode'], ipsc_status['flags'], ipsc_status['protocol'])
                for key, value in display.items():
                    if key.startswith('IPSC_'):
                        self._peers[peer_id][key] = value
                    elif value:
                        self._peers[peer_id][key] = value
        self._touch_ipsc_peer(peer_id)
        if peer_id in self._peers:
            self._peers[peer_id]['PINGS_RECEIVED'] = self._peers[peer_id].get('PINGS_RECEIVED', 0) + 1
        self._ipsc_send(self._alive_reply, host, port)

    def _on_peer_list_req(self, host, port):
        if not any(p['host'] == host for p in self._ipsc_peers.values()):
            return
        self._send_peer_list(host, port)

    def _on_de_reg_req(self, data, host, port):
        peer_id = data[1:5] if len(data) >= 5 else b'\x00\x00\x00\x00'
        logger.info('(%s) IPSC peer de-registering: %s from %s:%s',
                    self._system, int_id(peer_id), host, port)
        self._ipsc_send(self._dereg_reply, host, port)
        self._remove_ipsc_peer(peer_id)

    def _on_group_voice(self, data, host, port):
        if not self._ipsc_peers:
            return
        peer_id = data[1:5] if len(data) >= 5 else None
        if peer_id not in self._ipsc_peers:
            return
        if len(data) < GV_MIN_LEN:
            logger.warning('(%s) GROUP_VOICE too short (%d) from %s:%s',
                           self._system, len(data), host, port)
            return

        self._voice.learn_peer_header(data)

        burst_type = data[GV_BURST_TYPE_OFF]
        call_info = data[GV_CALL_INFO_OFF]
        if burst_type in (VOICE_HEAD, VOICE_TERM):
            ts = 2 if (call_info & TS_CALL_MSK) else 1
        else:
            ts = 2 if (burst_type & 0x80) else 1

        dmrd = self._voice.translate(data, ts, burst_type)
        if dmrd is None:
            return

        self._dispatch_ipsc_dmrd(dmrd, (host, port))

    def _dispatch_ipsc_dmrd(self, _data, _sockaddr):
        _peer_id = _data[11:15]
        if _peer_id not in self._ipsc_peers:
            return
        if self._ipsc_peers[_peer_id]['host'] != _sockaddr[0]:
            return

        _seq = _data[4]
        _rf_src = _data[5:8]
        _dst_id = _data[8:11]
        _bits = _data[15]
        _slot = 2 if (_bits & 0x80) else 1
        if _bits & 0x40:
            _call_type = 'unit'
        elif (_bits & 0x23) == 0x23:
            _call_type = 'vcsbk'
        else:
            _call_type = 'group'
        _frame_type = (_bits & 0x30) >> 4
        _dtype_vseq = (_bits & 0xF)
        _stream_id = _data[16:20]

        if self._config.get('USE_ACL') and not acl_check(_rf_src, self._config['SUB_ACL']):
            return
        if _call_type == 'group' and self._config.get('USE_ACL'):
            acl = self._config['TG2_ACL'] if _slot == 2 else self._config['TG1_ACL']
            if not acl_check(_dst_id, acl):
                return

        self.dmrd_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type,
                           _frame_type, _dtype_vseq, _stream_id, _data)

    def _remove_ipsc_peer(self, peer_id):
        had_peer = peer_id in self._ipsc_peers or peer_id in self._peers
        if peer_id in self._ipsc_peers or peer_id in self._peers:
            self._sync_ipsc_selfcare_logout(peer_id)
        if peer_id in self._ipsc_peers:
            del self._ipsc_peers[peer_id]
        if peer_id in self._peers:
            del self._peers[peer_id]
        if not self._ipsc_peers:
            self._voice.reset()
        if had_peer:
            self._send_peers_config()

    def _send_peer_list(self, host, port):
        entries = b''
        for pid, peer in self._ipsc_peers.items():
            try:
                packed_ip = socket.inet_aton(peer['host'])
            except OSError:
                packed_ip = b'\x00\x00\x00\x00'
            entries += pid + packed_ip + struct.pack('>H', peer['port']) + peer['mode']

        peer_list_reply = (
            bytes([PEER_LIST_REPLY])
            + self._master_id
            + struct.pack('>H', len(entries))
            + entries
        )
        self._ipsc_send(peer_list_reply, host, port)

    def _check_auth(self, data):
        if len(data) <= AUTH_DIGEST_LEN:
            return False
        payload = data[:-AUTH_DIGEST_LEN]
        received = data[-AUTH_DIGEST_LEN:]
        expected = hmac_mod.new(self._auth_key, payload, sha1).digest()[:10]
        return received == expected

    def _auth_suffix(self, packet):
        if not self._auth_enabled:
            return b''
        return hmac_mod.new(self._auth_key, packet, sha1).digest()[:10]

    def _ipsc_send(self, packet, host, port):
        self.transport.write(packet + self._auth_suffix(packet), (host, port))

    def _ipsc_send_voice(self, packet):
        for peer in self._ipsc_peers.values():
            self._ipsc_send(packet, peer['host'], peer['port'])

    def ipsc_send_system(self, _packet, _hops=b'', _ber=b'\x00', _rssi=b'\x00',
                         _source_server=b'\x00\x00\x00\x00', _source_rptr=b'\x00\x00\x00\x00'):
        """Bridge outbound DMRD → GROUP_VOICE to registered IPSC peers (Phase 2c)."""
        if _packet[:4] != DMRD:
            return
        if len(_packet) < 54:
            _packet = b''.join([_packet, _ber, _rssi])

        _bits = _packet[15]
        if _bits & 0x40:
            return
        if (_bits & 0x23) == 0x23:
            return

        if not self._config.get('REPEAT', True):
            return

        if not self._ipsc_peers:
            return

        gv = self._voice.handle_outbound(_packet)
        if gv is not None:
            self._ipsc_send_voice(gv)
