#!/usr/bin/env python3
"""Unit tests: dash activity START/END dedupe for multi-OBP / LOOPLOG legs."""
import unittest

from bridge_helpers import (
    earliest_obp_owner,
    should_report_obp_rx_start,
    should_report_hbp_rx_start,
    should_report_stream_end,
)


class TestEarliestObpOwner(unittest.TestCase):

    def test_empty(self):
        self.assertIsNone(earliest_obp_owner({}))
        self.assertIsNone(earliest_obp_owner(None))

    def test_picks_earliest(self):
        hr = {'OBP-PEER': 2.0, 'OBP-APOLLO': 1.5, 'OBP-EU': 3.0}
        self.assertEqual(earliest_obp_owner(hr), 'OBP-APOLLO')


class TestShouldReportObpRxStart(unittest.TestCase):

    def test_solo_path_reports(self):
        hr = {'OBP-PEER': 1.0}
        self.assertTrue(should_report_obp_rx_start('OBP-PEER', None, hr))

    def test_multi_obp_only_earliest_reports(self):
        hr = {'OBP-PEER': 1.0, 'OBP-APOLLO': 1.2}
        self.assertTrue(should_report_obp_rx_start('OBP-PEER', None, hr))
        self.assertFalse(should_report_obp_rx_start('OBP-APOLLO', None, hr))

    def test_hbp_owner_suppresses_all_obp(self):
        hr = {'OBP-PEER': 1.0}
        self.assertFalse(should_report_obp_rx_start('OBP-PEER', 'MASTER-UK', hr))

    def test_empty_hr_times_reports(self):
        self.assertTrue(should_report_obp_rx_start('OBP-PEER', None, {}))


class TestShouldReportHbpRxStart(unittest.TestCase):

    def test_solo_hbp_reports(self):
        self.assertTrue(should_report_hbp_rx_start(None, False))

    def test_other_hbp_suppresses(self):
        self.assertFalse(should_report_hbp_rx_start('MASTER-OTHER', False))

    def test_obp_inbound_suppresses(self):
        self.assertFalse(should_report_hbp_rx_start(None, True))


class TestShouldReportStreamEnd(unittest.TestCase):

    def test_normal_inbound_reports(self):
        self.assertTrue(should_report_stream_end({'START': 1.0, 'packets': 5}))

    def test_looplog_skips_end(self):
        self.assertFalse(should_report_stream_end({'LOOPLOG': True, 'START': 1.0}))

    def test_outbound_skips_end(self):
        self.assertFalse(should_report_stream_end({'_outbound': True, 'START': 1.0}))

    def test_none_status_reports(self):
        self.assertTrue(should_report_stream_end(None))
        self.assertTrue(should_report_stream_end({}))


class TestBridgeMasterWiresGates(unittest.TestCase):

    def test_obp_start_gated_before_report(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('should_report_obp_rx_start', source)
        self.assertIn('should_report_hbp_rx_start', source)
        self.assertIn('should_report_stream_end', source)
        self.assertIn('START RX suppressed', source)
        # Trimmer END must consult should_report_stream_end
        self.assertIn(
            "should_report_stream_end(_stream)",
            source)
        self.assertIn(
            "should_report_stream_end(_slot)",
            source)


if __name__ == '__main__':
    unittest.main()
