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
__copyright__  = 'Copyright (c) Simon Adlem, G7RZU 2022'
__credits__    = 'Jon Lee, G4TSN; Norman Williams, M6NBP'
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

    def __init__(self,Master,ListenPort,connTrack,peerTrack,blackList,IPBlackList,Timeout,Debug,ClientInfo,DestportStart,DestPortEnd):
        self.master = Master
        self.connTrack = connTrack
        self.peerTrack = peerTrack
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
        self.connTrack[self.peerTrack[_peer_id]['dport']] = False
        del self.peerTrack[_peer_id]

    def datagramReceived(self, data, addr):
        
        _peer_id = False
        
        host,port = addr
        
        nowtime = time()
        
        Debug = self.debug
        
        if host in self.IPBlackList:
            return
        
        #If the packet comes from the master
        if host == self.master:
        
            #fill this in 
            _peer_id = data[0:0]
            
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
            #fill this in 
            _peer_id = data[0:0]
            
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
                
                if self.clientinfo:
                    print(f'{datetime.now().replace(microsecond=0)} New client: ID:{str(int_id(_peer_id)).rjust(9)} IP:{host.rjust(15)} Port:{port}, assigned to port:{_dport}.')
                if self.debug:
                    print(data)
                return


if __name__ == '__main__':
    
    import signal
    import configparser
    import argparse
    import sys
    import json

    #Set process title early
    setproctitle(__file__)
        
    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually freedmr.cfg)')
    cli_args = parser.parse_args()


    # Ensure we have a path for the config file, if one wasn't specified, then use the execution directory
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/freedmr.cfg'
    
    _config_file = cli_args.CONFIG_FILE
    
    config = configparser.ConfigParser()
    
    if not config.read(_config_file):
        print('Configuration file \''+_config_file+'\' is not a valid configuration file!')
        
    try:

        Master = config.get('PROXY','Master')
        ListenPort = config.getint('PROXY','ListenPort')
        ListenIP = config.get('PROXY','ListenIP')
        DestportStart = config.getint('PROXY','DestportStart')
        DestPortEnd = config.getint('PROXY','DestPortEnd')
        Timeout = config.getint('PROXY','Timeout')
        Stats = config.getboolean('PROXY','Stats')
        Debug = config.getboolean('PROXY','Debug')
        ClientInfo = config.getboolean('PROXY','ClientInfo')
        BlackList = json.loads(config.get('PROXY','BlackList'))
        IPBlackList = json.loads(config.get('PROXY','IPBlackList'))
        
    except configparser.Error as err:
        print('Error processing configuration file -- {}'.format(err))
        
        print('Using default config')
#*** CONFIG HERE ***
    
        Master = "127.0.0.1"
        ListenPort = 62031
        #'' = all IPv4, '::' = all IPv4 and IPv6 (Dual Stack)
        ListenIP = ''
        DestportStart = 50000
        DestPortEnd = 50002
        Timeout = 30
        Stats = False
        Debug = False
        ClientInfo = False
        BlackList = [1234567]
        #e.g. {10.0.0.1: 0, 10.0.0.2: 0}
        IPBlackList = {}
        
#*******************        
    
    CONNTRACK = {}
    PEERTRACK = {}
    
    # Set up the signal handler
    def sig_handler(_signal, _frame):
        print('(GLOBAL) SHUTDOWN: PROXY IS TERMINATING WITH SIGNAL {}'.format(str(_signal)))
        reactor.stop()

    # Set signal handers so that we can gracefully exit if need be
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, sig_handler)
        
    #Override static config from Environment
    if 'FDPROXY_STATS' in os.environ:
        Stats = bool(os.environ['FDPROXY_STATS'])
    #if 'FDPROXY_DEBUG' in os.environ:
    #    Debug = bool(os.environ['FDPROXY_DEBUG'])
    if 'FDPROXY_CLIENTINFO' in os.environ:
        ClientInfo = bool(os.environ['FDPROXY_CLIENTINFO'])
    if 'FDPROXY_LISTENPORT' in os.environ:
        ListenPort = int(os.environ['FDPROXY_LISTENPORT'])
        
    for port in range(DestportStart,DestPortEnd+1,1):
        CONNTRACK[port] = False
    
    #If we are listening IPv6 and Master is an IPv4 IPv4Address
    #IPv6ify the address. 
    if ListenIP == '::' and IsIPv4Address(Master):
        Master = '::ffff:' + Master

    reactor.listenUDP(ListenPort,Proxy(Master,ListenPort,CONNTRACK,PEERTRACK,BlackList,IPBlackList,Timeout,Debug,ClientInfo,DestportStart,DestPortEnd),interface=ListenIP)

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
        
        
    if Stats == True:
        stats_task = task.LoopingCall(stats)
        statsa = stats_task.start(30)
        statsa.addErrback(loopingErrHandle)
            
    reactor.run()
    
