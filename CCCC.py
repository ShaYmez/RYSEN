
#import log
#import config

from time import time
from random import randint

from twisted.internet import reactor,task
from twisted.internet.defer import Deferred
from twisted.internet.protocol import ClientFactory,ReconnectingClientFactory,Protocol
from twisted.protocols.basic import LineReceiver

#from pyrtp import *

# The module needs logging logging, but handlers, etc. are controlled by the parent
#import logging
#logger = logging.getLogger(__name__)


class CCCC():
    def __init__(self,host,port):
        self._CCClient = self.CCClient
        self._rnd_number = randint(0x00, 0xFFFFFFFF)
        self.hex_id = bytes(hex(self._rnd_number),'utf8')
        self.bytes_id = self._rnd_number.to_bytes(4,byteorder="big")
        
        self.CF = reactor.connectTCP(host, port, self.CCClientFactory(self._CCClient,self.hex_id))
    
    def closeConnection(self):
        self.transport.loseConnection()
            
    class CCClient(LineReceiver):
        
        delimiter = b'\n'
        
        end = b"Bye-bye!"
        
        def connectionMade(self):
            #self.peer = self.transport.getPeer()
            _hexid = self.hex_id
            #
            #CCCC._ssrc_list[_hexid] = self._peer
            _linkid = b'91 '
            _channel_name = b'CC outbound link'
            _local_site_name = b'FreeDMR-Testing'
            #Generate a random MAC that looks like a real one
            #We don't really want to share our MAC ;-)
            _local_mac = bytes(hex(randint(0x00, 0xFFFFFFFFFFFF)),'utf8')
            #Fake a recent code_rev of CBridge
            _code_rev = b'9959__December_5_2021__13.25.28'
            #We'll use artistic licence on this one
            _os_ver = b'FreeDMR Peer Server'
        
            self.sendLine(_hexid)
            self.sendLine(_linkid)
            self.sendLine(_channel_name)
            self.sendLine(_local_site_name)
            self.sendLine(b'Server Inbound')
            self.sendLine(_local_mac)
            self.sendLine(_code_rev)
            self.sendLine(_os_ver)

        def lineReceived(self,line):
            packet_dict = {} 
            #If we get codec AMBE (that's all we support)
            if line == b'AMBE' or self._counter != 0:
                self._counter = 6
            else:
                self.transport.loseConnection()
            
            if self._counter:
                if self._counter == 6:
                    self._ms_window = line
                    self._counter = 5
                elif self._counter == 5:
                    self._seconds_microseconds = line
                    self._counter = 4
                elif self._counter == 4:
                    self._syn_and_safe_ver_date = line
                    self._counter = 3
                elif self._counter == 3:
                    self._remote_sys_name = line
                    self._counter = 2
                elif self._counter == 2:
                    self._remote_tos_value = line
                    self._counter = 1
                elif self._counter == 1:
                    self._remote_os_ver = line
                    self._counter = 0
            
            line = line.decode("utf8")
            if line[0:1] == 'B':
                if line[1:3] == '01':
                    packet_dict['type'] = 'ON'
                    packet_dict['linkid'] = line[3:5]
                    packet_dict['line'] = line
                else:
                    packet_dict['type'] = 'OFF'
                    packet_dict['linkid'] = line[1:3]
                    packet_dict['line'] = line
                    
                self.controlLine(packet_dict)
            
        def controlLine(self,packet_dict):
            pass
        
            

    class CCClientFactory(ReconnectingClientFactory):
        def __init__(self,CCClient,hex_id):
            self.done = Deferred()
            self.protocol = CCClient
            self.protocol.hex_id = hex_id
            

        def clientConnectionFailed(self, connector, reason):
            print("connection failed:", reason.getErrorMessage())
            ReconnectingClientFactory.clientConnectionLost(self, connector, reason)

        def clientConnectionLost(self, connector, reason):
            print("connection lost:", reason.getErrorMessage())
            ReconnectingClientFactory.clientConnectionLost(self, connector, reason)


