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

# The module needs logging logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)

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
