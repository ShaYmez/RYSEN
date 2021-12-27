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
import FreeDMR.Utilities.log as log
import FreeDMR.Config.config as config
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
        logger.error('(GLOBAL - freedmr.py) Unhandled error in timed loop.\n %s', failure)

    def startProtocol(self):
        # Set up periodic loop for tracking pings from peers. Run every 'PING_TIME' seconds
        self._system_maintenance = task.LoopingCall(self.maintenance_loop)
        self._system_maintenance_loop = self._system_maintenance.start(self._CONFIG['GLOBAL']['PING_TIME'])
        self._system_maintenance_loop.addErrback(self.loopingErrHandle)

    # Aliased in __init__ to maintenance_loop if system is a master
    def master_maintenance_loop(self):
        logger.debug('(%s) Master maintenance loop started', self._system)
        remove_list = []
        for peer in self._peers:
            _this_peer = self._peers[peer]
            # Check to see if any of the peers have been quiet (no ping) longer than allowed
            if _this_peer['LAST_PING']+(self._CONFIG['GLOBAL']['PING_TIME']*self._CONFIG['GLOBAL']['MAX_MISSED']) < time():
                remove_list.append(peer)
        for peer in remove_list:
            logger.info('(%s) Peer %s (%s) has timed out and is being removed', self._system, self._peers[peer]['CALLSIGN'], self._peers[peer]['RADIO_ID'])
            # Remove any timed out peers from the configuration
            del self._CONFIG['SYSTEMS'][self._system]['PEERS'][peer]
        if 'PEERS' not in self._CONFIG['SYSTEMS'][self._system] and 'OPTIONS' in self._CONFIG['SYSTEMS'][self._system]:
            
            if '_default_options' in self._CONFIG['SYSTEMS'][self._system]:
                logger.info('(%s) Setting default Options: %s',self._system, self._CONFIG['SYSTEMS'][self._system]['_default_options'])
                self._CONFIG['SYSTEMS'][self._system]['OPTIONS'] = self._CONFIG['SYSTEMS'][self._system]['_default_options']
            else:
                del self._CONFIG['SYSTEMS'][self._system]['OPTIONS']
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
            logger.debug('(%s) RPTPING Sent to Master. Total Sent: %s, Total Missed: %s, Currently Outstanding: %s', self._system, self._stats['PINGS_SENT'], self._stats['PINGS_SENT'] - self._stats['PINGS_ACKD'], self._stats['NUM_OUTSTANDING'])
            self._stats['PINGS_SENT'] += 1
            self._stats['PING_OUTSTANDING'] = True
            
    def updateSockaddr(self,ip):
        self._config['MASTER_IP'] = ip
        self._config['MASTER_SOCKADDR'] = (ip, self._config['MASTER_PORT'])
        logger.debug('(%s) hostname resolution performed: %s',self._system,ip)
        
    def updateSockaddr_errback(self,failure):
        logger.debug('(%s) hostname resolution error: %s',self._system,failure)

    def send_peers(self, _packet):
        for _peer in self._peers:
            self.send_peer(_peer, _packet)
            #logger.debug('(%s) Packet sent to peer %s', self._system, self._peers[_peer]['RADIO_ID'])

    def send_peer(self, _peer, _packet):
        if _packet[:4] == DMRD:
            _packet = b''.join([_packet[:11], _peer, _packet[15:]])
        self.transport.write(_packet, self._peers[_peer]['SOCKADDR'])
        # KEEP THE FOLLOWING COMMENTED OUT UNLESS YOU'RE DEBUGGING DEEPLY!!!!
        #logger.debug('(%s) TX Packet to %s on port %s: %s', self._peers[_peer]['RADIO_ID'], self._peers[_peer]['IP'], self._peers[_peer]['PORT'], ahex(_packet))

    def send_master(self, _packet):
        if _packet[:4] == DMRD:
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
            self.send_peer(_peer, MSTCL + _peer)
            logger.info('(%s) De-Registration sent to Peer: %s (%s)', self._system, self._peers[_peer]['CALLSIGN'], self._peers[_peer]['RADIO_ID'])

    def peer_dereg(self):
        self.send_master(RPTCL + self._config['RADIO_ID'])
        logger.info('(%s) De-Registration sent to Master: %s:%s', self._system, self._config['MASTER_SOCKADDR'][0], self._config['MASTER_SOCKADDR'][1])

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
                if acl_check(_peer_id, self._CONFIG['GLOBAL']['REG_ACL']) and acl_check(_peer_id, self._config['REG_ACL']):
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
                    logger.warning('(%s) Invalid Login from %s Radio ID: %s Denied by Registation ACL', self._system, _sockaddr[0], int_id(_peer_id))
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
                logger.warning('(%s) Login challenge from Radio ID that has not logged in: %s', self._system, int_id(_peer_id))

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
                        else:
                            logger.info('(%s) Deleting HBP Options',self._system)
                            del self._CONFIG['SYSTEMS'][self._system]['OPTIONS']
                    
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

                    self.send_peer(_peer_id, b''.join([RPTACK, _peer_id]))
                    logger.info('(%s) Peer %s (%s) has sent repeater configuration, Package ID: %s, Software ID: %s, Desc: %s', self._system, _this_peer['CALLSIGN'], _this_peer['RADIO_ID'],self._peers[_peer_id]['PACKAGE_ID'].decode().rstrip(),self._peers[_peer_id]['SOFTWARE_ID'].decode().rstrip(),self._peers[_peer_id]['DESCRIPTION'].decode().rstrip())
                else:
                    self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    logger.warning('(%s) Peer info from Radio ID that has not logged in: %s', self._system, int_id(_peer_id))

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
                logger.warning('(%s) Options from Radio ID that is not logged: %s', self._system, int_id(_peer_id))
        
        
        elif _command == RPTP:    # RPTPing -- peer is pinging us
                _peer_id = _data[7:11]
                if _peer_id in self._peers \
                            and self._peers[_peer_id]['CONNECTION'] == "YES" \
                            and self._peers[_peer_id]['SOCKADDR'] == _sockaddr:
                    self._peers[_peer_id]['PINGS_RECEIVED'] += 1
                    self._peers[_peer_id]['LAST_PING'] = time()
                    self.send_peer(_peer_id, b''.join([MSTPONG, _peer_id]))
                    #logger.debug('(%s) Received and answered RPTPING from peer %s (%s)', self._system, self._peers[_peer_id]['CALLSIGN'], int_id(_peer_id))
                else:
                    self.transport.write(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    logger.warning('(%s) Ping from Radio ID that is not logged in: %s', self._system, int_id(_peer_id))
        
        elif _command == DMRA:
                _peer_id = _data[4:8]
                logger.info('(%s) Peer has sent Talker Alias packet %s', self._system, _data)

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
                    #logger.debug('(%s) MSTPONG Received. Pongs Since Connected: %s', self._system, self._stats['PINGS_ACKD'])

            elif _command == MSTC:    # Actually MSTCL -- notify us the master is closing down
                _peer_id = _data[5:9]
                if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                    self._stats['CONNECTION'] = 'NO'
                    logger.info('(%s) MSTCL Recieved', self._system)

            else:
                logger.error('(%s) Received an invalid command in packet: %s', self._system, ahex(_data))







