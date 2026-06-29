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


def touch_reflector_ua_timers(bridges, bridge, int_dst_id, dst_id_bytes, slot, pkt_time):
    """Reset UA timers on active dial-a-tg links when the linked TG is in use (any bridge leg)."""
    if bridge not in bridges:
        return
    if not reflector_bridge_matches_group_call(bridge, int_dst_id):
        return
    if bridge[0:1] == '#' and int_dst_id == 9:
        return
    for entry in bridges[bridge]:
        if entry['TS'] != slot:
            continue
        if not entry.get('ACTIVE') or entry.get('TO_TYPE') != 'ON':
            continue
        timeout = entry.get('TIMEOUT')
        if not timeout:
            continue
        if reflector_bridge_uses_linked_tg(bridge, int_dst_id, dst_id_bytes, entry.get('ON')):
            entry['TIMER'] = pkt_time + timeout
