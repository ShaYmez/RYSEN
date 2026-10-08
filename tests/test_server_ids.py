#!/usr/bin/env python3
"""Fleet server_ids registry accepts CSV, TSV, comments, and a BOM."""
import os
import tempfile
import unittest

import hblink


class TestServerIdsRegistry(unittest.TestCase):
    def _write(self, name, body, encoding='utf-8'):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, name)
        with open(path, 'w', encoding=encoding, newline='') as handle:
            handle.write(body)
        return folder + os.sep, name

    def test_tab_file_and_csv_with_comment_both_load(self):
        tsv_path, tsv_name = self._write(
            'server_ids.tsv',
            'Country\tOPB Net ID\tIP/Hostname\n'
            'Europe\t2040\teurope.example\n'
            'UK\t2342\tdmr.example\n')
        csv_path, csv_name = self._write(
            'server_ids.tsv',
            '#SystemX_Hosts.csv\n'
            'Country,"OPB Net ID",IP/Hostname\n'
            'Europe,2040,europe.example\n'
            'UK,2342,dmr.example\n')
        bom_path, bom_name = self._write(
            'server_ids.tsv',
            'Country,OPB Net ID,IP/Hostname\nUK,2342,dmr.example\n',
            encoding='utf-8-sig')
        self.assertEqual(
            hblink.mk_server_dict(tsv_path, tsv_name)['2040'], 'Europe')
        self.assertEqual(
            hblink.mk_server_dict(csv_path, csv_name)['2342'], 'UK')
        self.assertEqual(
            hblink.mk_server_dict(bom_path, bom_name)['2342'], 'UK')

    def test_failed_reload_keeps_previous_registry(self):
        config = {
            'ALIASES': {
                'TRY_DOWNLOAD': False,
                'PATH': '/no/such/',
                'SERVER_ID_FILE': 'missing.tsv',
                'PEER_FILE': 'missing.json',
                'SUBSCRIBER_FILE': 'missing.json',
                'TGID_FILE': 'missing.json',
                'LOCAL_SUBSCRIBER_FILE': 'missing.json',
            },
            '_SERVER_IDS': {'2040': 'Europe'},
        }
        _peer, _sub, _tg, _local, servers = hblink.mk_aliases(config)
        self.assertEqual(servers['2040'], 'Europe')


if __name__ == '__main__':
    unittest.main()
