"""Hytera configuration parsing regressions."""

from pathlib import Path
from tempfile import NamedTemporaryFile
import unittest

import config


ROOT = Path(__file__).resolve().parents[1]


class TestHyteraConfig(unittest.TestCase):

    def test_direct_master_parses_rdac_discovery(self):
        content = (
            (ROOT / 'RYSEN-SAMPLE.cfg').read_text(encoding='utf-8')
            + '\n'
            + (ROOT / 'HYTERA-SAMPLE.cfg').read_text(encoding='utf-8'))
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


if __name__ == '__main__':
    unittest.main()
