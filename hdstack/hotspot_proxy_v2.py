from twisted.internet.protocol import DatagramProtocol
from twisted.internet import reactor, task
from time import time
from resettabletimer import ResettableTimer
from dmr_utils3.utils import int_id
import random

class Proxy(DatagramProtocol):

    def __init__(self,Master,ListenPort,connTrack,blackList,Timeout,Debug,DestportStart,DestPortEnd):
        self.master = Master
        self.connTrack = connTrack
        self.peerTrack = {}
        self.timeout = Timeout
        self.debug = Debug
        self.blackList = blackList
        self.destPortStart = DestportStart
        self.destPortEnd = DestPortEnd
        self.numPorts = DestPortEnd - DestportStart + 1

    def cleanup_peer(self,_peer_id):
        _peer = self.peerTrack.get(_peer_id)
        if not _peer:
            return
        _timer = _peer.get('timer')
        if _timer:
            try:
                _timer.cancel()
            except Exception:
                pass
        self.reaper(_peer_id)
        
        
    def reaper(self,_peer_id):
        _peer = self.peerTrack.get(_peer_id)
        if not _peer:
            return
        if self.debug:
            print("dead",_peer_id)
        _dport = _peer.get('dport')
        if _dport in self.connTrack:
            self.transport.write(b'RPTCL'+_peer_id, ('127.0.0.1',_dport))
            self.connTrack[_dport] = False
        if _peer_id in self.peerTrack:
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
        
        host,port = addr
        _peer_id = False
        
        nowtime = time()
        
        Debug = self.debug
        
        #If the packet comes from the master
        if host == self.master:
            _command = data[:4]
            
            if _command == DMRD and len(data) >= 15:
                _peer_id = data[11:15]
            elif  _command == RPTA:
                    if len(data) >= 10 and data[6:10] in self.peerTrack:
                        _peer_id = data[6:10]
                    else:
                        _peer_id = self.connTrack.get(port)
            elif data[:6] == MSTNAK:
                    _peer_id = data[6:10] if len(data) >= 10 else False
                    if _peer_id:
                        self.cleanup_peer(_peer_id)
                    return
            elif _command == MSTN:
                    _peer_id = data[6:10] if len(data) >= 10 else False
                    if _peer_id:
                        self.cleanup_peer(_peer_id)
                    return
            elif _command == MSTP and len(data) >= 11:
                    _peer_id = data[7:11]
            elif _command == MSTC:
                    _peer_id = data[5:9] if len(data) >= 9 else False
                    if _peer_id:
                        self.cleanup_peer(_peer_id)
                    return
                
          #  _peer_id = self.connTrack[port]
            if self.debug:
                print(data)
            if _peer_id and _peer_id in self.peerTrack:
                self.transport.write(data,(self.peerTrack[_peer_id]['shost'],self.peerTrack[_peer_id]['sport']))
                #self.peerTrack[_peer_id]['timer'].reset()
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
            elif _command == RPTC:              # Repeater is sending it's configuration OR disconnecting
                if data[:5] == RPTCL:          # Disconnect command
                    _peer_id = data[5:9]
                else:
                    _peer_id = data[4:8]       # Configure Command
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
                self.transport.write(data, ('127.0.0.1',_dport))
                self.peerTrack[_peer_id]['timer'].reset()
                if self.debug:
                    print(data)
                return
            else:
                
                if int_id(_peer_id) in self.blackList:
                    return
                _ports_avail = [port for port in self.connTrack if not self.connTrack[port]]
                if not _ports_avail:
                    return
                _dport = random.choice(_ports_avail)
                self.connTrack[_dport] = _peer_id
                self.peerTrack[_peer_id] = {}
                self.peerTrack[_peer_id]['dport'] = _dport
                self.peerTrack[_peer_id]['sport'] = port
                self.peerTrack[_peer_id]['shost'] = host
                self.peerTrack[_peer_id]['timer'] = ResettableTimer(self.timeout,self.reaper,[_peer_id])
                self.peerTrack[_peer_id]['timer'].start()
                self.transport.write(data, (self.master,_dport))
                if self.debug:
                    print(data)
                return


if __name__ == '__main__':

#*** CONFIG HERE ***
    
    Master = "127.0.0.1"
    ListenPort = 62031
    DestportStart = 54000
    DestPortEnd = 54300
    Timeout = 30
    Stats = False
    Debug = False
    BlackList = [1234567]
    
#*******************

    
    CONNTRACK = {}

    for port in range(DestportStart,DestPortEnd+1,1):
        CONNTRACK[port] = False
    

    reactor.listenUDP(ListenPort,Proxy(Master,ListenPort,CONNTRACK,BlackList,Timeout,Debug,DestportStart,DestPortEnd))

    def loopingErrHandle(failure):
        print('(GLOBAL) STOPPING REACTOR TO AVOID MEMORY LEAK: Unhandled error innowtimed loop.\n {}'.format(failure))
        reactor.stop()
        
    def stats():        
        count = 0
        nowtime = time()
        for port in CONNTRACK:
            if CONNTRACK[port]:
                count = count+1
                
        totalPorts = DestPortEnd - DestportStart + 1
        freePorts = totalPorts - count
        
        print("{} ports out of {} in use ({} free)".format(count,totalPorts,freePorts))


        
    if Stats == True:
        stats_task = task.LoopingCall(stats)
        statsa = stats_task.start(30)
        statsa.addErrback(loopingErrHandle)

    reactor.run()
    
