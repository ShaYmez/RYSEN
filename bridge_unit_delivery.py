#!/usr/bin/env python3
"""BM-parity unit (private) call destination resolution and targeted delivery."""

from collections import namedtuple

from dmr_utils3.utils import int_id, bytes_3
HOTSPOT_DEFAULT_SLOT = 2

UnitDestination = namedtuple('UnitDestination', ('system', 'slot', 'peer_id', 'source'))


def subscriber_id_from_peer(peer_id):
    """Map a hotspot/repeater peer ID to the 3-byte subscriber key used in SUB_MAP."""
    int_peer = int_id(peer_id)
    if int_peer >= 10000000:
        return bytes_3(int(str(int_peer)[:7]))
    return bytes_3(int_peer)


def seed_sub_map_for_peer(sub_map, system, peer_id, slot=HOTSPOT_DEFAULT_SLOT):
    """Seed cold destination from peer login (hotspot convention: slot 2)."""
    sub_id = subscriber_id_from_peer(peer_id)
    sub_map[sub_id] = (system, slot, None, 0, peer_id)


def _peer_matches_subscriber(peer_id, int_dst_id):
    int_peer = int_id(peer_id)
    return int_peer == int_dst_id or str(int_peer)[:7] == str(int_dst_id)[:7]


def _find_peer_for_subscriber(system_name, config, dst_id, systems=None):
    int_dst = int_id(dst_id)
    peers = config['SYSTEMS'][system_name].get('PEERS') or {}
    for peer_id in peers:
        if _peer_matches_subscriber(peer_id, int_dst):
            return peer_id
    if systems and system_name in systems:
        sys = systems[system_name]
        ipsc_peers = getattr(sys, '_ipsc_peers', None)
        if ipsc_peers:
            for peer_id in ipsc_peers:
                if _peer_matches_subscriber(peer_id, int_dst):
                    return peer_id
    return None


def _sub_map_entry(sub_map, dst_id):
    try:
        entry = sub_map[dst_id]
    except KeyError:
        return None
    if not entry or len(entry) < 2:
        return None
    system = entry[0]
    slot = entry[1]
    peer_id = entry[4] if len(entry) >= 5 else None
    return system, slot, peer_id


def resolve_unit_destination(dst_id, *, config, sub_map, systems, source_system,
                           source_mode, prefer_sub_map=True):
    """Resolve where a private call should be delivered (local BM-parity).

    Resolution order:
      1. SUB_MAP (system, slot, peer_id) when system is ENABLED
      2. Peer prefix match on MASTER/IPSC systems (slot 2 for hotspots)
      3. IPSC-only scan when source is not IPSC
    """
    int_dst_id = int_id(dst_id)

    if prefer_sub_map and dst_id in sub_map:
        parsed = _sub_map_entry(sub_map, dst_id)
        if parsed:
            system, slot, peer_id = parsed
            if system in systems and config['SYSTEMS'].get(system, {}).get('ENABLED'):
                if peer_id is None:
                    peer_id = _find_peer_for_subscriber(system, config, dst_id, systems)
                if peer_id is not None:
                    return UnitDestination(system, slot, peer_id, 'sub_map')

    for system_name in systems:
        sys_cfg = config['SYSTEMS'].get(system_name, {})
        mode = sys_cfg.get('MODE')
        if mode not in ('MASTER', 'IPSC'):
            continue
        if not sys_cfg.get('ENABLED'):
            continue
        peers = sys_cfg.get('PEERS') or {}
        for peer_id in peers:
            if _peer_matches_subscriber(peer_id, int_dst_id):
                return UnitDestination(system_name, HOTSPOT_DEFAULT_SLOT, peer_id, 'peer_prefix')

    if source_mode != 'IPSC':
        for system_name in systems:
            if config['SYSTEMS'].get(system_name, {}).get('MODE') != 'IPSC':
                continue
            if not config['SYSTEMS'][system_name].get('ENABLED'):
                continue
            peer_id = _find_peer_for_subscriber(system_name, config, dst_id, systems)
            if peer_id is not None:
                return UnitDestination(system_name, HOTSPOT_DEFAULT_SLOT, peer_id, 'ipsc_peer')

    return None


def resolve_unit_destination_local(dst_id, *, config, sub_map, systems, source_system,
                                   source_mode, source_peer_id=None, prefer_sub_map=True):
    """Resolve destination; drop same peer on same system (caller leg)."""
    dest = resolve_unit_destination(
        dst_id, config=config, sub_map=sub_map, systems=systems,
        source_system=source_system, source_mode=source_mode, prefer_sub_map=prefer_sub_map,
    )
    if dest is None:
        return None
    if dest.system == source_system and dest.peer_id == source_peer_id:
        return None
    if dest.system not in systems:
        return None
    if not config['SYSTEMS'].get(dest.system, {}).get('ENABLED'):
        return None
    return dest


def unit_voice_stream_active_elsewhere(stream_id, dst_id, source_system, systems, config):
    """True when another system already carries this private voice stream (loop guard)."""
    for system_name in systems:
        if system_name == source_system:
            continue
        mode = config['SYSTEMS'].get(system_name, {}).get('MODE')
        if mode == 'OPENBRIDGE':
            status = systems[system_name].STATUS
            if stream_id in status and status[stream_id].get('TGID') == dst_id:
                return True
            continue
        for slot_status in systems[system_name].STATUS.values():
            if slot_status.get('RX_STREAM_ID') == stream_id:
                return True
    return False


def deliver_unit_voice(dest, *, systems, config, source_system, slot, bits, data, dmrpkt,
                       source_peer_id=None):
    """Send one unit voice DMRD burst to a resolved destination. Returns True if sent."""
    if dest.system not in systems:
        return False
    if dest.system == source_system and dest.peer_id == source_peer_id:
        return False

    send_bits = bits ^ (1 << 7) if slot != dest.slot else bits
    packet = b''.join([
        data[:15], send_bits.to_bytes(1, 'big'), data[16:20], dmrpkt,
    ])

    target = systems[dest.system]
    mode = config['SYSTEMS'][dest.system].get('MODE')

    if mode == 'IPSC':
        voice = getattr(target, '_voice', None)
        if voice is None:
            return False
        ipsc_pkt = voice.handle_outbound(packet)
        if ipsc_pkt is None:
            return False
        sender = getattr(target, '_ipsc_send_voice_to_peer', None)
        if sender is None:
            return False
        return sender(ipsc_pkt, dest.peer_id)

    if dest.peer_id is None:
        return False
    target.send_peer(dest.peer_id, packet)
    return True
