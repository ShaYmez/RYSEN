#!/usr/bin/env python3
"""Tests for version ping and install_id."""
import os
import tempfile
import unittest
from unittest import mock

from rysen_trace import get_install_id, install_id_path, ping_version_async, post_version_ping
from rysen_version import __version__, user_agent


class TestInstallId(unittest.TestCase):

    def test_install_id_stable_when_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = install_id_path(tmp)
            os.makedirs(tmp, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('abc123')
            self.assertEqual(get_install_id(tmp), 'abc123')

    def test_install_id_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = get_install_id(tmp)
            second = get_install_id(tmp)
            self.assertEqual(first, second)
            self.assertTrue(os.path.isfile(install_id_path(tmp)))


class TestVersionPing(unittest.TestCase):

    @mock.patch('rysen_trace.urllib.request.urlopen')
    def test_post_version_ping_body(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{}'
        with tempfile.TemporaryDirectory() as tmp:
            post_version_ping(tmp)
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_full_url(), 'https://api.freestar.network/v1/rysen/ping.php')
        self.assertEqual(request.get_header('User-agent'), user_agent())

    @mock.patch('rysen_trace.post_version_ping', side_effect=OSError('network down'))
    def test_ping_async_silent_on_failure(self, _mock_post):
        ping_version_async('/tmp/nonexistent-rysen-log')


if __name__ == '__main__':
    unittest.main()
