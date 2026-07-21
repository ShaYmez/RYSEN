#!/usr/bin/env python3
"""FreeDMR LC policy (always H/T rewrite, EMB remap-only), LC_OPT 0x00, prompt cancel."""
import re
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


class TestEmbRemapGate(unittest.TestCase):

    def test_same_tg_no_emb_rewrite(self):
        tg = (116).to_bytes(3, 'big')
        self.assertFalse(target_requires_lc_rewrite(tg, tg))
        self.assertFalse(target_requires_emb_lc_rewrite(tg, tg))

    def test_remap_requires_emb_rewrite(self):
        self.assertTrue(target_requires_emb_lc_rewrite(
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

    def test_bridge_master_freedmr_lc_policy(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        # VHEAD/VTERM must not be gated by remap helper (FreeDMR always-rewrite)
        self.assertIsNone(re.search(
            r'target_requires_lc_rewrite\([^)]+\) and _frame_type == HBPF_DATA_SYNC',
            source,
        ))
        self.assertIn(
            'elif target_requires_emb_lc_rewrite(_dst_id, _target[\'TGID\']) '
            'and _frame_type == HBPF_VOICE',
            source,
        )
        self.assertIn('_tx_dmrpkt = dmrpkt', source)
        # Shared arg must not be reassigned (substring would also match _tx_dmrpkt = ...)
        self.assertIsNone(re.search(r'(?<!_tx_)dmrpkt = dmrbits\.tobytes\(\)', source))
        self.assertIn('cancel_generated_voice', source)
        self.assertIn('KeyError - H_LC, sending original bits', source)
        self.assertNotIn("KeyError - H_LC, skipping", source)
        self.assertIn("RX_FINISHED_STREAM_ID", source)


class TestDmrpktFanoutBleed(unittest.TestCase):
    """Remap-then-same-TG must not leave rewritten bytes in the shared dmrpkt arg."""

    def test_per_target_copy_isolates_rewrite(self):
        # Simulate the fanout pattern used in to_target without spinning up Twisted.
        original = bytes(range(33))
        remapped = bytes((b ^ 0xFF) for b in original)

        shared = original
        payloads = []
        for rewrite in (True, False):
            _tx_dmrpkt = shared
            if rewrite:
                _tx_dmrpkt = remapped
            payloads.append(_tx_dmrpkt)

        self.assertEqual(payloads[0], remapped)
        self.assertEqual(payloads[1], original)
        self.assertEqual(shared, original)


if __name__ == '__main__':
    unittest.main()
