#!/usr/bin/env python
#
###############################################################################
#   Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
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

'''
This program does very little on its own. It is intended to be used as a module
to build applications on top of the HomeBrew Repeater Protocol. By itself, it
will only act as a peer or master for the systems specified in its configuration
file (usually freedmr.cfg). It is ALWAYS best practice to ensure that this program
works stand-alone before troubleshooting any applications that use it. It has
sufficient logging to be used standalone as a troubleshooting application.
'''

# Specifig functions from modules we need
from binascii import b2a_hex as ahex
from binascii import a2b_hex as bhex
from random import randint
from hashlib import sha256, sha1
from hmac import new as hmac_new, compare_digest
from time import time
from collections import deque

# Twisted is pretty important, so I keep it separate
from twisted.internet.protocol import DatagramProtocol, Factory, Protocol
from twisted.protocols.basic import NetstringReceiver
from twisted.internet import reactor, task

# Other files we pull from -- this is mostly for readability and segmentation
import FreeDMR.Utilities.log as log
import FreeDMR.Config.config as config
from FreeDMR.Const.const import *
from dmr_utils3.utils import int_id, bytes_4, try_download, mk_id_dict

# Imports for the reporting server
import pickle
from FreeDMR.Const.reporting_const import *

#Protocols
from FreeDMR.Protocol.OpenBridge import OPENBRIDGE
from FreeDMR.Protocol.HomeBrewProtocol import HBSYSTEM
from FreeDMR.Utilities.report import report
from FreeDMR.Utilities.reportFactory import reportFactory

# The module needs logging logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)

# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS, Forked by Simon Adlem - G7RZU'
__copyright__  = 'Copyright (c) 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group, Simon Adlem, G7RZU 2020,2021'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT; Jon Lee, G4TSN; Norman Williams, M6NBP'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Simon Adlem G7RZU'
__email__      = 'simon@gb7fr.org.uk'


# Global variables used whether we are a module or __main__
systems = {}

# Timed loop used for reporting HBP status
def config_reports(_config, _factory):
    def reporting_loop(_logger, _server):
        _logger.debug('(GLOBAL) Periodic reporting loop started')
        _server.send_config()

    logger.info('(GLOBAL) freedmr TCP reporting server configured')

    report_server = _factory(_config)
    report_server.clients = []
    reactor.listenTCP(_config['REPORTS']['REPORT_PORT'], report_server)

    reporting = task.LoopingCall(reporting_loop, logger, report_server)
    reporting.start(_config['REPORTS']['REPORT_INTERVAL'])

    return report_server


# Shut ourselves down gracefully by disconnecting from the masters and peers.
def freedmr_handler(_signal, _frame):
    for system in systems:
        logger.info('(GLOBAL) SHUTDOWN: DE-REGISTER SYSTEM: %s', system)
        systems[system].dereg()

# Check a supplied ID against the ACL provided. Returns action (True|False) based
# on matching and the action specified.
def acl_check(_id, _acl):
    id = int_id(_id)
    for entry in _acl[1]:
        if entry[0] <= id <= entry[1]:
            return _acl[0]
    return not _acl[0]

# ID ALIAS CREATION
# Download
def mk_aliases(_config):
    if _config['ALIASES']['TRY_DOWNLOAD'] == True:
        # Try updating peer aliases file
        result = try_download(_config['ALIASES']['PATH'], _config['ALIASES']['PEER_FILE'], _config['ALIASES']['PEER_URL'], _config['ALIASES']['STALE_TIME'])
        logger.info('(GLOBAL) %s', result)
        # Try updating subscriber aliases file
        result = try_download(_config['ALIASES']['PATH'], _config['ALIASES']['SUBSCRIBER_FILE'], _config['ALIASES']['SUBSCRIBER_URL'], _config['ALIASES']['STALE_TIME'])
        logger.info('(GLOBAL) %s', result)
        #Try updating tgid aliases file
        result = try_download(_config['ALIASES']['PATH'], _config['ALIASES']['TGID_FILE'], _config['ALIASES']['TGID_URL'], _config['ALIASES']['STALE_TIME'])
        logger.info('(GLOBAL) %s', result)

    # Make Dictionaries
    peer_ids = mk_id_dict(_config['ALIASES']['PATH'], _config['ALIASES']['PEER_FILE'])
    if peer_ids:
        logger.info('(GLOBAL) ID ALIAS MAPPER: peer_ids dictionary is available')

    subscriber_ids = mk_id_dict(_config['ALIASES']['PATH'], _config['ALIASES']['SUBSCRIBER_FILE'])
    if subscriber_ids:
        logger.info('(GLOBAL) ID ALIAS MAPPER: subscriber_ids dictionary is available')

    talkgroup_ids = mk_id_dict(_config['ALIASES']['PATH'], _config['ALIASES']['TGID_FILE'])
    if talkgroup_ids:
        logger.info('(GLOBAL) ID ALIAS MAPPER: talkgroup_ids dictionary is available')

    return peer_ids, subscriber_ids, talkgroup_ids
       
                    
      

#************************************************
#      MAIN PROGRAM LOOP STARTS HERE
#************************************************

if __name__ == '__main__':
    # Python modules we need
    import argparse
    import sys
    import os
    import signal

    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually freedmr.cfg)')
    parser.add_argument('-l', '--logging', action='store', dest='LOG_LEVEL', help='Override config file logging level.')
    cli_args = parser.parse_args()

    # Ensure we have a path for the config file, if one wasn't specified, then use the execution directory
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/freedmr.cfg'

    # Call the external routine to build the configuration dictionary
    CONFIG = config.build_config(cli_args.CONFIG_FILE)

    # Call the external routing to start the system logger
    if cli_args.LOG_LEVEL:
        CONFIG['LOGGER']['LOG_LEVEL'] = cli_args.LOG_LEVEL
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('\n\nCopyright (c) 2013, 2014, 2015, 2016, 2018, 2019\n\tThe Regents of the K0USY Group. All rights reserved.\n')
    logger.debug('(GLOBAL) Logging system started, anything from here on gets logged')

    # Set up the signal handler
    def sig_handler(_signal, _frame):
        logger.info('(GLOBAL) SHUTDOWN: freedmr IS TERMINATING WITH SIGNAL %s', str(_signal))
        freedmr_handler(_signal, _frame)
        logger.info('(GLOBAL) SHUTDOWN: ALL SYSTEM HANDLERS EXECUTED - STOPPING REACTOR')
        reactor.stop()

    # Set signal handers so that we can gracefully exit if need be
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, sig_handler)

    peer_ids, subscriber_ids, talkgroup_ids = mk_aliases(CONFIG)

    # INITIALIZE THE REPORTING LOOP
    if CONFIG['REPORTS']['REPORT']:
        report_server = config_reports(CONFIG, reportFactory)
    else:
        report_server = None
        logger.info('(REPORT) TCP Socket reporting not configured')

    # freedmr instance creation
    logger.info('(GLOBAL) freedmr \'freedmr.py\' -- SYSTEM STARTING...')
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
                systems[system] = OPENBRIDGE(system, CONFIG, report_server)
            else:
                systems[system] = HBSYSTEM(system, CONFIG, report_server)
            reactor.listenUDP(CONFIG['SYSTEMS'][system]['PORT'], systems[system], interface=CONFIG['SYSTEMS'][system]['IP'])
            logger.debug('(GLOBAL) %s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])

    reactor.run()
