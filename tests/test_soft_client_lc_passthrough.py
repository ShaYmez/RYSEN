#!/usr/bin/env python3
"""Same-TG full LC passthrough, LC_OPT 0x00, voice-prompt cancel."""
import unittest

from const import LC_OPT, LC_OPT_HBLINK_LEGACY
from bridge_helpers import (
    begin_generated_voice,
    cancel_generated_voice,
    end_generated_voice,
    generated_voice_cancelled,
    hbp_slot_prompt_defaults,
    target_requires_emb_lc_rewrite,
    target_requires_lc_rewrite,
)


class TestSameTgLcPassthrough(unittest.TestCase):

    def test_same_tg_no_rewrite(self):
        tg = (116).to_bytes(3, 'big')
        self.assertFalse(target_requires_lc_rewrite(tg, tg))
        self.assertFalse(target_requires_emb_lc_rewrite(tg, tg))

    def test_remap_requires_rewrite(self):
        self.assertTrue(target_requires_lc_rewrite(
            (116).to_bytes(3, 'big'),
            (9).to_bytes(3, 'big'),
        ))


class TestLcOptFreeDmr(unittest.TestCase):

    def test_normal_service_options_zero(self):
        self.assertEqual(LC_OPT, b'\x00\x00\x00')
        self.assertEqual(LC_OPT_HBLINK_LEGACY, b'\x00\x00\x20')


class TestPromptCancel(unittest.TestCase):

    def test_cancel_stops_generated_voice(self):
        slot = hbp_slot_prompt_defaults()
        token = begin_generated_voice(slot)
        self.assertTrue(slot['TX_PROMPT_ACTIVE'])
        self.assertFalse(generated_voice_cancelled(slot, token))
        cancel_generated_voice(slot)
        self.assertTrue(generated_voice_cancelled(slot, token))
        end_generated_voice(slot, token)
        self.assertFalse(slot['TX_PROMPT_ACTIVE'])

    def test_bridge_master_uses_same_tg_full_lc_gate(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('target_requires_lc_rewrite', source)
        self.assertIn('cancel_generated_voice', source)
        self.assertIn('KeyError - H_LC, sending original bits', source)
        self.assertNotIn("KeyError - H_LC, skipping", source)
        self.assertIn("RX_FINISHED_STREAM_ID", source)


if __name__ == '__main__':
    unittest.main()
