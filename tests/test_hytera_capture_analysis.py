"""Offline Hytera capture analyser regressions."""

from io import BytesIO
import struct
import unittest

from tools.analyze_hytera_capture import _parse_timestamp, _pcap_records


def pcap(magic, fraction):
    header = magic + struct.pack('<HHIIII', 2, 4, 0, 0, 65535, 1)
    frame = b'test'
    record = struct.pack('<IIII', 100, fraction, len(frame), len(frame))
    return BytesIO(header + record + frame)


class TestHyteraCaptureAnalysis(unittest.TestCase):

    def test_classic_pcap_microsecond_timestamp(self):
        record = next(_pcap_records(pcap(b'\xd4\xc3\xb2\xa1', 250000)))
        self.assertEqual(record[0], 100.25)

    def test_classic_pcap_nanosecond_timestamp(self):
        record = next(_pcap_records(pcap(b'\x4d\x3c\xb2\xa1', 250000000)))
        self.assertEqual(record[0], 100.25)

    def test_iso_timestamp_accepts_utc_suffix(self):
        self.assertEqual(
            _parse_timestamp('1970-01-01T00:01:40.250000Z'), 100.25)


if __name__ == '__main__':
    unittest.main()
