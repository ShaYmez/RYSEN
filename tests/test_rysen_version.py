#!/usr/bin/env python3
"""Tests for rysen_version — single source of truth and PACKAGE_ID wire fingerprint."""
import os
import tempfile
import unittest

from rysen_version import (
    STOCK_PACKAGE_IDS,
    advertised_package_id,
    decode_package_id,
    read_version_file,
    user_agent,
    __version__,
)


class TestVersionSource(unittest.TestCase):

    def test_version_matches_version_txt(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        expected = read_version_file(os.path.join(repo_root, 'version.txt'))
        self.assertEqual(__version__, expected)

    def test_user_agent(self):
        self.assertEqual(user_agent(), 'RYSEN/{}'.format(__version__))

    def test_read_version_file_strips_whitespace(self):
        with tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8') as fh:
            fh.write('  9.9.9\n')
            path = fh.name
        try:
            self.assertEqual(read_version_file(path), '9.9.9')
        finally:
            os.unlink(path)


class TestAdvertisedPackageId(unittest.TestCase):

    def test_stock_system_x_becomes_rysen_version(self):
        out = advertised_package_id('SYSTEM-X')
        self.assertEqual(decode_package_id(out), 'RYSEN-{}'.format(__version__))

    def test_stock_mmdvm_system_x(self):
        out = advertised_package_id('MMDVM_SYSTEM-X')
        self.assertTrue(decode_package_id(out).startswith('RYSEN-'))

    def test_custom_package_unchanged(self):
        out = advertised_package_id('QUADNET-CUSTOM')
        self.assertEqual(decode_package_id(out), 'QUADNET-CUSTOM')

    def test_bytes_input(self):
        out = advertised_package_id(b'SYSTEM-X')
        self.assertEqual(decode_package_id(out), 'RYSEN-{}'.format(__version__))

    def test_padded_to_40_bytes(self):
        out = advertised_package_id('SYSTEM-X')
        self.assertEqual(len(out), 40)

    def test_stock_ids_set(self):
        self.assertIn('SYSTEM-X', STOCK_PACKAGE_IDS)
        self.assertIn('MMDVM_RYSEN', STOCK_PACKAGE_IDS)


if __name__ == '__main__':
    unittest.main()
