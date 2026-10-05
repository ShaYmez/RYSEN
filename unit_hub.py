#!/usr/bin/env python3
"""Off-reactor client and health reporting for topology-aware unit routing."""

import json
import random
from time import time
from urllib import error, request
from urllib.parse import urlencode


HEALTHY_AFTER_SECONDS = 60.0
HEALTH_REPORT_MIN_SECONDS = 45.0
HEALTH_REPORT_MAX_SECONDS = 75.0


def _as_net_id(value):
    if isinstance(value, dict):
        value = value.get('opb_net_id', value.get('net_id', value.get('id')))
    if isinstance(value, (bytes, bytearray)):
        value = int.from_bytes(value, 'big')
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if 1000 <= value <= 9999999 else None


def _read_token(config):
    aliases = config.get('ALIASES') or {}
    path = str(aliases.get('UNIT_SUB_MAP_TOKEN_FILE') or '').strip()
    if not path:
        return None
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def server_registry_net_ids(config):
    """Net IDs from the downloaded SystemX server alias registry."""
    registry = config.get('_SERVER_IDS') or {}
    values = registry.keys() if isinstance(registry, dict) else registry
    found = set()
    for value in values:
        parsed = _as_net_id(value)
        if parsed is not None:
            found.add(parsed)
    return frozenset(found)


def enhanced_obp_health_snapshot(config, now=None):
    """Return a secret-free snapshot of configured, fleet-only OBP links."""
    now = time() if now is None else float(now)
    fleet = server_registry_net_ids(config)
    peers = []
    for name, item in (config.get('SYSTEMS') or {}).items():
        net_id = _as_net_id(item.get('NETWORK_ID'))
        if (not item.get('ENABLED', True)
                or item.get('MODE') != 'OPENBRIDGE'
                or not item.get('ENHANCED_OBP')
                or int(item.get('VER', 0) or 0) < 5
                or net_id not in fleet):
            continue
        last = item.get('_bcka')
        age = None
        if last is not None:
            try:
                age = max(0.0, now - float(last))
            except (TypeError, ValueError):
                age = None
        peers.append({
            'opb_net_id': net_id,
            'enabled': True,
            'enhanced': True,
            'healthy': age is not None and age <= HEALTHY_AFTER_SECONDS,
        })
    peers.sort(key=lambda row: row['opb_net_id'])
    master = _as_net_id((config.get('GLOBAL') or {}).get('SERVER_ID'))
    if master not in fleet:
        master = None
    return {
        'opb_net_id': master,
        'reported_at': int(now),
        'peers': peers,
    }


def topology_health_url(config):
    aliases = config.get('ALIASES') or {}
    base = str(aliases.get('UNIT_SUB_MAP_URL') or '').strip().rstrip('/')
    suffix = '/sub-map'
    if base.endswith(suffix):
        return base[:-len(suffix)] + '/topology/report'
    return base + '/topology/report' if base else ''


def unit_lookup_url(config, radio_id, current_master=None):
    """Build the contracted subscriber-route URL, including this master."""
    aliases = config.get('ALIASES') or {}
    base = str(aliases.get('UNIT_SUB_MAP_URL') or '').strip().rstrip('/')
    if not base:
        return ''
    query = {}
    current = _as_net_id(current_master)
    if current is not None:
        query['from_net_id'] = current
    url = '%s/%s' % (base, int(radio_id))
    return url + ('?' + urlencode(query) if query else '')


def post_health_snapshot(config, opener=None, now=None):
    """POST one health snapshot. Tokens are header-only and never returned."""
    url = topology_health_url(config)
    token = _read_token(config)
    if not url or not token:
        return {'disabled': True}
    snapshot = enhanced_obp_health_snapshot(config, now=now)
    if snapshot.get('opb_net_id') is None:
        return {'disabled': True}
    body = json.dumps(snapshot, separators=(',', ':')).encode('utf-8')
    req = request.Request(
        url, data=body, method='POST',
        headers={
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        })
    try:
        open_url = opener or request.urlopen
        with open_url(req, timeout=2.0) as response:
            response.read()
        return {'ok': True}
    except Exception:
        return {'error': True}


def validate_topology_route(payload, current_master, fleet_net_ids,
                            configured_next_hops=None, ingress_master=None,
                            now=None):
    """Validate a hub route and return normalized topology metadata.

    A path must be simple, fleet-only, start at this master, end at the home,
    and name the immediate next hop. Legacy ``opb_net_id`` responses remain
    valid as direct one-hop routes.
    """
    if not isinstance(payload, dict) or payload.get('error') or payload.get('miss'):
        return None
    current = _as_net_id(current_master)
    response_current = _as_net_id(payload.get('current_master'))
    raw_path = payload.get('path')
    path_home = raw_path[-1] if isinstance(raw_path, (list, tuple)) and raw_path else None
    home = _as_net_id(payload.get(
        'home_net_id', payload.get('home', payload.get('opb_net_id', path_home))))
    if current is None or home is None:
        return None
    if response_current is not None and response_current != current:
        return None
    fleet = set()
    for value in fleet_net_ids or ():
        parsed = _as_net_id(value)
        if parsed is not None:
            fleet.add(parsed)
    configured = set()
    for value in configured_next_hops or ():
        parsed = _as_net_id(value)
        if parsed is not None:
            configured.add(parsed)
    if current not in fleet or home not in fleet:
        return None
    if raw_path is None:
        path = [current] if home == current else [current, home]
    elif not isinstance(raw_path, (list, tuple)) or not raw_path:
        return None
    else:
        path = [_as_net_id(value) for value in raw_path]
        if any(value is None for value in path):
            return None
    if (path[0] != current or path[-1] != home
            or len(path) != len(set(path)) or len(path) - 1 > 10):
        return None
    if any(value not in fleet for value in path):
        return None
    next_hop = _as_net_id(payload.get(
        'next_hop_net_id', payload.get('next_hop')))
    expected = path[1] if len(path) > 1 else None
    if next_hop is None:
        next_hop = expected
    if next_hop != expected:
        return None
    if next_hop is not None and next_hop not in configured:
        return None
    ingress = _as_net_id(ingress_master)
    if next_hop is not None and next_hop == ingress:
        return None
    expires = payload.get(
        'route_expires_at', payload.get('expires_at', payload.get('expiry')))
    if expires is not None:
        try:
            expires = float(expires)
        except (TypeError, ValueError):
            return None
        if expires <= (time() if now is None else float(now)):
            return None
    return {
        'home': home,
        'current_master': current,
        'next_hop': next_hop,
        'path': tuple(path),
        'topology_version': payload.get('topology_version'),
        'expires_at': expires,
    }


def schedule_off_reactor(func, args, callback, errback):
    """Run func(*args) off the Twisted reactor and deliver the result back on it."""
    from twisted.internet import reactor
    from twisted.internet.threads import deferToThread
    deferred = deferToThread(func, *args)

    def _ok(result):
        reactor.callFromThread(callback, result)

    def _bad(failure):
        reactor.callFromThread(errback, failure)

    deferred.addCallbacks(_ok, _bad)
    return deferred


def start_health_reporter(config, logger, reactor=None, random_fn=None):
    """Report immediately, then at bounded jittered intervals off-reactor."""
    aliases = config.get('ALIASES') or {}
    if (not str(aliases.get('UNIT_SUB_MAP_URL') or '').strip()
            or not str(aliases.get('UNIT_SUB_MAP_TOKEN_FILE') or '').strip()):
        return None
    if reactor is None:
        from twisted.internet import reactor
    random_fn = random_fn or random.uniform
    state = {'stopped': False, 'call': None}

    def schedule_next():
        if state['stopped']:
            return
        delay = max(
            HEALTH_REPORT_MIN_SECONDS,
            min(HEALTH_REPORT_MAX_SECONDS, float(random_fn(
                HEALTH_REPORT_MIN_SECONDS, HEALTH_REPORT_MAX_SECONDS))))
        state['call'] = reactor.callLater(delay, run)

    def done(result):
        if result and result.get('error'):
            logger.warning('(UNIT TOPOLOGY) health report failed')
        schedule_next()

    def failed(_failure):
        logger.warning('(UNIT TOPOLOGY) health report failed')
        schedule_next()

    def run():
        schedule_off_reactor(
            post_health_snapshot, (config,), done, failed)

    def stop():
        state['stopped'] = True
        call = state.get('call')
        if call is not None and call.active():
            call.cancel()

    state['stop'] = stop
    reactor.callLater(0, run)
    return state
