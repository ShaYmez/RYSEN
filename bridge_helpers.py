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
