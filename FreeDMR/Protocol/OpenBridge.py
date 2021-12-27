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
file (usually freedmr.cfg). It is ALWAYS best practice to ensure that this program
works stand-alone before troubleshooting any applications that use it. It has
sufficient logging to be used standalone as a troubleshooting application.
'''

# Specifig functions from modules we need
from binascii import b2a_hex as ahex
from binascii import a2b_hex as bhex
from random import randint
from hashlib import sha256, sha1
from hmac import new as hmac_new, compare_digest
from time import time
from collections import deque

# Twisted is pretty important, so I keep it separate
from twisted.internet.protocol import DatagramProtocol, Factory, Protocol
from twisted.protocols.basic import NetstringReceiver
from twisted.internet import reactor, task

# Other files we pull from -- this is mostly for readability and segmentation
import log
import config
from FreeDMR.Const.const import *
from dmr_utils3.utils import int_id, bytes_4, try_download, mk_id_dict

# Imports for the reporting server
import pickle
from FreeDMR.Const.reporting_const import *


# The module needs logging logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)

# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS, Forked by Simon Adlem - G7RZU'
__copyright__  = 'Copyright (c) 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group, Simon Adlem, G7RZU 2020,2021'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT; Jon Lee, G4TSN; Norman Williams, M6NBP'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Simon Adlem G7RZU'
__email__      = 'simon@gb7fr.org.uk'

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

            
    def loopingErrHandle(self,failure):
        logger.error('(GLOBAL - freedmr.py) Unhandled error in timed loop.\n %s', failure)
        

    def startProtocol(self):
        logger.info('(%s) Starting OBP. TARGET_IP: %s, TARGET_PORT: %s',self._system, self._config['TARGET_IP'], self._config['TARGET_PORT'])
        if self._config['ENHANCED_OBP']:
            logger.debug('(%s) *BridgeControl* starting KeepAlive timer',self._system)
            self._bcka_task = task.LoopingCall(self.send_bcka)
            self._bcka = self._bcka_task.start(10)
            self._bcka.addErrback(self.loopingErrHandle)

    def dereg(self):
        logger.info('(%s) is mode OPENBRIDGE. No De-Registration required, continuing shutdown', self._system)

    def send_system(self, _packet):
        if _packet[:4] == DMRD and self._config['TARGET_IP']:
            #_packet = _packet[:11] + self._config['NETWORK_ID'] + _packet[15:]
            _packet = b''.join([_packet[:11], self._CONFIG['GLOBAL']['SERVER_ID'], _packet[15:]])
            #_packet += hmac_new(self._config['PASSPHRASE'],_packet,sha1).digest()
            _packet = b''.join([_packet, (hmac_new(self._config['PASSPHRASE'],_packet,sha1).digest())])
            self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
            # KEEP THE FOLLOWING COMMENTED OUT UNLESS YOU'RE DEBUGGING DEEPLY!!!!
            #logger.debug('(%s) TX Packet to OpenBridge %s:%s -- %s', self._system, self._config['TARGET_IP'], self._config['TARGET_PORT'], ahex(_packet))                
        else:
            
            if not self._config['TARGET_IP']:
                logger.debug('(%s) Not sent packet as TARGET_IP not currently known')
            else:
                logger.error('(%s) OpenBridge system was asked to send non DMRD packet with send_system(): %s', self._system, _packet)
            
    def send_bcka(self):
        if self._config['TARGET_IP']:
            _packet = BCKA
            _packet = b''.join([_packet[:4], (hmac_new(self._config['PASSPHRASE'],_packet,sha1).digest())])
            self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
            logger.debug('(%s) *BridgeControl* sent KeepAlive',self._system)
        else:
            logger.debug('(%s) *BridgeControl* not sending KeepAlive, TARGET_IP currently not known',self._system)
        
        
    def send_bcsq(self,_tgid,_stream_id):
        if self._config['TARGET_IP']:
            _packet = b''.join([BCSQ, _tgid, _stream_id])
            _packet = b''.join([_packet, (hmac_new(self._config['PASSPHRASE'],_packet,sha1).digest())])
            self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
            logger.debug('(%s) *BridgeControl* sent BCSQ Source Quench, TG: %s, Stream ID: %s',self._system,int_id(_tgid), int_id(_stream_id))
        else:
            logger.debug('(%s) *BridgeControl* Not sent BCSQ Source Quench TARGET_IP not known , TG: %s, Stream ID: %s',self._system,int_id(_tgid))
    

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):
        pass
        #print(int_id(_peer_id), int_id(_rf_src), int_id(_dst_id), int_id(_seq), _slot, _call_type, _frame_type, repr(_dtype_vseq), int_id(_stream_id))

    def datagramReceived(self, _packet, _sockaddr):
        # Keep This Line Commented Unless HEAVILY Debugging!
        #logger.debug('(%s) RX packet from %s -- %s', self._system, _sockaddr, ahex(_packet))

        if _packet[:4] == DMRD:    # DMRData -- encapsulated DMR data frame
            _data = _packet[:53]
            _hash = _packet[53:]
            _ckhs = hmac_new(self._config['PASSPHRASE'],_data,sha1).digest()

            if compare_digest(_hash, _ckhs) and (_sockaddr == self._config['TARGET_SOCK'] or self._config['RELAX_CHECKS']):
                _peer_id = _data[11:15]
                if self._config['NETWORK_ID'] != _peer_id:
                    logger.error('(%s) OpenBridge packet discarded because NETWORK_ID: %s Does not match sent Peer ID: %s', self._system, int_id(self._config['NETWORK_ID']), int_id(_peer_id))
                    return
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
                #logger.debug('(%s) DMRD - Seqence: %s, RF Source: %s, Destination ID: %s', self._system, int_id(_seq), int_id(_rf_src), int_id(_dst_id))

                # Sanity check for OpenBridge -- all calls must be on Slot 1
                if _slot != 1:
                    logger.error('(%s) OpenBridge packet discarded because it was not received on slot 1. SID: %s, TGID %s', self._system, int_id(_rf_src), int_id(_dst_id))
                    return
                
                #Low-level TG filtering 
                if _call_type != 'unit':
                    _int_dst_id = int_id(_dst_id)
                    if _int_dst_id <= 79 or (_int_dst_id >= 9990 and _int_dst_id <= 9999) or _int_dst_id == 900999:
                        if _stream_id not in self._laststrid:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TG FILTER', self._system, int_id(_stream_id), _int_dst_id)
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
                self.dmrd_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data)
                #Silently treat a DMRD packet like a keepalive - this is because it's traffic and the 
                #Other end may not have enabled ENAHNCED_OBP
                self._config['_bcka'] = time()
            else:
                h,p = _sockaddr
                logger.info('(%s) OpenBridge HMAC failed, packet discarded - OPCODE: %s DATA: %s HMAC LENGTH: %s HMAC: %s SRC IP: %s SRC PORT: %s', self._system, _packet[:4], repr(_packet[:53]), len(_packet[53:]), repr(_packet[53:]),h,p) 

        if self._config['ENHANCED_OBP']:
            if _packet[:2] == BC:    # Bridge Control packet (Extended OBP)
                #Keep Alive
                if _packet[:4] == BCKA:
                    #_data = _packet[:53]
                    _hash = _packet[4:]
                    _ckhs = hmac_new(self._config['PASSPHRASE'],_packet[:4],sha1).digest()
                    if compare_digest(_hash, _ckhs):
                        logger.debug('(%s) *BridgeControl* Keep Alive received',self._system)
                        self._config['_bcka'] = time()
                        if _sockaddr != self._config['TARGET_SOCK']:
                            h,p =  _sockaddr
                            logger.warning('(%s) *BridgeControl* Source IP and Port has changed for OBP from %s:%s to %s:%s,  updating',self._system,self._config['TARGET_IP'],self._config['TARGET_PORT'],h,p)
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
                        logger.debug('(%s) *BridgeControl*  BCSQ Source Quench request received for TGID: %s, Stream ID: %s',self._system,int_id(_tgid), int_id(_stream_id))
                        if '_bcsq' not in self._config:
                            self._config['_bcsq'] = {}
                        self._config['_bcsq'][_tgid] = _stream_id
                    else:
                        h,p = _sockaddr
                        logger.warning('(%s) *BridgeControl* BCSQ invalid Source Quench, packet discarded - OPCODE: %s DATA: %s HMAC LENGTH: %s HMAC: %s SRC IP: %s SRC PORT: %s', self._system, _packet[:4], repr(_packet[:53]), len(_packet[53:]), repr(_packet[53:]),h,p)  
         
