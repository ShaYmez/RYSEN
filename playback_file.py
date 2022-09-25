#!/usr/bin/env python
#
###############################################################################
#   Copyright (C) 2016-2019  Cortney T. Buffington, N0MJS <n0mjs@me.com> (and Mike Zingman N4IRR)
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


# Python modules we need
import sys
from bitarray import bitarray
from time import time, sleep
from importlib import import_module
from random import randint
from setproctitle import setproctitle

# Twisted is pretty important, so I keep it separate
from twisted.internet.protocol import Factory, Protocol
from twisted.protocols.basic import NetstringReceiver
from twisted.internet import reactor, task

# Things we import from the main hblink module
from hblink import HBSYSTEM, systems, hblink_handler, reportFactory, REPORT_OPCODES, config_reports, mk_aliases
from dmr_utils3.utils import bytes_3, bytes_4, int_id, get_alias
from dmr_utils3 import decode, bptc, const
import config
import log
import const

from mk_voice import pkt_gen
from read_ambe import readAMBE

# The module needs logging logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)


# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Simon Adlem, based on work by Cortney T. Buffington, N0MJS and Mike Zingman, N4IRR'
__copyright__  = 'Copyright (c) 2022, Simon Adlem G7RZU, 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Cort Buffington, N0MJS'
__email__      = 'simon@gb7fr.org.uk'
__status__     = 'pre-alpha'

# Module gobal varaibles

def playFile(fileName,dstTG,subid):
    for system in systems:
        reactor.callInThread(playFileOnRequest,system,fileName,dstTG,subid)

def playFileOnRequest(system,fileName,dstTG,subid):
    _dst_id = bytes_3(dstTG)
    _source_id = bytes_3(subid)
    logger.debug('(%s) Sending contents of AMBE file: %s',system,fileName)
    sleep(1)
    _say = []
    try:

        _say.append(SILENCE)
        _say.append(SILENCE)
        _say.append(SILENCE)
        _say.append(AMBEobj.readSingleFile(fileName))
        _say.append(SILENCE)
        _say.append(SILENCE)
    except IOError as err:
        logger.warning('(%s) cannot read file %s: %s',system,fileName,err)
        return
    speech = pkt_gen(_source_id, _dst_id, bytes_4(5000), 0, _say)
    sleep(1)
    _slot  = systems[system].STATUS[1]
    while True:
        try:
            pkt = next(speech)
        except StopIteration:
                break
        #Packet every 60ms
        sleep(0.058)
        _stream_id = pkt[16:20]
        reactor.callFromThread(sendVoicePacket,systems[system],pkt,_source_id,_dst_id,_slot)
    logger.debug('(%s) Sending AMBE file %s end',system,fileName)
    
    if ONESHOT: 
        reactor.stop()
    
def sendVoicePacket(self,pkt,_source_id,_dest_id,_slot):
    _stream_id = pkt[16:20]
    _pkt_time = time()
    #if _stream_id not in systems[system].STATUS:
    #    systems[system].STATUS[_stream_id] = {
    #    'START':     _pkt_time,
    #    'CONTENTION':False,
    #    'RFS':       _source_id,
    #    'TGID':      _dest_id,
    #    'LAST':      _pkt_time
    #    }
    #    _slot['TX_TGID'] = _dest_id
    #else:
    #    systems[system].STATUS[_stream_id]['LAST'] = _pkt_time
    #    _slot['TX_TIME'] = _pkt_time
                                            
    self.send_system(pkt)

class playback(HBSYSTEM):
    def __init__(self, _name, _config, _report):
        HBSYSTEM.__init__(self, _name, _config, _report)

        # Status information for the system, TS1 & TS2
        # 1 & 2 are "timeslot"
        # In TX_EMB_LC, 2-5 are burst B-E
        self.STATUS = {
            1: {
                'RX_START':     time(),
                'RX_SEQ':       '\x00',
                'RX_RFS':       '\x00',
                'TX_RFS':       '\x00',
                'RX_STREAM_ID': '\x00',
                'TX_STREAM_ID': '\x00',
                'RX_TGID':      '\x00\x00\x00',
                'TX_TGID':      '\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      const.HBPF_SLT_VTERM,
                'RX_LC':        '\x00',
                'TX_H_LC':      '\x00',
                'TX_T_LC':      '\x00',
                'TX_EMB_LC': {
                    1: '\x00',
                    2: '\x00',
                    3: '\x00',
                    4: '\x00',
                }
                },
            2: {
                'RX_START':     time(),
                'RX_SEQ':       '\x00',
                'RX_RFS':       '\x00',
                'TX_RFS':       '\x00',
                'RX_STREAM_ID': '\x00',
                'TX_STREAM_ID': '\x00',
                'RX_TGID':      '\x00\x00\x00',
                'TX_TGID':      '\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      const.HBPF_SLT_VTERM,
                'RX_LC':        '\x00',
                'TX_H_LC':      '\x00',
                'TX_T_LC':      '\x00',
                'TX_EMB_LC': {
                    1: '\x00',
                    2: '\x00',
                    3: '\x00',
                    4: '\x00',
                }
            }
        }
        self.CALL_DATA = []
    


    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):
        pass
    


#************************************************
#      MAIN PROGRAM LOOP STARTS HERE
#************************************************

if __name__ == '__main__':
    
    import argparse
    import sys
    import os
    import signal
    from dmr_utils3.utils import try_download, mk_id_dict
    
    #Set process title early
    setproctitle(__file__)
    
    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually playback_file.cfg)')
    parser.add_argument('-l', '--logging', action='store', dest='LOG_LEVEL', help='Override config file logging level.')
    parser.add_argument('-f', '--file', action='store', dest='FILE', help='Filename to play')
    parser.add_argument('-o', '--oneshot', action='store', dest='ONESHOT', help='play once then exit [0|1]')
    parser.add_argument('-i', '--interval', action='store', dest='INTERVAL', help='play every N seconds')
    parser.add_argument('-t', '--talkgroup', action='store', dest='TALKGROUP', help='target talkgroup')
    parser.add_argument('-s', '--source', action='store', dest='SUBID', help='subscriber (source) ID')
    
    
    cli_args = parser.parse_args()

    # Ensure we have a path for the config file, if one wasn't specified, then use the default (top of file)
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/playback_file.cfg'

    # Call the external routine to build the configuration dictionary
    CONFIG = config.build_config(cli_args.CONFIG_FILE)
    
    FILE = cli_args.FILE
    ONESHOT = False
    INTERVAL = 120
    TALKGROUP = 9
    SUBID = int(cli_args.SUBID)
    if 'ONESHOT' in cli_args:
        ONESHOT = bool(cli_args.ONESHOT)
    #Minimum interval is every 120s Anything else is antisocial! 
    if 'INTERVAL' in cli_args and cli_args.INTERVAL and int(cli_args.INTERVAL) > 120:
        INTERVAL = int(cli_args.INTERVAL)
    if 'TALKGROUP' in cli_args:
        TALKGROUP = int(cli_args.TALKGROUP)
    
    # Start the system logger
    if cli_args.LOG_LEVEL:
        CONFIG['LOGGER']['LOG_LEVEL'] = cli_args.LOG_LEVEL
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('\n\nCopyright (c) 2022, Simon G7RZU based on work fromn - 2013, 2014, 2015, 2016, 2018, 2019\n\tThe Founding Members of the K0USY Group. All rights reserved.\n')
    logger.debug('Logging system started, anything from here on gets logged')
    
    # Set up the signal handler
    def sig_handler(_signal, _frame):
        logger.info('SHUTDOWN: HBROUTER IS TERMINATING WITH SIGNAL %s', str(_signal))
        hblink_handler(_signal, _frame)
        logger.info('SHUTDOWN: ALL SYSTEM HANDLERS EXECUTED - STOPPING REACTOR')
        reactor.stop()
        
    def loopingErrHandle(failure):
        logger.error('(GLOBAL) STOPPING REACTOR TO AVOID MEMORY LEAK: Unhandled error in timed loop.\n %s', failure)
        reactor.stop()
        
    # Set signal handers so that we can gracefully exit if need be
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, sig_handler)
    
    #ID ALIAS CREATION
    #Download
    #if CONFIG['ALIASES']['TRY_DOWNLOAD'] == True:
    #    Try updating peer aliases file
    #    result = try_download(CONFIG['ALIASES']['PATH'], CONFIG['ALIASES']['PEER_FILE'], CONFIG['ALIASES']['PEER_URL'], #CONFIG['ALIASES']['STALE_TIME'])
    #    logger.info(result)
    #    Try updating subscriber aliases file
    #    result = try_download(CONFIG['ALIASES']['PATH'], CONFIG['ALIASES']['SUBSCRIBER_FILE'], CONFIG['ALIASES']['SUBSCRIBER_URL'], CONFIG['ALIASES']['STALE_TIME'])
    #    logger.info(result)
        
    # Create the name-number mapping dictionaries
    #peer_ids, subscriber_ids, talkgroup_ids = mk_aliases(CONFIG)
    
    peer_ids = {}
    subscriber_ids = {}
    talkgroup_ids = {}
        
    # INITIALIZE THE REPORTING LOOP
    report_server = config_reports(CONFIG, reportFactory)    
    
    # HBlink instance creation
    logger.info('FreeDMR \'playback_file.py\' (c) 2022 Simon Adlem based on work from 2017-2019 Cort Buffington, N0MJS & Mike Zingman, N4IRR -- SYSTEM STARTING...')
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
                logger.critical('%s FATAL: Instance is mode \'OPENBRIDGE\', \n\t\t...Which would be tragic for playback, since it carries multiple call\n\t\tstreams simultaneously. playback.py onlyl works with MMDVM-based systems', system)
                sys.exit('playback.py cannot function with systems that are not MMDVM devices. System {} is configured as an OPENBRIDGE'.format(system))
            else:
                systems[system] = playback(system, CONFIG, report_server)
            reactor.listenUDP(CONFIG['SYSTEMS'][system]['PORT'], systems[system], interface=CONFIG['SYSTEMS'][system]['IP'])
            logger.debug('%s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])
            
    #Read AMBE
    AMBEobj = readAMBE(CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGES'],'./Audio/')
    AMBEobj.path = ('/')
    
    SILENCE = ([
                    [bitarray('101011000000101010100000010000000000001000000000000000000000010001000000010000000000100000000000100000000000'),
                    bitarray('001010110000001010101000000100000000000010000000000000000000000100010000000100000000001000000000001000000000')]
                ])
    if ONESHOT: 
        reactor.callLater(10,playFile,FILE,TALKGROUP,SUBID)
    else:
        logger.info('(PLAYBACK) Setting interval to %s seconds',INTERVAL)
        ambe_task = task.LoopingCall(playFile,FILE,TALKGROUP,SUBID)
        ambe = ambe_task.start(INTERVAL)
        ambe.addErrback(loopingErrHandle)
    
    reactor.run()
