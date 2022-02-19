
import sys

from time import time

from twisted.internet import reactor,task
from twisted.internet.defer import Deferred
from twisted.internet.protocol import ClientFactory,ClientFactory,Protocol
from twisted.protocols.basic import LineReceiver

class AMI():
    def __init__(self,host,port,username,secret,nodenum):
        self._AMIClient = self.AMIClient
        self.host = host
        self.port = port
        self.username = username.encode('utf-8')
        self.secret = secret.encode('utf-8')
        self.nodenum = str(nodenum)

    def send_command(self,command):
        self._AMIClient.command = command.encode('utf-8')
        self._AMIClient.username = self.username
        self._AMIClient.secret = self.secret
        self._AMIClient.nodenum = self.nodenum.encode('utf-8')
        self.command = command
        
        self.CF = reactor.connectTCP(self.host, self.port, self.AMIClientFactory(self._AMIClient))
    
    def closeConnection(self):
        self.transport.loseConnection()
            
    class AMIClient(LineReceiver):
        
        delimiter = b'\r\n'
        
        def connectionMade(self):
            self.sendLine(b'Action: login')
            self.sendLine(b''.join([b'Username: ',self.username]))
            self.sendLine(b''.join([b'Secret: ',self.secret]))
            self.sendLine(self.delimiter)
            
        def lineReceived(self,line):
            print(line)
            if line == b'Asterisk Call Manager/1.0':
                return
            
            if line == b'Response: Success':
                self.sendLine(b'Action: command')
                #print(b''.join([b'Command: ',b'rpt cmd ',self.nodenum,b' ',self.command]))
                self.sendLine(b''.join([b'Command: ',b'rpt cmd ',self.nodenum,b' ',self.command]))
                #self.sendLine(b'Command: ' + b'rpt cmd 29177 ilink 3 2001')
                self.sendLine(self.delimiter)
                self.transport.loseConnection()
                    
            
            
    class AMIClientFactory(ClientFactory):
        def __init__(self,AMIClient):
            #self.command = command
            self.done = Deferred()
            self.protocol = AMIClient
            #self.protocol.command = command
            
        def clientConnectionFailed(self, connector, reason):
            ClientFactory.clientConnectionLost(self, connector, reason)

        def clientConnectionLost(self, connector, reason):
            ClientFactory.clientConnectionLost(self, connector, reason)
        
            
if __name__ == '__main__':

    
    a = AMI(sys.argv[1],int(sys.argv[2]),'admin','llcgi',29177)
    #AMIOBJ.AMIClientFactory(AMIOBJ.AMIClient,'rpt cmd 29177 ilink 3 2001')
    a.send_command(sys.argv[3])
    reactor.run()
