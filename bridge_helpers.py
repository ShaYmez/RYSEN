#!/usr/bin/env python3
"""Shared bridge routing helpers (no Twisted / heavy imports)."""

from ipsc_const import is_routing_master


def iter_routing_master_systems(config_systems):
    """Yield system names that are bridge routing endpoints (MASTER / IPSC, not OBP)."""
    for _system in config_systems:
        if _system[0:3] == 'OBP':
            continue
        if is_routing_master(config_systems[_system]['MODE']):
            yield _system
