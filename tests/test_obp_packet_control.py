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


class TestStartGateReportOnly(unittest.TestCase):

    def test_obp_suppress_does_not_early_return(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('Report-only: losers skip START', source)
        idx = source.find('OBP *LoopControl* START RX suppressed')
        self.assertGreater(idx, 0)
        # No return between suppress log args and the CALL START else branch
        before_else = source[idx:source.find('\n                else:', idx)]
        self.assertNotIn('\n                    return\n', before_else)
        self.assertNotIn('\n                    return\r\n', before_else)

    def test_hbp_suppress_does_not_early_return(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        idx = source.find('HBP *LoopControl* START RX suppressed')
        self.assertGreater(idx, 0)
        window = source[idx:idx + 350]
        self.assertNotIn('return\n', window.split('else:')[0] if 'else:' in window else window)


if __name__ == '__main__':
    unittest.main()
