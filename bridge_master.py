#!/usr/bin/env python
#
###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>  
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
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
This application, in conjuction with it's rule file (rules.py) will
work like a "conference bridge". This is similar to what most hams think of as a
reflector. You define conference bridges and any system joined to that conference
bridge will both receive traffic from, and send traffic to any other system
joined to the same conference bridge. It does not provide end-to-end connectivity
as each end system must individually be joined to a conference bridge (a name
you create in the configuraiton file) to pass traffic.

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

# Things we import from the main hblink module
from hblink import HBSYSTEM, OPENBRIDGE, systems, hblink_handler, reportFactory, REPORT_OPCODES, mk_aliases, acl_check
from dmr_utils3.utils import bytes_3, int_id, get_alias, bytes_4
from dmr_utils3 import decode, bptc, const
import config
from config import acl_build
import log
from const import *
from mk_voice import pkt_gen
#from voice_lib import words

#Read voices
from read_ambe import readAMBE
#Remap some words for certain languages
from i8n_voice_map import voiceMap

# Stuff for socket reporting
import pickle
# REMOVE LATER from datetime import datetime
# The module needs logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)

#REGEX
import re

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
            if CONFIG['SYSTEMS'][_confsystem]['MODE'] != 'MASTER':
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

#Make a single bridge - used for on-the-fly UA bridges
def make_single_bridge(_tgid,_sourcesystem,_slot,_tmout):
    _tgid_s = str(int_id(_tgid))
    #Always a 1 min timeout for Echo
    if _tgid_s == '9990':
        _tmout = 1
    BRIDGES[_tgid_s] = []
    for _system in CONFIG['SYSTEMS']:
        if _system[0:3] != 'OBP':
        #if CONFIG['SYSTEMS'][system]['MODE'] == 'MASTER':
            #_tmout = CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER']
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
                
        if _system[0:3] == 'OBP' and (int_id(_tgid) >= 79 and (int_id(_tgid) < 9990 or int_id(_tgid) > 9999)):
            BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': '','TO_TYPE': 'NONE','OFF': [],'ON': [],'RESET': [], 'TIMER': time()})
        
#Make static bridge - used for on-the-fly relay bridges
def make_stat_bridge(_tgid):
    _tgid_s = str(int_id(_tgid))
    BRIDGES[_tgid_s] = []
    for _system in CONFIG['SYSTEMS']:
        if _system[0:3] != 'OBP':
            if CONFIG['SYSTEMS'][_system]['MODE'] == 'MASTER':
                _tmout = CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER']
                BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
                BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 2, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
                    
        if _system[0:3] == 'OBP':
            BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': '','TO_TYPE': 'STAT','OFF': [],'ON': [],'RESET': [], 'TIMER': time()})
        

def make_default_reflector(reflector,_tmout,system):
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
        
def make_static_tg(tg,ts,_tmout,system):
    #_tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    if str(tg) not in BRIDGES:
        make_single_bridge(bytes_3(tg),system,ts,_tmout)
    bridgetemp = deque()
    for bridgesystem in BRIDGES[str(tg)]:
        if bridgesystem['SYSTEM'] == system and bridgesystem['TS'] == ts:
            bridgetemp.append({'SYSTEM': system, 'TS': ts, 'TGID': bytes_3(tg),'ACTIVE': True,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'OFF','OFF': [],'ON': [bytes_3(tg),],'RESET': [], 'TIMER': time() + (_tmout * 60)})
        else:
            bridgetemp.append(bridgesystem)
        
    BRIDGES[str(tg)] = bridgetemp
    
def reset_static_tg(tg,ts,_tmout,system):
    #_tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    bridgetemp = deque()
    try:
        for bridgesystem in BRIDGES[str(tg)]:
            if bridgesystem['SYSTEM'] == system and bridgesystem['TS'] == ts:
                bridgetemp.append({'SYSTEM': system, 'TS': ts, 'TGID': bytes_3(tg),'ACTIVE': False,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(tg),],'RESET': [], 'TIMER': time() + (_tmout * 60)})
            else:
                bridgetemp.append(bridgesystem)
            
        BRIDGES[str(tg)] = bridgetemp
    except KeyError:
        logger.exception('(ERROR) KeyError in reset_static_tg() - bridge gone away?')
        return
        
def reset_default_reflector(reflector,_tmout,system):
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
            
def make_single_reflector(_tgid,_tmout,_sourcesystem):
    _tgid_s = str(int_id(_tgid))
    _bridge = ''.join(['#',_tgid_s])
    #1 min timeout for echo
    if _tgid_s == '9990':
        _tmout = 1
    BRIDGES[_bridge] = []
    for _system in CONFIG['SYSTEMS']:
        #if _system[0:3] != 'OBP':
        if CONFIG['SYSTEMS'][_system]['MODE'] == 'MASTER':
            #_tmout = CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER']
            if _system == _sourcesystem:
                BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': True,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time() + (_tmout * 60)})
            else:
                BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT':  CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER'] * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
        if _system[0:3] == 'OBP' and (int_id(_tgid) >= 79 and (int_id(_tgid) < 9990 or int_id(_tgid) > 9999)):
            BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': '','TO_TYPE': 'NONE','OFF': [],'ON': [],'RESET': [], 'TIMER': time()})
        
def remove_bridge_system(system):
    _bridgestemp = {}
    _bridgetemp = {}
    for _bridge in BRIDGES:
        for _bridgesystem in BRIDGES[_bridge]:
            if _bridgesystem['SYSTEM'] != system:
                if _bridge not in _bridgestemp:
                    _bridgestemp[_bridge] = []
                _bridgestemp[_bridge].append(_bridgesystem)
    BRIDGES.update(_bridgestemp)
                

# Run this every minute for rule timer updates
def rule_timer_loop():
    logger.debug('(ROUTER) routerHBP Rule timer loop started')
    _now = time()
    _remove_bridges = deque()
    for _bridge in BRIDGES:
        _bridge_used = False
        for _system in BRIDGES[_bridge]:
            if _system['TO_TYPE'] == 'ON':
                if _system['ACTIVE'] == True:
                    _bridge_used = True
                    if _system['TIMER'] < _now:
                        _system['ACTIVE'] = False
                        logger.info('(ROUTER) Conference Bridge TIMEOUT: DEACTIVATE System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                        if _bridge[0:1] == '#':
                            reactor.callInThread(disconnectedVoice,_system['SYSTEM'])
                    else:
                        timeout_in = _system['TIMER'] - _now
                        _bridge_used = True
                        logger.info('(ROUTER) Conference Bridge ACTIVE (ON timer running): System: %s Bridge: %s, TS: %s, TGID: %s, Timeout in: %.2fs,', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']),  timeout_in)
                elif _system['ACTIVE'] == False:
                    logger.trace('(ROUTER) Conference Bridge INACTIVE (no change): System: %s Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
            elif _system['TO_TYPE'] == 'OFF':
                if _system['ACTIVE'] == False:
                    if _system['TIMER'] < _now:
                        _system['ACTIVE'] = True
                        _bridge_used = True 
                        logger.info('(ROUTER) Conference Bridge TIMEOUT: ACTIVATE System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                    else:
                        timeout_in = _system['TIMER'] - _now
                        _bridge_used = True
                        logger.info('(ROUTER) Conference Bridge INACTIVE (OFF timer running): System: %s Bridge: %s, TS: %s, TGID: %s, Timeout in: %.2fs,', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']),  timeout_in)
                elif _system['ACTIVE'] == True:
                    _bridge_used = True
                    logger.trace('(ROUTER) Conference Bridge ACTIVE (no change): System: %s Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
            else:
                if _system['SYSTEM'][0:3] != 'OBP':
                    _bridge_used = True
                elif _system['SYSTEM'][0:3] == 'OBP' and _system['TO_TYPE'] == 'STAT':
                    _bridge_used = True
                logger.trace('(ROUTER) Conference Bridge NO ACTION: System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                
        if _bridge_used == False:
            _remove_bridges.append(_bridge)
                
    for _bridgerem in _remove_bridges:
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
            elif _system['TO_TYPE'] == 'OFF' and not _system['ACTIVE']:
                _in_use = True
        if _bridge_stat and not _in_use:
            _remove_bridges.append(_bridge)
    for _bridgerem in _remove_bridges:
        del BRIDGES[_bridgerem]
        logger.debug('(ROUTER) STAT bridge %s removed',_bridgerem)
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
        if SUB_MAP[_subscriber][2] < (_sub_time - 86400):
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
                    if CONFIG['REPORTS']['REPORT']:
                        systems[system]._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(_slot['RX_STREAM_ID']), int_id(_slot['RX_PEER']), int_id(_slot['RX_RFS']), slot, int_id(_slot['RX_TGID']), _slot['RX_TIME'] - _slot['RX_START']).encode(encoding='utf-8', errors='ignore'))
                #Null stream_id - for loop control 
                if _slot['RX_TIME'] < _now - 60:
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
                            
                        if CONFIG['REPORTS']['REPORT']:
                                systems[system]._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(stream_id), int_id(_stream['RX_PEER']), int_id(_stream['RFS']), 1, int_id(_stream['TGID']), _stream['LAST'] - _stream['START']).encode(encoding='utf-8', errors='ignore'))
                        systems[system].STATUS[stream_id]['_to'] = True
                        continue
                except Exception as e:
                    logger.exception("(%s) Keyerror - stream trimmer Stream ID: %s",system,stream_id, exc_info=e)
                    systems[system].STATUS[stream_id]['LAST'] = _now
                    continue

                    
                try:
                    if systems[system].STATUS[stream_id]['LAST'] < _now - 180:
                        remove_list.append(stream_id)
                except Exception as e:
                    logger.exception("(%s) Keyerror - stream trimmer Stream ID: %s",system,stream_id, exc_info=e)
                    systems[system].STATUS[stream_id]['LAST'] = _now
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

def sendVoicePacket(self,pkt,_source_id,_dest_id,_slot):
    _stream_id = pkt[16:20]
    _pkt_time = time()
    if _stream_id not in systems[system].STATUS:
        systems[system].STATUS[_stream_id] = {
        'START':     _pkt_time,
        'CONTENTION':False,
        'RFS':       _source_id,
        'TGID':      _dest_id,
        'LAST':      _pkt_time
        }
        _slot['TX_TGID'] = _dest_id
    else:
        systems[system].STATUS[_stream_id]['LAST'] = _pkt_time
        _slot['TX_TIME'] = _pkt_time
                                            
    self.send_system(pkt)
    
def sendSpeech(self,speech):
    logger.debug('(%s) Inside sendspeech thread',self._system)
    sleep(1)
    _nine = bytes_3(9)
    _source_id = bytes_3(5000)
    _slot  = systems[system].STATUS[2]
    while True:
        try:
            pkt = next(speech)
        except StopIteration:
            break
        #Packet every 60ms
        sleep(0.058)
        reactor.callFromThread(sendVoicePacket,self,pkt,_source_id,_nine,_slot)

    logger.debug('(%s) Sendspeech thread ended',self._system)

def disconnectedVoice(system):
    _nine = bytes_3(9)
    _source_id = bytes_3(5000)
    _lang = CONFIG['SYSTEMS'][system]['ANNOUNCEMENT_LANGUAGE']
    logger.debug('(%s) Sending disconnected voice',system)
    _say = [words[_lang]['silence']]
    _say.append(words[_lang]['silence']) 
    if CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'] > 0:
        _say.append(words[_lang]['silence'])
        _say.append(words[_lang]['linkedto'])
        _say.append(words[_lang]['silence'])
        _say.append(words[_lang]['to'])
        _say.append(words[_lang]['silence'])
        _say.append(words[_lang]['silence']) 
        
        for number in str(CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR']):
            _say.append(words[_lang][number])
            _say.append(words[_lang]['silence'])
    else:
        _say.append(words[_lang]['notlinked'])
    
    _say.append(words[_lang]['silence']) 
    
    speech = pkt_gen(_source_id, _nine, bytes_4(9), 1, _say)

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
        reactor.callFromThread(sendVoicePacket,systems[system],pkt,_source_id,_nine,_slot)
        logger.debug('(%s) disconnected voice thread end',system)

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
    while True:
        try:
            pkt = next(speech)
        except StopIteration:
                break
        #Packet every 60ms
        sleep(0.058)
        _stream_id = pkt[16:20]
        _pkt_time = time()
        reactor.callFromThread(sendVoicePacket,self,pkt,_source_id,_nine,_slot)
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
                
                _say.append(words[_lang]['freedmr'])
                
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
            logger.debug('(OPTIONS) Bridge reset for %s - no peers',_system)
            remove_bridge_system(_system)
            CONFIG['SYSTEMS'][_system]['_reset'] = False
        try:
            if CONFIG['SYSTEMS'][_system]['MODE'] != 'MASTER':
                continue
            if CONFIG['SYSTEMS'][_system]['ENABLED'] == True:
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
                        _options[k] = v
                    logger.debug('(OPTIONS) Options found for %s',_system)
                    
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
                        
                    if 'DEFAULT_REFLECTOR' not in _options:
                        _options['DEFAULT_REFLECTOR'] = 0
                    
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
                        re.sub("\s","",_options['TS1_STATIC'])
                        if re.search("![\d\,]",_options['TS1_STATIC']):
                            logger.debug('(OPTIONS) %s - TS1_STATIC contains characters other than numbers and comma, ignoring',_system)
                            continue
                    
                    if _options['TS2_STATIC']:
                        re.sub("\s","",_options['TS2_STATIC'])
                        if re.search("![\d\,]",_options['TS2_STATIC']):
                            logger.debug('(OPTIONS) %s - TS2_STATIC contains characters other than numbers and comma, ignoring',_system)
                            continue
                    
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
                        remove_bridge_system(_system)
                        for _bridge in BRIDGES:
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
            
                    if int(_options['DEFAULT_REFLECTOR']) != CONFIG['SYSTEMS'][_system]['DEFAULT_REFLECTOR']:
                        if int(_options['DEFAULT_REFLECTOR']) > 0:
                            logger.debug('(OPTIONS) %s default reflector changed, updating',_system) 
                            reset_default_reflector(CONFIG['SYSTEMS'][_system]['DEFAULT_REFLECTOR'],_tmout,_system)
                            make_default_reflector(int(_options['DEFAULT_REFLECTOR']),_tmout,_system)
                        else:
                            logger.debug('(OPTIONS) %s default reflector disabled, updating',_system) 
                            reset_default_reflector(int(_options['DEFAULT_REFLECTOR']),_tmout,_system)
                    
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
        except Exception as e:
            logger.exception('(OPTIONS) caught exception: %s',e)
            continue


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
                    if _noOBP == True:
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

                        }
                        # Generate LCs (full and EMB) for the TX stream
                        try:
                            dst_lc = b''.join([self.STATUS[_stream_id]['LC'][0:3], _target['TGID'], _rf_src])
                        except Exception:
                            logger.exception('(to_target) caught exception')
                            _target_status[_stream_id]['LAST'] = pkt_time
                            return
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
                    dmrbits = bitarray(endian='big')
                    dmrbits.frombytes(dmrpkt)
                    # Create a voice header packet (FULL LC)
                    if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                        dmrbits = _target_status[_stream_id]['H_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['H_LC'][98:197]
                    # Create a voice terminator packet (FULL LC)
                    elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                        dmrbits = _target_status[_stream_id]['T_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['T_LC'][98:197]
                        if CONFIG['REPORTS']['REPORT']:
                            call_duration = pkt_time - _target_status[_stream_id]['START']
                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                    # Create a Burst B-E packet (Embedded LC)
                    elif _dtype_vseq in [1,2,3,4]:
                        dmrbits = dmrbits[0:116] + _target_status[_stream_id]['EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                    dmrpkt = dmrbits.tobytes()
                    _tmp_data = b''.join([_tmp_data, dmrpkt])

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
                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed to TGID %s, target active or in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                        continue
                    if ((_target['TGID'] != _target_status[_target['TS']]['TX_TGID']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < _target_system['GROUP_HANGTIME'])):
                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed to TGID%s, target in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']))
                        continue
                    if (_target['TGID'] == _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < STREAM_TO):
                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed to TGID%s, matching call already active on target: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                        continue
                    if (_target['TGID'] == _target_status[_target['TS']]['TX_TGID']) and (_rf_src != _target_status[_target['TS']]['TX_RFS']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < STREAM_TO):
                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed for subscriber %s, call route in progress on target: HBSystem: %s, TS: %s, TGID: %s, SUB: %s', self._system, int_id(_rf_src), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']), int_id(_target_status[_target['TS']]['TX_RFS']))
                        continue

                    # Is this a new call stream?
                    if (_target_status[_target['TS']]['TX_STREAM_ID'] != _stream_id):
                        # Record the DST TGID and Stream ID
                        _target_status[_target['TS']]['TX_START'] = pkt_time
                        _target_status[_target['TS']]['TX_TGID'] = _target['TGID']
                        _target_status[_target['TS']]['TX_STREAM_ID'] = _stream_id
                        _target_status[_target['TS']]['TX_RFS'] = _rf_src
                        _target_status[_target['TS']]['TX_PEER'] = _peer_id
                        # Generate LCs (full and EMB) for the TX stream
                        dst_lc = b''.join([self.STATUS[_stream_id]['LC'][0:3], _target['TGID'], _rf_src])
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
                    dmrbits = bitarray(endian='big')
                    dmrbits.frombytes(dmrpkt)
                    # Create a voice header packet (FULL LC)
                    if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                        dmrbits = _target_status[_target['TS']]['TX_H_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_H_LC'][98:197]
                    # Create a voice terminator packet (FULL LC)
                    elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                        dmrbits = _target_status[_target['TS']]['TX_T_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_T_LC'][98:197]
                        if CONFIG['REPORTS']['REPORT']:
                            call_duration = pkt_time - _target_status[_target['TS']]['TX_START']
                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                    # Create a Burst B-E packet (Embedded LC)
                    elif _dtype_vseq in [1,2,3,4]:
                        dmrbits = dmrbits[0:116] + _target_status[_target['TS']]['TX_EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                    dmrpkt = dmrbits.tobytes()
                    #_tmp_data = b''.join([_tmp_data, dmrpkt, b'\x00\x00']) # Add two bytes of nothing since OBP doesn't include BER & RSSI bytes #_data[53:55]
                    _tmp_data = b''.join([_tmp_data, dmrpkt])

                # Transmit the packet to the destination system
                systems[_target['SYSTEM']].send_system(_tmp_data,_hops,_ber,_rssi,_source_server, _source_rptr)
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
                'packets': 0
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
                    'crcs': set()

                }
            
            self.STATUS[_stream_id]['LAST'] = pkt_time
            self.STATUS[_stream_id]['packets'] = self.STATUS[_stream_id]['packets'] + 1
            
            hr_times = {}
            for system in systems: 
                if system != self._system and CONFIG['SYSTEMS'][system]['MODE'] != 'OPENBRIDGE':
                        for _sysslot in systems[system].STATUS:
                            if 'RX_STREAM_ID' in systems[system].STATUS[_sysslot] and _stream_id == systems[system].STATUS[_sysslot]['RX_STREAM_ID']:
                                if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']: 
                                    logger.debug("(%s) OBP UNIT *LoopControl* FIRST HBP: %s, STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",self._system, system, int_id(_stream_id), int_id(_dst_id),_sysslot)
                                    self.STATUS[_stream_id]['LOOPLOG'] = True
                                self.STATUS[_stream_id]['LAST'] = pkt_time
                                return
                else:
                    if _stream_id in systems[system].STATUS and '1ST' in systems[system].STATUS[_stream_id] and    systems[system].STATUS[_stream_id]['TGID'] == _dst_id:
                        hr_times[system] = systems[system].STATUS[_stream_id]['1ST']
                    
            #use the minimum perf_counter to ensure
            #We always use only the earliest packet
            fi = min(hr_times, key=hr_times.get, default = False)
            
            hr_times = None
            
            if not fi:
                logger.warning("(%s) OBP UNIT *LoopControl* fi is empty for some reason : %s, STREAM ID: %s, TG: %s, TS: %s",self._system, int_id(_stream_id), int_id(_dst_id),_sysslot)
                self.STATUS[_stream_id]['LAST'] = pkt_time
                return
            
            if self._system != fi:             
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
                self.sendDataToOBP('DATA-GATEWAY',_data,dmrpkt,pkt_time,_stream_id,_dst_id,_peer_id,_rf_src,_bits,_slot,_source_rptr,_ber,_rssi)
                 
            
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
                (_d_system,_d_slot,_d_time) = SUB_MAP[_dst_id]
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
                #If destination ID is logged in as a hotspot
                for _d_system in systems:
                    if CONFIG['SYSTEMS'][_d_system]['MODE'] == 'MASTER':
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

                _inthops = 0 
                if _hops:
                    _inthops = int.from_bytes(_hops,'big')
                logger.info('(%s) *CALL START* STREAM ID: %s, SUB: %s (%s), RPTR: %s (%s), PEER: %s (%s) TGID %s (%s), TS %s, SRC: %s, HOPS %s', 
                        self._system, int_id(_stream_id),get_alias(_rf_src, subscriber_ids),int_id(_rf_src),self.get_rptr(_source_rptr), int_id(_source_rptr),  get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot,int_id(_source_server),_inthops)
                if CONFIG['REPORTS']['REPORT']:
                    self._report.send_bridgeEvent('GROUP VOICE,START,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))


            else:
                if 'packets' in self.STATUS[_stream_id]:
                    self.STATUS[_stream_id]['packets'] = self.STATUS[_stream_id]['packets'] +1
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
                    
                
                #LoopControl
                hr_times = {}
                for system in systems:                            
                   # if system  == self._system:
                   #     continue
                    if system != self._system and CONFIG['SYSTEMS'][system]['MODE'] != 'OPENBRIDGE':
                        for _sysslot in systems[system].STATUS:
                            if 'RX_STREAM_ID' in systems[system].STATUS[_sysslot] and _stream_id == systems[system].STATUS[_sysslot]['RX_STREAM_ID']:
                                if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']: 
                                    logger.debug("(%s) OBP *LoopControl* FIRST HBP: %s, STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",self._system, system, int_id(_stream_id), int_id(_dst_id),_sysslot)
                                    self.STATUS[_stream_id]['LOOPLOG'] = True
                                self.STATUS[_stream_id]['LAST'] = pkt_time
                                return
                    else:
                        #if _stream_id in systems[system].STATUS and systems[system].STATUS[_stream_id]['START'] <= self.STATUS[_stream_id]['START']:
                        if _stream_id in systems[system].STATUS and '1ST' in systems[system].STATUS[_stream_id] and systems[system].STATUS[_stream_id]['TGID'] == _dst_id:
                             hr_times[system] = systems[system].STATUS[_stream_id]['1ST']
                
                #use the minimum perf_counter to ensure
                #We always use only the earliest packet
                fi = min(hr_times, key=hr_times.get, default = False)
                
                hr_times = None
                
                if not fi:
                    logger.warning("(%s) OBP *LoopControl* fi is empty for some reason : STREAM ID: %s, TG: %s, TS: %s",self._system, int_id(_stream_id), int_id(_dst_id),_sysslot)
                    return
                
                if self._system != fi:             
                    if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']:
                        call_duration = pkt_time - self.STATUS[_stream_id]['START']
                        packet_rate = 0
                        if 'packets' in self.STATUS[_stream_id]:
                            packet_rate = self.STATUS[_stream_id]['packets'] / call_duration
                        logger.debug("(%s) OBP *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE. PACKET RATE %0.2f/s",self._system, fi, int_id(_stream_id), int_id(_dst_id),call_duration)
                        self.STATUS[_stream_id]['LOOPLOG'] = True
                    self.STATUS[_stream_id]['LAST'] = pkt_time
                    
                    if CONFIG['SYSTEMS'][self._system]['ENHANCED_OBP'] and '_bcsq' not in self.STATUS[_stream_id]:
                        systems[self._system].send_bcsq(_dst_id,_stream_id)
                        self.STATUS[_stream_id]['_bcsq'] = True
                    return
                
                #Rate drop
                if self.STATUS[_stream_id]['packets'] > 18 and (self.STATUS[_stream_id]['packets'] / self.STATUS[_stream_id]['START'] > 25):
                    logger.warning("(%s) *PacketControl* RATE DROP! Stream ID:, %s TGID: %s",self._system,int_id(_stream_id),int_id(_dst_id))
                    return
                
                #Duplicate handling#
                #Handle inbound duplicates
                #Duplicate complete packet
                if self.STATUS[_stream_id]['lastData'] and self.STATUS[_stream_id]['lastData'] == _data and _seq > 1:
                    self.STATUS[_stream_id]['loss'] += 1
                    logger.debug("(%s) *PacketControl* last packet is a complete duplicate of the previous one, disgarding. Stream ID:, %s TGID: %s, LOSS: %.2f%%",self._system,int_id(_stream_id),int_id(_dst_id),((self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100))
                    return
                #Duplicate SEQ number
                if _seq and _seq == self.STATUS[_stream_id]['lastSeq']:
                    self.STATUS[_stream_id]['loss'] += 1
                    logger.debug("(%s) *PacketControl* Duplicate sequence number %s, disgarding. Stream ID:, %s TGID: %s, LOSS: %.2f%%",self._system,_seq,int_id(_stream_id),int_id(_dst_id),((self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100))
                    return
                #Inbound out-of-order packets
                if _seq and self.STATUS[_stream_id]['lastSeq']  and (_seq != 1) and (_seq < self.STATUS[_stream_id]['lastSeq']):
                    self.STATUS[_stream_id]['loss'] += 1
                    logger.debug("%s) *PacketControl* Out of order packet - last SEQ: %s, this SEQ: %s,  disgarding. Stream ID:, %s TGID: %s, LOSS: %.2f%%",self._system,self.STATUS[_stream_id]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id),((self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100))
                    return
                #Duplicate DMR payload to previuos packet (by hash
                if  _seq > 0 and _pkt_crc in self.STATUS[_stream_id]['crcs']:
                    self.STATUS[_stream_id]['loss'] += 1
                    logger.debug("(%s) *PacketControl* DMR packet payload with hash: %s seen before in this stream, disgarding. Stream ID:, %s TGID: %s: SEQ:%s PACKETS: %s, LOSS: %.2f%% ",self._system,_pkt_crc,int_id(_stream_id),int_id(_dst_id),_seq, self.STATUS[_stream_id]['packets'],((self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100))
                    return
                #Inbound missed packets
                if _seq and self.STATUS[_stream_id]['lastSeq'] and _seq > (self.STATUS[_stream_id]['lastSeq']+1):
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
                if int_id(_dst_id) >= 5 and int_id(_dst_id) != 9 and (str(int_id(_dst_id)) not in BRIDGES):
                    logger.debug('(%s) Bridge for STAT TG %s does not exist. Creating',self._system, int_id(_dst_id))
                    make_stat_bridge(_dst_id)
            
            _sysIgnore = deque()
            for _bridge in BRIDGES:
                    for _system in BRIDGES[_bridge]:
                        
                        if _system['SYSTEM'] == self._system and _system['TGID'] == _dst_id and _system['TS'] == _slot and _system['ACTIVE'] == True:
                            _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,False,_sysIgnore,_hops, _source_server, _ber, _rssi, _source_rptr)


            # Final actions - Is this a voice terminator?
            if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM):
                call_duration = pkt_time - self.STATUS[_stream_id]['START']
                packet_rate = 0
                loss = 0.00
                if call_duration:
                    packet_rate = self.STATUS[_stream_id]['packets'] / call_duration
                    loss = (self.STATUS[_stream_id]['loss'] / self.STATUS[_stream_id]['packets']) * 100
                logger.info('(%s) *CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, Duration: %.2f, Packet rate: %.2f/s, Loss: %.2f%%', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration, packet_rate,loss)
                if CONFIG['REPORTS']['REPORT']:
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
                'crcs': set(),
                '_allStarMode': False
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
                'crcs': set(),
                '_allStarMode': False
                }
            }

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
                        if _noOBP == True:
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
                                'RX_PEER':   _peer_id
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
                        dmrbits = bitarray(endian='big')
                        dmrbits.frombytes(dmrpkt)
                        # Create a voice header packet (FULL LC)
                        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                            dmrbits = _target_status[_stream_id]['H_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['H_LC'][98:197]
                        # Create a voice terminator packet (FULL LC)
                        elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                            dmrbits = _target_status[_stream_id]['T_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['T_LC'][98:197]
                            if CONFIG['REPORTS']['REPORT']:
                                call_duration = pkt_time - _target_status[_stream_id]['START']
                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                        # Create a Burst B-E packet (Embedded LC)
                        elif _dtype_vseq in [1,2,3,4]:
                            dmrbits = dmrbits[0:116] + _target_status[_stream_id]['EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                        dmrpkt = dmrbits.tobytes()
                        _tmp_data = b''.join([_tmp_data, dmrpkt])

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

                        # Is this a new call stream?
                        if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
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
                        dmrbits = bitarray(endian='big')
                        dmrbits.frombytes(dmrpkt)
                        # Create a voice header packet (FULL LC)
                        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                            dmrbits = _target_status[_target['TS']]['TX_H_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_H_LC'][98:197]
                        # Create a voice terminator packet (FULL LC)
                        elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                            dmrbits = _target_status[_target['TS']]['TX_T_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_T_LC'][98:197]
                            if CONFIG['REPORTS']['REPORT']:
                                call_duration = pkt_time - _target_status[_target['TS']]['TX_START']
                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                        # Create a Burst B-E packet (Embedded LC)
                        elif _dtype_vseq in [1,2,3,4]:
                            dmrbits = dmrbits[0:116] + _target_status[_target['TS']]['TX_EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                        try:
                            dmrpkt = dmrbits.tobytes()
                        except AttributeError:
                            logger.exception('(%s) Non-fatal AttributeError - dmrbits.tobytes()',self._system)
                            
                        _tmp_data = b''.join([_tmp_data, dmrpkt, _data[53:55]])

                    # Transmit the packet to the destination system
                    systems[_target['SYSTEM']].send_system(_tmp_data,b'',_ber,_rssi,_source_server, _source_rptr)
       
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
                'RX_PEER':   _peer_id
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
            systems[system]._report.send_bridgeEvent('UNIT DATA,DATA,TX,{},{},{},{},{},{}'.format(_target, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), 1, _int_dst_id).encode(encoding='utf-8', errors='ignore'))
    

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
        
        #Add system to SUB_MAP
        SUB_MAP[_rf_src] = (self._system,_slot,pkt_time)
        
        def resetallStarMode():
            self.STATUS[_slot]['_allStarMode'] = False
            logger.info('(%s) Reset all star mode -> dial mode',self._system)
        
        #Rewrite GPS Data comming in as a group call to a unit call
        #if (_call_type == 'group' or _call_type == 'vcsbk') and _int_dst_id == 900999:
            #_bits = header(_slot,'unit',_bits)
            #logger.info('(%s) Type Rewrite - GPS data from ID: %s,  on TG 900999 rewritten to unit call to ID 900999 : bits %s',self._system,int_id(_rf_src),_bits)
            #_call_type == 'unit'
       
        #Rewrite incoming loro request to group call
        #if _call_type == 'unit' and _int_dst_id == 9990:
            #_bits = header(_slot,'group',_bits)
            #logger.info('(%s) Type Rewrite - Echo data from ID: %s,  on PC 9990 rewritten to group call to TG 9990',self._system,int_id(_rf_src))
            #_call_type == 'group'
       
       
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
                self.sendDataToOBP('DATA-GATEWAY',_data,dmrpkt,pkt_time,_stream_id,_dst_id,_peer_id,_rf_src,_bits,_slot,_source_rptr)
                
            #Send to all openbridges 
            # sysIgnore = []
            for system in systems:
                if system  == self._system:
                    continue
                if system == 'DATA-GATEWAY':
                    continue
                #We only want to send data calls to individual IDs via FreeBridge (not OpenBridge)
                if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE' and CONFIG['SYSTEMS'][system]['VER'] > 1 and (_int_dst_id >= 1000000):
                    self.sendDataToOBP(system,_data,dmrpkt,pkt_time,_stream_id,_dst_id,_peer_id,_rf_src,_bits,_slot,_source_rptr)
                    
            #If destination ID is in the Subscriber Map
            if _dst_id in SUB_MAP:
                (_d_system,_d_slot,_d_time) = SUB_MAP[_dst_id]
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
                #If destination ID is logged in as a hotspot
                for _d_system in systems:
                    if CONFIG['SYSTEMS'][_d_system]['MODE'] == 'MASTER':
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
            self.STATUS[_slot]['RX_STREAM_ID'] = _stream_id
            self.STATUS[_slot]['VOICE_STREAM'] = _voice_call
            
            self.STATUS[_slot]['packets'] = self.STATUS[_slot]['packets'] +1 
            
                
        
        #Handle  private voice calls (for reflectors)
        elif _call_type == 'unit' and not _data_call and not self.STATUS[_slot]['_allStarMode']:
            if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
                
                self.STATUS[_slot]['packets'] = 0
                self.STATUS[_slot]['crcs'] = set()
                
                self.STATUS[_slot]['_stopTgAnnounce'] = False
                
                logger.info('(%s) Reflector: Private call from %s to %s',self._system, int_id(_rf_src), _int_dst_id)
                if _int_dst_id >= 5 and _int_dst_id != 8  and _int_dst_id != 9 and _int_dst_id <= 999999:
                    _bridgename = ''.join(['#',str(_int_dst_id)])
                    if _bridgename not in BRIDGES and not (_int_dst_id >= 4000 and _int_dst_id <= 5000) and not (_int_dst_id >=9991 and _int_dst_id <= 9999):
                            logger.info('(%s) [A] Reflector for TG %s does not exist. Creating as User Activated. Timeout: %s',self._system, _int_dst_id,CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'])
                            make_single_reflector(_dst_id,CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'],self._system)
                    
                    if _int_dst_id > 5 and _int_dst_id != 9 and _int_dst_id != 5000 and not (_int_dst_id >=9991 and _int_dst_id <= 9999):
                        for _bridge in BRIDGES:
                            if _bridge[0:1] != '#':
                                continue
                            for _system in BRIDGES[_bridge]:
                                _dehash_bridge = _bridge[1:]
                                if _system['SYSTEM'] == self._system:
                                    # TGID matches a rule source, reset its timer
                                    if _slot == _system['TS'] and _dst_id == _system['TGID'] and ((_system['TO_TYPE'] == 'ON' and (_system['ACTIVE'] == True)) or (_system['TO_TYPE'] == 'OFF' and _system['ACTIVE'] == False)):
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [B] Transmission match for Reflector: %s. Reset timeout to %s', self._system, _bridge, _system['TIMER'])
                            
                                # TGID matches an ACTIVATION trigger
                                if _int_dst_id == int(_dehash_bridge) and _system['SYSTEM'] == self._system and  _slot == _system['TS']:
                                    # Set the matching rule as ACTIVE
                                    if _system['ACTIVE'] == False:
                                        _system['ACTIVE'] = True
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [C] Reflector: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                        # Cancel the timer if we've enabled an "OFF" type timeout
                                        if _system['TO_TYPE'] == 'OFF':
                                            _system['TIMER'] = pkt_time
                                            logger.info('(%s) [D] Reflector: %s has an "OFF" timer and set to "ON": timeout timer cancelled', self._system, _bridge)
                                # Reset the timer for the rule
                                if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON':
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
            
            
            if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM) and (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM):
                
                _say = [words[_lang]['silence']]

                if _int_dst_id <= 5 or _int_dst_id == 9:
                    logger.info('(%s) Reflector: voice called - TG < 5 or 9 - "busy""', self._system)
                    _say.append(words[_lang]['busy'])
                    _say.append(words[_lang]['silence'])
                    self.STATUS[_slot]['_stopTgAnnounce'] = True
                    
                #Allstar mode switch
                if CONFIG['ALLSTAR']['ENABLED'] and _int_dst_id == 8:
                    logger.info('(%s) Reflector: voice called - TG 8 AllStar"', self._system)
                    _say.append(words[_lang]['all-star-link-mode'])
                    _say.append(words[_lang]['silence'])
                    self.STATUS[_slot]['_stopTgAnnounce'] = True
                    self.STATUS[_slot]['_allStarMode'] = True
                    reactor.callLater(30,resetallStarMode)
                elif not CONFIG['ALLSTAR']['ENABLED'] and _int_dst_id == 8:
                    logger.info('(%s) Reflector: TG 8 AllStar not enabled"', self._system)
                    _say.append(words[_lang]['busy'])
                    _say.append(words[_lang]['silence'])
                    self.STATUS[_slot]['_stopTgAnnounce'] = True
                    
                    
                
                #If disconnection called
                if _int_dst_id == 4000:
                    logger.info('(%s) Reflector: voice called - 4000 "not linked"', self._system)
                    _say.append(words[_lang]['notlinked'])
                    _say.append(words[_lang]['silence'])
                 
                 #If status called
                elif _int_dst_id == 5000:
                    _active = False
                    for _bridge in BRIDGES:
                        if _bridge[0:1] != '#':
                            continue
                        for _system in BRIDGES[_bridge]:
                            _dehash_bridge = _bridge[1:]
                            if _system['SYSTEM'] == self._system and _slot == _system['TS']:
                                    if _system['ACTIVE'] == True:
                                        logger.info('(%s) Reflector: voice called - 5000 status - "linked to %s"', self._system,_dehash_bridge)
                                        _say.append(words[_lang]['silence'])
                                        _say.append(words[_lang]['linkedto'])
                                        _say.append(words[_lang]['silence'])
                                        _say.append(words[_lang]['to'])
                                        _say.append(words[_lang]['silence'])
                                        _say.append(words[_lang]['silence']) 
                                        
                                        for num in str(_dehash_bridge):
                                            _say.append(words[_lang][num])
                                        
                                        _active = True
                                        break
                        
                    if _active == False:
                        logger.info('(%s) Reflector: voice called - 5000 status - "not linked"', self._system)
                        _say.append(words[_lang]['notlinked'])
                
                #Information services
                elif _int_dst_id >= 9991 and _int_dst_id <= 9999:
                    self.STATUS[_slot]['_stopTgAnnounce'] = True
                    reactor.callInThread(playFileOnRequest,self,_int_dst_id)
                    #playFileOnRequest(self,_int_dst_id)
                    
                
                #Speak what TG was requested to link
                elif not self.STATUS[_slot]['_stopTgAnnounce']:
                    logger.info('(%s) Reflector: voice called (linking)  "linked to %s"', self._system,_int_dst_id)
                    _say.append(words[_lang]['silence'])
                    _say.append(words[_lang]['linkedto'])
                    _say.append(words[_lang]['silence'])
                    _say.append(words[_lang]['to'])
                    _say.append(words[_lang]['silence'])
                    _say.append(words[_lang]['silence'])
                    
                    for num in str(_int_dst_id):
                        _say.append(words[_lang][num])
     
                if _say:
                    speech = pkt_gen(bytes_3(5000), _nine, bytes_4(9), 1, _say)
                    #call speech in a thread as it contains sleep() and hence could block the reactor
                    reactor.callInThread(sendSpeech,self,speech)

            # Mark status variables for use later
            self.STATUS[_slot]['RX_PEER']      = _peer_id
            self.STATUS[_slot]['RX_SEQ']       = _seq
            self.STATUS[_slot]['RX_RFS']       = _rf_src
            self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
            self.STATUS[_slot]['RX_TGID']      = _dst_id
            self.STATUS[_slot]['RX_TIME']      = pkt_time
            self.STATUS[_slot]['RX_STREAM_ID'] = _stream_id
            self.STATUS[_slot]['VOICE_STREAM'] = _voice_call
            
            self.STATUS[_slot]['packets'] = self.STATUS[_slot]['packets'] +1                
        
        #Handle group calls
        if _call_type == 'group' or _call_type == 'vcsbk':

            # Is this a new call stream?
            if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
                
                self.STATUS[_slot]['packets'] = 0
                self.STATUS[_slot]['loss'] = 0
                self.STATUS[_slot]['crcs'] = set()
                
                if (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM) and (pkt_time < (self.STATUS[_slot]['RX_TIME'] + STREAM_TO)) and (_rf_src != self.STATUS[_slot]['RX_RFS']):
                    logger.warning('(%s) Packet received with STREAM ID: %s <FROM> SUB: %s PEER: %s <TO> TGID %s, SLOT %s collided with existing call', self._system, int_id(_stream_id), int_id(_rf_src), int_id(_peer_id), int_id(_dst_id), _slot)
                    return

                # This is a new call stream
                self.STATUS[_slot]['RX_START'] = pkt_time
                
                if _call_type == 'group' :
                    if _dtype_vseq == 6:
                        logger.info('(%s) *DATA HEADER* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                                self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                        if CONFIG['REPORTS']['REPORT']:
                            self._report.send_bridgeEvent('DATA HEADER,DATA,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))
                    
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
                if int_id(_dst_id) >= 5 and int_id(_dst_id) != 9 and int_id(_dst_id) != 4000 and int_id(_dst_id) != 5000  and (str(int_id(_dst_id)) not in BRIDGES):
                    logger.info('(%s) Bridge for TG %s does not exist. Creating as User Activated. Timeout %s',self._system, int_id(_dst_id),CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'])
                    make_single_bridge(_dst_id,self._system,_slot,CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'])

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
                        
            #Packet rate limit
            #Rate drop
            if self.STATUS[_slot]['packets'] > 18 and (self.STATUS[_slot]['packets'] / (pkt_time - self.STATUS[_slot]['RX_START']) > 25):
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
            for system in systems:                            
                if system  == self._system:
                    continue
                if CONFIG['SYSTEMS'][system]['MODE'] != 'OPENBRIDGE':
                    for _sysslot in systems[system].STATUS:
                        if 'RX_STREAM_ID' in systems[system].STATUS[_sysslot] and _stream_id == systems[system].STATUS[_sysslot]['RX_STREAM_ID']:
                            if 'LOOPLOG' not in self.STATUS[_slot] or not self.STATUS[_slot]['LOOPLOG']: 
                                logger.debug("(%s) OBP *LoopControl* FIRST HBP: %s, STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",self._system, system, int_id(_stream_id), int_id(_dst_id),_sysslot)
                                self.STATUS[_slot]['LOOPLOG'] = True
                            self.STATUS[_slot]['LAST'] = pkt_time
                            return
                else:
                    if _stream_id in systems[system].STATUS and '1ST' in systems[system].STATUS[_stream_id] and systems[system].STATUS[_stream_id]['TGID'] == _dst_id:
                        if 'LOOPLOG' not in self.STATUS[_slot] or not self.STATUS[_slot]['LOOPLOG']:
                            logger.debug("(%s) OBP *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE",self._system, system, int_id(_stream_id), int_id(_dst_id))
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
            #Handle inbound duplicates
            if _seq and _seq == self.STATUS[_slot]['lastSeq']:
                self.STATUS[_slot]['loss'] += 1
                logger.debug("(%s) *PacketControl* Duplicate sequence number %s, disgarding. Stream ID:, %s TGID: %s",self._system,_seq,int_id(_stream_id),int_id(_dst_id))
                return
            #Inbound out-of-order packets
            if _seq and self.STATUS[_slot]['lastSeq']  and (_seq != 1) and (_seq < self.STATUS[_slot]['lastSeq']):
                self.STATUS[_slot]['loss'] += 1
                logger.debug("%s) *PacketControl* Out of order packet - last SEQ: %s, this SEQ: %s,  disgarding. Stream ID:, %s TGID: %s ",self._system,self.STATUS[_slot]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id))
                return
            #Duplicate DMR payload to previuos packet (by hash)
            if _seq > 0 and _pkt_crc in self.STATUS[_slot]['crcs']:
                self.STATUS[_slot]['loss'] += 1
                logger.debug("(%s) *PacketControl* DMR packet payload with hash: %s seen before in this stream, disgarding. Stream ID:, %s TGID: %s, SEQ: %s, packets %s: ",self._system,_pkt_crc,int_id(_stream_id),int_id(_dst_id),_seq,self.STATUS[_slot]['packets'])
                return
            #Inbound missed packets
            if _seq and self.STATUS[_slot]['lastSeq'] and _seq > (self.STATUS[_slot]['lastSeq']+1):
                self.STATUS[_slot]['loss'] += 1
                logger.debug("(%s) *PacketControl* Missed packet(s) - last SEQ: %s, this SEQ: %s. Stream ID:, %s TGID: %s ",self._system,self.STATUS[_slot]['lastSeq'],_seq,int_id(_stream_id),int_id(_dst_id))
        
            #Save this sequence number 
            self.STATUS[_slot]['lastSeq'] = _seq
            #Save this packet
            self.STATUS[_slot]['lastData'] = _data
                          
            _sysIgnore = deque()
            for _bridge in BRIDGES:
                #if _bridge[0:1] != '#':
                if True:
                    for _system in BRIDGES[_bridge]:
                        if _system['SYSTEM'] == self._system and _system['TGID'] == _dst_id and _system['TS'] == _slot and _system['ACTIVE'] == True:
                            _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,False,_sysIgnore,_source_server,_ber,_rssi, _source_rptr)
                        
                            #Send to reflector or TG too, if it exists
                            if _bridge[0:1] == '#':
                                _bridge = _bridge[1:]
                            else:
                                _bridge = ''.join(['#',_bridge])
                            if _bridge in BRIDGES:
                                _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,False,_sysIgnore,_source_server,_ber,_rssi,_source_rptr)

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
                if CONFIG['REPORTS']['REPORT']:
                   self._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))
                
                #Reset back to False  
                self.STATUS[_slot]['lastSeq'] = False
                self.STATUS[_slot]['lastData'] = False

                #
                # Begin in-band signalling for call end. This has nothign to do with routing traffic directly.
                #

                # Iterate the rules dictionary
                for _bridge in BRIDGES:
                    if (_bridge[0:1] == '#') and (_int_dst_id != 9):
                        continue
                    for _system in BRIDGES[_bridge]:
                        if _system['SYSTEM'] == self._system:

                            # TGID matches a rule source, reset its timer
                            if _slot == _system['TS'] and _dst_id == _system['TGID'] and ((_system['TO_TYPE'] == 'ON' and (_system['ACTIVE'] == True)) or (_system['TO_TYPE'] == 'OFF' and _system['ACTIVE'] == False)):
                                _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                logger.info('(%s) [1] Transmission match for Bridge: %s. Reset timeout to %s', self._system, _bridge, _system['TIMER'])

                            # TGID matches an ACTIVATION trigger
                            if (_dst_id in _system['ON'] or _dst_id in _system['RESET']) and _slot == _system['TS']:
                                # Set the matching rule as ACTIVE
                                if _dst_id in _system['ON']:
                                    if _system['ACTIVE'] == False:
                                        _system['ACTIVE'] = True
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [2] Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                        # Cancel the timer if we've enabled an "OFF" type timeout
                                        if _system['TO_TYPE'] == 'OFF':
                                            _system['TIMER'] = pkt_time
                                            logger.info('(%s) [3] Bridge: %s set to "OFF" with an on timer rule: timeout timer cancelled', self._system, _bridge)
                                # Reset the timer for the rule
                                if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON':
                                    _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                    logger.info('(%s) [4] Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)

                            # TGID matches an DE-ACTIVATION trigger
                            #Single TG mode
                            if (CONFIG['SYSTEMS'][self._system]['MODE'] == 'MASTER' and CONFIG['SYSTEMS'][self._system]['SINGLE_MODE']) == True:
                                if (_dst_id in _system['OFF']  or _dst_id in _system['RESET'] or _dst_id != _system['TGID']) and _slot == _system['TS']:
                                #if (_dst_id in _system['OFF']  or _dst_id in _system['RESET']) and _slot == _system['TS']:
                                    # Set the matching rule as ACTIVE
                                    #Single TG mode
                                    if _dst_id in _system['OFF'] or _dst_id != _system['TGID']:
                                    #if _dst_id in _system['OFF']:
                                        if _system['ACTIVE'] == True:
                                            _system['ACTIVE'] = False
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
            self.STATUS[_slot]['RX_STREAM_ID'] = _stream_id
            
            self.STATUS[_slot]['crcs'].add(_pkt_crc)

#
# Socket-based reporting section
#
class bridgeReportFactory(reportFactory):

    def send_bridge(self):
        serialized = pickle.dumps(BRIDGES, protocol=2) #.decode("utf-8", errors='ignore')
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

    # Ensure we have a path for the rules file, if one wasn't specified, then use the default (top of file)
    if not cli_args.RULES_FILE:
        cli_args.RULES_FILE = os.path.dirname(os.path.abspath(__file__))+'/rules.py'

    # Start the system logger
    if cli_args.LOG_LEVEL:
        CONFIG['LOGGER']['LOG_LEVEL'] = cli_args.LOG_LEVEL
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('\n\nCopyright (c) 2020, 2021, 2022 Simon G7RZU simon@gb7fr.org.uk')
    logger.info('Copyright (c) 2013, 2014, 2015, 2016, 2018, 2019\n\tThe Regents of the K0USY Group. All rights reserved.\n')
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
    
    #Subscriber map for unit calls - complete with test entry
    #SUB_MAP = {bytes_3(73578):('REP-1',1,time())}
    SUB_MAP = {}
    
    
    if CONFIG['ALIASES']['SUB_MAP_FILE']:
        try:
            with open(CONFIG['ALIASES']['PATH'] + CONFIG['ALIASES']['SUB_MAP_FILE'],'rb') as _fh:
                SUB_MAP = pickle.load(_fh)
        except:
            logger.warning('(SUBSCRIBER) Cannot load SUB_MAP file')
            #sys.exit('(SUBSCRIBER) TERMINATING: SUB_MAP file not found or invalid')
        
        #Test value
        #SUB_MAP[bytes_3(73578)] = ('REP-1',1,time())
    
    
    #Generator
    generator = {}
    systemdelete = deque()
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if CONFIG['SYSTEMS'][system]['MODE'] == 'MASTER' and (CONFIG['SYSTEMS'][system]['GENERATOR'] > 1):
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
    
    # Default reflector
    logger.debug('(ROUTER) Setting default reflectors')
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['MODE'] != 'MASTER':
            continue
        if CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'] > 0:
            make_default_reflector(CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'],CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER'],system)
            
    #static TGs 
    logger.debug('(ROUTER) setting static TGs')
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['MODE'] != 'MASTER':
            continue
        _tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
        ts1 = []
        ts2 = []
        if CONFIG['SYSTEMS'][system]['TS1_STATIC']:
            ts1 = CONFIG['SYSTEMS'][system]['TS1_STATIC'].split(',')
        if CONFIG['SYSTEMS'][system]['TS2_STATIC']:
            ts2 = CONFIG['SYSTEMS'][system]['TS2_STATIC'].split(',')
            
        for tg in ts1:
                if not tg:
                    continue
                tg = int(tg)
                make_static_tg(tg,1,_tmout,system)
        for tg in ts2:
                if not tg:
                    continue
                tg = int(tg)
                make_static_tg(tg,2,_tmout,system)

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
    logger.info('(GLOBAL) FreeDMR \'bridge_master.py\' -- SYSTEM STARTING...')

    
    listeningPorts = {}

    
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
                systems[system] = routerOBP(system, CONFIG, report_server)                
            else:
                if CONFIG['SYSTEMS'][system]['MODE'] == 'MASTER' and CONFIG['SYSTEMS'][system]['ANNOUNCEMENT_LANGUAGE'] not in CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGES'].split(','):
                    logger.warning('(GLOBAL) Invalid language in ANNOUNCEMENT_LANGUAGE, skipping system %s',system)
                    continue
                systems[system] = routerHBP(system, CONFIG, report_server)
            listeningPorts[system] = reactor.listenUDP(CONFIG['SYSTEMS'][system]['PORT'], systems[system], interface=CONFIG['SYSTEMS'][system]['IP'])
            logger.debug('(GLOBAL) %s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])

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
        
    #STAT trimmer - once every hour (roughly - shifted so all timed tasks don't run at once
    if CONFIG['GLOBAL']['GEN_STAT_BRIDGES']:
        stat_trimmer_task = task.LoopingCall(statTrimmer)
        stat_trimmer = stat_trimmer_task.start(3700)#3600
        stat_trimmer.addErrback(loopingErrHandle)
        
    #KA Reporting
    ka_task = task.LoopingCall(kaReporting)
    ka = ka_task.start(60)
    ka.addErrback(loopingErrHandle)
    
    #Subscriber map trimmer
    sub_trimmer_task = task.LoopingCall(SubMapTrimmer)
    sub_trimmer = sub_trimmer_task.start(3600)#3600
    sub_trimmer.addErrback(loopingErrHandle)
    
    #more threads
    reactor.suggestThreadPoolSize(100)
    
    reactor.run()
