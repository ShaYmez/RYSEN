#!/usr/bin/env python3
"""PacketControl: wrap-aware SEQ and disabled OBP RATE DROP."""
import unittest

from bridge_helpers import OBP_RATE_DROP_ENABLED, HBP_RATE_DROP_ENABLED, dmrd_seq_delta


class TestDmrdSeqDelta(unittest.TestCase):

    def test_unset_last(self):
        self.assertIsNone(dmrd_seq_delta(5, False))
        self.assertIsNone(dmrd_seq_delta(5, None))

    def test_normal_advance(self):
        self.assertEqual(dmrd_seq_delta(6, 5), 1)
        self.assertEqual(dmrd_seq_delta(10, 5), 5)

    def test_duplicate(self):
        self.assertEqual(dmrd_seq_delta(5, 5), 0)

    def test_wrap_255_to_0(self):
        self.assertEqual(dmrd_seq_delta(0, 255), 1)

    def test_ooo_backwards(self):
        # 5 -> 3 wraps the long way: (3-5) % 256 = 254 > 127
        self.assertEqual(dmrd_seq_delta(3, 5), 254)
        self.assertGreater(dmrd_seq_delta(3, 5), 127)


class TestObpRateDropDisabled(unittest.TestCase):

    def test_flag_is_false(self):
        self.assertFalse(OBP_RATE_DROP_ENABLED)
        self.assertFalse(HBP_RATE_DROP_ENABLED)

    def test_bridge_master_gates_on_flag(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('OBP_RATE_DROP_ENABLED', source)
        self.assertIn('if OBP_RATE_DROP_ENABLED:', source)
        self.assertIn('if HBP_RATE_DROP_ENABLED:', source)
        # Must not unconditionally RATE DROP on OBP continuation
        self.assertIn('dmrd_seq_delta', source)


class TestStartGateOwnsFirstPacket(unittest.TestCase):
    """LoopControl losers must not route creating packet (DMO RX stretch fix)."""

    def test_obp_suppress_returns_before_route(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('do not route pkt1 into HBP/DMO', source)
        idx = source.find('OBP *LoopControl* START RX suppressed')
        self.assertGreater(idx, 0)
        # return must follow suppress log before CALL START / to_target fallthrough
        window = source[idx:idx + 500]
        self.assertIn('return', window)
        ret_idx = window.find('return')
        # CALL START for winners comes after the return branch ends
        self.assertLess(ret_idx, window.find('*CALL START*') if '*CALL START*' in window else len(window))

    def test_hbp_suppress_returns_before_route(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        idx = source.find('HBP *LoopControl* START RX suppressed')
        self.assertGreater(idx, 0)
        window = source[idx:idx + 400]
        self.assertIn('return', window)
        # No fall-through to CALL START on the suppress path
        before_call = window.split('*CALL START*')[0] if '*CALL START*' in window else window
        self.assertIn('\n                            return\n', before_call.replace('\r\n', '\n'))

    def test_rate_drop_still_disabled(self):
        self.assertFalse(OBP_RATE_DROP_ENABLED)
        self.assertFalse(HBP_RATE_DROP_ENABLED)
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertNotIn('FreeDMR parity', source)
        self.assertNotIn('still route the\n                # first packet', source)

if __name__ == '__main__':
    unittest.main()
