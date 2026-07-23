#!/usr/bin/env python
# RYSEN DMRMaster+ Version 1.5.1
###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>  
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
# Copyright (C) 2024-2026 Shane Daley, M0VUB <shane@freestar.network> (IPSC / SystemX)
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software Foundation,
#   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
###############################################################################

'''
This application, in conjunction with it's rule file (rules.py) will
work like a "conference bridge". This is similar to what most hams think of as a
reflector. You define conference bridges and any system joined to that conference
bridge will both receive traffic from, and send traffic to any other system
joined to the same conference bridge. It does not provide end-to-end connectivity
as each end system must individually be joined to a conference bridge (a name
you create in the configuration file) to pass traffic.

This program currently only works with group voice calls.
'''

# Python modules we need
import sys
from bitarray import bitarray
from time import time,sleep,perf_counter
import importlib.util
import re
import copy
from setproctitle import setproctitle
from collections import deque

#from crccheck.crc import Crc32
from hashlib import blake2b

# Twisted is pretty important, so I keep it separate
from twisted.internet.protocol import Factory, Protocol
from twisted.protocols.basic import NetstringReceiver
from twisted.internet import reactor, task
from twisted.internet.defer import inlineCallbacks

# Things we import from the main hblink module
from hblink import HBSYSTEM, OPENBRIDGE, systems, hblink_handler, reportFactory, REPORT_OPCODES, mk_aliases, acl_check
from dmr_utils3.utils import bytes_3, int_id, get_alias, bytes_4
from dmr_utils3 import decode, bptc, const
import config
from config import acl_build
import log
from const import *
from mk_voice import pkt_gen
from ipsc_master import IpscMasterMixin
from ipsc_const import is_routing_master
from selfcare_db import (
    SelfcareDB,
    find_hotspot_master_peer,
    find_ipsc_peer_for_radio_id,
)
from bridge_helpers import iter_routing_master_systems as _iter_routing_master_systems
from bridge_helpers import (
    DIAL_A_TG,
    DIAL_A_TG_BYTES,
    PARROT_TG,
    is_dial_service_code,
    is_invalid_dial_reflector,
    normalize_default_reflector,
    normalize_static_tg_csv,
    parse_options_static_fields,
    is_parrot_talkgroup,
    is_parrot_bridge,
    reflector_bridge_matches_group_call,
    bridge_transmission_matches_rule,
    reflector_single_mode_wrong_tg,
    reflector_timer_reset_allowed,
    set_reflector_link_owner,
    clear_reflector_link_owner,
    reset_dial_reflector_timers_on_user_activity,
    selfcare_disconnect_requested,
    strip_disc_from_options,
    deactivate_linked_ipsc_bridge_legs,
    paired_group_route_bridge,
    clear_default_reflectors_for_system,
    system_has_static_tgs,
    is_valid_talkgroup_bridge,
    parse_static_tg_list,
    bridge_has_active_static_leg,
    is_static_field_keyup_noise,
    classify_obp_outbound_collision,
    ensure_obp_inbound_status_keys,
    report_include_bridge_leg,
    build_report_bridge_leg,
    should_report_obp_rx_start,
    should_report_hbp_rx_start,
    should_report_stream_end,
    dmrd_seq_delta,
    target_requires_emb_lc_rewrite,
    begin_generated_voice,
    generated_voice_cancelled,
    end_generated_voice,
    cancel_generated_voice,
    hbp_slot_prompt_defaults,
    obp_target_already_has_inbound,
    group_call_end_bridge_candidates,
    STAT_TRIMMER_INTERVAL_S,
    OBP_RATE_DROP_ENABLED,
    OBP_RATE_DROP_MIN_DURATION,
    OBP_RATE_DROP_MIN_PACKETS,
    OBP_RATE_DROP_MAX_PPS,
    HBP_RATE_DROP_ENABLED,
    HBP_RATE_DROP_MIN_PACKETS,
    HBP_RATE_DROP_MAX_PPS,
    OBP_OUTBOUND_ECHO,
    OBP_OUTBOUND_REPLACE,
)
# NOTE: 'words' is loaded dynamically via readAMBE() at runtime (see line ~2689)
#from voice_lib import words

#Read voices
from read_ambe import readAMBE
#Remap some words for certain languages
from i8n_voice_map import voiceMap

# Stuff for socket reporting
import pickle
# The module needs logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)

from binascii import b2a_hex as ahex

from AMI import AMI


# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS, Forked by Simon Adlem - G7RZU'
__copyright__  = 'Copyright (c) 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group, Simon Adlem, G7RZU 2020,2021, 2022'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT; Jon Lee, G4TSN; Norman Williams, M6NBP, Eric Craw KF7EEL'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Simon Adlem G7RZU'
__email__      = 'simon@gb7fr.org.uk'

#Set header bits
#used for slot rewrite and type rewrite
def header(slot,call_type,bits):
    
    if not bits:
        bits = 0b00100000
    
    bits = slot << 7 | bits
    
    if call_type == 'unit':
        
        bits = 0b00000011 | bits
    
    return bits
    
        

# Timed loop used for reporting HBP status
#
# REPORT BASED ON THE TYPE SELECTED IN THE MAIN CONFIG FILE
def config_reports(_config, _factory):
    if True: #_config['REPORTS']['REPORT']:
        def reporting_loop(logger, _server):
            logger.debug('(REPORT) Periodic reporting loop started')
            _server.send_config()
            _server.send_bridge()
            i = 0
            for system in CONFIG['SYSTEMS']:
                if 'PEERS' in CONFIG['SYSTEMS'][system] and CONFIG['SYSTEMS'][system]['PEERS']:
                    i = i +1
            logger.info('(REPORT) %s systems have at least one peer',i)
            logger.info('(REPORT) Subscriber Map has %s entries',len(SUB_MAP))
            
        logger.info('(REPORT) HBlink TCP reporting server configured')

        report_server = _factory(_config)
        report_server.clients = []
        reactor.listenTCP(_config['REPORTS']['REPORT_PORT'], report_server)

        reporting = task.LoopingCall(reporting_loop, logger, report_server)
        reporting.start(_config['REPORTS']['REPORT_INTERVAL'])

    return report_server


# ---------------------------------------------------------------------------
# Routing index
# BRIDGE_IDX maps (system_name, ts, tgid_bytes) -> set(bridge_names)
# so per-packet routing can find the relevant bridge names in O(1) instead
# of scanning the entire BRIDGES dict (which can grow to O(GENERATOR * TGs)).
#
# Rules:
#   - Every function that adds/removes entries in BRIDGES MUST update the index.
#   - The hot-path routing loops (routerOBP/routerHBP.dmrd_received) use
#     BRIDGE_IDX to avoid the full O(N*M) scan.
#   - If an index inconsistency is ever detected at runtime the code falls back
#     to the full scan and schedules a rebuild (belt-and-suspenders safety).
# ---------------------------------------------------------------------------
BRIDGE_IDX = {}

# Routing statistics counters (reset every _ROUTE_STATS_INTERVAL seconds)
_ROUTE_STATS = {'packets': 0, 'index_hits': 0, 'index_misses': 0, 'fallbacks': 0}
_ROUTE_STATS_INTERVAL = 300          # report every 5 minutes
_ROUTE_STATS_NEXT_LOG = [0.0]        # mutable list so inner functions can write it

# Reactor-lag diagnostics
_REACTOR_LAG_INTERVAL = 5.0          # expected loop-call interval (seconds)
_REACTOR_LAG_LAST = [None]           # timestamp of last check

# LoopControl optimisation: avoid scanning every SYSTEM-XXX on each voice packet.
_OBP_SYSTEMS = ()                    # refreshed at startup
_STREAM_RX_OWNER = {}                  # stream_id bytes -> HBP system name


def refresh_obp_system_list():
    """Cache OPENBRIDGE system names for LoopControl (small fixed set)."""
    global _OBP_SYSTEMS
    _OBP_SYSTEMS = tuple(
        s for s in CONFIG['SYSTEMS']
        if CONFIG['SYSTEMS'][s].get('ENABLED')
        and CONFIG['SYSTEMS'][s]['MODE'] == 'OPENBRIDGE'
    )


def _track_hbp_rx_stream(system, slot_status, stream_id):
    """Record which HBP system is receiving stream_id (LoopControl fast path)."""
    _prev = slot_status.get('RX_STREAM_ID')
    if _prev and _prev != b'\x00' and _prev != stream_id:
        if _STREAM_RX_OWNER.get(_prev) == system:
            _STREAM_RX_OWNER.pop(_prev, None)
    if stream_id and stream_id != b'\x00':
        _STREAM_RX_OWNER[stream_id] = system


def _untrack_hbp_rx_stream(system, stream_id):
    if stream_id and stream_id != b'\x00' and _STREAM_RX_OWNER.get(stream_id) == system:
        _STREAM_RX_OWNER.pop(stream_id, None)


def _find_hbp_stream_rx_owner(stream_id, exclude=None):
    """Return (True, system) if a non-OBP system is RXing stream_id; else (False, None).

    Uses _STREAM_RX_OWNER when warm; falls back to full scan so behaviour matches
    pre-optimisation code if the map is stale or missing an entry.
    """
    _owner = _STREAM_RX_OWNER.get(stream_id)
    if _owner is not None and _owner != exclude:
        if CONFIG['SYSTEMS'][_owner]['MODE'] != 'OPENBRIDGE':
            return True, _owner
        return False, None
    for _system in systems:
        if _system == exclude:
            continue
        if CONFIG['SYSTEMS'][_system]['MODE'] == 'OPENBRIDGE':
            continue
        for _sysslot in systems[_system].STATUS:
            if systems[_system].STATUS[_sysslot].get('RX_STREAM_ID') == stream_id:
                _STREAM_RX_OWNER[stream_id] = _system
                return True, _system
    return False, None


def _obp_loop_hr_times(stream_id, dst_id):
    """Collect OBP first-receiver times for stream_id (inbound OBP legs only)."""
    _hr_times = {}
    for _obp in _OBP_SYSTEMS:
        _st = systems[_obp].STATUS.get(stream_id)
        if _st and not _st.get('_outbound') and '1ST' in _st and _st['TGID'] == dst_id:
            _hr_times[_obp] = _st['1ST']
    return _hr_times


def _idx_add_bridge(bridge_name):
    """Add all entries for *bridge_name* into BRIDGE_IDX."""
    for e in BRIDGES.get(bridge_name, ()):
        _key = (e['SYSTEM'], e['TS'], e['TGID'])
        if _key not in BRIDGE_IDX:
            BRIDGE_IDX[_key] = set()
        BRIDGE_IDX[_key].add(bridge_name)


def _idx_remove_bridge(bridge_name):
    """Remove all BRIDGE_IDX entries that reference *bridge_name*."""
    empty_keys = [k for k, v in BRIDGE_IDX.items() if bridge_name in v]
    for _key in empty_keys:
        BRIDGE_IDX[_key].discard(bridge_name)
        if not BRIDGE_IDX[_key]:
            del BRIDGE_IDX[_key]


def _idx_replace_bridge(bridge_name):
    """Refresh BRIDGE_IDX for a single bridge (remove stale, add fresh entries)."""
    _idx_remove_bridge(bridge_name)
    _idx_add_bridge(bridge_name)


def rebuild_bridge_index():
    """Rebuild BRIDGE_IDX from scratch.  Call after bulk BRIDGES mutations."""
    global BRIDGE_IDX
    new_idx = {}
    for _bname, _entries in BRIDGES.items():
        for e in _entries:
            _key = (e['SYSTEM'], e['TS'], e['TGID'])
            if _key not in new_idx:
                new_idx[_key] = set()
            new_idx[_key].add(_bname)
    BRIDGE_IDX = new_idx
    logger.debug('(ROUTER) BRIDGE_IDX rebuilt: %d keys across %d bridges',
                 len(BRIDGE_IDX), len(BRIDGES))


def reactorLagCheck():
    """Looping diagnostic: warn when the Twisted reactor falls behind schedule."""
    _now = time()
    if _REACTOR_LAG_LAST[0] is not None:
        _actual = _now - _REACTOR_LAG_LAST[0]
        _lag = _actual - _REACTOR_LAG_INTERVAL
        if _lag > 0.5:
            logger.warning(
                '(DIAGNOSTICS) Reactor lag: %.3fs behind schedule '
                '(actual interval %.3fs vs expected %.1fs). '
                'Bridge index size: %d keys / %d bridges.',
                _lag, _actual, _REACTOR_LAG_INTERVAL,
                len(BRIDGE_IDX), len(BRIDGES))
    _REACTOR_LAG_LAST[0] = _now


def _log_route_stats():
    """Periodically log routing index hit/miss counters (called from hot path)."""
    _now = time()
    if _now >= _ROUTE_STATS_NEXT_LOG[0]:
        logger.info(
            '(DIAGNOSTICS) Routing stats (last %.0fs): '
            'packets=%d idx_hits=%d idx_misses=%d fallbacks=%d '
            'bridges=%d idx_keys=%d',
            _ROUTE_STATS_INTERVAL,
            _ROUTE_STATS['packets'], _ROUTE_STATS['index_hits'],
            _ROUTE_STATS['index_misses'], _ROUTE_STATS['fallbacks'],
            len(BRIDGES), len(BRIDGE_IDX))
        _ROUTE_STATS['packets'] = 0
        _ROUTE_STATS['index_hits'] = 0
        _ROUTE_STATS['index_misses'] = 0
        _ROUTE_STATS['fallbacks'] = 0
        _ROUTE_STATS_NEXT_LOG[0] = _now + _ROUTE_STATS_INTERVAL


# Import Bridging rules
# Note: A stanza *must* exist for any MASTER or CLIENT configured in the main
# configuration file and listed as "active". It can be empty,
# but it has to exist.
def make_bridges(_rules):
    # Convert integer GROUP ID numbers from the config into hex strings
    # we need to send in the actual data packets.
    for _bridge in _rules:
        for _system in _rules[_bridge]:
            if _system['SYSTEM'] not in CONFIG['SYSTEMS']:
                sys.exit('ERROR: Conference bridge "{}" references a system named "{}" that is not enabled in the main configuration'.format(_bridge, _system['SYSTEM']))

            _system['TGID']       = bytes_3(_system['TGID'])
            for i, e in enumerate(_system['ON']):
                _system['ON'][i]  = bytes_3(_system['ON'][i])
            for i, e in enumerate(_system['OFF']):
                _system['OFF'][i] = bytes_3(_system['OFF'][i])
            _system['TIMEOUT']    = _system['TIMEOUT']*60
            if _system['ACTIVE'] == True:
                _system['TIMER']  = time() + _system['TIMEOUT']
            else:
                _system['TIMER']  = time()
        
       # if _bridge[0:1] == '#':
        #    continue
        
        for _confsystem in CONFIG['SYSTEMS']:
            #if _confsystem[0:3] == 'OBP':
            if not is_routing_master(CONFIG['SYSTEMS'][_confsystem]['MODE']):
                continue
            ts1 = False 
            ts2 = False
            for i,e in enumerate(_rules[_bridge]):
                if e['SYSTEM'] == _confsystem and e['TS'] == 1:
                    ts1 = True
                if e['SYSTEM'] == _confsystem and e['TS'] == 2:
                    ts2 = True
            if _bridge[0:1] != '#':
                _tmout = CONFIG['SYSTEMS'][_confsystem]['DEFAULT_UA_TIMER']
                if ts1 == False:
                    _rules[_bridge].append({'SYSTEM': _confsystem, 'TS': 1, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                if ts2 == False:
                    _rules[_bridge].append({'SYSTEM': _confsystem, 'TS': 2, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
            else:
                _tmout = CONFIG['SYSTEMS'][_confsystem]['DEFAULT_UA_TIMER']
                if ts2 == False:
                    _rules[_bridge].append({'SYSTEM': _confsystem, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [bytes_3(4000)],'ON': [],'RESET': [], 'TIMER': time()})
    
    return _rules


def iter_routing_master_systems():
    """Yield system names that are bridge routing endpoints (MASTER / IPSC, not OBP)."""
    yield from _iter_routing_master_systems(CONFIG['SYSTEMS'])


def augment_bridges_for_masters():
    """After GENERATOR split, sync each bridge with all current routing masters.

    make_bridges() runs before SYSTEM/IPSC -> SYSTEM-0..N / IPSC-0..N expansion, so
    rules-based bridges may reference removed names and lack generated slots.
    Safe to call with GENERATOR 1 (no-op aside from stale entry cleanup).
    """
    _removed = 0
    _added = 0
    for _bridge in list(BRIDGES.keys()):
        _fresh = []
        for _entry in BRIDGES[_bridge]:
            if _entry['SYSTEM'] not in CONFIG['SYSTEMS']:
                _removed += 1
                logger.info('(ROUTER) Removed stale bridge entry %s / %s (system not in config)',
                            _bridge, _entry['SYSTEM'])
                continue
            _fresh.append(_entry)
        BRIDGES[_bridge] = _fresh

        try:
            _bridge_tgid = int(_bridge[1:]) if _bridge[0:1] == '#' else int(_bridge)
        except ValueError:
            continue

        for _confsystem in CONFIG['SYSTEMS']:
            if not is_routing_master(CONFIG['SYSTEMS'][_confsystem]['MODE']):
                continue
            ts1 = False
            ts2 = False
            for _entry in BRIDGES[_bridge]:
                if _entry['SYSTEM'] == _confsystem and _entry['TS'] == 1:
                    ts1 = True
                if _entry['SYSTEM'] == _confsystem and _entry['TS'] == 2:
                    ts2 = True
            _tmout = CONFIG['SYSTEMS'][_confsystem]['DEFAULT_UA_TIMER']
            if _bridge[0:1] != '#':
                if not ts1:
                    BRIDGES[_bridge].append({
                        'SYSTEM': _confsystem, 'TS': 1, 'TGID': bytes_3(_bridge_tgid),
                        'ACTIVE': False, 'TIMEOUT': _tmout * 60, 'TO_TYPE': 'ON',
                        'OFF': [], 'ON': [bytes_3(_bridge_tgid)], 'RESET': [], 'TIMER': time()})
                    _added += 1
                if not ts2:
                    BRIDGES[_bridge].append({
                        'SYSTEM': _confsystem, 'TS': 2, 'TGID': bytes_3(_bridge_tgid),
                        'ACTIVE': False, 'TIMEOUT': _tmout * 60, 'TO_TYPE': 'ON',
                        'OFF': [], 'ON': [bytes_3(_bridge_tgid)], 'RESET': [], 'TIMER': time()})
                    _added += 1
            elif not ts2:
                BRIDGES[_bridge].append({
                    'SYSTEM': _confsystem, 'TS': 2, 'TGID': bytes_3(9),
                    'ACTIVE': False, 'TIMEOUT': _tmout * 60, 'TO_TYPE': 'ON',
                    'OFF': [bytes_3(4000)], 'ON': [], 'RESET': [], 'TIMER': time()})
                _added += 1

    if _removed or _added:
        rebuild_bridge_index()
        logger.info('(ROUTER) Post-generator bridge augment: removed %s stale, added %s master slots',
                    _removed, _added)


def _ensure_master_on_legs(bridge_name, system):
    """Add missing UA ON legs for a routing master on a lazy STAT bridge.

    make_stat_bridge only creates OBP STAT legs; master ON legs are added on demand
    when this hotspot/IPSC actually keys or needs the TG.
    """
    if bridge_name not in BRIDGES or system not in CONFIG['SYSTEMS']:
        return False
    if not is_routing_master(CONFIG['SYSTEMS'][system]['MODE']):
        return False
    try:
        _tgid_i = int(bridge_name[1:]) if bridge_name[0:1] == '#' else int(bridge_name)
    except ValueError:
        return False
    _tgid_b = bytes_3(_tgid_i)
    _tmout = CONFIG['SYSTEMS'][system].get('DEFAULT_UA_TIMER', 10)
    if bridge_name == '9990':
        _tmout = 1
    _existing = {
        (_e['SYSTEM'], _e['TS'])
        for _e in BRIDGES[bridge_name]
        if _e['SYSTEM'] == system and _e.get('TO_TYPE') == 'ON'
    }
    _added = False
    for _ts in (1, 2):
        if (system, _ts) in _existing:
            continue
        BRIDGES[bridge_name].append({
            'SYSTEM': system, 'TS': _ts, 'TGID': _tgid_b,
            'ACTIVE': False, 'TIMEOUT': _tmout * 60, 'TO_TYPE': 'ON',
            'OFF': [], 'ON': [_tgid_b], 'RESET': [], 'TIMER': time(),
        })
        _added = True
    if _added:
        _idx_replace_bridge(bridge_name)
    return _added


def activate_ua_bridge_source(bridge_name, system, slot, tmout=None, peer_id=None):
    """Activate this master's UA slot on an existing bridge (e.g. direct TG 9990 PTT)."""
    if bridge_name not in BRIDGES:
        return False
    if system not in CONFIG['SYSTEMS']:
        return False
    _ensure_master_on_legs(bridge_name, system)
    if tmout is None:
        tmout = CONFIG['SYSTEMS'][system].get('DEFAULT_UA_TIMER')
        if tmout is None:
            for _entry in BRIDGES[bridge_name]:
                if _entry['SYSTEM'] == system and _entry['TS'] == slot:
                    _entry_timeout = _entry.get('TIMEOUT')
                    if isinstance(_entry_timeout, (int, float)) and _entry_timeout > 0:
                        tmout = max(1, int(_entry_timeout // 60))
                    break
        if tmout is None:
            tmout = 10
    if bridge_name == '9990':
        tmout = 1
    _timeout_s = tmout * 60
    _changed = False
    _ua_refreshed = False
    for _entry in BRIDGES[bridge_name]:
        if (_entry['SYSTEM'] == system and _entry['TS'] == slot
                and _entry['TO_TYPE'] != 'NONE'):
            if not _entry['ACTIVE']:
                _entry['ACTIVE'] = True
                _changed = True
                logger.info('(ROUTER) Bridge %s activated for %s TS%s', bridge_name, system, slot)
            if _entry.get('TO_TYPE') == 'OFF' and _entry.get('ACTIVE'):
                continue
            _entry['TIMER'] = time() + _timeout_s
            if _entry.get('TO_TYPE') == 'ON':
                _ua_refreshed = True
    _activate_linked_ipsc_legs(bridge_name, system, slot, _timeout_s, peer_id)
    if _changed or _ua_refreshed:
        notify_bridge_table_updated()
    return _changed


def _activate_linked_ipsc_legs(bridge_name, source_system, slot, timeout_s, peer_id=None):
    """Optionally wake linked IPSC slot(s) when a hotspot keys a UA bridge (OPTIONS IPSC=)."""
    from bridge_helpers import activate_linked_bridge_legs
    _now = time()
    for _target in activate_linked_bridge_legs(
            BRIDGES, CONFIG['SYSTEMS'], bridge_name, source_system, slot, timeout_s, _now, peer_id):
        logger.info('(ROUTER) Bridge %s linked leg activated: %s TS%s (source %s)',
                    bridge_name, _target, slot, source_system)


#Make a single bridge - used for on-the-fly UA bridges
def make_single_bridge(_tgid,_sourcesystem,_slot,_tmout):
    _tgid_s = str(int_id(_tgid))
    #Always a 1 min timeout for Echo
    if _tgid_s == '9990':
        _tmout = 1
    BRIDGES[_tgid_s] = []
    for _system in iter_routing_master_systems():
        if _system == _sourcesystem:
            if _slot == 1:
                BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time() + (_tmout * 60)})
                BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 2, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
            else:
                BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 2, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time() + (_tmout * 60)})
                BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
        else:
            BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
            BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 2, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})

    for _system in CONFIG['SYSTEMS']:
        if _system[0:3] == 'OBP' and (int_id(_tgid) >= 59 and (int_id(_tgid) < 9990 or int_id(_tgid) > 9999)):
            BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': '','TO_TYPE': 'NONE','OFF': [],'ON': [],'RESET': [], 'TIMER': time()})
    _activate_linked_ipsc_legs(_tgid_s, _sourcesystem, _slot, _tmout * 60)
    # Keep routing index in sync
    _idx_add_bridge(_tgid_s)
    notify_bridge_table_updated()

#Make static bridge - used for on-the-fly relay bridges
def make_stat_bridge(_tgid):
    """Create a lazy STAT bridge: OBP STAT legs only.

    Master UA ON legs are added on demand via _ensure_master_on_legs /
    activate_ua_bridge_source (avoids 2*N_masters BRIDGE_IDX keys per discovered TG).
    """
    if is_parrot_talkgroup(int_id(_tgid)):
        return
    _tgid_s = str(int_id(_tgid))
    BRIDGES[_tgid_s] = []
    for _system in CONFIG['SYSTEMS']:
        if _system[0:3] == 'OBP':
            BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': '','TO_TYPE': 'STAT','OFF': [],'ON': [],'RESET': [], 'TIMER': time()})
    # Keep routing index in sync
    _idx_add_bridge(_tgid_s)
        

_DIAL_A_TG = DIAL_A_TG
_DIAL_A_TG_BYTES = DIAL_A_TG_BYTES


def is_reflector_private_destination(int_dst_id):
    """Private-call dial-a-tg targets handled locally (never unit-voice bridged outward).

    Today this is intentionally broad (any ID >= 5 except 8/9) so dial-a-tg private
    calls never leak to _forward_unit_voice(). That blocks unit-to-unit routing for
    normal subscriber IDs — see roadmap Phase 4.

    DMR numbering on SystemX (convention, not strict CPS):
      ≤5 digits — talkgroups (dial-a-tg link targets, max 99999)
      6 digits  — repeater radio IDs
      7 digits  — individual subscribers (and some 7-digit hotspots without SSID)
      9 digits  — hotspots with SSID suffix (intended; not always enforced in field)
    """
    if is_parrot_talkgroup(int_dst_id):
        return False
    if int_dst_id in (4000, 5000):
        return True
    if int_dst_id in range(9991, 10000):
        return True
    if int_dst_id >= 5 and int_dst_id not in (8, 9) and int_dst_id <= 999999:
        return True
    return False


def _reflector_bridge_matches_group_call(bridge, int_dst_id):
    return reflector_bridge_matches_group_call(bridge, int_dst_id)


def _build_disconnect_say(system):
    """AMBE phrase list when a user-activated reflector link times out."""
    _lang = CONFIG['SYSTEMS'][system]['ANNOUNCEMENT_LANGUAGE']
    _say = [words[_lang]['silence'], words[_lang]['silence']]
    if CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'] > 0:
        _say.extend([
            words[_lang]['silence'], words[_lang]['linkedto'], words[_lang]['silence'],
            words[_lang]['to'], words[_lang]['silence'], words[_lang]['silence'],
        ])
        for number in str(CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR']):
            _say.append(words[_lang][number])
            _say.append(words[_lang]['silence'])
    else:
        _say.append(words[_lang]['notlinked'])
    _say.append(words[_lang]['silence'])
    return _say


def make_default_reflector(reflector,_tmout,system):
    if is_invalid_dial_reflector(reflector):
        logger.warning('(REFLECTOR) Ignoring invalid default reflector %s for %s', reflector, system)
        return
    bridge = ''.join(['#',str(reflector)])
    #_tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    if bridge not in BRIDGES:
        BRIDGES[bridge] = []
        make_single_reflector(bytes_3(reflector),_tmout, system)
    bridgetemp = deque()
    for bridgesystem in BRIDGES[bridge]:
        if bridgesystem['SYSTEM'] == system and bridgesystem['TS'] == 2:
            bridgetemp.append({'SYSTEM': system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': True,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'OFF','OFF': [],'ON': [bytes_3(reflector),],'RESET': [], 'TIMER': time() + (_tmout * 60)})
        else:
            bridgetemp.append(bridgesystem)
            
        BRIDGES[bridge] = bridgetemp
    _idx_replace_bridge(bridge)

def _ensure_obp_stat_leg(tg_s, tgid_b):
    """Permanent OBP STAT leg so inbound TS1 (e.g. from OpenBridge) can route to static TGs."""
    if is_parrot_talkgroup(tg_s):
        return
    if not CONFIG['GLOBAL'].get('GEN_STAT_BRIDGES'):
        return
    if tg_s not in BRIDGES:
        return
    for _system in CONFIG['SYSTEMS']:
        if _system[0:3] != 'OBP':
            continue
        for _entry in BRIDGES[tg_s]:
            if _entry['SYSTEM'] == _system and _entry.get('TO_TYPE') == 'STAT':
                return
        BRIDGES[tg_s].append({
            'SYSTEM': _system, 'TS': 1, 'TGID': tgid_b,
            'ACTIVE': True, 'TIMEOUT': '', 'TO_TYPE': 'STAT',
            'OFF': [], 'ON': [], 'RESET': [], 'TIMER': 0,
        })
        logger.info('(OPTIONS) Added OBP STAT leg for static TG %s', tg_s)


def make_static_tg(tg, ts, _tmout, system):
    #_tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    tg_s = str(tg)
    tgid_b = bytes_3(tg)
    static_entry = {
        'SYSTEM': system, 'TS': ts, 'TGID': tgid_b,
        'ACTIVE': True, 'TIMEOUT': 0, 'TO_TYPE': 'OFF',
        'OFF': [], 'ON': [tgid_b], 'RESET': [],
        'TIMER': 0,
    }
    if tg_s not in BRIDGES:
        make_single_bridge(tgid_b, system, ts, _tmout)
    bridgetemp = deque()
    matched = False
    for bridgesystem in BRIDGES[tg_s]:
        if bridgesystem['SYSTEM'] == system and bridgesystem['TS'] == ts:
            bridgetemp.append(static_entry)
            matched = True
        else:
            bridgetemp.append(bridgesystem)
    if not matched:
        bridgetemp.append(static_entry)
        logger.info('(OPTIONS) Added static TG %s TS%s leg for %s (bridge existed without slot)',
                    tg_s, ts, system)
    BRIDGES[tg_s] = bridgetemp
    _idx_replace_bridge(tg_s)
    _ensure_obp_stat_leg(tg_s, tgid_b)


def purge_invalid_bridges():
    """Remove bogus bridge keys (4000 disconnect, 9 dial channel, etc.)."""
    _removed = []
    for _bridge in list(BRIDGES.keys()):
        if is_valid_talkgroup_bridge(_bridge):
            continue
        _idx_remove_bridge(_bridge)
        del BRIDGES[_bridge]
        _removed.append(_bridge)
    if _removed:
        rebuild_bridge_index()
        logger.info('(ROUTER) Purged invalid bridge keys: %s', _removed)
    return _removed


def reset_static_tg(tg,ts,_tmout,system):
    #_tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    if str(tg) not in BRIDGES:
        logger.debug('(OPTIONS) reset_static_tg skipped, missing bridge %s for %s TS%s', tg, system, ts)
        return
    bridgetemp = deque()
    for bridgesystem in BRIDGES[str(tg)]:
        if bridgesystem['SYSTEM'] == system and bridgesystem['TS'] == ts:
            bridgetemp.append({'SYSTEM': system, 'TS': ts, 'TGID': bytes_3(tg),'ACTIVE': False,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(tg),],'RESET': [], 'TIMER': time() + (_tmout * 60)})
        else:
            bridgetemp.append(bridgesystem)
        
    BRIDGES[str(tg)] = bridgetemp
    _idx_replace_bridge(str(tg))


def ensure_static_tgs_for_system(system, tmout=None):
    """Idempotent: create any missing OFF/ACTIVE static legs from CONFIG TS1/TS2_STATIC."""
    if system not in CONFIG['SYSTEMS']:
        return 0
    if not is_routing_master(CONFIG['SYSTEMS'][system]['MODE']):
        return 0
    if not system_has_static_tgs(CONFIG['SYSTEMS'][system]):
        return 0
    if tmout is None:
        tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    repaired = 0
    for tg in parse_static_tg_list(CONFIG['SYSTEMS'][system].get('TS1_STATIC')):
        if not bridge_has_active_static_leg(BRIDGES, system, 1, tg):
            make_static_tg(tg, 1, tmout, system)
            repaired += 1
            logger.info('(OPTIONS) Repaired missing static TG %s TS1 for %s', tg, system)
    for tg in parse_static_tg_list(CONFIG['SYSTEMS'][system].get('TS2_STATIC')):
        if not bridge_has_active_static_leg(BRIDGES, system, 2, tg):
            make_static_tg(tg, 2, tmout, system)
            repaired += 1
            logger.info('(OPTIONS) Repaired missing static TG %s TS2 for %s', tg, system)
    if repaired:
        notify_bridge_table_updated()
    return repaired


def reapply_static_tgs_for_system(system, tmout=None):
    """Re-create static TG bridge legs for one routing master (after bridge reset)."""
    return ensure_static_tgs_for_system(system, tmout)


def repair_static_tgs_all_systems():
    """Ensure static legs exist for every routing master with TS1/TS2_STATIC configured."""
    for _system in CONFIG['SYSTEMS']:
        if not CONFIG['SYSTEMS'][_system].get('ENABLED'):
            continue
        ensure_static_tgs_for_system(_system)


def reset_default_reflector(reflector,_tmout,system):
    try:
        reflector = int(reflector)
    except (TypeError, ValueError):
        return
    if reflector <= 0 or is_invalid_dial_reflector(reflector):
        return
    bridge = ''.join(['#',str(reflector)])
    #_tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    if bridge not in BRIDGES:
        BRIDGES[bridge] = []
        make_single_reflector(bytes_3(reflector),_tmout, system)
    bridgetemp = deque()
    for bridgesystem in BRIDGES[bridge]:
        if bridgesystem['SYSTEM'] == system and bridgesystem['TS'] == 2:
            bridgetemp.append({'SYSTEM': system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(reflector),],'RESET': [], 'TIMER': time() + (_tmout * 60)})
        else:
            bridgetemp.append(bridgesystem)
        BRIDGES[bridge] = bridgetemp
    _idx_replace_bridge(bridge)

def make_single_reflector(_tgid,_tmout,_sourcesystem):
    if is_parrot_talkgroup(int_id(_tgid)):
        return
    if is_invalid_dial_reflector(int_id(_tgid)):
        logger.warning('(REFLECTOR) Refusing to create invalid dial reflector #9 for %s', _sourcesystem)
        return
    _tgid_s = str(int_id(_tgid))
    _bridge = ''.join(['#',_tgid_s])
    #1 min timeout for echo
    if _tgid_s == '9990':
        _tmout = 1
    BRIDGES[_bridge] = []
    for _system in iter_routing_master_systems():
        if _system == _sourcesystem:
            BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': True,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time() + (_tmout * 60)})
        else:
            BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT':  CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER'] * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
    for _system in CONFIG['SYSTEMS']:
        if _system[0:3] == 'OBP' and (int_id(_tgid) >= 59 and (int_id(_tgid) < 9990 or int_id(_tgid) > 9999)):
            BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': '','TO_TYPE': 'NONE','OFF': [],'ON': [],'RESET': [], 'TIMER': time()})
    # Keep routing index in sync
    _idx_add_bridge(_bridge)

def remove_bridge_system(system, new_timeout_s=None, preserve_static_legs=True):
    _bridgestemp = {}
    for _bridge in BRIDGES:
        for _bridgesystem in BRIDGES[_bridge]:
            if _bridgesystem['SYSTEM'] != system:
                if _bridge not in _bridgestemp:
                    _bridgestemp[_bridge] = []
                _bridgestemp[_bridge].append(_bridgesystem)
            else:
                if _bridge not in _bridgestemp:
                    _bridgestemp[_bridge] = []
                if (preserve_static_legs
                        and _bridgesystem['TO_TYPE'] == 'OFF' and _bridgesystem['ACTIVE'] == True):
                    _timeout = _bridgesystem['TIMEOUT']
                    if new_timeout_s is not None and isinstance(_timeout, (int, float)):
                        _timeout = new_timeout_s
                    _bridgestemp[_bridge].append({
                        'SYSTEM': system,
                        'TS': _bridgesystem['TS'],
                        'TGID': _bridgesystem['TGID'],
                        'ACTIVE': True,
                        'TIMEOUT': 0,
                        'TO_TYPE': 'OFF',
                        'OFF': list(_bridgesystem['OFF']),
                        'ON': list(_bridgesystem['ON']),
                        'RESET': list(_bridgesystem['RESET']),
                        'TIMER': 0,
                    })
                else:
                    _timeout = _bridgesystem['TIMEOUT']
                    if (new_timeout_s is not None and isinstance(_timeout, (int, float))):
                        _timeout = new_timeout_s
                    _timer = time() + _timeout if isinstance(_timeout, (int, float)) and _timeout else time()
                    _bridgestemp[_bridge].append({
                        'SYSTEM': system,
                        'TS': _bridgesystem['TS'],
                        'TGID': _bridgesystem['TGID'],
                        'ACTIVE': False,
                        'TIMEOUT': _timeout,
                        'TO_TYPE': _bridgesystem['TO_TYPE'],
                        'OFF': list(_bridgesystem['OFF']),
                        'ON': (list(_bridgesystem['ON']) if _bridgesystem['ON']
                               else ([] if _bridge[0:1] == '#' else [_bridgesystem['TGID']])),
                        'RESET': list(_bridgesystem['RESET']),
                        'TIMER': _timer,
                    })
            
    BRIDGES.update(_bridgestemp)
    # Entries for the system changed across ALL bridges; cheapest correct option is a full rebuild
    rebuild_bridge_index()


def clear_default_reflectors(system):
    """Deactivate stale default (TO_TYPE OFF) dial reflectors on one MASTER slot."""
    if clear_default_reflectors_for_system(BRIDGES, system):
        rebuild_bridge_index()
        logger.info('(REFLECTOR) Cleared stale default reflector(s) for %s', system)


def reset_dynamic_reflectors(system):
    """Deactivate dial-a-tg reflector links (TO_TYPE ON, bridge name #...) for one MASTER.

    Default reflector bridges (TO_TYPE OFF) are cleared by clear_default_reflectors().
  """
    _changed = False
    for _bridge in BRIDGES:
        if _bridge[0:1] != '#':
            continue
        for _sys in BRIDGES[_bridge]:
            if _sys['SYSTEM'] != system or _sys['TO_TYPE'] != 'ON':
                continue
            if _sys['ACTIVE']:
                _sys['ACTIVE'] = False
                _sys['TIMER'] = time()
                clear_reflector_link_owner(_sys)
                _changed = True
                logger.info('(REFLECTOR) Cleared dynamic dial-a-tg link %s for %s', _bridge, system)
    if _changed:
        rebuild_bridge_index()


def disconnect_dial_reflectors(system):
    """Deactivate all # reflector bridges for one MASTER (private call 4000 disconnect)."""
    _changed = False
    for _bridge in BRIDGES:
        if _bridge[0:1] != '#':
            continue
        for _sys in BRIDGES[_bridge]:
            if _sys['SYSTEM'] != system:
                continue
            if _sys['ACTIVE']:
                _sys['ACTIVE'] = False
                _sys['TIMER'] = time()
                clear_reflector_link_owner(_sys)
                _changed = True
                logger.info('(REFLECTOR) Disconnect (4000): deactivated %s for %s', _bridge, system)
    if _changed:
        rebuild_bridge_index()
    purge_invalid_bridges()


def deactivate_user_activated_bridges(system):
    """Deactivate numeric user-activated bridge legs (TO_TYPE ON) for one routing system."""
    _changed = False
    for _bridge in BRIDGES:
        if _bridge[0:1] == '#':
            continue
        for _sys in BRIDGES[_bridge]:
            if (_sys['SYSTEM'] == system and _sys['ACTIVE']
                    and _sys['TO_TYPE'] == 'ON'):
                _sys['ACTIVE'] = False
                _sys['TIMER'] = time()
                _changed = True
                logger.info('(UA) Selfcare disconnect: deactivated bridge %s for %s',
                            _bridge, system)
    if _changed:
        rebuild_bridge_index()


def selfcare_disconnect(source_system, peer_id=None):
    """Drop dial-a-tg reflector and user-activated talkgroup links for one subscriber."""
    disconnect_dial_reflectors(source_system)
    deactivate_user_activated_bridges(source_system)
    if deactivate_linked_ipsc_bridge_legs(
            BRIDGES, CONFIG['SYSTEMS'], source_system, peer_id):
        rebuild_bridge_index()
    if peer_id:
        clear_sub_map_for_peer(peer_id)
    else:
        clear_sub_map_for_system(source_system)
    notify_bridge_table_updated()
    logger.info('(SELF SERVICE) Dynamic links cleared for %s (peer %s)',
                source_system, int_id(peer_id) if peer_id else 'n/a')


def apply_selfcare_options(source_system, peer_id, options_str):
    """Process DISC=1 immediately; return remaining OPTIONS text (without DISC)."""
    _had_disc = selfcare_disconnect_requested(options_str)
    if _had_disc:
        selfcare_disconnect(source_system, peer_id)
    if _had_disc:
        return strip_disc_from_options(options_str), True
    return options_str, False


def sanitize_dial_reflectors(system):
    """Deactivate service-code reflectors and strip dial TG from poisoned ON lists."""
    _changed = False
    for _bridge in BRIDGES:
        if _bridge[0:1] != '#':
            continue
        for _sys in BRIDGES[_bridge]:
            if _sys['SYSTEM'] != system:
                continue
            if is_dial_service_code(_bridge[1:]):
                if _sys['ACTIVE']:
                    _sys['ACTIVE'] = False
                    _sys['TIMER'] = time()
                    _changed = True
                    logger.info('(REFLECTOR) Cleared dial service reflector %s for %s', _bridge, system)
                if _sys['ON']:
                    _sys['ON'] = []
                    _changed = True
            elif _DIAL_A_TG_BYTES in _sys['ON']:
                _sys['ON'] = [x for x in _sys['ON'] if x != _DIAL_A_TG_BYTES]
                _changed = True
                logger.info('(REFLECTOR) Removed dial TG from ON list on %s for %s', _bridge, system)
    if _changed:
        rebuild_bridge_index()


def deactivate_other_dynamic_reflectors(system, keep_bridge, slot=2):
    """Ensure only one user-activated (TO_TYPE ON) reflector is active per MASTER."""
    _changed = False
    for _bridge in BRIDGES:
        if _bridge[0:1] != '#' or _bridge == keep_bridge:
            continue
        for _sys in BRIDGES[_bridge]:
            if (_sys['SYSTEM'] == system and _sys['TS'] == slot
                    and _sys['TO_TYPE'] == 'ON' and _sys['ACTIVE']):
                _sys['ACTIVE'] = False
                _sys['TIMER'] = time()
                _changed = True
                logger.info('(REFLECTOR) Single dial-a-tg mode: deactivated %s for %s (keeping %s)',
                            _bridge, system, keep_bridge)
    if _changed:
        rebuild_bridge_index()


def clear_sub_map_for_system(system):
    """Remove persisted subscriber entries for a MASTER (e.g. on hotspot login/disconnect)."""
    _remove = []
    for _subscriber in SUB_MAP:
        try:
            if SUB_MAP[_subscriber][0] == system:
                _remove.append(_subscriber)
        except (TypeError, IndexError):
            pass
    for _subscriber in _remove:
        SUB_MAP.pop(_subscriber, None)
    if _remove:
        logger.info('(SUBSCRIBER) Cleared %s SUB_MAP entries for %s', len(_remove), system)


def clear_sub_map_for_peer(peer_id):
    """Remove SUB_MAP entries for a hotspot peer (survives REPEATER-N slot changes)."""
    _remove = []
    for _subscriber in SUB_MAP:
        try:
            _entry = SUB_MAP[_subscriber]
            if len(_entry) >= 5 and _entry[4] == peer_id:
                _remove.append(_subscriber)
        except (TypeError, IndexError):
            pass
    for _subscriber in _remove:
        SUB_MAP.pop(_subscriber, None)
    if _remove:
        logger.info('(SUBSCRIBER) Cleared %s SUB_MAP entries for peer %s', len(_remove), int_id(peer_id))


def clear_subscriber_on_disconnect(system, subscriber_id, peer_id):
    """Drop sticky-TG state when the user dials disconnect (4000)."""
    if subscriber_id not in SUB_MAP:
        return
    try:
        _entry = SUB_MAP[subscriber_id]
        if _entry[0] == system or (len(_entry) >= 5 and _entry[4] == peer_id):
            SUB_MAP.pop(subscriber_id, None)
            logger.info('(SUBSCRIBER) Cleared sticky TG for subscriber %s on disconnect', int_id(subscriber_id))
    except (TypeError, IndexError):
        pass


def notify_bridge_table_updated():
    """Push fresh bridge table (incl. TIMER) to RYSEN-MONITOR (BRIDGE_SND).

    Used after dial-a-tg timer changes and dynamic UA bridge create/activate.
    """
    if not CONFIG.get('REPORTS', {}).get('REPORT'):
        return
    _server = globals().get('report_server')
    if _server is None:
        return
    try:
        _server.send_bridge()
    except Exception as exc:
        logger.debug('(REPORT) send_bridge after dial-a-tg timer reset failed: %s', exc)


# Run this every minute for rule timer updates
def rule_timer_loop():
    logger.debug('(ROUTER) routerHBP Rule timer loop started')
    _now = time()
    _remove_bridges = deque()

    # Pre-compute set of systems that have any sticky-TG feature enabled.
    # This avoids scanning the full SUB_MAP for every active bridge entry on
    # systems where stickiness is not configured at all.
    _sticky_enabled_systems = set()
    for _sys in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][_sys].get('STICKY_TG', False):
            _sticky_enabled_systems.add(_sys)
        elif 'PEERS' in CONFIG['SYSTEMS'][_sys]:
            for _pid in CONFIG['SYSTEMS'][_sys]['PEERS']:
                if CONFIG['SYSTEMS'][_sys]['PEERS'][_pid].get('STICKY', False):
                    _sticky_enabled_systems.add(_sys)
                    break

    for _bridge in BRIDGES:
        _bridge_used = False
        for _system in BRIDGES[_bridge]:
            if _system['TO_TYPE'] == 'ON':
                if _system['ACTIVE'] == True:
                    _bridge_used = True
                    
                    # STICKY_TG LOGIC: Check if this bridge should remain active due to sticky TG
                    # PRODUCTION SAFETY: Feature flag check - only apply sticky logic if enabled
                    # Priority: Peer STICKY > System STICKY_TG > Default (False)
                    # Dial-a-tg reflector bridges (#...) must not use sticky TG — they share TGID 9
                    # and would all stay active while the user is on TG 9.
                    _sticky_active = False
                    # Optimisation: only scan SUB_MAP for systems that actually have
                    # sticky TG enabled (pre-computed above).  Avoids O(N_subscribers)
                    # scan for every active bridge entry on non-sticky systems.
                    if (_bridge[0:1] != '#' and
                            is_routing_master(CONFIG['SYSTEMS'][_system['SYSTEM']]['MODE']) and
                            _system['SYSTEM'] in _sticky_enabled_systems and
                            not system_has_static_tgs(CONFIG['SYSTEMS'][_system['SYSTEM']])):
                        # Check if any subscriber has this TG as their sticky TG
                        for _subscriber in SUB_MAP:
                            try:
                                _sub_peer_id = None
                                if len(SUB_MAP[_subscriber]) == 5:
                                    _sub_system, _sub_ts, _sub_tg, _sub_time, _sub_peer_id = SUB_MAP[_subscriber]
                                elif len(SUB_MAP[_subscriber]) == 4:
                                    _sub_system, _sub_ts, _sub_tg, _sub_time = SUB_MAP[_subscriber]
                                else:
                                    continue  # Old 3-element format doesn't support sticky TG
                                
                                # Check if subscriber is on this system and has this TG as sticky
                                if (_sub_system == _system['SYSTEM'] and 
                                    _sub_ts == _system['TS'] and 
                                    _sub_tg == _system['TGID'] and
                                    _sub_tg is not None):
                                    
                                    # Determine if sticky TG is enabled for this subscriber
                                    # Priority 1: Per-peer STICKY setting (if available)
                                    # Priority 2: System-wide STICKY_TG setting
                                    # Priority 3: Default to False
                                    _sticky_enabled = False
                                    if (_sub_peer_id and 
                                        'PEERS' in CONFIG['SYSTEMS'][_system['SYSTEM']] and
                                        _sub_peer_id in CONFIG['SYSTEMS'][_system['SYSTEM']]['PEERS'] and
                                        'STICKY' in CONFIG['SYSTEMS'][_system['SYSTEM']]['PEERS'][_sub_peer_id]):
                                        # Peer has explicit STICKY setting - use it
                                        _sticky_enabled = CONFIG['SYSTEMS'][_system['SYSTEM']]['PEERS'][_sub_peer_id]['STICKY']
                                        logger.debug('(%s) STICKY_TG: Using peer-level STICKY=%s for subscriber %s (peer %s)', 
                                                   _system['SYSTEM'], _sticky_enabled, int_id(_subscriber), int_id(_sub_peer_id))
                                    elif CONFIG['SYSTEMS'][_system['SYSTEM']].get('STICKY_TG', False):
                                        # No peer-level setting, use system default
                                        _sticky_enabled = True
                                        logger.debug('(%s) STICKY_TG: Using system-level STICKY_TG for subscriber %s', 
                                                   _system['SYSTEM'], int_id(_subscriber))
                                    
                                    if _sticky_enabled:
                                        _sticky_active = True
                                        logger.debug('(%s) STICKY_TG: Bridge %s kept active for subscriber %s on TG %s', 
                                                   _system['SYSTEM'], _bridge, int_id(_subscriber), int_id(_sub_tg))
                                        break
                            except (TypeError, ValueError, IndexError):
                                pass
                    
                    if _sticky_active:
                        # Keep bridge active due to sticky TG - don't timeout
                        _bridge_used = True
                        logger.debug('(ROUTER) Conference Bridge ACTIVE (STICKY_TG): System: %s Bridge: %s, TS: %s, TGID: %s', 
                                   _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                    elif _system['TIMER'] < _now:
                        # Normal timeout behavior when sticky TG not active
                        _system['ACTIVE'] = False
                        if _bridge[0:1] == '#':
                            clear_reflector_link_owner(_system)
                        _timeout_min = int(_system['TIMEOUT'] // 60) if _system['TIMEOUT'] else 0
                        logger.info(
                            '(ROUTER) Conference Bridge TIMEOUT (%s min): DEACTIVATE System: %s, Bridge: %s, TS: %s, TGID: %s',
                            _timeout_min, _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                        if _bridge[0:1] == '#':
                            reactor.callInThread(disconnectedVoice, _system['SYSTEM'])
                    else:
                        timeout_in = _system['TIMER'] - _now
                        _bridge_used = True
                        logger.debug('(ROUTER) Conference Bridge ACTIVE (ON timer running): System: %s Bridge: %s, TS: %s, TGID: %s, Timeout in: %.2fs,', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']),  timeout_in)
                elif _system['ACTIVE'] == False:
                    logger.debug('(ROUTER) Conference Bridge INACTIVE (no change): System: %s Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
            elif _system['TO_TYPE'] == 'OFF':
                # PRIORITY: Static TGs always override - they use TO_TYPE='OFF'
                # Static TGs (TS1_STATIC/TS2_STATIC) have highest priority and always remain active
                if _system['ACTIVE'] == False:
                    if _system['TIMER'] < _now:
                        _system['ACTIVE'] = True
                        _bridge_used = True 
                        logger.info('(ROUTER) Conference Bridge TIMEOUT: ACTIVATE System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                    else:
                        timeout_in = _system['TIMER'] - _now
                        _bridge_used = True
                        logger.debug('(ROUTER) Conference Bridge INACTIVE (OFF timer running): System: %s Bridge: %s, TS: %s, TGID: %s, Timeout in: %.2fs,', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']),  timeout_in)
                elif _system['ACTIVE'] == True:
                    _bridge_used = True
                    logger.debug('(ROUTER) Conference Bridge ACTIVE (no change): System: %s Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
            else:
                if _system['SYSTEM'][0:3] != 'OBP':
                    _bridge_used = True
                elif _system['SYSTEM'][0:3] == 'OBP' and _system['TO_TYPE'] == 'STAT':
                    _bridge_used = True
                logger.debug('(ROUTER) Conference Bridge NO ACTION: System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                
        if _bridge_used == False:
            _remove_bridges.append(_bridge)
                
    for _bridgerem in _remove_bridges:
        _idx_remove_bridge(_bridgerem)
        del BRIDGES[_bridgerem]
        logger.debug('(ROUTER) Unused conference bridge %s removed',_bridgerem)

    if CONFIG['REPORTS']['REPORT']:
        report_server.send_clients(b'bridge updated')

def statTrimmer():
    logger.debug('(ROUTER) STAT trimmer loop started')
    _remove_bridges = deque()
    for _bridge in BRIDGES:
        _bridge_stat = False
        _in_use = False
        for _system in BRIDGES[_bridge]:
            if _system['TO_TYPE'] == 'STAT':
                _bridge_stat = True
            if _system['TO_TYPE'] == 'ON' and _system['ACTIVE']:
                _in_use = True
            elif _system['TO_TYPE'] == 'OFF' and _system['ACTIVE']:
                _in_use = True
            elif _system['TO_TYPE'] == 'OFF' and not _system['ACTIVE']:
                _in_use = True
        if _bridge_stat and not _in_use:
            _remove_bridges.append(_bridge)
    for _bridgerem in _remove_bridges:
        _idx_remove_bridge(_bridgerem)
        del BRIDGES[_bridgerem]
        logger.info('(ROUTER) STAT bridge %s removed (idle, no active legs)', _bridgerem)
    if _remove_bridges:
        rebuild_bridge_index()
    repair_static_tgs_all_systems()
    if CONFIG['REPORTS']['REPORT']:
        report_server.send_clients(b'bridge updated')

def kaReporting():
    logger.debug('(ROUTER) KeepAlive reporting loop started')
    for system in systems:
        if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
            if CONFIG['SYSTEMS'][system]['ENHANCED_OBP']:
                if '_bcka' not in CONFIG['SYSTEMS'][system]:
                   logger.warning('(ROUTER) not sending to system %s as KeepAlive never seen',system)
                elif CONFIG['SYSTEMS'][system]['_bcka'] < time() - 60:
                    logger.warning('(ROUTER) not sending to system %s as last KeepAlive was %s seconds ago',system, int(time() - CONFIG['SYSTEMS'][system]['_bcka']))
 
#Write SUB_MAP to disk 
def subMapWrite():
    try:
        _fh = open(CONFIG['ALIASES']['PATH'] + CONFIG['ALIASES']['SUB_MAP_FILE'],'wb')
        pickle.dump(SUB_MAP,_fh)
        _fh.close()
        logger.info('(SUBSCRIBER) Writing SUB_MAP to disk')
    except:
        logger.warning('(SUBSCRIBER) Cannot write SUB_MAP to file')
        

#Subscriber Map trimmer loop
def SubMapTrimmer():
    logger.debug('(SUBSCRIBER) Subscriber Map trimmer loop started')
    _sub_time = time()
    _remove_list = deque()
    for _subscriber in SUB_MAP:
        # BACKWARDS COMPATIBILITY: Handle 3, 4, and 5-element formats
        try:
            # Determine timestamp index based on format
            if len(SUB_MAP[_subscriber]) == 5:
                # New format: (system, ts, tg, timestamp, peer_id)
                _timestamp_idx = 3
            elif len(SUB_MAP[_subscriber]) == 4:
                # Old format: (system, ts, tg, timestamp)
                _timestamp_idx = 3
            elif len(SUB_MAP[_subscriber]) == 3:
                # Old format: (system, ts, timestamp)
                _timestamp_idx = 2
            else:
                logger.warning('(SUBSCRIBER) Invalid SUB_MAP entry for subscriber %s: unexpected length %s', 
                             int_id(_subscriber), len(SUB_MAP[_subscriber]))
                _remove_list.append(_subscriber)
                continue
            
            # Check if entry should be removed (older than 24 hours)
            if SUB_MAP[_subscriber][_timestamp_idx] < (_sub_time - 86400):
                _remove_list.append(_subscriber)
        except (TypeError, IndexError) as e:
            logger.warning('(SUBSCRIBER) Invalid SUB_MAP entry for subscriber %s, removing: %s', int_id(_subscriber), e)
            _remove_list.append(_subscriber)
    
    for _remove in _remove_list:
        SUB_MAP.pop(_remove)
    if CONFIG['ALIASES']['SUB_MAP_FILE']:
        subMapWrite()
 

# run this every 10 seconds to trim stream ids
def stream_trimmer_loop():
    logger.debug('(ROUTER) Trimming inactive stream IDs from system lists')
    _now = time()

    for system in systems:
        # HBP systems, master and peer
        if CONFIG['SYSTEMS'][system]['MODE'] != 'OPENBRIDGE':
            for slot in range(1,3):
                _slot  = systems[system].STATUS[slot]

                # RX slot check
                if _slot['RX_TYPE'] != HBPF_SLT_VTERM and _slot['RX_TIME'] <  _now - 5:
                    _slot['RX_TYPE'] = HBPF_SLT_VTERM
                    if 'loss' in _slot and 'packets' in _slot and _slot['packets']:
                        loss = (_slot['loss'] / _slot['packets']) * 100
                        logger.info('(%s) *TIME OUT*  RX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f, LOSS: %.2f%%', \
                            system, int_id(_slot['RX_STREAM_ID']), int_id(_slot['RX_RFS']), int_id(_slot['RX_TGID']), slot, _slot['RX_TIME'] - _slot['RX_START'],loss)
                    else:
                        logger.info('(%s) *TIME OUT*  RX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f', \
                            system, int_id(_slot['RX_STREAM_ID']), int_id(_slot['RX_RFS']), int_id(_slot['RX_TGID']), slot, _slot['RX_TIME'] - _slot['RX_START'])
                    if CONFIG['REPORTS']['REPORT'] and should_report_stream_end(_slot):
                        systems[system]._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(_slot['RX_STREAM_ID']), int_id(_slot['RX_PEER']), int_id(_slot['RX_RFS']), slot, int_id(_slot['RX_TGID']), _slot['RX_TIME'] - _slot['RX_START']).encode(encoding='utf-8', errors='ignore'))
                #Null stream_id - for loop control 
                if _slot['RX_TIME'] < _now - 60:
                    _untrack_hbp_rx_stream(system, _slot['RX_STREAM_ID'])
                    _slot['RX_STREAM_ID'] = b'\x00'

                # TX slot check
                if _slot['TX_TYPE'] != HBPF_SLT_VTERM and _slot['TX_TIME'] <  _now - 5:
                    _slot['TX_TYPE'] = HBPF_SLT_VTERM
                    logger.debug('(%s) *TIME OUT*  TX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f', \
                        system, int_id(_slot['TX_STREAM_ID']), int_id(_slot['TX_RFS']), int_id(_slot['TX_TGID']), slot, _slot['TX_TIME'] - _slot['TX_START'])
                    if CONFIG['REPORTS']['REPORT']:
                        systems[system]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(_slot['TX_STREAM_ID']), int_id(_slot['TX_PEER']), int_id(_slot['TX_RFS']), slot, int_id(_slot['TX_TGID']), _slot['TX_TIME'] - _slot['TX_START']).encode(encoding='utf-8', errors='ignore'))

        # OBP systems
        # We can't delete items from a dicationry that's being iterated, so we have to make a temporarly list of entrys to remove later
        if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
            remove_list = deque()
            fin_list = deque()
            for stream_id in systems[system].STATUS:
                
                #if stream already marked as finished, just remove it
                if '_fin' in systems[system].STATUS[stream_id] and systems[system].STATUS[stream_id]['LAST'] < _now - 180:
                    logger.debug('(%s) *FINISHED STREAM* STREAM ID: %s',system, int_id(stream_id))
                    fin_list.append(stream_id)
                    continue
                
                try:
                    if '_to' not in systems[system].STATUS[stream_id] and '_fin' not in systems[system].STATUS[stream_id] and systems[system].STATUS[stream_id]['LAST'] < _now - 5:
                        _stream = systems[system].STATUS[stream_id]
                        _sysconfig = CONFIG['SYSTEMS'][system]
                        #systems[system].STATUS[stream_id]['_fin'] = True
                        if '_bcsq' in _sysconfig and _stream['TGID'] in _sysconfig['_bcsq'] and _sysconfig['_bcsq'][_stream['TGID']] == stream_id:
                            logger.debug('(%s) *TIME OUT*   STREAM ID: %s SUB: %s PEER: %s TGID: %s TS 1 (BCSQ)', \
                                system, int_id(stream_id), get_alias(int_id(_stream['RFS']), subscriber_ids), get_alias(int_id(_stream['RX_PEER']), peer_ids), get_alias(int_id(_stream['TGID']), talkgroup_ids))
                        elif '_bcsq' in systems[system].STATUS[stream_id] :
                            logger.debug('(%s) *TIME OUT*   STREAM ID: %s SUB: %s PEER: %s TGID: %s TS 1 (BCSQ)', \
                                system, int_id(stream_id), get_alias(int_id(_stream['RFS']), subscriber_ids), get_alias(int_id(_stream['RX_PEER']), peer_ids), get_alias(int_id(_stream['TGID']), talkgroup_ids))
                        else:
                            if 'loss' in _stream and 'packets' in _stream and _stream['packets']:
                                loss = _stream['loss'] / _stream['packets'] * 100
                                #Only report this at INFO level if it has loss information as this will be a source
                                #stream not a target stream
                                #These represent streams where the stream has been lost - i.e. no TERM packet.
                                logger.info('(%s) *TIME OUT - STREAM LOST*   STREAM ID: %s SUB: %s PEER: %s TGID: %s TS 1 Duration: %.2f, Loss: %.2f%%', \
                                    system, int_id(stream_id), get_alias(int_id(_stream['RFS']), subscriber_ids), get_alias(int_id(_stream['RX_PEER']), peer_ids), get_alias(int_id(_stream['TGID']), talkgroup_ids), _stream['LAST'] - _stream['START'],loss)
                            else:
                                logger.debug('(%s) *TIME OUT*   STREAM ID: %s SUB: %s PEER: %s TGID: %s TS 1 Duration: %.2f', \
                                    system, int_id(stream_id), get_alias(int_id(_stream['RFS']), subscriber_ids), get_alias(int_id(_stream['RX_PEER']), peer_ids), get_alias(int_id(_stream['TGID']), talkgroup_ids), _stream['LAST'] - _stream['START'])
                            
                        if CONFIG['REPORTS']['REPORT'] and should_report_stream_end(_stream):
                                systems[system]._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(stream_id), int_id(_stream['RX_PEER']), int_id(_stream['RFS']), 1, int_id(_stream['TGID']), _stream['LAST'] - _stream['START']).encode(encoding='utf-8', errors='ignore'))
                        systems[system].STATUS[stream_id]['_to'] = True
                        continue
                except Exception as e:
                    logger.exception("(%s) Keyerror - stream trimmer Stream ID: %s",system,stream_id, exc_info=e)
                    # Remove corrupt STATUS instead of bumping LAST (which delayed cleanup 180s)
                    remove_list.append(stream_id)
                    continue

                    
                try:
                    if systems[system].STATUS[stream_id]['LAST'] < _now - 180:
                        remove_list.append(stream_id)
                except Exception as e:
                    logger.exception("(%s) Keyerror - stream trimmer Stream ID: %s",system,stream_id, exc_info=e)
                    remove_list.append(stream_id)
                    continue
                
            #remove finished     
            for stream_id in fin_list:
                removed = systems[system].STATUS.pop(stream_id)
                
            for stream_id in remove_list:
                if stream_id in systems[system].STATUS:
                    _stream = systems[system].STATUS[stream_id]
                    _sysconfig = CONFIG['SYSTEMS'][system]
                    
                    removed = systems[system].STATUS.pop(stream_id)
                
                    try:
                        _bcsq_remove = deque()
                        for tgid in _sysconfig['_bcsq']:
                            if _sysconfig['_bcsq'][tgid] == stream_id:
                                _bcsq_remove.append(tgid)
                        for bcrm in _bcsq_remove:
                            removed = _sysconfig['_bcsq'].pop(bcrm)
                    except KeyError:
                        pass
                else:
                    logger.debug('(%s) Attemped to remove OpenBridge Stream ID %s not in the Stream ID list: %s', system, int_id(stream_id), [id for id in systems[system].STATUS])

def sendVoicePacket(self, pkt, _source_id, _dest_id, _slot):
    _system = self._system
    _stream_id = pkt[16:20]
    _pkt_time = time()
    if _slot.get('TX_PROMPT_CANCEL', False):
        return
    if _stream_id not in systems[_system].STATUS:
        systems[_system].STATUS[_stream_id] = {
            'START': _pkt_time,
            'CONTENTION': False,
            'RFS': _source_id,
            'TGID': _dest_id,
            'LAST': _pkt_time,
        }
        _slot['TX_TGID'] = _dest_id
    else:
        systems[_system].STATUS[_stream_id]['LAST'] = _pkt_time
        _slot['TX_TIME'] = _pkt_time
    _slot['TX_PROMPT_ACTIVE'] = True
    _slot['TX_PROMPT_TIME'] = _pkt_time
    _slot['TX_PROMPT_STREAM_ID'] = _stream_id
    _slot['TX_PROMPT_TGID'] = _dest_id
    _slot['TX_PROMPT_RFS'] = _source_id

    self.send_system(pkt)

def sendSpeech(self, speech):
    logger.debug('(%s) Inside sendspeech thread', self._system)
    sleep(1)
    _nine = bytes_3(9)
    _source_id = bytes_3(5000)
    _slot = systems[self._system].STATUS[2]
    _prompt_token = begin_generated_voice(_slot)
    while True:
        if generated_voice_cancelled(_slot, _prompt_token):
            break
        try:
            pkt = next(speech)
        except StopIteration:
            break
        #Packet every 60ms
        sleep(0.058)
        if generated_voice_cancelled(_slot, _prompt_token):
            break
        reactor.callFromThread(sendVoicePacket,self,pkt,_source_id,_nine,_slot)

    end_generated_voice(_slot, _prompt_token)
    logger.debug('(%s) Sendspeech thread ended',self._system)

def disconnectedVoice(system):
    _say = _build_disconnect_say(system)
    if CONFIG['SYSTEMS'][system]['MODE'] == 'IPSC':
        _master = systems.get(system)
        if _master is None or not hasattr(_master, 'ipsc_reflector_speech'):
            logger.warning('(%s) IPSC reflector timeout — no router for disconnect voice', system)
            return
        logger.info('(%s) IPSC reflector timeout — sending disconnect voice to subscribers', system)
        for _subscriber, _entry in list(SUB_MAP.items()):
            try:
                if _entry[0] != system:
                    continue
                _slot = _entry[1]
                _peer_id = _entry[4] if len(_entry) >= 5 else None
            except (TypeError, IndexError):
                continue
            if not _peer_id:
                _peer_id = _master.STATUS.get(_slot, {}).get('RX_PEER')
            if not _peer_id:
                continue
            _master._reflector_speech_gen = getattr(_master, '_reflector_speech_gen', 0) + 1
            _gen = _master._reflector_speech_gen
            hbp_slot = 1 if _slot == 2 else 0
            speech = pkt_gen(
                bytes_3(5000), _subscriber, _peer_id, hbp_slot, _say, private_call=True)
            reactor.callInThread(
                _master.ipsc_reflector_speech, speech, _slot, _peer_id, _gen, 5000)
        return

    _nine = bytes_3(9)
    _source_id = bytes_3(5000)
    logger.debug('(%s) Sending disconnected voice', system)
    speech = pkt_gen(_source_id, _nine, bytes_4(9), 1, _say)

    sleep(1)
    _master = systems[system]
    _slot = _master.STATUS[2]
    _prompt_token = begin_generated_voice(_slot)
    while True:
        if generated_voice_cancelled(_slot, _prompt_token):
            break
        try:
            pkt = next(speech)
        except StopIteration:
            break
        sleep(0.058)
        if generated_voice_cancelled(_slot, _prompt_token):
            break
        reactor.callFromThread(sendVoicePacket, _master, pkt, _source_id, _nine, _slot)

    end_generated_voice(_slot, _prompt_token)
    logger.debug('(%s) disconnected voice thread end', system)

def playFileOnRequest(self,fileNumber):
    system = self._system
    _lang = CONFIG['SYSTEMS'][system]['ANNOUNCEMENT_LANGUAGE']
    _nine = bytes_3(9)
    _source_id = bytes_3(5000)
    logger.debug('(%s) Sending contents of AMBE file: %s',system,fileNumber)
    sleep(1)
    _say = []
    try:
        _say.append(AMBEobj.readSingleFile(''.join(['/',_lang,'/ondemand/',str(fileNumber),'.ambe'])))
    except IOError:
        logger.warning('(%s) cannot read file for number %s',system,fileNumber)
        return
    speech = pkt_gen(_source_id, _nine, bytes_4(9), 1, _say)
    sleep(1)
    _slot  = systems[system].STATUS[2]
    _prompt_token = begin_generated_voice(_slot)
    while True:
        if generated_voice_cancelled(_slot, _prompt_token):
            break
        try:
            pkt = next(speech)
        except StopIteration:
                break
        #Packet every 60ms
        sleep(0.058)
        if generated_voice_cancelled(_slot, _prompt_token):
            break
        reactor.callFromThread(sendVoicePacket,self,pkt,_source_id,_nine,_slot)
    end_generated_voice(_slot, _prompt_token)
    logger.debug('(%s) Sending AMBE file %s end',system,fileNumber)

    

def threadIdent():
    logger.debug('(IDENT) starting ident thread')
    reactor.callInThread(ident)
    
def threadAlias():
    logger.debug('(ALIAS) starting alias thread')
    reactor.callInThread(aliasb)

def setAlias(_peer_ids,_subscriber_ids, _talkgroup_ids, _local_subscriber_ids, _server_ids):
    peer_ids, subscriber_ids, talkgroup_ids,local_subscriber_ids,server_ids = _peer_ids, _subscriber_ids, _talkgroup_ids, _local_subscriber_ids,_server_ids
    
def aliasb():
    _peer_ids, _subscriber_ids, _talkgroup_ids, _local_subscriber_ids, _server_ids = mk_aliases(CONFIG)
    reactor.callFromThread(setAlias,_peer_ids, _subscriber_ids, _talkgroup_ids, _local_subscriber_ids, _server_ids)

def ident():
    for system in systems:
        if CONFIG['SYSTEMS'][system]['MODE'] != 'MASTER':
            continue
        if CONFIG['SYSTEMS'][system]['VOICE_IDENT'] == True:
            _lang = CONFIG['SYSTEMS'][system]['ANNOUNCEMENT_LANGUAGE']
            if CONFIG['SYSTEMS'][system]['MAX_PEERS'] > 1:
                logger.debug("(IDENT) %s System has MAX_PEERS > 1, skipping",system)
                continue
            _callsign = False
            for _peerid in CONFIG['SYSTEMS'][system]['PEERS']:
                if CONFIG['SYSTEMS'][system]['PEERS'][_peerid]['CALLSIGN']:
                    _callsign = CONFIG['SYSTEMS'][system]['PEERS'][_peerid]['CALLSIGN'].decode()
            if not _callsign:
                logger.debug("(IDENT) %s System has no peers or no recorded callsign (%s), skipping",system,_callsign)
                continue
            _slot  = systems[system].STATUS[2]
            #If slot is idle for RX and TX for over 30 seconds
            if (_slot['RX_TYPE'] == HBPF_SLT_VTERM) and (_slot['TX_TYPE'] == HBPF_SLT_VTERM) and (time() - _slot['TX_TIME'] > 30 and time() - _slot['RX_TIME'] > 30):
                _all_call = bytes_3(16777215)
                _source_id= bytes_3(5000)

                _dst_id = b''
                
                if 'OVERRIDE_IDENT_TG' in CONFIG['SYSTEMS'][system] and CONFIG['SYSTEMS'][system]['OVERRIDE_IDENT_TG'] and int(CONFIG['SYSTEMS'][system]['OVERRIDE_IDENT_TG']) > 0 and int(CONFIG['SYSTEMS'][system]['OVERRIDE_IDENT_TG'] < 16777215):
                    _dst_id = bytes_3(CONFIG['SYSTEMS'][system]['OVERRIDE_IDENT_TG'])
                else:
                    _dst_id = _all_call
                logger.info('(%s) %s System idle. Sending voice ident to TG %s',system,_callsign,get_alias(_dst_id,talkgroup_ids))
                _say = [words[_lang]['silence']]
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['this-is'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                
                _systemcs = re.sub(r'\W+', '', _callsign)
                _systemcs.upper()
                for character in _systemcs:
                    _say.append(words[_lang][character])
                    _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                _say.append(words[_lang]['silence'])
                
                # _say.append(words[_lang]['freedmr'])
                
                #test 
                #_say.append(AMBEobj.readSingleFile('alpha.ambe'))

                _peer_id = CONFIG['GLOBAL']['SERVER_ID']
                speech = pkt_gen(_source_id, _dst_id, _peer_id, 1, _say)

                sleep(1)
                _slot  = systems[system].STATUS[2]
                while True:
                    try:
                        pkt = next(speech)
                    except StopIteration:
                            break
                    #Packet every 60ms
                    sleep(0.058)
                    
                    _stream_id = pkt[16:20]
                    _pkt_time = time()
                    reactor.callFromThread(sendVoicePacket,systems[system],pkt,_source_id,_dst_id,_slot)

def options_config():
    logger.debug('(OPTIONS) Running options parser')
    for _system in CONFIG['SYSTEMS']:
        if '_reset' in  CONFIG['SYSTEMS'][_system] and CONFIG['SYSTEMS'][_system]['_reset']:
            logger.info('(OPTIONS) Bridge reset for %s - no peers', _system)
            _opt_str = CONFIG['SYSTEMS'][_system].get('OPTIONS', '')
            if _opt_str:
                _ts1, _ts2 = parse_options_static_fields(_opt_str)
                CONFIG['SYSTEMS'][_system]['TS1_STATIC'] = _ts1 if _ts1 else False
                CONFIG['SYSTEMS'][_system]['TS2_STATIC'] = _ts2 if _ts2 else False
            remove_bridge_system(_system, preserve_static_legs=False)
            CONFIG['SYSTEMS'][_system]['_reset'] = False
            continue
        try:
            _mode = CONFIG['SYSTEMS'][_system]['MODE']
            if _mode not in ('MASTER', 'IPSC'):
                continue
            if CONFIG['SYSTEMS'][_system]['ENABLED'] == True:
                # Process per-peer OPTIONS first (MMDVM hotspots on MASTER only)
                if _mode == 'MASTER' and 'PEERS' in CONFIG['SYSTEMS'][_system]:
                    for _peer_id in CONFIG['SYSTEMS'][_system]['PEERS']:
                        _peer = CONFIG['SYSTEMS'][_system]['PEERS'][_peer_id]
                        if 'OPTIONS' in _peer and _peer['OPTIONS']:
                            try:
                                _peer_options_str = _peer['OPTIONS'].decode() if isinstance(_peer['OPTIONS'], bytes) else str(_peer['OPTIONS'])
                                _peer_options_str = _peer_options_str.rstrip('\x00')
                                _peer_options_str = _peer_options_str.encode('ascii', 'ignore').decode()
                                _peer_options_str = re.sub("\'","",_peer_options_str)
                                _peer_options_str = re.sub("\"","",_peer_options_str)
                                
                                # Parse STICKY / LINK_IPSC from peer OPTIONS
                                for x in _peer_options_str.split(";"):
                                    try:
                                        k, v = x.split('=')
                                        if k == 'STICKY':
                                            # Validate and store per-peer STICKY setting
                                            # Accept: "1", "0", "true", "false", "yes", "no"
                                            if v.lower() in ['1', 'true', 'yes']:
                                                _peer['STICKY'] = True
                                            elif v.lower() in ['0', 'false', 'no']:
                                                _peer['STICKY'] = False
                                            else:
                                                logger.warning('(OPTIONS) %s - Peer %s invalid STICKY value "%s", ignoring', 
                                                             _system, int_id(_peer_id), v)
                                                continue
                                            logger.info('(OPTIONS) %s - Peer %s set STICKY=%s', _system, int_id(_peer_id), _peer['STICKY'])
                                        elif k in ('IPSC', 'LINK_IPSC'):
                                            _link_slot = v.strip()
                                            if (_link_slot in CONFIG['SYSTEMS']
                                                    and CONFIG['SYSTEMS'][_link_slot]['MODE'] == 'IPSC'):
                                                _peer['LINK_IPSC'] = _link_slot
                                                logger.info('(OPTIONS) %s - Peer %s set LINK_IPSC=%s',
                                                            _system, int_id(_peer_id), _link_slot)
                                            else:
                                                logger.warning('(OPTIONS) %s - Peer %s invalid LINK_IPSC "%s", ignoring',
                                                             _system, int_id(_peer_id), _link_slot)
                                    except (ValueError, KeyError):
                                        continue
                            except Exception as e:
                                logger.debug('(OPTIONS) %s - Error parsing peer %s OPTIONS: %s', _system, int_id(_peer_id), e)
                
                if 'OPTIONS' in CONFIG['SYSTEMS'][_system]:
                    _options = {}
                    CONFIG['SYSTEMS'][_system]['OPTIONS'] = CONFIG['SYSTEMS'][_system]['OPTIONS'].rstrip('\x00')
                    CONFIG['SYSTEMS'][_system]['OPTIONS'] = CONFIG['SYSTEMS'][_system]['OPTIONS'].encode('ascii', 'ignore').decode()
                    CONFIG['SYSTEMS'][_system]['OPTIONS'] = re.sub("\'","",CONFIG['SYSTEMS'][_system]['OPTIONS'])
                    CONFIG['SYSTEMS'][_system]['OPTIONS'] = re.sub("\"","",CONFIG['SYSTEMS'][_system]['OPTIONS'])
                    for x in CONFIG['SYSTEMS'][_system]['OPTIONS'].split(";"):
                        try:
                            k,v = x.split('=')
                        except ValueError:
                            #logger.debug('(OPTIONS) Value error %s ignoring %s %s',_system,k,v)
                            continue
                        if k == 'DISC':
                            continue
                        _options[k] = v
                    logger.debug('(OPTIONS) Options found for %s',_system)

                    _link_slot = _options.get('IPSC') or _options.get('LINK_IPSC')
                    if _link_slot:
                        _link_slot = _link_slot.strip()
                        if (_link_slot in CONFIG['SYSTEMS']
                                and CONFIG['SYSTEMS'][_link_slot]['MODE'] == 'IPSC'):
                            CONFIG['SYSTEMS'][_system]['LINK_IPSC'] = _link_slot
                            logger.info('(OPTIONS) %s - LINK_IPSC=%s', _system, _link_slot)
                        else:
                            logger.warning('(OPTIONS) %s - invalid LINK_IPSC "%s", ignoring',
                                           _system, _link_slot)
                    
                    if 'DIAL' in _options:
                        _options['DEFAULT_REFLECTOR'] = _options.pop('DIAL')
                    if 'TIMER' in _options:
                        _options['DEFAULT_UA_TIMER'] = _options.pop('TIMER')
                    if 'TS1' in _options:
                        _options['TS1_STATIC'] = _options.pop('TS1')
                    if 'TS2' in _options:
                        _options['TS2_STATIC'] = _options.pop('TS2')
                    if 'IDENTTG' in _options:
                        _options['OVERRIDE_IDENT_TG'] = _options.pop('IDENTTG')
                    elif 'VOICETG' in _options:
                        _options['OVERRIDE_IDENT_TG'] = _options.pop('VOICETG')                         
                    if 'IDENT' in _options:
                        _options['VOICE'] = _options.pop('IDENT')
                     
                    #DMR+ style options
                    if 'StartRef' in _options:
                        _options['DEFAULT_REFLECTOR'] = _options.pop('StartRef')
                    if 'RelinkTime' in _options:
                        # IPSC2 / DMR+ selfcare name for UA relink timer (minutes)
                        _options['DEFAULT_UA_TIMER'] = _options.pop('RelinkTime')
                    if 'TS1_1' in _options:
                        _options['TS1_STATIC'] = _options.pop('TS1_1')
                        if 'TS1_2' in _options:
                            _options['TS1_STATIC'] = ''.join([_options['TS1_STATIC'],',',_options.pop('TS1_2')])
                        if 'TS1_3' in _options:
                            _options['TS1_STATIC'] = ''.join([_options['TS1_STATIC'],',',_options.pop('TS1_3')])
                        if 'TS1_4' in _options:
                            _options['TS1_STATIC'] = ''.join([_options['TS1_STATIC'],',',_options.pop('TS1_4')])
                        if 'TS1_5' in _options:
                            _options['TS1_STATIC'] = ''.join([_options['TS1_STATIC'],',',_options.pop('TS1_5')])
                        if 'TS1_6' in _options:
                            _options['TS1_STATIC'] = ''.join([_options['TS1_STATIC'],',',_options.pop('TS1_6')])
                        if 'TS1_7' in _options:
                            _options['TS1_STATIC'] = ''.join([_options['TS1_STATIC'],',',_options.pop('TS1_7')])
                        if 'TS1_8' in _options:
                            _options['TS1_STATIC'] = ''.join([_options['TS1_STATIC'],',',_options.pop('TS1_8')])
                        if 'TS1_9' in _options:
                            _options['TS1_STATIC'] = ''.join([_options['TS1_STATIC'],',',_options.pop('TS1_9')])
                    if 'TS2_1' in _options:
                        _options['TS2_STATIC'] = _options.pop('TS2_1')
                        if 'TS2_2' in _options:
                            _options['TS2_STATIC'] = ''.join([_options['TS2_STATIC'],',', _options.pop('TS2_2')])
                        if 'TS2_3' in _options:
                            _options['TS2_STATIC'] = ''.join([_options['TS2_STATIC'],',',_options.pop('TS2_3')])
                        if 'TS2_4' in _options:
                            _options['TS2_STATIC'] = ''.join([_options['TS2_STATIC'],',',_options.pop('TS2_4')])
                        if 'TS2_5' in _options:
                            _options['TS2_STATIC'] = ''.join([_options['TS2_STATIC'],',',_options.pop('TS2_5')])
                        if 'TS2_6' in _options:
                            _options['TS2_STATIC'] = ''.join([_options['TS2_STATIC'],',',_options.pop('TS2_6')])
                        if 'TS2_7' in _options:
                            _options['TS2_STATIC'] = ''.join([_options['TS2_STATIC'],',',_options.pop('TS2_7')])
                        if 'TS2_8' in _options:
                            _options['TS2_STATIC'] = ''.join([_options['TS2_STATIC'],',',_options.pop('TS2_8')])
                        if 'TS2_9' in _options:
                            _options['TS2_STATIC'] = ''.join([_options['TS2_STATIC'],',',_options.pop('TS2_9')])

                    if 'UserLink' in _options:
                        _options.pop('UserLink')
                    
                    if 'TS1_STATIC' not in _options:
                        _options['TS1_STATIC'] = False
                    
                    if 'TS2_STATIC' not in _options:
                        _options['TS2_STATIC'] = False

                    # Align with shared BlueDV/DMR+ static parser (drops empty TS2_N=)
                    _ts1_parsed, _ts2_parsed = parse_options_static_fields(
                        CONFIG['SYSTEMS'][_system]['OPTIONS'])
                    if _ts1_parsed is not False:
                        _options['TS1_STATIC'] = _ts1_parsed
                    else:
                        _options['TS1_STATIC'] = normalize_static_tg_csv(
                            _options.get('TS1_STATIC', False))
                    if _ts2_parsed is not False:
                        _options['TS2_STATIC'] = _ts2_parsed
                    else:
                        _options['TS2_STATIC'] = normalize_static_tg_csv(
                            _options.get('TS2_STATIC', False))
                        
                    if 'DEFAULT_REFLECTOR' not in _options:
                        _options['DEFAULT_REFLECTOR'] = 0
                    try:
                        _raw_reflector = int(_options['DEFAULT_REFLECTOR'] or 0)
                    except (TypeError, ValueError):
                        _raw_reflector = 0
                    _options['DEFAULT_REFLECTOR'] = normalize_default_reflector(_raw_reflector)
                    if _raw_reflector and _options['DEFAULT_REFLECTOR'] == 0:
                        logger.info(
                            '(OPTIONS) %s StartRef/DEFAULT_REFLECTOR=%s is a dial service '
                            'code (9/4000/5000), treating as 0 (no default reflector)',
                            _system, _raw_reflector)
                    
                    if 'OVERRIDE_IDENT_TG' not in _options:
                        _options['OVERRIDE_IDENT_TG'] = False
                        
                    if 'DEFAULT_UA_TIMER' not in _options:
                        _options['DEFAULT_UA_TIMER'] = CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER']
                    
                    if 'VOICE' in _options and bool(_options['VOICE']) and (CONFIG['SYSTEMS'][_system]['VOICE_IDENT'] != bool(int(_options['VOICE']))):
                        CONFIG['SYSTEMS'][_system]['VOICE_IDENT'] = bool(int(_options['VOICE']))
                        logger.debug("(OPTIONS) %s - Setting voice ident to %s",_system,CONFIG['SYSTEMS'][_system]['VOICE_IDENT'])
                        
                    if 'OVERRIDE_IDENT_TG' in _options and _options['OVERRIDE_IDENT_TG'] and (CONFIG['SYSTEMS'][_system]['OVERRIDE_IDENT_TG'] != int(_options['OVERRIDE_IDENT_TG'])):
                        CONFIG['SYSTEMS'][_system]['OVERRIDE_IDENT_TG'] = int(_options['OVERRIDE_IDENT_TG'])
                        logger.debug("(OPTIONS) %s - Setting OVERRIDE_IDENT_TG to %s",_system,CONFIG['SYSTEMS'][_system]['OVERRIDE_IDENT_TG'])
                        
                    if 'LANG' in _options and _options['LANG'] in words and _options['LANG'] != CONFIG['SYSTEMS'][_system]['ANNOUNCEMENT_LANGUAGE'] :
                        CONFIG['SYSTEMS'][_system]['ANNOUNCEMENT_LANGUAGE'] = _options['LANG']
                        logger.debug("(OPTIONS) %s - Setting voice language to  %s",_system,CONFIG['SYSTEMS'][_system]['ANNOUNCEMENT_LANGUAGE'])
                        
                        
                    if 'SINGLE' in _options and (CONFIG['SYSTEMS'][_system]['SINGLE_MODE'] != bool(int(_options['SINGLE']))):
                        CONFIG['SYSTEMS'][_system]['SINGLE_MODE'] = bool(int(_options['SINGLE']))
                        logger.debug("(OPTIONS) %s - Setting SINGLE_MODE to %s",_system,CONFIG['SYSTEMS'][_system]['SINGLE_MODE'])
                    
                    if 'TS1_STATIC' not in _options or 'TS2_STATIC' not in _options or 'DEFAULT_REFLECTOR' not in _options or 'DEFAULT_UA_TIMER' not in _options:
                        logger.debug('(OPTIONS) %s - Required field missing, ignoring',_system)
                        continue
                    
                    if _options['TS1_STATIC'] == '':
                        _options['TS1_STATIC'] = False
                    if _options['TS2_STATIC'] == '':
                        _options['TS2_STATIC'] = False
                        
                    if _options['TS1_STATIC']:
                        _options['TS1_STATIC'] = re.sub(r"\s", "", str(_options['TS1_STATIC']))
                        if re.search(r"[^\d,]", _options['TS1_STATIC']):
                            logger.debug('(OPTIONS) %s - TS1_STATIC contains characters other than numbers and comma, ignoring',_system)
                            continue
                    
                    if _options['TS2_STATIC']:
                        _options['TS2_STATIC'] = re.sub(r"\s", "", str(_options['TS2_STATIC']))
                        if re.search(r"[^\d,]", _options['TS2_STATIC']):
                            logger.debug('(OPTIONS) %s - TS2_STATIC contains characters other than numbers and comma, ignoring',_system)
                            continue

                    if is_static_field_keyup_noise(
                            CONFIG['SYSTEMS'][_system].get('TS1_STATIC'), _options['TS1_STATIC']):
                        logger.info(
                            '(OPTIONS) %s ignoring TS1=%s (key-up noise vs static bundle %s)',
                            _system, _options['TS1_STATIC'],
                            CONFIG['SYSTEMS'][_system].get('TS1_STATIC'))
                        _options['TS1_STATIC'] = CONFIG['SYSTEMS'][_system]['TS1_STATIC']
                    if is_static_field_keyup_noise(
                            CONFIG['SYSTEMS'][_system].get('TS2_STATIC'), _options['TS2_STATIC']):
                        logger.info(
                            '(OPTIONS) %s ignoring TS2=%s (key-up noise vs static bundle %s)',
                            _system, _options['TS2_STATIC'],
                            CONFIG['SYSTEMS'][_system].get('TS2_STATIC'))
                        _options['TS2_STATIC'] = CONFIG['SYSTEMS'][_system]['TS2_STATIC']
                    
                    if isinstance(_options['DEFAULT_REFLECTOR'], str) and not _options['DEFAULT_REFLECTOR'].isdigit():
                        logger.debug('(OPTIONS) %s - DEFAULT_REFLECTOR is not an integer, ignoring',_system)
                        continue
                    
                    if isinstance(_options['OVERRIDE_IDENT_TG'], str) and not _options['OVERRIDE_IDENT_TG'].isdigit():
                        logger.debug('(OPTIONS) %s - OVERRIDE_IDENT_TG is not an integer, ignoring',_system)
                        continue
                    
                    
                    if isinstance(_options['DEFAULT_UA_TIMER'], str) and not _options['DEFAULT_UA_TIMER'].isdigit():
                        logger.debug('(OPTIONS) %s - DEFAULT_REFLECTOR is not an integer, ignoring',_system)
                        continue
                        
                    _tmout = int(_options['DEFAULT_UA_TIMER'])
                    
                    if int(_options['DEFAULT_UA_TIMER']) != CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER']:
                        logger.debug('(OPTIONS) %s Updating DEFAULT_UA_TIMER for existing bridges.',_system)
                        remove_bridge_system(_system, new_timeout_s=_tmout * 60)
                        for _bridge in BRIDGES:
                            if not is_valid_talkgroup_bridge(_bridge):
                                continue
                            ts1 = False 
                            ts2 = False
                            for i,e in enumerate(BRIDGES[_bridge]):
                                if e['SYSTEM'] == _system and e['TS'] == 1:
                                    ts1 = True
                                if e['SYSTEM'] == _system and e['TS'] == 2:
                                    ts2 = True
                            if _bridge[0:1] != '#':
                                if ts1 == False:
                                    BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 1, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                                if ts2 == False:
                                    BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 2, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                            else:
                                if ts2 == False:
                                    BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [bytes_3(4000)],'ON': [],'RESET': [], 'TIMER': time()})
                        # Direct appends to BRIDGES above bypass the individual index helpers;
                        # rebuild the full index to restore consistency.
                        rebuild_bridge_index()
                    
                    if int(_options['DEFAULT_REFLECTOR']) != CONFIG['SYSTEMS'][_system]['DEFAULT_REFLECTOR']:
                        if int(_options['DEFAULT_REFLECTOR']) > 0:
                            logger.debug('(OPTIONS) %s default reflector changed, updating',_system) 
                            reset_default_reflector(CONFIG['SYSTEMS'][_system]['DEFAULT_REFLECTOR'],_tmout,_system)
                            make_default_reflector(int(_options['DEFAULT_REFLECTOR']),_tmout,_system)
                        else:
                            logger.debug('(OPTIONS) %s default reflector disabled, updating',_system)
                            reset_default_reflector(CONFIG['SYSTEMS'][_system]['DEFAULT_REFLECTOR'],_tmout,_system)
                    
                    ts1 = []
                    if _options['TS1_STATIC'] != CONFIG['SYSTEMS'][_system]['TS1_STATIC']:
                        _tmout = int(_options['DEFAULT_UA_TIMER'])
                        logger.debug('(OPTIONS) %s TS1 static TGs changed, updating',_system)
                        ts1 = []
                        if CONFIG['SYSTEMS'][_system]['TS1_STATIC']:
                            ts1 = CONFIG['SYSTEMS'][_system]['TS1_STATIC'].split(',')
                            for tg in ts1:
                                if not tg:
                                    continue
                                tg = int(tg)
                                reset_static_tg(tg,1,_tmout,_system)   
                        if _options['TS1_STATIC']:
                            ts1 = _options['TS1_STATIC'].split(',')
                            for tg in ts1:
                                if not tg:
                                    continue
                                tg = int(tg)
                                make_static_tg(tg,1,_tmout,_system)
                    ts2 = []
                    if _options['TS2_STATIC'] != CONFIG['SYSTEMS'][_system]['TS2_STATIC']:
                        _tmout = int(_options['DEFAULT_UA_TIMER'])
                        logger.debug('(OPTIONS) %s TS2 static TGs changed, updating',_system)
                        if CONFIG['SYSTEMS'][_system]['TS2_STATIC']:
                            ts2 = CONFIG['SYSTEMS'][_system]['TS2_STATIC'].split(',')
                            for tg in ts2:
                                if not tg or int(tg) == 0 or int(tg) >= 16777215:
                                    continue
                                tg = int(tg)
                                reset_static_tg(tg,2,_tmout,_system)
                        ts2 = []
                        if _options['TS2_STATIC']:
                            ts2 = _options['TS2_STATIC'].split(',')
                            for tg in ts2:
                                if not tg or int(tg) == 0 or int(tg) >= 16777215:
                                    continue
                                tg = int(tg)
                                make_static_tg(tg,2,_tmout,_system)
                    
                    CONFIG['SYSTEMS'][_system]['TS1_STATIC'] =  _options['TS1_STATIC']
                    CONFIG['SYSTEMS'][_system]['TS2_STATIC'] = _options['TS2_STATIC']
                    CONFIG['SYSTEMS'][_system]['DEFAULT_REFLECTOR'] = int(_options['DEFAULT_REFLECTOR'])
                    CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER'] = int(_options['DEFAULT_UA_TIMER'])
                    ensure_static_tgs_for_system(_system, _tmout)
        except Exception as e:
            logger.exception('(OPTIONS) caught exception: %s',e)
            continue


_selfcare_db = None


@inlineCallbacks
def ipsc_selfcare_poll():
    """Apply selfcare TS1/TS2 options for connected IPSC repeaters (mode = 0)."""
    ss = CONFIG.get('SELF SERVICE', {})
    if not ss.get('ENABLED') or _selfcare_db is None:
        return
    try:
        rows = yield _selfcare_db.select_modified_ipsc()
        if not rows:
            return
        for int_id_val, options in rows:
            opt_str = (options.decode('utf-8', errors='ignore')
                       if isinstance(options, bytes) else str(options))
            if not opt_str or not opt_str.strip():
                logger.warning(
                    '(SELF SERVICE) IPSC int_id %s modified but options empty — clearing flag',
                    int_id_val)
                yield _selfcare_db.clear_modified(int_id_val)
                continue
            slot, peer_id = find_ipsc_peer_for_radio_id(CONFIG['SYSTEMS'], int_id_val)
            if not slot:
                logger.warning(
                    '(SELF SERVICE) IPSC int_id %s modified but no connected IPSC slot',
                    int_id_val)
                continue
            CONFIG['SYSTEMS'][slot]['OPTIONS'] = opt_str
            remaining, had_disc = apply_selfcare_options(slot, peer_id, opt_str)
            if had_disc:
                CONFIG['SYSTEMS'][slot]['OPTIONS'] = remaining
                yield _selfcare_db.save_client_options(int_id_val, remaining)
            try:
                if remaining or not had_disc:
                    options_config()
            except Exception:
                logger.exception(
                    '(SELF SERVICE) options_config failed for IPSC %s on %s',
                    int_id_val, slot)
                continue
            yield _selfcare_db.clear_modified(int_id_val)
            logger.info('(SELF SERVICE) Applied options for IPSC %s on %s: %s',
                        int_id_val, slot, remaining if had_disc else opt_str)
    except Exception as err:
        logger.exception('(SELF SERVICE) poll error: %s', err)


@inlineCallbacks
def hotspot_selfcare_static_reconcile():
    """Repair selfcare static bundle after key-up noise overwrote CONFIG (not peer login Options=)."""
    ss = CONFIG.get('SELF SERVICE', {})
    if not ss.get('ENABLED') or _selfcare_db is None:
        return
    try:
        for system, syscfg in CONFIG['SYSTEMS'].items():
            if syscfg.get('MODE') != 'MASTER' or not syscfg.get('ENABLED'):
                continue
            connected = [(pid, p) for pid, p in (syscfg.get('PEERS') or {}).items()
                         if p.get('CONNECTION') == 'YES']
            if len(connected) != 1:
                continue
            peer_id, _peer = connected[0]
            rows = yield _selfcare_db.select_hotspot_options(peer_id)
            if not rows:
                continue
            options = rows[0][0]
            opt_str = (options.decode('utf-8', errors='ignore')
                       if isinstance(options, bytes) else str(options))
            if not opt_str.strip():
                continue
            db_ts1, db_ts2 = parse_options_static_fields(opt_str)
            cfg_ts1 = syscfg.get('TS1_STATIC') or False
            cfg_ts2 = syscfg.get('TS2_STATIC') or False
            if not is_static_field_keyup_noise(db_ts2, cfg_ts2):
                if not is_static_field_keyup_noise(db_ts1, cfg_ts1):
                    continue
            if (parse_static_tg_list(db_ts1) == parse_static_tg_list(cfg_ts1)
                    and parse_static_tg_list(db_ts2) == parse_static_tg_list(cfg_ts2)):
                continue
            logger.info(
                '(SELF SERVICE) Hotspot static key-up noise on %s — restoring selfcare statics (cfg TS2=%s, db TS2=%s)',
                system, cfg_ts2, db_ts2)
            CONFIG['SYSTEMS'][system]['OPTIONS'] = opt_str
            try:
                options_config()
            except Exception:
                logger.exception(
                    '(SELF SERVICE) options_config failed during hotspot reconcile for %s', system)
    except Exception as err:
        logger.exception('(SELF SERVICE) hotspot static reconcile error: %s', err)


@inlineCallbacks
def hotspot_selfcare_disc_poll():
    """Apply DISC=1 from MariaDB for hotspots without waiting for proxy RPTO."""
    ss = CONFIG.get('SELF SERVICE', {})
    if not ss.get('ENABLED') or _selfcare_db is None:
        return
    try:
        rows = yield _selfcare_db.select_hotspot_disc_pending()
        if not rows:
            return
        for int_id_val, options in rows:
            opt_str = (options.decode('utf-8', errors='ignore')
                       if isinstance(options, bytes) else str(options))
            if not opt_str or not selfcare_disconnect_requested(opt_str):
                continue
            system, peer_id = find_hotspot_master_peer(CONFIG['SYSTEMS'], int_id_val)
            if not system:
                logger.warning(
                    '(SELF SERVICE) Hotspot int_id %s DISC=1 but no connected master peer',
                    int_id_val)
                continue
            remaining, had_disc = apply_selfcare_options(system, peer_id, opt_str)
            CONFIG['SYSTEMS'][system]['OPTIONS'] = remaining
            yield _selfcare_db.save_client_options(int_id_val, remaining)
            if remaining:
                try:
                    options_config()
                except Exception:
                    logger.exception(
                        '(SELF SERVICE) options_config failed after hotspot DISC for %s',
                        int_id_val)
                    continue
            yield _selfcare_db.clear_modified_client(int_id_val)
            logger.info('(SELF SERVICE) Hotspot disconnect applied for int_id %s on %s',
                        int_id_val, system)
    except Exception as err:
        logger.exception('(SELF SERVICE) hotspot disc poll error: %s', err)


class routerOBP(OPENBRIDGE):

    def __init__(self, _name, _config, _report):
        OPENBRIDGE.__init__(self, _name, _config, _report)
        self.STATUS = {}
        
    def get_rptr(self,_sid):
        _int_peer_id = int_id(_sid)
        if _int_peer_id in local_subscriber_ids:
            return local_subscriber_ids[_int_peer_id]
        elif _int_peer_id in subscriber_ids:
            return subscriber_ids[_int_peer_id]
        elif _int_peer_id in peer_ids:
            return peer_ids[_int_peer_id]
        else:
            return _int_peer_id

                
    def to_target(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,_noOBP,sysIgnore, _hops = b'', _source_server = b'\x00\x00\x00\x00', _ber = b'\x00', _rssi = b'\x00', _source_rptr = b'\x00\x00\x00\x00'):
        _sysIgnore = sysIgnore
        for _target in BRIDGES[_bridge]:
            if (_target['SYSTEM'] != self._system) and (_target['ACTIVE']):
                _target_status = systems[_target['SYSTEM']].STATUS
                _target_system = self._CONFIG['SYSTEMS'][_target['SYSTEM']]
                if (_target['SYSTEM'],_target['TS']) in _sysIgnore:
                    #logger.debug("(DEDUP) OBP Source Skipping system %s TS: %s",_target['SYSTEM'],_target['TS'])
                    continue
                if _target_system['MODE'] == 'OPENBRIDGE':
                    if _noOBP == True or is_parrot_bridge(_bridge):
                        continue
                    # Peer already hearing this stream inbound — skip mesh re-fanout TX
                    if obp_target_already_has_inbound(_target_status, _stream_id, _dst_id):
                        _sysIgnore.append((_target['SYSTEM'], _target['TS']))
                        continue
                    #We want to ignore this system and TS combination if it's called again for this packet
                    _sysIgnore.append((_target['SYSTEM'],_target['TS']))
        
                    #If target has quenched us, don't send
                    if ('_bcsq' in _target_system) and (_dst_id in _target_system['_bcsq']) and (_target_system['_bcsq'][_dst_id] == _stream_id):
                        #logger.info('(%s) Conference Bridge: %s, is Source Quenched for Stream ID: %s, skipping system: %s TS: %s, TGID: %s', self._system, _bridge, int_id(_stream_id), _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                        continue
                    
                    #If target has missed 6 (on 1 min) of keepalives, don't send
                    if _target_system['ENHANCED_OBP'] and ('_bcka' not in _target_system or _target_system['_bcka'] < pkt_time - 60):
                        continue
                    
                    #If talkgroup is prohibited by ACL
                    if self._CONFIG['GLOBAL']['USE_ACL']:
                        if not acl_check(_target['TGID'], self._CONFIG['GLOBAL']['TG1_ACL']):
                            #logger.info('(%s) TGID prohibited by ACL, not sending', _target['SYSTEM'], int_id(_dst_id))
                            continue
                        
                        if not acl_check(_target['TGID'],_target_system['TG1_ACL']):
                            #logger.info('(%s) TGID prohibited by ACL, not sending', _target['SYSTEM'])
                            continue
                        
                    
                    # Is this a new call stream on the target?
                    if (_stream_id not in _target_status):
                        # This is a new call stream on the target
                        _target_status[_stream_id] = {
                            'START':     pkt_time,
                            'CONTENTION':False,
                            'RFS':       _rf_src,
                            'TGID':      _dst_id,
                            'RX_PEER': _peer_id,
                            '_outbound': True,
                            'packets': 0,
                            'loss': 0,

                        }
                        # Generate LCs (full and EMB) for the TX stream
                        _src_lc = LC_OPT
                        if _stream_id in self.STATUS and 'LC' in self.STATUS[_stream_id]:
                            _src_lc = self.STATUS[_stream_id]['LC'][0:3]
                        dst_lc = b''.join([_src_lc, _target['TGID'], _rf_src])
                        _target_status[_stream_id]['H_LC'] = bptc.encode_header_lc(dst_lc)
                        _target_status[_stream_id]['T_LC'] = bptc.encode_terminator_lc(dst_lc)
                        _target_status[_stream_id]['EMB_LC'] = bptc.encode_emblc(dst_lc)

                        logger.debug('(%s) Conference Bridge: %s, Call Bridged to OBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                        if CONFIG['REPORTS']['REPORT']:
                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))

                    # Record the time of this packet so we can later identify a stale stream
                    _target_status[_stream_id]['LAST'] = pkt_time
                    # Clear the TS bit -- all OpenBridge streams are effectively on TS1
                    _tmp_bits = _bits & ~(1 << 7)

                    # Assemble transmit HBP packet header
                    _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                    # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                    # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                    # if _dst_id != rule['DST_GROUP']:
                    # Per-target copy — never mutate shared dmrpkt across fanout legs
                    _tx_dmrpkt = dmrpkt
                    dmrbits = bitarray(endian='big')
                    dmrbits.frombytes(_tx_dmrpkt)
                    if 'H_LC' not in _target_status.get(_stream_id, {}):
                        _src_lc = LC_OPT
                        if _stream_id in self.STATUS and 'LC' in self.STATUS[_stream_id]:
                            _src_lc = self.STATUS[_stream_id]['LC'][0:3]
                        _dst_lc = b''.join([_src_lc, _target['TGID'], _rf_src])
                        _target_status[_stream_id]['H_LC'] = bptc.encode_header_lc(_dst_lc)
                        _target_status[_stream_id]['T_LC'] = bptc.encode_terminator_lc(_dst_lc)
                        _target_status[_stream_id]['EMB_LC'] = bptc.encode_emblc(_dst_lc)
                    # Create a voice header packet (FULL LC) — FreeDMR: always rewrite
                    if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                        try:
                            dmrbits = _target_status[_stream_id]['H_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['H_LC'][98:197]
                        except KeyError:
                            logger.debug('(%s) KeyError - H_LC, sending original bits', self._system)
                    # Create a voice terminator packet (FULL LC) — FreeDMR: always rewrite
                    elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                        try:
                            dmrbits = _target_status[_stream_id]['T_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['T_LC'][98:197]
                        except KeyError:
                            logger.debug('(%s) KeyError - T_LC, sending original bits', self._system)
                        if CONFIG['REPORTS']['REPORT']:
                            call_duration = pkt_time - _target_status[_stream_id]['START']
                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                    # Create a Burst B-E packet (Embedded LC) — FreeDMR: only when TG remaps
                    elif target_requires_emb_lc_rewrite(_dst_id, _target['TGID']) and _frame_type == HBPF_VOICE and _dtype_vseq in [1,2,3,4]:
                        try:
                            dmrbits = dmrbits[0:116] + _target_status[_stream_id]['EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                        except KeyError:
                            logger.debug('(%s) KeyError - EMB_LC, skipping', self._system)
                            continue
                    _tx_dmrpkt = dmrbits.tobytes()
                    _tmp_data = b''.join([_tmp_data, _tx_dmrpkt])

                else:
                    # BEGIN CONTENTION HANDLING
                    #
                    # The rules for each of the 4 "ifs" below are listed here for readability. The Frame To Send is:
                    #   From a different group than last RX from this HBSystem, but it has been less than Group Hangtime
                    #   From a different group than last TX to this HBSystem, but it has been less than Group Hangtime
                    #   From the same group as the last RX from this HBSystem, but from a different subscriber, and it has been less than stream timeout
                    #   From the same group as the last TX to this HBSystem, but from a different subscriber, and it has been less than stream timeout
                    # The "continue" at the end of each means the next iteration of the for loop that tests for matching rules
                    #
                    if ((_target['TGID'] != _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < _target_system['GROUP_HANGTIME'])):
                        if _stream_id in self.STATUS and self.STATUS[_stream_id].get('CONTENTION') == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed to TGID %s, target active or in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                        continue
                    if ((_target['TGID'] != _target_status[_target['TS']]['TX_TGID']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < _target_system['GROUP_HANGTIME'])):
                        if _stream_id in self.STATUS and self.STATUS[_stream_id].get('CONTENTION') == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed to TGID%s, target in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']))
                        continue
                    if (_target['TGID'] == _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < STREAM_TO):
                        if _stream_id in self.STATUS and self.STATUS[_stream_id].get('CONTENTION') == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed to TGID%s, matching call already active on target: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                        continue
                    if (_target['TGID'] == _target_status[_target['TS']]['TX_TGID']) and (_rf_src != _target_status[_target['TS']]['TX_RFS']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < STREAM_TO):
                        if _stream_id in self.STATUS and self.STATUS[_stream_id].get('CONTENTION') == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed for subscriber %s, call route in progress on target: HBSystem: %s, TS: %s, TGID: %s, SUB: %s', self._system, int_id(_rf_src), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']), int_id(_target_status[_target['TS']]['TX_RFS']))
                        continue

                    # Is this a new call stream?
                    if (_target_status[_target['TS']]['TX_STREAM_ID'] != _stream_id):
                        cancel_generated_voice(_target_status[_target['TS']])
                        # Record the DST TGID and Stream ID
                        _target_status[_target['TS']]['TX_START'] = pkt_time
                        _target_status[_target['TS']]['TX_TGID'] = _target['TGID']
                        _target_status[_target['TS']]['TX_STREAM_ID'] = _stream_id
                        _target_status[_target['TS']]['TX_RFS'] = _rf_src
                        _target_status[_target['TS']]['TX_PEER'] = _peer_id
                        # Generate LCs (full and EMB) for the TX stream
                        _src_lc = LC_OPT
                        if _stream_id in self.STATUS and 'LC' in self.STATUS[_stream_id]:
                            _src_lc = self.STATUS[_stream_id]['LC'][0:3]
                        dst_lc = b''.join([_src_lc, _target['TGID'], _rf_src])
                        _target_status[_target['TS']]['TX_H_LC'] = bptc.encode_header_lc(dst_lc)
                        _target_status[_target['TS']]['TX_T_LC'] = bptc.encode_terminator_lc(dst_lc)
                        _target_status[_target['TS']]['TX_EMB_LC'] = bptc.encode_emblc(dst_lc)
                        logger.debug('(%s) Generating TX FULL and EMB LCs for HomeBrew destination: System: %s, TS: %s, TGID: %s', self._system, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                        logger.debug('(%s) Conference Bridge: %s, Call Bridged to HBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                        if CONFIG['REPORTS']['REPORT']:
                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))

                    # Set other values for the contention handler to test next time there is a frame to forward
                    _target_status[_target['TS']]['TX_TIME'] = pkt_time
                    _target_status[_target['TS']]['TX_TYPE'] = _dtype_vseq

                    # Handle any necessary re-writes for the destination
                    if _system['TS'] != _target['TS']:
                        _tmp_bits = _bits ^ 1 << 7
                    else:
                        _tmp_bits = _bits

                    # Assemble transmit HBP packet header
                    _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                    # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                    # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                    # if _dst_id != rule['DST_GROUP']:
                    # Per-target copy — never mutate shared dmrpkt across fanout legs
                    _tx_dmrpkt = dmrpkt
                    dmrbits = bitarray(endian='big')
                    dmrbits.frombytes(_tx_dmrpkt)
                    # Create a voice header packet (FULL LC) — FreeDMR: always rewrite
                    if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                        dmrbits = _target_status[_target['TS']]['TX_H_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_H_LC'][98:197]
                    # Create a voice terminator packet (FULL LC) — FreeDMR: always rewrite
                    elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                        dmrbits = _target_status[_target['TS']]['TX_T_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_T_LC'][98:197]
                        if CONFIG['REPORTS']['REPORT']:
                            call_duration = pkt_time - _target_status[_target['TS']]['TX_START']
                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                    # Create a Burst B-E packet (Embedded LC) — FreeDMR: only when TG remaps
                    elif target_requires_emb_lc_rewrite(_dst_id, _target['TGID']) and _frame_type == HBPF_VOICE and _dtype_vseq in [1,2,3,4]:
                        dmrbits = dmrbits[0:116] + _target_status[_target['TS']]['TX_EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                    _tx_dmrpkt = dmrbits.tobytes()
                    #_tmp_data = b''.join([_tmp_data, _tx_dmrpkt, b'\x00\x00']) # Add two bytes of nothing since OBP doesn't include BER & RSSI bytes #_data[53:55]
                    _tmp_data = b''.join([_tmp_data, _tx_dmrpkt])

                # Transmit the packet to the destination system
                systems[_target['SYSTEM']].send_system(_tmp_data,_hops,_ber,_rssi,_source_server, _source_rptr)
                # Expire outbound OBP bookkeeping on VTERM to shrink inbound collision window
                if (_frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM
                        and _stream_id in _target_status
                        and isinstance(_target_status.get(_stream_id), dict)
                        and _target_status[_stream_id].get('_outbound')):
                    del _target_status[_stream_id]
                    #logger.debug('(%s) Packet routed by bridge: %s to system: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                #Ignore this system and TS pair if it's called again on this packet
        return(_sysIgnore)
    
    def sendDataToHBP(self,_d_system,_d_slot,_dst_id,_tmp_bits,_data,dmrpkt,_rf_src,_stream_id,_peer_id):
        _int_dst_id = int_id(_dst_id)
        #Assemble transmit HBP packet header
        _tmp_data = b''.join([_data[:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])
        _tmp_data = b''.join([_tmp_data, dmrpkt])
        systems[_d_system].send_system(_tmp_data)
        logger.debug('(%s) UNIT Data Bridged to HBP on slot 1: %s DST_ID: %s',self._system,_d_system,_int_dst_id)
        if CONFIG['REPORTS']['REPORT']:
            systems[_d_system]._report.send_bridgeEvent('UNIT DATA,DATA,TX,{},{},{},{},{},{}'.format(_d_system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), 1, _int_dst_id).encode(encoding='utf-8', errors='ignore'))
        
    def sendDataToOBP(self,_target,_data,dmrpkt,pkt_time,_stream_id,_dst_id,_peer_id,_rf_src,_bits,_slot,_hops = b'',_source_server = b'\x00\x00\x00\x00', _ber = b'\x00', _rssi = b'\x00', _source_rptr = b'\x00\x00\x00\x00'):

        _int_dst_id = int_id(_dst_id)
        _target_status = systems[_target].STATUS
        _target_system = self._CONFIG['SYSTEMS'][_target]

        
        #If target has missed 6 (on 1 min) of keepalives, don't send
        if _target_system['ENHANCED_OBP'] and '_bcka' in _target_system and _target_system['_bcka'] < pkt_time - 60:
            return
        
        if (_stream_id not in _target_status):
            # This is a new call stream on the target
            _target_status[_stream_id] = {
                'START':     pkt_time,
                'CONTENTION':False,
                'RFS':       _rf_src,
                'TGID':      _dst_id,
                'RX_PEER':   _peer_id,
                'packets': 0,
                'loss': 0,
                '_outbound': True,
            }
            
        # Record the time of this packet so we can later identify a stale stream
        _target_status[_stream_id]['LAST'] = pkt_time
        # Clear the TS bit -- all OpenBridge streams are effectively on TS1
        #_tmp_bits = _bits & ~(1 << 7)
        #rewrite slot if required
        if _slot == 2:
            _tmp_bits = _bits ^ 1 << 7
        else: 
            _tmp_bits = _bits 
        #Assemble transmit HBP packet header
        _tmp_data = b''.join([_data[:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])
        _tmp_data = b''.join([_tmp_data, dmrpkt])
        systems[_target].send_system(_tmp_data,_hops,_ber,_rssi, _source_server, _source_rptr)
        logger.debug('(%s) UNIT Data Bridged to OBP System: %s DST_ID: %s', self._system, _target,_int_dst_id)
        if CONFIG['REPORTS']['REPORT']:
            systems[_target]._report.send_bridgeEvent('UNIT DATA,DATA,TX,{},{},{},{},{},{}'.format(_target, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), 1, _int_dst_id).encode(encoding='utf-8', errors='ignore'))


    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data,_hash, _hops = b'', _source_server = b'\x00\x00\x00\x00', _ber = b'\x00', _rssi = b'\x00', _source_rptr = b'\x00\x00\x00\x00'):
        
        pkt_time = time()
        dmrpkt = _data[20:53]
        _bits = _data[15]

        #pkt_crc = Crc32.calc(_data[4:53])
        #_pkt_crc = Crc32.calc(dmrpkt)
        
        #Use blake2b hash
        _h = blake2b(digest_size=16)
        _h.update(_data)
        _pkt_crc = _h.digest()
        
        #_pkt_crc = _hash

        _int_dst_id = int_id(_dst_id)

        # Parrot (TG 9990) is HBP/PEER only — never bridge through OpenBridge.
        if is_parrot_talkgroup(_int_dst_id):
            return

        # Match UNIT data, SMS/GPS, and send it to the dst_id if it is in SUB_MAP
        if _call_type == 'unit' and (_dtype_vseq == 6 or _dtype_vseq == 7 or _dtype_vseq == 8 or ((_stream_id not in self.STATUS) and _dtype_vseq == 3)):
        
            _int_dst_id = int_id(_dst_id)
##        if ahex(dmrpkt)[27:-27] == b'd5d7f77fd757':
            # This is a data call
            _data_call = True
            
            # Is this a new call stream?
            if (_stream_id not in self.STATUS):
                
                # This is a new call stream
                self.STATUS[_stream_id] = {
                    'START':     pkt_time,
                    'CONTENTION':False,
                    'RFS':       _rf_src,
                    'TGID':      _dst_id,
                    '1ST': perf_counter(),
                    'lastSeq': False,
                    'lastData': False,
                    'RX_PEER': _peer_id,
                    'packets': 0,
                    'loss': 0,
                    'crcs': set()

                }
            
            self.STATUS[_stream_id]['LAST'] = pkt_time
            self.STATUS[_stream_id]['packets'] = self.STATUS[_stream_id]['packets'] + 1

            if '1ST' not in self.STATUS[_stream_id]:
                self.STATUS[_stream_id]['1ST'] = perf_counter()
            
            _hbp_active, _hbp_sys = _find_hbp_stream_rx_owner(_stream_id, exclude=self._system)
            if _hbp_active:
                if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']:
                    logger.debug("(%s) OBP UNIT *LoopControl* FIRST HBP: %s, STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",self._system, _hbp_sys, int_id(_stream_id), int_id(_dst_id), _slot)
                    self.STATUS[_stream_id]['LOOPLOG'] = True
                self.STATUS[_stream_id]['LAST'] = pkt_time
                return
            hr_times = _obp_loop_hr_times(_stream_id, _dst_id)
                    
            #use the minimum perf_counter to ensure
            #We always use only the earliest packet
            fi = min(hr_times, key=hr_times.get, default = False)
            
            hr_times = None
            
            if fi and self._system != fi:             
                if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']:
                    call_duration = pkt_time - self.STATUS[_stream_id]['START']
                    packet_rate = 0
                    if 'packets' in self.STATUS[_stream_id]:
                        packet_rate = self.STATUS[_stream_id]['packets'] / call_duration
                    logger.debug("(%s) OBP UNIT *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE. PACKET RATE %0.2f/s",self._system, fi, int_id(_stream_id), int_id(_dst_id),packet_rate)
                    self.STATUS[_stream_id]['LOOPLOG'] = True
                self.STATUS[_stream_id]['LAST'] = pkt_time
                return
            

            
            if _dtype_vseq == 3:
                logger.info('(%s) *UNIT CSBK* STREAM ID: %s, RPTR: %s SUB: %s (%s) PEER: %s (%s) DST_ID %s (%s), TS %s, SRC: %s, RPTR: %s', \
                        self._system, int_id(_stream_id), self.get_rptr(_source_rptr), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, int_id(_source_server),int_id(_source_rptr))
                if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('UNIT CSBK,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
            elif _dtype_vseq == 6:
                logger.info('(%s) *UNIT DATA HEADER* STREAM ID: %s, RPTR: %s SUB: %s (%s) PEER: %s (%s) DST_ID %s (%s), TS %s, SRC: %s, RPTR: %s', \
                        self._system, int_id(_stream_id),self.get_rptr(_source_rptr), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot,int_id(_source_server),int_id(_source_rptr))
                if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('UNIT DATA HEADER,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
            elif _dtype_vseq == 7:
                    logger.info('(%s) *UNIT VCSBK 1/2 DATA BLOCK * STREAM ID: %s, RPTR: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, SRC: %s, RPTR: %s', \
                            self._system, int_id(_stream_id), self.get_rptr(_source_rptr), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, int_id(_source_server),int_id(_source_rptr))
                    if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('UNIT VCSBK 1/2 DATA BLOCK,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
            elif _dtype_vseq == 8:
                    logger.info('(%s) *UNIT VCSBK 3/4 DATA BLOCK * STREAM ID: %s, RPTR: %s, SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, SRC: %s, RPTR: %s', \
                            self._system, int_id(_stream_id), self.get_rptr(_source_rptr), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot,int_id(_source_server),int_id(_source_rptr))
                    if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('UNIT VCSBK 3/4 DATA BLOCK,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
            else:
                    logger.info('(%s) *UNKNOWN DATA TYPE* STREAM ID: %s, RPTR: %s, SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, SRC: %s, RPTR: %s', \
                            self._system, int_id(_stream_id), self.get_rptr(_source_rptr), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot,int_id(_source_server),int_id(_source_rptr))
            
            #Send all data to DATA-GATEWAY if enabled and valid
            if CONFIG['GLOBAL']['DATA_GATEWAY'] and 'DATA-GATEWAY' in CONFIG['SYSTEMS'] and CONFIG['SYSTEMS']['DATA-GATEWAY']['MODE'] == 'OPENBRIDGE' and CONFIG['SYSTEMS']['DATA-GATEWAY']['ENABLED']:
                logger.debug('(%s) DATA packet sent to DATA-GATEWAY',self._system)
                self.sendDataToOBP('DATA-GATEWAY',_data,dmrpkt,pkt_time,_stream_id,_dst_id,_peer_id,_rf_src,_bits,_slot,_hops,_source_server,_ber,_rssi,_source_rptr)
                 
            
            #Send other openbridges
            for system in systems:
                if system  == self._system:
                    continue
                if system == 'DATA-GATEWAY':
                    continue
                #We only want to send data calls to individual IDs via OpenBridge
                #Only send if proto ver for bridge is > 1
                if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE' and CONFIG['SYSTEMS'][system]['VER'] > 1 and (_int_dst_id >= 1000000):
                    self.sendDataToOBP(system,_data,dmrpkt,pkt_time,_stream_id,_dst_id,_peer_id,_rf_src,_bits,_slot,_hops,_source_server,_ber,_rssi)
            
            #If destination ID is in the Subscriber Map
            if _dst_id in SUB_MAP:
                # BACKWARDS COMPATIBILITY: Handle 3, 4, and 5-element formats
                try:
                    if len(SUB_MAP[_dst_id]) == 5:
                        (_d_system, _d_slot, _d_tg, _d_time, _d_peer_id) = SUB_MAP[_dst_id]
                    elif len(SUB_MAP[_dst_id]) == 4:
                        (_d_system, _d_slot, _d_tg, _d_time) = SUB_MAP[_dst_id]
                    else:  # Old 3-element format
                        (_d_system, _d_slot, _d_time) = SUB_MAP[_dst_id]
                except (TypeError, ValueError):
                    logger.warning('(%s) Invalid SUB_MAP entry for destination %s', self._system, int_id(_dst_id))
                else:
                    _dst_slot  = systems[_d_system].STATUS[_d_slot]
                    logger.info('(%s) SUB_MAP matched, System: %s Slot: %s, Time: %s',self._system, _d_system,_d_slot,_d_time)
                    #If slot is idle for RX and TX
                if (_dst_slot['RX_TYPE'] == HBPF_SLT_VTERM) and (_dst_slot['TX_TYPE'] == HBPF_SLT_VTERM) and (time() - _dst_slot['TX_TIME'] > CONFIG['SYSTEMS'][_d_system]['GROUP_HANGTIME']):                
                #rewrite slot if required
                    if _slot != _d_slot:
                        _tmp_bits = _bits ^ 1 << 7
                    else: 
                        _tmp_bits = _bits                        
                    self.sendDataToHBP(_d_system,_d_slot,_dst_id,_tmp_bits,_data,dmrpkt,_rf_src,_stream_id,_peer_id)
                        
                else:
                    logger.debug('(%s) UNIT Data not bridged to HBP on slot 1 - target busy: %s DST_ID: %s',self._system,_d_system,_int_dst_id)
      
            else:                
                #If destination ID is logged in as a hotspot or IPSC repeater
                for _d_system in systems:
                    if not is_routing_master(CONFIG['SYSTEMS'][_d_system]['MODE']):
                        continue
                    for _to_peer in CONFIG['SYSTEMS'][_d_system]['PEERS']:
                            _int_to_peer = int_id(_to_peer)
                            if (str(_int_to_peer)[:7] == str(_int_dst_id)[:7]):
                                #(_d_system,_d_slot,_d_time) = SUB_MAP[_dst_id]
                                _d_slot = 2
                                _dst_slot  = systems[_d_system].STATUS[_d_slot]
                                logger.info('(%s) User Peer Hotspot ID matched, System: %s Slot: %s',self._system, _d_system,_d_slot)
                                #If slot is idle for RX and TX
                                if (_dst_slot['RX_TYPE'] == HBPF_SLT_VTERM) and (_dst_slot['TX_TYPE'] == HBPF_SLT_VTERM) and (time() - _dst_slot['TX_TIME'] > CONFIG['SYSTEMS'][_d_system]['GROUP_HANGTIME']):
                                #Always use slot2 for hotspots - many of them are simplex and this 
                                #is the convention 
                                    #rewrite slot if required (slot 2 is used on hotspots)
                                    if _slot != 2:
                                        _tmp_bits = _bits ^ 1 << 7
                                    else: 
                                        _tmp_bits = _bits
                                    self.sendDataToHBP(_d_system,_d_slot,_dst_id,_tmp_bits,_data,dmrpkt,_rf_src,_stream_id,_peer_id)
                                    
                                else:
                                    logger.debug('(%s) UNIT Data not bridged to HBP on slot %s - target busy: %s DST_ID: %s',self._system,_d_slot,_d_system,_int_dst_id)
            
            self.STATUS[_stream_id]['crcs'].add(_pkt_crc)
            
                    
        if _call_type == 'group' or _call_type == 'vcsbk':
            # Outbound STATUS collision: never reclaim/promote to CALL START (MAX HOPS).
            if _stream_id in self.STATUS:
                _collision = classify_obp_outbound_collision(self.STATUS[_stream_id], _dst_id)
                if _collision == OBP_OUTBOUND_ECHO:
                    self.STATUS[_stream_id]['LAST'] = pkt_time
                    if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']:
                        logger.debug(
                            "(%s) OBP *PacketControl* outbound echo drop STREAM ID: %s TG: %s",
                            self._system, int_id(_stream_id), int_id(_dst_id))
                        self.STATUS[_stream_id]['LOOPLOG'] = True
                    return
                if _collision == OBP_OUTBOUND_REPLACE:
                    del self.STATUS[_stream_id]

            # Is this a new call stream?
            if (_stream_id not in self.STATUS):
                
                # This is a new call stream
                self.STATUS[_stream_id] = {
                    'START':     pkt_time,
                    'CONTENTION':False,
                    'RFS':       _rf_src,
                    'TGID':      _dst_id,
                    '1ST': perf_counter(),
                    'lastSeq': False,
                    'lastData': False,
                    'RX_PEER': _peer_id,
                    'packets': 0,
                    'loss': 0,
                    'crcs': set()

                }

                # If we can, use the LC from the voice header as to keep all options intact
                if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                    decoded = decode.voice_head_term(dmrpkt)
                    self.STATUS[_stream_id]['LC'] = decoded['LC']

                # If we don't have a voice header then don't wait to decode the Embedded LC
                # just make a new one from the HBP header. This is good enough, and it saves lots of time
                else:
                    self.STATUS[_stream_id]['LC'] = b''.join([LC_OPT,_dst_id,_rf_src])

                # Gate START RX report on LoopControl ownership (multi-OBP mesh).
                # Report-only: losers skip START for the dash but still route the
                # first packet (FreeDMR parity); continuation LoopControl ignores them.
                _hbp_active, _hbp_sys = _find_hbp_stream_rx_owner(_stream_id, exclude=self._system)
                _hr_times = _obp_loop_hr_times(_stream_id, _dst_id)
                _report_start = should_report_obp_rx_start(
                    self._system, _hbp_sys if _hbp_active else None, _hr_times)
                if not _report_start:
                    self.STATUS[_stream_id]['LOOPLOG'] = True
                    logger.debug(
                        "(%s) OBP *LoopControl* START RX suppressed STREAM ID: %s TG: %s "
                        "(HBP=%s hr=%s)",
                        self._system, int_id(_stream_id), int_id(_dst_id),
                        _hbp_sys if _hbp_active else None, list(_hr_times))
                else:
                    _inthops = 0 
                    if _hops:
                        _inthops = int.from_bytes(_hops,'big')
                    logger.info('(%s) *CALL START* STREAM ID: %s, SUB: %s (%s), RPTR: %s (%s), PEER: %s (%s) TGID %s (%s), TS %s, SRC: %s, HOPS %s', 
                            self._system, int_id(_stream_id),get_alias(_rf_src, subscriber_ids),int_id(_rf_src),self.get_rptr(_source_rptr), int_id(_source_rptr),  get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot,int_id(_source_server),_inthops)
                    if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('GROUP VOICE,START,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
                # Count the creating packet (parity with HBP / OBP unit)
                self.STATUS[_stream_id]['packets'] = 1


            else:
                ensure_obp_inbound_status_keys(self.STATUS[_stream_id], perf_counter)
                self.STATUS[_stream_id]['packets'] = self.STATUS[_stream_id]['packets'] + 1
                #Finished stream handling#
                if '_fin' in self.STATUS[_stream_id]:
                    if '_finlog' not in self.STATUS[_stream_id]:
                        logger.debug("(%s) OBP *LoopControl* STREAM ID: %s ALREADY FINISHED FROM THIS SOURCE, IGNORING",self._system, int_id(_stream_id))
                    self.STATUS[_stream_id]['_finlog'] = True
                    return
                
                #TIMEOUT
                if self.STATUS[_stream_id]['START'] + 180 < pkt_time:
                    if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']: 
                        logger.info("(%s) OBP *TIMEOUT*, STREAM ID: %s, TG: %s, IGNORE THIS SOURCE",self._system, int_id(_stream_id), int_id(_dst_id))
                        self.STATUS[_stream_id]['LOOPLOG'] = True
                    self.STATUS[_stream_id]['LAST'] = pkt_time
                    return
                    
                if '1ST' not in self.STATUS[_stream_id]:
                    self.STATUS[_stream_id]['1ST'] = perf_counter()

                #LoopControl
                _hbp_active, _hbp_sys = _find_hbp_stream_rx_owner(_stream_id, exclude=self._system)
                if _hbp_active:
                    if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']:
                        logger.debug("(%s) OBP *LoopControl* FIRST HBP: %s, STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",self._system, _hbp_sys, int_id(_stream_id), int_id(_dst_id), _slot)
                        self.STATUS[_stream_id]['LOOPLOG'] = True
                    self.STATUS[_stream_id]['LAST'] = pkt_time
                    return
                hr_times = _obp_loop_hr_times(_stream_id, _dst_id)
                
                #use the minimum perf_counter to ensure
                #We always use only the earliest packet
                fi = min(hr_times, key=hr_times.get, default = False)
                
                hr_times = None
                
                if fi and self._system != fi:             
                    if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']:
                        call_duration = pkt_time - self.STATUS[_stream_id]['START']
                        packet_rate = 0
                        if 'packets' in self.STATUS[_stream_id]:
                            packet_rate = self.STATUS[_stream_id]['packets'] / call_duration
                        logger.debug("(%s) OBP *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE. PACKET RATE %0.2f/s",self._system, fi, int_id(_stream_id), int_id(_dst_id),packet_rate)
                        self.STATUS[_stream_id]['LOOPLOG'] = True
                    self.STATUS[_stream_id]['LAST'] = pkt_time
                    
                    if CONFIG['SYSTEMS'][self._system]['ENHANCED_OBP'] and '_bcsq' not in self.STATUS[_stream_id]:
                        systems[self._system].send_bcsq(_dst_id,_stream_id)
                        self.STATUS[_stream_id]['_bcsq'] = True
                    return
                
                # FreeDMR OBP RATE DROP — discard catch-up bursts (soft-client playout)
                if OBP_RATE_DROP_ENABLED:
                    call_duration = pkt_time - self.STATUS[_stream_id]['START']
                    packet_rate = 0
                    if call_duration:
                        packet_rate = self.STATUS[_stream_id]['packets'] / call_duration
                    if (call_duration >= OBP_RATE_DROP_MIN_DURATION
                            and self.STATUS[_stream_id]['packets'] > OBP_RATE_DROP_MIN_PACKETS
                            and packet_rate > OBP_RATE_DROP_MAX_PPS):
                        logger.warning(
                            "(%s) *PacketControl* RATE DROP! Stream ID: %s TGID: %s PACKETS: %s DURATION: %.2f RATE: %.2f/s",
                            self._system, int_id(_stream_id), int_id(_dst_id),
                            self.STATUS[_stream_id]['packets'], call_duration, packet_rate)
                        return
                
                #Duplicate handling#
                #Handle inbound duplicates
                #Duplicate complete packet
                if self.STATUS[_stream_id]['lastData'] and self.STATUS[_stream_id]['lastData'] == _data and _seq > 1:
                    self.STATUS[_stream_id]['loss'] += 1
                    logger.debug("(%s) *PacketControl* last packet is a complete duplicate of the previous one, disgarding. Stream ID:, %s TGID: %s, LOSS: %.2f%%",self._system,int_id(_stream_id),int_id(_dst_id),((self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100))
                    return
                _seq_delta = dmrd_seq_delta(_seq, self.STATUS[_stream_id]['lastSeq'])
                #Duplicate SEQ number
                if _seq_delta == 0:
                    self.STATUS[_stream_id]['loss'] += 1
                    logger.debug("(%s) *PacketControl* Duplicate sequence number %s, disgarding. Stream ID:, %s TGID: %s, LOSS: %.2f%%",self._system,_seq,int_id(_stream_id),int_id(_dst_id),((self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100))
                    return
                #Inbound out-of-order packets (wrap-aware)
                if _seq_delta is not None and _seq_delta > 127:
                    self.STATUS[_stream_id]['loss'] += 1
                    logger.debug("%s) *PacketControl* Out of order packet - last SEQ: %s, this SEQ: %s,  disgarding. Stream ID:, %s TGID: %s, LOSS: %.2f%%",self._system,self.STATUS[_stream_id]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id),((self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100))
                    return
                #Duplicate DMR payload to previuos packet (by hash
                if  _seq > 0 and _pkt_crc in self.STATUS[_stream_id]['crcs']:
                    self.STATUS[_stream_id]['loss'] += 1
                    logger.debug("(%s) *PacketControl* DMR packet payload with hash: %s seen before in this stream, disgarding. Stream ID:, %s TGID: %s: SEQ:%s PACKETS: %s, LOSS: %.2f%% ",self._system,_pkt_crc,int_id(_stream_id),int_id(_dst_id),_seq, self.STATUS[_stream_id]['packets'],((self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100))
                    return
                #Inbound missed packets
                if _seq_delta is not None and _seq_delta > 1:
                    self.STATUS[_stream_id]['loss'] += 1
                    logger.debug("(%s) *PacketControl* Missed packet(s) - last SEQ: %s, this SEQ: %s. Stream ID:, %s TGID: %s , LOSS: %.2f%%",self._system,self.STATUS[_stream_id]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id),((self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100))

            
                #Save this sequence number 
                self.STATUS[_stream_id]['lastSeq'] = _seq
                #Save this packet
                self.STATUS[_stream_id]['lastData'] = _data
               

            
            self.STATUS[_stream_id]['crcs'].add(_pkt_crc)
            
            self.STATUS[_stream_id]['LAST'] = pkt_time
            
            
            #Create STAT bridge for unknown TG
            if CONFIG['GLOBAL']['GEN_STAT_BRIDGES']:
                if (int_id(_dst_id) >= 5 and int_id(_dst_id) != 9
                        and not is_dial_service_code(int_id(_dst_id))
                        and not is_parrot_talkgroup(int_id(_dst_id))
                        and is_valid_talkgroup_bridge(str(int_id(_dst_id)))
                        and (str(int_id(_dst_id)) not in BRIDGES)):
                    logger.debug('(%s) Bridge for STAT TG %s does not exist. Creating',self._system, int_id(_dst_id))
                    make_stat_bridge(_dst_id)

            # Activate this OBP leg on an existing conference bridge (same as HBP on call start)
            _int_dst = int_id(_dst_id)
            if (_int_dst >= 5 and _int_dst != 9 and _int_dst not in (4000, 5000)
                    and not is_parrot_talkgroup(_int_dst)
                    and not (_int_dst >= 9991 and _int_dst <= 9999)
                    and str(_int_dst) in BRIDGES):
                activate_ua_bridge_source(str(_int_dst), self._system, _slot, peer_id=_peer_id)
            
            if not is_dial_service_code(_int_dst):
                # --- OPTIMISED ROUTING: use BRIDGE_IDX for O(1) lookup instead of O(N*M) full scan ---
                _sysIgnore = deque()
                _lookup_key = (self._system, _slot, _dst_id)
                _candidate_bridges = BRIDGE_IDX.get(_lookup_key)
                _ROUTE_STATS['packets'] += 1
                if _candidate_bridges is None:
                    # Index miss - fall back to full scan and schedule a rebuild.
                    # This should never happen in normal operation; log at WARNING.
                    logger.warning('(%s) OBP BRIDGE_IDX miss for key (%s, %s, %s) '
                                   '- falling back to full scan and rebuilding index',
                                   self._system, self._system, _slot, int_id(_dst_id))
                    _ROUTE_STATS['index_misses'] += 1
                    _ROUTE_STATS['fallbacks'] += 1
                    rebuild_bridge_index()
                    _candidate_bridges = BRIDGE_IDX.get(_lookup_key, set())
                    # Full-scan fallback for safety
                    for _bridge in BRIDGES:
                        for _system in BRIDGES[_bridge]:
                            if _system['SYSTEM'] == self._system and _system['TGID'] == _dst_id and _system['TS'] == _slot and _system['ACTIVE'] == True:
                                _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,False,_sysIgnore,_hops, _source_server, _ber, _rssi, _source_rptr)
                                _paired_bridge = paired_group_route_bridge(
                                    _bridge, BRIDGES, _dst_id)
                                if _paired_bridge:
                                    _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_paired_bridge,_system,False,_sysIgnore,_hops, _source_server, _ber, _rssi, _source_rptr)
                else:
                    _ROUTE_STATS['index_hits'] += 1
                    for _orig_bridge in list(_candidate_bridges):
                        if _orig_bridge not in BRIDGES:
                            # Stale index entry - skip and schedule a rebuild
                            logger.debug('(%s) OBP BRIDGE_IDX stale entry for bridge %s, skipping',
                                         self._system, _orig_bridge)
                            continue
                        for _system in BRIDGES[_orig_bridge]:
                            if _system['SYSTEM'] == self._system and _system['TGID'] == _dst_id and _system['TS'] == _slot and _system['ACTIVE'] == True:
                                _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_orig_bridge,_system,False,_sysIgnore,_hops, _source_server, _ber, _rssi, _source_rptr)
                                _paired_bridge = paired_group_route_bridge(
                                    _orig_bridge, BRIDGES, _dst_id)
                                if _paired_bridge:
                                    _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_paired_bridge,_system,False,_sysIgnore,_hops, _source_server, _ber, _rssi, _source_rptr)
                _log_route_stats()


            # Final actions - Is this a voice terminator?
            if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM):
                call_duration = pkt_time - self.STATUS[_stream_id]['START']
                packet_rate = 0
                loss = 0.00
                if call_duration and self.STATUS[_stream_id].get('packets'):
                    packet_rate = self.STATUS[_stream_id]['packets'] / call_duration
                    loss = (self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100
                logger.info('(%s) *CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, Duration: %.2f, Packet rate: %.2f/s, Loss: %.2f%%', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration, packet_rate,loss)
                if CONFIG['REPORTS']['REPORT'] and should_report_stream_end(self.STATUS[_stream_id]):
                   self._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))
                   self.STATUS[_stream_id]['_fin'] = True
                   
                self.STATUS[_stream_id]['lastSeq'] = False

class routerHBP(HBSYSTEM):

    def __init__(self, _name, _config, _report):
        HBSYSTEM.__init__(self, _name, _config, _report)
        # Status information for the system, TS1 & TS2
        # 1 & 2 are "timeslot"
        # In TX_EMB_LC, 2-5 are burst B-E
        self.STATUS = {
            1: {
                'RX_START':     time(),
                'TX_START':     time(),
                'RX_SEQ':       0,
                'RX_RFS':       b'\x00',
                'TX_RFS':       b'\x00',
                'RX_PEER':      b'\x00',
                'TX_PEER':      b'\x00',
                'RX_STREAM_ID': b'\x00',
                'TX_STREAM_ID': b'\x00',
                'RX_TGID':      b'\x00\x00\x00',
                'TX_TGID':      b'\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      HBPF_SLT_VTERM,
                'TX_TYPE':      HBPF_SLT_VTERM,
                'RX_LC':        b'\x00',
                'TX_H_LC':      b'\x00',
                'TX_T_LC':      b'\x00',
                'TX_EMB_LC': {
                    1: b'\x00',
                    2: b'\x00',
                    3: b'\x00',
                    4: b'\x00',
                    },
                'lastSeq': False,
                'lastData': False,
                'packets': 0,
                'loss': 0,
                'crcs': set(),
                '_allStarMode': False,
                **hbp_slot_prompt_defaults(),
                },
            2: {
                'RX_START':     time(),
                'TX_START':     time(),
                'RX_SEQ':       0,
                'RX_RFS':       b'\x00',
                'TX_RFS':       b'\x00',
                'RX_PEER':      b'\x00',
                'TX_PEER':      b'\x00',
                'RX_STREAM_ID': b'\x00',
                'TX_STREAM_ID': b'\x00',
                'RX_TGID':      b'\x00\x00\x00',
                'TX_TGID':      b'\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      HBPF_SLT_VTERM,
                'TX_TYPE':      HBPF_SLT_VTERM,
                'RX_LC':        b'\x00',
                'TX_H_LC':      b'\x00',
                'TX_T_LC':      b'\x00',
                'TX_EMB_LC': {
                    1: b'\x00',
                    2: b'\x00',
                    3: b'\x00',
                    4: b'\x00',
                    },
                'lastSeq': False,
                'lastData': False,
                'packets': 0,
                'loss': 0,
                'crcs': set(),
                '_allStarMode': False,
                **hbp_slot_prompt_defaults(),
                }
            }

    def _assign_rx_stream_id(self, slot, stream_id):
        _track_hbp_rx_stream(self._system, self.STATUS[slot], stream_id)
        self.STATUS[slot]['RX_STREAM_ID'] = stream_id

    def master_maintenance_loop(self):
        """Clear reflectors/SUB_MAP on ping timeout (parity with RPTCL path)."""
        _ping_deadline = (
            self._CONFIG['GLOBAL']['PING_TIME'] * self._CONFIG['GLOBAL']['MAX_MISSED'])
        _now = time()
        for _peer_id in list(self._peers):
            _this_peer = self._peers[_peer_id]
            if _this_peer['LAST_PING'] + _ping_deadline < _now:
                clear_default_reflectors(self._system)
                reset_dynamic_reflectors(self._system)
                clear_sub_map_for_system(self._system)
                clear_sub_map_for_peer(_peer_id)
        HBSYSTEM.master_maintenance_loop(self)

    def master_datagramReceived(self, _data, _sockaddr):
        _command = _data[:4]
        if _command == RPTC:
            if _data[:5] == RPTCL and len(_data) >= 9:
                _peer_id = _data[5:9]
                if (_peer_id in self._peers
                        and self._peers[_peer_id]['CONNECTION'] == 'YES'
                        and self._peers[_peer_id]['SOCKADDR'] == _sockaddr):
                    clear_default_reflectors(self._system)
                    reset_dynamic_reflectors(self._system)
                    clear_sub_map_for_system(self._system)
                    clear_sub_map_for_peer(_peer_id)
            elif len(_data) >= 8:
                _peer_id = _data[4:8]
                if (_peer_id in self._peers
                        and self._peers[_peer_id]['CONNECTION'] == 'WAITING_CONFIG'
                        and self._peers[_peer_id]['SOCKADDR'] == _sockaddr):
                    clear_default_reflectors(self._system)
                    reset_dynamic_reflectors(self._system)
                    sanitize_dial_reflectors(self._system)
                    clear_sub_map_for_system(self._system)
                    clear_sub_map_for_peer(_peer_id)
        HBSYSTEM.master_datagramReceived(self, _data, _sockaddr)

    def to_target(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,_noOBP,sysIgnore,_source_server, _ber, _rssi, _source_rptr):
        _sysIgnore = sysIgnore
        for _target in BRIDGES[_bridge]:
            #if _target['SYSTEM'] != self._system or (_target['SYSTEM'] == self._system and _target['TS'] != _slot):
            if _target['SYSTEM'] != self._system and _target['ACTIVE']:
                #if _target['ACTIVE']:
                    _target_status = systems[_target['SYSTEM']].STATUS
                    _target_system = self._CONFIG['SYSTEMS'][_target['SYSTEM']]

                    if (_target['SYSTEM'],_target['TS']) in _sysIgnore:
                        #logger.debug("(DEDUP) HBP Source - Skipping system %s TS: %s",_target['SYSTEM'],_target['TS'])
                        continue
                    if _target_system['MODE'] == 'OPENBRIDGE':
                        if _noOBP == True or is_parrot_bridge(_bridge):
                            continue
                        # Peer already hearing this stream inbound — skip mesh re-fanout TX
                        if obp_target_already_has_inbound(_target_status, _stream_id, _dst_id):
                            _sysIgnore.append((_target['SYSTEM'], _target['TS']))
                            continue
                        #We want to ignore this system and TS combination if it's called again for this packet
                        _sysIgnore.append((_target['SYSTEM'],_target['TS']))
                        
                        #If target has quenched us, don't send
                        if ('_bcsq' in _target_system) and (_dst_id in _target_system['_bcsq']) and (_target_system['_bcsq'][_target['TGID']] == _stream_id):
                            continue
                        
                        #If target has missed 6 (on 1 min) of keepalives, don't send
                        if _target_system['ENHANCED_OBP'] and '_bcka' in _target_system and _target_system['_bcka'] < pkt_time - 60:
                            continue
                        
                        #If talkgroup is prohibited by ACL
                        if self._CONFIG['GLOBAL']['USE_ACL']:
                            if not acl_check(_target['TGID'],self._CONFIG['GLOBAL']['TG1_ACL']):
                                continue
                        
                        if _target_system['USE_ACL']:
                            if not acl_check(_target['TGID'],_target_system['TG1_ACL']):
                                continue
                        
        
                        # Is this a new call stream on the target?
                        if (_stream_id not in _target_status):
                            # This is a new call stream on the target
                            _target_status[_stream_id] = {
                                'START':     pkt_time,
                                'CONTENTION':False,
                                'RFS':       _rf_src,
                                'TGID':      _dst_id,
                                'RX_PEER':   _peer_id,
                                '_outbound': True,
                                'packets': 0,
                                'loss': 0,
                            }
                            # Generate LCs (full and EMB) for the TX stream
                            dst_lc = b''.join([self.STATUS[_slot]['RX_LC'][0:3], _target['TGID'], _rf_src])
                            _target_status[_stream_id]['H_LC'] = bptc.encode_header_lc(dst_lc)
                            _target_status[_stream_id]['T_LC'] = bptc.encode_terminator_lc(dst_lc)
                            _target_status[_stream_id]['EMB_LC'] = bptc.encode_emblc(dst_lc)

                            logger.debug('(%s) Conference Bridge: %s, Call Bridged to OBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                            if CONFIG['REPORTS']['REPORT']:
                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))
                            
                        # Record the time of this packet so we can later identify a stale stream
                        _target_status[_stream_id]['LAST'] = pkt_time
                        # Clear the TS bit -- all OpenBridge streams are effectively on TS1
                        _tmp_bits = _bits & ~(1 << 7)

                        # Assemble transmit HBP packet header
                        _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                        # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                        # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                        # if _dst_id != rule['DST_GROUP']:
                        # Per-target copy — never mutate shared dmrpkt across fanout legs
                        _tx_dmrpkt = dmrpkt
                        dmrbits = bitarray(endian='big')
                        dmrbits.frombytes(_tx_dmrpkt)
                        # Create a voice header packet (FULL LC) — FreeDMR: always rewrite
                        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                            try:
                                dmrbits = _target_status[_stream_id]['H_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['H_LC'][98:197]
                            except KeyError:
                                logger.debug('(%s) KeyError - H_LC, sending original bits', self._system)
                        # Create a voice terminator packet (FULL LC) — FreeDMR: always rewrite
                        elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                            try:
                                dmrbits = _target_status[_stream_id]['T_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['T_LC'][98:197]
                            except KeyError:
                                logger.debug('(%s) KeyError - T_LC, sending original bits', self._system)
                            if CONFIG['REPORTS']['REPORT']:
                                call_duration = pkt_time - _target_status[_stream_id]['START']
                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                        # Create a Burst B-E packet (Embedded LC) — FreeDMR: only when TG remaps
                        elif target_requires_emb_lc_rewrite(_dst_id, _target['TGID']) and _frame_type == HBPF_VOICE and _dtype_vseq in [1,2,3,4]:
                            try:
                                dmrbits = dmrbits[0:116] + _target_status[_stream_id]['EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                            except KeyError:
                                logger.debug('(%s) KeyError - EMB_LC, skipping', self._system)
                                continue
                        _tx_dmrpkt = dmrbits.tobytes()
                        _tmp_data = b''.join([_tmp_data, _tx_dmrpkt])

                    else:
                        # BEGIN STANDARD CONTENTION HANDLING
                        #
                        # The rules for each of the 4 "ifs" below are listed here for readability. The Frame To Send is:
                        #   From a different group than last RX from this HBSystem, but it has been less than Group Hangtime
                        #   From a different group than last TX to this HBSystem, but it has been less than Group Hangtime
                        #   From the same group as the last RX from this HBSystem, but from a different subscriber, and it has been less than stream timeout
                        #   From the same group as the last TX to this HBSystem, but from a different subscriber, and it has been less than stream timeout
                        # The "continue" at the end of each means the next iteration of the for loop that tests for matching rules
                        #
                        if ((_target['TGID'] != _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < _target_system['GROUP_HANGTIME'])):
                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                logger.info('(%s) Call not routed to TGID %s, target active or in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                            continue
                        if ((_target['TGID'] != _target_status[_target['TS']]['TX_TGID']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < _target_system['GROUP_HANGTIME'])):
                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                logger.info('(%s) Call not routed to TGID%s, target in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']))
                            continue
                        if (_target['TGID'] == _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < STREAM_TO):
                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                logger.info('(%s) Call not routed to TGID%s, matching call already active on target: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                            continue
                        if (_target['TGID'] == _target_status[_target['TS']]['TX_TGID']) and (_rf_src != _target_status[_target['TS']]['TX_RFS']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < STREAM_TO):
                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                logger.info('(%s) Call not routed for subscriber %s, call route in progress on target: HBSystem: %s, TS: %s, TGID: %s, SUB: %s', self._system, int_id(_rf_src), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']), int_id(_target_status[_target['TS']]['TX_RFS']))
                            continue

                        # Is this a new call stream on the *target*?
                        # Match OBP: key off target TX_STREAM_ID, not source RX_STREAM_ID.
                        # Source RX_STREAM_ID is assigned after to_target, so the old gate
                        # skipped LC regen after hangtime and left stale TX_H_LC (DV3000 stretch).
                        if (_target_status[_target['TS']]['TX_STREAM_ID'] != _stream_id):
                                cancel_generated_voice(_target_status[_target['TS']])
                                # Record the DST TGID and Stream ID
                                _target_status[_target['TS']]['TX_START'] = pkt_time
                                _target_status[_target['TS']]['TX_TGID'] = _target['TGID']
                                _target_status[_target['TS']]['TX_STREAM_ID'] = _stream_id
                                _target_status[_target['TS']]['TX_RFS'] = _rf_src
                                _target_status[_target['TS']]['TX_PEER'] = _peer_id
                                # Generate LCs (full and EMB) for the TX stream
                                dst_lc = b''.join([self.STATUS[_slot]['RX_LC'][0:3],_target['TGID'],_rf_src])
                                _target_status[_target['TS']]['TX_H_LC'] = bptc.encode_header_lc(dst_lc)
                                _target_status[_target['TS']]['TX_T_LC'] = bptc.encode_terminator_lc(dst_lc)
                                _target_status[_target['TS']]['TX_EMB_LC'] = bptc.encode_emblc(dst_lc)
                                logger.debug('(%s) Generating TX FULL and EMB LCs for HomeBrew destination: System: %s, TS: %s, TGID: %s', self._system, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                                logger.debug('(%s) Conference Bridge: %s, Call Bridged to HBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                                if CONFIG['REPORTS']['REPORT']:
                                    systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))

                        # Set other values for the contention handler to test next time there is a frame to forward
                        _target_status[_target['TS']]['TX_TIME'] = pkt_time
                        _target_status[_target['TS']]['TX_TYPE'] = _dtype_vseq

                        # Handle any necessary re-writes for the destination
                        if _system['TS'] != _target['TS']:
                            _tmp_bits = _bits ^ 1 << 7
                        else:
                            _tmp_bits = _bits

                        # Assemble transmit HBP packet header
                        _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                        # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                        # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                        # if _dst_id != rule['DST_GROUP']:
                        # Per-target copy — never mutate shared dmrpkt across fanout legs
                        _tx_dmrpkt = dmrpkt
                        dmrbits = bitarray(endian='big')
                        dmrbits.frombytes(_tx_dmrpkt)
                        # Create a voice header packet (FULL LC) — FreeDMR: always rewrite
                        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                            dmrbits = _target_status[_target['TS']]['TX_H_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_H_LC'][98:197]
                        # Create a voice terminator packet (FULL LC) — FreeDMR: always rewrite
                        elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                            dmrbits = _target_status[_target['TS']]['TX_T_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_T_LC'][98:197]
                            if CONFIG['REPORTS']['REPORT']:
                                call_duration = pkt_time - _target_status[_target['TS']]['TX_START']
                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                        # Create a Burst B-E packet (Embedded LC) — FreeDMR: only when TG remaps
                        elif target_requires_emb_lc_rewrite(_dst_id, _target['TGID']) and _frame_type == HBPF_VOICE and _dtype_vseq in [1,2,3,4]:
                            dmrbits = dmrbits[0:116] + _target_status[_target['TS']]['TX_EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                        try:
                            _tx_dmrpkt = dmrbits.tobytes()
                        except AttributeError:
                            logger.exception('(%s) Non-fatal AttributeError - dmrbits.tobytes()',self._system)
                            
                        _tmp_data = b''.join([_tmp_data, _tx_dmrpkt, _data[53:55]])

                    # Transmit the packet to the destination system
                    systems[_target['SYSTEM']].send_system(_tmp_data,b'',_ber,_rssi,_source_server, _source_rptr)
                    # Expire outbound OBP bookkeeping on VTERM to shrink inbound collision window
                    if (_frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM
                            and _stream_id in _target_status
                            and isinstance(_target_status.get(_stream_id), dict)
                            and _target_status[_stream_id].get('_outbound')):
                        del _target_status[_stream_id]
       
        return _sysIgnore
    
    def sendDataToHBP(self,_d_system,_d_slot,_dst_id,_tmp_bits,_data,dmrpkt,_rf_src,_stream_id,_peer_id):
        #Assemble transmit HBP packet header
        _int_dst_id = int_id(_dst_id)
        _tmp_data = b''.join([_data[:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])
        _tmp_data = b''.join([_tmp_data, dmrpkt])
        systems[_d_system].send_system(_tmp_data,None)
        logger.debug('(%s) UNIT Data Bridged to HBP on slot 1: %s DST_ID: %s',self._system,_d_system,_int_dst_id)
        if CONFIG['REPORTS']['REPORT']:
            systems[_d_system]._report.send_bridgeEvent('UNIT DATA,DATA,TX,{},{},{},{},{},{}'.format(_d_system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), 1, _int_dst_id).encode(encoding='utf-8', errors='ignore'))

    def _cancel_reflector_fallback(self, slot):
        timer = self.STATUS[slot].pop('_reflect_fallback', None)
        if timer is not None and timer.active():
            timer.cancel()

    def _cancel_reflector_timers(self, slot):
        self._cancel_reflector_fallback(slot)

    def _reflector_fallback_cb(self, int_dst_id, rf_src, peer_id, slot, stream_id, lang):
        if self.STATUS[slot].get('_reflect_announced') == stream_id:
            return
        if self.STATUS[slot].get('RX_STREAM_ID') != stream_id:
            return
        _say = self._build_reflector_announce_say(int_dst_id, slot, lang)
        if _say:
            logger.info('(%s) IPSC reflector speech fallback (no VTERM, private call to %s)',
                        self._system, int_dst_id)
            self._play_reflector_announcement(
                _say, rf_src, peer_id, slot, stream_id, int_dst_id)

    def _schedule_reflector_fallback(self, int_dst_id, rf_src, peer_id, slot, stream_id, lang):
        self._cancel_reflector_fallback(slot)
        self.STATUS[slot]['_reflect_fallback'] = reactor.callLater(
            6.0, self._reflector_fallback_cb,
            int_dst_id, rf_src, peer_id, slot, stream_id, lang)

    def _build_reflector_announce_say(self, int_dst_id, slot, lang):
        """Build AMBE phrase list for dial-a-tg private-call announcements."""
        _ipsc = CONFIG['SYSTEMS'][self._system]['MODE'] == 'IPSC'
        _say = [words[lang]['silence']]

        if int_dst_id < 8 or int_dst_id == 9:
            logger.info('(%s) Reflector: voice called - TG <  8 or 9 - "busy""', self._system)
            _say.append(words[lang]['busy'])
            _say.append(words[lang]['silence'])
            self.STATUS[slot]['_stopTgAnnounce'] = True

        if CONFIG['ALLSTAR']['ENABLED'] and int_dst_id == 8:
            logger.info('(%s) Reflector: voice called - TG 8 AllStar"', self._system)
            _say.append(words[lang]['all-star-link-mode'])
            _say.append(words[lang]['silence'])
            self.STATUS[slot]['_stopTgAnnounce'] = True
            self.STATUS[slot]['_allStarMode'] = True
            reactor.callLater(30, self._reset_allstar_mode, slot)
        elif not CONFIG['ALLSTAR']['ENABLED'] and int_dst_id == 8:
            logger.info('(%s) Reflector: TG 8 AllStar not enabled"', self._system)
            _say.append(words[lang]['busy'])
            _say.append(words[lang]['silence'])
            self.STATUS[slot]['_stopTgAnnounce'] = True

        if int_dst_id == 4000:
            logger.info('(%s) Reflector: voice called - 4000 "not linked"', self._system)
            _say.append(words[lang]['notlinked'])
            _say.append(words[lang]['silence'])

        elif int_dst_id == 5000:
            _active = False
            for _bridge in BRIDGES:
                if _bridge[0:1] != '#' or is_dial_service_code(_bridge[1:]):
                    continue
                for _system in BRIDGES[_bridge]:
                    _dehash_bridge = _bridge[1:]
                    if _system['SYSTEM'] == self._system and slot == _system['TS']:
                        if _system['ACTIVE'] == True:
                            logger.info('(%s) Reflector: voice called - 5000 status - "linked to %s"',
                                        self._system, _dehash_bridge)
                            if not _ipsc:
                                _say.append(words[lang]['silence'])
                            _say.append(words[lang]['linkedto'])
                            if not _ipsc:
                                _say.append(words[lang]['silence'])
                            _say.append(words[lang]['to'])
                            if not _ipsc:
                                _say.append(words[lang]['silence'])
                                _say.append(words[lang]['silence'])
                            for num in str(_dehash_bridge):
                                _say.append(words[lang][num])
                            _active = True
                            break
            if _active == False:
                logger.info('(%s) Reflector: voice called - 5000 status - "not linked"', self._system)
                _say.append(words[lang]['notlinked'])

        elif int_dst_id >= 9991 and int_dst_id <= 9999:
            self.STATUS[slot]['_stopTgAnnounce'] = True
            reactor.callInThread(playFileOnRequest, self, int_dst_id)
            return None

        elif is_parrot_talkgroup(int_dst_id):
            # Parrot uses echo playback — no "linked to …" dial-a-tg announcement.
            self.STATUS[slot]['_stopTgAnnounce'] = True
            return None

        elif not self.STATUS[slot]['_stopTgAnnounce']:
            logger.info('(%s) Reflector: voice called (linking)  "linked to %s"', self._system, int_dst_id)
            if not _ipsc:
                _say.append(words[lang]['silence'])
            _say.append(words[lang]['linkedto'])
            if not _ipsc:
                _say.append(words[lang]['silence'])
            _say.append(words[lang]['to'])
            if not _ipsc:
                _say.append(words[lang]['silence'])
                _say.append(words[lang]['silence'])
            for num in str(int_dst_id):
                _say.append(words[lang][num])

        return _say if len(_say) > 1 else None

    def _play_reflector_announcement(self, _say, rf_src, peer_id, slot, stream_id,
                                     int_dst_id=None):
        if not _say:
            return
        if self.STATUS[slot].get('_reflect_announced') == stream_id:
            return
        self.STATUS[slot]['_reflect_announced'] = stream_id
        self._cancel_reflector_timers(slot)
        if CONFIG['SYSTEMS'][self._system]['MODE'] == 'IPSC':
            self._reflector_speech_gen = getattr(self, '_reflector_speech_gen', 0) + 1
            _gen = self._reflector_speech_gen
            # Moto repeaters expect the reply from the ID that was private-called.
            reply_as = bytes_3(int_dst_id if int_dst_id is not None else 5000)
            hbp_slot = 1 if slot == 2 else 0
            speech = pkt_gen(reply_as, rf_src, peer_id, hbp_slot, _say, private_call=True)
            reactor.callInThread(
                self.ipsc_reflector_speech, speech, slot, peer_id, _gen, int_dst_id)
        else:
            speech = pkt_gen(bytes_3(5000), bytes_3(9), bytes_4(9), 1, _say)
            reactor.callInThread(sendSpeech, self, speech)

    def _reset_allstar_mode(self, slot):
        self.STATUS[slot]['_allStarMode'] = False
        logger.info('(%s) Reset all star mode -> dial mode', self._system)

    def _relay_unit_voice_packet(self, _dst_id, _slot, _bits, _data, dmrpkt, _peer_id=None):
        """Forward one unit-voice DMRD frame toward a destination system (parrot echo path)."""
        _int_dst_id = int_id(_dst_id)

        def _send(_d_system, _d_slot):
            if _d_system == self._system or _d_system not in systems:
                return False
            _send_bits = _bits ^ (1 << 7) if _slot != _d_slot else _bits
            _tmp_data = b''.join([
                _data[:15], _send_bits.to_bytes(1, 'big'), _data[16:20], dmrpkt,
            ])
            systems[_d_system].send_system(_tmp_data)
            logger.debug('(%s) UNIT voice bridged to %s slot %s DST %s',
                         self._system, _d_system, _d_slot, _int_dst_id)
            return True

        if _dst_id in SUB_MAP:
            try:
                _entry = SUB_MAP[_dst_id]
                _d_system = _entry[0]
                _d_slot = _entry[1]
                if _send(_d_system, _d_slot):
                    return
            except (TypeError, ValueError, IndexError):
                pass

        for _d_system in systems:
            if _d_system == self._system:
                continue
            _mode = CONFIG['SYSTEMS'][_d_system].get('MODE')
            if _mode not in ('MASTER', 'IPSC'):
                continue
            _peers = CONFIG['SYSTEMS'][_d_system].get('PEERS') or {}
            for _to_peer in _peers:
                if str(int_id(_to_peer))[:7] == str(_int_dst_id)[:7]:
                    if _send(_d_system, _slot):
                        return

        if CONFIG['SYSTEMS'][self._system].get('MODE') != 'IPSC':
            for _d_system in systems:
                if _d_system == self._system:
                    continue
                if CONFIG['SYSTEMS'][_d_system].get('MODE') != 'IPSC':
                    continue
                if not CONFIG['SYSTEMS'][_d_system].get('ENABLED'):
                    continue
                _peers = CONFIG['SYSTEMS'][_d_system].get('PEERS') or {}
                for _to_peer in _peers:
                    _int_peer = int_id(_to_peer)
                    if (_int_peer == _int_dst_id
                            or str(_int_peer)[:7] == str(_int_dst_id)[:7]):
                        if _send(_d_system, _slot):
                            return

    def _forward_unit_voice(self, _dst_id, _slot, _bits, _data, dmrpkt, _stream_id, _peer_id):
        """Bridge unit (private) voice DMRD to SUB_MAP destination, hotspot peer, or IPSC."""
        self._relay_unit_voice_packet(_dst_id, _slot, _bits, _data, dmrpkt, _peer_id)

    def _forward_parrot_unit_voice(self, _dst_id, _slot, _bits, _data, dmrpkt):
        """Send unit-voice to the PARROT playback peer (private call to TG 9990)."""
        if 'PARROT' not in systems or not CONFIG['SYSTEMS'].get('PARROT', {}).get('ENABLED'):
            logger.warning('(%s) Parrot private call but PARROT system is not enabled', self._system)
            return
        _target_slot = 2
        _send_bits = _bits ^ (1 << 7) if _slot != _target_slot else _bits
        _tmp_data = b''.join([
            _data[:15], _send_bits.to_bytes(1, 'big'), _data[16:20], dmrpkt,
        ])
        systems['PARROT'].send_system(_tmp_data)
        logger.debug('(%s) Parrot unit voice forwarded to PARROT DST %s',
                     self._system, int_id(_dst_id))
            
    def sendDataToOBP(self,_target,_data,dmrpkt,pkt_time,_stream_id,_dst_id,_peer_id,_rf_src,_bits,_slot,_hops = b'',_ber = b'\x00', _rssi = b'\x00',_source_server = b'\x00\x00\x00\x00', _source_rptr = b'\x00\x00\x00\x00'):
 #       _sysIgnore = sysIgnore
        _source_server = self._CONFIG['GLOBAL']['SERVER_ID']
        _source_rptr = _peer_id
        _int_dst_id = int_id(_dst_id)
        _target_status = systems[_target].STATUS
        _target_system = self._CONFIG['SYSTEMS'][_target]
        
        #We want to ignore this system and TS combination if it's called again for this packet
#        _sysIgnore.append((_target,_target['TS']))
        
        #If target has missed 6 (in 1 min) of keepalives, don't send
        if _target_system['ENHANCED_OBP'] and '_bcka' in _target_system and _target_system['_bcka'] < pkt_time - 60:
            return
        
        if (_stream_id not in _target_status):
            # This is a new call stream on the target
            _target_status[_stream_id] = {
                'START':     pkt_time,
                'CONTENTION':False,
                'RFS':       _rf_src,
                'TGID':      _dst_id,
                'RX_PEER':   _peer_id,
                '_outbound': True,
                'packets': 0,
                'loss': 0,
            }
            
        # Record the time of this packet so we can later identify a stale stream
        _target_status[_stream_id]['LAST'] = pkt_time
        # Clear the TS bit -- all OpenBridge streams are effectively on TS1
        #_tmp_bits = _bits & ~(1 << 7)
        #rewrite slot if required
        if _slot == 2:
            _tmp_bits = _bits ^ 1 << 7
        else: 
            _tmp_bits = _bits 
        #Assemble transmit HBP packet header
        _tmp_data = b''.join([_data[:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])
        _tmp_data = b''.join([_tmp_data, dmrpkt])
        systems[_target].send_system(_tmp_data,b'',_ber,_rssi,_source_server,_source_rptr)
        logger.debug('(%s) UNIT Data Bridged to OBP System: %s DST_ID: %s', self._system, _target,_int_dst_id)
        if CONFIG['REPORTS']['REPORT']:
            systems[_target]._report.send_bridgeEvent('UNIT DATA,DATA,TX,{},{},{},{},{},{}'.format(_target, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), 1, _int_dst_id).encode(encoding='utf-8', errors='ignore'))
    

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):
        pkt_time = time()
        dmrpkt = _data[20:53]
        
        _ber = _data[53:54]
        _rssi = _data[54:55]
        
        _bits = _data[15]
        
        _source_server = self._CONFIG['GLOBAL']['SERVER_ID']
        
        _source_rptr = _peer_id
        
        #_pkt_crc = Crc32.calc(_data[4:53])
        #_pkt_crc = hash(_data).digest()
        
        #Use blake2b hash
        _h = blake2b(digest_size=16)
        _h.update(_data)
        _pkt_crc = _h.digest()
        
        _nine = bytes_3(9)
        
        _lang = CONFIG['SYSTEMS'][self._system]['ANNOUNCEMENT_LANGUAGE']
        
        _int_dst_id = int_id(_dst_id)

        # Assume this is not a data call. We use this to prevent SMS/GPS data from triggering a reflector.
        _data_call = False
        _voice_call = False
        
        # Add system to SUB_MAP - initialize with current TG as None (will be updated for group calls)
        # New format: SUB_MAP[subscriber] = (system, ts, tg, timestamp, peer_id)
        # Keep existing TG if subscriber already in map, else set to None
        _existing_tg = None
        if _rf_src in SUB_MAP:
            try:
                if len(SUB_MAP[_rf_src]) == 5:
                    _existing_tg = SUB_MAP[_rf_src][2]  # Keep existing sticky TG
                elif len(SUB_MAP[_rf_src]) == 4:
                    _existing_tg = SUB_MAP[_rf_src][2]  # Keep existing sticky TG (old format)
                elif len(SUB_MAP[_rf_src]) == 3:
                    _existing_tg = None  # Old format, no TG
            except (TypeError, IndexError):
                _existing_tg = None
        SUB_MAP[_rf_src] = (self._system, _slot, _existing_tg, pkt_time, _peer_id)
        
        def resetallStarMode():
            self.STATUS[_slot]['_allStarMode'] = False
            logger.info('(%s) Reset all star mode -> dial mode',self._system)
        
        #Rewrite GPS Data comming in as a group call to a unit call
        #if (_call_type == 'group' or _call_type == 'vcsbk') and _int_dst_id == 900999:
            #_bits = header(_slot,'unit',_bits)
            #logger.info('(%s) Type Rewrite - GPS data from ID: %s,  on TG 900999 rewritten to unit call to ID 900999 : bits %s',self._system,int_id(_rf_src),_bits)
            #_call_type == 'unit'
       
       
        if _call_type == 'unit' and (_dtype_vseq == 6 or _dtype_vseq == 7 or _dtype_vseq == 8 or (_stream_id != self.STATUS[_slot]['RX_STREAM_ID'] and _dtype_vseq == 3)):
            _data_call = True
            
            self.STATUS[_slot]['packets'] = 0
            self.STATUS[_slot]['crcs'] = set()
            
            if _dtype_vseq == 3:
                logger.info('(%s) *UNIT CSBK* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) DST_ID %s (%s), TS %s', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('UNIT CSBK,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
            elif _dtype_vseq == 6:
                logger.info('(%s) *UNIT DATA HEADER* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) DST_ID %s (%s), TS %s', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('UNIT DATA HEADER,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
            elif _dtype_vseq == 7:
                    logger.info('(%s) *UNIT VCSBK 1/2 DATA BLOCK * STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                            self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                    if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('UNIT VCSBK 1/2 DATA BLOCK,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
            elif _dtype_vseq == 8:
                    logger.info('(%s) *UNIT VCSBK 3/4 DATA BLOCK * STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                            self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                    if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('UNIT VCSBK 3/4 DATA BLOCK,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
            else:
                    logger.info('(%s) *UNKNOW TYPE* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                            self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
            
            
            #Send all data to DATA-GATEWAY if enabled and valid
            if CONFIG['GLOBAL']['DATA_GATEWAY'] and 'DATA-GATEWAY' in CONFIG['SYSTEMS'] and CONFIG['SYSTEMS']['DATA-GATEWAY']['MODE'] == 'OPENBRIDGE' and CONFIG['SYSTEMS']['DATA-GATEWAY']['ENABLED']:
                logger.debug('(%s) DATA packet sent to DATA-GATEWAY',self._system)
                self.sendDataToOBP('DATA-GATEWAY',_data,dmrpkt,pkt_time,_stream_id,_dst_id,_peer_id,_rf_src,_bits,_slot,b'')
                
            #Send to all openbridges 
            # sysIgnore = []
            for system in systems:
                if system  == self._system:
                    continue
                if system == 'DATA-GATEWAY':
                    continue
                #We only want to send data calls to individual IDs via FreeBridge (not OpenBridge)
                if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE' and CONFIG['SYSTEMS'][system]['VER'] > 1 and (_int_dst_id >= 1000000):
                    self.sendDataToOBP(system,_data,dmrpkt,pkt_time,_stream_id,_dst_id,_peer_id,_rf_src,_bits,_slot,b'')
                    
            #If destination ID is in the Subscriber Map
            if _dst_id in SUB_MAP:
                # BACKWARDS COMPATIBILITY: Handle 3, 4, and 5-element formats
                try:
                    if len(SUB_MAP[_dst_id]) == 5:
                        (_d_system, _d_slot, _d_tg, _d_time, _d_peer_id) = SUB_MAP[_dst_id]
                    elif len(SUB_MAP[_dst_id]) == 4:
                        (_d_system, _d_slot, _d_tg, _d_time) = SUB_MAP[_dst_id]
                    else:  # Old 3-element format
                        (_d_system, _d_slot, _d_time) = SUB_MAP[_dst_id]
                except (TypeError, ValueError):
                    logger.warning('(%s) Invalid SUB_MAP entry for destination %s', self._system, int_id(_dst_id))
                else:
                    _dst_slot  = systems[_d_system].STATUS[_d_slot]
                    logger.info('(%s) SUB_MAP matched, System: %s Slot: %s, Time: %s',self._system, _d_system,_d_slot,_d_time)
                    #If slot is idle for RX and TX
                    if (_dst_slot['RX_TYPE'] == HBPF_SLT_VTERM) and (_dst_slot['TX_TYPE'] == HBPF_SLT_VTERM) and (time() - _dst_slot['TX_TIME'] > CONFIG['SYSTEMS'][_d_system]['GROUP_HANGTIME']):                
                    #rewrite slot if required
                        if _slot != _d_slot:
                            _tmp_bits = _bits ^ 1 << 7
                        else: 
                            _tmp_bits = _bits                        
                        self.sendDataToHBP(_d_system,_d_slot,_dst_id,_tmp_bits,_data,dmrpkt,_rf_src,_stream_id,_peer_id)
                            
                    else:
                        logger.debug('(%s) UNIT Data not bridged to HBP on slot 1 - target busy: %s DST_ID: %s',self._system,_d_system,_int_dst_id)
            
            elif _int_dst_id == 900999:
                    if 'D-APRS' in systems and CONFIG['SYSTEMS']['D-APRS']['MODE'] == 'MASTER':
                        _d_system = 'D-APRS'
                        _d_slot = _slot
                        _dst_slot  = systems['D-APRS'].STATUS[_slot]
                        logger.info('(%s) D-APRS ID matched, System: %s Slot: %s',self._system, _d_system,_slot)
                        #If slot is idle for RX and TX
                        if (_dst_slot['RX_TYPE'] == HBPF_SLT_VTERM) and (_dst_slot['TX_TYPE'] == HBPF_SLT_VTERM) and (time() - _dst_slot['TX_TIME'] > CONFIG['SYSTEMS'][_d_system]['GROUP_HANGTIME']):
                            #We will allow the system to use both slots
                            _tmp_bits = _bits
                            self.sendDataToHBP(_d_system,_d_slot,_dst_id,_tmp_bits,_data,dmrpkt,_rf_src,_stream_id,_peer_id)
                                
                        else:
                            logger.debug('(%s) UNIT Data not bridged to HBP on slot %s - target busy: %s DST_ID: %s',self._system,_d_slot,_d_system,_int_dst_id)
      
            else:                
                #If destination ID is logged in as a hotspot or IPSC repeater
                for _d_system in systems:
                    if not is_routing_master(CONFIG['SYSTEMS'][_d_system]['MODE']):
                        continue
                    for _to_peer in CONFIG['SYSTEMS'][_d_system]['PEERS']:
                            _int_to_peer = int_id(_to_peer)
                            if (str(_int_to_peer)[:7] == str(_int_dst_id)[:7]):
                                #(_d_system,_d_slot,_d_time) = SUB_MAP[_dst_id]
                                _d_slot = 2
                                _dst_slot  = systems[_d_system].STATUS[_d_slot]
                                logger.info('(%s) User Peer Hotspot ID matched, System: %s Slot: %s',self._system, _d_system,_d_slot)
                                #If slot is idle for RX and TX
                                if (_dst_slot['RX_TYPE'] == HBPF_SLT_VTERM) and (_dst_slot['TX_TYPE'] == HBPF_SLT_VTERM) and (time() - _dst_slot['TX_TIME'] > CONFIG['SYSTEMS'][_d_system]['GROUP_HANGTIME']):
                                #Always use slot2 for hotspots - many of them are simplex and this 
                                #is the convention 
                                    #rewrite slot if required (slot 2 is used on hotspots)
                                    if _slot != 2:
                                        _tmp_bits = _bits ^ 1 << 7
                                    else: 
                                        _tmp_bits = _bits
                                    self.sendDataToHBP(_d_system,_d_slot,_dst_id,_tmp_bits,_data,dmrpkt,_rf_src,_stream_id,_peer_id)
                                    
                                else:
                                    logger.debug('(%s) UNIT Data not bridged to HBP on slot %s - target busy: %s DST_ID: %s',self._system,_d_slot,_d_system,_int_dst_id)
                                
        #Handle AMI private calls
        if _call_type == 'unit' and not _data_call and self.STATUS[_slot]['_allStarMode'] and CONFIG['ALLSTAR']['ENABLED']:
            if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
                 logger.info('(%s) AMI: Private call from %s to %s',self._system, int_id(_rf_src), _int_dst_id)
                
                    
            if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM) and (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM):
                
                if _int_dst_id == 4000:
                    logger.info('(%s) AMI: Private call from %s to %s (Disconnect)',self._system, int_id(_rf_src), _int_dst_id)
                    AMIOBJ.send_command('ilink 6 0')                    
                elif _int_dst_id == 5000:
                    logger.info('(%s) AMI: Private call from %s to %s (Status)',self._system, int_id(_rf_src), _int_dst_id)
                    AMIOBJ.send_command('ilink 5 0')                    
                else:
                    logger.info('(%s) AMI: Private call from %s to %s (Link)',self._system, int_id(_rf_src), _int_dst_id)
                    AMIOBJ.send_command('ilink 6 0')
                    AMIOBJ.send_command('ilink 3 ' + str(_int_dst_id))
                
            # Mark status variables for use later
            self.STATUS[_slot]['RX_PEER']      = _peer_id
            self.STATUS[_slot]['RX_SEQ']       = _seq
            self.STATUS[_slot]['RX_RFS']       = _rf_src
            self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
            self.STATUS[_slot]['RX_TGID']      = _dst_id
            self.STATUS[_slot]['RX_TIME']      = pkt_time
            self._assign_rx_stream_id(_slot, _stream_id)
            self.STATUS[_slot]['VOICE_STREAM'] = _voice_call
            
            self.STATUS[_slot]['packets'] = self.STATUS[_slot]['packets'] +1 
            
                
        
        #Handle  private voice calls (for reflectors and parrot)
        elif _call_type == 'unit' and not _data_call:
            if self._system == 'PARROT':
                self._relay_unit_voice_packet(_dst_id, _slot, _bits, _data, dmrpkt, _peer_id)
                self.STATUS[_slot]['RX_PEER']      = _peer_id
                self.STATUS[_slot]['RX_SEQ']       = _seq
                self.STATUS[_slot]['RX_RFS']       = _rf_src
                self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
                self.STATUS[_slot]['RX_TGID']      = _dst_id
                self.STATUS[_slot]['RX_TIME']      = pkt_time
                self._assign_rx_stream_id(_slot, _stream_id)
                self.STATUS[_slot]['VOICE_STREAM'] = _voice_call
                self.STATUS[_slot]['packets'] = self.STATUS[_slot]['packets'] + 1

            elif is_parrot_talkgroup(_int_dst_id):
                if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
                    self.STATUS[_slot]['packets'] = 0
                    self.STATUS[_slot]['crcs'] = set()
                    logger.info('(%s) Parrot: Private call from %s to %s',
                                self._system, int_id(_rf_src), _int_dst_id)
                self._forward_parrot_unit_voice(_dst_id, _slot, _bits, _data, dmrpkt)
                self.STATUS[_slot]['RX_PEER']      = _peer_id
                self.STATUS[_slot]['RX_SEQ']       = _seq
                self.STATUS[_slot]['RX_RFS']       = _rf_src
                self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
                self.STATUS[_slot]['RX_TGID']      = _dst_id
                self.STATUS[_slot]['RX_TIME']      = pkt_time
                self._assign_rx_stream_id(_slot, _stream_id)
                self.STATUS[_slot]['VOICE_STREAM'] = _voice_call
                self.STATUS[_slot]['packets'] = self.STATUS[_slot]['packets'] + 1

            elif not self.STATUS[_slot]['_allStarMode']:
                if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
                
                    self.STATUS[_slot]['packets'] = 0
                    self.STATUS[_slot]['crcs'] = set()
                
                    self.STATUS[_slot]['_stopTgAnnounce'] = False
                    self.STATUS[_slot]['_reflect_announced'] = None
                    self._cancel_reflector_timers(_slot)
                
                    logger.info('(%s) Reflector: Private call from %s to %s',self._system, int_id(_rf_src), _int_dst_id)
                    if _int_dst_id == 4000:
                        disconnect_dial_reflectors(self._system)
                        clear_subscriber_on_disconnect(self._system, _rf_src, _peer_id)
                    if _int_dst_id >= 5 and _int_dst_id != 8  and _int_dst_id != 9 and _int_dst_id <= 999999:
                        _bridgename = ''.join(['#',str(_int_dst_id)])
                        if _bridgename not in BRIDGES and not (_int_dst_id >= 4000 and _int_dst_id <= 5000) and not (_int_dst_id >=9991 and _int_dst_id <= 9999):
                                logger.info('(%s) [A] Reflector for TG %s does not exist. Creating as User Activated. Timeout: %s',self._system, _int_dst_id,CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'])
                                make_single_reflector(_dst_id,CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'],self._system)
                    
                        if (_int_dst_id > 5 and _int_dst_id != 9 and not is_dial_service_code(_int_dst_id)
                                and not (_int_dst_id >=9991 and _int_dst_id <= 9999)):
                            for _bridge in BRIDGES:
                                if _bridge[0:1] != '#':
                                    continue
                                for _system in BRIDGES[_bridge]:
                                    _dehash_bridge = _bridge[1:]
                                    if _system['SYSTEM'] == self._system:
                                        # TGID matches a rule source, reset its timer
                                        if (bridge_transmission_matches_rule(
                                                _bridge, _int_dst_id, _dst_id, _slot, _system)
                                                and reflector_timer_reset_allowed(
                                                    _bridge, _system, _rf_src, _peer_id)
                                                and ((_system['TO_TYPE'] == 'ON' and (_system['ACTIVE'] == True))
                                                     or (_system['TO_TYPE'] == 'OFF' and _system['ACTIVE'] == False))):
                                            _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                            logger.info('(%s) [B] Transmission match for Reflector: %s. Reset timeout to %s', self._system, _bridge, _system['TIMER'])
                                
                                    # TGID matches an ACTIVATION trigger
                                    if (not is_dial_service_code(_int_dst_id)
                                            and _int_dst_id == int(_dehash_bridge) and _system['SYSTEM'] == self._system and  _slot == _system['TS']):
                                        # Set the matching rule as ACTIVE
                                        if _system['ACTIVE'] == False:
                                            _system['ACTIVE'] = True
                                            _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                            set_reflector_link_owner(_system, _rf_src, _peer_id)
                                            logger.info('(%s) [C] Reflector: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                            # Cancel the timer if we've enabled an "OFF" type timeout
                                            if _system['TO_TYPE'] == 'OFF':
                                                _system['TIMER'] = pkt_time
                                                logger.info('(%s) [D] Reflector: %s has an "OFF" timer and set to "ON": timeout timer cancelled', self._system, _bridge)
                                    # Reset the timer for the rule (linked private call only)
                                    if (_system['SYSTEM'] == self._system
                                            and not is_dial_service_code(_int_dst_id)
                                            and _int_dst_id == int(_dehash_bridge)
                                            and _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON'):
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [E] Reflector: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)

                                    # TGID matches an DE-ACTIVATION trigger
                                    #Single TG mode
                                    if (_dst_id in _system['OFF']  or _dst_id in _system['RESET'] or (_int_dst_id != int(_dehash_bridge)) and _system['SYSTEM'] == self._system and _slot == _system['TS']):
                                            # Set the matching rule as ACTIVE
                                            #Single TG mode
                                            if _dst_id in _system['OFF'] or _int_dst_id != int(_dehash_bridge) :
                                                if _system['ACTIVE'] == True:
                                                    _system['ACTIVE'] = False
                                                    clear_reflector_link_owner(_system)
                                                    logger.info('(%s) [F] Reflector: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                                    # Cancel the timer if we've enabled an "ON" type timeout
                                                    if _system['TO_TYPE'] == 'ON':
                                                        _system['TIMER'] = pkt_time
                                                        logger.info('(%s) [G] Reflector: %s has ON timer and set to "OFF": timeout timer cancelled', self._system, _bridge)
                                            # Reset the timer for the rule
                                            if _system['ACTIVE'] == False and _system['TO_TYPE'] == 'OFF':
                                                _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                                logger.info('(%s) [H] Reflector: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)
                                            # Cancel the timer if we've enabled an "ON" type timeout
                                            if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON' and _dst_id in _system['OFF']:
                                                _system['TIMER'] = pkt_time
                                                logger.info('(%s) [I] Reflector: %s has ON timer and set to "OFF": timeout timer cancelled', self._system, _bridge)
                            deactivate_other_dynamic_reflectors(self._system, _bridgename, _slot)

                    if (CONFIG['SYSTEMS'][self._system]['MODE'] == 'IPSC'
                            and is_reflector_private_destination(_int_dst_id)):
                        self._schedule_reflector_fallback(
                            _int_dst_id, _rf_src, _peer_id, _slot, _stream_id, _lang)
            
                if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM) and (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM):
                    _reset = reset_dial_reflector_timers_on_user_activity(
                        BRIDGES, self._system, _rf_src, _peer_id, _slot, pkt_time,
                        _int_dst_id, group_call=False)
                    for _rb in _reset:
                        logger.info('(%s) [P] Dial-a-tg timer reset on private call end: %s', self._system, _rb)
                    if _reset:
                        notify_bridge_table_updated()
                    _say = self._build_reflector_announce_say(_int_dst_id, _slot, _lang)
                    if _say:
                        logger.info('(%s) IPSC reflector: PTT released, speech in 1s', self._system)
                        self._play_reflector_announcement(
                            _say, _rf_src, _peer_id, _slot, _stream_id, _int_dst_id)

                if (not is_reflector_private_destination(_int_dst_id)
                        and _int_dst_id not in (8, 9)):
                    self._forward_unit_voice(
                        _dst_id, _slot, _bits, _data, dmrpkt, _stream_id, _peer_id)

                # Mark status variables for use later
                self.STATUS[_slot]['RX_PEER']      = _peer_id
                self.STATUS[_slot]['RX_SEQ']       = _seq
                self.STATUS[_slot]['RX_RFS']       = _rf_src
                self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
                self.STATUS[_slot]['RX_TGID']      = _dst_id
                self.STATUS[_slot]['RX_TIME']      = pkt_time
                self._assign_rx_stream_id(_slot, _stream_id)
                self.STATUS[_slot]['VOICE_STREAM'] = _voice_call
            
                self.STATUS[_slot]['packets'] = self.STATUS[_slot]['packets'] +1                
        
        #Handle group calls
        if _call_type == 'group' or _call_type == 'vcsbk':

            if self.STATUS[_slot].get('RX_FINISHED_STREAM_ID') == _stream_id:
                if not self.STATUS[_slot].get('RX_FINISHED_STREAM_LOG'):
                    logger.debug(
                        "(%s) HBP *LoopControl* STREAM ID: %s ALREADY FINISHED FROM THIS SOURCE, IGNORING",
                        self._system,
                        int_id(_stream_id),
                    )
                    self.STATUS[_slot]['RX_FINISHED_STREAM_LOG'] = True
                return

            # Is this a new call stream?
            if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
                cancel_generated_voice(self.STATUS[_slot])
                self.STATUS[_slot]['RX_FINISHED_STREAM_ID'] = b'\x00'
                self.STATUS[_slot]['RX_FINISHED_STREAM_LOG'] = False
                
                self.STATUS[_slot]['packets'] = 0
                self.STATUS[_slot]['loss'] = 0
                self.STATUS[_slot]['crcs'] = set()
                
                if (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM) and (pkt_time < (self.STATUS[_slot]['RX_TIME'] + STREAM_TO)) and (_rf_src != self.STATUS[_slot]['RX_RFS']):
                    logger.warning('(%s) Packet received with STREAM ID: %s <FROM> SUB: %s PEER: %s <TO> TGID %s, SLOT %s collided with existing call', self._system, int_id(_stream_id), int_id(_rf_src), int_id(_peer_id), int_id(_dst_id), _slot)
                    return

                # This is a new call stream — FreeDMR: clear SEQ/dup state after collision
                # gate so a collided attempt does not wipe the active stream.
                self.STATUS[_slot]['lastSeq'] = False
                self.STATUS[_slot]['lastData'] = False
                self.STATUS[_slot]['RX_START'] = pkt_time
                self.STATUS[_slot].pop('LOOPLOG', None)
                
                if _call_type == 'group' :
                    if _dtype_vseq == 6:
                        logger.info('(%s) *DATA HEADER* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                                self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                        if CONFIG['REPORTS']['REPORT']:
                            self._report.send_bridgeEvent('DATA HEADER,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
                    
                    else:
                        # Gate START RX report on LoopControl ownership (report-only).
                        _hbp_active, _hbp_sys = _find_hbp_stream_rx_owner(_stream_id, exclude=self._system)
                        _obp_first = False
                        for _obp_system in _OBP_SYSTEMS:
                            if _obp_system == self._system:
                                continue
                            _obp_st = systems[_obp_system].STATUS.get(_stream_id)
                            if (_obp_st and not _obp_st.get('_outbound')
                                    and '1ST' in _obp_st and _obp_st['TGID'] == _dst_id):
                                _obp_first = True
                                break
                        _report_start = should_report_hbp_rx_start(
                            _hbp_sys if _hbp_active else None, _obp_first)
                        if not _report_start:
                            self.STATUS[_slot]['LOOPLOG'] = True
                            logger.debug(
                                "(%s) HBP *LoopControl* START RX suppressed STREAM ID: %s TG: %s "
                                "(HBP=%s OBP=%s)",
                                self._system, int_id(_stream_id), int_id(_dst_id),
                                _hbp_sys if _hbp_active else None, _obp_first)
                        else:
                            logger.info('(%s) *CALL START* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                                self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                            if CONFIG['REPORTS']['REPORT']:
                                self._report.send_bridgeEvent('GROUP VOICE,START,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
                else:
                    logger.info('(%s) *VCSBK* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s _dtype_vseq: %s', 
                            self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, _dtype_vseq)
                    if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('OTHER DATA,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))

                # If we can, use the LC from the voice header as to keep all options intact
                if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                    decoded = decode.voice_head_term(dmrpkt)
                    self.STATUS[_slot]['RX_LC'] = decoded['LC']

                # If we don't have a voice header then don't wait to decode it from the Embedded LC
                # just make a new one from the HBP header. This is good enough, and it saves lots of time
                else:
                    self.STATUS[_slot]['RX_LC'] = b''.join([LC_OPT,_dst_id,_rf_src])

            #Create default bridge for unknown TG
                if int_id(_dst_id) == 4000:
                    disconnect_dial_reflectors(self._system)
                    clear_subscriber_on_disconnect(self._system, _rf_src, _peer_id)
                if int_id(_dst_id) >= 5 and int_id(_dst_id) != 9 and int_id(_dst_id) != 4000 and int_id(_dst_id) != 5000  and (str(int_id(_dst_id)) not in BRIDGES):
                    logger.info('(%s) Bridge for TG %s does not exist. Creating as User Activated. Timeout %s',self._system, int_id(_dst_id),CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'])
                    make_single_bridge(_dst_id,self._system,_slot,CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'])
                elif is_routing_master(CONFIG['SYSTEMS'][self._system]['MODE']) and str(int_id(_dst_id)) in BRIDGES:
                    activate_ua_bridge_source(str(int_id(_dst_id)), self._system, _slot, peer_id=_peer_id)
                
                # Update SUB_MAP with the TG for this call
                # This enables sticky TG functionality - subscriber is now associated with this TG
                if _rf_src in SUB_MAP:
                    # BACKWARDS COMPATIBILITY: Handle 3, 4, and 5-element formats
                    _sub_peer_id = None  # Initialize for backwards compatibility
                    try:
                        if len(SUB_MAP[_rf_src]) == 5:
                            _system, _ts, _old_tg, _timestamp, _sub_peer_id = SUB_MAP[_rf_src]
                        elif len(SUB_MAP[_rf_src]) == 4:
                            _system, _ts, _old_tg, _timestamp = SUB_MAP[_rf_src]
                        else:  # Old 3-element format
                            _system, _ts, _timestamp = SUB_MAP[_rf_src]
                            _old_tg = None
                    except (TypeError, ValueError) as e:
                        logger.warning('(%s) Invalid SUB_MAP entry for subscriber %s: %s', 
                                      self._system, int_id(_rf_src), e)
                        _system, _ts, _old_tg, _timestamp = self._system, _slot, None, pkt_time
                    
                    SUB_MAP[_rf_src] = (_system, _ts, _dst_id, pkt_time, _peer_id)
                    
                    # Check if we should log sticky TG change based on per-peer or system-wide setting
                    _sticky_enabled = False
                    if (is_routing_master(CONFIG['SYSTEMS'][self._system]['MODE']) and
                        not system_has_static_tgs(CONFIG['SYSTEMS'][self._system]) and
                        'PEERS' in CONFIG['SYSTEMS'][self._system] and
                        _peer_id in CONFIG['SYSTEMS'][self._system]['PEERS']):
                        # Priority 1: Check peer-specific STICKY setting
                        if 'STICKY' in CONFIG['SYSTEMS'][self._system]['PEERS'][_peer_id]:
                            _sticky_enabled = CONFIG['SYSTEMS'][self._system]['PEERS'][_peer_id]['STICKY']
                        # Priority 2: Check system-wide STICKY_TG setting
                        elif CONFIG['SYSTEMS'][self._system].get('STICKY_TG', False):
                            _sticky_enabled = True
                    
                    if _sticky_enabled and _old_tg and _old_tg != _dst_id:
                        logger.info('(%s) STICKY_TG: Subscriber %s (Peer %s) changed from TG %s to TG %s', 
                                   self._system, int_id(_rf_src), int_id(_peer_id), int_id(_old_tg), int_id(_dst_id))

            self.STATUS[_slot]['packets'] = self.STATUS[_slot]['packets'] +1
            
            if _call_type == 'vcsbk':
                if _dtype_vseq == 7:
                    logger.info('(%s) *VCSBK 1/2 DATA BLOCK * STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                            self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                    if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('VCSBK 1/2 DATA BLOCK,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
                elif _dtype_vseq == 8:
                    logger.info('(%s) *VCSBK 3/4 DATA BLOCK * STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                            self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                    if CONFIG['REPORTS']['REPORT']:
                        self._report.send_bridgeEvent('VCSBK 3/4 DATA BLOCK,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
                        
            # FreeDMR HBP RATE DROP — discard catch-up bursts (soft-client playout)
            if HBP_RATE_DROP_ENABLED:
                call_duration = pkt_time - self.STATUS[_slot]['RX_START']
                # 1s warmup (5ef71eb): avoid false drop right after reactor lag
                if (call_duration > 1.0
                        and self.STATUS[_slot]['packets'] > HBP_RATE_DROP_MIN_PACKETS
                        and (self.STATUS[_slot]['packets'] / call_duration) > HBP_RATE_DROP_MAX_PPS):
                    logger.warning("(%s) *PacketControl* RATE DROP! Stream ID:, %s TGID: %s",self._system,int_id(_stream_id),int_id(_dst_id))
                    self.STATUS[_slot]['LAST'] = pkt_time
                    return
            
            #Timeout
            if self.STATUS[_slot]['RX_START'] + 180 < pkt_time:
                if 'LOOPLOG' not in self.STATUS[_slot] or not self.STATUS[_slot]['LOOPLOG']: 
                    logger.info("(%s) HBP *SOURCE TIMEOUT* STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",self._system, int_id(_stream_id), int_id(_dst_id),_slot)
                    self.STATUS[_slot]['LOOPLOG'] = True
                self.STATUS[_slot]['LAST'] = pkt_time
                return
            
            #LoopControl#
            _hbp_active, _hbp_sys = _find_hbp_stream_rx_owner(_stream_id, exclude=self._system)
            if _hbp_active:
                if 'LOOPLOG' not in self.STATUS[_slot] or not self.STATUS[_slot]['LOOPLOG']:
                    logger.debug("(%s) OBP *LoopControl* FIRST HBP: %s, STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",self._system, _hbp_sys, int_id(_stream_id), int_id(_dst_id), _slot)
                    self.STATUS[_slot]['LOOPLOG'] = True
                self.STATUS[_slot]['LAST'] = pkt_time
                return
            for _obp_system in _OBP_SYSTEMS:
                if _obp_system == self._system:
                    continue
                if (_stream_id in systems[_obp_system].STATUS
                        and not systems[_obp_system].STATUS[_stream_id].get('_outbound')
                        and '1ST' in systems[_obp_system].STATUS[_stream_id]
                        and systems[_obp_system].STATUS[_stream_id]['TGID'] == _dst_id):
                    if 'LOOPLOG' not in self.STATUS[_slot] or not self.STATUS[_slot]['LOOPLOG']:
                        logger.debug("(%s) OBP *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE",self._system, _obp_system, int_id(_stream_id), int_id(_dst_id))
                        self.STATUS[_slot]['LOOPLOG'] = True
                    self.STATUS[_slot]['LAST'] = pkt_time
                    if 'ENHANCED_OBP' in CONFIG['SYSTEMS'][self._system] and CONFIG['SYSTEMS'][self._system]['ENHANCED_OBP'] and '_bcsq' not in self.STATUS[_slot]:
                        systems[self._system].send_bcsq(_dst_id,_stream_id)
                        self.STATUS[_slot]['_bcsq'] = True
                    return
            
            #Duplicate handling#
            #Duplicate complete packet
            if self.STATUS[_slot]['lastData'] and self.STATUS[_slot]['lastData'] == _data and _seq > 1:
                self.STATUS[_slot]['loss'] += 1
                logger.info("(%s) *PacketControl* last packet is a complete duplicate of the previous one, disgarding. Stream ID:, %s TGID: %s",self._system,int_id(_stream_id),int_id(_dst_id))
                return
            _seq_delta = dmrd_seq_delta(_seq, self.STATUS[_slot]['lastSeq'])
            #Handle inbound duplicates
            if _seq_delta == 0:
                self.STATUS[_slot]['loss'] += 1
                logger.debug("(%s) *PacketControl* Duplicate sequence number %s, disgarding. Stream ID:, %s TGID: %s",self._system,_seq,int_id(_stream_id),int_id(_dst_id))
                return
            #Inbound out-of-order packets (wrap-aware)
            if _seq_delta is not None and _seq_delta > 127:
                self.STATUS[_slot]['loss'] += 1
                logger.debug("%s) *PacketControl* Out of order packet - last SEQ: %s, this SEQ: %s,  disgarding. Stream ID:, %s TGID: %s ",self._system,self.STATUS[_slot]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id))
                return
            #Duplicate DMR payload to previuos packet (by hash)
            if _seq > 0 and _pkt_crc in self.STATUS[_slot]['crcs']:
                self.STATUS[_slot]['loss'] += 1
                logger.debug("(%s) *PacketControl* DMR packet payload with hash: %s seen before in this stream, disgarding. Stream ID:, %s TGID: %s, SEQ: %s, packets %s: ",self._system,_pkt_crc,int_id(_stream_id),int_id(_dst_id),_seq,self.STATUS[_slot]['packets'])
                return
            #Inbound missed packets
            if _seq_delta is not None and _seq_delta > 1:
                self.STATUS[_slot]['loss'] += 1
                logger.debug("(%s) *PacketControl* Missed packet(s) - last SEQ: %s, this SEQ: %s. Stream ID:, %s TGID: %s ",self._system,self.STATUS[_slot]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id))
        
            #Save this sequence number 
            self.STATUS[_slot]['lastSeq'] = _seq
            #Save this packet
            self.STATUS[_slot]['lastData'] = _data

            if not is_dial_service_code(int_id(_dst_id)):
                # --- OPTIMISED ROUTING: use BRIDGE_IDX for O(1) lookup instead of O(N*M) full scan ---
                _sysIgnore = deque()
                _lookup_key = (self._system, _slot, _dst_id)
                _candidate_bridges = BRIDGE_IDX.get(_lookup_key)
                _ROUTE_STATS['packets'] += 1
                if _candidate_bridges is None:
                    # Index miss - fall back to full scan and schedule a rebuild.
                    # This should never happen in normal operation; log at WARNING.
                    logger.warning('(%s) HBP BRIDGE_IDX miss for key (%s, %s, %s) '
                                   '- falling back to full scan and rebuilding index',
                                   self._system, self._system, _slot, int_id(_dst_id))
                    _ROUTE_STATS['index_misses'] += 1
                    _ROUTE_STATS['fallbacks'] += 1
                    rebuild_bridge_index()
                    _candidate_bridges = BRIDGE_IDX.get(_lookup_key, set())
                    # Full-scan fallback for safety
                    for _bridge in BRIDGES:
                        for _system in BRIDGES[_bridge]:
                            if _system['SYSTEM'] == self._system and _system['TGID'] == _dst_id and _system['TS'] == _slot and _system['ACTIVE'] == True:
                                _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,False,_sysIgnore,_source_server,_ber,_rssi, _source_rptr)
                                _paired_bridge = paired_group_route_bridge(
                                    _bridge, BRIDGES, _dst_id)
                                if _paired_bridge:
                                    _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_paired_bridge,_system,False,_sysIgnore,_source_server,_ber,_rssi,_source_rptr)
                else:
                    _ROUTE_STATS['index_hits'] += 1
                    for _orig_bridge in list(_candidate_bridges):
                        if _orig_bridge not in BRIDGES:
                            # Stale index entry - skip
                            logger.debug('(%s) HBP BRIDGE_IDX stale entry for bridge %s, skipping',
                                         self._system, _orig_bridge)
                            continue
                        for _system in BRIDGES[_orig_bridge]:
                            if _system['SYSTEM'] == self._system and _system['TGID'] == _dst_id and _system['TS'] == _slot and _system['ACTIVE'] == True:
                                _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_orig_bridge,_system,False,_sysIgnore,_source_server,_ber,_rssi, _source_rptr)
                                # Also route to paired reflector/TG bridge on dial-a-tg (TG 9) only
                                _paired_bridge = paired_group_route_bridge(
                                    _orig_bridge, BRIDGES, _dst_id)
                                if _paired_bridge:
                                    _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_paired_bridge,_system,False,_sysIgnore,_source_server,_ber,_rssi,_source_rptr)
                _log_route_stats()

            # Final actions - Is this a voice terminator?
            if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM) and (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM):
                packet_rate = 0
                loss = 0.00
                call_duration = pkt_time - self.STATUS[_slot]['RX_START']
                if call_duration:
                    packet_rate = self.STATUS[_slot]['packets'] / call_duration
                    loss = (self.STATUS[_slot]['loss'] / self.STATUS[_slot]['packets']) * 100
                logger.info('(%s) *CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, Duration: %.2f,  Packet rate: %.2f/s, LOSS: %.2f%%', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration, packet_rate, loss)
                if CONFIG['REPORTS']['REPORT'] and should_report_stream_end(self.STATUS[_slot]):
                   self._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))
                self.STATUS[_slot]['RX_FINISHED_STREAM_ID'] = _stream_id
                self.STATUS[_slot]['RX_FINISHED_STREAM_LOG'] = False
                
                #Reset back to False  
                self.STATUS[_slot]['lastSeq'] = False
                self.STATUS[_slot]['lastData'] = False

                #
                # Begin in-band signalling for call end. This has nothign to do with routing traffic directly.
                #

                # Iterate the rules dictionary
                _reset = reset_dial_reflector_timers_on_user_activity(
                    BRIDGES, self._system, _rf_src, _peer_id, _slot, pkt_time,
                    _int_dst_id, group_call=True)
                for _rb in _reset:
                    logger.info('(%s) [G9] Dial-a-tg timer reset on group call end: %s', self._system, _rb)
                if _reset:
                    notify_bridge_table_updated()

                for _bridge in group_call_end_bridge_candidates(BRIDGES, _int_dst_id):
                    if not _reflector_bridge_matches_group_call(_bridge, _int_dst_id):
                        continue
                    for _system in BRIDGES[_bridge]:
                        if _system['SYSTEM'] == self._system:

                            # TGID matches a rule source, reset its timer
                            if (bridge_transmission_matches_rule(
                                    _bridge, _int_dst_id, _dst_id, _slot, _system)
                                    and reflector_timer_reset_allowed(
                                        _bridge, _system, _rf_src, _peer_id)
                                    and ((_system['TO_TYPE'] == 'ON' and (_system['ACTIVE'] == True))
                                         or (_system['TO_TYPE'] == 'OFF' and _system['ACTIVE'] == False))):
                                _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                logger.info('(%s) [1] Transmission match for Bridge: %s. Reset timeout to %s', self._system, _bridge, _system['TIMER'])

                            # TGID matches an ACTIVATION trigger
                            # Dial-a-tg # reflectors link via private call only — not group PTT
                            if (_bridge[0:1] != '#' and (_dst_id in _system['ON'] or _dst_id in _system['RESET']) and _slot == _system['TS']):
                                # Set the matching rule as ACTIVE
                                if _dst_id in _system['ON']:
                                    if _system['ACTIVE'] == False:
                                        _system['ACTIVE'] = True
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        if _bridge[0:1] == '#':
                                            set_reflector_link_owner(_system, _rf_src, _peer_id)
                                        logger.info('(%s) [2] Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                        if _system['TO_TYPE'] == 'ON':
                                            notify_bridge_table_updated()
                                        # Cancel the timer if we've enabled an "OFF" type timeout
                                        if _system['TO_TYPE'] == 'OFF':
                                            _system['TIMER'] = pkt_time
                                            logger.info('(%s) [3] Bridge: %s set to "OFF" with an on timer rule: timeout timer cancelled', self._system, _bridge)
                                # Reset the timer for the rule (link owner PTT only on # reflectors)
                                if (_system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON'
                                        and reflector_timer_reset_allowed(
                                            _bridge, _system, _rf_src, _peer_id)):
                                    _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                    logger.info('(%s) [4] Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)

                            # TGID matches an DE-ACTIVATION trigger
                            #Single TG mode
                            # TO_TYPE NONE bridges (e.g. parrot rules) must not be cleared here —
                            # dial-a-tg PTT on TG 9 would otherwise deactivate bridge 9990 (9 != 9990).
                            if (is_routing_master(CONFIG['SYSTEMS'][self._system]['MODE']) and CONFIG['SYSTEMS'][self._system]['SINGLE_MODE']) == True and _system['TO_TYPE'] != 'NONE':
                                if (_dst_id in _system['OFF'] or _dst_id in _system['RESET']
                                        or reflector_single_mode_wrong_tg(
                                            _int_dst_id, _dst_id, _bridge, _system)) and _slot == _system['TS']:
                                #if (_dst_id in _system['OFF']  or _dst_id in _system['RESET']) and _slot == _system['TS']:
                                    # Set the matching rule as ACTIVE
                                    #Single TG mode
                                    if (_dst_id in _system['OFF']
                                            or reflector_single_mode_wrong_tg(
                                                _int_dst_id, _dst_id, _bridge, _system)):
                                    #if _dst_id in _system['OFF']:
                                        if (_system['TO_TYPE'] == 'OFF' and _system['ACTIVE'] == True):
                                            pass  # static / default reflector — never torn down by wrong-TG traffic
                                        elif _system['ACTIVE'] == True:
                                            _system['ACTIVE'] = False
                                            if _bridge[0:1] == '#':
                                                clear_reflector_link_owner(_system)
                                            logger.info('(%s) [5] Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                            # Cancel the timer if we've enabled an "ON" type timeout
                                            if _system['TO_TYPE'] == 'ON':
                                                _system['TIMER'] = pkt_time
                                                logger.info('(%s) [6] Bridge: %s set to ON with an "OFF" timer rule: timeout timer cancelled', self._system, _bridge)
                                    # Reset the timer for the rule
                                    if _system['ACTIVE'] == False and _system['TO_TYPE'] == 'OFF':
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [7] Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)
                                    # Cancel the timer if we've enabled an "ON" type timeout
                                    if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON' and _dst_id in _system['OFF']:
                                        _system['TIMER'] = pkt_time
                                        logger.info('(%s) [8] Bridge: %s set to ON with and "OFF" timer rule: timeout timer cancelled', self._system, _bridge)
                            else:
                                
                                if (_dst_id in _system['OFF']  or _dst_id in _system['RESET']) and _slot == _system['TS']:
                                    # Set the matching rule as ACTIVE
                                    if _dst_id in _system['OFF']:
                                        if _system['ACTIVE'] == True:
                                            _system['ACTIVE'] = False
                                            logger.info('(%s) [9] Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                            # Cancel the timer if we've enabled an "ON" type timeout
                                        if _system['TO_TYPE'] == 'ON':
                                            _system['TIMER'] = pkt_time
                                            logger.info('(%s) [10] Bridge: %s set to ON with and "OFF" timer rule: timeout timer cancelled', self._system, _bridge)
                                    # Reset the timer for the rule
                                    if _system['ACTIVE'] == False and _system['TO_TYPE'] == 'OFF':
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [11] Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)
                                    # Cancel the timer if we've enabled an "ON" type timeout
                                    if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON' and _dst_id in _system['OFF']:
                                        _system['TIMER'] = pkt_time
                                        logger.info('(%s) [12] Bridge: %s set to ON with and "OFF" timer rule: timeout timer cancelled', self._system, _bridge)

            #
            # END IN-BAND SIGNALLING
            #


            # Mark status variables for use later
            self.STATUS[_slot]['RX_PEER']      = _peer_id
            self.STATUS[_slot]['RX_SEQ']       = _seq
            self.STATUS[_slot]['RX_RFS']       = _rf_src
            self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
            self.STATUS[_slot]['RX_TGID']      = _dst_id
            self.STATUS[_slot]['RX_TIME']      = pkt_time
            self._assign_rx_stream_id(_slot, _stream_id)
            
            self.STATUS[_slot]['crcs'].add(_pkt_crc)

#
# Motorola IPSC master (MODE: IPSC) — Copyright (C) 2026 Shane Daley, M0VUB
# See ipsc_master.py, ipsc_voice.py
#
class routerIPSC(IpscMasterMixin, routerHBP):

    def __init__(self, _name, _config, _report):
        if 'PEERS' not in _config['SYSTEMS'][_name]:
            _config['SYSTEMS'][_name]['PEERS'] = {}
        routerHBP.__init__(self, _name, _config, _report)
        self._peers = _config['SYSTEMS'][_name]['PEERS']
        self.init_ipsc()

    def _remove_ipsc_peer(self, peer_id):
        """Clear dial-a-tg state when a repeater drops off (parity with HBP RPTCL)."""
        clear_default_reflectors(self._system)
        reset_dynamic_reflectors(self._system)
        sanitize_dial_reflectors(self._system)
        clear_sub_map_for_peer(peer_id)
        last_peer = len(self._ipsc_peers) <= 1 and peer_id in self._ipsc_peers
        IpscMasterMixin._remove_ipsc_peer(self, peer_id)
        if last_peer and 'OPTIONS' in self._CONFIG['SYSTEMS'][self._system]:
            _sys = self._CONFIG['SYSTEMS'][self._system]
            if '_default_options' in _sys:
                _sys['OPTIONS'] = _sys['_default_options']
                logger.info('(%s) IPSC peer gone — restoring default OPTIONS', self._system)
                _sys['_reset'] = True
            else:
                del _sys['OPTIONS']
                logger.info('(%s) IPSC peer gone — clearing OPTIONS', self._system)
                _sys['_reset'] = True


#
# Socket-based reporting section
#
class bridgeReportFactory(reportFactory):

    @staticmethod
    def _clean_trigger_list(value):
        if value is None:
            return []
        if isinstance(value, (list, tuple, deque)):
            return list(value)
        return [value]

    @classmethod
    def _safe_bridges_payload(cls):
        safe_bridges = {}
        for bridge, systems in BRIDGES.items():
            if not is_valid_talkgroup_bridge(bridge):
                continue
            if not isinstance(systems, (list, tuple, deque)):
                logger.warning('(REPORT) Skipping malformed bridge %s payload type: %s', bridge, type(systems))
                continue
            safe_systems = []
            for bridge_system in systems:
                if not isinstance(bridge_system, dict):
                    logger.warning('(REPORT) Skipping malformed bridge entry in %s payload type: %s', bridge, type(bridge_system))
                    continue
                leg = build_report_bridge_leg(bridge_system)
                if leg is None:
                    if not report_include_bridge_leg(
                            bridge_system.get('TO_TYPE', 'NONE'),
                            bool(bridge_system.get('ACTIVE', False))):
                        continue
                    logger.warning('(REPORT) Skipping incomplete bridge entry in %s: %s', bridge, bridge_system)
                    continue
                safe_systems.append(leg)
            safe_bridges[str(bridge)] = safe_systems
        return safe_bridges

    def send_bridge(self):
        serialized = pickle.dumps(self._safe_bridges_payload(), protocol=2) #.decode("utf-8", errors='ignore')
        self.send_clients(b''.join([REPORT_OPCODES['BRIDGE_SND'],serialized]))

    def send_bridgeEvent(self, _data):
        if isinstance(_data, str):
            _data = _data.decode('utf-8', error='ignore')
        self.send_clients(b''.join([REPORT_OPCODES['BRDG_EVENT'],_data]))


#************************************************
#      MAIN PROGRAM LOOP STARTS HERE
#************************************************

if __name__ == '__main__':

    import argparse
    import sys
    import os
    import signal
    
    # Higheset peer ID permitted by HBP
    PEER_MAX = 4294967295
    
    ID_MAX = 16776415

    #Set process title early
    setproctitle(__file__)
    
    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually hblink.cfg)')
    parser.add_argument('-r', '--rules', action='store', dest='RULES_FILE', help='/full/path/to/rules.file (usually rules.py)')
    parser.add_argument('-l', '--logging', action='store', dest='LOG_LEVEL', help='Override config file logging level.')
    cli_args = parser.parse_args()

    # Ensure we have a path for the config file, if one wasn't specified, then use the default (top of file)
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/hblink.cfg'


    #configP = False
    #if os.path.isfile('config.pkl'):
        #if os.path.getmtime('config.pkl') > (time() - 25):
            #try:
                #with open('config.pkl','rb') as _fh:
                    #CONFIG = pickle.load(_fh)
                    #print('(CONFIG) loaded config .pkl from previous shutdown')
                    #configP = True
            #except:
                #print('(CONFIG) Cannot load config.pkl file')
                #CONFIG = config.build_config(cli_args.CONFIG_FILE)
        #else:
            #os.unlink("config.pkl")
    #else:
    
    CONFIG = config.build_config(cli_args.CONFIG_FILE)

    _ipsc_enabled = any(
        CONFIG['SYSTEMS'][s].get('ENABLED') and CONFIG['SYSTEMS'][s].get('MODE') == 'IPSC'
        for s in CONFIG['SYSTEMS']
    )

    # Ensure we have a path for the rules file, if one wasn't specified, then use the default (top of file)
    if not cli_args.RULES_FILE:
        cli_args.RULES_FILE = os.path.dirname(os.path.abspath(__file__))+'/rules.py'

    # Start the system logger
    if cli_args.LOG_LEVEL:
        CONFIG['LOGGER']['LOG_LEVEL'] = cli_args.LOG_LEVEL
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('\n\nCopyright (c) 2020, 2021, 2022 Simon G7RZU simon@gb7fr.org.uk')
    logger.info('Copyright (c) 2013, 2014, 2015, 2016, 2018, 2019\n\tThe Regents of the K0USY Group. All rights reserved.\n')
    if _ipsc_enabled:
        logger.info('(IPSC) Copyright (c) 2026 Shane Daley, M0VUB <shane@freestar.network>')
    logger.debug('(GLOBAL) Logging system started, anything from here on gets logged')

        
    if CONFIG['ALLSTAR']['ENABLED']:
        logger.info('(AMI) Setting up AMI: Server: %s, Port: %s, User: %s, Pass: %s, Node: %s',CONFIG['ALLSTAR']['SERVER'],CONFIG['ALLSTAR']['PORT'],CONFIG['ALLSTAR']['USER'],CONFIG['ALLSTAR']['PASS'],CONFIG['ALLSTAR']['NODE'])
        
        AMIOBJ = AMI(CONFIG['ALLSTAR']['SERVER'],CONFIG['ALLSTAR']['PORT'],CONFIG['ALLSTAR']['USER'],CONFIG['ALLSTAR']['PASS'],CONFIG['ALLSTAR']['NODE'])
            

    # Set up the signal handler
    def sig_handler(_signal, _frame):
        logger.info('(GLOBAL) SHUTDOWN: CONFBRIDGE IS TERMINATING WITH SIGNAL %s', str(_signal))
        hblink_handler(_signal, _frame)
        logger.info('(GLOBAL) SHUTDOWN: ALL SYSTEM HANDLERS EXECUTED - STOPPING REACTOR')
        reactor.stop()
        if CONFIG['ALIASES']['SUB_MAP_FILE']:
            subMapWrite()

    # Set signal handers so that we can gracefully exit if need be
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, sig_handler)

    # Create the name-number mapping dictionaries
    peer_ids, subscriber_ids, talkgroup_ids, local_subscriber_ids, server_ids = mk_aliases(CONFIG)
    
    #Add special IDs to DB
    subscriber_ids[900999] = 'D-APRS'
    subscriber_ids[4294967295] = 'SC'
    
    CONFIG['_SUB_IDS'] = subscriber_ids
    CONFIG['_PEER_IDS'] = peer_ids
    CONFIG['_LOCAL_SUBSCRIBER_IDS'] = local_subscriber_ids
    CONFIG['_SERVER_IDS'] = server_ids
    
    
    
    # Import the ruiles file as a module, and create BRIDGES from it
    spec = importlib.util.spec_from_file_location("module.name", cli_args.RULES_FILE)
    rules_module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(rules_module)
        logger.info('(ROUTER) Routing bridges file found and bridges imported: %s', cli_args.RULES_FILE)
    except (ImportError, FileNotFoundError):
        sys.exit('(ROUTER) TERMINATING: Routing bridges file not found or invalid: {}'.format(cli_args.RULES_FILE))

    #Load pickle of bridges if it's less than 25 seconds old 
    #if os.path.isfile('bridge.pkl'):
        #if os.path.getmtime('config.pkl') > (time() - 25):
            #try:
                #with open('bridge.pkl','rb') as _fh:
                    #BRIDGES = pickle.load(_fh)
                    #logger.info('(BRIDGE) loaded bridge.pkl from previous shutdown')
            #except:
                #logger.warning('(BRIDGE) Cannot load bridge.pkl file')
                #BRIDGES = make_bridges(rules_module.BRIDGES)
        #else:
            #BRIDGES = make_bridges(rules_module.BRIDGES)
        #os.unlink("bridge.pkl")
    #else:
    
    BRIDGES = make_bridges(rules_module.BRIDGES)
    # Build initial routing index from the just-created BRIDGES dict
    rebuild_bridge_index()
    logger.info('(ROUTER) Initial BRIDGE_IDX built: %d keys across %d bridges',
                len(BRIDGE_IDX), len(BRIDGES))
    
    #Subscriber map for unit calls - complete with test entry
    #SUB_MAP = {bytes_3(73578):('REP-1',1,time())}
    SUB_MAP = {}
    
    
    if CONFIG['ALIASES']['SUB_MAP_FILE']:
        try:
            with open(CONFIG['ALIASES']['PATH'] + CONFIG['ALIASES']['SUB_MAP_FILE'],'rb') as _fh:
                SUB_MAP = pickle.load(_fh)
            
            # BACKWARDS COMPATIBILITY: Handle old SUB_MAP formats
            # Old 3-element format: SUB_MAP[subscriber] = (system, ts, timestamp)
            # Old 4-element format: SUB_MAP[subscriber] = (system, ts, tg, timestamp)
            # New 5-element format: SUB_MAP[subscriber] = (system, ts, tg, timestamp, peer_id)
            # Convert old formats to new format by adding None for missing fields
            _converted_count = 0
            for _subscriber in list(SUB_MAP.keys()):
                try:
                    if len(SUB_MAP[_subscriber]) == 3:  # Old 3-element format
                        _system, _ts, _timestamp = SUB_MAP[_subscriber]
                        SUB_MAP[_subscriber] = (_system, _ts, None, _timestamp, None)
                        _converted_count += 1
                    elif len(SUB_MAP[_subscriber]) == 4:  # Old 4-element format
                        _system, _ts, _tg, _timestamp = SUB_MAP[_subscriber]
                        SUB_MAP[_subscriber] = (_system, _ts, _tg, _timestamp, None)
                        _converted_count += 1
                except (TypeError, ValueError) as e:
                    logger.warning('(SUBSCRIBER) Invalid SUB_MAP entry for subscriber %s, removing: %s', int_id(_subscriber), e)
                    SUB_MAP.pop(_subscriber, None)
            
            if _converted_count > 0:
                logger.info('(SUBSCRIBER) Converted %s SUB_MAP entries to new 5-element format', _converted_count)
            logger.info('(SUBSCRIBER) Loaded SUB_MAP with %s entries', len(SUB_MAP))
        except Exception as e:
            logger.warning('(SUBSCRIBER) Cannot load SUB_MAP file: %s', e)
            #sys.exit('(SUBSCRIBER) TERMINATING: SUB_MAP file not found or invalid')
        
        #Test value
        #SUB_MAP[bytes_3(73578)] = ('REP-1',1,None,time())
    
    
    #Generator
    generator = {}
    systemdelete = deque()
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if (is_routing_master(CONFIG['SYSTEMS'][system]['MODE'])
                    and (CONFIG['SYSTEMS'][system]['GENERATOR'] > 1)):
                for count in range(CONFIG['SYSTEMS'][system]['GENERATOR']):
                    _systemname = ''.join([system,'-',str(count)])
                    generator[_systemname] = copy.deepcopy(CONFIG['SYSTEMS'][system])
                    generator[_systemname]['PORT'] = generator[_systemname]['PORT'] + count
                    generator[_systemname]['_default_options'] = "TS1_STATIC={};TS2_STATIC={};SINGLE={};DEFAULT_UA_TIMER={};DEFAULT_REFLECTOR={};VOICE={};LANG={}".format(generator[_systemname]['TS1_STATIC'],generator[_systemname]['TS2_STATIC'],int(generator[_systemname]['SINGLE_MODE']),generator[_systemname]['DEFAULT_UA_TIMER'],generator[_systemname]['DEFAULT_REFLECTOR'],int(generator[_systemname]['VOICE_IDENT']), generator[_systemname]['ANNOUNCEMENT_LANGUAGE'])
                    logger.debug('(GLOBAL) Generator - generated system %s',_systemname)
                    generator[_systemname]['_default_options']
                systemdelete.append(system)
    
    for _system in generator:
        CONFIG['SYSTEMS'][_system] = generator[_system]
    for _system in systemdelete:
            CONFIG['SYSTEMS'].pop(_system)
    
    del generator
    del systemdelete

    augment_bridges_for_masters()
    
    # Default reflector
    logger.debug('(ROUTER) Setting default reflectors')
    for system in CONFIG['SYSTEMS']:
        if not is_routing_master(CONFIG['SYSTEMS'][system]['MODE']):
            continue
        if CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'] > 0 and not is_invalid_dial_reflector(CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR']):
            make_default_reflector(CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'],CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER'],system)
            
    #static TGs 
    logger.debug('(ROUTER) setting static TGs')
    for system in CONFIG['SYSTEMS']:
        if not is_routing_master(CONFIG['SYSTEMS'][system]['MODE']):
            continue
        reapply_static_tgs_for_system(system)

    purge_invalid_bridges()

    # INITIALIZE THE REPORTING LOOP
    if CONFIG['REPORTS']['REPORT']:
        report_server = config_reports(CONFIG, bridgeReportFactory)
    else:
        report_server = None
        logger.info('(REPORT) TCP Socket reporting not configured')
        
    #Read AMBE
    AMBEobj = readAMBE(CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGES'],'./Audio/')
    
    #global words
    words = AMBEobj.readfiles()
    
    for lang in words.keys():
        logger.info('(AMBE) for language %s, read %s words into voice dict',lang,len(words[lang]) - 1)

        #Remap words for internationalisation
        if lang in voiceMap:
            logger.info('(AMBE) i8n voice map entry for language %s',lang)
            _map = voiceMap[lang]
            for _mapword in _map:
                logger.info('(AMBE) Mapping \"%s\" to \"%s\"',_mapword,_map[_mapword])
                words[lang][_mapword] = words[lang][_map[_mapword]]

    # HBlink instance creation
    logger.info('(GLOBAL) RYSEN \'bridge_master.py\' -- SYSTEM STARTING...')

    
    listeningPorts = {}

    
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
                systems[system] = routerOBP(system, CONFIG, report_server)
            elif CONFIG['SYSTEMS'][system]['MODE'] == 'IPSC':
                systems[system] = routerIPSC(system, CONFIG, report_server)
            else:
                if (is_routing_master(CONFIG['SYSTEMS'][system]['MODE'])
                        and CONFIG['SYSTEMS'][system]['ANNOUNCEMENT_LANGUAGE']
                        not in CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGES'].split(',')):
                    logger.warning('(GLOBAL) Invalid language in ANNOUNCEMENT_LANGUAGE, skipping system %s',system)
                    continue
                systems[system] = routerHBP(system, CONFIG, report_server)
            listeningPorts[system] = reactor.listenUDP(CONFIG['SYSTEMS'][system]['PORT'], systems[system], interface=CONFIG['SYSTEMS'][system]['IP'])
            logger.debug('(GLOBAL) %s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])

    refresh_obp_system_list()
    logger.info('(ROUTER) LoopControl fast path: %d OBP systems cached', len(_OBP_SYSTEMS))

    def loopingErrHandle(failure):
        logger.error('(GLOBAL) STOPPING REACTOR TO AVOID MEMORY LEAK: Unhandled error in timed loop.\n %s', failure)
        reactor.stop()

    # Initialize the rule timer -- this if for user activated stuff
    rule_timer_task = task.LoopingCall(rule_timer_loop)
    rule_timer = rule_timer_task.start(52)
    rule_timer.addErrback(loopingErrHandle)

    # Initialize the stream trimmer
    stream_trimmer_task = task.LoopingCall(stream_trimmer_loop)
    stream_trimmer = stream_trimmer_task.start(5)
    stream_trimmer.addErrback(loopingErrHandle)
   
    # Ident
    #This runs in a thread so as not to block the reactor
    ident_task = task.LoopingCall(threadIdent)
    identa = ident_task.start(914)
    identa.addErrback(loopingErrHandle)
    
    #Alias reloader
    alias_time = CONFIG['ALIASES']['STALE_TIME'] * 86400
    aliasa_task = task.LoopingCall(threadAlias)
    aliasa = aliasa_task.start(alias_time)
    aliasa.addErrback(loopingErrHandle)
    
    #Options parsing
    options_task = task.LoopingCall(options_config)
    options = options_task.start(26)
    options.addErrback(loopingErrHandle)

    # IPSC selfcare — poll Clients (mode=0) and apply static TG options on master
    if CONFIG.get('SELF SERVICE', {}).get('ENABLED'):
        ss = CONFIG['SELF SERVICE']
        _selfcare_db = SelfcareDB(
            ss['DB_HOST'], ss['DB_USER'], ss['DB_PASS'], ss['DB_NAME'], ss['DB_PORT'])
        CONFIG['_SELF_SERVICE_DB'] = _selfcare_db
        _selfcare_db.test_db(reactor, logger)
        ipsc_sc_task = task.LoopingCall(ipsc_selfcare_poll)
        ipsc_sc = ipsc_sc_task.start(ss.get('POLL_INTERVAL', 5))
        ipsc_sc.addErrback(loopingErrHandle)
        logger.info('(SELF SERVICE) IPSC selfcare enabled (poll every %ss)', ss.get('POLL_INTERVAL', 5))
        hs_disc_task = task.LoopingCall(hotspot_selfcare_disc_poll)
        hs_disc = hs_disc_task.start(ss.get('DISC_POLL_INTERVAL', 2))
        hs_disc.addErrback(loopingErrHandle)
        logger.info('(SELF SERVICE) Hotspot DISC=1 poll every %ss',
                    ss.get('DISC_POLL_INTERVAL', 2))
        hs_static_task = task.LoopingCall(hotspot_selfcare_static_reconcile)
        hs_static = hs_static_task.start(ss.get('POLL_INTERVAL', 5))
        hs_static.addErrback(loopingErrHandle)
        logger.info('(SELF SERVICE) Hotspot static reconcile poll every %ss',
                    ss.get('POLL_INTERVAL', 5))
        
    # STAT trimmer — idle GEN_STAT bridges (comment historically said 10 min; was 3600)
    if CONFIG['GLOBAL']['GEN_STAT_BRIDGES']:
        stat_trimmer_task = task.LoopingCall(statTrimmer)
        stat_trimmer = stat_trimmer_task.start(STAT_TRIMMER_INTERVAL_S)
        stat_trimmer.addErrback(loopingErrHandle)
        logger.info('(ROUTER) STAT trimmer every %ss', STAT_TRIMMER_INTERVAL_S)
        
    #KA Reporting
    ka_task = task.LoopingCall(kaReporting)
    ka = ka_task.start(60)
    ka.addErrback(loopingErrHandle)
    
    #Subscriber map trimmer
    sub_trimmer_task = task.LoopingCall(SubMapTrimmer)
    sub_trimmer = sub_trimmer_task.start(3600)#3600
    sub_trimmer.addErrback(loopingErrHandle)

    # Reactor lag / event-loop health diagnostics
    _REACTOR_LAG_LAST[0] = time()  # seed with current time so first check is meaningful
    _ROUTE_STATS_NEXT_LOG[0] = time() + _ROUTE_STATS_INTERVAL
    reactor_lag_task = task.LoopingCall(reactorLagCheck)
    reactor_lag = reactor_lag_task.start(_REACTOR_LAG_INTERVAL)
    reactor_lag.addErrback(loopingErrHandle)

    #more threads
    reactor.suggestThreadPoolSize(100)
    
    reactor.run()
