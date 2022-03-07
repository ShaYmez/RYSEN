###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>  
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

from twisted.internet.protocol import DatagramProtocol
from twisted.internet import reactor, task
from time import time
from dmr_utils3.utils import int_id
import random
import ipaddress
import os
from setproctitle import setproctitle
from datetime import datetime

# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Simon Adlem - G7RZU'
__copyright__  = 'Copyright (c) Simon Adlem, G7RZU 2020,2021,2022'
__credits__    = 'Jon Lee, G4TSN; Norman Williams, M6NBP; Christian, OA4DOA'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Simon Adlem G7RZU'
__email__      = 'simon@gb7fr.org.uk'

def IsIPv4Address(ip):
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ValueError as errorCode:
        pass
        return False
    
def IsIPv6Address(ip):
    try:
        ipaddress.IPv6Address(ip)
        return True
    except ValueError as errorCode:
        pass

class Proxy(DatagramProtocol):

    def __init__(self,Master,ListenPort,connTrack,blackList,IPBlackList,Timeout,Debug,ClientInfo,DestportStart,DestPortEnd):
        self.master = Master
        self.connTrack = connTrack
        self.peerTrack = {}
        self.timeout = Timeout
        self.debug = Debug
        self.clientinfo = ClientInfo
        self.blackList = blackList
        self.IPBlackList = IPBlackList
        self.destPortStart = DestportStart
        self.destPortEnd = DestPortEnd
        self.numPorts = DestPortEnd - DestportStart
        
        
    def reaper(self,_peer_id):
        if self.debug:
            print("dead",_peer_id)
        if self.clientinfo and _peer_id != b'\xff\xff\xff\xff':
            print(f"{datetime.now().replace(microsecond=0)} Client: ID:{str(int_id(_peer_id)).rjust(9)} IP:{self.peerTrack[_peer_id]['shost'].rjust(15)} Port:{self.peerTrack[_peer_id]['sport']} Removed.")
        self.transport.write(b'RPTCL'+_peer_id, (self.master,self.peerTrack[_peer_id]['dport']))
        self.connTrack[self.peerTrack[_peer_id]['dport']] = False
        del self.peerTrack[_peer_id]

    def datagramReceived(self, data, addr):
        
        # HomeBrew Protocol Commands
        DMRD    = b'DMRD'
        DMRA    = b'DMRA'
        MSTCL   = b'MSTCL'
        MSTNAK  = b'MSTNAK'
        MSTPONG = b'MSTPONG'
        MSTN    = b'MSTN'
        MSTP    = b'MSTP'
        MSTC    = b'MSTC'
        RPTL    = b'RPTL'
        RPTPING = b'RPTPING'
        RPTCL   = b'RPTCL'
        RPTL    = b'RPTL'
        RPTACK  = b'RPTACK'
        RPTK    = b'RPTK'
        RPTC    = b'RPTC'
        RPTP    = b'RPTP'
        RPTA    = b'RPTA'
        RPTO    = b'RPTO'
        
        #Proxy control commands
        PRBL    = b'PRBL'
        
        _peer_id = False
        
        host,port = addr
        
        nowtime = time()
        
        Debug = self.debug
        
        if host in self.IPBlackList:
            return
        
        #If the packet comes from the master
        if host == self.master:
            _command = data[:4]
            
            if _command == PRBL:
                _peer_id = data[4:8]
                _bltime = data[8:].decode('UTF-8')
                _bltime = float(_bltime)
                try: 
                    self.IPBlackList[self.peerTrack[_peer_id]['shost']] = _bltime
                except KeyError:
                    pass
                return
            
            if _command == DMRD:
                _peer_id = data[11:15]
            elif  _command == RPTA:
                    if data[6:10] in self.peerTrack:
                        _peer_id = data[6:10]
                    else:
                        _peer_id = self.connTrack[port]
            elif _command == MSTN:
                    _peer_id = data[6:10]
            elif _command == MSTP:
                    _peer_id = data[7:11]
            elif _command == MSTC:
                    _peer_id = data[5:9]
                
            if self.debug:
                print(data)
            if _peer_id in self.peerTrack:
                self.transport.write(data,(self.peerTrack[_peer_id]['shost'],self.peerTrack[_peer_id]['sport']))
                # Remove the client after send a MSTN or MSTC packet
                if _command in (MSTN,MSTC):
                    # Give time to the client for a reply to prevent port reassignment 
                    self.peerTrack[_peer_id]['timer'].reset(15)
 
            return
            
                   
        else:
            _command = data[:4]
            
            if _command == DMRD:                # DMRData -- encapsulated DMR data frame
                _peer_id = data[11:15]
            elif _command == DMRA:              # DMRAlias -- Talker Alias information
                _peer_id = data[4:8]
            elif _command == RPTL:              # RPTLogin -- a repeater wants to login
                _peer_id = data[4:8]
            elif _command == RPTK:              # Repeater has answered our login challenge
                _peer_id = data[4:8]
            elif _command == RPTC:              # Repeater is sending it's configuraiton OR disconnecting
                if data[:5] == RPTCL:           # Disconnect command
                    _peer_id = data[5:9]
                else:
                    _peer_id = data[4:8]        # Configure Command
            elif _command == RPTO:              # options
                _peer_id = data[4:8]
            elif _command == RPTP:              # RPTPing -- peer is pinging us
                _peer_id = data[7:11]
            else:
                return
            
            if _peer_id in self.peerTrack:
                _dport = self.peerTrack[_peer_id]['dport']
                self.peerTrack[_peer_id]['sport'] = port
                self.peerTrack[_peer_id]['shost'] = host
                self.transport.write(data, (self.master,_dport))
                self.peerTrack[_peer_id]['timer'].reset(self.timeout)
                if self.debug:
                    print(data)
                return

            else:
                if int_id(_peer_id) in self.blackList:
                    return   
                # Make a list with the available ports
                _ports_avail = [port for port in self.connTrack if not self.connTrack[port]]
                if _ports_avail:
                    _dport = random.choice(_ports_avail)
                else:
                    return
                self.connTrack[_dport] = _peer_id
                self.peerTrack[_peer_id] = {}
                self.peerTrack[_peer_id]['dport'] = _dport
                self.peerTrack[_peer_id]['sport'] = port
                self.peerTrack[_peer_id]['shost'] = host
                self.peerTrack[_peer_id]['timer'] = reactor.callLater(self.timeout,self.reaper,_peer_id)
                self.transport.write(data, (self.master,_dport))
                pripacket = b''.join([b'PRIN',host.encode('UTF-8'),b':',str(port).encode('UTF-8')])
                #Send IP and Port info to server
                self.transport.write(pripacket, (self.master,_dport))

                if self.clientinfo and _peer_id != b'\xff\xff\xff\xff':
                    print(f'{datetime.now().replace(microsecond=0)} New client: ID:{str(int_id(_peer_id)).rjust(9)} IP:{host.rjust(15)} Port:{port}, assigned to port:{_dport}.')
                if self.debug:
                    print(data)
                return


if __name__ == '__main__':

#*** CONFIG HERE ***
    
    Master = "127.0.0.1"
    ListenPort = 62031
    # '' = all IPv4, '::' = all IPv4 and IPv6 (Dual Stack)
    ListenIP = ''
    DestportStart = 54000
    DestPortEnd = 54100
    Timeout = 30
    Stats = False
    Debug = False
    ClientInfo = False
    BlackList = [1234567]
    #e.g. {10.0.0.1: 0, 10.0.0.2: 0}
    IPBlackList = {}
    
#*******************
    
    
    #Set process title early
    setproctitle(__file__)
    
    #If IPv6 is enabled by enivornment variable...
    if ListenIP == '' and 'FDPROXY_IPV6' in os.environ and bool(os.environ['FDPROXY_IPV6']):
        ListenIP = '::'
        
    #Override static config from Environment
    if 'FDPROXY_STATS' in os.environ:
        Stats = bool(os.environ['FDPROXY_STATS'])
    #if 'FDPROXY_DEBUG' in os.environ:
    #    Debug = bool(os.environ['FDPROXY_DEBUG'])
    if 'FDPROXY_CLIENTINFO' in os.environ:
        ClientInfo = bool(os.environ['FDPROXY_CLIENTINFO'])
    if 'FDPROXY_LISTENPORT' in os.environ:
        ListenPort = os.environ['FDPROXY_LISTENPORT']
        
    
    CONNTRACK = {}

    for port in range(DestportStart,DestPortEnd+1,1):
        CONNTRACK[port] = False
    
    #If we are listening IPv6 and Master is an IPv4 IPv4Address
    #IPv6ify the address. 
    if ListenIP == '::' and IsIPv4Address(Master):
        Master = '::ffff:' + Master

    reactor.listenUDP(ListenPort,Proxy(Master,ListenPort,CONNTRACK,BlackList,IPBlackList,Timeout,Debug,ClientInfo,DestportStart,DestPortEnd),interface=ListenIP)

    def loopingErrHandle(failure):
        print('(GLOBAL) STOPPING REACTOR TO AVOID MEMORY LEAK: Unhandled error innowtimed loop.\n {}'.format(failure))
        reactor.stop()
        
    def stats():        
        count = 0
        nowtime = time()
        for port in CONNTRACK:
            if CONNTRACK[port]:
                count = count+1
                
        totalPorts = DestPortEnd - DestportStart
        freePorts = totalPorts - count
        
        print("{} ports out of {} in use ({} free)".format(count,totalPorts,freePorts))
        
    def blackListTrimmer():
        _timenow = time()
        _dellist = []
        for entry in IPBlackList:
            deletetime = IPBlackList[entry]
            if deletetime and deletetime < _timenow:
                _dellist.append(entry)
        
        for delete in _dellist:
            IPBlackList.pop(delete)

        
    if Stats == True:
        stats_task = task.LoopingCall(stats)
        statsa = stats_task.start(30)
        statsa.addErrback(loopingErrHandle)
        
    blacklist_task = task.LoopingCall(blackListTrimmer)
    blacklista = blacklist_task.start(15)
    blacklista.addErrback(loopingErrHandle)
    
    reactor.run()
    
