import json
import re
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

from tarkov_core import VERSION
from tarkov_core.modes import MODES

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_version_is_consistent(self):
        metadata = re.search(r'^version:\s*v?(\S+)', (ROOT/'metadata.yaml').read_text(encoding='utf-8'), re.M).group(1)
        registered = re.search(r"@register\(.*'([0-9][^']*)'\)", (ROOT/'main.py').read_text(encoding='utf-8')).group(1)
        released = re.search(r'^## ([0-9][^ ]*) - ', (ROOT/'CHANGELOG.md').read_text(encoding='utf-8'), re.M).group(1)
        self.assertEqual({metadata, registered, released, VERSION}, {VERSION})

    def test_schema_defaults_match_code_fallbacks(self):
        # main.py reads each key with a fallback; a fresh install uses the schema default. Keep them equal.
        schema = json.loads((ROOT/'_conf_schema.json').read_text(encoding='utf-8'))
        main = (ROOT/'main.py').read_text(encoding='utf-8')
        for key, spec in schema.items():
            with self.subTest(key=key):
                self.assertIn(f"'{key}'", main)
                fallback = re.search(rf"'{key}', ([^,)]+)", main)
                if fallback and spec['type'] in ('int', 'string', 'bool'):
                    self.assertEqual(fallback.group(1).strip(), repr(spec['default']))
        self.assertEqual(set(schema['default_mode']['options']), set(MODES))
        for spec in schema.values():
            if 'labels' in spec:
                self.assertEqual(len(spec['labels']), len(spec['options']))

    def test_package_contents(self):
        out = subprocess.run([sys.executable, str(ROOT/'tools'/'package_plugin.py')], capture_output=True, text=True, check=True).stdout
        path = Path(out.split(' entries:')[0])
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        self.assertIn('astrbot_tarkov_datasearch/main.py', names)
        self.assertIn('astrbot_tarkov_datasearch/tarkov_core/data/static.json', names)
        self.assertFalse([n for n in names if '/tests/' in n or n.endswith(('.sqlite3', '.pyc'))])
