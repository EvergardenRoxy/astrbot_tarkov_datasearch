"""SDK boundary doubles, not a claim of a running AstrBot installation."""
import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures import profile

ROOT = Path(__file__).resolve().parents[1]


class Event:
    unified_msg_origin = 'platform:group:1'
    admin = False

    def __init__(self, text='/tarkov pve Test_Player', sender='alice'):
        self.message_str = text
        self.sender = sender
        self.stopped = False

    def get_sender_id(self): return self.sender
    def plain_result(self, text): return text
    def is_admin(self): return self.admin
    def stop_event(self): self.stopped = True


class FakeNames:
    """Stands in for NameIndex: a fixed name table per mode path."""
    def __init__(self, table=None, refresh=False, indexed=True, refresh_seconds=168*3600):
        self.table = table or {}
        self.refresh = refresh
        self.indexed = indexed
        self.refresh_seconds = refresh_seconds
        self.lookups = []

    def needs_refresh(self, mode_path): return self.refresh

    def has_index(self, mode_path): return self.indexed

    async def lookup(self, mode_path, name):
        self.lookups.append((mode_path, name))
        found = [entry for entry in self.table.get(mode_path, []) if entry[1].lower() == name.lower()]
        found.sort(key=lambda entry: (entry[1] != name, entry[0]))
        return found[:6], len(found)


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        api = types.ModuleType('astrbot.api')
        api.AstrBotConfig = dict
        self.logs = []
        api.logger = types.SimpleNamespace(**{level: (lambda *a: self.logs.append(a)) for level in ('warning', 'info', 'debug')})
        event = types.ModuleType('astrbot.api.event')
        decorator = lambda *a, **k: lambda f: f
        event.filter = types.SimpleNamespace(command=decorator)
        event.AstrMessageEvent = Event
        star = types.ModuleType('astrbot.api.star')

        class Star:
            def __init__(self, context): self.context = context
        star.Star = Star
        star.Context = object
        star.StarTools = types.SimpleNamespace(get_data_dir=lambda name: Path(self.temp.name))
        star.register = decorator
        self.modules = patch.dict(sys.modules, {'astrbot': types.ModuleType('astrbot'), 'astrbot.api': api,
                                                'astrbot.api.event': event, 'astrbot.api.star': star})
        self.modules.start()
        package = types.ModuleType('tarkov_test_plugin')
        package.__path__ = [str(ROOT)]
        sys.modules['tarkov_test_plugin'] = package
        spec = importlib.util.spec_from_file_location('tarkov_test_plugin.main', ROOT/'main.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.transport = sys.modules['tarkov_test_plugin.tarkov_core.transport']
        self.fetched = []
        self.profiles = {}

    def tearDown(self):
        self.modules.stop()
        for name in [n for n in sys.modules if n.startswith('tarkov_test_plugin')]:
            del sys.modules[name]
        self.temp.cleanup()

    async def fake_fetch(self, mode_path, aid, **kwargs):
        self.fetched.append((mode_path, aid))
        if (mode_path, aid) not in self.profiles:
            raise self.transport.NotFound('not found')
        return self.profiles[(mode_path, aid)]

    def plugin(self, config=None, names=None):
        plugin = self.module.TarkovPlugin(types.SimpleNamespace(), config or {})
        plugin.names = names or FakeNames()
        return plugin

    async def run_command(self, plugin, event):
        with patch.object(self.transport, 'fetch_profile', self.fake_fetch):
            return [r async for r in plugin.tarkov_command(event)]

    async def test_help(self):
        for text in ('/tarkov', 'tarkov help', '/塔科夫 帮助', '/tarkov pve'):
            with self.subTest(text=text):
                event = Event(text)
                result = await self.run_command(self.plugin(), event)
                self.assertIn('用法：/tarkov [模式] <昵称或账号ID>', result[0])
                self.assertTrue(event.stopped)

    async def test_query_by_id(self):
        self.profiles[('pve', 1234567)] = profile()
        result = await self.run_command(self.plugin(), Event('/tarkov pve 1234567'))
        self.assertEqual(len(result), 1)
        self.assertIn('【塔科夫 PVE 档案】Test_Player（账号 ID 1234567）', result[0])
        self.assertEqual(self.fetched, [('pve', 1234567)])

    async def test_query_by_name_uses_the_mode_index(self):
        self.profiles[('profile', 1234567)] = profile()
        names = FakeNames({'profile': [(1234567, 'Test_Player')]})
        result = await self.run_command(self.plugin(names=names), Event('/tarkov test_player'))
        self.assertIn('【塔科夫 PVP 档案】Test_Player', result[-1])
        self.assertEqual(names.lookups, [('profile', 'test_player')])
        self.assertNotIn('注意', result[-1])

    async def test_default_mode_setting(self):
        self.profiles[('pvp-season', 1234567)] = profile()
        result = await self.run_command(self.plugin({'default_mode': 'season'}), Event('/tarkov 1234567'))
        self.assertIn('PVP 赛季', result[0])
        # An unknown saved value falls back to PVP.
        self.profiles[('profile', 1234567)] = profile()
        result = await self.run_command(self.plugin({'default_mode': 'bogus'}), Event('/tarkov 1234567'))
        self.assertIn('PVP 档案', result[0])

    async def test_refresh_notice_comes_first(self):
        self.profiles[('pve', 1)] = profile(aid=1)
        names = FakeNames({'pve': [(1, 'Test_Player')]}, refresh=True)
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pve Test_Player'))
        self.assertIn('正在检查 PVE 玩家名单的更新', result[0])
        self.assertIn('档案', result[1])

    async def test_name_not_found(self):
        result = await self.run_command(self.plugin(), Event('/tarkov pve Nobody_Here'))
        self.assertIn('名单里找不到 Nobody_Here', result[0])
        self.assertIn('用 ID 查询', result[0])
        self.assertEqual(self.fetched, [])

    async def test_ambiguous_names_are_listed(self):
        names = FakeNames({'pve': [(1, 'Same'), (2, 'SAME')]})
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pve same'))
        self.assertIn('多个名为 same', result[0])
        self.assertIn('Same（ID 1）', result[0])
        self.assertEqual(self.fetched, [])

    async def test_exact_case_wins_among_variants(self):
        self.profiles[('pve', 2)] = profile(aid=2, info_nickname='SAME')
        names = FakeNames({'pve': [(1, 'Same'), (2, 'SAME')]})
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pve SAME'))
        self.assertIn('SAME（账号 ID 2）', result[0])

    async def test_long_candidate_lists_say_there_are_more(self):
        names = FakeNames({'pve': [(i, 'same') for i in range(1, 9)]})
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pve Same'))
        self.assertEqual(result[0].count('（ID '), 5)
        self.assertIn('等，共 8 个', result[0])

    async def test_two_exact_matches_are_still_ambiguous(self):
        names = FakeNames({'pve': [(1, 'Same'), (2, 'Same')]})
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pve Same'))
        self.assertIn('多个名为 Same', result[0])
        self.assertEqual(self.fetched, [])

    async def test_renamed_player_is_noted(self):
        self.profiles[('pve', 5)] = profile(aid=5, info_nickname='NewName')
        names = FakeNames({'pve': [(5, 'OldName')]})
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pve OldName'))
        self.assertIn('档案里的昵称是 NewName', result[0])

    async def test_missing_id_profile(self):
        result = await self.run_command(self.plugin(), Event('/tarkov 赛季 12'))
        self.assertIn('PVP 赛季 模式下没有账号 ID 12 的档案', result[0])

    async def test_digits_missing_as_id_and_as_name(self):
        result = await self.run_command(self.plugin(), Event('/tarkov 赛季 11505337'))
        self.assertEqual(len(result), 1)
        self.assertIn('没有账号 ID 11505337 的档案，名单里也没有叫 11505337 的玩家', result[0])

    async def test_refresh_notice_mentions_size_only_for_a_first_pvp_download(self):
        # Review F-20: the size note is only true when there is no index yet.
        for text, indexed, size in (('/tarkov pve Nobody', False, False), ('/tarkov pvp Nobody', False, True),
                                    ('/tarkov pvp Nobody', True, False)):
            with self.subTest(text=text, indexed=indexed):
                result = await self.run_command(self.plugin(names=FakeNames(refresh=True, indexed=indexed)), Event(text))
                self.assertEqual('30 MB' in result[0], size)
                self.assertIn('下载' if not indexed else '检查', result[0])

    async def test_texts_follow_the_refresh_interval(self):
        # Review F-05: the chat texts used to say "每周" whatever the setting.
        for seconds, words in ((168*3600, '每周'), (24*3600, '每天'), (72*3600, '每 3 天'), (336*3600, '每 2 周'), (5*3600, '每 5 小时')):
            with self.subTest(words=words):
                plugin = self.plugin(names=FakeNames(refresh=True, refresh_seconds=seconds))
                self.assertIn(f'昵称名单{words}更新一次', plugin.help_text())
                result = await self.run_command(plugin, Event('/tarkov pve Nobody'))
                self.assertIn(words, result[1])

    async def test_all_digit_name_falls_back_to_the_index(self):
        self.profiles[('pve', 77)] = profile(aid=77, info_nickname='123456')
        names = FakeNames({'pve': [(77, '123456')]})
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pve 123456'))
        self.assertEqual(self.fetched, [('pve', 123456), ('pve', 77)])
        self.assertIn('123456（账号 ID 77）', result[0])

    async def test_bad_input(self):
        for text in ('/tarkov pve 名字', '/tarkov pve a', '/tarkov pve x/../y', '/tarkov pve a b'):
            with self.subTest(text=text):
                result = await self.run_command(self.plugin(), Event(text))
                self.assertTrue('昵称只能包含' in result[0] or '用法' in result[0])
        self.assertEqual(self.fetched, [])

    async def test_errors_become_chat_messages(self):
        plugin = self.plugin()
        cases = ((self.transport.QueryError('tarkov.dev 拒绝了访问'), '查询失败：tarkov.dev 拒绝了访问'),
                 (TimeoutError('阶段=读取响应体'), '超时'),
                 (RuntimeError('/secret/path'), '查询失败：RuntimeError。'))
        for error, words in cases:
            async def failing(*a, error=error, **k):
                raise error
            with self.subTest(error=error), patch.object(self.transport, 'fetch_profile', failing):
                result = [r async for r in plugin.tarkov_command(Event('/tarkov pve 1'))]
                self.assertIn(words, result[0])
                self.assertNotIn('/secret/path', result[0])
        self.assertFalse(plugin.active)

    async def test_profile_cache(self):
        self.profiles[('pve', 1)] = profile(aid=1)
        plugin = self.plugin({'profile_cache_minutes': 10})
        await self.run_command(plugin, Event('/tarkov pve 1'))
        await self.run_command(plugin, Event('/tarkov pve 1', sender='bob'))
        self.assertEqual(len(self.fetched), 1)
        plugin = self.plugin({'profile_cache_minutes': 0})
        await self.run_command(plugin, Event('/tarkov pve 1'))
        await self.run_command(plugin, Event('/tarkov pve 1'))
        self.assertEqual(len(self.fetched), 3)
        self.assertFalse(plugin.cache)

    async def test_cache_is_bounded(self):
        plugin = self.plugin()
        for aid in range(self.module.CACHE_ENTRIES+5):
            self.profiles[('pve', aid+1)] = profile(aid=aid+1)
            await self.run_command(plugin, Event(f'/tarkov pve {aid+1}'))
        self.assertEqual(len(plugin.cache), self.module.CACHE_ENTRIES)

    async def test_one_query_per_sender_at_a_time(self):
        release = asyncio.Event()
        async def slow(*a, **k):
            await release.wait()
            return profile()
        plugin = self.plugin()
        with patch.object(self.transport, 'fetch_profile', slow):
            first = asyncio.create_task(self.collect(plugin, Event('/tarkov pve 1')))
            await asyncio.sleep(0.01)
            # Bounded: without the guard this request would wait on `release` and hang the test.
            second = await asyncio.wait_for(self.collect(plugin, Event('/tarkov pve 2')), 2)
            other = asyncio.create_task(self.collect(plugin, Event('/tarkov pve 3', sender='bob')))
            await asyncio.sleep(0.01)
            release.set()
            first, other = await asyncio.wait_for(asyncio.gather(first, other), 10)
        self.assertIn('上一次查询还没完成', second[0])
        self.assertIn('档案', first[0])
        self.assertIn('档案', other[0])

    @staticmethod
    async def collect(plugin, event):
        return [r async for r in plugin.tarkov_command(event)]

    async def test_access_modes(self):
        self.profiles[('pve', 1)] = profile(aid=1)
        admin = Event('/tarkov pve 1')
        admin.admin = True
        cases = [({}, Event('/tarkov pve 1'), True),
                 ({'access_mode': 'admin_only'}, Event('/tarkov pve 1'), False),
                 ({'access_mode': 'admin_only'}, admin, True),
                 ({'access_mode': 'admin_and_whitelist', 'user_whitelist': ['alice']}, Event('/tarkov pve 1'), True),
                 ({'access_mode': 'admin_and_whitelist', 'user_whitelist': ['bob']}, Event('/tarkov pve 1'), False),
                 ({'access_mode': 'unexpected'}, Event('/tarkov pve 1'), False),
                 ({'allowed_origins': ['platform:group:2']}, Event('/tarkov pve 1'), False),
                 ({'allowed_origins': ['platform:group:1']}, Event('/tarkov pve 1'), True)]
        for config, event, ok in cases:
            with self.subTest(config=config, admin=event.admin):
                result = await self.run_command(self.plugin(config), event)
                self.assertEqual('档案' in result[0], ok)
                if not ok:
                    self.assertIn('没有塔科夫查询权限', result[0])
                self.assertTrue(event.stopped)

    async def test_access_list_types(self):
        # Audit M-1: a string used to be a substring test for allowed_origins. (config, allowed, warning logged)
        cases = [({'allowed_origins': 'platform:group:12'}, False, False),
                 ({'allowed_origins': 'platform:group:1'}, True, False),
                 ({'allowed_origins': ('platform:group:1',)}, True, False),
                 ({'allowed_origins': {'platform:group:1': 1}}, False, True),
                 ({'allowed_origins': 1}, False, True),
                 ({'access_mode': 'admin_and_whitelist', 'user_whitelist': 'alice'}, True, False),
                 ({'access_mode': 'admin_and_whitelist', 'user_whitelist': 'alice2'}, False, False),
                 ({'access_mode': 'admin_and_whitelist', 'user_whitelist': {'alice': 1}}, False, True)]
        for config, ok, warns in cases:
            with self.subTest(config=config):
                self.logs.clear()
                self.assertEqual(self.plugin(config).allowed(Event()), ok)
                self.assertEqual(any('not a list' in str(entry[0]) for entry in self.logs), warns)

    async def test_empty_list_items_are_ignored(self):
        # Review F-11: an empty row from the config page used to deny everyone.
        for value in ('', [''], ['  '], ['platform:group:1 '], [' ', 'platform:group:1']):
            with self.subTest(value=value):
                self.assertTrue(self.plugin({'allowed_origins': value}).allowed(Event()))
        self.assertFalse(self.plugin({'allowed_origins': ['', 'platform:group:2']}).allowed(Event()))

    async def test_enabled_proxy_without_address_fails_closed(self):
        # Review F-04: an empty address used to mean a direct connection.
        for url in ('', '   '):
            with self.subTest(url=url):
                result = await self.run_command(self.plugin({'proxy_enabled': True, 'proxy_url': url}), Event('/tarkov pve 1'))
                self.assertIn('没有填写代理地址', result[0])
        self.assertEqual(self.fetched, [])

    async def test_index_404_and_timeouts_in_chat(self):
        plugin = self.module.TarkovPlugin(types.SimpleNamespace(), {})
        async def missing(*a, **k):
            raise self.transport.NotFound('x')
        with patch.object(self.transport, 'fetch_index', missing):
            result = [r async for r in plugin.tarkov_command(Event('/tarkov pve Somebody'))]
        # Review F-12: no English text.
        self.assertIn('没有 PVE 模式的玩家名单', result[-1])
        # Review F-17: only an index timeout suggests the download-timeout setting.
        async def slow_index(*a, **k):
            raise TimeoutError('阶段=读取响应体')
        plugin = self.module.TarkovPlugin(types.SimpleNamespace(), {})
        with patch.object(self.transport, 'fetch_index', slow_index):
            result = [r async for r in plugin.tarkov_command(Event('/tarkov season Somebody'))]
        self.assertIn('下载最长等待', result[-1])
        async def slow_profile(*a, **k):
            raise TimeoutError('阶段=读取响应体')
        with patch.object(self.transport, 'fetch_profile', slow_profile):
            result = [r async for r in plugin.tarkov_command(Event('/tarkov pve 1'))]
        self.assertIn('超时', result[-1])
        self.assertNotIn('下载最长等待', result[-1])

    async def test_long_generated_names_are_accepted(self):
        self.profiles[('pve', 5)] = profile(aid=5, info_nickname='TarkovCitizen1019349')
        names = FakeNames({'pve': [(5, 'TarkovCitizen1019349')]})
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pve TarkovCitizen1019349'))
        self.assertIn('TarkovCitizen1019349（账号 ID 5）', result[0])
        result = await self.run_command(self.plugin(), Event('/tarkov pve '+'x'*25))
        self.assertIn('长度 3～24', result[0])

    async def test_infinite_setting_falls_back(self):
        plugin = self.plugin({'name_index_refresh_hours': float('inf'), 'skill_count': float('nan')})
        self.assertEqual(plugin.int_setting('name_index_refresh_hours', 168, 1, 720), 168)
        self.assertEqual(plugin.skill_count(), 10)

    async def test_fullwidth_digits_are_not_an_account_id(self):
        result = await self.run_command(self.plugin(), Event('/tarkov pve １２３４５'))
        self.assertIn('账号 ID 只能是数字', result[0])
        self.assertEqual(self.fetched, [])

    async def test_proxy_setting_reaches_transport(self):
        seen = {}
        async def capture(mode_path, aid, **kwargs):
            seen.update(kwargs)
            return profile()
        plugin = self.plugin({'proxy_enabled': True, 'proxy_url': ' http://proxy:8080 ', 'download_timeout_seconds': 300})
        with patch.object(self.transport, 'fetch_profile', capture):
            await self.collect(plugin, Event('/tarkov pve 1'))
        self.assertEqual(seen, {'proxy': 'http://proxy:8080', 'timeout_seconds': 60})
        self.assertIsNone(self.plugin({'proxy_url': 'http://proxy:8080'}).proxy())
