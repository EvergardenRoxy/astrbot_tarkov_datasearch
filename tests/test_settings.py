import json
import unittest
from pathlib import Path

from tarkov_core.settings import GROUP_OF, GROUPS, Settings

ROOT = Path(__file__).resolve().parents[1]


class Raw(dict):
    saved = 0

    def save_config(self):
        self.saved += 1


class SettingsTests(unittest.TestCase):
    def test_grouped_values_are_found_by_their_flat_key(self):
        raw = Raw(boss={'boss_enabled': True, 'boss_modes': ['pve']}, general={'command_style': 'slash'})
        settings = Settings(raw)
        self.assertIs(settings.get('boss_enabled', False), True)
        self.assertEqual(settings.get('boss_modes'), ['pve'])
        self.assertEqual(settings['command_style'], 'slash')
        self.assertEqual(settings.get('status_enabled', 'fallback'), 'fallback')
        with self.assertRaises(KeyError):
            settings['status_enabled']

    def test_flat_values_still_work_and_the_group_wins(self):
        settings = Settings({'boss_enabled': True, 'boss': {'boss_enabled': False}, 'proxy_url': 'x'})
        self.assertIs(settings['boss_enabled'], False)
        self.assertEqual(settings['proxy_url'], 'x')

    def test_writes_go_where_the_value_lives_and_save_passes_through(self):
        raw = Raw(boss={'boss_portraits_update': True}, flat=1)
        settings = Settings(raw)
        settings['boss_portraits_update'] = False
        self.assertIs(raw['boss']['boss_portraits_update'], False)
        self.assertNotIn('boss_portraits_update', raw)
        settings['proxy_url'] = 'p'            # the group is missing: stays flat
        self.assertEqual(raw['proxy_url'], 'p')
        settings.update(boss_enabled=True)
        self.assertIs(raw['boss'].get('boss_enabled'), True)
        settings.save_config()
        self.assertEqual(raw.saved, 1)
        Settings({}).save_config()             # a plain dict has nothing to save

    def test_iteration_lists_flat_keys(self):
        settings = Settings({'boss': {'boss_enabled': True}, 'general': {'command_style': 'mention'}, 'extra': 1})
        self.assertEqual(sorted(settings), ['boss_enabled', 'command_style', 'extra'])
        self.assertEqual(len(settings), 3)

    def test_every_key_is_in_one_group_and_the_schema_page_is_short_and_grouped(self):
        self.assertEqual(len(GROUP_OF), sum(len(keys) for keys in GROUPS.values()))
        schema = json.loads((ROOT/'_conf_schema.json').read_text(encoding='utf-8'))
        self.assertEqual(list(schema), ['general', 'player', 'status', 'boss'])
        for group, spec in schema.items():
            for key, item in spec['items'].items():
                with self.subTest(key=key):
                    self.assertLessEqual(len(item['description']), 30)
                    self.assertLessEqual(len(item['hint']), 170)
        # Maintainer: the removed settings stay removed.
        keys = {key for spec in schema.values() for key in spec['items']}
        self.assertFalse(keys & {'short_commands_enabled', 'qq_official_compat_enabled'})
        self.assertEqual(schema['general']['items']['command_style']['options'], ['mention', 'slash'])
        for key, low, high, default in (('status_poll_minutes', 10, 90, 30), ('boss_poll_minutes', 10, 90, 30)):
            item = [spec['items'][key] for spec in schema.values() if key in spec['items']][0]
            self.assertEqual((item['slider']['min'], item['slider']['max'], item['default']), (low, high, default))
        self.assertIs(schema['player']['items']['name_index_enabled']['default'], True)
        self.assertEqual(schema['status']['items']['status_notify_targets']['default'], [])
