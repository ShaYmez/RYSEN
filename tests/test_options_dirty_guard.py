#!/usr/bin/env python3
"""Regression guards for the reactor-blocking periodic OPTIONS scan."""
import unittest

from bridge_helpers import mark_options_dirty


class TestOptionsDirtyMarker(unittest.TestCase):

    def test_marks_shared_config(self):
        config = {}
        mark_options_dirty(config)
        self.assertIs(config['_OPTIONS_DIRTY'], True)


class TestOptionsParserGuard(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open('bridge_master.py', encoding='utf-8') as fh:
            cls.bridge_source = fh.read()
        with open('hblink.py', encoding='utf-8') as fh:
            cls.hblink_source = fh.read()

    def test_unchanged_periodic_tick_returns_before_full_scan(self):
        guard = "if not CONFIG.get('_OPTIONS_DIRTY', True):"
        clear = "CONFIG['_OPTIONS_DIRTY'] = False"
        loop = "for _system in CONFIG['SYSTEMS']:"
        options_block = self.bridge_source[
            self.bridge_source.index('def options_config():'):
            self.bridge_source.index('\n\n_selfcare_db = None')
        ]
        self.assertIn(guard, options_block)
        self.assertIn(clear, options_block)
        self.assertNotIn("CONFIG.pop('_OPTIONS_DIRTY', True)", options_block)
        self.assertLess(options_block.index(guard), options_block.index(clear))
        self.assertLess(options_block.index(clear), options_block.index(loop))

    def test_timer_change_does_not_rebuild_bridge_index(self):
        options_block = self.bridge_source[
            self.bridge_source.index('def options_config():'):
            self.bridge_source.index('\n\n_selfcare_db = None')
        ]
        timer_block = options_block[
            options_block.index("int(_options['DEFAULT_UA_TIMER']) != CONFIG"):
            options_block.index("int(_options['DEFAULT_REFLECTOR']) != CONFIG")
        ]
        self.assertNotIn('remove_bridge_system(_system)', timer_block)
        self.assertNotIn('rebuild_bridge_index()', timer_block)

    def test_sticky_and_link_ipsc_info_only_on_change(self):
        options_block = self.bridge_source[
            self.bridge_source.index('def options_config():'):
            self.bridge_source.index('\n\n_selfcare_db = None')
        ]
        self.assertIn("_prev_sticky = _peer.get('STICKY')", options_block)
        self.assertIn("_prev_link_ipsc = _peer.get('LINK_IPSC')", options_block)
        self.assertIn("if _peer['STICKY'] != _prev_sticky:", options_block)
        self.assertIn("if _link_slot != _prev_link_ipsc:", options_block)

    def test_parser_error_rearms_dirty_flag(self):
        self.assertIn(
            "logger.exception('(OPTIONS) caught exception: %s',e)\n"
            "            mark_options_dirty(CONFIG)",
            self.bridge_source,
        )

    def test_reset_pass_rearms_restored_options(self):
        options_block = self.bridge_source[
            self.bridge_source.index('def options_config():'):
            self.bridge_source.index('\n\n_selfcare_db = None')
        ]
        reset_block = options_block[
            options_block.index("if '_reset' in"):
            options_block.index('        try:')
        ]
        self.assertIn('mark_options_dirty(CONFIG)', reset_block)
        self.assertLess(
            reset_block.index('mark_options_dirty(CONFIG)'),
            reset_block.index('continue'),
        )

    def test_rpto_and_disconnect_paths_mark_dirty(self):
        self.assertIn('from bridge_helpers import (', self.hblink_source)
        self.assertIn('mark_options_dirty, dmr_seq_delta, reset_slot_voice_ident,',
                      self.hblink_source)
        self.assertGreaterEqual(
            self.hblink_source.count('mark_options_dirty(self._CONFIG)'),
            4,
        )

    def test_selfcare_and_ipsc_paths_mark_dirty(self):
        self.assertGreaterEqual(
            self.bridge_source.count('mark_options_dirty(CONFIG)'),
            4,
        )
        self.assertIn('mark_options_dirty(self._CONFIG)', self.bridge_source)


if __name__ == '__main__':
    unittest.main()
