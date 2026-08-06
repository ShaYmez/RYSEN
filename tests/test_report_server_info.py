#!/usr/bin/env python3
"""Tests for SERVER_INFO_SND report opcode and payload."""
import json
import unittest

from reporting_const import REPORT_OPCODES
from rysen_trace import REPORT_PROTOCOL, server_info_message, server_info_payload
from rysen_version import __version__


class TestReportServerInfo(unittest.TestCase):

    def test_opcode_defined(self):
        self.assertEqual(REPORT_OPCODES['SERVER_INFO_SND'], b'\x08')

    def test_existing_opcodes_unchanged(self):
        self.assertEqual(REPORT_OPCODES['CONFIG_SND'], b'\x01')
        self.assertEqual(REPORT_OPCODES['BRIDGE_SND'], b'\x03')
        self.assertEqual(REPORT_OPCODES['BRDG_EVENT'], b'\x07')

    def test_payload_shape(self):
        payload = server_info_payload(hostname='testhost', started_at=1000)
        self.assertEqual(payload['rysen_version'], __version__)
        self.assertEqual(payload['report_protocol'], REPORT_PROTOCOL)
        self.assertEqual(payload['hostname'], 'testhost')
        self.assertEqual(payload['started_at'], 1000)

    def test_message_is_valid_json(self):
        data = json.loads(server_info_message().decode('utf-8'))
        self.assertIn('rysen_version', data)
        self.assertEqual(data['rysen_version'], __version__)

    def test_bridge_master_sends_server_info(self):
        with open('bridge_master.py', encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('def send_server_info(self):', source)
        self.assertIn("REPORT_OPCODES['SERVER_INFO_SND']", source)
        self.assertIn('send_server_info()', source)


if __name__ == '__main__':
    unittest.main()
