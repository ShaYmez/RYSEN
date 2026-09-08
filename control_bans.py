#!/usr/bin/env python3
"""Persistent Radio-ID bans for the loopback operator control API."""

from __future__ import annotations

import json
import os
import tempfile
import time


DEFAULT_PATH = os.environ.get(
    'FREESTAR_CONTROL_BANS', '/etc/rysen/freestar-control-bans.json')


def radio_id_core(radio_id) -> str:
    """Return the owner identity; a 9-digit ESSID is banned as its 7-digit ID."""
    if isinstance(radio_id, (bytes, bytearray)):
        radio_id = int.from_bytes(radio_id, 'big')
    try:
        digits = str(int(radio_id))
    except (TypeError, ValueError):
        digits = ''.join(ch for ch in str(radio_id) if ch.isdigit())
    if len(digits) == 9:
        return digits[:7]
    return digits


class ControlBanStore:
    def __init__(self, path=DEFAULT_PATH, now_fn=None, logger=None):
        self.path = path
        self.now_fn = now_fn or time.time
        self.logger = logger
        self.bans = {}
        self._load()

    def _load(self):
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding='utf-8') as fh:
                data = json.load(fh)
            rows = data.get('bans', data) if isinstance(data, dict) else {}
            if isinstance(rows, dict):
                self.bans = rows
            self.prune(save=False)
        except (OSError, ValueError) as err:
            self._warn('cannot load control bans: %s', err)

    def _warn(self, message, *args):
        if self.logger is not None:
            self.logger.warning('(CONTROL) ' + message, *args)

    def _save(self):
        if not self.path:
            return
        directory = os.path.dirname(self.path) or '.'
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix='.control-bans-', suffix='.json', dir=directory)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                    json.dump(
                        {'version': 1, 'bans': self.bans}, fh,
                        sort_keys=True, separators=(',', ':'))
                    fh.write('\n')
                os.replace(tmp_path, self.path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as err:
            # Runtime enforcement remains active even if persistence fails.
            self._warn('cannot persist control bans: %s', err)

    def prune(self, save=True):
        now = float(self.now_fn())
        expired = []
        for radio_id, entry in self.bans.items():
            try:
                expires_at = float(entry.get('expires_at') or 0)
            except (AttributeError, TypeError, ValueError):
                expires_at = 0
            if expires_at <= now:
                expired.append(radio_id)
        for radio_id in expired:
            self.bans.pop(radio_id, None)
        if expired and save:
            self._save()
        return len(expired)

    def get(self, radio_id):
        self.prune()
        return self.bans.get(radio_id_core(radio_id))

    def is_banned(self, radio_id) -> bool:
        return self.get(radio_id) is not None

    def ban(self, radio_id, duration_seconds=300, reason=''):
        core = radio_id_core(radio_id)
        if not core:
            raise ValueError('radio_id is required')
        duration = max(1, min(86400, int(duration_seconds)))
        entry = {
            'radio_id': int(core),
            'created_at': float(self.now_fn()),
            'expires_at': float(self.now_fn()) + duration,
            'reason': str(reason or '')[:256],
        }
        self.bans[core] = entry
        self._save()
        return dict(entry)

    def unban(self, radio_id) -> bool:
        removed = self.bans.pop(radio_id_core(radio_id), None) is not None
        if removed:
            self._save()
        return removed
