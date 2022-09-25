#!/usr/bin/env python
#
###############################################################################
#   Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
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
This program does very little on its own. It is intended to be used as a module
to build applications on top of the HomeBrew Repeater Protocol. By itself, it
will only act as a peer or master for the systems specified in its configuration
file (usually hblink.cfg). It is ALWAYS best practice to ensure that this program
works stand-alone before troubleshooting any applications that use it. It has
sufficient logging to be used standalone as a troubleshooting application.
'''

# Specifig functions from modules we need
from binascii import b2a_hex as ahex
from binascii import a2b_hex as bhex
from random import randint
from hashlib import sha256, sha1, blake2b
from hmac import new as hmac_new, compare_digest
from time import time, time_ns
from collections import deque

# Twisted is pretty important, so I keep it separate
from twisted.internet.protocol import DatagramProtocol, Factory, Protocol
from twisted.protocols.basic import NetstringReceiver
from twisted.internet import reactor, task

# Other files we pull from -- this is mostly for readability and segmentation
import log
import config
from const import *
from dmr_utils3.utils import int_id, bytes_4, mk_id_dict

# Imports for the reporting server
import pickle
from reporting_const import *

# The module needs logging logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)

from functools import partial, partialmethod

import ssl

from os.path import isfile, getmtime
from urllib.request import urlopen

import csv


logging.TRACE = 5
logging.addLevelName(logging.TRACE, 'TRACE')
logging.Logger.trace = partialmethod(logging.Logger.log, logging.TRACE)
logging.trace = partial(logging.log, logging.TRACE)

# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS, Forked by Simon Adlem - G7RZU'
__copyright__  = 'Copyright (c) 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group, Simon Adlem, G7RZU 2020,2021,2022'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT; Jon Lee, G4TSN; Norman Williams, M6NBP'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Simon Adlem G7RZU'
__email__      = 'simon@gb7fr.org.uk'


# Global variables used whether we are a module or __main__
systems = {}

# Timed loop used for reporting HBP status
def config_reports(_config, _factory):
    def reporting_loop(_logger, _server):
        _logger.debug('(GLOBAL) Periodic reporting loop started')
        _server.send_config()

    logger.info('(GLOBAL) HBlink TCP reporting server configured')

    report_server = _factory(_config)
    report_server.clients = deque()
    reactor.listenTCP(_config['REPORTS']['REPORT_PORT'], report_server)

    reporting = task.LoopingCall(reporting_loop, logger, report_server)
    reporting.start(_config['REPORTS']['REPORT_INTERVAL'])

    return report_server


# Shut ourselves down gracefully by disconnecting from the masters and peers.
def hblink_handler(_signal, _frame):
    for system in systems:
        logger.info('(GLOBAL) SHUTDOWN: DE-REGISTER SYSTEM: %s', system)
        systems[system].dereg()

# Check a supplied ID against the ACL provided. Returns action (True|False) based
# on matching and the action specified.
def acl_check(_id, _acl):
    id = int_id(_id)
    for entry in _acl[1]:
        if entry[0] <= id <= entry[1]:
            return _acl[0]
    return not _acl[0]


#************************************************
#    OPENBRIDGE CLASS
#************************************************

class OPENBRIDGE(DatagramProtocol):
    def __init__(self, _name, _config, _report):
        # Define a few shortcuts to make the rest of the class more readable
        self._CONFIG = _config
        self._system = _name
        self._report = _report
        self._config = self._CONFIG['SYSTEMS'][self._system]
        self._laststrid = deque([], 20)

    def validate_id(self,_peer_id):
                
        _int_peer_id = int_id(_peer_id)
        _int_peer_id = int(_int_peer_id)
        _subscriber_ids = self._CONFIG['_SUB_IDS']
        _peer_ids = self._CONFIG['_PEER_IDS']
        _local_subscriber_ids = self._CONFIG['_LOCAL_SUBSCRIBER_IDS']
        
        if _int_peer_id in _local_subscriber_ids:
            return _local_subscriber_ids[_int_peer_id]
        elif _int_peer_id in _subscriber_ids:
            return _subscriber_ids[_int_peer_id]
        elif _int_peer_id in _peer_ids:
            return _peer_ids[_int_peer_id]
        else:
            return False
            
    def loopingErrHandle(self,failure):
        logger.error('(GLOBAL - hblink.py) Unhandled error in timed loop.\n %s', failure)
        

    def startProtocol(self):
        logger.info('(%s) Starting OBP. TARGET_IP: %s, TARGET_PORT: %s',self._system, self._config['TARGET_IP'], self._config['TARGET_PORT'])
        if self._config['ENHANCED_OBP']:
            logger.debug('(%s) *BridgeControl* starting KeepAlive timer',self._system)
            self._bcka_task = task.LoopingCall(self.send_bcka)
            self._bcka = self._bcka_task.start(10)
            self._bcka.addErrback(self.loopingErrHandle)
            
            logger.debug('(%s) *BridgeControl* starting Version timer',self._system)
            self._bcve_task = task.LoopingCall(self.send_bcve)
            self._bcve = self._bcve_task.start(60)
            self._bcve.addErrback(self.loopingErrHandle)
            

    def dereg(self):
        logger.info('(%s) is mode OPENBRIDGE. No De-Registration required, continuing shutdown', self._system)

    def send_system(self, _packet, _hops = b'', _ber = b'\x00', _rssi = b'\x00', _source_server = b'\x00\x00\x00\x00', _source_rptr = b'\x00\x00\x00\x00'):                      
        #Don't do anything if we are STUNned
        if 'STUN' in self._CONFIG:
            logger.info('(%s) Bridge STUNned, discarding', self._system)
            return
        
        if not _hops:
            _hops = 1
            _hops = _hops.to_bytes(1,'big')
            
        
        if _packet[:3] == DMR and self._config['TARGET_IP']:
            
            if 'VER' in self._config and self._config['VER'] > 4:
                _ver = VER.to_bytes(1,'big')
                _packet = b''.join([DMRE,_packet[4:11], self._CONFIG['GLOBAL']['SERVER_ID'],_packet[15:],_ber,_rssi,_ver,time_ns().to_bytes(8,'big'), _source_server, _source_rptr, _hops])
                _h = blake2b(key=self._config['PASSPHRASE'], digest_size=16)
                _h.update(_packet)
                _hash = _h.digest()
                _packet = b''.join([_packet, _hash])
                self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))

            elif 'VER' in self._config and self._config['VER'] == 4:
                _ver = VER.to_bytes(1,'big')
                _packet = b''.join([DMRE,_packet[4:11], self._CONFIG['GLOBAL']['SERVER_ID'],_packet[15:],_ber,_rssi,_ver,time_ns().to_bytes(8,'big'), _source_server, _hops])
                _h = blake2b(key=self._config['PASSPHRASE'], digest_size=16)
                _h.update(_packet)
                _hash = _h.digest()
                _packet = b''.join([_packet, _hash])
                self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
            
            elif 'VER' in self._config and self._config['VER'] == 3:
                _packet = b''.join([DMRF,_packet[4:11], self._CONFIG['GLOBAL']['SERVER_ID'],_packet[15:]])
                _h = blake2b(key=self._config['PASSPHRASE'], digest_size=16)
                _h.update(_packet)
                _hash = _h.digest()
                _packet = b''.join([_packet,time_ns().to_bytes(8,'big'), _hops, _hash])
                self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
            
            elif 'VER' in self._config and self._config['VER'] == 2:
                _packet = b''.join([DMRF,_packet[4:11], self._CONFIG['GLOBAL']['SERVER_ID'],_packet[15:], time_ns().to_bytes(8,'big')])
                _h = blake2b(key=self._config['PASSPHRASE'], digest_size=16)
                _h.update(_packet)
                _hash = _h.digest()
                _packet = b''.join([_packet,_hops, _hash])
                self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
                # KEEP THE FOLLOWING COMMENTED OUT UNLESS YOU'RE DEBUGGING DEEPLY!!!!
                #logger.debug('(%s) TX Packet to OpenBridge %s:%s -- %s %s', self._system, self._config['TARGET_IP'], self._config['TARGET_PORT'], _packet, _hash)
            else:                
                _packet = b''.join([DMRD,_packet[4:11], self._CONFIG['GLOBAL']['SERVER_ID'], _packet[15:]])
                _packet = b''.join([_packet, (hmac_new(self._config['PASSPHRASE'],_packet,sha1).digest())])
                self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
                
        else:
            
            if not self._config['TARGET_IP']:
                logger.debug('(%s) Not sent packet as TARGET_IP not currently known')
            else:
                logger.error('(%s) OpenBridge system was asked to send non DMR packet with send_system(): %s', self._system, _packet)
            
    def send_bcka(self):
        if self._config['TARGET_IP']:
            _packet = BCKA
            _packet = b''.join([_packet[:4], (hmac_new(self._config['PASSPHRASE'],_packet,sha1).digest())])
            self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
            logger.trace('(%s) *BridgeControl* sent KeepAlive',self._system)
        else:
            logger.trace('(%s) *BridgeControl* not sending KeepAlive, TARGET_IP currently not known',self._system)

    def send_bcst(self):
        if self._config['TARGET_IP']:
            _packet = BCST
            _packet = b''.join([_packet[:4], (hmac_new(self._config['PASSPHRASE'],_packet,sha1).digest())])
            self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
            logger.trace('(%s) *BridgeControl* sent BCST STUN',self._system)
        else:
            logger.trace('(%s) *BridgeControl* not sending BCST STUN, TARGET_IP currently not known',self._system)        
        
    def send_bcsq(self,_tgid,_stream_id):
        if self._config['TARGET_IP']:
            _packet = b''.join([BCSQ, _tgid, _stream_id])
            _packet = b''.join([_packet, (hmac_new(self._config['PASSPHRASE'],_packet,sha1).digest())])
            self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
            logger.trace('(%s) *BridgeControl* sent BCSQ Source Quench, TG: %s, Stream ID: %s',self._system,int_id(_tgid), int_id(_stream_id))
        else:
            logger.trace('(%s) *BridgeControl* Not sent BCSQ Source Quench TARGET_IP not known , TG: %s, Stream ID: %s',self._system,int_id(_tgid))
            
    def send_bcve(self):
        if self._config['ENHANCED_OBP'] and self._config['TARGET_IP']:
            _packet = b''.join([BCVE,VER.to_bytes(1,'big')])
            _packet = b''.join([_packet, (hmac_new(self._config['PASSPHRASE'],_packet[4:5],sha1).digest())])
            self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
            logger.trace('(%s) *BridgeControl* sent BCVE. Ver: %s',self._system,VER)
        else:
            logger.trace('(%s) *BridgeControl* not sending BCVE, TARGET_IP currently not known',self._system) 

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data,_hash,_hops = b'', _source_server = b'\x00\x00\x00\x00', _ber = b'\x00', _rssi = b'\x00', _source_rptr = b'\x00\x00\x00\x00'):
        pass
        #print(int_id(_peer_id), int_id(_rf_src), int_id(_dst_id), int_id(_seq), _slot, _call_type, _frame_type, repr(_dtype_vseq), int_id(_stream_id))

    def datagramReceived(self, _packet, _sockaddr):
        # Keep This Line Commented Unless HEAVILY Debugging!
        #logger.debug('(%s) RX packet from %s -- %s', self._system, _sockaddr, ahex(_packet))
        
        if _packet[:3] == DMR:    # DMRData -- encapsulated DMR data frame
            if _packet[:4] == DMRD:
                _data = _packet[:53]
                _stream_id = _data[16:20]
                if self._config['VER'] > 1:
                    if _stream_id not in self._laststrid:
                        logger.warning('(%s) *ProtoControl*  Version 1 protocol prohibited by PROTO_VER, Ver: %s',self._system,self._config['VER'])
                        self._laststrid.append(_stream_id)
                    self.send_bcve()
                    return
                _hash = _packet[53:]
                _ckhs = hmac_new(self._config['PASSPHRASE'],_data,sha1).digest()

                if compare_digest(_hash, _ckhs) and (_sockaddr == self._config['TARGET_SOCK'] or self._config['RELAX_CHECKS']):
                    _peer_id = _data[11:15]
                    if self._config['NETWORK_ID'] != _peer_id:
                        if _stream_id not in self._laststrid:
                            logger.error('(%s) OpenBridge packet discarded because NETWORK_ID: %s Does not match sent Peer ID: %s', self._system, int_id(self._config['NETWORK_ID']), int_id(_peer_id))
                            self._laststrid.append(_stream_id)
                        return
                    
                    #This is a v1 packet, so all the extended stuff we can set to default
                    #We are the source server if traffic came over a v1 bridge - sysops are responsible
                    #for bridged in traffic from their system
                    _source_server = self._CONFIG['GLOBAL']['SERVER_ID']
                    _source_rptr = b'\x00\x00\x00\x00'
                    _ber = b'\x00'
                    _rssi = b'\x00'
                    _hops = b''
                    
                    _seq = _data[4]
                    _rf_src = _data[5:8]
                    _dst_id = _data[8:11]
                    _bits = _data[15]
                    _slot = 2 if (_bits & 0x80) else 1
                    #_call_type = 'unit' if (_bits & 0x40) else 'group'
                    if _bits & 0x40:
                        _call_type = 'unit'
                    elif (_bits & 0x23) == 0x23:
                        _call_type = 'vcsbk'
                    else:
                        _call_type = 'group'
                    _frame_type = (_bits & 0x30) >> 4
                    _dtype_vseq = (_bits & 0xF) # data, 1=voice header, 2=voice terminator; voice, 0=burst A ... 5=burst F
                    #logger.debug('(%s) DMRD - Seqence: %s, RF Source: %s, Destination ID: %s', self._system, int_id(_seq), int_id(_rf_src), int_id(_dst_id))
                        

                    # Sanity check for OpenBridge -- all calls must be on Slot 1
                    if _slot != 1:
                        logger.error('(%s) OpenBridge packet discarded because it was not received on slot 1. SID: %s, TGID %s', self._system, int_id(_rf_src), int_id(_dst_id))
                        return
                    
                    #Don't do anything if we are STUNned
                    if 'STUN' in self._CONFIG:
                            if _stream_id not in self._laststrid:
                                logger.warning('(%s) Bridge STUNned, discarding', self._system)
                                self._laststrid.append(_stream_id)
                            return
                    
                    
                    #Low-level TG filtering 
                    if _call_type != 'unit':
                        _int_dst_id = int_id(_dst_id)
                        if _int_dst_id <= 79 or (_int_dst_id >= 9990 and _int_dst_id <= 9999) or (_int_dst_id >= 92 and _int_dst_id <= 199)  or _int_dst_id == 900999:
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY GLOBAL TG FILTER', self._system, int_id(_stream_id), _int_dst_id)
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                    
                    # ACL Processing
                    if self._CONFIG['GLOBAL']['USE_ACL']:
                        if not acl_check(_rf_src, self._CONFIG['GLOBAL']['SUB_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                        if _slot == 1 and not acl_check(_dst_id, self._CONFIG['GLOBAL']['TG1_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                    if self._config['USE_ACL']:
                        if not acl_check(_rf_src, self._config['SUB_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY SYSTEM ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                        if not acl_check(_dst_id, self._config['TG1_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return

                    # Userland actions -- typically this is the function you subclass for an application
                    self.dmrd_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data,_hash,_hops,_source_server,_ber,_rssi,_source_rptr)
                    #Silently treat a DMRD packet like a keepalive - this is because it's traffic and the 
                    #Other end may not have enabled ENAHNCED_OBP
                    self._config['_bcka'] = time()
                else:
                    h,p = _sockaddr
                    logger.warning('(%s) OpenBridge HMAC failed, packet discarded - OPCODE: %s DATA: %s HMAC LENGTH: %s HMAC: %s SRC IP: %s SRC PORT: %s', self._system, _packet[:4], repr(_packet[:53]), len(_packet[53:]), repr(_packet[53:]),h,p)
                    
            elif _packet[:4] == EOBP:
               logger.warning('(%s) *ProtoControl* KF7EEL EOBP protocol not supported',self._system)
               return
           
            elif _packet[:4] == DMRE:
                
                if _packet[55] > 4:
                    _data = _packet[:53]
                    _ber = _packet[53:54]
                    _rssi = _packet[54:55]
                    _embedded_version  = _packet[55]
                    self._config['VER'] = _embedded_version
                    _timestamp = _packet[56:64]
                    _source_server = _packet[64:68]
                    _source_rptr = _packet[68:72]
                    _hops = _packet[72]
                    _hash = _packet[73:89]
                    #_ckhs = hmac_new(self._config['PASSPHRASE'],_data,sha1).digest()
                    _h = blake2b(key=self._config['PASSPHRASE'], digest_size=16)
                    _h.update(_packet[:73])
                else:
                    _data = _packet[:53]
                    _ber = _packet[53:54]
                    _rssi = _packet[54:55]
                    _embedded_version  = _packet[55]
                    self._config['VER'] = _embedded_version
                    _timestamp = _packet[56:64]
                    _source_server = _packet[64:68]
                    _source_rptr = b'\x00\x00\x00\x00'
                    _hops = _packet[68]
                    _hash = _packet[69:85]
                    #_ckhs = hmac_new(self._config['PASSPHRASE'],_data,sha1).digest()
                    _h = blake2b(key=self._config['PASSPHRASE'], digest_size=16)
                    _h.update(_packet[:69])
                    
                _ckhs = _h.digest()
                
                _stream_id = _data[16:20]

                if compare_digest(_hash, _ckhs) and (_sockaddr == self._config['TARGET_SOCK'] or self._config['RELAX_CHECKS']):
                    _peer_id = _data[11:15]
                    if self._config['NETWORK_ID'] != _peer_id:
                        if _stream_id not in self._laststrid:
                            logger.error('(%s) OpenBridge packet discarded because NETWORK_ID: %s Does not match sent Peer ID: %s', self._system, int_id(self._config['NETWORK_ID']), int_id(_peer_id))
                            self._laststrid.append(_stream_id)
                        return
                    _seq = _data[4]
                    _rf_src = _data[5:8]
                    _dst_id = _data[8:11]
                    _int_dst_id = int_id(_dst_id)
                    _bits = _data[15]
                    _slot = 2 if (_bits & 0x80) else 1
                    #_call_type = 'unit' if (_bits & 0x40) else 'group'
                    if _bits & 0x40:
                        _call_type = 'unit'
                    elif (_bits & 0x23) == 0x23:
                        _call_type = 'vcsbk'
                    else:
                        _call_type = 'group'
                    _frame_type = (_bits & 0x30) >> 4
                    _dtype_vseq = (_bits & 0xF) # data, 1=voice header, 2=voice terminator; voice, 0=burst A ... 5=burst F
                    #logger.debug('(%s) DMRD - Seqence: %s, RF Source: %s, Destination ID: %s', self._system, int_id(_seq), int_id(_rf_src), int_id(_dst_id))
                    
                    #Don't do anything if we are STUNned
                    if 'STUN' in self._CONFIG:
                            if _stream_id not in self._laststrid:
                                logger.warning('(%s) Bridge STUNned, discarding', self._system)
                                self._laststrid.append(_stream_id)
                            return
                        
                    #Discard old packets
                    if (int.from_bytes(_timestamp,'big')/1000000000) < (time() - 5):
                        if _stream_id not in self._laststrid:
                            logger.warning('(%s) Packet from server %s more than 5s old!, discarding',  self._system,int.from_bytes(_source_server,'big'))
                            self.send_bcsq(_dst_id,_stream_id)
                            self._laststrid.append(_stream_id)
                        return
                    
                    #Discard bad source server 
                    if ((len(str(int.from_bytes(_source_server,'big'))) < 4) or (len(str(int.from_bytes(_source_server,'big'))) > 7)):
                        if _stream_id not in self._laststrid:
                            logger.warning('(%s) Source Server should be  between 4 and 7 digits, discarding Src: %s', self._system, int.from_bytes(_source_server,'big'))
                            self.send_bcsq(_dst_id,_stream_id)
                            self._laststrid.append(_stream_id)
                        return
                    elif self._CONFIG['GLOBAL']['VALIDATE_SERVER_IDS'] and (len(str(int.from_bytes(_source_server,'big'))) == 4 or (len(str(int.from_bytes(_source_server,'big'))) == 5))  and ((str(int.from_bytes(_source_server,'big'))[:4]) not in self._CONFIG['_SERVER_IDS'] ):
                        if _stream_id not in self._laststrid:
                            logger.warning('(%s) Source Server ID is 4 or 5 digits but not in list: %s', self._system, int.from_bytes(_source_server,'big'))
                            self.send_bcsq(_dst_id,_stream_id)
                            self._laststrid.append(_stream_id)
                        return
                    elif len(str(int.from_bytes(_source_server,'big'))) > 5 and not self.validate_id(_source_server):
                        if _stream_id not in self._laststrid:
                            logger.warning('(%s) Source Server 6 or 7 digits but not a valid DMR ID, discarding Src: %s', self._system, int.from_bytes(_source_server,'big'))
                            self.send_bcsq(_dst_id,_stream_id)
                            self._laststrid.append(_stream_id)
                        return

                    #Increment max hops
                    _inthops = _hops +1 
                    
                    if _inthops > 10:
                        logger.warning('(%s) MAX HOPS exceed, dropping. Hops: %s, DST: %s, SRC: %s', self._system, _inthops, _int_dst_id, int.from_bytes(_source_server,'big'))
                        self.send_bcsq(_dst_id,_stream_id)
                        return
                    
                    
                    #Low-level TG filtering 
                    if _call_type != 'unit':
                        _int_dst_id = int_id(_dst_id)
                        if _int_dst_id <= 79 or (_int_dst_id >= 9990 and _int_dst_id <= 9999) or _int_dst_id == 900999:
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY GLOBAL TG FILTER', self._system, int_id(_stream_id), _int_dst_id)
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                    
                    # ACL Processing
                    if self._CONFIG['GLOBAL']['USE_ACL']:
                        if not acl_check(_rf_src, self._CONFIG['GLOBAL']['SUB_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                        if _slot == 1 and not acl_check(_dst_id, self._CONFIG['GLOBAL']['TG1_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                    if self._config['USE_ACL']:
                        if not acl_check(_rf_src, self._config['SUB_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY SYSTEM ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                        if not acl_check(_dst_id, self._config['TG1_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return

                    _data = b''.join([DMRD,_data[4:]])
                    
                    _hops = _inthops.to_bytes(1,'big')
                    # Userland actions -- typically this is the function you subclass for an application
                    self.dmrd_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data,_hash,_hops,_source_server,_ber,_rssi,_source_rptr)
                    #Silently treat a DMRD packet like a keepalive - this is because it's traffic and the 
                    #Other end may not have enabled ENAHNCED_OBP
                    self._config['_bcka'] = time()
                else:
                    h,p = _sockaddr
                    logger.warning('(%s) FreeBridge HMAC failed, packet discarded - OPCODE: %s DATA: %s HMAC LENGTH: %s HMAC: %s SRC IP: %s SRC PORT: %s', self._system, _packet[:4], repr(_packet[:69]), len(_packet[69:]), repr(_packet[61:]),h,p) 


            elif _packet[:4] == DMRF:
                _data = _packet[:53]
                _timestamp = _packet[53:60]
                _hops = _packet[61]
                _hash = _packet[62:]
                #_ckhs = hmac_new(self._config['PASSPHRASE'],_data,sha1).digest()
                _h = blake2b(key=self._config['PASSPHRASE'], digest_size=16)
                if 'VER' in self._config and self._config['VER'] > 2:
                    _h.update(_packet[:53])
                elif 'VER' in self._config and self._config['VER'] == 2:
                    _h.update(_packet[:61])
                    
                _ckhs = _h.digest()

                if compare_digest(_hash, _ckhs) and (_sockaddr == self._config['TARGET_SOCK'] or self._config['RELAX_CHECKS']):
                    _peer_id = _data[11:15]
                    if self._config['NETWORK_ID'] != _peer_id:
                        if _stream_id not in self._laststrid:
                            logger.error('(%s) OpenBridge packet discarded because NETWORK_ID: %s Does not match sent Peer ID: %s', self._system, int_id(self._config['NETWORK_ID']), int_id(_peer_id))
                            self._laststrid.append(_stream_id)
                        return
                    _seq = _data[4]
                    _rf_src = _data[5:8]
                    _dst_id = _data[8:11]
                    _int_dst_id = int_id(_dst_id)
                    _bits = _data[15]
                    _slot = 2 if (_bits & 0x80) else 1
                    #_call_type = 'unit' if (_bits & 0x40) else 'group'
                    if _bits & 0x40:
                        _call_type = 'unit'
                    elif (_bits & 0x23) == 0x23:
                        _call_type = 'vcsbk'
                    else:
                        _call_type = 'group'
                    _frame_type = (_bits & 0x30) >> 4
                    _dtype_vseq = (_bits & 0xF) # data, 1=voice header, 2=voice terminator; voice, 0=burst A ... 5=burst F
                    _stream_id = _data[16:20]
                    #logger.debug('(%s) DMRD - Seqence: %s, RF Source: %s, Destination ID: %s', self._system, int_id(_seq), int_id(_rf_src), int_id(_dst_id))
                    
                    #Don't do anything if we are STUNned
                    if 'STUN' in self._CONFIG:
                            if _stream_id not in self._laststrid:
                                logger.warning('(%s) Bridge STUNned, discarding', self._system)
                                self._laststrid.append(_stream_id)
                            return
                        
                    #Increment max hops
                    _inthops = _hops +1 
                    
                    if _inthops > 10:
                        logger.warning('(%s) MAX HOPS exceed, dropping. Hops: %s, DST: %s', self._system, _inthops, _int_dst_id)
                        self.send_bcsq(_dst_id,_stream_id)
                        return
                    
                    
                    #Low-level TG filtering 
                    if _call_type != 'unit':
                        _int_dst_id = int_id(_dst_id)
                        if _int_dst_id <= 79 or (_int_dst_id >= 9990 and _int_dst_id <= 9999) or _int_dst_id == 900999:
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY GLOBAL TG FILTER', self._system, int_id(_stream_id), _int_dst_id)
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                    
                    # ACL Processing
                    if self._CONFIG['GLOBAL']['USE_ACL']:
                        if not acl_check(_rf_src, self._CONFIG['GLOBAL']['SUB_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                        if _slot == 1 and not acl_check(_dst_id, self._CONFIG['GLOBAL']['TG1_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                    if self._config['USE_ACL']:
                        if not acl_check(_rf_src, self._config['SUB_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY SYSTEM ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                        if not acl_check(_dst_id, self._config['TG1_ACL']):
                            if _stream_id not in self._laststrid:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                                self.send_bcsq(_dst_id,_stream_id)
                                self._laststrid.append(_stream_id)
                            return
                

                    
                    #Remove timestamp from data. For now dmrd_received does not expect it
                    #Leaving it in screws up the AMBE data
                    #_data = b''.join([_data[:5],_data[12:]])
                    _data = b''.join([DMRD,_data[4:]])
                    
                    _hops = _inthops.to_bytes(1,'big')
                    # Userland actions -- typically this is the function you subclass for an application
                    self.dmrd_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data,_hash,_hops)
                    #Silently treat a DMRD packet like a keepalive - this is because it's traffic and the 
                    #Other end may not have enabled ENAHNCED_OBP
                    self._config['_bcka'] = time()
                else:
                    h,p = _sockaddr
                    logger.warning('(%s) FreeBridge HMAC failed, packet discarded - OPCODE: %s DATA: %s HMAC LENGTH: %s HMAC: %s SRC IP: %s SRC PORT: %s', self._system, _packet[:4], repr(_packet[:61]), len(_packet[61:]), repr(_packet[61:]),h,p) 

        if self._config['ENHANCED_OBP']:
            if _packet[:2] == BC:    # Bridge Control packet (Extended OBP)
                #Keep Alive
                if _packet[:4] == BCKA:
                    #_data = _packet[:53]
                    _hash = _packet[4:]
                    _ckhs = hmac_new(self._config['PASSPHRASE'],_packet[:4],sha1).digest()
                    if compare_digest(_hash, _ckhs):
                        logger.trace('(%s) *BridgeControl* Keep Alive received',self._system)
                        self._config['_bcka'] = time()
                        if _sockaddr != self._config['TARGET_SOCK']:
                            h,p =  _sockaddr
                            logger.info('(%s) *BridgeControl* Source IP and Port has changed for OBP from %s:%s to %s:%s,  updating',self._system,self._config['TARGET_IP'],self._config['TARGET_PORT'],h,p)
                            self._config['TARGET_IP'] = h
                            self._config['TARGET_PORT'] = p
                            self._config['TARGET_SOCK'] = (h,p)
                                
                    else:
                        h,p = _sockaddr
                        logger.info('(%s) *BridgeControl* BCKA invalid KeepAlive, packet discarded - OPCODE: %s DATA: %s HMAC LENGTH: %s HMAC: %s SRC IP: %s SRC PORT: %s', self._system, _packet[:4], repr(_packet[:53]), len(_packet[53:]), repr(_packet[53:]),h,p) 
                #Source Quench
                if _packet[:4] == BCSQ:
                    #_data = _packet[:11]
                    _hash = _packet[11:]
                    _tgid = _packet[4:7]
                    _stream_id = _packet[7:11]
                    _ckhs = hmac_new(self._config['PASSPHRASE'],_packet[:11],sha1).digest()
                    if compare_digest(_hash, _ckhs):
                        logger.trace('(%s) *BridgeControl*  BCSQ Source Quench request received for TGID: %s, Stream ID: %s',self._system,int_id(_tgid), int_id(_stream_id))
                        if '_bcsq' not in self._config:
                            self._config['_bcsq'] = {}
                        self._config['_bcsq'][_tgid] = _stream_id
                    else:
                        h,p = _sockaddr
                        logger.warning('(%s) *BridgeControl* BCSQ invalid Source Quench, packet discarded - OPCODE: %s DATA: %s HMAC LENGTH: %s HMAC: %s SRC IP: %s SRC PORT: %s', self._system, _packet[:4], repr(_packet[:53]), len(_packet[53:]), repr(_packet[53:]),h,p)  
                #Stun
                if _packet[:4] == BCST:
                    #_data = _packet[:11]
                    _hash = _packet[4:]
                    _ckhs = hmac_new(self._config['PASSPHRASE'],_packet[4:],sha1).digest()
                    if compare_digest(_hash, _ckhs):
                        logger.trace('(%s) *BridgeControl*  BCST STUN request received for TGID: %s, Stream ID: %s',self._system,int_id(_tgid), int_id(_stream_id))
                        self._config['_STUN'] = True
                    else:
                        h,p = _sockaddr
                        logger.warning('(%s) *BridgeControl* BCST invalid STUN, packet discarded - OPCODE: %s DATA: %s HMAC LENGTH: %s HMAC: %s SRC IP: %s SRC PORT: %s', self._system, _packet[:4], repr(_packet[:53]), len(_packet[53:]), repr(_packet[53:]),h,p) 
                #Version
                if _packet[:4] == BCVE:
                    #_data = _packet[:11]
                    _ver = int.from_bytes(_packet[4:5],'big')
                    _hash = _packet[5:]
                    _ckhs = hmac_new(self._config['PASSPHRASE'],_packet[4:5],sha1).digest()
                    if compare_digest(_hash, _ckhs):
                        logger.trace('(%s) *ProtoControl*  BCVE Version received, Ver: %s',self._system,_ver)
                        
                        if _ver > self._config['VER']:
                            logger.info('(%s) *ProtoControl*  BCVE Version upgrade, Ver: %s',self._system,_ver)
                            self._config['VER'] = _ver
                        elif _ver == self._config['VER']:
                            pass
                        else:
                            logger.warning('(%s) *ProtoControl*  BCVE Version downgrade not allowed, Ver: %s',self._system,_ver)
                        
                    else:
                        h,p = _sockaddr
                        logger.warning('(%s) *ProtoControl* BCVE invalid, packet discarded - OPCODE: %s DATA: %s HMAC LENGTH: %s HMAC: %s SRC IP: %s SRC PORT: %s', self._system, _packet[:4], repr(_packet[:53]), len(_packet[53:]), repr(_packet[53:]),h,p) 
                
                
                
                    
      
#************************************************
#     HB MASTER CLASS
#************************************************

class HBSYSTEM(DatagramProtocol):
    def __init__(self, _name, _config, _report):
        # Define a few shortcuts to make the rest of the class more readable
        self._CONFIG = _config
        self._system = _name
        self._report = _report
        self._config = self._CONFIG['SYSTEMS'][self._system]
        self._laststrid = {1: b'', 2: b''}
        

        # Define shortcuts and generic function names based on the type of system we are
        if self._config['MODE'] == 'MASTER':
            self._peers = self._CONFIG['SYSTEMS'][self._system]['PEERS']
            self.send_system = self.send_peers
            self.maintenance_loop = self.master_maintenance_loop
            self.datagramReceived = self.master_datagramReceived
            self.dereg = self.master_dereg


        elif self._config['MODE'] == 'PEER':
            self._stats = self._config['STATS']
            self._stats['DNS_TIME'] = time()
            self.send_system = self.send_master
            self.maintenance_loop = self.peer_maintenance_loop
            self.datagramReceived = self.peer_datagramReceived
            self.dereg = self.peer_dereg

        elif self._config['MODE'] == 'XLXPEER':
            self._stats = self._config['XLXSTATS']
            self._stats['DNS_TIME'] = time()
            self.send_system = self.send_master
            self.maintenance_loop = self.peer_maintenance_loop
            self.datagramReceived = self.peer_datagramReceived
            self.dereg = self.peer_dereg
            
    def loopingErrHandle(self,failure):
        logger.error('(GLOBAL - hblink.py) Unhandled error in timed loop.\n %s', failure)

    def startProtocol(self):
        # Set up periodic loop for tracking pings from peers. Run every 'PING_TIME' seconds
        self._system_maintenance = task.LoopingCall(self.maintenance_loop)
        self._system_maintenance_loop = self._system_maintenance.start(self._CONFIG['GLOBAL']['PING_TIME'])
        self._system_maintenance_loop.addErrback(self.loopingErrHandle)


    # Aliased in __init__ to maintenance_loop if system is a master
    def master_maintenance_loop(self):
        logger.debug('(%s) Master maintenance loop started', self._system)
        remove_list = deque()
        for peer in self._peers:
            _this_peer = self._peers[peer]
            # Check to see if any of the peers have been quiet (no ping) longer than allowed
            if _this_peer['LAST_PING']+(self._CONFIG['GLOBAL']['PING_TIME']*self._CONFIG['GLOBAL']['MAX_MISSED']) < time():
                remove_list.append(peer)
        for peer in remove_list:
            logger.info('(%s) Peer %s (%s) has timed out and is being removed', self._system, self._peers[peer]['CALLSIGN'], self._peers[peer]['RADIO_ID'])
            #First, MSTCL the peer
            self.transport.write(b''.join([MSTCL, peer]),self._CONFIG['SYSTEMS'][self._system]['PEERS'][peer]['SOCKADDR'])
            # Remove any timed out peers from the configuration
            del self._CONFIG['SYSTEMS'][self._system]['PEERS'][peer]
        if 'PEERS' not in self._CONFIG['SYSTEMS'][self._system] and 'OPTIONS' in self._CONFIG['SYSTEMS'][self._system]:
            
            if '_default_options' in self._CONFIG['SYSTEMS'][self._system]:
                logger.info('(%s) Setting default Options: %s',self._system, self._CONFIG['SYSTEMS'][self._system]['_default_options'])
                self._CONFIG['SYSTEMS'][self._system]['OPTIONS'] = self._CONFIG['SYSTEMS'][self._system]['_default_options']
                self._CONFIG['SYSTEMS'][self._system]['_reset'] = True
            else:
                del self._CONFIG['SYSTEMS'][self._system]['OPTIONS']
                w
                logger.info('(%s) Deleting HBP Options',self._system)

    # Aliased in __init__ to maintenance_loop if system is a peer
    def peer_maintenance_loop(self):
        logger.debug('(%s) Peer maintenance loop started', self._system)
        if self._stats['PING_OUTSTANDING']:
            self._stats['NUM_OUTSTANDING'] += 1
        # If we're not connected, zero out the stats and send a login request RPTL
        if self._stats['CONNECTION'] != 'YES' or self._stats['NUM_OUTSTANDING'] >= self._CONFIG['GLOBAL']['MAX_MISSED']:
            self._stats['PINGS_SENT'] = 0
            self._stats['PINGS_ACKD'] = 0
            self._stats['NUM_OUTSTANDING'] = 0
            self._stats['PING_OUTSTANDING'] = False
            self._stats['CONNECTION'] = 'RPTL_SENT'
            if self._stats['DNS_TIME'] < (time() - 600):
                self._stats['DNS_TIME'] = time()
                _d = reactor.resolve(self._config['_MASTER_IP'])
                _d.addCallback(self.updateSockaddr)
                _d.addErrback(self.updateSockaddr_errback)
            self.send_master(b''.join([RPTL, self._config['RADIO_ID']]))
            logger.info('(%s) Sending login request to master %s:%s', self._system, self._config['MASTER_IP'], self._config['MASTER_PORT'])
        # If we are connected, sent a ping to the master and increment the counter
        if self._stats['CONNECTION'] == 'YES':
            self.send_master(b''.join([RPTPING, self._config['RADIO_ID']]))
            logger.trace('(%s) RPTPING Sent to Master. Total Sent: %s, Total Missed: %s, Currently Outstanding: %s', self._system, self._stats['PINGS_SENT'], self._stats['PINGS_SENT'] - self._stats['PINGS_ACKD'], self._stats['NUM_OUTSTANDING'])
            self._stats['PINGS_SENT'] += 1
            self._stats['PING_OUTSTANDING'] = True
            
    def updateSockaddr(self,ip):
        self._config['MASTER_IP'] = ip
        self._config['MASTER_SOCKADDR'] = (ip, self._config['MASTER_PORT'])
        logger.info('(%s) hostname resolution performed: %s',self._system,ip)
        
    def updateSockaddr_errback(self,failure):
        logger.info('(%s) hostname resolution error: %s',self._system,failure)

    def send_peers(self, _packet, _hops = b'', _ber = b'\x00', _rssi = b'\x00',_source_server = b'\x00\x00\x00\x00', _source_rptr = b'\x00\x00\x00\x00'):
        for _peer in self._peers:
            if len(_packet) < 54:
                _packet =b''.join([_packet,_ber,_rssi])
            self.send_peer(_peer, _packet)
            #logger.debug('(%s) Packet sent to peer %s', self._system, self._peers[_peer]['RADIO_ID'])

    def send_peer(self, _peer, _packet):
        if _packet[:4] == DMRD:
            _packet = b''.join([_packet[:11], _peer, _packet[15:]])
        self.transport.write(_packet, self._peers[_peer]['SOCKADDR'])
        # KEEP THE FOLLOWING COMMENTED OUT UNLESS YOU'RE DEBUGGING DEEPLY!!!!
        #logger.debug('(%s) TX Packet to %s on port %s: %s', self._peers[_peer]['RADIO_ID'], self._peers[_peer]['IP'], self._peers[_peer]['PORT'], ahex(_packet))

    def send_master(self, _packet, _hops = b'', _ber = b'\x00', _rssi = b'\x00',_source_server = b'\x00\x00\x00\x00',source_rptr = b'\x00\x00\x00\x00'):
        if _packet[:4] == DMRD:
            if len(_packet) < 54:
                _packet = b''.join([_packet[:11], self._config['RADIO_ID'], _packet[15:],_ber,_rssi])
            else:
                _packet = b''.join([_packet[:11], self._config['RADIO_ID'], _packet[15:]])
            
        self.transport.write(_packet, self._config['MASTER_SOCKADDR'])
        # KEEP THE FOLLOWING COMMENTED OUT UNLESS YOU'RE DEBUGGING DEEPLY!!!!
        #logger.debug('(%s) TX Packet to %s:%s -- %s', self._system, self._config['MASTER_IP'], self._config['MASTER_PORT'], ahex(_packet))

    def send_xlxmaster(self, radio, xlx, mastersock):
        radio3 = int.from_bytes(radio, 'big').to_bytes(3, 'big')
        radio4 = int.from_bytes(radio, 'big').to_bytes(4, 'big')
        xlx3   = xlx.to_bytes(3, 'big')
        streamid = randint(0,255).to_bytes(1, 'big')+randint(0,255).to_bytes(1, 'big')+randint(0,255).to_bytes(1, 'big')+randint(0,255).to_bytes(1, 'big')
        # Wait for .5 secs for the XLX to log us in
        for packetnr in range(5):
            if packetnr < 3:
                # First 3 packets, voice start, stream type e1
                strmtype = 225
                payload = bytearray.fromhex('4f2e00b501ae3a001c40a0c1cc7dff57d75df5d5065026f82880bd616f13f185890000')
            else:
                # Last 2 packets, voice end, stream type e2
                strmtype = 226
                payload = bytearray.fromhex('4f410061011e3a781c30a061ccbdff57d75df5d2534425c02fe0b1216713e885ba0000')
            packetnr1 = packetnr.to_bytes(1, 'big')
            strmtype1 = strmtype.to_bytes(1, 'big')
            _packet = b''.join([DMRD, packetnr1, radio3, xlx3, radio4, strmtype1, streamid, payload])
            self.transport.write(_packet, mastersock)
            # KEEP THE FOLLOWING COMMENTED OUT UNLESS YOU'RE DEBUGGING DEEPLY!!!!
            #logger.debug('(%s) XLX Module Change Packet: %s', self._system, ahex(_packet))
        return

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):
        pass

    def master_dereg(self):
        for _peer in self._peers:
            self.send_peer(_peer, b''.join([MSTCL,_peer]))
            logger.info('(%s) De-Registration sent to Peer: %s (%s)', self._system, self._peers[_peer]['CALLSIGN'], self._peers[_peer]['RADIO_ID'])

    def peer_dereg(self):
        self.send_master(b''.join([RPTCL,self._config['RADIO_ID']]))
        logger.info('(%s) De-Registration sent to Master: %s:%s', self._system, self._config['MASTER_SOCKADDR'][0], self._config['MASTER_SOCKADDR'][1])
        
    def proxy_IPBlackList(self,peer_id,sockaddr):
        _timenow = time()
        _bltime = _timenow + 300
        _bltime = str(_bltime)
        _prpacket = b''.join([PRBL,peer_id,_bltime.encode('UTF-8')])
        self.transport.write(_prpacket,sockaddr)
        
    def validate_id(self,_peer_id):
        
        if 'ALLOW_UNREG_ID' not in self._config:
            return True
        
        if 'ALLOW_UNREG_ID' in self._config and self._config['ALLOW_UNREG_ID']:
            return True
        
        _int_peer_id = int_id(_peer_id)
        _int_peer_id = str(_int_peer_id)[:7]
        _int_peer_id = int(_int_peer_id)
        _subscriber_ids = self._CONFIG['_SUB_IDS']
        _peer_ids = self._CONFIG['_PEER_IDS']
        _local_subscriber_ids = self._CONFIG['_LOCAL_SUBSCRIBER_IDS']
        
        if _int_peer_id in _local_subscriber_ids:
            return _local_subscriber_ids[_int_peer_id]
        elif _int_peer_id in _subscriber_ids:
            return _subscriber_ids[_int_peer_id]
        elif _int_peer_id in _peer_ids:
            return _peer_ids[_int_peer_id]
        else:
            return False
        

    # Aliased in __init__ to datagramReceived if system is a master
    def master_datagramReceived(self, _data, _sockaddr):
        # Keep This Line Commented Unless HEAVILY Debugging!
        # logger.debug('(%s) RX packet from %s -- %s', self._system, _sockaddr, ahex(_data))

        # Extract the command, which is various length, all but one 4 significant characters -- RPTCL
        _command = _data[:4]

        if _command == DMRD:    # DMRData -- encapsulated DMR data frame
            _peer_id = _data[11:15]
            if _peer_id in self._peers \
                        and self._peers[_peer_id]['CONNECTION'] == 'YES' \
                        and self._peers[_peer_id]['SOCKADDR'] == _sockaddr:
                _seq = _data[4]
                _rf_src = _data[5:8]
                _dst_id = _data[8:11]
                _bits = _data[15]
                _slot = 2 if (_bits & 0x80) else 1
                #_call_type = 'unit' if (_bits & 0x40) else 'group'
                if _bits & 0x40:
                    _call_type = 'unit'
                elif (_bits & 0x23) == 0x23:
                    _call_type = 'vcsbk'
                else:
                    _call_type = 'group'
                _frame_type = (_bits & 0x30) >> 4
                _dtype_vseq = (_bits & 0xF) # data, 1=voice header, 2=voice terminator; voice, 0=burst A ... 5=burst F
                _stream_id = _data[16:20]
                if not int_id(_stream_id):
                    logger.warning('(%s) CALL DROPPED AS STREAM ID IS NULL FROM SUBSCRIBER %s', self._system, int_id(_rf_src))
                    return
                #logger.debug('(%s) DMRD - Seqence: %s, RF Source: %s, Destination ID: %s', self._system, _seq, int_id(_rf_src), int_id(_dst_id))
                # ACL Processing
                if self._CONFIG['GLOBAL']['USE_ACL']:
                    if not acl_check(_rf_src, self._CONFIG['GLOBAL']['SUB_ACL']):
                        if self._laststrid[_slot] != _stream_id:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY GLOBAL ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                            self._laststrid[_slot] = _stream_id
                        return
                    if _slot == 1 and not acl_check(_dst_id, self._CONFIG['GLOBAL']['TG1_ACL']):
                        if self._laststrid[_slot] != _stream_id:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                            self._laststrid[_slot] = _stream_id
                        return
                    if _slot == 2 and not acl_check(_dst_id, self._CONFIG['GLOBAL']['TG2_ACL']):
                        if self._laststrid[_slot] != _stream_id:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS2 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                            self._laststrid[_slot] = _stream_id
                        return
                if self._config['USE_ACL']:
                    if not acl_check(_rf_src, self._config['SUB_ACL']):
                        if self._laststrid[_slot] != _stream_id:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY SYSTEM ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                            self._laststrid[_slot] = _stream_id
                        return
                    if _slot == 1 and not acl_check(_dst_id, self._config['TG1_ACL']):
                        if self._laststrid[_slot] != _stream_id:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM TS1 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                            self._laststrid[_slot] = _stream_id
                        return
                    if _slot == 2 and not acl_check(_dst_id, self._config['TG2_ACL']):
                        if self._laststrid[_slot]!= _stream_id:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM TS2 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                            self._laststrid[_slot] = _stream_id
                        return

                # The basic purpose of a master is to repeat to the peers
                if self._config['REPEAT'] == True:
                    pkt = [_data[:11], '', _data[15:]]
                    for _peer in self._peers:
                        if _peer != _peer_id:
                            pkt[1] = _peer
                            self.transport.write(b''.join(pkt), self._peers[_peer]['SOCKADDR'])
                            #logger.debug('(%s) Packet on TS%s from %s (%s) for destination ID %s repeated to peer: %s (%s) [Stream ID: %s]', self._system, _slot, self._peers[_peer_id]['CALLSIGN'], int_id(_peer_id), int_id(_dst_id), self._peers[_peer]['CALLSIGN'], int_id(_peer), int_id(_stream_id))


                # Userland actions -- typically this is the function you subclass for an application
                self.dmrd_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data)

        elif _command == RPTL:    # RPTLogin -- a repeater wants to login
            _peer_id = _data[4:8]
            # Check to see if we've reached the maximum number of allowed peers
            if len(self._peers) < self._config['MAX_PEERS'] or _peer_id in self._peers:
                # Check for valid Radio ID
                if _peer_id == b'\xff\xff\xff\xff' or (acl_check(_peer_id, self._CONFIG['GLOBAL']['REG_ACL']) and acl_check(_peer_id, self._config['REG_ACL']) and self.validate_id(_peer_id)):
                    # Build the configuration data strcuture for the peer
                    self._peers.update({_peer_id: {
                        'CONNECTION': 'RPTL-RECEIVED',
                        'CONNECTED': time(),
                        'PINGS_RECEIVED': 0,
                        'LAST_PING': time(),
                        'SOCKADDR': _sockaddr,
                        'IP': _sockaddr[0],
                        'PORT': _sockaddr[1],
                        'SALT': randint(0,0xFFFFFFFF),
                        'RADIO_ID': str(int(ahex(_peer_id), 16)),
                        'CALLSIGN': '',
                        'RX_FREQ': '',
                        'TX_FREQ': '',
                        'TX_POWER': '',
                        'COLORCODE': '',
                        'LATITUDE': '',
                        'LONGITUDE': '',
                        'HEIGHT': '',
                        'LOCATION': '',
                        'DESCRIPTION': '',
                        'SLOTS': '',
                        'URL': '',
                        'SOFTWARE_ID': '',
                        'PACKAGE_ID': '',
                    }})
                    if _peer_id == b'\xff\xff\xff\xff':
                        logger.info('(%s) Server Status Probe Logging in with Radio ID: %s, %s:%s', self._system, int_id(_peer_id), _sockaddr[0], _sockaddr[1])
                    else:
                        logger.info('(%s) Repeater Logging in with Radio ID: %s, %s:%s', self._system, int_id(_peer_id), _sockaddr[0], _sockaddr[1])
                    _salt_str = bytes_4(self._peers[_peer_id]['SALT'])
                    self.send_peer(_peer_id, b''.join([RPTACK, _salt_str]))
                    self._peers[_peer_id]['CONNECTION'] = 'CHALLENGE_SENT'
                    logger.info('(%s) Sent Challenge Response to %s for login: %s', self._system, int_id(_peer_id), self._peers[_peer_id]['SALT'])
                else:
                    self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    if self._config['PROXY_CONTROL']:
                        self.proxy_IPBlackList(_peer_id,_sockaddr)
                    logger.warning('(%s) Invalid Login from %s Radio ID: %s Denied by Registation ACL or not registered ID', self._system, _sockaddr[0], int_id(_peer_id))
            else:
                self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                logger.warning('(%s) Registration denied from Radio ID: %s Maximum number of peers exceeded', self._system, int_id(_peer_id))

        elif _command == RPTK:    # Repeater has answered our login challenge
            _peer_id = _data[4:8]
            if _peer_id in self._peers \
                        and self._peers[_peer_id]['CONNECTION'] == 'CHALLENGE_SENT' \
                        and self._peers[_peer_id]['SOCKADDR'] == _sockaddr:
                _this_peer = self._peers[_peer_id]
                _this_peer['LAST_PING'] = time()
                _sent_hash = _data[8:]
                _salt_str = bytes_4(_this_peer['SALT'])
                if self._CONFIG['GLOBAL']['ALLOW_NULL_PASSPHRASE'] and len(self._config['PASSPHRASE']) == 0:
                    _this_peer['CONNECTION'] = 'WAITING_CONFIG'
                    self.send_peer(_peer_id, b''.join([RPTACK, _peer_id]))
                    logger.info('(%s) Peer %s has completed the login exchange successfully', self._system, _this_peer['RADIO_ID'])
                else:
                    _calc_hash = bhex(sha256(_salt_str+self._config['PASSPHRASE']).hexdigest())                
                    if _sent_hash == _calc_hash:
                        _this_peer['CONNECTION'] = 'WAITING_CONFIG'
                        self.send_peer(_peer_id, b''.join([RPTACK, _peer_id]))
                        logger.info('(%s) Peer %s has completed the login exchange successfully', self._system, _this_peer['RADIO_ID'])
                    else:
                        logger.info('(%s) Peer %s has FAILED the login exchange successfully', self._system, _this_peer['RADIO_ID'])
                        self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                        del self._peers[_peer_id]
            else:
                self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                logger.info('(%s) Login challenge from Radio ID that has not logged in: %s', self._system, int_id(_peer_id))

        elif _command == RPTC:    # Repeater is sending it's configuraiton OR disconnecting
            if _data[:5] == RPTCL:    # Disconnect command
                _peer_id = _data[5:9]
                if _peer_id in self._peers \
                            and self._peers[_peer_id]['CONNECTION'] == 'YES' \
                            and self._peers[_peer_id]['SOCKADDR'] == _sockaddr:
                    logger.info('(%s) Peer is closing down: %s (%s)', self._system, self._peers[_peer_id]['CALLSIGN'], int_id(_peer_id))
                    self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    del self._peers[_peer_id]
                    if 'OPTIONS' in self._CONFIG['SYSTEMS'][self._system]:
                        if '_default_options' in self._CONFIG['SYSTEMS'][self._system]:
                            self._CONFIG['SYSTEMS'][self._system]['OPTIONS'] = self._CONFIG['SYSTEMS'][self._system]['_default_options']
                            logger.info('(%s) Setting default Options: %s',self._system, self._CONFIG['SYSTEMS'][self._system]['_default_options'])
                            self._CONFIG['SYSTEMS'][self._system]['_reset'] = True
                        else:
                            logger.info('(%s) Deleting HBP Options',self._system)
                            del self._CONFIG['SYSTEMS'][self._system]['OPTIONS']
                            self._CONFIG['SYSTEMS'][self._system]['_reset'] = True
                    
            else:
                _peer_id = _data[4:8]      # Configure Command
                if _peer_id in self._peers \
                            and self._peers[_peer_id]['CONNECTION'] == 'WAITING_CONFIG' \
                            and self._peers[_peer_id]['SOCKADDR'] == _sockaddr:
                    _this_peer = self._peers[_peer_id]
                    _this_peer['CONNECTION'] = 'YES'
                    _this_peer['CONNECTED'] = time()
                    _this_peer['LAST_PING'] = time()
                    _this_peer['CALLSIGN'] = _data[8:16]
                    _this_peer['RX_FREQ'] = _data[16:25]
                    _this_peer['TX_FREQ'] =  _data[25:34]
                    _this_peer['TX_POWER'] = _data[34:36]
                    _this_peer['COLORCODE'] = _data[36:38]
                    _this_peer['LATITUDE'] = _data[38:46]
                    _this_peer['LONGITUDE'] = _data[46:55]
                    _this_peer['HEIGHT'] = _data[55:58]
                    _this_peer['LOCATION'] = _data[58:78]
                    _this_peer['DESCRIPTION'] = _data[78:97]
                    _this_peer['SLOTS'] = _data[97:98]
                    _this_peer['URL'] = _data[98:222]
                    _this_peer['SOFTWARE_ID'] = _data[222:262]
                    _this_peer['PACKAGE_ID'] = _data[262:302]
                    
                    if ('ALLOW_UNREG_ID' in self._config and not self._config['ALLOW_UNREG_ID']) and _this_peer['CALLSIGN'].decode('utf8').rstrip() != self.validate_id(_peer_id):
                        del self._peers[_peer_id]
                        if self._config['PROXY_CONTROL']:
                            self.proxy_IPBlackList(_peer_id,_sockaddr)
                        self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                        logger.info('(%s) Callsign does not match subscriber database: ID: %s, Sent Call: %s, DB call %s', self._system, int_id(_peer_id),_this_peer['CALLSIGN'].decode('utf8').rstrip(),self.validate_id(_peer_id))
                    else:
                        self.send_peer(_peer_id, b''.join([RPTACK, _peer_id]))
                        logger.info('(%s) Peer %s (%s) has sent repeater configuration, Package ID: %s, Software ID: %s, Desc: %s', self._system, _this_peer['CALLSIGN'], _this_peer['RADIO_ID'],self._peers[_peer_id]['PACKAGE_ID'].decode().rstrip(),self._peers[_peer_id]['SOFTWARE_ID'].decode().rstrip(),self._peers[_peer_id]['DESCRIPTION'].decode().rstrip())
                else:
                    self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    logger.info('(%s) Peer info from Radio ID that has not logged in: %s', self._system, int_id(_peer_id))

        elif _command == RPTO:
            _peer_id = _data[4:8]
            if _peer_id in self._peers and self._peers[_peer_id]['SOCKADDR'] == _sockaddr:
                _this_peer = self._peers[_peer_id]
                _this_peer['OPTIONS'] = _data[8:]
                self.send_peer(_peer_id, b''.join([RPTACK, _peer_id]))
                logger.info('(%s) Peer %s has sent options %s', self._system, _this_peer['CALLSIGN'], _this_peer['OPTIONS'])
                self._CONFIG['SYSTEMS'][self._system]['OPTIONS'] = _this_peer['OPTIONS'].decode()
            else:
                self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                logger.info('(%s) Options from Radio ID that is not logged: %s', self._system, int_id(_peer_id))
        
        
        elif _command == RPTP:    # RPTPing -- peer is pinging us
                _peer_id = _data[7:11]
                if _peer_id in self._peers \
                            and self._peers[_peer_id]['CONNECTION'] == "YES" \
                            and self._peers[_peer_id]['SOCKADDR'] == _sockaddr:
                    self._peers[_peer_id]['PINGS_RECEIVED'] += 1
                    self._peers[_peer_id]['LAST_PING'] = time()
                    self.send_peer(_peer_id, b''.join([MSTPONG, _peer_id]))
                    logger.trace('(%s) Received and answered RPTPING from peer %s (%s)', self._system, self._peers[_peer_id]['CALLSIGN'], int_id(_peer_id))
                else:
                    self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    logger.info('(%s) Ping from Radio ID that is not logged in: %s', self._system, int_id(_peer_id))
        
        elif _command == DMRA:
                _peer_id = _data[4:8]
                logger.debug('(%s) Peer has sent Talker Alias packet %s', self._system, _data)
                
        elif _command == PRIN:
                logger.info('(%s) *ProxyInfo* Connection from IP:Port: %s', self._system, _data.decode('utf8')[4:])

        else:
            logger.error('(%s) Unrecognized command. Raw HBP PDU: %s', self._system, _data)

    # Aliased in __init__ to datagramReceived if system is a peer
    def peer_datagramReceived(self, _data, _sockaddr):
        # Keep This Line Commented Unless HEAVILY Debugging!
        # logger.debug('(%s) RX packet from %s -- %s', self._system, _sockaddr, ahex(_data))

        # Validate that we receveived this packet from the master - security check!
        if self._config['MASTER_SOCKADDR'] == _sockaddr:
            # Extract the command, which is various length, but only 4 significant characters
            _command = _data[:4]
            if   _command == DMRD:    # DMRData -- encapsulated DMR data frame

                _peer_id = _data[11:15]
                if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                    #_seq = _data[4:5]
                    _seq = _data[4]
                    _rf_src = _data[5:8]
                    _dst_id = _data[8:11]
                    _bits = _data[15]
                    _slot = 2 if (_bits & 0x80) else 1
                    #_call_type = 'unit' if (_bits & 0x40) else 'group'
                    if _bits & 0x40:
                        _call_type = 'unit'
                    elif (_bits & 0x23) == 0x23:
                        _call_type = 'vcsbk'
                    else:
                        _call_type = 'group'
                    _frame_type = (_bits & 0x30) >> 4
                    _dtype_vseq = (_bits & 0xF) # data, 1=voice header, 2=voice terminator; voice, 0=burst A ... 5=burst F
                    _stream_id = _data[16:20]
                    if not int_id(_stream_id):
                        logger.warning('(%s) CALL DROPPED AS STREAM ID IS NULL FROM SUBSCRIBER %s', self._system, int_id(_rf_src))
                        return
                    #logger.debug('(%s) DMRD - Sequence: %s, RF Source: %s, Destination ID: %s', self._system, int_id(_seq), int_id(_rf_src), int_id(_dst_id))

                    # ACL Processing
                    if self._CONFIG['GLOBAL']['USE_ACL']:
                        if not acl_check(_rf_src, self._CONFIG['GLOBAL']['SUB_ACL']):
                            if self._laststrid[_slot] != _stream_id:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY GLOBAL ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                                self._laststrid[_slot] = _stream_id
                            return
                        if _slot == 1 and not acl_check(_dst_id, self._CONFIG['GLOBAL']['TG1_ACL']):
                            if self._laststrid[_slot] != _stream_id:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                                self._laststrid[_slot] = _stream_id
                            return
                        if _slot == 2 and not acl_check(_dst_id, self._CONFIG['GLOBAL']['TG2_ACL']):
                            if self._laststrid[_slot] != _stream_id:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS2 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                                self._laststrid[_slot] = _stream_id
                            return
                    if self._config['USE_ACL']:
                        if not acl_check(_rf_src, self._config['SUB_ACL']):
                            if self._laststrid[_slot] != _stream_id:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY SYSTEM ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                                self._laststrid[_slot] = _stream_id
                            return
                        if _slot == 1 and not acl_check(_dst_id, self._config['TG1_ACL']):
                            if self._laststrid[_slot] != _stream_id:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM TS1 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                                self._laststrid[_slot] = _stream_id
                            return
                        if _slot == 2 and not acl_check(_dst_id, self._config['TG2_ACL']):
                            if self._laststrid[_slot] != _stream_id:
                                logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM TS2 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                                self._laststrid[_slot] = _stream_id
                            return


                    # Userland actions -- typically this is the function you subclass for an application
                    self.dmrd_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data)

            elif _command == MSTN:    # Actually MSTNAK -- a NACK from the master
                _peer_id = _data[6:10]
                if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                    logger.warning('(%s) MSTNAK Received. Resetting connection to the Master.', self._system)
                    self._stats['CONNECTION'] = 'NO' # Disconnect ourselves and re-register
                    self._stats['CONNECTED'] = time()

            elif _command == RPTA:    # Actually RPTACK -- an ACK from the master
                # Depending on the state, an RPTACK means different things, in each clause, we check and/or set the state
                if self._stats['CONNECTION'] == 'RPTL_SENT': # If we've sent a login request...
                    _login_int32 = _data[6:10]
                    logger.info('(%s) Repeater Login ACK Received with 32bit ID: %s', self._system, int_id(_login_int32))
                    _pass_hash = sha256(b''.join([_login_int32, self._config['PASSPHRASE']])).hexdigest()
                    _pass_hash = bhex(_pass_hash)
                    self.send_master(b''.join([RPTK, self._config['RADIO_ID'], _pass_hash]))
                    self._stats['CONNECTION'] = 'AUTHENTICATED'

                elif self._stats['CONNECTION'] == 'AUTHENTICATED': # If we've sent the login challenge...
                    _peer_id = _data[6:10]
                    if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                        logger.info('(%s) Repeater Authentication Accepted', self._system)
                        _config_packet =  b''.join([\
                                              self._config['RADIO_ID'],\
                                              self._config['CALLSIGN'],\
                                              self._config['RX_FREQ'],\
                                              self._config['TX_FREQ'],\
                                              self._config['TX_POWER'],\
                                              self._config['COLORCODE'],\
                                              self._config['LATITUDE'],\
                                              self._config['LONGITUDE'],\
                                              self._config['HEIGHT'],\
                                              self._config['LOCATION'],\
                                              self._config['DESCRIPTION'],\
                                              self._config['SLOTS'],\
                                              self._config['URL'],\
                                              self._config['SOFTWARE_ID'],\
                                              self._config['PACKAGE_ID']\
                                          ])

                        self.send_master(b''.join([RPTC, _config_packet]))
                        self._stats['CONNECTION'] = 'CONFIG-SENT'
                        logger.info('(%s) Repeater Configuration Sent', self._system)
                    else:
                        self._stats['CONNECTION'] = 'NO'
                        logger.error('(%s) Master ACK Contained wrong ID - Connection Reset', self._system)

                elif self._stats['CONNECTION'] == 'CONFIG-SENT': # If we've sent out configuration to the master
                    _peer_id = _data[6:10]
                    if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                        logger.info('(%s) Repeater Configuration Accepted', self._system)
                        if self._config['OPTIONS']:
                            self.send_master(b''.join([RPTO, self._config['RADIO_ID'], self._config['OPTIONS']]))
                            self._stats['CONNECTION'] = 'OPTIONS-SENT'
                            logger.info('(%s) Sent options: (%s)', self._system, self._config['OPTIONS'])
                        else:
                            self._stats['CONNECTION'] = 'YES'
                            self._stats['CONNECTED'] = time()
                            logger.info('(%s) Connection to Master Completed', self._system)
                            # If we are an XLX, send the XLX module request here.
                            if self._config['MODE'] == 'XLXPEER':
                                self.send_xlxmaster(self._config['RADIO_ID'], int(4000), self._config['MASTER_SOCKADDR'])
                                self.send_xlxmaster(self._config['RADIO_ID'], self._config['XLXMODULE'], self._config['MASTER_SOCKADDR'])
                                logger.info('(%s) Sending XLX Module request', self._system)
                    else:
                        self._stats['CONNECTION'] = 'NO'
                        logger.error('(%s) Master ACK Contained wrong ID - Connection Reset', self._system)

                elif self._stats['CONNECTION'] == 'OPTIONS-SENT': # If we've sent out options to the master
                    _peer_id = _data[6:10]
                    if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                        logger.info('(%s) Repeater Options Accepted', self._system)
                        self._stats['CONNECTION'] = 'YES'
                        self._stats['CONNECTED'] = time()
                        logger.info('(%s) Connection to Master Completed with options', self._system)
                    else:
                        self._stats['CONNECTION'] = 'NO'
                        logger.error('(%s) Master ACK Contained wrong ID - Connection Reset', self._system)

            elif _command == MSTP:    # Actually MSTPONG -- a reply to RPTPING (send by peer)
                _peer_id = _data[7:11]
                if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                    self._stats['PING_OUTSTANDING'] = False
                    self._stats['NUM_OUTSTANDING'] = 0
                    self._stats['PINGS_ACKD'] += 1
                    logger.trace('(%s) MSTPONG Received. Pongs Since Connected: %s', self._system, self._stats['PINGS_ACKD'])

            elif _command == MSTC:    # Actually MSTCL -- notify us the master is closing down
                _peer_id = _data[5:9]
                if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                    self._stats['CONNECTION'] = 'NO'
                    logger.info('(%s) MSTCL Recieved', self._system)

            else:
                logger.error('(%s) Received an invalid command in packet: %s', self._system, ahex(_data))

#
# Socket-based reporting section
#
class report(NetstringReceiver):
    def __init__(self, factory):
        self._factory = factory

    def connectionMade(self):
        self._factory.clients.append(self)
        logger.info('(REPORT) HBlink reporting client connected: %s', self.transport.getPeer())

    def connectionLost(self, reason):
        logger.info('(REPORT) HBlink reporting client disconnected: %s', self.transport.getPeer())
        self._factory.clients.remove(self)

    def stringReceived(self, data):
        self.process_message(data)

    def process_message(self, _message):
        opcode = _message[:1]
        if opcode == REPORT_OPCODES['CONFIG_REQ']:
            logger.info('(REPORT) HBlink reporting client sent \'CONFIG_REQ\': %s', self.transport.getPeer())
            self.send_config()
        else:
            logger.error('(REPORT) got unknown opcode')

class reportFactory(Factory):
    def __init__(self, config):
        self._config = config

    def buildProtocol(self, addr):
        if (addr.host) in self._config['REPORTS']['REPORT_CLIENTS'] or '*' in self._config['REPORTS']['REPORT_CLIENTS']:
            logger.debug('(REPORT) Permitting report server connection attempt from: %s:%s', addr.host, addr.port)
            return report(self)
        else:
            logger.error('(REPORT) Invalid report server connection attempt from: %s:%s', addr.host, addr.port)
            return None

    def send_clients(self, _message):
        for client in self.clients:
            client.sendString(_message)

    def send_config(self):
        serialized = pickle.dumps(self._config['SYSTEMS'], protocol=2) #.decode('utf-8', errors='ignore') #pickle.HIGHEST_PROTOCOL)
        logger.debug('(REPORT) Send config')
        self.send_clients(b''.join([REPORT_OPCODES['CONFIG_SND'], serialized]))

#Use this try_download instead of that from dmr_utils3
def try_download(_path, _file, _url, _stale,):
    no_verify = ssl._create_unverified_context()
    now = time()
    file_exists = isfile(''.join([_path,_file])) == True
    if file_exists:
        file_old = (getmtime(''.join([_path,_file])) + _stale) < now
    if not file_exists or (file_exists and file_old):
        try:
            with urlopen(_url, context=no_verify) as response:
                data = response.read()
                #outfile.write(data)
                response.close()
            result = 'ID ALIAS MAPPER: \'{}\' successfully downloaded'.format(_file)
        except IOError:
            result = 'ID ALIAS MAPPER: \'{}\' could not be downloaded due to an IOError'.format(_file)
        else:
            if data and (data != b'{}'):
                try:
                    with open(''.join([_path,_file]), 'wb') as outfile:
                        outfile.write(data)
                        outfile.close()
                except IOError:
                    result = 'ID ALIAS mapper \'{}\' file could not be written due to an IOError'.format(_file)
            else:
                result = 'ID ALIAS mapper \'{}\' file not written because downloaded data is empty for some reason'.format(_file)
                
    else:
        result = 'ID ALIAS MAPPER: \'{}\' is current, not downloaded'.format(_file)
    
    return result

#Read list of listed servers from CSV (actually TSV) file 
def mk_server_dict(path,filename):
    server_ids = {}
    try:
        with open(''.join([path,filename]),newline='') as csvfile:
            reader = csv.DictReader(csvfile,dialect='excel-tab')
            for _row in reader:
                server_ids[_row['OPB Net ID']] = _row['Country']
        return(server_ids)
    except IOError as err:
        logger.warning('ID ALIAS MAPPER: %s could not be read due to IOError: %s',filename,err)
        return(False)


# ID ALIAS CREATION
# Download
def mk_aliases(_config):
    peer_ids = {}
    subscriber_ids = {}
    local_subscriber_ids = {}
    talkgroup_ids = {}
    server_ids = {}
    if _config['ALIASES']['TRY_DOWNLOAD'] == True:
        # Try updating peer aliases file
        result = try_download(_config['ALIASES']['PATH'], _config['ALIASES']['PEER_FILE'], _config['ALIASES']['PEER_URL'], _config['ALIASES']['STALE_TIME'])
        logger.info('(ALIAS) %s', result)
        # Try updating subscriber aliases file
        result = try_download(_config['ALIASES']['PATH'], _config['ALIASES']['SUBSCRIBER_FILE'], _config['ALIASES']['SUBSCRIBER_URL'], _config['ALIASES']['STALE_TIME'])
        logger.info('(ALIAS) %s', result)
        #Try updating tgid aliases file
        result = try_download(_config['ALIASES']['PATH'], _config['ALIASES']['TGID_FILE'], _config['ALIASES']['TGID_URL'], _config['ALIASES']['STALE_TIME'])
        logger.info('(ALIAS) %s', result)
        #Try updating server ids file
        result = try_download(_config['ALIASES']['PATH'], _config['ALIASES']['SERVER_ID_FILE'], _config['ALIASES']['SERVER_ID_URL'], _config['ALIASES']['STALE_TIME'])
        logger.info('(ALIAS) %s', result)
        
    # Make Dictionaries
    try:
        peer_ids = mk_id_dict(_config['ALIASES']['PATH'], _config['ALIASES']['PEER_FILE'])
    except Exception as e:
        logger.error('(ALIAS) ID ALIAS MAPPER: problem with data in peer_ids dictionary, not updating: %s',e)
    else:
        if peer_ids:
            logger.info('(ALIAS) ID ALIAS MAPPER: peer_ids dictionary is available')

    try:
        subscriber_ids = mk_id_dict(_config['ALIASES']['PATH'], _config['ALIASES']['SUBSCRIBER_FILE'])
    except Exception as e:
        logger.info('(ALIAS) ID ALIAS MAPPER: problem with data in subscriber_ids dictionary, not updating: %s',e)
    else:
        #Add special IDs to DB
        subscriber_ids[900999] = 'D-APRS'
        subscriber_ids[4294967295] = 'SC'

        if subscriber_ids:
            logger.info('(ALIAS) ID ALIAS MAPPER: subscriber_ids dictionary is available')
    try:
        talkgroup_ids = mk_id_dict(_config['ALIASES']['PATH'], _config['ALIASES']['TGID_FILE'])
    except Exception as e:
        logger.info('(ALIAS) ID ALIAS MAPPER: problem with data in talkgroup_ids dictionary, not updating: %s',e)
    else:
        if talkgroup_ids:
            logger.info('(ALIAS) ID ALIAS MAPPER: talkgroup_ids dictionary is available')
    try:   
        local_subscriber_ids = mk_id_dict(_config['ALIASES']['PATH'], _config['ALIASES']['LOCAL_SUBSCRIBER_FILE'])
    except Exception as e:
        logger.info('(ALIAS) ID ALIAS MAPPER: problem with data in local_subscriber_ids dictionary, not updating: %s',e)
    else:
        if subscriber_ids:
            logger.info('(ALIAS) ID ALIAS MAPPER: local_subscriber_ids dictionary is available')
    try:        
        server_ids = mk_server_dict(_config['ALIASES']['PATH'], _config['ALIASES']['SERVER_ID_FILE'])
    except Exception as e:
        logger.info('(ALIAS) ID ALIAS MAPPER: problem with data in server_ids dictionary, not updating: %s',e)
    if server_ids:
        logger.info('(ALIAS) ID ALIAS MAPPER: server_ids dictionary is available')
        
        
    return peer_ids, subscriber_ids, talkgroup_ids, local_subscriber_ids, server_ids


#************************************************
#      MAIN PROGRAM LOOP STARTS HERE
#************************************************

if __name__ == '__main__':
    # Python modules we need
    import argparse
    import sys
    import os
    import signal

    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually hblink.cfg)')
    parser.add_argument('-l', '--logging', action='store', dest='LOG_LEVEL', help='Override config file logging level.')
    cli_args = parser.parse_args()

    # Ensure we have a path for the config file, if one wasn't specified, then use the execution directory
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/hblink.cfg'

    # Call the external routine to build the configuration dictionary
    CONFIG = config.build_config(cli_args.CONFIG_FILE)

    # Call the external routing to start the system logger
    if cli_args.LOG_LEVEL:
        CONFIG['LOGGER']['LOG_LEVEL'] = cli_args.LOG_LEVEL
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('\n\nCopyright (c) 2013, 2014, 2015, 2016, 2018, 2019\n\tThe Regents of the K0USY Group. All rights reserved.\n')
    logger.debug('(GLOBAL) Logging system started, anything from here on gets logged')

    # Set up the signal handler
    def sig_handler(_signal, _frame):
        logger.info('(GLOBAL) SHUTDOWN: HBLINK IS TERMINATING WITH SIGNAL %s', str(_signal))
        hblink_handler(_signal, _frame)
        logger.info('(GLOBAL) SHUTDOWN: ALL SYSTEM HANDLERS EXECUTED - STOPPING REACTOR')
        reactor.stop()

    # Set signal handers so that we can gracefully exit if need be
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, sig_handler)

    peer_ids, subscriber_ids, talkgroup_ids = mk_aliases(CONFIG)

    # INITIALIZE THE REPORTING LOOP
    if CONFIG['REPORTS']['REPORT']:
        report_server = config_reports(CONFIG, reportFactory)
    else:
        report_server = None
        logger.info('(REPORT) TCP Socket reporting not configured')

    # HBlink instance creation
    logger.info('(GLOBAL) HBlink \'HBlink.py\' -- SYSTEM STARTING...')
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
                systems[system] = OPENBRIDGE(system, CONFIG, report_server)
            else:
                systems[system] = HBSYSTEM(system, CONFIG, report_server)
            reactor.listenUDP(CONFIG['SYSTEMS'][system]['PORT'], systems[system], interface=CONFIG['SYSTEMS'][system]['IP'])
            logger.debug('(GLOBAL) %s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])

    reactor.run()
