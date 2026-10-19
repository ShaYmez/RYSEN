#!/usr/bin/env python3
"""Regression tests for alias reload hardening."""
import unittest

from hblink import coalesce_alias_dict


class TestCoalesceAliasDict(unittest.TestCase):

    def test_prefers_new_non_empty_dict(self):
        previous = {1: 'OLD'}
        new = {2: 'NEW'}
        self.assertEqual(
            coalesce_alias_dict(new, previous, 'subscriber_ids'),
            new,
        )

    def test_keeps_previous_when_reload_empty(self):
        previous = {2350001: 'M0ABC'}
        self.assertEqual(
            coalesce_alias_dict({}, previous, 'subscriber_ids'),
            previous,
        )

    def test_empty_when_no_previous(self):
        self.assertEqual(coalesce_alias_dict({}, {}, 'peer_ids'), {})

    def test_keeps_previous_when_reload_none(self):
        previous = {99: 'GB7NR'}
        self.assertEqual(
            coalesce_alias_dict(None, previous, 'peer_ids'),
            previous,
        )


class TestAliasLoopDeferredStart(unittest.TestCase):

    def test_alias_looping_call_waits_for_first_interval(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        alias_block = source[
            source.index('    #Alias reloader'):
            source.index('    #Options parsing')
        ]
        self.assertIn('aliasa_task.start(alias_time, now=False)', alias_block)


if __name__ == '__main__':
    unittest.main()
