#!/usr/bin/env python3
"""Voice ident must not stick True on reused generator slots (All-Call 5000)."""
import unittest

from bridge_helpers import (
    apply_voice_ident_from_options,
    reset_slot_voice_ident,
    voice_ident_requested,
)


class TestVoiceIdentRequested(unittest.TestCase):

    def test_voice_one(self):
        self.assertTrue(voice_ident_requested('TS2=2350;VOICE=1;TIMER=60;'))

    def test_ident_alias(self):
        self.assertTrue(voice_ident_requested('IDENT=1;TS2=69;'))

    def test_voice_zero(self):
        self.assertFalse(voice_ident_requested(
            'TS1_STATIC=;TS2_STATIC=;SINGLE=1;DEFAULT_UA_TIMER=10;'
            'DEFAULT_REFLECTOR=0;VOICE=0;LANG=en_GB'))

    def test_missing_voice(self):
        self.assertFalse(voice_ident_requested('TS2=2350;TIMER=10;'))

    def test_empty(self):
        self.assertFalse(voice_ident_requested(''))
        self.assertFalse(voice_ident_requested(None))

    def test_userlink_not_voice(self):
        self.assertFalse(voice_ident_requested(
            'StartRef=4000;RelinkTime=60;Userlink=1;TS2_1=235;'))


class TestApplyVoiceIdentFromOptions(unittest.TestCase):

    def test_voice_one_sets_true(self):
        cfg = {'VOICE_IDENT': False}
        self.assertTrue(apply_voice_ident_from_options(cfg, {'VOICE': '1'}))
        self.assertTrue(cfg['VOICE_IDENT'])

    def test_voice_zero_clears_leftover(self):
        cfg = {'VOICE_IDENT': True}
        self.assertFalse(apply_voice_ident_from_options(cfg, {'VOICE': '0'}))
        self.assertFalse(cfg['VOICE_IDENT'])

    def test_missing_voice_clears_leftover(self):
        cfg = {'VOICE_IDENT': True}
        self.assertFalse(apply_voice_ident_from_options(cfg, {'TS2_STATIC': '2350'}))
        self.assertFalse(cfg['VOICE_IDENT'])

    def test_reset_slot(self):
        cfg = {'VOICE_IDENT': True, 'OPTIONS': 'VOICE=0;'}
        reset_slot_voice_ident(cfg)
        self.assertFalse(cfg['VOICE_IDENT'])


class TestIdentUsesOptionsString(unittest.TestCase):

    def test_ident_loop_reads_options_not_sticky_flag(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        ident = source[source.index('def ident():'):source.index('def options_config():')]
        self.assertIn('voice_ident_requested', ident)
        self.assertNotIn("['VOICE_IDENT'] == True", ident)


if __name__ == '__main__':
    unittest.main()
