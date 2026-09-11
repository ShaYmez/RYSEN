#!/usr/bin/env python3
"""Regression guard for the alias refresh interval unit conversion."""
import unittest


class TestAliasReloadInterval(unittest.TestCase):

    def test_stale_time_seconds_are_not_converted_twice(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        alias_block = source[
            source.index('    #Alias reloader'):
            source.index('    #Options parsing')
        ]

        self.assertIn(
            "alias_time = CONFIG['ALIASES']['STALE_TIME']",
            alias_block,
        )
        self.assertNotIn("STALE_TIME'] * 86400", alias_block)


if __name__ == '__main__':
    unittest.main()
