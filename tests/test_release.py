import json
import re
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

from tarkov_core import VERSION
from tarkov_core.modes import GAME_MODES, MODES
from tarkov_core.settings import GROUPS

ROOT = Path(__file__).resolve().parents[1]


def flat_schema():
    """The schema's settings by key; the settings page groups them in nested objects (tarkov_core.settings.GROUPS)."""
    nested = json.loads((ROOT/'_conf_schema.json').read_text(encoding='utf-8'))
    flat = {}
    for group, spec in nested.items():
        assert spec['type'] == 'object' and spec['description'] and group in GROUPS, group
        for key, item in spec['items'].items():
            assert key in GROUPS[group] and key not in flat, key
            flat[key] = item
    return flat


class ReleaseTests(unittest.TestCase):
    def test_version_is_consistent(self):
        metadata = re.search(r'^version:\s*v?(\S+)', (ROOT/'metadata.yaml').read_text(encoding='utf-8'), re.M).group(1)
        registered = re.search(r"@register\(.*'([0-9][^']*)'\)", (ROOT/'main.py').read_text(encoding='utf-8')).group(1)
        released = re.search(r'^## ([0-9][^ ]*) - ', (ROOT/'CHANGELOG.md').read_text(encoding='utf-8'), re.M).group(1)
        self.assertEqual({metadata, registered, released, VERSION}, {VERSION})

    def test_footer_facts_match_the_repository(self):
        from tarkov_core import LICENSE, REPO
        repo = re.search(r'^repo:\s*(\S+)', (ROOT/'metadata.yaml').read_text(encoding='utf-8'), re.M).group(1)
        self.assertEqual(repo, REPO)
        self.assertIn('GNU GENERAL PUBLIC LICENSE', (ROOT/'LICENSE').read_text(encoding='utf-8'))
        self.assertIn(LICENSE, (ROOT/'README.md').read_text(encoding='utf-8'))

    def test_schema_defaults_match_code_fallbacks(self):
        # main.py reads each key with a fallback; a fresh install uses the schema default. Keep them equal.
        schema = flat_schema()
        main = (ROOT/'main.py').read_text(encoding='utf-8')
        for key, spec in schema.items():
            with self.subTest(key=key):
                self.assertIn(f"'{key}'", main)
                fallback = re.search(rf"'{key}', ([^,)]+)", main)
                if fallback and spec['type'] in ('int', 'string', 'bool'):
                    self.assertEqual(fallback.group(1).strip(), repr(spec['default']))
        # Arena is not a default mode; the separate "arena_by_default" switch adds it (maintainer).
        self.assertEqual(set(schema['default_mode']['options']), set(GAME_MODES))
        # Every setting belongs to exactly one group, and every grouped key exists in the schema.
        self.assertEqual(set(schema), {key for keys in GROUPS.values() for key in keys})
        for spec in schema.values():
            if 'labels' in spec:
                self.assertEqual(len(spec['labels']), len(spec['options']))

    def test_direct_imports_are_declared(self):
        requirements = (ROOT/'requirements.txt').read_text(encoding='utf-8')
        for package in ('aiohttp', 'yarl', 'pillow'):
            self.assertRegex(requirements, rf'(?m)^{package}\b')

    def test_package_contents(self):
        out = subprocess.run([sys.executable, str(ROOT/'tools'/'package_plugin.py')], capture_output=True, text=True, check=True).stdout
        path = Path(out.split(' entries:')[0])
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        self.assertIn('astrbot_tarkov_datasearch/main.py', names)
        self.assertIn('astrbot_tarkov_datasearch/tarkov_core/data/static.json', names)
        for font in ('NotoSansSC-Subset.ttf', 'NotoSansSC-Subset.chars.txt', 'Oswald-Variable.ttf', 'ShareTechMono-Regular.ttf',
                     'OFL-NotoSansSC.txt', 'OFL-Oswald.txt', 'OFL-ShareTechMono.txt'):
            self.assertIn('astrbot_tarkov_datasearch/tarkov_core/fonts/'+font, names)
        self.assertLess(path.stat().st_size, 1024*1024)
        self.assertFalse([n for n in names if '/tests/' in n or n.endswith(('.sqlite3', '.pyc'))])
