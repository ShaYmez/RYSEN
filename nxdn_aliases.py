#!/usr/bin/env python3
###############################################################################
#   NXDN RadioID table, joined to DMR subscriber aliases by callsign.
#
#   Same pattern as YSF: DMR IDs come from subscriber_ids.json. NXDN IDs come
#   from nxdn.csv. The two tables meet on callsign. Stanza NXDN_ID is only the
#   fallback when that join misses.
#
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

'''Load RadioID nxdn.csv and map NXDN IDs to DMR IDs by callsign.'''

import csv
import logging
from io import StringIO
from os.path import isfile

from ysf_voice import callsign_for_id, callsign_index, id_for_callsign

logger = logging.getLogger(__name__)

DEFAULT_NXDN_URL = 'https://database.radioid.net/static/nxdn.csv'
DEFAULT_NXDN_FILE = 'nxdn_ids.csv'


def _callsign(value):
    if isinstance(value, bytes):
        value = value.decode('ascii', 'ignore')
    return str(value or '').strip().split()[0].upper()


def parse_nxdn_csv(text):
    """RADIO_ID,CALLSIGN,... -> {id: callsign} and {callsign: id}."""
    by_id = {}
    by_call = {}
    if not text:
        return by_id, by_call
    reader = csv.reader(StringIO(text))
    for row in reader:
        if not row or len(row) < 2:
            continue
        radio, callsign = row[0].strip(), _callsign(row[1])
        if not radio or radio.upper() in ('RADIO_ID', 'ID'):
            continue
        try:
            ident = int(radio) & 0xFFFF
        except (TypeError, ValueError):
            continue
        if not ident or not callsign:
            continue
        by_id[ident] = callsign
        by_call[callsign] = ident
    return by_id, by_call


def load_nxdn_file(path):
    if not path or not isfile(path):
        return {}, {}
    with open(path, 'r', encoding='latin1') as handle:
        return parse_nxdn_csv(handle.read())


def attach_nxdn_maps(config, by_id, previous=None):
    """Store NXDN maps on CONFIG. Keep the last good table if a reload is empty."""
    if not by_id and previous:
        logger.warning('(ALIAS) keeping previously loaded nxdn_ids')
        by_id = previous
    by_call = {_callsign(name): ident for ident, name in (by_id or {}).items()}
    config['_NXDN_IDS'] = by_id or {}
    config['_NXDN_CALL'] = by_call
    return by_id or {}


def attach_callsign_index(config):
    """Callsign -> DMR ID, rebuilt when alias tables reload."""
    config['_SUB_CALL'] = callsign_index(config)
    return config['_SUB_CALL']


def nxdn_id_for_dmr(config, dmr_id, default):
    """DMR subscriber ID -> NXDN ID via callsign, else stanza NXDN_ID."""
    callsign = callsign_for_id(config, int(dmr_id or 0), '')
    mapped = (config.get('_NXDN_CALL') or {}).get(callsign)
    if mapped:
        return int(mapped) & 0xFFFF
    return int(default or 0) & 0xFFFF


def dmr_id_for_nxdn(config, nxdn_id, default):
    """NXDN ID -> DMR subscriber ID via callsign, else the 16-bit NXDN ID."""
    callsign = (config.get('_NXDN_IDS') or {}).get(int(nxdn_id or 0) & 0xFFFF)
    if callsign:
        index = config.get('_SUB_CALL')
        if index is None:
            index = attach_callsign_index(config)
        mapped = id_for_callsign(index, callsign, 0)
        if mapped:
            return int(mapped)
    return int(default or nxdn_id or 0)
