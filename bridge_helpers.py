#!/usr/bin/env python3
"""Shared bridge routing helpers (no Twisted / heavy imports)."""

import re
import time

from dmr_utils3.utils import bytes_3
from ipsc_const import is_routing_master

DIAL_A_TG = 9
DIAL_A_TG_BYTES = bytes_3(DIAL_A_TG)
_DIAL_SERVICE_CODES = frozenset([DIAL_A_TG, 4000, 5000])
# Group TX that must not enter BRIDGE_IDX / to_target (signalling only).
# TG 9 is intentionally NOT here — it is the dial-a-tg voice channel after link.
_BRIDGE_IDX_SKIP_DST = frozenset([4000, 5000])
PARROT_TG = 9990
_SERVICE_TG_RANGE = range(9991, 10000)


def is_dial_service_code(reflector):
    """TGs reserved for dial-a-tg signalling (channel, disconnect, status) — not link targets."""
    try:
        return int(reflector) in _DIAL_SERVICE_CODES
    except (TypeError, ValueError):
        return False


def skip_bridge_idx_routing(dst):
    """True when group RX must bypass BRIDGE_IDX (4000 disconnect / 5000 status).

    Do not include TG 9: after a dial-a-tg PC link, voice is keyed on TG 9 and
    must hit to_target / paired_group_route_bridge via BRIDGE_IDX.
    """
    try:
        return int(dst) in _BRIDGE_IDX_SKIP_DST
    except (TypeError, ValueError):
        return False


def is_invalid_dial_reflector(reflector):
    """Reflector 9 is the dial-a-tg relay channel, not a linkable reflector."""
    try:
        return int(reflector) == DIAL_A_TG
    except (TypeError, ValueError):
        return False


def is_parrot_talkgroup(tgid):
    """TG 9990 — parrot echo (group call on 9990, or dial-a-tg private call to 9990)."""
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


def is_valid_talkgroup_bridge(bridge_name):
    """False for dial service codes (9/4000/5000) and parrot/service TG ranges."""
    if bridge_name[0:1] == '#':
        return not is_dial_service_code(bridge_name[1:])
    try:
        n = int(bridge_name)
    except (TypeError, ValueError):
        return True
    if is_dial_service_code(n):
        return False
    if n in _SERVICE_TG_RANGE:
        return False
    return n >= 5


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

    stored = _valid_ipsc_slot(config_systems, sys_cfg.get('LINK_IPSC'))
    if stored:
        linked.add(stored)

    parsed = _valid_ipsc_slot(
        config_systems, parse_ipsc_link_from_options(sys_cfg.get('OPTIONS')))
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

    Returns (new_options_str, changed). Stops options_config_loop from re-parsing
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


def deactivate_linked_ipsc_bridge_legs(bridges, config_systems, source_system, peer_id=None):
    """Deactivate linked IPSC legs on bridges that are active for source_system."""
    if config_systems.get(source_system, {}).get('MODE') == 'IPSC':
        return False
    linked = linked_ipsc_slots(config_systems, source_system, peer_id)
    if not linked:
        return False
    linked_set = set(linked)
    changed = False
    now = time.time()
    for bridge_name, entries in bridges.items():
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


def system_has_static_tgs(system_cfg):
    """True when TS1_STATIC or TS2_STATIC is configured (sticky TG must not run)."""
    for key in ('TS1_STATIC', 'TS2_STATIC'):
        val = system_cfg.get(key)
        if val and str(val).strip() and str(val).strip() not in ('0', 'False'):
            return True
    return False


def parse_static_tg_list(ts_static):
    """Normalize TS1_STATIC/TS2_STATIC config value to a list of TG integers."""
    if not ts_static or ts_static is False:
        return []
    text = re.sub(r'\s', '', str(ts_static))
    if not text or text in ('0', 'False'):
        return []
    result = []
    for part in text.split(','):
        if not part:
            continue
        try:
            tg = int(part)
        except (TypeError, ValueError):
            continue
        if tg <= 0 or tg >= 16777215:
            continue
        result.append(tg)
    return result


def parse_options_static_fields(options_str):
    """Extract TS1/TS2 static talkgroup lists from a semicolon OPTIONS string."""
    ts1 = False
    ts2 = False
    if not options_str:
        return ts1, ts2
    text = options_str
    if isinstance(text, bytes):
        text = text.decode('ascii', errors='ignore')
    text = text.rstrip('\x00')
    for part in str(text).split(';'):
        if '=' not in part:
            continue
        key, val = part.split('=', 1)
        key = key.strip()
        val = val.strip()
        if key in ('TS1', 'TS1_1'):
            ts1 = val if val else False
        elif key in ('TS2', 'TS2_1'):
            ts2 = val if val else False
        elif key.startswith('TS1_') and val:
            ts1 = ','.join([str(ts1), val]) if ts1 and ts1 is not False else val
        elif key.startswith('TS2_') and val:
            ts2 = ','.join([str(ts2), val]) if ts2 and ts2 is not False else val
    return ts1, ts2


def bridge_has_active_static_leg(bridges, system, ts, tg):
    """True when bridge *tg* has a permanent static leg on *system* slot *ts*."""
    from dmr_utils3.utils import bytes_3
    tgid_b = bytes_3(tg)
    for entry in bridges.get(str(tg), ()):
        if (entry.get('SYSTEM') == system and entry.get('TS') == ts
                and entry.get('TGID') == tgid_b
                and entry.get('TO_TYPE') == 'OFF' and entry.get('ACTIVE')):
            return True
    return False


def is_static_field_keyup_noise(existing_ts_static, proposed_ts_static):
    """True when a lone TG in OPTIONS likely reflects a keyed talkgroup, not static config.

    Pi-Star/VoxDMR login Options= with comma-separated statics is real config and is not
    noise. A single TG replacing an established multi-static bundle usually is key-up noise.
    """
    old_list = parse_static_tg_list(existing_ts_static)
    new_list = parse_static_tg_list(proposed_ts_static)
    if len(new_list) != 1:
        return False
    if len(old_list) <= 1:
        return False
    return new_list[0] not in old_list


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
            if entry.get('LINKER') is None:
                set_reflector_link_owner(entry, rf_src, peer_id)
            if not reflector_timer_reset_allowed(bridge, entry, rf_src, peer_id):
                continue
            entry['TIMER'] = pkt_time + timeout
            reset_bridges.append(bridge)
    return reset_bridges


# Coalesce selfcare-driven options_config() rebuilds (max once per this many seconds).
OPTIONS_CONFIG_COALESCE_S = 5.0

# STAT trimmer interval (~10 min). Tip previously used 523s offset from other timers.
STAT_TRIMMER_INTERVAL_S = 600


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


# ---------------------------------------------------------------------------
# OBP mesh hygiene (soft-client stretch keeps — do not casually remove)
# ---------------------------------------------------------------------------
# Outbound collision actions for inbound OBP RX (never reclaim/promote to CALL START).
OBP_OUTBOUND_ECHO = 'echo'
OBP_OUTBOUND_REPLACE = 'replace'

_OBP_RECLAIM_CLEAR_KEYS = (
    'LOOPLOG', '_bcsq', '_finlog', '_fin', 'H_LC', 'T_LC', 'EMB_LC',
)


def obp_target_already_has_inbound(target_status, stream_id, dst_id):
    """True if OBP STATUS already has this stream as inbound (not our outbound TX).

    Skipping TX to those peers avoids mesh re-fanout CPU when they already heard
    the call on another path (LoopControl loser / parallel ingress).
    """
    if not target_status or stream_id not in target_status:
        return False
    st = target_status[stream_id]
    if st.get('_outbound'):
        return False
    if '1ST' not in st:
        return False
    return st.get('TGID') == dst_id


def classify_obp_outbound_collision(status_entry, dst_id):
    """Classify inbound RX against an existing OBP STATUS entry.

    Returns:
      'echo' — STATUS is _outbound with same TGID; mesh/TX echo — do not route.
      'replace' — STATUS is _outbound with different TGID; delete then create fresh inbound.
      None — not an outbound collision (normal new stream or continuation).

    Production must NEVER reclaim/promote outbound into CALL START (MAX HOPS meltdown).
    """
    if not status_entry or not status_entry.get('_outbound'):
        return None
    if status_entry.get('TGID') == dst_id:
        return OBP_OUTBOUND_ECHO
    return OBP_OUTBOUND_REPLACE


def ensure_obp_inbound_status_keys(st, perf_counter_fn=None):
    """Backfill inbound-only keys on continuation STATUS (safe if already present)."""
    if 'packets' not in st:
        st['packets'] = 0
    if 'loss' not in st:
        st['loss'] = 0
    if 'lastSeq' not in st:
        st['lastSeq'] = False
    if 'lastData' not in st:
        st['lastData'] = False
    if 'crcs' not in st:
        st['crcs'] = set()
    if '1ST' not in st:
        _pc = perf_counter_fn if perf_counter_fn is not None else time.perf_counter
        st['1ST'] = _pc()
    return st


def reclaim_obp_inbound_stream(status, stream_id, pkt_time, rf_src, dst_id, peer_id):
    """Unit-test helper only — MUST remain unwired in production.

    Reclaim on VHEAD promoted mesh echoes into CALL START, skipped LoopControl,
    and inflated hops to MAX HOPS. Use classify_obp_outbound_collision instead:
    same-TG echo drop, or delete+fresh create on different TGID.
    """
    st = status.get(stream_id)
    if not st or not st.get('_outbound'):
        return False
    st.pop('_outbound', None)
    st['START'] = pkt_time
    st['CONTENTION'] = False
    st['RFS'] = rf_src
    st['TGID'] = dst_id
    st['RX_PEER'] = peer_id
    st['1ST'] = time.perf_counter()
    st['lastSeq'] = False
    st['lastData'] = False
    st['packets'] = 0
    st['loss'] = 0
    st['crcs'] = set()
    for _k in _OBP_RECLAIM_CLEAR_KEYS:
        st.pop(_k, None)
    return True
