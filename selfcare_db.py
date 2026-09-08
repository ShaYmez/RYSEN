#!/usr/bin/env python3
###############################################################################
#   IPSC repeater selfcare — Clients table access (mode = 0)
#   Pattern mirrors proxy/proxy_db.py; no schema migration required.
#
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

from twisted.enterprise import adbapi
from twisted.internet.defer import inlineCallbacks

from dmr_utils3.utils import int_id

IPSC_CLIENT_MODE = 0


def _peer_radio_id_str(radio_id_value):
    if radio_id_value is None:
        return None
    try:
        return str(int_id(radio_id_value))
    except (TypeError, ValueError):
        return str(radio_id_value)


def radio_id_core(radio_id):
    """7-digit DMR identity. 9-digit ESSID (01-99) strips to the first 7 (234018901 -> 2340189)."""
    if radio_id is None:
        return ''
    try:
        digits = str(int(radio_id))
    except (TypeError, ValueError):
        digits = ''.join(ch for ch in str(radio_id) if ch.isdigit())
    if len(digits) == 9:
        return digits[:7]
    return digits


def radio_ids_match(left, right):
    core_left = radio_id_core(left)
    core_right = radio_id_core(right)
    return bool(core_left) and core_left == core_right


def _radio_lookup_strings(peer, peer_id):
    radio_str = _peer_radio_id_str(peer.get('RADIO_ID'))
    try:
        pid_str = str(int_id(peer_id))
    except (TypeError, ValueError):
        pid_str = None
    return radio_str, pid_str


def _peer_matches_radio(peer, peer_id, radio_id, exact_only=False):
    target = str(radio_id)
    radio_str, pid_str = _radio_lookup_strings(peer, peer_id)
    if radio_str == target or pid_str == target:
        return True
    if exact_only:
        return False
    if radio_str and radio_ids_match(radio_str, radio_id):
        return True
    if pid_str and radio_ids_match(pid_str, radio_id):
        return True
    return False


class SelfcareDB:
    """MariaDB access for IPSC repeater rows in Clients (mode = 0)."""

    def __init__(self, host, user, password, db_name, port):
        self.db_name = db_name
        self.dbpool = adbapi.ConnectionPool(
            "MySQLdb", host, user, password, db_name,
            port=port, charset="utf8mb4",
        )

    @inlineCallbacks
    def test_db(self, reactor, logger):
        try:
            res = yield self.dbpool.runQuery("SELECT 1")
            if res:
                logger.info('(SELF SERVICE) Database connection test: OK')
        except Exception as err:
            if reactor.running:
                logger.error('(SELF SERVICE) Database connection error: %s', err)
                reactor.stop()
            else:
                raise SystemExit(f'(SELF SERVICE) Database connection error: {err}')

    @inlineCallbacks
    def upsert_ipsc_client(self, int_id, dmr_id, callsign, host, seed_options=None):
        """Register or refresh IPSC repeater row; preserve psswd/options on re-register."""
        flag_modified = 1 if seed_options else 0
        try:
            yield self.dbpool.runOperation(
                '''INSERT INTO Clients (
                    int_id, dmr_id, callsign, host, mode, logged_in, last_seen, options, modified
                ) VALUES (%s, %s, %s, %s, %s, 1, UNIX_TIMESTAMP(), %s, %s)
                ON DUPLICATE KEY UPDATE
                    callsign = VALUES(callsign),
                    host = VALUES(host),
                    mode = %s,
                    logged_in = 1,
                    last_seen = UNIX_TIMESTAMP()''',
                (int_id, dmr_id, callsign, host, IPSC_CLIENT_MODE,
                 seed_options, flag_modified, IPSC_CLIENT_MODE),
            )
        except Exception as err:
            raise RuntimeError(f'upsert_ipsc_client error: {err}') from err

    @inlineCallbacks
    def mark_ipsc_options_pending(self, int_id):
        """Re-queue stored selfcare options for apply after reconnect (parity with hotspot login_opt)."""
        try:
            yield self.dbpool.runOperation(
                'UPDATE Clients SET modified = 1 '
                'WHERE int_id = %s AND mode = %s '
                "AND options IS NOT NULL AND TRIM(options) != '' "
                "AND options NOT LIKE '%%DISC=1%%'",
                (int_id, IPSC_CLIENT_MODE),
            )
        except Exception as err:
            raise RuntimeError(f'mark_ipsc_options_pending error: {err}') from err

    @inlineCallbacks
    def save_client_options(self, int_id, options_str):
        """Persist stripped selfcare options after one-shot DISC apply."""
        try:
            yield self.dbpool.runOperation(
                'UPDATE Clients SET options = %s WHERE int_id = %s',
                (options_str, int_id),
            )
        except Exception as err:
            raise RuntimeError(f'save_client_options error: {err}') from err

    @inlineCallbacks
    def logout_ipsc_client(self, int_id):
        try:
            yield self.dbpool.runOperation(
                'UPDATE Clients SET logged_in = 0 WHERE int_id = %s AND mode = %s',
                (int_id, IPSC_CLIENT_MODE),
            )
        except Exception as err:
            raise RuntimeError(f'logout_ipsc_client error: {err}') from err

    def select_modified_ipsc(self):
        return self.dbpool.runQuery(
            'SELECT int_id, options FROM Clients WHERE modified = 1 AND mode = %s',
            (IPSC_CLIENT_MODE,),
        )

    @inlineCallbacks
    def touch_ipsc_seen(self, int_id, host=None):
        """Keep IPSC Clients row marked online while repeater is connected."""
        try:
            if host:
                yield self.dbpool.runOperation(
                    'UPDATE Clients SET logged_in = 1, last_seen = UNIX_TIMESTAMP(), '
                    'host = %s WHERE int_id = %s AND mode = %s',
                    (host, int_id, IPSC_CLIENT_MODE),
                )
            else:
                yield self.dbpool.runOperation(
                    'UPDATE Clients SET logged_in = 1, last_seen = UNIX_TIMESTAMP() '
                    'WHERE int_id = %s AND mode = %s',
                    (int_id, IPSC_CLIENT_MODE),
                )
        except Exception as err:
            raise RuntimeError(f'touch_ipsc_seen error: {err}') from err

    def select_hotspot_disc_pending(self):
        """Hotspot rows waiting for DISC=1 disconnect (selfcare button)."""
        return self.dbpool.runQuery(
            'SELECT int_id, options FROM Clients '
            'WHERE modified = 1 AND logged_in = 1 AND mode > 0 '
            "AND options LIKE '%%DISC=1%%'",
        )

    @inlineCallbacks
    def clear_modified(self, int_id):
        try:
            yield self.dbpool.runOperation(
                'UPDATE Clients SET modified = 0 WHERE int_id = %s AND mode = %s',
                (int_id, IPSC_CLIENT_MODE),
            )
        except Exception as err:
            raise RuntimeError(f'clear_modified error: {err}') from err

    @inlineCallbacks
    def clear_modified_client(self, int_id):
        try:
            yield self.dbpool.runOperation(
                'UPDATE Clients SET modified = 0 WHERE int_id = %s',
                (int_id,),
            )
        except Exception as err:
            raise RuntimeError(f'clear_modified_client error: {err}') from err

    def queue_client_options(self, int_id, options_str):
        """Persist TS1=/TS2= (or DISC=1) like PHP updateDevOptions. Fire-and-forget."""
        return self.dbpool.runOperation(
            'UPDATE Clients SET options = %s, modified = 1 WHERE int_id = %s',
            (options_str, int_id),
        )

    def queue_client_disc(self, int_id):
        """Set DISC=1 on the existing Clients.options row without replacing TS1/TS2."""
        return self.dbpool.runOperation(
            "UPDATE Clients SET options = CASE "
            "WHEN options IS NULL OR TRIM(options) = '' THEN 'DISC=1;' "
            "WHEN options LIKE '%%DISC=1%%' THEN options "
            "ELSE CONCAT(TRIM(TRAILING ';' FROM options), ';DISC=1;') END, "
            "modified = 1 WHERE int_id = %s",
            (int_id,),
        )


def build_ipsc_seed_options(system_cfg):
    """Build TS1=/TS2= options string from cfg static TG fields (first register)."""
    if not system_cfg:
        return None
    parts = []
    ts1 = system_cfg.get('TS1_STATIC') or ''
    ts2 = system_cfg.get('TS2_STATIC') or ''
    if ts1:
        parts.append(f'TS1={ts1}')
    if ts2:
        parts.append(f'TS2={ts2}')
    if not parts:
        return None
    return ';'.join(parts) + ';'


def find_ipsc_slot_for_radio_id(config_systems, radio_id):
    """Return IPSC-N slot name where connected peer RADIO_ID matches radio_id."""
    slot, _peer_id = find_ipsc_peer_for_radio_id(config_systems, radio_id)
    return slot


class AmbiguousPeerError(LookupError):
    """A seven-digit owner ID matched more than one connected ESSID."""


def find_connected_dmr_peer(config_systems, radio_id, require_unique=False):
    """Find one connected MASTER/IPSC peer, rejecting ambiguous control targets."""
    exact = []
    fuzzy = []
    for system, syscfg in config_systems.items():
        mode = syscfg.get('MODE')
        if mode not in ('MASTER', 'IPSC') or not syscfg.get('ENABLED'):
            continue
        for peer_id, peer in (syscfg.get('PEERS') or {}).items():
            connection = peer.get('CONNECTION')
            if mode == 'MASTER' and connection != 'YES':
                continue
            if mode == 'IPSC' and connection not in (None, 'YES'):
                continue
            candidate = (system, peer_id)
            if _peer_matches_radio(peer, peer_id, radio_id, exact_only=True):
                exact.append(candidate)
            elif _peer_matches_radio(peer, peer_id, radio_id):
                fuzzy.append(candidate)
    matches = exact or fuzzy
    if require_unique and len(matches) > 1:
        raise AmbiguousPeerError(
            'Multiple connected peers match; use the exact connected radio ID')
    return matches[0] if matches else (None, None)


def find_ipsc_peer_for_radio_id(
        config_systems, radio_id, require_unique=False):
    """Return (IPSC-N slot, peer_id) for a connected IPSC repeater radio ID."""
    fuzzy = []
    for slot, syscfg in config_systems.items():
        if syscfg.get('MODE') != 'IPSC' or not syscfg.get('ENABLED'):
            continue
        for peer_id, peer in syscfg.get('PEERS', {}).items():
            if peer.get('CONNECTION') not in (None, 'YES'):
                continue
            if _peer_matches_radio(peer, peer_id, radio_id, exact_only=True):
                return slot, peer_id
            if _peer_matches_radio(peer, peer_id, radio_id):
                fuzzy.append((slot, peer_id))
    if require_unique and len(fuzzy) > 1:
        raise AmbiguousPeerError(
            'Multiple connected ESSIDs match; use the exact connected radio ID')
    return fuzzy[0] if fuzzy else (None, None)


def find_hotspot_master_peer(
        config_systems, radio_id, require_unique=False):
    """Return (MASTER system name, peer_id) for a logged-in hotspot radio ID.

    Exact ID wins. ESSID 01-99 is a fallback (234018901 matches 2340189).
    """
    fuzzy = []
    for system, syscfg in config_systems.items():
        if syscfg.get('MODE') != 'MASTER' or not syscfg.get('ENABLED'):
            continue
        for peer_id, peer in (syscfg.get('PEERS') or {}).items():
            if peer.get('CONNECTION') != 'YES':
                continue
            if _peer_matches_radio(peer, peer_id, radio_id, exact_only=True):
                return system, peer_id
            if _peer_matches_radio(peer, peer_id, radio_id):
                fuzzy.append((system, peer_id))
    if require_unique and len(fuzzy) > 1:
        raise AmbiguousPeerError(
            'Multiple connected ESSIDs match; use the exact connected radio ID')
    return fuzzy[0] if fuzzy else (None, None)


def comma_tg_list(value):
    if not value or value is False:
        return []
    if isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = str(value).split(',')
    out = []
    for part in parts:
        part = str(part).strip()
        if part.isdigit():
            out.append(part)
    return out


def ts_lists_from_options(options_value):
    """TS1=/TS2= (or TS1_STATIC/TS2_STATIC) lists from one hotspot's OPTIONS string."""
    ts1, ts2 = [], []
    if not options_value:
        return ts1, ts2
    text = options_value.decode('utf-8', errors='ignore') if isinstance(options_value, bytes) else str(options_value)
    for part in text.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        key, value = part.split('=', 1)
        key_u = key.strip().upper()
        if key_u in ('TS1', 'TS1_STATIC'):
            ts1 = comma_tg_list(value)
        elif key_u in ('TS2', 'TS2_STATIC'):
            ts2 = comma_tg_list(value)
    return ts1, ts2


def peer_own_options(syscfg, peer_id):
    """This peer's OPTIONS only. Do not fall back to the MASTER last-writer string.

    IPSC is one repeater per slot, so empty peer OPTIONS may use the slot string.
    """
    if peer_id is not None:
        peer = (syscfg.get('PEERS') or {}).get(peer_id) or {}
        opt = peer.get('OPTIONS')
        if opt:
            return opt
        if syscfg.get('MODE') == 'MASTER':
            return ''
    return syscfg.get('OPTIONS') or ''


def store_peer_options(syscfg, peer_id, options):
    """Write OPTIONS onto this peer. Never copy onto a MASTER stanza."""
    if peer_id is not None:
        peer = (syscfg.get('PEERS') or {}).get(peer_id)
        if peer is not None:
            peer['OPTIONS'] = options
            if syscfg.get('MODE') == 'MASTER':
                return True
            syscfg['OPTIONS'] = options
            return True
    if syscfg.get('MODE') == 'MASTER':
        return False
    syscfg['OPTIONS'] = options
    return True


def peer_counts_for_live_statics(syscfg, peer):
    """True if this peer's OPTIONS should count toward live MASTER/IPSC statics."""
    conn = peer.get('CONNECTION')
    if syscfg.get('MODE') == 'IPSC':
        return conn in (None, 'YES')
    return conn == 'YES'


def master_has_peer_options(syscfg):
    for peer in (syscfg.get('PEERS') or {}).values():
        if peer_counts_for_live_statics(syscfg, peer) and 'OPTIONS' in peer:
            return True
    return False


def union_peer_static_lists(syscfg):
    """TS1/TS2 from cfg defaults plus every connected peer's OPTIONS (no last-writer)."""
    ts1, ts2 = [], []
    seen1, seen2 = set(), set()

    def _add(dest, seen, groups):
        for group in groups:
            if group not in seen:
                seen.add(group)
                dest.append(group)

    d1, d2 = ts_lists_from_options(syscfg.get('_default_options'))
    _add(ts1, seen1, d1)
    _add(ts2, seen2, d2)
    for peer in (syscfg.get('PEERS') or {}).values():
        if not peer_counts_for_live_statics(syscfg, peer):
            continue
        p1, p2 = ts_lists_from_options(peer.get('OPTIONS'))
        _add(ts1, seen1, p1)
        _add(ts2, seen2, p2)
    return ts1, ts2


def other_peer_has_static(syscfg, slot, tgid, except_peer_id=None):
    """True if another *connected* peer still has this static in OPTIONS."""
    want = str(int(tgid))
    for pid, peer in (syscfg.get('PEERS') or {}).items():
        if except_peer_id is not None and pid == except_peer_id:
            continue
        if not peer_counts_for_live_statics(syscfg, peer):
            continue
        ts1, ts2 = ts_lists_from_options(peer.get('OPTIONS'))
        groups = ts1 if int(slot) == 1 else ts2
        if want in groups:
            return True
    return False


def live_static_required(syscfg, slot, tgid, except_peer_id=None):
    """True when cfg defaults or another connected peer still require a static."""
    want = str(int(tgid))
    default_ts1, default_ts2 = ts_lists_from_options(
        syscfg.get('_default_options'))
    defaults = default_ts1 if int(slot) == 1 else default_ts2
    if want in defaults:
        return True
    return other_peer_has_static(
        syscfg, slot, tgid, except_peer_id=except_peer_id)


def merge_ts_into_options(options_value, ts1, ts2, disc=False):
    """Rebuild a selfcare OPTIONS string, replacing TS1/TS2 (and optional DISC=1)."""
    text = ''
    if options_value:
        text = options_value.decode() if isinstance(options_value, bytes) else str(options_value)
    kept = []
    skip = {'TS1', 'TS2', 'TS1_STATIC', 'TS2_STATIC', 'DISC'}
    for part in text.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        key, value = part.split('=', 1)
        if key.strip().upper() in skip:
            continue
        kept.append(f'{key.strip()}={value.strip()}')
    ts1_list = comma_tg_list(ts1)
    ts2_list = comma_tg_list(ts2)
    if ts1_list:
        kept.append('TS1=' + ','.join(ts1_list))
    if ts2_list:
        kept.append('TS2=' + ','.join(ts2_list))
    if disc:
        kept.append('DISC=1')
    if not kept:
        return ''
    return ';'.join(kept) + ';'
