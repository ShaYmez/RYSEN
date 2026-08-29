#!/usr/bin/env python3
"""Loopback HTTP control listener for FreeSTAR hub /v2/control/*.

Listens on 127.0.0.1 only when /etc/rysen/freestar-control.token exists.
Calls existing bridge_master hooks. Does not scrape REPORT.
"""

from __future__ import annotations

import json
import os
from typing import Callable, Dict, Optional, Tuple

TOKEN_PATH = os.environ.get('FREESTAR_CONTROL_TOKEN', '/etc/rysen/freestar-control.token')
LISTEN_HOST = os.environ.get('FREESTAR_CONTROL_HOST', '127.0.0.1')
LISTEN_PORT = int(os.environ.get('FREESTAR_CONTROL_PORT', '8765'))
MAX_BODY = 65536


def load_token(path: str = TOKEN_PATH) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    text = open(path, encoding='utf-8').read().strip()
    return text or None


def slot_from_body(body: dict) -> int:
    try:
        slot = int(body.get('slot') or 0)
    except (TypeError, ValueError):
        slot = 0
    return 2 if slot in (0, None) else slot


def handle_control_request(
    action: str,
    method: str,
    body: dict,
    *,
    find_peer: Callable[[int], Tuple[Optional[str], object]],
    disconnect: Callable,
    drop_dynamic: Callable,
    activate_tg: Callable,
    deactivate_tg: Callable,
) -> Tuple[int, dict]:
    radio = body.get('radio_id')
    try:
        radio_id = int(radio)
    except (TypeError, ValueError):
        return 400, {'error': 'radio_id is required'}

    system, peer_id = find_peer(radio_id)
    if not system:
        return 404, {'error': 'Peer not connected on this master'}

    method = (method or 'POST').upper()
    action = (action or '').strip().strip('/')

    if action in ('disconnect', 'drop-call'):
        disconnect(system, peer_id)
        return 200, {'ok': True, 'action': action, 'system': system}

    if action == 'drop-dynamic':
        drop_dynamic(system, peer_id)
        return 200, {'ok': True, 'action': action, 'system': system}

    if action == 'talkgroup':
        tg = body.get('talkgroup', body.get('group'))
        try:
            tgid = int(tg)
        except (TypeError, ValueError):
            return 400, {'error': 'talkgroup is required'}
        slot = slot_from_body(body)
        if method == 'DELETE':
            deactivate_tg(system, tgid, slot, peer_id)
            return 200, {'ok': True, 'action': 'talkgroup-remove', 'system': system, 'talkgroup': tgid}
        activate_tg(system, tgid, slot, peer_id)
        return 200, {'ok': True, 'action': 'talkgroup-add', 'system': system, 'talkgroup': tgid}

    return 404, {'error': 'Unknown control action'}


def _http_response(code: int, body: dict) -> bytes:
    payload = json.dumps(body, separators=(',', ':')).encode('utf-8')
    reason = {200: 'OK', 400: 'Bad Request', 401: 'Unauthorized', 404: 'Not Found', 405: 'Method Not Allowed'}.get(code, 'Error')
    headers = (
        f'HTTP/1.1 {code} {reason}\r\n'
        'Content-Type: application/json\r\n'
        f'Content-Length: {len(payload)}\r\n'
        'Connection: close\r\n'
        '\r\n'
    )
    return headers.encode('ascii') + payload


def _wire_handlers():
    import bridge_master as bm
    from dmr_utils3.utils import bytes_3
    from selfcare_db import find_hotspot_master_peer, find_ipsc_peer_for_radio_id

    def find_peer(radio_id: int):
        system, peer_id = find_hotspot_master_peer(bm.CONFIG['SYSTEMS'], radio_id)
        if system:
            return system, peer_id
        return find_ipsc_peer_for_radio_id(bm.CONFIG['SYSTEMS'], radio_id)

    def disconnect(system, peer_id):
        bm.selfcare_disconnect(system, peer_id)

    def drop_dynamic(system, peer_id=None):
        bm.disconnect_dial_reflectors(system)
        bm.deactivate_user_activated_bridges(system)
        bm.notify_bridge_table_updated()

    def activate_tg(system, tgid, slot, peer_id):
        name = str(int(tgid))
        tgid_b = bytes_3(int(tgid))
        tmout = bm.CONFIG['SYSTEMS'].get(system, {}).get('DEFAULT_UA_TIMER', 10)
        if name not in bm.BRIDGES:
            bm.make_single_bridge(tgid_b, system, slot, tmout)
        else:
            bm.activate_ua_bridge_source(name, system, slot, tmout, peer_id)

    def deactivate_tg(system, tgid, slot, peer_id=None):
        name = str(int(tgid))
        if name not in bm.BRIDGES:
            return
        changed = False
        for entry in bm.BRIDGES[name]:
            if entry.get('SYSTEM') != system:
                continue
            if slot and entry.get('TS') != slot:
                continue
            if entry.get('ACTIVE'):
                entry['ACTIVE'] = False
                changed = True
        if changed:
            bm.rebuild_bridge_index()
            bm.notify_bridge_table_updated()

    return {
        'find_peer': find_peer,
        'disconnect': disconnect,
        'drop_dynamic': drop_dynamic,
        'activate_tg': activate_tg,
        'deactivate_tg': deactivate_tg,
    }


def start_control_api(logger=None):
    token = load_token()
    if not token:
        if logger:
            logger.info('(CONTROL) token file missing — listener not started')
        return None

    from twisted.internet import reactor
    from twisted.internet.protocol import Factory, Protocol

    class ControlProtocol(Protocol):
        def __init__(self, token: str, handlers: dict):
            self.token = token
            self.handlers = handlers
            self._buf = b''

        def dataReceived(self, data: bytes):
            self._buf += data
            if len(self._buf) > MAX_BODY + 4096:
                self.transport.write(_http_response(400, {'error': 'Request too large'}))
                self.transport.loseConnection()
                return
            header_end = self._buf.find(b'\r\n\r\n')
            if header_end < 0:
                return
            raw_headers = self._buf[:header_end].decode('iso-8859-1', errors='replace')
            rest = self._buf[header_end + 4:]
            lines = raw_headers.split('\r\n')
            request_line = lines[0] if lines else ''
            parts = request_line.split()
            if len(parts) < 2:
                self.transport.write(_http_response(400, {'error': 'Bad request'}))
                self.transport.loseConnection()
                return
            method, path = parts[0].upper(), parts[1]
            headers: Dict[str, str] = {}
            for line in lines[1:]:
                if ':' not in line:
                    continue
                name, val = line.split(':', 1)
                headers[name.strip().lower()] = val.strip()
            try:
                length = int(headers.get('content-length', '0') or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY:
                self.transport.write(_http_response(400, {'error': 'Request too large'}))
                self.transport.loseConnection()
                return
            if len(rest) < length:
                return
            auth = headers.get('authorization', '')
            if auth != 'Bearer ' + self.token:
                self.transport.write(_http_response(401, {'error': 'Unauthorized'}))
                self.transport.loseConnection()
                return
            if method not in ('POST', 'DELETE'):
                self.transport.write(_http_response(405, {'error': 'Method not allowed'}))
                self.transport.loseConnection()
                return
            body = {}
            if rest[:length]:
                try:
                    parsed = json.loads(rest[:length].decode('utf-8'))
                    if isinstance(parsed, dict):
                        body = parsed
                except (ValueError, UnicodeDecodeError):
                    self.transport.write(_http_response(400, {'error': 'Invalid JSON body'}))
                    self.transport.loseConnection()
                    return
            action = path.split('?')[0].strip('/')
            code, payload = handle_control_request(
                action,
                method,
                body,
                find_peer=self.handlers['find_peer'],
                disconnect=self.handlers['disconnect'],
                drop_dynamic=self.handlers['drop_dynamic'],
                activate_tg=self.handlers['activate_tg'],
                deactivate_tg=self.handlers['deactivate_tg'],
            )
            self.transport.write(_http_response(code, payload))
            self.transport.loseConnection()

    class ControlFactory(Factory):
        def __init__(self, inner_token: str, handlers: dict):
            self.inner_token = inner_token
            self.handlers = handlers

        def buildProtocol(self, addr):
            return ControlProtocol(self.inner_token, self.handlers)

    handlers = _wire_handlers()
    port = reactor.listenTCP(
        LISTEN_PORT,
        ControlFactory(token, handlers),
        interface=LISTEN_HOST,
    )
    if logger:
        logger.info('(CONTROL) loopback listener on %s:%s', LISTEN_HOST, LISTEN_PORT)
    return port
