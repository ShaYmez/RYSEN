#!/usr/bin/env python3
"""Shared bridge routing helpers (no Twisted / heavy imports)."""

import re

from ipsc_const import is_routing_master

_IPSC_LINK_KEYS = frozenset(['IPSC', 'LINK_IPSC'])


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
        return int_dst_id == 9 or (linked is not None and int_dst_id == linked)
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
