#!/usr/bin/env python3
"""Regression: try_download must not crash on import (Request, User-Agent)."""
import unittest
from unittest.mock import MagicMock, patch

import hblink


class TestTryDownloadImports(unittest.TestCase):

    @patch('hblink.urlopen')
    @patch('hblink.isfile', return_value=False)
    @patch('hblink.time', return_value=1000.0)
    def test_try_download_uses_request_without_name_error(self, _time, _isfile, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'{}')))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        result = hblink.try_download('/tmp/', 'peer_ids.json', 'https://example.test/peer_ids.json', 3600)
        self.assertNotIn('NameError', result)
        mock_urlopen.assert_called_once()
        request = mock_urlopen.call_args[0][0]
        self.assertTrue(any(k.lower() == 'user-agent' for k in request.headers))


if __name__ == '__main__':
    unittest.main()
