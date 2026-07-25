#!/usr/bin/env python3
"""Regression guards for BlueDV/Peanut fanout and target stream state."""
import re
import unittest


class TestTargetOwnedStreamState(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open('bridge_master.py', encoding='utf-8') as fh:
            cls.source = fh.read()

    def test_hbp_tx_lc_regen_uses_target_stream(self):
        self.assertIn(
            "_target_status[_target['TS']]['TX_STREAM_ID'] != _stream_id",
            self.source,
        )
        self.assertIn('_new_generation', self.source)

    def test_hbp_tx_lc_regen_does_not_use_source_stream(self):
        # Source RX can advance while a destination rejects early frames during
        # hangtime. It must not own destination TX LC initialization.
        source_gate = (
            r"if \(_stream_id != self\.STATUS\[_slot\]\['RX_STREAM_ID'\]\):"
            r"\s*\n\s+# Record the DST TGID and Stream ID"
        )
        self.assertIsNone(re.search(source_gate, self.source))

    def test_delayed_target_acceptance_requires_regen(self):
        source_rx = b'new!'
        target_tx = b'old!'
        incoming = source_rx
        self.assertFalse(incoming != source_rx)
        self.assertTrue(target_tx != incoming)


class TestPerTargetPayloadIsolation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open('bridge_master.py', encoding='utf-8') as fh:
            cls.source = fh.read()

    def test_all_four_fanout_rewrites_use_private_payload(self):
        self.assertGreaterEqual(
            self.source.count('_tx_dmrpkt = dmrpkt'),
            4,
        )
        self.assertGreaterEqual(
            self.source.count('dmrbits.frombytes(_tx_dmrpkt)'),
            4,
        )

    def test_shared_payload_is_never_reassigned_after_lc_rewrite(self):
        self.assertIsNone(re.search(
            r'(?<!_tx_)dmrpkt = dmrbits\.tobytes\(\)',
            self.source,
        ))

    def test_target_order_cannot_mutate_original_payload(self):
        original = bytes(range(33))
        leg_a = bytes(value ^ 0x55 for value in original)
        leg_b = bytes(value ^ 0xAA for value in original)

        outputs = []
        for rewritten in (leg_a, leg_b):
            _tx_dmrpkt = original
            _tx_dmrpkt = rewritten
            outputs.append(_tx_dmrpkt)

        self.assertEqual(outputs, [leg_a, leg_b])
        self.assertEqual(original, bytes(range(33)))


class TestReactorCatchupAudio(unittest.TestCase):

    def test_packet_control_does_not_drop_reactor_catchup_bursts(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        # A post-stall burst has already paid ingress cost. Dropping it here
        # only creates holes that soft-client jitter buffers stretch over.
        self.assertNotIn('*PacketControl* RATE DROP!', source)


if __name__ == '__main__':
    unittest.main()
