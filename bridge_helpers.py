#!/usr/bin/env python3
"""Shared bridge routing helpers (no Twisted / heavy imports)."""

import re
import time

from dmr_utils3.utils import bytes_3, int_id
from ipsc_const import is_routing_master

DIAL_A_TG = 9
DIAL_A_TG_BYTES = bytes_3(DIAL_A_TG)
_DIAL_SERVICE_CODES = frozenset([DIAL_A_TG, 4000, 5000])
PARROT_TG = 9990
_SERVICE_TG_RANGE = range(9991, 10000)


def mark_options_dirty(config):
    """Schedule one reactor-owned OPTIONS rebuild after configuration changes."""
    config['_OPTIONS_DIRTY'] = True


# RPTO / selfcare: VOICE=1 or legacy IDENT=1. Do not match UserLink=1 / RelinkTime=.
_VOICE_IDENT_ON_RE = re.compile(r'(?:^|;)(?:VOICE|IDENT)=1(?:;|$)')


def voice_ident_requested(options):
    """True only when the current OPTIONS string explicitly enables voice ident."""
    if not options:
        return False
    if isinstance(options, (bytes, bytearray)):
        options = options.decode('ascii', 'ignore')
    return bool(_VOICE_IDENT_ON_RE.search(str(options).replace(' ', '')))


def apply_voice_ident_from_options(system_cfg, parsed_options):
    """Set VOICE_IDENT from parsed OPTIONS. Missing or 0 clears a leftover slot flag."""
    if 'VOICE' not in parsed_options:
        system_cfg['VOICE_IDENT'] = False
        return False
    try:
        system_cfg['VOICE_IDENT'] = bool(int(parsed_options['VOICE']))
    except (TypeError, ValueError):
        system_cfg['VOICE_IDENT'] = False
    return system_cfg['VOICE_IDENT']


def reset_slot_voice_ident(system_cfg):
    """Generator slots reuse ports; ident must not survive the previous occupant."""
    if system_cfg is None:
        return
    system_cfg['VOICE_IDENT'] = False


def dmr_seq_delta(sequence, previous):
    """Return forward 8-bit sequence distance, or None before first packet."""
    if previous is False or previous is None:
        return None
    return (int(sequence) - int(previous)) & 0xFF


def earliest_obp_owner(openbridge_systems, system_objects, stream_id,
                       dst_id, rf_src, now, stream_timeout):
    """Return the earliest active OBP claimant, including the local ingress."""
    claims = {}
    for system in openbridge_systems:
        if system not in system_objects:
            continue
        claim = system_objects[system].STATUS.get(stream_id)
        if (claim is not None
                and '1ST' in claim
                and claim.get('TGID') == dst_id
                and claim.get('RFS') == rf_src
                and not claim.get('_fin')
                and now - claim.get('LAST', 0) < stream_timeout):
            claims[system] = claim['1ST']
    return min(claims, key=claims.get, default=False)


def harden_obp_stub(status, first_seen, lc):
    """Complete an outbound OBP status stub before it becomes an ingress."""
    status.setdefault('1ST', first_seen)
    status.setdefault('LC', lc)
    status.setdefault('lastSeq', False)
    status.setdefault('lastData', False)
    return status


def hbp_claim_is_local(claim, system, slot):
    """Return whether an active HBP claim belongs to this exact ingress."""
    return (
        claim is not None
        and claim[0] == system
        and claim[1] == slot
    )


def hbp_should_scan_obp(claim, system, slot, local_lineage):
    """Only consider an OBP mirror when this HBP ingress has no ownership."""
    return not (
        hbp_claim_is_local(claim, system, slot)
        or local_lineage
    )


def hbp_short_gap_continuation(incoming_identity, active_identity, idle,
                               stream_timeout, claim_timeout,
                               is_voice_header=False,
                               active_finished=False):
    """Keep an active HBP lineage across jitter beyond STREAM_TO."""
    return (
        incoming_identity == active_identity
        and not active_finished
        and not is_voice_header
        and stream_timeout <= idle < claim_timeout
    )


def is_dial_service_code(reflector):
    """TGs reserved for dial-a-tg signalling (channel, disconnect, status) — not link targets."""
    try:
        return int(reflector) in _DIAL_SERVICE_CODES
    except (TypeError, ValueError):
        return False


def is_invalid_dial_reflector(reflector):
    """Reflector 9 is the dial-a-tg relay channel, not a linkable reflector."""
    try:
        return int(reflector) == DIAL_A_TG
    except (TypeError, ValueError):
        return False


def is_parrot_talkgroup(tgid):
    """TG 9990 — parrot echo (group in → group out; private in → private out); never dial-a-tg / TG 9."""
    try:
        return int(tgid) == PARROT_TG
    except (TypeError, ValueError):
        return False


def is_parrot_bridge(bridge_name):
    """Conference or dial reflector bridge for parrot (never routes via OpenBridge)."""
    if not bridge_name:
        return False
    if bridge_name[0:1] == '#':
        return is_parrot_talkgroup(bridge_name[1:])
    return is_parrot_talkgroup(bridge_name)


def build_bridge_index(bridges):
    """Map (system, ts, tgid_bytes) -> set(bridge_names). Matches BRIDGE_IDX layout."""
    index = {}
    for bridge_name, entries in bridges.items():
        for entry in entries:
            key = (entry['SYSTEM'], entry['TS'], entry['TGID'])
            index.setdefault(key, set()).add(bridge_name)
    return index


def paired_group_route_bridge(orig_bridge, bridges, dst_id_bytes):
    """Return the numeric/# pair only on dial-a-tg (TG 9), not on direct talkgroup keys."""
    if dst_id_bytes != DIAL_A_TG_BYTES:
        return None
    paired = (orig_bridge[1:] if orig_bridge.startswith('#')
              else ''.join(['#', orig_bridge]))
    if paired in bridges:
        return paired
    return None


def collect_group_route_bridges(bridges, bridge_idx, system, slot, dst_id_bytes):
    """Bridge names that would invoke to_target for one inbound group RX (routerHBP path)."""
    lookup_key = (system, slot, dst_id_bytes)
    candidates = bridge_idx.get(lookup_key)
    if candidates is None:
        candidates = set()
        for bridge_name, entries in bridges.items():
            for entry in entries:
                if (entry['SYSTEM'] == system and entry['TGID'] == dst_id_bytes
                        and entry['TS'] == slot and entry.get('ACTIVE')):
                    candidates.add(bridge_name)
    routed = []
    for orig_bridge in candidates:
        if orig_bridge not in bridges:
            continue
        for entry in bridges[orig_bridge]:
            if (entry['SYSTEM'] == system and entry['TGID'] == dst_id_bytes
                    and entry['TS'] == slot and entry.get('ACTIVE')):
                routed.append(orig_bridge)
                paired = paired_group_route_bridge(orig_bridge, bridges, dst_id_bytes)
                if paired and paired not in routed:
                    routed.append(paired)
                break
    return routed


def to_target_forward_systems(bridge_entries, source_system):
    """Destination systems that would receive traffic from to_target (ACTIVE legs only)."""
    return [
        entry['SYSTEM']
        for entry in bridge_entries
        if entry['SYSTEM'] != source_system and entry.get('ACTIVE')
    ]


def private_call_may_create_reflector(int_dst_id, bridges):
    """True when a private call would invoke make_single_reflector (routerHBP private path)."""
    if is_parrot_talkgroup(int_dst_id):
        return False
    if int_dst_id < 5 or int_dst_id in (8, 9) or int_dst_id > 999999:
        return False
    if 4000 <= int_dst_id <= 5000:
        return False
    if int_dst_id in _SERVICE_TG_RANGE:
        return False
    return f'#{int_dst_id}' not in bridges


def sanitize_dial_reflectors_for_system(bridges, system):
    """Deactivate service-code # reflectors for one MASTER. Returns True if state changed."""
    changed = False
    for bridge_name in bridges:
        if bridge_name[0:1] != '#':
            continue
        for entry in bridges[bridge_name]:
            if entry['SYSTEM'] != system:
                continue
            if is_dial_service_code(bridge_name[1:]):
                if entry.get('ACTIVE'):
                    entry['ACTIVE'] = False
                    changed = True
                if entry.get('ON'):
                    entry['ON'] = []
                    changed = True
            elif DIAL_A_TG_BYTES in entry.get('ON', []):
                entry['ON'] = [x for x in entry['ON'] if x != DIAL_A_TG_BYTES]
                changed = True
    return changed


def clear_default_reflectors_for_system(bridges, system):
    """Deactivate TO_TYPE OFF (#) default dial reflector legs for one MASTER.

    Clears stale auto-linked reflectors left on a proxy slot after the prior
    hotspot disconnects (DEFAULT_REFLECTOR / DIAL / StartRef), without touching
    user-activated (TO_TYPE ON) links — those are cleared by reset_dynamic_reflectors().
    """
    changed = False
    for bridge_name in bridges:
        if bridge_name[0:1] != '#':
            continue
        if is_dial_service_code(bridge_name[1:]):
            continue
        for entry in bridges[bridge_name]:
            if entry['SYSTEM'] != system:
                continue
            if entry.get('TO_TYPE') != 'OFF':
                continue
            if entry.get('ACTIVE'):
                entry['ACTIVE'] = False
                entry['TIMER'] = time.time()
                changed = True
    return changed


_IPSC_LINK_KEYS = frozenset(['IPSC', 'LINK_IPSC'])
_SELFCARE_DISC_TRUTHY = frozenset(('1', 'true', 'yes'))


def iter_routing_master_systems(config_systems):
    """Yield system names that are bridge routing endpoints (MASTER / IPSC, not OBP)."""
    for _system in config_systems:
        if _system[0:3] == 'OBP':
            continue
        if is_routing_master(config_systems[_system]['MODE']):
            yield _system


def _normalize_options_str(options_value):
    if not options_value:
        return ''
    text = options_value.decode() if isinstance(options_value, bytes) else str(options_value)
    text = text.rstrip('\x00')
    text = text.encode('ascii', 'ignore').decode()
    text = re.sub(r"['\"]", '', text)
    return text


def parse_ipsc_link_from_options(options_value):
    """Return IPSC slot name from OPTIONS string (IPSC= or LINK_IPSC=), or None."""
    text = _normalize_options_str(options_value)
    if not text:
        return None
    for part in text.split(';'):
        try:
            key, value = part.split('=', 1)
        except ValueError:
            continue
        if key.strip() in _IPSC_LINK_KEYS:
            slot = value.strip()
            if slot:
                return slot
    return None


def _valid_ipsc_slot(config_systems, slot_name):
    if not slot_name or slot_name not in config_systems:
        return None
    if config_systems[slot_name].get('MODE') != 'IPSC':
        return None
    return slot_name


def linked_ipsc_slots(config_systems, source_system, peer_id=None):
    """IPSC slot names explicitly linked to a hotspot system (never for IPSC sources)."""
    sys_cfg = config_systems.get(source_system)
    if not sys_cfg or sys_cfg.get('MODE') == 'IPSC':
        return ()

    linked = set()

    # A live MASTER OPTIONS string may currently be the sole peer's synthesized
    # legacy policy. Once another peer arrives it must not inherit that link
    # before the coalesced options parser runs. Peer-targeted routing therefore
    # reads only the immutable stanza baseline plus that peer's own policy.
    baseline = sys_cfg.get('_default_options')
    if peer_id is None or baseline is None:
        stored = _valid_ipsc_slot(config_systems, sys_cfg.get('LINK_IPSC'))
        if stored:
            linked.add(stored)
        stanza_options = sys_cfg.get('OPTIONS')
    else:
        stanza_options = baseline

    parsed = _valid_ipsc_slot(
        config_systems, parse_ipsc_link_from_options(stanza_options))
    if parsed:
        linked.add(parsed)

    peers = sys_cfg.get('PEERS') or {}
    if peer_id and peer_id in peers:
        peer = peers[peer_id]
        peer_stored = _valid_ipsc_slot(config_systems, peer.get('LINK_IPSC'))
        if peer_stored:
            linked.add(peer_stored)
        peer_parsed = _valid_ipsc_slot(
            config_systems, parse_ipsc_link_from_options(peer.get('OPTIONS')))
        if peer_parsed:
            linked.add(peer_parsed)

    return tuple(sorted(linked))


def selfcare_disconnect_requested(options_value):
    """Return True when selfcare sent DISC=1 in an OPTIONS string."""
    text = _normalize_options_str(options_value)
    if not text:
        return False
    for part in text.split(';'):
        try:
            key, value = part.split('=', 1)
        except ValueError:
            continue
        if key.strip() == 'DISC' and value.strip().lower() in _SELFCARE_DISC_TRUTHY:
            return True
    return False


def strip_disc_from_options(options_value):
    """Remove DISC= from an OPTIONS string (one-shot selfcare disconnect flag)."""
    text = _normalize_options_str(options_value)
    if not text:
        return ''
    kept = []
    for part in text.split(';'):
        if not part.strip():
            continue
        try:
            key, value = part.split('=', 1)
        except ValueError:
            continue
        if key.strip() == 'DISC':
            continue
        kept.append(f'{key.strip()}={value.strip()}')
    if not kept:
        return ''
    return ';'.join(kept) + ';'


_INVALID_DIAL_OPTION_KEYS = frozenset({'DIAL', 'StartRef', 'DEFAULT_REFLECTOR'})


def sanitize_invalid_default_reflector_options(options_value):
    """Rewrite DIAL/StartRef/DEFAULT_REFLECTOR=9 (dial channel) to 0.

    Returns (new_options_str, changed). Stops options_config from re-parsing
    a sticky DIAL=9 every 26s after CONFIG was already coerced to 0.
    """
    text = _normalize_options_str(options_value)
    if not text:
        return '', False
    kept = []
    changed = False
    for part in text.split(';'):
        if not part.strip():
            continue
        try:
            key, value = part.split('=', 1)
        except ValueError:
            continue
        key_s = key.strip()
        val_s = value.strip()
        if key_s in _INVALID_DIAL_OPTION_KEYS and is_invalid_dial_reflector(val_s):
            kept.append(f'{key_s}=0')
            changed = True
            continue
        kept.append(f'{key_s}={val_s}')
    if not kept:
        return '', changed
    return ';'.join(kept) + ';', changed


def peer_options_sanitized_dial9(syscfg):
    """Connected peers whose OPTIONS still have DIAL/StartRef/DEFAULT_REFLECTOR=9.

    Returns [(peer_id, rewritten_options), ...]. MASTER last-writer stanza is
    not used — each radio keeps its own TS1/TS2.
    """
    out = []
    for peer_id, peer in (syscfg.get('PEERS') or {}).items():
        conn = peer.get('CONNECTION')
        if syscfg.get('MODE') == 'MASTER':
            if conn != 'YES':
                continue
        elif conn not in (None, 'YES'):
            continue
        remaining, changed = sanitize_invalid_default_reflector_options(
            peer.get('OPTIONS'))
        if changed:
            out.append((peer_id, remaining))
    return out


def _tg_int(value):
    if value is None or value is False:
        return None
    try:
        return int(int_id(value))
    except (TypeError, ValueError):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


# Per-radio UA membership survives unkey. Drop-dynamic mutes RX for this
# hotspot without taking the shared MASTER leg down for everyone else.
_PEER_DYNAMIC_TGS = {}
_PEER_REFLECTORS = {}
_PEER_MUTED_TGS = {}
_DROPPED_STREAMS = {}
_DROPPED_STREAM_GAP_S = 1.0
_DROPPED_STREAM_MAX_S = 120.0


def _peer_tg_item(slot, tgid):
    try:
        return (int(slot or 2), int(tgid))
    except (TypeError, ValueError):
        return None


def reset_peer_rx_filters():
    """Test helper: clear membership, TG mutes, and dropped streams."""
    _PEER_DYNAMIC_TGS.clear()
    _PEER_REFLECTORS.clear()
    _PEER_MUTED_TGS.clear()
    _DROPPED_STREAMS.clear()


def _live_peer_dynamic_tgs(system, peer_id, now=None):
    now = time.time() if now is None else now
    key = (system, peer_id)
    memberships = _PEER_DYNAMIC_TGS.get(key, {})
    expired = [
        item for item, expires_at in memberships.items()
        if expires_at is not None and expires_at <= now
    ]
    for item in expired:
        memberships.pop(item, None)
    if expired:
        _PEER_MUTED_TGS.setdefault(key, set()).update(expired)
    if not memberships:
        _PEER_DYNAMIC_TGS.pop(key, None)
    return memberships


def remember_peer_dynamic_tg(system, peer_id, slot, tgid, expires_at=None):
    """Record that this hotspot joined a numbered UA. Clears a prior mute."""
    if peer_id is None or not system:
        return
    item = _peer_tg_item(slot, tgid)
    if item is None:
        return
    key = (system, peer_id)
    _PEER_DYNAMIC_TGS.setdefault(key, {})[item] = expires_at
    muted = _PEER_MUTED_TGS.get(key)
    if muted:
        muted.discard(item)
        if not muted:
            _PEER_MUTED_TGS.pop(key, None)


def peer_remembered_dynamic_tgs(system, peer_id):
    return set(_live_peer_dynamic_tgs(system, peer_id))


def forget_peer_dynamic_tgs(system, peer_id):
    _PEER_DYNAMIC_TGS.pop((system, peer_id), None)


def forget_peer_dynamic_tg(system, peer_id, slot, tgid):
    key = (system, peer_id)
    memberships = _PEER_DYNAMIC_TGS.get(key)
    if not memberships:
        return
    item = _peer_tg_item(slot, tgid)
    if item is None:
        return
    memberships.pop(item, None)
    if not memberships:
        _PEER_DYNAMIC_TGS.pop(key, None)


def _live_peer_reflectors(system, peer_id, now=None):
    now = time.time() if now is None else now
    key = (system, peer_id)
    memberships = _PEER_REFLECTORS.get(key, {})
    expired = [
        item for item, expires_at in memberships.items()
        if expires_at is not None and expires_at <= now
    ]
    for item in expired:
        memberships.pop(item, None)
    if expired:
        muted = _PEER_MUTED_TGS.setdefault(key, set())
        for slot, group in expired:
            muted.add((slot, group))
            muted.add((slot, DIAL_A_TG))
    if not memberships:
        _PEER_REFLECTORS.pop(key, None)
    return memberships


def remember_peer_reflector(system, peer_id, slot, group, expires_at=None):
    if peer_id is None or not system:
        return
    item = _peer_tg_item(slot, group)
    if item is None:
        return
    key = (system, peer_id)
    _PEER_REFLECTORS.setdefault(key, {})[item] = expires_at
    muted = _PEER_MUTED_TGS.get(key)
    if muted:
        muted.discard(item)
        muted.discard((item[0], DIAL_A_TG))
        if not muted:
            _PEER_MUTED_TGS.pop(key, None)


def peer_remembered_reflectors(system, peer_id):
    return set(_live_peer_reflectors(system, peer_id))


def forget_peer_reflector(system, peer_id, slot, group):
    key = (system, peer_id)
    memberships = _PEER_REFLECTORS.get(key)
    if not memberships:
        return
    item = _peer_tg_item(slot, group)
    if item is None:
        return
    memberships.pop(item, None)
    if not memberships:
        _PEER_REFLECTORS.pop(key, None)


def other_peer_has_reflector(system, slot, group, except_peer_id=None):
    item = _peer_tg_item(slot, group)
    if item is None:
        return False
    for sys, pid in list(_PEER_REFLECTORS):
        if sys != system:
            continue
        if except_peer_id is not None and pid == except_peer_id:
            continue
        if item in _live_peer_reflectors(sys, pid):
            return True
    return False


def other_peer_has_dynamic_tg(system, slot, tgid, except_peer_id=None):
    item = _peer_tg_item(slot, tgid)
    if item is None:
        return False
    for sys, pid in list(_PEER_DYNAMIC_TGS):
        if sys != system:
            continue
        if except_peer_id is not None and pid == except_peer_id:
            continue
        tgs = _live_peer_dynamic_tgs(sys, pid)
        if item in tgs:
            return True
    return False


def mute_peer_tgs(system, peer_id, tgs):
    if peer_id is None or not system:
        return
    key = (system, peer_id)
    dest = _PEER_MUTED_TGS.setdefault(key, set())
    for slot, tgid in tgs:
        item = _peer_tg_item(slot, tgid)
        if item is not None:
            dest.add(item)
    if not dest:
        _PEER_MUTED_TGS.pop(key, None)


def unmute_peer_tg(system, peer_id, slot, tgid):
    key = (system, peer_id)
    muted = _PEER_MUTED_TGS.get(key)
    if not muted:
        return
    item = _peer_tg_item(slot, tgid)
    if item is None:
        return
    muted.discard(item)
    if not muted:
        _PEER_MUTED_TGS.pop(key, None)


def prune_dropped_streams(now=None):
    now = time.time() if now is None else now
    stale = [
        key for key, state in _DROPPED_STREAMS.items()
        if now - state['dropped_at'] >= _DROPPED_STREAM_MAX_S
    ]
    for key in stale:
        _DROPPED_STREAMS.pop(key, None)


def drop_peer_stream(system, peer_id, stream_id, slot=None, now=None):
    if peer_id is None or not stream_id:
        return
    now = time.time() if now is None else now
    prune_dropped_streams(now)
    _DROPPED_STREAMS[(system, peer_id, slot, stream_id)] = {
        'dropped_at': now,
        'last_seen': now,
    }


def release_dropped_stream(system, stream_id, slot=None):
    """VTERM / new over: this stream_id is done for every dest on the system."""
    if not stream_id:
        return
    stale = [
        key for key in _DROPPED_STREAMS
        if key[0] == system and key[3] == stream_id
        and (slot is None or key[2] == slot)
    ]
    for key in stale:
        _DROPPED_STREAMS.pop(key, None)


def stream_is_dropped(system, peer_id, stream_id, slot=None, now=None,
                      frame_type=None, dtype_vseq=None):
    now = time.time() if now is None else now
    if slot is not None:
        key = (system, peer_id, slot, stream_id)
        state = _DROPPED_STREAMS.get(key)
    else:
        key = next((
            candidate for candidate in _DROPPED_STREAMS
            if candidate[0] == system and candidate[1] == peer_id
            and candidate[3] == stream_id
        ), None)
        state = _DROPPED_STREAMS.get(key) if key is not None else None
    if state is None:
        return False
    if state is None:
        return False
    if now - state['dropped_at'] >= _DROPPED_STREAM_MAX_S:
        _DROPPED_STREAMS.pop(key, None)
        return False
    is_vhead = frame_type == 2 and dtype_vseq == 1
    if is_vhead and now - state['last_seen'] >= _DROPPED_STREAM_GAP_S:
        _DROPPED_STREAMS.pop(key, None)
        return False
    state['last_seen'] = now
    return True


def dest_peer_rx_blocked(system, dest_peer, stream_id, slot, tgid,
                         frame_type=None, dtype_vseq=None, now=None):
    """True when REPEAT / send_peers must not deliver this DMRD to dest_peer."""
    if dest_peer is None:
        return False
    _live_peer_dynamic_tgs(system, dest_peer, now=now)
    _live_peer_reflectors(system, dest_peer, now=now)
    if stream_id and stream_is_dropped(
            system, dest_peer, stream_id, slot=slot, now=now,
            frame_type=frame_type, dtype_vseq=dtype_vseq):
        return True
    muted = _PEER_MUTED_TGS.get((system, dest_peer))
    if not muted:
        return False
    item = _peer_tg_item(slot, tgid)
    return item in muted if item is not None else False


def forget_peer_rx_state(system, peer_id):
    """Peer left the stanza: drop membership, mutes, and leftover stream blocks."""
    forget_peer_dynamic_tgs(system, peer_id)
    _PEER_REFLECTORS.pop((system, peer_id), None)
    _PEER_MUTED_TGS.pop((system, peer_id), None)
    stale = [key for key in _DROPPED_STREAMS if key[0] == system and key[1] == peer_id]
    for key in stale:
        _DROPPED_STREAMS.pop(key, None)


def peer_sub_map_tgs(sub_map, system, peer_id):
    """(slot, tgid) pairs this hotspot currently has in SUB_MAP."""
    out = []
    if not sub_map or peer_id is None:
        return out
    for entry in sub_map.values():
        try:
            if len(entry) < 5 or entry[4] != peer_id or entry[0] != system:
                continue
            tgid = _tg_int(entry[2])
            if not tgid:
                continue
            out.append((int(entry[1] or 2), tgid))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def other_peer_has_sub_map_tg(sub_map, system, slot, tgid, except_peer_id=None):
    want = int(tgid)
    slot = int(slot)
    for entry in (sub_map or {}).values():
        try:
            if len(entry) < 4 or entry[0] != system or int(entry[1] or 2) != slot:
                continue
            if len(entry) >= 5 and except_peer_id is not None and entry[4] == except_peer_id:
                continue
            if _tg_int(entry[2]) == want:
                return True
        except (TypeError, ValueError, IndexError):
            continue
    return False


def _bridge_is_numbered_ua(bridges, system, slot, tgid):
    for entry in (bridges or {}).get(str(tgid), ()):
        if (entry.get('SYSTEM') == system
                and int(entry.get('TS') or 2) == slot
                and entry.get('TO_TYPE') == 'ON'):
            return True
    return False


def _numbered_ua_is_active(bridges, system, slot, tgid):
    for entry in (bridges or {}).get(str(tgid), ()):
        if (entry.get('SYSTEM') == system
                and int(entry.get('TS') or 2) == slot
                and entry.get('ACTIVE')):
            return True
    return False


def peer_dynamic_groups(sub_map, bridges, system, peer_id):
    """This hotspot's currently active dynamic TGs and owned dial reflectors."""
    out = []
    seen = set()
    for slot, tgid in (
            peer_sub_map_tgs(sub_map, system, peer_id)
            + list(peer_remembered_dynamic_tgs(system, peer_id))):
        if not _numbered_ua_is_active(bridges, system, slot, tgid):
            continue
        item = (slot, tgid)
        if item in seen:
            continue
        seen.add(item)
        out.append({'slot': slot, 'group': tgid})
    for slot, group in peer_remembered_reflectors(system, peer_id):
        active = any(
            entry.get('SYSTEM') == system
            and int(entry.get('TS') or 2) == slot
            and entry.get('ACTIVE')
            for entry in (bridges or {}).get('#' + str(group), ()))
        item = (slot, group)
        if active and item not in seen:
            seen.add(item)
            out.append({'slot': slot, 'group': group})
    for name, entries in (bridges or {}).items():
        if not name or str(name)[:1] != '#':
            continue
        try:
            group = int(str(name)[1:])
        except ValueError:
            continue
        for entry in entries:
            if entry.get('SYSTEM') != system or not entry.get('ACTIVE'):
                continue
            if entry.get('LINKER_PEER') != peer_id:
                continue
            slot = int(entry.get('TS') or 2)
            item = (slot, group)
            if item in seen:
                continue
            seen.add(item)
            out.append({'slot': slot, 'group': group})
    return out


def _merge_peer_dynamic_tgs(sub_map, system, peer_id):
    merged = []
    seen = set()
    for item in (
            list(peer_remembered_dynamic_tgs(system, peer_id))
            + peer_sub_map_tgs(sub_map, system, peer_id)):
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged


def deactivate_peer_dynamic_bridges(bridges, sub_map, system, peer_id):
    """Drop this hotspot's UA/dial and mute its RX. Leave other peers' dynamics up.

    Returns (changed, dropped_bridge_names). Dropped names are stanza legs that
    actually went inactive (for linked-IPSC cleanup). Per-peer mutes always apply.
    """
    changed = False
    dropped = set()
    if peer_id is None:
        return changed, dropped
    now = time.time()
    tgs = _merge_peer_dynamic_tgs(sub_map, system, peer_id)
    mute_items = list(peer_remembered_dynamic_tgs(system, peer_id))
    owned_reflectors = peer_remembered_reflectors(system, peer_id)
    for slot, group in owned_reflectors:
        mute_items.append((slot, group))
        mute_items.append((slot, DIAL_A_TG))
    for slot, tgid in peer_sub_map_tgs(sub_map, system, peer_id):
        if _bridge_is_numbered_ua(bridges, system, slot, tgid):
            mute_items.append((slot, tgid))

    for name, entries in (bridges or {}).items():
        if not name or str(name)[:1] != '#':
            continue
        for entry in entries:
            if entry.get('SYSTEM') != system or not entry.get('ACTIVE'):
                continue
            try:
                group = int(str(name)[1:])
            except ValueError:
                group = None
            slot = int(entry.get('TS') or 2)
            item = (slot, group) if group else None
            if (item not in owned_reflectors
                    and entry.get('LINKER_PEER') != peer_id):
                continue
            if group:
                mute_items.append((slot, group))
            dial_tgid = _tg_int(entry.get('TGID'))
            if dial_tgid:
                mute_items.append((slot, dial_tgid))
            forget_peer_reflector(system, peer_id, slot, group)
            if group and other_peer_has_reflector(
                    system, slot, group, except_peer_id=peer_id):
                if entry.get('LINKER_PEER') == peer_id:
                    clear_reflector_link_owner(entry)
                continue
            entry['ACTIVE'] = False
            entry['TIMER'] = now
            clear_reflector_link_owner(entry)
            changed = True
            dropped.add(name)

    mute_peer_tgs(system, peer_id, mute_items)

    for slot, tgid in tgs:
        if (other_peer_has_sub_map_tg(
                sub_map, system, slot, tgid, except_peer_id=peer_id)
                or other_peer_has_dynamic_tg(
                    system, slot, tgid, except_peer_id=peer_id)):
            continue
        name = str(tgid)
        if not bridges or name not in bridges or name[:1] == '#':
            continue
        for entry in bridges[name]:
            if entry.get('SYSTEM') != system or int(entry.get('TS') or 2) != slot:
                continue
            if entry.get('TO_TYPE') != 'ON' or not entry.get('ACTIVE'):
                continue
            entry['ACTIVE'] = False
            entry['TIMER'] = now
            changed = True
            dropped.add(name)

    forget_peer_dynamic_tgs(system, peer_id)
    _PEER_REFLECTORS.pop((system, peer_id), None)
    return changed, dropped


def deactivate_linked_ipsc_bridge_legs(bridges, config_systems, source_system, peer_id=None, bridge_names=None):
    """Deactivate linked IPSC legs on bridges that are active for source_system."""
    if config_systems.get(source_system, {}).get('MODE') == 'IPSC':
        return False
    linked = linked_ipsc_slots(config_systems, source_system, peer_id)
    if not linked:
        return False
    linked_set = set(linked)
    named = set(bridge_names) if bridge_names is not None else None
    changed = False
    now = time.time()
    for bridge_name, entries in bridges.items():
        if named is not None:
            if bridge_name not in named:
                continue
        else:
            source_dynamic = any(
                entry['SYSTEM'] == source_system and entry.get('ACTIVE')
                and entry.get('TO_TYPE') == 'ON'
                for entry in entries)
            source_reflector = (
                bridge_name[0:1] == '#'
                and any(entry['SYSTEM'] == source_system and entry.get('ACTIVE') for entry in entries))
            if not source_dynamic and not source_reflector:
                continue
        for entry in entries:
            if entry['SYSTEM'] not in linked_set or not entry.get('ACTIVE'):
                continue
            entry['ACTIVE'] = False
            entry['TIMER'] = now
            if bridge_name[0:1] == '#':
                clear_reflector_link_owner(entry)
            changed = True
    return changed


def activate_linked_bridge_legs(bridges, config_systems, bridge_name, source_system,
                                slot, timeout_s, now, peer_id=None):
    """Activate only explicitly linked IPSC legs on a UA bridge (hotspot → repeater).

    Returns names of legs that were newly activated. IPSC sources never wake peer legs.
    """
    if bridge_name not in bridges:
        return []
    if config_systems.get(source_system, {}).get('MODE') == 'IPSC':
        return []

    activated = []
    for target in linked_ipsc_slots(config_systems, source_system, peer_id):
        for entry in bridges[bridge_name]:
            if (entry['SYSTEM'] == target and entry['TS'] == slot
                    and entry.get('TO_TYPE') != 'NONE'):
                if not entry['ACTIVE']:
                    entry['ACTIVE'] = True
                    activated.append(target)
                entry['TIMER'] = now + timeout_s
    return activated


def reflector_bridge_matches_group_call(bridge, int_dst_id):
    """# reflector bridges: timer logic applies on dial TG 9 or the linked reflector TG."""
    if bridge[0:1] != '#':
        return True
    if int_dst_id == 9:
        return True
    try:
        return int_dst_id == int(bridge[1:])
    except (TypeError, ValueError):
        return False


def reflector_bridge_linked_int(bridge):
    if bridge[0:1] != '#':
        return None
    try:
        return int(bridge[1:])
    except (TypeError, ValueError):
        return None


def reflector_bridge_uses_linked_tg(bridge, int_dst_id, dst_id_bytes, on_list):
    """Group traffic on the linked reflector TG (not dial-a-tg channel 9)."""
    if bridge[0:1] != '#':
        return False
    if int_dst_id == 9:
        return False
    linked = reflector_bridge_linked_int(bridge)
    if linked is not None and int_dst_id == linked:
        return True
    return dst_id_bytes in (on_list or [])


def bridge_transmission_matches_rule(bridge, int_dst_id, dst_id_bytes, slot, entry):
    """Bridge rule source match: entry TGID, or linked reflector TG for # bridges."""
    if slot != entry['TS']:
        return False
    if dst_id_bytes == entry['TGID']:
        return True
    return reflector_bridge_uses_linked_tg(bridge, int_dst_id, dst_id_bytes, entry.get('ON'))


def reflector_single_mode_wrong_tg(int_dst_id, dst_id_bytes, bridge, entry):
    """SINGLE_MODE 'wrong TG' deactivation — exclude linked reflector TG activity."""
    if dst_id_bytes == entry['TGID']:
        return False
    if reflector_bridge_uses_linked_tg(bridge, int_dst_id, dst_id_bytes, entry.get('ON')):
        return False
    return True


def set_reflector_link_owner(entry, rf_src, peer_id):
    """Record who owns an active dial-a-tg link (timer resets only on their PTT)."""
    entry['LINKER'] = rf_src
    entry['LINKER_PEER'] = peer_id


def clear_reflector_link_owner(entry):
    entry.pop('LINKER', None)
    entry.pop('LINKER_PEER', None)


def reflector_timer_reset_allowed(bridge, entry, rf_src, peer_id):
    """Dial-a-tg UA timer extends only for the subscriber who linked, not network RX."""
    if bridge[0:1] != '#':
        return True
    linked = reflector_bridge_linked_int(bridge)
    if linked is not None:
        item = (int(entry.get('TS') or 2), linked)
        if item in peer_remembered_reflectors(entry.get('SYSTEM'), peer_id):
            return True
    linker = entry.get('LINKER')
    if linker is None:
        return False
    if rf_src != linker:
        return False
    linker_peer = entry.get('LINKER_PEER')
    if linker_peer is not None and peer_id != linker_peer:
        return False
    return True


def dial_reflector_user_activity_counts(int_dst_id, bridge, group_call=False):
    """Return True when this TX is dial-a-tg user activity that should extend the link timer."""
    if int_dst_id == 4000:
        return False
    linked = reflector_bridge_linked_int(bridge)
    if group_call:
        # TG 9 (dial slot) or group PTT on the linked reflector TG both count.
        if int_dst_id == 9:
            return True
        if linked is not None and int_dst_id == linked:
            return True
        return False
    if int_dst_id == 5000:
        return True
    if linked is not None and int_dst_id == linked:
        return True
    return False


def reset_dial_reflector_timers_on_user_activity(bridges, system, rf_src, peer_id, slot,
                                                 pkt_time, int_dst_id, group_call=False):
    """
    Extend UA timer when the link owner uses dial-a-tg (TG 9 group/private, status 5000,
    or private/group on the linked reflector TG). Does not reset on network RX.
    """
    if int_dst_id == 4000:
        return []
    reset_bridges = []
    for bridge, entries in bridges.items():
        if bridge[0:1] != '#':
            continue
        if not dial_reflector_user_activity_counts(int_dst_id, bridge, group_call=group_call):
            continue
        for entry in entries:
            if entry.get('SYSTEM') != system or entry.get('TS') != slot:
                continue
            if not entry.get('ACTIVE') or entry.get('TO_TYPE') != 'ON':
                continue
            timeout = entry.get('TIMEOUT')
            if not timeout:
                continue
            linked = reflector_bridge_linked_int(bridge)
            if linked is None:
                continue
            is_member = (
                (slot, linked)
                in peer_remembered_reflectors(system, peer_id))
            is_owner = entry.get('LINKER_PEER') == peer_id
            if int_dst_id == 5000 and not (is_member or is_owner):
                continue
            entry['TIMER'] = pkt_time + timeout
            remember_peer_reflector(
                system, peer_id, slot, linked,
                expires_at=entry['TIMER'])
            set_reflector_link_owner(entry, rf_src, peer_id)
            reset_bridges.append(bridge)
    return reset_bridges


# STAT trimmer: prune idle master ON legs and expire cold stat bridges.
STAT_TRIMMER_INTERVAL_S = 120
STAT_ON_LEG_IDLE_TTL_S = 3600
STAT_BRIDGE_IDLE_TTL_S = 600


def bridge_has_stat_legs(entries):
    """True when bridge was created by make_stat_bridge (has OBP STAT legs)."""
    return any(entry.get('TO_TYPE') == 'STAT' for entry in entries)


def stat_bridge_in_active_use(entries):
    """ON timer running or OFF waiting to re-activate — blocks whole-bridge removal."""
    for entry in entries:
        if entry.get('TO_TYPE') == 'ON' and entry.get('ACTIVE'):
            return True
        if entry.get('TO_TYPE') == 'OFF' and not entry.get('ACTIVE'):
            return True
    return False


def stat_bridge_last_activity(entries):
    """Latest TIMER among STAT legs (bumped on OBP call-start)."""
    last = 0.0
    for entry in entries:
        if entry.get('TO_TYPE') != 'STAT':
            continue
        timer = entry.get('TIMER', 0)
        if isinstance(timer, (int, float)):
            last = max(last, timer)
    return last


def touch_stat_bridge_activity(entries, now=None):
    """Record OBP call-start on STAT legs (never on every voice frame)."""
    _now = now if now is not None else time.time()
    for entry in entries:
        if entry.get('TO_TYPE') == 'STAT':
            entry['TIMER'] = _now


def prune_idle_stat_on_legs(entries, now=None):
    """Drop idle UA ON legs from a stat bridge after STAT_ON_LEG_IDLE_TTL_S.

    Returns (new_entries, removed_count). Master ON legs are added on demand and
    otherwise never pruned — this is the main BRIDGE_IDX bloat fix.
    """
    _now = now if now is not None else time.time()
    kept = []
    removed = 0
    for entry in entries:
        if entry.get('TO_TYPE') == 'ON' and not entry.get('ACTIVE'):
            timer = entry.get('TIMER', 0)
            if isinstance(timer, (int, float)):
                idle_s = (_now - timer) if timer <= _now else 0.0
                if idle_s >= STAT_ON_LEG_IDLE_TTL_S:
                    removed += 1
                    continue
        kept.append(entry)
    return kept, removed


def stat_bridge_should_remove(entries, now=None):
    """Remove whole stat bridge when idle (no active ON/OFF) and no recent OBP traffic."""
    if not bridge_has_stat_legs(entries):
        return False
    if stat_bridge_in_active_use(entries):
        return False
    _now = now if now is not None else time.time()
    last = stat_bridge_last_activity(entries)
    if last <= 0:
        return True
    return (_now - last) >= STAT_BRIDGE_IDLE_TTL_S


def report_include_bridge_leg(to_type, active):
    """Whether a bridge leg belongs in the monitor report payload.

    Idle UA ON legs dominate GEN_STAT meshes and inflate BRIDGE_SND pickles;
    static OFF+ACTIVE and live/STAT/NONE legs are kept.
    """
    if to_type == 'ON' and not active:
        return False
    return True


def clean_report_trigger_list(value):
    """Normalise ON/OFF/RESET trigger lists for report pickle; empty -> []."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return [value]


def build_report_bridge_leg(bridge_system, now_fn=None):
    """Build one slim report leg dict, or None if the leg should be omitted."""
    if not isinstance(bridge_system, dict):
        return None
    if 'SYSTEM' not in bridge_system or 'TS' not in bridge_system or 'TGID' not in bridge_system:
        return None
    _to_type = bridge_system.get('TO_TYPE', 'NONE')
    _active = bool(bridge_system.get('ACTIVE', False))
    if not report_include_bridge_leg(_to_type, _active):
        return None
    _now = now_fn if now_fn is not None else time.time
    _timeout = bridge_system.get('TIMEOUT', '')
    _timer = bridge_system.get('TIMER', _now())
    if _to_type == 'OFF' and _active:
        _timeout = 0
        _timer = 0
    leg = {
        'SYSTEM': bridge_system['SYSTEM'],
        'TS': bridge_system['TS'],
        'TGID': bridge_system['TGID'],
        'ACTIVE': _active,
        'TIMEOUT': _timeout,
        'TO_TYPE': _to_type,
        'TIMER': _timer,
    }
    for key in ('OFF', 'ON', 'RESET'):
        cleaned = clean_report_trigger_list(bridge_system.get(key))
        if cleaned:
            leg[key] = cleaned
    return leg
