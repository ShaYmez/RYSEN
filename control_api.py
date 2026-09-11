#!/usr/bin/env python3
"""Loopback HTTP control listener for FreeSTAR ops and owner device APIs.

Listens when /etc/rysen/freestar-control.token exists.
Legacy root actions are ops-only. Owner actions are under /device/*.
"""

from __future__ import annotations

import json
import hmac
import logging
import os
import sys
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

TOKEN_PATH = os.environ.get('FREESTAR_CONTROL_TOKEN', '/etc/rysen/freestar-control.token')
LISTEN_HOST = os.environ.get('FREESTAR_CONTROL_HOST', '127.0.0.1')
LISTEN_PORT = int(os.environ.get('FREESTAR_CONTROL_PORT', '8765'))
MAX_BODY = 65536


def load_token(path: str = TOKEN_PATH) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    text = open(path, encoding='utf-8').read().strip()
    return text or None


def parse_control_path(path: str) -> Tuple[str, List[str]]:
    parts = [p for p in (path or '').split('?')[0].strip('/').split('/') if p]
    if not parts:
        return '', []
    if parts[0] == 'device':
        if len(parts) < 2:
            return '', []
        return 'device:' + parts[1], parts[2:]
    return parts[0], parts[1:]


def slot_from_body(body: dict) -> int:
    """UA talkgroup slot. 0 or omitted means slot 2 (ops compatibility)."""
    try:
        slot = int(body.get('slot') or 0)
    except (TypeError, ValueError):
        slot = 0
    return 2 if slot in (0, None) else slot


def static_slot(value) -> int:
    """Device/static slot. 0 (simplex) and omitted map to TS2. 1 and 2 stay."""
    try:
        slot = int(value)
    except (TypeError, ValueError):
        slot = 0
    return 1 if slot == 1 else 2


def _radio_from(body: dict, path_parts: List[str]) -> Optional[int]:
    raw = body.get('radio_id')
    if raw in (None, '') and path_parts:
        raw = path_parts[0]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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
    drop_call: Optional[Callable] = None,
    list_peer: Optional[Callable] = None,
    add_static: Optional[Callable] = None,
    remove_static: Optional[Callable] = None,
    persist_disc: Optional[Callable] = None,
    kick_peer: Optional[Callable] = None,
    ban_radio: Optional[Callable] = None,
    unban_radio: Optional[Callable] = None,
    path_parts: Optional[List[str]] = None,
) -> Tuple[int, dict]:
    path_parts = list(path_parts or [])
    method = (method or 'POST').upper()
    action = (action or '').strip().strip('/')
    scope = 'ops'
    if action.startswith('device:'):
        scope = 'device'
        action = action.split(':', 1)[1]

    ops_actions = {
        'disconnect', 'drop-call', 'drop-dynamic', 'talkgroup',
        'kick', 'ban', 'unban',
    }
    device_actions = {
        'peer', 'static-talkgroup', 'drop-call', 'drop-dynamic',
        'disconnect',
    }
    if action not in (device_actions if scope == 'device' else ops_actions):
        return 404, {'error': 'Unknown control action'}

    radio_id = _radio_from(body, path_parts if action == 'peer' else [])
    if radio_id is None:
        radio_id = _radio_from(body, [])
    if radio_id is None:
        return 400, {'error': 'radio_id is required'}

    if scope == 'ops' and action == 'unban':
        if method != 'POST' or unban_radio is None:
            return 405, {'error': 'Method not allowed'}
        removed = bool(unban_radio(radio_id))
        return 200, {
            'ok': True, 'action': 'unban', 'radio_id': radio_id,
            'removed': removed,
        }

    if scope == 'ops' and action == 'ban':
        if method != 'POST' or ban_radio is None:
            return 405, {'error': 'Method not allowed'}
        try:
            duration = int(body.get('duration_seconds', 300))
        except (TypeError, ValueError):
            return 400, {'error': 'duration_seconds must be an integer'}
        if duration < 1 or duration > 86400:
            return 400, {'error': 'duration_seconds must be between 1 and 86400'}
        result = ban_radio(radio_id, duration, body.get('reason', ''))
        payload = {
            'ok': True, 'action': 'ban', 'radio_id': radio_id,
            'duration_seconds': duration,
        }
        if isinstance(result, dict):
            payload.update(result)
        return 200, payload

    try:
        system, peer_id = find_peer(radio_id)
    except LookupError as err:
        return 409, {'error': str(err)}
    if not system:
        return 404, {'error': 'Peer not connected on this master'}

    if scope == 'device' and action == 'peer' and method == 'GET':
        if list_peer is None:
            return 404, {'error': 'Unknown control action'}
        payload = list_peer(system, peer_id, radio_id)
        if not isinstance(payload, dict):
            payload = {'ok': True, 'system': system}
        payload.setdefault('ok', True)
        payload.setdefault('connected', True)
        payload.setdefault('system', system)
        payload.setdefault('radio_id', radio_id)
        return 200, payload

    if action == 'disconnect' and method == 'POST':
        disconnect(system, peer_id)
        if scope == 'device' and persist_disc is not None:
            persist_disc(system, peer_id)
        return 200, {
            'ok': True, 'action': action, 'scope': scope, 'system': system,
        }

    if action == 'drop-call' and method == 'POST':
        if drop_call is not None:
            drop_call(system, peer_id)
        return 200, {
            'ok': True, 'action': action, 'scope': scope, 'system': system,
        }

    if action == 'drop-dynamic' and method == 'POST':
        drop_dynamic(system, peer_id)
        return 200, {
            'ok': True, 'action': action, 'scope': scope, 'system': system,
        }

    if scope == 'ops' and action == 'kick' and method == 'POST':
        if kick_peer is None:
            return 404, {'error': 'Kick is not available'}
        kicked = bool(kick_peer(system, peer_id))
        return 200, {
            'ok': True, 'action': action, 'system': system, 'kicked': kicked,
        }

    if scope == 'ops' and action == 'talkgroup':
        tg = body.get('talkgroup', body.get('group'))
        try:
            tgid = int(tg)
        except (TypeError, ValueError):
            return 400, {'error': 'talkgroup is required'}
        slot = slot_from_body(body)
        if method == 'DELETE':
            deactivate_tg(system, tgid, slot, peer_id)
            return 200, {'ok': True, 'action': 'talkgroup-remove', 'system': system, 'talkgroup': tgid}
        if method != 'POST':
            return 405, {'error': 'Method not allowed'}
        activate_tg(system, tgid, slot, peer_id)
        return 200, {'ok': True, 'action': 'talkgroup-add', 'system': system, 'talkgroup': tgid}

    if scope == 'device' and action == 'static-talkgroup':
        if add_static is None or remove_static is None:
            return 404, {'error': 'Unknown control action'}
        slot_raw = body.get('slot')
        group_raw = body.get('talkgroup', body.get('group'))
        if method == 'DELETE' and len(path_parts) >= 2:
            slot_raw = path_parts[-2]
            group_raw = path_parts[-1]
        try:
            tgid = int(group_raw)
        except (TypeError, ValueError):
            return 400, {'error': 'talkgroup is required'}
        slot = static_slot(slot_raw)
        if method == 'DELETE':
            remove_static(system, tgid, slot, peer_id)
            return 200, {
                'ok': True,
                'action': 'static-talkgroup-remove',
                'system': system,
                'talkgroup': tgid,
                'slot': slot,
            }
        if method != 'POST':
            return 405, {'error': 'Method not allowed'}
        add_static(system, tgid, slot, peer_id)
        return 200, {
            'ok': True,
            'action': 'static-talkgroup-add',
            'system': system,
            'talkgroup': tgid,
            'slot': slot,
        }

    return 405, {'error': 'Method not allowed'}


def _http_response(code: int, body: dict) -> bytes:
    payload = json.dumps(body, separators=(',', ':')).encode('utf-8')
    reason = {
        200: 'OK',
        400: 'Bad Request',
        401: 'Unauthorized',
        404: 'Not Found',
        409: 'Conflict',
        405: 'Method Not Allowed',
        500: 'Internal Server Error',
    }.get(code, 'Error')
    headers = (
        f'HTTP/1.1 {code} {reason}\r\n'
        'Content-Type: application/json\r\n'
        f'Content-Length: {len(payload)}\r\n'
        'Connection: close\r\n'
        '\r\n'
    )
    return headers.encode('ascii') + payload


def _runtime_bridge():
    """Use the running process module. bridge_master.py is started as __main__,
    so `import bridge_master` is a second copy without CONFIG/BRIDGES."""
    main = sys.modules.get('__main__')
    if main is not None and getattr(main, 'CONFIG', None) is not None:
        return main
    import bridge_master as bm
    return bm


def _selfcare_db(bm):
    db = getattr(bm, '_selfcare_db', None)
    if db is not None:
        return db
    cfg = getattr(bm, 'CONFIG', None) or {}
    return cfg.get('_SELF_SERVICE_DB')


def _peer_int(peer_id) -> Optional[int]:
    if peer_id is None:
        return None
    try:
        from dmr_utils3.utils import int_id
        return int(int_id(peer_id))
    except Exception:
        try:
            return int(peer_id)
        except (TypeError, ValueError):
            return None


def _queue_options(bm, peer_id, options_str: str) -> None:
    db = _selfcare_db(bm)
    rid = _peer_int(peer_id)
    if db is None or rid is None or not hasattr(db, 'queue_client_options'):
        return
    try:
        deferred = db.queue_client_options(rid, options_str)
        if deferred is not None and hasattr(deferred, 'addErrback'):
            deferred.addErrback(lambda err: log.warning('(CONTROL) Clients persist failed: %s', err))
    except Exception as err:
        log.warning('(CONTROL) Clients persist failed: %s', err)


def _wire_handlers():
    bm = _runtime_bridge()
    from dmr_utils3.utils import bytes_3
    from bridge_helpers import (
        linked_ipsc_slots,
        forget_peer_dynamic_tg,
        mark_options_dirty,
        peer_dynamic_groups,
        unmute_peer_tg,
    )
    from selfcare_db import (
        find_connected_dmr_peer,
        live_static_required,
        merge_ts_into_options,
        peer_own_options,
        store_peer_options,
        ts_lists_from_options,
    )

    def find_peer(radio_id: int):
        return find_connected_dmr_peer(
            bm.CONFIG['SYSTEMS'], radio_id, require_unique=True)

    def disconnect(system, peer_id):
        bm.selfcare_disconnect(system, peer_id)

    def drop_call(system, peer_id):
        bm.drop_peer_call(system, peer_id)

    def drop_dynamic(system, peer_id=None):
        bm.selfcare_disconnect(system, peer_id)

    def persist_disc(system, peer_id):
        # DISC=1 must land on the existing Clients row. Rewriting TS1/TS2 from
        # the MASTER stanza would copy another hotspot's statics onto this device.
        db = _selfcare_db(bm)
        rid = _peer_int(peer_id)
        if db is None or rid is None or not hasattr(db, 'queue_client_disc'):
            return
        try:
            deferred = db.queue_client_disc(rid)
            if deferred is not None and hasattr(deferred, 'addErrback'):
                deferred.addErrback(lambda err: log.warning('(CONTROL) Clients DISC persist failed: %s', err))
        except Exception as err:
            log.warning('(CONTROL) Clients DISC persist failed: %s', err)

    def _options_base(system, peer_id):
        cfg = bm.CONFIG['SYSTEMS'].get(system, {})
        return peer_own_options(cfg, peer_id)

    def _write_peer_options(system, peer_id, options):
        cfg = bm.CONFIG['SYSTEMS'].get(system, {})
        if store_peer_options(cfg, peer_id, options):
            mark_options_dirty(bm.CONFIG)
            _queue_options(bm, peer_id, options)
            return
        log.warning('(CONTROL) skip OPTIONS write: peer %s not in PEERS', _peer_int(peer_id))

    def _persist_statics(system, peer_id, ts1, ts2):
        options = merge_ts_into_options(_options_base(system, peer_id), ts1, ts2)
        _write_peer_options(system, peer_id, options)

    def add_static(system, tgid, slot, peer_id):
        cfg = bm.CONFIG['SYSTEMS'][system]
        ts1, ts2 = ts_lists_from_options(_options_base(system, peer_id))
        groups = ts1 if slot == 1 else ts2
        name = str(int(tgid))
        if name not in groups:
            groups.append(name)
        if slot == 1:
            ts1 = groups
        else:
            ts2 = groups
        tmout = cfg.get('DEFAULT_UA_TIMER', 10)
        bm.make_static_tg(int(tgid), slot, tmout, system)
        forget_peer_dynamic_tg(system, peer_id, slot, tgid)
        unmute_peer_tg(system, peer_id, slot, tgid)
        bm.notify_bridge_table_updated()
        _persist_statics(system, peer_id, ts1, ts2)

    def remove_static(system, tgid, slot, peer_id):
        cfg = bm.CONFIG['SYSTEMS'][system]
        ts1, ts2 = ts_lists_from_options(_options_base(system, peer_id))
        name = str(int(tgid))
        groups = ts1 if slot == 1 else ts2
        if name not in groups:
            return False
        if slot == 1:
            ts1 = [g for g in ts1 if g != name]
        else:
            ts2 = [g for g in ts2 if g != name]
        tmout = cfg.get('DEFAULT_UA_TIMER', 10)
        if not live_static_required(
                cfg, slot, tgid, except_peer_id=peer_id):
            bm.reset_static_tg(int(tgid), slot, tmout, system)
        bm.notify_bridge_table_updated()
        _persist_statics(system, peer_id, ts1, ts2)
        return True

    def list_peer(system, peer_id, radio_id):
        ts1, ts2 = ts_lists_from_options(_options_base(system, peer_id))
        statics = []
        for tg in ts1:
            statics.append({'slot': 1, 'group': int(tg)})
        for tg in ts2:
            statics.append({'slot': 2, 'group': int(tg)})
        static_keys = {(row['slot'], row['group']) for row in statics}
        dynamics = []
        for row in peer_dynamic_groups(
                getattr(bm, 'SUB_MAP', None) or {},
                getattr(bm, 'BRIDGES', None) or {},
                system,
                peer_id):
            key = (row['slot'], row['group'])
            if key in static_keys:
                continue
            dynamics.append(row)
        connected_id = _peer_int(peer_id)
        return {
            'ok': True,
            'connected': True,
            'radio_id': radio_id,
            'connected_radio_id': connected_id if connected_id is not None else radio_id,
            'system': system,
            'statics': statics,
            'dynamics': dynamics,
        }

    def activate_tg(system, tgid, slot, peer_id):
        name = str(int(tgid))
        tgid_b = bytes_3(int(tgid))
        tmout = bm.CONFIG['SYSTEMS'].get(system, {}).get('DEFAULT_UA_TIMER', 10)
        if name not in bm.BRIDGES:
            bm.make_single_bridge(tgid_b, system, slot, tmout)
        else:
            # Ops activation is stanza-scoped; the radio only locates its host.
            bm.activate_ua_bridge_source(name, system, slot, tmout, None)
        bm.notify_bridge_table_updated()

    def deactivate_tg(system, tgid, slot, peer_id=None):
        name = str(int(tgid))
        if name not in bm.BRIDGES:
            return
        changed = False
        target_systems = {system}
        target_systems.update(linked_ipsc_slots(
            bm.CONFIG.get('SYSTEMS', {}), system, None))
        for entry in bm.BRIDGES[name]:
            if entry.get('SYSTEM') not in target_systems:
                continue
            if slot and entry.get('TS') != slot:
                continue
            # Static OFF legs remain governed by OPTIONS. Ops DELETE force-drops
            # the shared user-activated leg; it does not corrupt static state.
            if entry.get('TO_TYPE') == 'ON' and entry.get('ACTIVE'):
                entry['ACTIVE'] = False
                entry['TIMER'] = 0
                changed = True
        if changed:
            bm.rebuild_bridge_index()
            bm.notify_bridge_table_updated()

    def kick_peer(system, peer_id):
        return bm.kick_hotspot_peer(system, peer_id)

    def ban_radio(radio_id, duration_seconds, reason):
        return bm.ban_hotspot_radio(
            radio_id, duration_seconds=duration_seconds, reason=reason)

    def unban_radio(radio_id):
        return bm.unban_hotspot_radio(radio_id)

    return {
        'find_peer': find_peer,
        'disconnect': disconnect,
        'drop_dynamic': drop_dynamic,
        'activate_tg': activate_tg,
        'deactivate_tg': deactivate_tg,
        'drop_call': drop_call,
        'list_peer': list_peer,
        'add_static': add_static,
        'remove_static': remove_static,
        'persist_disc': persist_disc,
        'kick_peer': kick_peer,
        'ban_radio': ban_radio,
        'unban_radio': unban_radio,
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
            if length < 0 or length > MAX_BODY:
                self.transport.write(_http_response(400, {'error': 'Request too large'}))
                self.transport.loseConnection()
                return
            if len(rest) < length:
                return
            auth = headers.get('authorization', '')
            if not hmac.compare_digest(auth, 'Bearer ' + self.token):
                self.transport.write(_http_response(401, {'error': 'Unauthorized'}))
                self.transport.loseConnection()
                return
            if method not in ('GET', 'POST', 'DELETE'):
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
            action, path_parts = parse_control_path(path)
            try:
                code, payload = handle_control_request(
                    action,
                    method,
                    body,
                    find_peer=self.handlers['find_peer'],
                    disconnect=self.handlers['disconnect'],
                    drop_dynamic=self.handlers['drop_dynamic'],
                    activate_tg=self.handlers['activate_tg'],
                    deactivate_tg=self.handlers['deactivate_tg'],
                    drop_call=self.handlers.get('drop_call'),
                    list_peer=self.handlers.get('list_peer'),
                    add_static=self.handlers.get('add_static'),
                    remove_static=self.handlers.get('remove_static'),
                    persist_disc=self.handlers.get('persist_disc'),
                    kick_peer=self.handlers.get('kick_peer'),
                    ban_radio=self.handlers.get('ban_radio'),
                    unban_radio=self.handlers.get('unban_radio'),
                    path_parts=path_parts,
                )
            except Exception as err:
                log.exception('(CONTROL) request failed: %s', err)
                code, payload = 500, {'error': 'Control handler failed'}
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
