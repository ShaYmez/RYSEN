#!/usr/bin/env python3
###############################################################################
#   Shared routing-master mode helpers
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

ROUTING_MASTER_MODES = frozenset(('MASTER', 'IPSC'))
REPEATER_PROTOCOL_MODES = frozenset(('IPSC', 'HYTERA'))
GENERATED_MASTER_MODES = ROUTING_MASTER_MODES | frozenset(('HYTERA',))


def is_routing_master(mode):
    """Return True for systems that participate as local routing masters."""
    return mode in ROUTING_MASTER_MODES


def is_repeater_protocol(mode):
    """Return True for native commercial repeater protocol masters."""
    return mode in REPEATER_PROTOCOL_MODES


def is_generated_master(mode):
    """Return True for modes that support isolated generated backend slots."""
    return mode in GENERATED_MASTER_MODES
