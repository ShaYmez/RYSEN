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

# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Simon Adlem - G7RZU'
__copyright__  = 'Copyright (c) Simon Adlem, G7RZU 2022'
__credits__    = ''
__license__    = 'GNU GPLv3'
__maintainer__ = 'Simon Adlem G7RZU'
__email__      = 'simon@gb7fr.org.uk'

#This is example code to connect to the report service in FreeDMR / HBLink3
#It can be used as a skeleton to build logging and monitoring tools. 

import pickle

from twisted.internet import reactor
from twisted.internet.protocol import ReconnectingClientFactory
from twisted.protocols.basic import NetstringReceiver

import mysql.connector
from mysql.connector import errorcode

from reporting_const import *

class reportClient(NetstringReceiver):
    def __init__(self,db,reactor):
        self.db = db
        self.reactor = reactor
            
    def stringReceived(self, data):
        
        if data[:1] == REPORT_OPCODES['BRDG_EVENT']:
            self.bridgeEvent(data[1:].decode('UTF-8'))
        elif data[:1] == REPORT_OPCODES['CONFIG_SND']:
            self.configSend(data[1:])
        elif data[:1] == REPORT_OPCODES['BRIDGE_SND']:
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
            'duration'  : 0
             }

        if len(datalist) > 9:
            event['duration'] = datalist[9]
        
        
        #self.reactor.callInThread(self.send_mysql,event)
        self.send_mysql(event)
        
    def send_mysql(self,event):

        while not self.db.is_connected():
            try:
                self.db.reconnect()
            except mysql.connector.Error as err:
                print('(MYSQL) error on reconnect: {}'.format(err))    
                
        print("{} {} {} {} {} {} {} {} {}".format(event['type'],event['event'], event['trx'],event['system'],event['streamid'],event['peerid'],event['subid'],event['slot'],event['dstid'],event['duration']))
        _cursor = self.db.cursor()
        try:
            _cursor.execute("insert into feed values (NULL,'{}','{}','{}','{}','{}','{}','{}','{}','{}','{}')".format(event['type'],event['event'], event['trx'],event['system'],event['streamid'],event['peerid'],event['subid'],event['slot'],event['dstid'],event['duration']))
            self.db.commit()
        except mysql.connector.Error as err:
            _cursor.close()
            print('(MYSQL) error, problem with cursor execute: {}'.format(err))
            
        
    def bridgeSend(self,data):
        self.BRIDGES = pickle.loads(data)
        
    def configSend(self,data):
        self.CONFIG = pickle.loads(data)
        

class reportClientFactory(ReconnectingClientFactory):
    def __init__(self,proto,db,reactor):
        self.proto = proto
        self.db = db
        self.reactor = reactor
        
    def startedConnecting(self, connector):
        print('Started to connect.')

    def buildProtocol(self, addr):
        print('Connected.')
        print('Resetting reconnection delay')
        self.resetDelay()
        return self.proto(db,reactor)

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
    
    #Set process title early
    setproctitle(__file__)
        
    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))
    
    def sig_handler(_signal, _frame):
        print('SHUTDOWN: TERMINATING WITH SIGNAL {}'.format(str(_signal)))
        reactor.stop()

    # Set signal handers so that we can gracefully exit if need be
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, sig_handler)
        
    try:
        db = mysql.connector.connect(
            host=sys.argv[3],
            user=sys.argv[4],
            password=sys.argv[5],
            database=sys.argv[6],
            #pool_name = "master",
            #pool_size = 5
        )
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            sys.exit('(MYSQL) username or password error')
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            sys.exit('(MYSQL) DB Error')
        else:
            sys.exit('(MYSQL) error: %s',err)
            
        
    reactor.connectTCP(sys.argv[1],int(sys.argv[2]), reportClientFactory(reportClient,db,reactor))
    reactor.run()
