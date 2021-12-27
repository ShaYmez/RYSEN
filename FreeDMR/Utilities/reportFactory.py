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
# Twisted is pretty important, so I keep it separate
from twisted.internet.protocol import Factory

# Imports for the reporting server
import pickle
from FreeDMR.Const.reporting_const import *


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


class reportFactory(Factory):
    def __init__(self, config):
        self._config = config

    def buildProtocol(self, addr):
        if (addr.host) in self._config['REPORTS']['REPORT_CLIENTS'] or '*' in self._config['REPORTS']['REPORT_CLIENTS']:
            logger.debug('(REPORT) Permitting report server connection attempt from: %s:%s', addr.host, addr.port)
            return report(self)
        else:
            logger.error('(REPORT) Invalid report server connection attempt from: %s:%s', addr.host, addr.port)
            return None

    def send_clients(self, _message):
        for client in self.clients:
            client.sendString(_message)

    def send_config(self):
        serialized = pickle.dumps(self._config['SYSTEMS'], protocol=2) #.decode('utf-8', errors='ignore') #pickle.HIGHEST_PROTOCOL)
        logger.debug('(REPORT) Send config')
        self.send_clients(b''.join([REPORT_OPCODES['CONFIG_SND'], serialized]))
