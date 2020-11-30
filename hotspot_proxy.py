from twisted.internet.protocol import DatagramProtocol
from twisted.internet import reactor, task
from time import time


class Proxy(DatagramProtocol):

    def __init__(self,ListenPort,connTrack,Timeout,Debug):
        self.connTrack = connTrack
        self.timeout = Timeout
        self.debug = Debug

    def datagramReceived(self, data, addr):
        host,port = addr
        
        Debug = self.debug
        
        #If the packet comes from the master
        if host == '127.0.0.1' and port in self.connTrack:
            if int(self.connTrack[port]['time'])+self.timeout > time():
                self.transport.write(data,(self.connTrack[port]['host'],self.connTrack[port]['sport']))
                #if master refuses login, remove tracking and block for timeout seconds
                if data == b'MSTNAK\x00#\xbf"':
                    self.connTrack[port]['time'] = False
                    self.connTrack[port]['nacktime'] = time()+self.timeout
                    if Debug:
                        print(data)
            return
        
        for dport in self.connTrack:
            #If blocked from refused login, ignore the packet if its been less than nacktime
            if int(self.connTrack[dport]['nacktime']) + self.timeout > time():
                if Debug:
                    print("NACK\n")
                return
            #If we have a conntrack for this connect and the timeout has not expired, forward to tracked port
            if self.connTrack[dport]['host'] == host and self.connTrack[dport]['sport'] == port and (int(self.connTrack[dport]['time'])+self.timeout > time()):
                self.connTrack[dport]['time'] = time()
                self.connTrack[dport]['host'] = host
                self.connTrack[dport]['sport'] = port
                self.transport.write(data, ('127.0.0.1',dport))
                self.connTrack[dport]['time'] = time()
                if Debug:
                    print(data)
                return
        
        #Find free port to map for new connection
        for dport in self.connTrack:
            if (self.connTrack[dport]['time'] == False or (int(self.connTrack[dport]['time'])+self.timeout < time())):
                self.connTrack[dport]['sport'] = port
                self.connTrack[dport]['host'] = host
                self.connTrack[dport]['time'] = time()
                self.transport.write(data, ('127.0.0.1',dport))
                if Debug:
                    print(data)
                return
     


if __name__ == '__main__':

#*** CONFIG HERE ***
    
    ListenPort = 62031
    DestportStart = 54001
    DestPortEnd = 54002
    Timeout = 35
    Stats = True
    Debug = False
    
#*******************

    
    CONNTRACK = {}

    for port in range(DestportStart,DestPortEnd,1):
        CONNTRACK[port] = {'host': False,'time': False,'sport':False, 'nacktime': False}

    reactor.listenUDP(ListenPort,Proxy(ListenPort,CONNTRACK,Timeout,Debug))

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
    
