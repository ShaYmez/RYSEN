"""Hytera configuration parsing regressions."""

from pathlib import Path
from tempfile import NamedTemporaryFile
import unittest

import config
from hytera_const import DEFAULT_UNKNOWN_CAPTURE_FILE


ROOT = Path(__file__).resolve().parents[1]


class TestHyteraConfig(unittest.TestCase):

    def test_direct_master_parses_rdac_discovery(self):
        content = (ROOT / 'RYSEN-SAMPLE.cfg').read_text(encoding='utf-8')
        content = content.replace(
            '[IPSC]\nMODE: IPSC\nENABLED: False',
            '[IPSC]\nMODE: IPSC\nENABLED: True',
            1)
        content = content.replace(
            '[HYTERA]\nMODE: HYTERA\nENABLED: False',
            '[HYTERA]\nMODE: HYTERA\nENABLED: True',
            1)
        with NamedTemporaryFile(
                mode='w', encoding='utf-8', suffix='.cfg', delete=False) as fh:
            fh.write(content)
            config_path = Path(fh.name)
        try:
            parsed = config.build_config(str(config_path))
        finally:
            config_path.unlink(missing_ok=True)

        self.assertIn('HYTERA', parsed['SYSTEMS'])
        self.assertFalse(parsed['SYSTEMS']['HYTERA']['RDAC_DISCOVERY'])
        self.assertEqual(
            parsed['SYSTEMS']['HYTERA']['CAPTURE_FILE'],
            DEFAULT_UNKNOWN_CAPTURE_FILE)
        # Repeaters default off so dual statics stay subscribed; hangtime
        # serialises the RF slot. Homebrew MASTER hotspots stay last-TG-wins.
        self.assertFalse(parsed['SYSTEMS']['HYTERA']['SINGLE_MODE'])
        self.assertFalse(parsed['SYSTEMS']['IPSC']['SINGLE_MODE'])
        self.assertTrue(parsed['SYSTEMS']['SYSTEM']['SINGLE_MODE'])
        self.assertNotIn('ALIAS', parsed['SYSTEMS']['IPSC'])
        self.assertNotIn('ALIAS', parsed['SYSTEMS']['HYTERA'])
        self.assertNotIn('ALIAS', parsed['SYSTEMS']['SYSTEM'])


if __name__ == '__main__':
    unittest.main()
