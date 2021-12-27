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


from twisted.protocols.basic import NetstringReceiver

# Socket-based reporting section
#
class report(NetstringReceiver):
    def __init__(self, factory):
        self._factory = factory

    def connectionMade(self):
        self._factory.clients.append(self)
        logger.info('(REPORT) freedmr reporting client connected: %s', self.transport.getPeer())

    def connectionLost(self, reason):
        logger.info('(REPORT) freedmr reporting client disconnected: %s', self.transport.getPeer())
        self._factory.clients.remove(self)

    def stringReceived(self, data):
        self.process_message(data)

    def process_message(self, _message):
        opcode = _message[:1]
        if opcode == REPORT_OPCODES['CONFIG_REQ']:
            logger.info('(REPORT) freedmr reporting client sent \'CONFIG_REQ\': %s', self.transport.getPeer())
            self.send_config()
        else:
            logger.error('(REPORT) got unknown opcode')
