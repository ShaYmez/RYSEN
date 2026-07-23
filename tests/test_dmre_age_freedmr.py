#!/usr/bin/env python3
"""FreeDMR/ADN soft-client audio gates: DMRE age, BCSQ, HBP SEQ reset."""
import re
import unittest

from bridge_helpers import DMRE_MAX_PACKET_AGE_S


class TestDmreAgeFreeDmrAdn(unittest.TestCase):

    def test_age_is_five_seconds(self):
        self.assertEqual(DMRE_MAX_PACKET_AGE_S, 5.0)

    def test_hblink_age_sends_bcsq(self):
        with open('hblink.py', encoding='utf-8') as fh:
            source = fh.read()
        # Age discard must quench like FreeDMR/ADN
        self.assertIsNotNone(re.search(
            r"more than %\.0fs old!, discarding.*?self\.send_bcsq\(_dst_id,_stream_id\)",
            source,
            re.DOTALL,
        ))


class TestHbpNewStreamSeqReset(unittest.TestCase):

    def test_lastseq_reset_after_collision_gate(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        # Collision return must appear before lastSeq clear on the new-stream path
        marker = "collided with existing call"
        idx = source.find(marker)
        self.assertGreater(idx, 0)
        # Within the HBP new-stream block after first collision warning
        window = source[idx:idx + 800]
        self.assertIn("self.STATUS[_slot]['lastSeq'] = False", window)
        self.assertIn("self.STATUS[_slot]['lastData'] = False", window)
        # lastSeq reset must come after the collision return in this window
        ret_pos = window.find('return')
        seq_pos = window.find("['lastSeq'] = False")
        self.assertGreater(seq_pos, ret_pos)


if __name__ == '__main__':
    unittest.main()
