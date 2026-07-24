#!/usr/bin/env python3
"""Parrot TG 9990 helpers — never OBP, never dial-a-tg / TG 9."""
import unittest

from bridge_helpers import (
    PARROT_TG,
    is_parrot_bridge,
    is_parrot_talkgroup,
    private_call_may_create_reflector,
)


class TestParrotHelpers(unittest.TestCase):

    def test_parrot_tg_constant(self):
        self.assertEqual(PARROT_TG, 9990)

    def test_is_parrot_talkgroup(self):
        self.assertTrue(is_parrot_talkgroup(9990))
        self.assertTrue(is_parrot_talkgroup('9990'))
        self.assertFalse(is_parrot_talkgroup(9))
        self.assertFalse(is_parrot_talkgroup(9991))

    def test_is_parrot_bridge(self):
        self.assertTrue(is_parrot_bridge('9990'))
        self.assertTrue(is_parrot_bridge('#9990'))
        self.assertFalse(is_parrot_bridge('2350'))
        self.assertFalse(is_parrot_bridge('#9'))

    def test_parrot_not_dial_reflector(self):
        self.assertFalse(private_call_may_create_reflector(9990, {}))


class TestParrotSourceGuards(unittest.TestCase):

    def test_make_stat_and_reflector_refuse_parrot(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if is_parrot_talkgroup(int_id(_tgid)):', source)
        self.assertIn('Refusing parrot TG 9990 as dial-a-tg reflector', source)
        self.assertIn('is_parrot_bridge(_bridge)', source)
        self.assertIn('_forward_parrot_unit_voice', source)
        self.assertNotIn('deferToThread', source)

    def test_playback_always_private_echo(self):
        with open('playback.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("if _call_type in ('group', 'unit'):", source)
        self.assertIn('_parrot_src = bytes_3(9990)', source)
        self.assertIn('_bits_out = i[15] | 0x40', source)


if __name__ == '__main__':
    unittest.main()
