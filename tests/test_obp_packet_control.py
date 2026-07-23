#!/usr/bin/env python3
"""PacketControl: FreeDMR OBP RATE DROP defaults (duration-based, not epoch)."""
import unittest

from bridge_helpers import (
    OBP_RATE_DROP_ENABLED,
    OBP_RATE_DROP_MIN_DURATION,
    OBP_RATE_DROP_MIN_PACKETS,
    OBP_RATE_DROP_MAX_PPS,
)


class TestFreeDmrObpRateDrop(unittest.TestCase):

    def test_flags_default_true(self):
        self.assertTrue(OBP_RATE_DROP_ENABLED)

    def test_freedmr_obp_thresholds(self):
        self.assertEqual(OBP_RATE_DROP_MIN_DURATION, 2.0)
        self.assertEqual(OBP_RATE_DROP_MIN_PACKETS, 50)
        self.assertEqual(OBP_RATE_DROP_MAX_PPS, 50)

    def test_bridge_master_uses_duration_gate(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('if OBP_RATE_DROP_ENABLED:', source)
        self.assertIn('OBP_RATE_DROP_MIN_DURATION', source)
        self.assertIn('OBP_RATE_DROP_MIN_PACKETS', source)
        self.assertIn('OBP_RATE_DROP_MAX_PPS', source)
        # Dead epoch formula must stay gone (packets / absolute START)
        self.assertNotIn("packets'] / self.STATUS[_stream_id]['START']", source)
        self.assertNotIn('packets / self.STATUS[_stream_id][\'START\']', source)


class TestLoopingErrHandleNoReactorStop(unittest.TestCase):

    def test_looping_err_handle_does_not_stop_reactor(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        start = source.find('def loopingErrHandle(failure):')
        self.assertGreater(start, 0)
        end = source.find('\n    # Initialize the rule timer', start)
        block = source[start:end]
        self.assertIn('Unhandled error in timed loop', block)
        self.assertNotIn('reactor.stop()', block)
        self.assertNotIn('STOPPING REACTOR', block)


class TestFiEmptyTreatsSelfAsOwner(unittest.TestCase):

    def test_group_fi_empty_does_not_hard_return(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        idx = source.find('OBP *LoopControl* fi is empty; treating this system as owner')
        self.assertGreater(idx, 0)
        window = source[idx:idx + 200]
        # Warning then elif loser path — no bare return right after empty-fi log
        self.assertNotIn('return\n                elif', window)
        before_elif = window.split('elif self._system != fi:')[0]
        self.assertNotIn('return', before_elif)

    def test_unit_fi_empty_does_not_hard_return(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        idx = source.find('OBP UNIT *LoopControl* fi is empty; treating this system as owner')
        self.assertGreater(idx, 0)
        window = source[idx:idx + 200]
        before_elif = window.split('elif self._system != fi:')[0]
        self.assertNotIn('return', before_elif)


if __name__ == '__main__':
    unittest.main()
