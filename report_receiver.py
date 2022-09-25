###############################################################################
# Copyright (C) 2022 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>  
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

#This is example code to connect to the report service in FreeDMR / HBLink3
#It can be used as a skeleton to build logging and monitoring tools. 

import pickle

from twisted.internet import reactor
from twisted.internet.protocol import ReconnectingClientFactory
from twisted.protocols.basic import NetstringReceiver

from reporting_const import *

from pprint import pprint

class reportClient(NetstringReceiver):
            
    def stringReceived(self, data):
        
        if data[:1] == REPORT_OPCODES['BRDG_EVENT']:
            self.bridgeEvent(data[1:].decode('UTF-8'))
        elif data[:1] == REPORT_OPCODES['CONFIG_SND']:
            if cli_args.CONFIG:
                self.configSend(data[1:])
        elif data[:1] == REPORT_OPCODES['BRIDGE_SND']:
            if cli_args.BRIDGES:
                self.bridgeSend(data[1:])
        elif data == b'bridge updated':
            pass
        else:
            print('Unkown opcode - line:',data)
        
    def bridgeEvent(self,data):
        datalist = data.split(',')
        event = {
            'type'      : datalist[0],
            'event'     : datalist[1],
            'trx'       : datalist[2],
            'system'    : datalist[3],
            'streamid'  : datalist[4],
            'peerid'    : datalist[5],
            'subid'     : datalist[6],
            'slot'      : datalist[7],
            'dstid'     : datalist[8],
            'duration'  : False
             }
        
        if len(datalist) > 9:
            event['duration'] = datalist[9]
            
        if cli_args.EVENTS:
            pprint(event, compact=True)
        
    def bridgeSend(self,data):
        self.BRIDGES = pickle.loads(data)
        if cli_args.STATS:
            print('There are currently {} active bridges in the bridge table:\n'.format(len(self.BRIDGES)))
            for _bridge in self.BRIDGES.keys():
                print('{},'.format({str(_bridge)}))
            
        else:
            if cli_args.WATCH and cli_args.WATCH in self.BRIDGES:
                pprint(self.BRIDGES[cli_args.WATCH], compact=True)
            else:
                pprint(self.BRIDGES, compact=True, indent=4)
        
    def configSend(self,data):
        self.CONFIG = pickle.loads(data)
        pprint(self.CONFIG, compact=True)
        
    

class reportClientFactory(ReconnectingClientFactory):
    def __init__(self,proto):
        self.proto = proto
        
    def startedConnecting(self, connector):
        print('Started to connect.')

    def buildProtocol(self, addr):
        print('Connected.')
        print('Resetting reconnection delay')
        self.resetDelay()
        return self.proto()

    def clientConnectionLost(self, connector, reason):
        print('Lost connection.  Reason:', reason)
        ReconnectingClientFactory.clientConnectionLost(self, connector, reason)

    def clientConnectionFailed(self, connector, reason):
        print('Connection failed. Reason:', reason)
        ReconnectingClientFactory.clientConnectionFailed(self, connector,reason)

if __name__ == '__main__':
    
    from twisted.internet import reactor
    from setproctitle import setproctitle
    import signal
    import sys
    import os
    import argparse
    
    #Set process title early
    setproctitle(__file__)
        
    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))
    
    def sig_handler(_signal, _frame):
        print('SHUTDOWN: TERMINATING WITH SIGNAL {}'.format(str(_signal)))
        reactor.stop()
        
    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-e', '--events', action='store', dest='EVENTS', help='print events [0|1]')
    parser.add_argument('-c', '--config', action='store', dest='CONFIG', help='print config [0|1]')
    parser.add_argument('-b', '--bridges', action='store', dest='BRIDGES', help='print bridges [0|1]')
    parser.add_argument('-w', '--watch', action='store', dest='WATCH', help='watch bridge <name>')
    parser.add_argument('-o', '--host', action='store', dest='HOST', help='host to connect to <ip address>')
    parser.add_argument('-p', '--port', action='store', dest='PORT', help='port to connect to <port>')
    parser.add_argument('-s', '--stats', action='store', dest='STATS', help='print stats only') 
    
    cli_args = parser.parse_args()

    # Set signal handers so that we can gracefully exit if need be
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, sig_handler)
    
    reactor.connectTCP(cli_args.HOST,int(cli_args.PORT), reportClientFactory(reportClient))
    reactor.run()
