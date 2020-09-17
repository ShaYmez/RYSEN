from bitarray import bitarray
from itertools import islice

class readAMBE:
    
    def __init__(self, lang,path):
        self.lang = lang
        self.path = path
        self.prefix = path+lang
    
    def _make_bursts(self,data):
        it = iter(data)
        for i in range(0, len(data), 108):
            yield bitarray([k for k in islice(it, 108)])
    
    def readfiles(self):
        _AMBE_LENGTH = 9
        indexDict = {}
        try:
            with open(self.prefix+'.indx') as index:
                for line in index:
                    (voice,start,length) = line.split()
                    indexDict[voice] = [int(start) * _AMBE_LENGTH ,int(length) * _AMBE_LENGTH]
            index.close()
        except IOError:
            return False
        
        ambeBytearray = {}
        _wordBitarray = bitarray(endian='big')
        _wordBADict = {}
        try:
            with open(self.prefix+'.ambe','rb') as ambe:            
                for _voice in indexDict:
                    ambe.seek(indexDict[_voice][0])
                    _wordBitarray.frombytes(ambe.read(indexDict[_voice][1]))
                    #108
                    _wordBADict[_voice] = []
                    pairs = 1
                    _lastburst = ''
                    for _burst in self._make_bursts(_wordBitarray):
 #Not sure if we need to pad or not? Seems to make little difference. 
                        if len(_burst) < 108:
                            pad = (108 - len(_burst))
                            for i in range(0,pad,1):
                                _burst.append(False)
                        if pairs == 2:
                            _wordBADict[_voice].append([_lastburst,_burst])  
                            _lastburst = ''
                            pairs = 1
                            next
                        else:
                            pairs = pairs + 1
                            _lastburst = _burst
                        
                    _wordBitarray.clear()
                ambe.close()
        except IOError:
            return False
        _wordBADict['silence'] = ([
                [bitarray('000000001010110000000000000000001010101000000000000000000100000000000000000000000010000000000000000000000'),
                 bitarray('0000000000000000000000001000100000000000000000001000000000000000000000010000000000000000000000010000000')]
        ])
        return _wordBADict
            
if __name__ == '__main__':
    
    test = readAMBE('en_GB','./Audio/')
    
    print(test.readfiles())
