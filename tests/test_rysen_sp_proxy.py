#!/usr/bin/env python3
import unittest

from hotspot_proxy_v2 import IsIPv4Address, IsIPv6Address


class TestRysenSpProxyHelpers(unittest.TestCase):

    def test_ipv4_valid(self):
        self.assertTrue(IsIPv4Address('127.0.0.1'))
        self.assertTrue(IsIPv4Address('192.168.0.1'))

    def test_ipv4_invalid(self):
        self.assertFalse(IsIPv4Address('not-an-ip'))
        self.assertFalse(IsIPv4Address('::1'))

    def test_ipv6_valid(self):
        self.assertTrue(IsIPv6Address('::1'))
        self.assertTrue(IsIPv6Address('2001:db8::1'))

    def test_ipv6_invalid(self):
        self.assertFalse(IsIPv6Address('192.168.1.1'))
        self.assertFalse(IsIPv6Address('not-an-ip'))


if __name__ == '__main__':
    unittest.main()
