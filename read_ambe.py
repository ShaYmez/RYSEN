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
from bitarray import bitarray
from itertools import islice
import os
import glob

class readAMBE:
    
    def __init__(self, lang,path):
        self.langcsv = lang
        self.langs = lang.split(',')
        self.path = path
 
    def _make_bursts(self,data):
        it = iter(data)
        for i in range(0, len(data), 108):
            yield bitarray([k for k in islice(it, 108)] )
    
    #Read indexed files
    def readfiles(self):
        
        _AMBE_LENGTH = 9
        
        _wordBADictofDicts = {}
            
        for _lang in self.langs:
            
            _prefix = self.path+_lang
            _wordBADict = {}
            
            indexDict = {}
            
            if os.path.isdir(_prefix):
                ambeBytearray = {}
                _wordBitarray = bitarray(endian='big')
                _wordBADict = {}
                _glob = _prefix + "/*.ambe"
                for ambe in glob.glob(_glob):
                    basename = os.path.basename(ambe)
                    _voice,ext = basename.split('.')
                    inambe = open(ambe,'rb')
                    _wordBitarray.frombytes(inambe.read())
                    inambe.close()
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
                _wordBADict['silence'] = ([
                        [bitarray('101011000000101010100000010000000000001000000000000000000000010001000000010000000000100000000000100000000000'),
                        bitarray('001010110000001010101000000100000000000010000000000000000000000100010000000100000000001000000000001000000000')]
                ])
                _wordBADictofDicts[_lang] = _wordBADict
            else:
                try:
                    with open(_prefix+'.indx') as index:
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
                    with open(_prefix+'.ambe','rb') as ambe:            
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
                        [bitarray('101011000000101010100000010000000000001000000000000000000000010001000000010000000000100000000000100000000000'),
                        bitarray('001010110000001010101000000100000000000010000000000000000000000100010000000100000000001000000000001000000000')]
                ])
                _wordBADictofDicts[_lang] = _wordBADict
        
        return _wordBADictofDicts
        
    #Read a single ambe file from the audio directory
    def readSingleFile(self,filename):
        ambeBytearray = {}
        _wordBitarray = bitarray(endian='big')
        _wordBA= []
        try:
            with open(self.path+filename,'rb') as ambe:            
                _wordBitarray.frombytes(ambe.read())
                #108
                _wordBA = []
                pairs = 1
                _lastburst = ''
                for _burst in self._make_bursts(_wordBitarray):
#Not sure if we need to pad or not? Seems to make little difference. 
                    if len(_burst) < 108:
                        pad = (108 - len(_burst))
                        for i in range(0,pad,1):
                            _burst.append(False)
                    if pairs == 2:
                        _wordBA.append([_lastburst,_burst])  
                        _lastburst = ''
                        pairs = 1
                        next
                    else:
                        pairs = pairs + 1
                        _lastburst = _burst
                    
                _wordBitarray.clear()
                ambe.close()
        except IOError:
            raise
        
        return(_wordBA)
        
  
if __name__ == '__main__':
    
    #test = readAMBE('en_GB','./Audio/')
    
    #print(test.readfiles())
    test = readAMBE('en_GB_2','./Audio/')
    print(test.readfiles())
    print(test.readSingleFile('44xx.ambe'))
