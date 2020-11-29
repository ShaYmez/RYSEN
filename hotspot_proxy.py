from twisted.internet.protocol import DatagramProtocol
from twisted.internet import reactor, task
from time import time


class Proxy(DatagramProtocol):

    def __init__(self,ListenPort,connTrack,Timeout):
        self.connTrack = connTrack
        self.timeout = Timeout

    def datagramReceived(self, data, addr):
        host,port = addr
        
        if host == '127.0.0.1' and port in self.connTrack:
            if int(self.connTrack[port]['time'])+self.timeout > time():
                self.transport.write(data,(self.connTrack[port]['host'],self.connTrack[port]['sport']))
            return
        
        for dport in self.connTrack:
            if self.connTrack[dport]['host'] == host and self.connTrack[dport]['sport'] == port and (int(self.connTrack[dport]['time'])+self.timeout > time()):
                self.connTrack[dport]['time'] = time()
                self.connTrack[dport]['host'] = host
                self.connTrack[dport]['sport'] = port
                self.transport.write(data, ('127.0.0.1',dport))
                self.connTrack[dport]['time'] = time()
                return
            
        for dport in self.connTrack:
            if (self.connTrack[dport]['time'] == False or (int(self.connTrack[dport]['time'])+self.timeout < time())):
                self.connTrack[dport]['sport'] = port
                self.connTrack[dport]['host'] = host
                self.connTrack[dport]['time'] = time()
                self.transport.write(data, ('127.0.0.1',dport))
                return
     


if __name__ == '__main__':

#*** CONFIG HERE ***
    
    ListenPort = 62031
    DestportStart = 50000
    DestPortEnd = 50500
    Timeout = 35
    Stats = True
    
#*******************

    
    CONNTRACK = {}

    for port in range(DestportStart,DestPortEnd,1):
        CONNTRACK[port] = {'host': False,'time': False,'sport':False}

    reactor.listenUDP(ListenPort,Proxy(ListenPort,CONNTRACK,Timeout))

    def loopingErrHandle(failure):
        print('(GLOBAL) STOPPING REACTOR TO AVOID MEMORY LEAK: Unhandled error in timed loop.\n {}'.format(failure))
        reactor.stop()
        
    def stats():        
        count = 0
        for port in CONNTRACK:
            if int(CONNTRACK[port]['time'])+Timeout > time():
                count = count+1
                
        totalPorts = DestPortEnd - DestportStart
        freePorts = totalPorts - count
        
        print("{} ports out of {} in use ({} free)".format(count,totalPorts,freePorts))


        
    if Stats == True:
        stats_task = task.LoopingCall(stats)
        statsa = stats_task.start(30)
        statsa.addErrback(loopingErrHandle)

    reactor.run()
    
