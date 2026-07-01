#!/usr/bin/env python3
###############################################################################
#   IPSC repeater selfcare — Clients table access (mode = 0)
#   Pattern mirrors proxy/proxy_db.py; no schema migration required.
#
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

from twisted.enterprise import adbapi
from twisted.internet.defer import inlineCallbacks

IPSC_CLIENT_MODE = 0


class SelfcareDB:
    """MariaDB access for IPSC repeater rows in Clients (mode = 0)."""

    def __init__(self, host, user, password, db_name, port):
        self.db_name = db_name
        self.dbpool = adbapi.ConnectionPool(
            "MySQLdb", host, user, password, db_name,
            port=port, charset="utf8mb4",
        )

    @inlineCallbacks
    def test_db(self, reactor, logger):
        try:
            res = yield self.dbpool.runQuery("SELECT 1")
            if res:
                logger.info('(SELF SERVICE) Database connection test: OK')
        except Exception as err:
            if reactor.running:
                logger.error('(SELF SERVICE) Database connection error: %s', err)
                reactor.stop()
            else:
                raise SystemExit(f'(SELF SERVICE) Database connection error: {err}')

    @inlineCallbacks
    def upsert_ipsc_client(self, int_id, dmr_id, callsign, host, seed_options=None):
        """Register or refresh IPSC repeater row; preserve psswd/options on re-register."""
        flag_modified = 1 if seed_options else 0
        try:
            yield self.dbpool.runOperation(
                '''INSERT INTO Clients (
                    int_id, dmr_id, callsign, host, mode, logged_in, last_seen, options, modified
                ) VALUES (%s, %s, %s, %s, %s, 1, UNIX_TIMESTAMP(), %s, %s)
                ON DUPLICATE KEY UPDATE
                    callsign = VALUES(callsign),
                    host = VALUES(host),
                    mode = %s,
                    logged_in = 1,
                    last_seen = UNIX_TIMESTAMP(),
                    modified = IF(options IS NOT NULL AND TRIM(options) != '', 1, modified)''',
                (int_id, dmr_id, callsign, host, IPSC_CLIENT_MODE,
                 seed_options, flag_modified, IPSC_CLIENT_MODE),
            )
        except Exception as err:
            raise RuntimeError(f'upsert_ipsc_client error: {err}') from err

    @inlineCallbacks
    def logout_ipsc_client(self, int_id):
        try:
            yield self.dbpool.runOperation(
                'UPDATE Clients SET logged_in = 0 WHERE int_id = %s AND mode = %s',
                (int_id, IPSC_CLIENT_MODE),
            )
        except Exception as err:
            raise RuntimeError(f'logout_ipsc_client error: {err}') from err

    def select_modified_ipsc(self):
        return self.dbpool.runQuery(
            'SELECT int_id, options FROM Clients WHERE modified = 1 AND mode = %s',
            (IPSC_CLIENT_MODE,),
        )

    @inlineCallbacks
    def clear_modified(self, int_id):
        try:
            yield self.dbpool.runOperation(
                'UPDATE Clients SET modified = 0 WHERE int_id = %s AND mode = %s',
                (int_id, IPSC_CLIENT_MODE),
            )
        except Exception as err:
            raise RuntimeError(f'clear_modified error: {err}') from err


def build_ipsc_seed_options(system_cfg):
    """Build TS1=/TS2= options string from cfg static TG fields (first register)."""
    if not system_cfg:
        return None
    parts = []
    ts1 = system_cfg.get('TS1_STATIC') or ''
    ts2 = system_cfg.get('TS2_STATIC') or ''
    if ts1:
        parts.append(f'TS1={ts1}')
    if ts2:
        parts.append(f'TS2={ts2}')
    if not parts:
        return None
    return ';'.join(parts) + ';'


def find_ipsc_slot_for_radio_id(config_systems, radio_id):
    """Return IPSC-N slot name where connected peer RADIO_ID matches radio_id."""
    target = str(radio_id)
    for slot, syscfg in config_systems.items():
        if syscfg.get('MODE') != 'IPSC' or not syscfg.get('ENABLED'):
            continue
        for peer in syscfg.get('PEERS', {}).values():
            if str(peer.get('RADIO_ID')) == target:
                return slot
    return None
