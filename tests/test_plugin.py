"""SDK boundary doubles, not a claim of a running AstrBot installation."""
import asyncio
import importlib.util
import io
import sqlite3
import threading
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures import arena_profile, profile
from tarkov_core.names import spelling_variants

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
    def chain_result(self, chain): return chain
    def is_admin(self): return self.admin
    def stop_event(self): self.stopped = True


class ImageStub:
    """Shape of astrbot.api.message_components.Image as main.py uses it."""
    def __init__(self, data): self.data = data

    @staticmethod
    def fromBytes(data): return ImageStub(data)


class PlainStub:
    def __init__(self, text): self.text = text


class FakeNames:
    """Stands in for NameIndex: a fixed name table per mode path."""
    def __init__(self, table=None, refresh=False, indexed=True, refresh_seconds=168*3600):
        self.table = table or {}
        self.refresh = refresh
        self.indexed = indexed
        self.refresh_seconds = refresh_seconds
        self.lookups = []
        self.similar_calls = []

    def needs_refresh(self, mode_path): return self.refresh

    def has_index(self, mode_path): return self.indexed

    async def similar(self, mode_path, name):
        self.similar_calls.append((mode_path, name))
        variants = spelling_variants(name)
        return [entry for entry in self.table.get(mode_path, []) if entry[1].lower() in variants][:3]

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
        components = types.ModuleType('astrbot.api.message_components')
        components.Image = ImageStub
        components.Plain = PlainStub
        self.modules = patch.dict(sys.modules, {'astrbot': types.ModuleType('astrbot'), 'astrbot.api': api,
                                                'astrbot.api.event': event, 'astrbot.api.star': star,
                                                'astrbot.api.message_components': components})
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

    def plugin(self, config=None, names=None, output='text'):
        # Most tests read the reply as text; the output tests below pass output=None for the real default (image).
        config = dict(config or {})
        if output:
            config.setdefault('output_mode', output)
        if names is not None:
            # A fake server index only matters when the optional index is switched on.
            config.setdefault('name_index_enabled', True)
        plugin = self.module.TarkovPlugin(types.SimpleNamespace(), config)
        plugin.names = names or FakeNames()
        return plugin

    async def run_command(self, plugin, event):
        with patch.object(self.transport, 'fetch_profile', self.fake_fetch):
            return [r async for r in plugin.tarkov_command(event)]

    async def test_help(self):
        for text in ('tarkov help', '/塔科夫 帮助', '/tarkov pve a b'):
            with self.subTest(text=text):
                event = Event(text)
                result = await self.run_command(self.plugin(), event)
                self.assertIn('绑定自己：/tarkov 绑定', result[0])
                self.assertIn('档案不会自动更新', result[0])
                self.assertNotIn('昵称名单', result[0])
                self.assertTrue(event.stopped)

    async def test_help_follows_the_output_mode(self):
        # Maintainer: the text help is hard to read on phones; with image output it is a picture plus the search link.
        from PIL import Image as PILImage
        result = await self.run_command(self.plugin({'default_mode': 'pve'}, output=None), Event('/tarkov 帮助'))
        image, link = result[0]
        self.assertIsInstance(image, ImageStub)
        self.assertEqual(PILImage.open(io.BytesIO(image.data)).width, 820)
        self.assertIsInstance(link, PlainStub)
        self.assertEqual(link.text, 'tarkov.dev 玩家搜索：https://tarkov.dev/players?gameMode=pve')
        result = await self.run_command(self.plugin(output=None), Event('/tarkov pve a b'))
        self.assertIsInstance(result[0][0], ImageStub)
        for output in ('text', 'markdown'):
            with self.subTest(output=output):
                result = await self.run_command(self.plugin({'output_mode': output}, output=None), Event('/tarkov 帮助'))
                self.assertIn('绑定自己：/tarkov 绑定', result[0])

    async def test_help_image_settings_and_failure(self):
        cards = []
        def spy(card):
            cards.append(card)
            raise OSError('font missing')
        plugin = self.plugin({'default_mode': 'season', 'arena_by_default': True}, names=FakeNames(refresh_seconds=24*3600),
                             output='image')
        with patch.object(self.module, 'render_help_image', spy):
            result = await self.run_command(plugin, Event('/tarkov 帮助'))
        self.assertEqual(cards, [{'mode': 'season', 'label': 'PVP 赛季',
                                  'search': 'https://tarkov.dev/players?gameMode=pvp-season',
                                  'example': 'https://tarkov.dev/players/pvp-season/1234567',
                                  'arena_by_default': True, 'index': '每天'}])
        self.assertIn('绑定自己：/tarkov 绑定', result[0])
        self.assertTrue(any('help image failed' in str(entry[0]) for entry in self.logs))
        self.assertIsNone(self.plugin(output='image').help_card()['index'])

    async def test_query_by_id(self):
        self.profiles[('pve', 1234567)] = profile()
        result = await self.run_command(self.plugin(), Event('/tarkov pve 1234567'))
        self.assertEqual(len(result), 1)
        self.assertIn('【塔科夫 PVE 档案】Test_Player（账号 ID 1234567）', result[0])
        self.assertEqual(self.fetched, [('pve', 1234567), ('profile', 1234567), ('pvp-season', 1234567)])

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
        # Index off (the default): the reply explains how to make the player known.
        plugin = self.module.TarkovPlugin(types.SimpleNamespace(), {'output_mode': 'text'})
        with patch.object(self.transport, 'fetch_index', side_effect=AssertionError('index must not be used')):
            result = await self.run_command(plugin, Event('/tarkov pve Nobody_Here'))
        self.assertIn('还不知道 Nobody_Here 的账号 ID', result[0])
        self.assertIn('https://tarkov.dev/players?gameMode=pve', result[0])
        self.assertIn('/tarkov https://tarkov.dev/players/pve/1234567', result[0])
        self.assertEqual(self.fetched, [])

    async def test_ambiguous_names_are_listed(self):
        names = FakeNames({'pve': [(1, 'Same'), (2, 'SAME')]})
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pve same'))
        self.assertIn('有多个名为 same 的玩家', result[0])
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
        self.assertIn('名单中的 OldName 对应账号 ID 5，档案里现在的昵称是 NewName', result[0])

    async def test_missing_id_profile(self):
        result = await self.run_command(self.plugin(), Event('/tarkov 赛季 12'))
        self.assertIn('tarkov.dev 上还没有账号 ID 12 的档案（PVP、PVE、赛季都没有）', result[0])
        self.assertIn('https://tarkov.dev/players?gameMode=pvp-season', result[0])

    async def test_digits_missing_as_id_and_as_name(self):
        result = await self.run_command(self.plugin(), Event('/tarkov 赛季 3456789'))
        self.assertEqual(len(result), 1)
        self.assertIn('tarkov.dev 上还没有账号 ID 3456789 的档案（PVP、PVE、赛季都没有）', result[0])

    async def test_digits_with_a_profile_in_another_mode(self):
        # Live run: `/tarkov pvp 7654321` said the account had no profile at all, though it has a PVE one (an id this
        # long is also a possible nickname, so the name lookup ran and its "unknown" text replaced the id's).
        self.profiles[('pve', 7654321)] = profile(aid=7654321)
        result = await self.run_command(self.plugin(), Event('/tarkov pvp 7654321'))
        self.assertEqual(len(result), 1)
        self.assertIn('PVP 模式下还没有账号 ID 7654321 的档案，但 PVE 模式（/tarkov pve 7654321）下有档案', result[0])
        self.assertNotIn('还不知道', result[0])

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
                self.assertIn(f'昵称名单（{words}更新一次）', plugin.help_text())
                result = await self.run_command(plugin, Event('/tarkov pve Nobody'))
                self.assertIn(words, result[1])

    async def test_all_digit_name_falls_back_to_the_record(self):
        self.profiles[('pve', 77)] = profile(aid=77, info_nickname='123456')
        names = FakeNames({'pve': [(77, '123456')]})
        plugin = self.plugin(names=names)
        await asyncio.to_thread(plugin.accounts.remember, 77, '123456')
        result = await self.run_command(plugin, Event('/tarkov pve 123456'))
        # Every mode is looked at for the id, then for the account the name points to.
        self.assertEqual(self.fetched, [('pve', 123456), ('profile', 123456), ('pvp-season', 123456), ('pve', 77), ('profile', 77), ('pvp-season', 77)])
        self.assertIn('123456（账号 ID 77）', result[0])

    async def test_an_account_id_never_downloads_the_index(self):
        # Review R-03: an id tried as a name used to start a 30 MB index download, and an index error replaced the
        # useful "but PVE has a profile" answer.
        self.profiles[('pve', 7654321)] = profile(aid=7654321)
        names = FakeNames(refresh=True, indexed=False)
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pvp 7654321'))
        self.assertEqual(len(result), 1)
        self.assertIn('但 PVE 模式（/tarkov pve 7654321）下有档案', result[0])
        self.assertEqual(names.lookups, [])

    async def test_bad_input(self):
        for text in ('/tarkov pve 名字', '/tarkov pve a', '/tarkov pve x/../y', '/tarkov pve http://example.com/players/pve/1'):
            with self.subTest(text=text):
                result = await self.run_command(self.plugin(), Event(text))
                self.assertIn('请发送昵称', result[0])
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
        # One request per mode for the first query (two of them "no profile", cached too); none for the second.
        self.assertEqual(len(self.fetched), 3)
        plugin = self.plugin({'profile_cache_minutes': 0})
        await self.run_command(plugin, Event('/tarkov pve 1'))
        await self.run_command(plugin, Event('/tarkov pve 1'))
        self.assertEqual(len(self.fetched), 9)
        self.assertFalse(plugin.cache)

    async def test_missing_profiles_are_cached_briefly(self):
        self.profiles[('pve', 1)] = profile(aid=1)
        plugin = self.plugin({'profile_cache_minutes': 10})
        await self.run_command(plugin, Event('/tarkov pve 1'))
        # Past NOT_FOUND_SECONDS the missing modes are asked again; the found profile still comes from the cache.
        for key, (stamp, summary) in list(plugin.cache.items()):
            plugin.cache[key] = (stamp-self.module.NOT_FOUND_SECONDS-1, summary)
        await self.run_command(plugin, Event('/tarkov pve 1'))
        self.assertEqual(sorted(self.fetched[3:]), [('profile', 1), ('pvp-season', 1)])

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
        plugin = self.module.TarkovPlugin(types.SimpleNamespace(), {'name_index_enabled': True})
        async def missing(*a, **k):
            raise self.transport.NotFound('x')
        with patch.object(self.transport, 'fetch_index', missing):
            result = [r async for r in plugin.tarkov_command(Event('/tarkov pve Somebody'))]
        # Review F-12: no English text.
        self.assertIn('没有 PVE 模式的玩家名单', result[-1])
        # Review F-17: only an index timeout suggests the download-timeout setting.
        async def slow_index(*a, **k):
            raise TimeoutError('阶段=读取响应体')
        plugin = self.module.TarkovPlugin(types.SimpleNamespace(), {'name_index_enabled': True})
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
        self.assertIn('账号 ID（数字）', result[0])
        self.assertEqual(self.fetched, [])

    async def test_default_output_is_an_image(self):
        from PIL import Image as PILImage
        self.profiles[('pve', 1234567)] = profile()
        for config in ({}, {'output_mode': 'something else'}):
            with self.subTest(config=config):
                result = await self.run_command(self.plugin(config, output=None), Event('/tarkov pve 1234567'))
                self.assertEqual(len(result), 1)
                self.assertIsInstance(result[0], list)
                image, link = result[0]
                self.assertIsInstance(image, ImageStub)
                # The image cannot carry a clickable link; a text line after it does.
                self.assertIsInstance(link, PlainStub)
                self.assertIn('https://tarkov.dev/players/pve/1234567', link.text)
                self.assertEqual(image.data[:2], b'\xff\xd8')
                self.assertEqual(PILImage.open(io.BytesIO(image.data)).width, 820)

    async def test_markdown_and_text_outputs(self):
        self.profiles[('pve', 1234567)] = profile()
        result = await self.run_command(self.plugin({'output_mode': 'markdown'}, output=None), Event('/tarkov pve 1234567'))
        self.assertTrue(result[0].startswith('## 塔科夫 PVE 档案：Test\\_Player'))
        self.assertIn('| PMC | 1000 |', result[0])
        result = await self.run_command(self.plugin({'output_mode': 'text'}, output=None), Event('/tarkov pve 1234567'))
        self.assertTrue(result[0].startswith('【塔科夫 PVE 档案】Test_Player'))

    async def test_image_failure_falls_back_to_text(self):
        self.profiles[('pve', 1234567)] = profile()
        def broken(card):
            raise OSError('font missing')
        with patch.object(self.module, 'render_image', broken):
            result = await self.run_command(self.plugin(output=None), Event('/tarkov pve 1234567'))
        self.assertTrue(result[0].startswith('【塔科夫 PVE 档案】'))
        self.assertIn('图片生成失败，已改为文字', result[0])
        self.assertTrue(any('image rendering failed' in str(entry[0]) for entry in self.logs))

    async def test_image_is_drawn_off_the_event_loop(self):
        self.profiles[('pve', 1234567)] = profile()
        threads = []
        real = self.module.render_image
        def spy(card):
            threads.append(threading.current_thread())
            return real(card)
        with patch.object(self.module, 'render_image', spy):
            await self.run_command(self.plugin(output=None), Event('/tarkov pve 1234567'))
        self.assertEqual(len(threads), 1)
        self.assertIsNot(threads[0], threading.main_thread())

    async def test_bind_then_query_yourself(self):
        self.profiles[('pve', 2345678)] = profile(aid=2345678, info_nickname='Player_Two')
        plugin = self.plugin()
        result = await self.run_command(plugin, Event('/tarkov 绑定 https://tarkov.dev/players/pve/2345678'))
        self.assertIn('已绑定：Player_Two（账号 ID 2345678）', result[0])
        # A binding holds in every chat of the platform, not on another platform.
        other_group = Event('/tarkov pve')
        other_group.unified_msg_origin = 'platform:group:2'
        result = await self.run_command(plugin, other_group)
        self.assertIn('【塔科夫 PVE 档案】Player_Two（账号 ID 2345678）', result[0])
        elsewhere = Event('/tarkov pve')
        elsewhere.unified_msg_origin = 'telegram:private:1'
        self.assertIn('你还没有绑定账号', (await self.run_command(plugin, elsewhere))[0])
        # No mode: the default mode (PVP) has no profile, so the PVE one is shown, saying so.
        result = await self.run_command(plugin, Event('/tarkov'))
        self.assertIn('【塔科夫 PVE 档案】Player_Two', result[0])
        self.assertIn('tarkov.dev 上还没有这个账号的 PVP 档案（要有的话，请在 tarkov.dev 用 PVP 模式搜索一次：https://tarkov.dev/players?gameMode=regular），下面是 PVE 模式的档案', result[0])
        # An explicit mode without a profile says where there is one.
        result = await self.run_command(plugin, Event('/tarkov pvp'))
        self.assertIn('你绑定的账号 Player_Two：PVP 模式下还没有账号 ID 2345678 的档案，但 PVE 模式（/tarkov pve 2345678）下有档案', result[0])
        self.assertIn('https://tarkov.dev/players?gameMode=regular', result[0])

    async def test_bind_by_id_by_name_and_failures(self):
        self.profiles[('pvp-season', 7)] = profile(aid=7, info_nickname='Seven')
        plugin = self.plugin()
        self.assertIn('用法：/tarkov 绑定', (await self.run_command(plugin, Event('/tarkov 绑定')))[0])
        result = await self.run_command(plugin, Event('/tarkov 绑定 赛季 9'))
        self.assertIn('tarkov.dev 上还没有账号 ID 9 的档案（PVP、PVE、赛季都没有）', result[0])
        self.assertIn('/tarkov 绑定 https://tarkov.dev/players/pvp-season/1234567', result[0])
        self.assertIn('不能确定 Seven 是哪个账号', (await self.run_command(plugin, Event('/tarkov 绑定 赛季 Seven')))[0])
        self.assertIn('已绑定：Seven', (await self.run_command(plugin, Event('/tarkov bind season 7')))[0])
        # The name is now known, so binding by name works too.
        self.assertIn('已绑定：Seven', (await self.run_command(plugin, Event('/tarkov 绑定 赛季 seven')))[0])

    async def test_unbind(self):
        self.profiles[('pve', 1)] = profile(aid=1)
        plugin = self.plugin()
        self.assertIn('你还没有绑定账号', (await self.run_command(plugin, Event('/tarkov 解绑')))[0])
        await self.run_command(plugin, Event('/tarkov 绑定 pve 1'))
        self.assertIn('已解除绑定：Test_Player（账号 ID 1）', (await self.run_command(plugin, Event('/tarkov unbind')))[0])
        self.assertIn('你还没有绑定账号', (await self.run_command(plugin, Event('/tarkov')))[0])

    async def test_query_by_player_page_link(self):
        self.profiles[('pve', 7654321)] = profile(aid=7654321, info_nickname='Player_One')
        plugin = self.plugin()
        result = await self.run_command(plugin, Event('/tarkov https://tarkov.dev/players/pve/7654321'))
        self.assertIn('【塔科夫 PVE 档案】Player_One（账号 ID 7654321）', result[0])
        self.assertEqual(self.fetched, [('pve', 7654321), ('profile', 7654321), ('pvp-season', 7654321)])
        self.assertIn('不一致', (await self.run_command(plugin, Event('/tarkov pvp https://tarkov.dev/players/pve/1')))[0])
        self.assertIn('竞技场', (await self.run_command(plugin, Event('/tarkov https://tarkov.dev/players/arena/1')))[0])
        result = await self.run_command(plugin, Event('/tarkov https://tarkov.dev/players/regular/7654321'))
        self.assertIn('PVP 模式下还没有账号 ID 7654321 的档案', result[0])
        self.assertIn('https://tarkov.dev/players?gameMode=regular', result[0])

    async def test_names_are_learned_from_queries(self):
        # Index off (the default); a query by id or link teaches the name to everyone.
        self.profiles[('pve', 7654321)] = profile(aid=7654321, info_nickname='Player_One')
        plugin = self.module.TarkovPlugin(types.SimpleNamespace(), {'output_mode': 'text'})
        with patch.object(self.transport, 'fetch_index', side_effect=AssertionError('index must not be used')):
            self.assertIn('还不知道 player_one', (await self.run_command(plugin, Event('/tarkov pve player_one')))[0])
            await self.run_command(plugin, Event('/tarkov pve 7654321'))
            someone_else = Event('/tarkov pve player_one', sender='bob')
            result = await self.run_command(plugin, someone_else)
        self.assertIn('【塔科夫 PVE 档案】Player_One', result[0])
        self.assertNotIn('注意', result[0])

    async def test_renamed_player_found_by_old_name(self):
        plugin = self.module.TarkovPlugin(types.SimpleNamespace(), {'output_mode': 'text'})
        self.profiles[('pve', 5)] = profile(aid=5, info_nickname='OldName')
        await self.run_command(plugin, Event('/tarkov pve 5'))
        plugin.cache.clear()
        self.profiles[('pve', 5)] = profile(aid=5, info_nickname='NewName')
        result = await self.run_command(plugin, Event('/tarkov pve OldName'))
        self.assertIn('记录中的 OldName 对应账号 ID 5，档案里现在的昵称是 NewName', result[0])

    async def test_replies_say_the_data_needs_a_manual_refresh(self):
        self.profiles[('pvp-season', 1)] = profile(aid=1)
        for output, words in (('text', 'https://tarkov.dev/players/pvp-season/1'),
                              ('markdown', '[这名玩家的页面](https://tarkov.dev/players/pvp-season/1)')):
            with self.subTest(output=output):
                result = await self.run_command(self.plugin(output=output), Event('/tarkov 赛季 1'))
                self.assertIn('数据不会自动更新', result[0])
                self.assertIn(words, result[0])

    async def test_failed_name_record_does_not_fail_the_query(self):
        self.profiles[('pve', 1)] = profile(aid=1)
        plugin = self.plugin()
        with patch.object(plugin.accounts, 'remember', side_effect=sqlite3.OperationalError('locked')):
            result = await self.run_command(plugin, Event('/tarkov pve 1'))
        self.assertIn('【塔科夫 PVE 档案】', result[0])
        self.assertTrue(any('name record not saved' in str(entry[0]) for entry in self.logs))

    async def test_without_a_mode_every_mode_is_looked_at(self):
        # The default mode (PVP) has no profile: the first mode that has one is shown, and the others are named.
        self.profiles[('pvp-season', 7)] = profile(aid=7, info_nickname='Seven')
        self.profiles[('pve', 7)] = profile(aid=7, info_nickname='Seven')
        result = await self.run_command(self.plugin(), Event('/tarkov 7'))
        self.assertTrue(result[0].startswith('【塔科夫 PVE 档案】Seven'))
        self.assertIn('tarkov.dev 上还没有这个账号的 PVP 档案（要有的话，请在 tarkov.dev 用 PVP 模式搜索一次：https://tarkov.dev/players?gameMode=regular），下面是 PVE 模式的档案', result[0])
        self.assertIn('这个账号在 PVP 赛季 模式下也有档案，查询时写上模式即可（例如 /tarkov 赛季 7）', result[0])
        self.assertEqual(sorted(self.fetched), [('profile', 7), ('pve', 7), ('pvp-season', 7)])

    async def test_a_chosen_mode_is_kept(self):
        self.profiles[('profile', 7)] = profile(aid=7)
        self.profiles[('pvp-season', 7)] = profile(aid=7)
        result = await self.run_command(self.plugin(), Event('/tarkov 赛季 7'))
        self.assertTrue(result[0].startswith('【塔科夫 PVP 赛季 档案】'))
        self.assertNotIn('下面是', result[0])
        self.assertIn('这个账号在 PVP 模式下也有档案', result[0])
        result = await self.run_command(self.plugin(), Event('/tarkov pve 7'))
        self.assertIn('PVE 模式下还没有账号 ID 7 的档案，但 PVP 模式（/tarkov pvp 7）、PVP 赛季 模式（/tarkov 赛季 7）下有档案', result[0])

    async def test_names_work_in_every_mode(self):
        self.profiles[('pvp-season', 7)] = profile(aid=7, info_nickname='Seven')
        plugin = self.plugin()
        await self.run_command(plugin, Event('/tarkov 赛季 7'))
        result = await self.run_command(plugin, Event('/tarkov seven'))
        self.assertTrue(result[0].startswith('【塔科夫 PVP 赛季 档案】Seven'))
        self.assertIn('tarkov.dev 上还没有这个账号的 PVP 档案（要有的话，请在 tarkov.dev 用 PVP 模式搜索一次：https://tarkov.dev/players?gameMode=regular），下面是 PVP 赛季 模式的档案', result[0])

    async def test_other_modes_failing_do_not_fail_the_query(self):
        self.profiles[('pve', 7)] = profile(aid=7)
        async def flaky(mode_path, aid, **kwargs):
            if mode_path == 'profile':
                raise self.transport.QueryError('tarkov.dev 返回 HTTP 500，请稍后再试')
            return await self.fake_fetch(mode_path, aid, **kwargs)
        plugin = self.plugin()
        with patch.object(self.transport, 'fetch_profile', flaky):
            result = [r async for r in plugin.tarkov_command(Event('/tarkov pve 7'))]
            self.assertTrue(result[0].startswith('【塔科夫 PVE 档案】'))
            self.assertTrue(any('profile failed' in str(entry[0]) for entry in self.logs))
            # Said in the reply, not dropped silently (review R-01).
            self.assertIn('PVP 模式这次没有查到（tarkov.dev 返回 HTTP 500，请稍后再试），请稍后再试', result[0])
            # The mode the reply is about must not fail silently.
            result = [r async for r in plugin.tarkov_command(Event('/tarkov pvp 7'))]
        self.assertIn('查询失败：tarkov.dev 返回 HTTP 500', result[0])

    def failing(self, modes, error=None):
        """fetch_profile failing for the mode paths in `modes` (a 429 by default), the fake store otherwise."""
        async def fetch(mode_path, aid, **kwargs):
            if mode_path in modes:
                self.fetched.append((mode_path, aid))
                raise error or self.transport.QueryError('tarkov.dev 限制了访问频率（HTTP 429），请稍后再试')
            return await self.fake_fetch(mode_path, aid, **kwargs)
        return fetch

    async def test_a_failed_mode_is_never_reported_as_no_profile(self):
        # Review R-01: PVE has a profile but its request got a 429; the reply said "PVP、PVE、赛季都没有".
        self.profiles[('pve', 7)] = profile(aid=7)
        plugin = self.plugin()
        with patch.object(self.transport, 'fetch_profile', self.failing({'pve'})):
            for text in ('/tarkov 7', '/tarkov 绑定 7', '/tarkov pve 7'):
                with self.subTest(text=text):
                    result = [r async for r in plugin.tarkov_command(Event(text))]
                    self.assertEqual(result, ['查询失败：tarkov.dev 限制了访问频率（HTTP 429），请稍后再试'])
            # A mode with a profile is still shown when another mode failed, and the failure is named.
            self.profiles[('pvp-season', 7)] = profile(aid=7)
            plugin.cache.clear()  # "no season profile" from the queries above is cached for a minute
            result = [r async for r in plugin.tarkov_command(Event('/tarkov 赛季 7'))]
            self.assertTrue(result[0].startswith('【塔科夫 PVP 赛季 档案】'))
            self.assertIn('PVE 模式这次没有查到（tarkov.dev 限制了访问频率（HTTP 429），请稍后再试），请稍后再试', result[0])
            # Explicit mode without a profile while another mode failed: "has a profile in" plus the failure.
            result = [r async for r in plugin.tarkov_command(Event('/tarkov pvp 7'))]
            self.assertIn('但 PVP 赛季 模式（/tarkov 赛季 7）下有档案；PVE 模式这次没有查到', result[0])
            self.assertNotIn('都没有', result[0])

    async def test_a_failed_default_mode_falls_back_with_a_note(self):
        self.profiles[('profile', 7)] = profile(aid=7)
        self.profiles[('pve', 7)] = profile(aid=7)
        plugin = self.plugin()
        with patch.object(self.transport, 'fetch_profile', self.failing({'profile'}, TimeoutError('读取响应体'))):
            result = [r async for r in plugin.tarkov_command(Event('/tarkov 7'))]
        self.assertTrue(result[0].startswith('【塔科夫 PVE 档案】'))
        self.assertIn('PVP 模式这次没有查到（连接超时），下面是 PVE 模式的档案。', result[0])
        self.assertNotIn('还没有这个账号的 PVP 档案', result[0])

    async def test_cached_profiles_say_how_old_they_are_and_links_skip_the_cache(self):
        # Review R-02: after refreshing on the website, a query within the cache time still showed the old snapshot.
        self.profiles[('pve', 7)] = profile(aid=7)
        plugin = self.plugin()
        await self.run_command(plugin, Event('/tarkov pve 7'))
        self.assertEqual(self.fetched.count(('pve', 7)), 1)
        result = await self.run_command(plugin, Event('/tarkov pve 7'))
        self.assertEqual(self.fetched.count(('pve', 7)), 1)
        self.assertNotIn('分钟前从 tarkov.dev 取得', result[0])
        plugin.cache[('pve', 7)][1]['fetched'] -= 300
        result = await self.run_command(plugin, Event('/tarkov pve 7'))
        self.assertIn('这是插件 5 分钟前从 tarkov.dev 取得的数据。刚在网站上刷新过的话，发送这名玩家的页面网址查询', result[0])
        result = await self.run_command(plugin, Event('/tarkov https://tarkov.dev/players/pve/7'))
        self.assertEqual(self.fetched.count(('pve', 7)), 2)
        self.assertNotIn('分钟前从 tarkov.dev 取得', result[0])

    async def test_one_request_per_profile_for_queries_at_the_same_time(self):
        # Review R-08: ten people asking for one account sent 30 requests.
        self.profiles[('pve', 7)] = profile(aid=7)
        plugin = self.plugin()
        events = [Event('/tarkov 7', sender=f'u{i}') for i in range(10)]
        with patch.object(self.transport, 'fetch_profile', self.fake_fetch):
            results = await asyncio.gather(*(self.collect(plugin, event) for event in events))
        self.assertTrue(all(result[0].startswith('【塔科夫 PVE 档案】') for result in results))
        self.assertEqual(sorted(self.fetched), [('profile', 7), ('pve', 7), ('pvp-season', 7)])
        self.assertEqual(plugin.pending, {})

    async def test_requests_at_once_are_capped(self):
        plugin = self.plugin()
        running, peak, gate = 0, 0, asyncio.Event()
        async def slow(mode_path, aid, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await gate.wait()
            running -= 1
            raise self.transport.NotFound('not found')
        with patch.object(self.transport, 'fetch_profile', slow):
            tasks = [asyncio.ensure_future(self.collect(plugin, Event(f'/tarkov {i}', sender=f'u{i}'))) for i in range(1, 6)]
            await asyncio.sleep(0.05)
            gate.set()
            await asyncio.gather(*tasks)
        self.assertEqual(peak, self.module.FETCHES_AT_ONCE)

    async def test_one_user_giving_up_does_not_cancel_the_shared_request(self):
        self.profiles[('pve', 7)] = profile(aid=7)
        plugin = self.plugin()
        gate = asyncio.Event()
        async def slow(mode_path, aid, **kwargs):
            await gate.wait()
            return await self.fake_fetch(mode_path, aid, **kwargs)
        with patch.object(self.transport, 'fetch_profile', slow):
            first = asyncio.ensure_future(self.collect(plugin, Event('/tarkov pve 7', sender='a')))
            second = asyncio.ensure_future(self.collect(plugin, Event('/tarkov pve 7', sender='b')))
            await asyncio.sleep(0.02)
            first.cancel()
            gate.set()
            result = await second
        self.assertTrue(result[0].startswith('【塔科夫 PVE 档案】'))
        self.assertEqual(self.fetched.count(('pve', 7)), 1)

    async def test_the_shown_account_id_is_the_one_asked_for(self):
        # Review R-06: the id in the header came from the document and could disagree with the link.
        self.profiles[('pve', 1)] = profile(aid=999)
        result = await self.run_command(self.plugin(), Event('/tarkov pve 1'))
        self.assertIn('（账号 ID 1）', result[0])
        self.assertNotIn('999', result[0])
        self.assertTrue(any('names account' in str(entry[0]) for entry in self.logs))

    async def test_index_name_miss_says_which_mode_was_searched(self):
        # Review R-04: only the default mode's index is searched, but the reply said "the index" as a whole.
        names = FakeNames({'pve': [(5, 'PveOnly')]})
        result = await self.run_command(self.plugin(names=names), Event('/tarkov PveOnly'))
        self.assertIn('tarkov.dev 的 PVP 昵称名单', result[0])
        self.assertIn('其他模式的名单要写上模式查询，例如 /tarkov pve PveOnly', result[0])
        result = await self.run_command(self.plugin(names=names), Event('/tarkov pvp PveOnly'))
        self.assertNotIn('其他模式的名单', result[0])

    async def test_bind_reply_reminds_to_refresh(self):
        # Review R-05.
        self.profiles[('pve', 7)] = profile(aid=7, info_nickname='Seven')
        result = await self.run_command(self.plugin(), Event('/tarkov 绑定 7'))
        self.assertIn('PVP、PVP 赛季 模式的档案要在 tarkov.dev 用该模式搜索一次自己才会出现', result[0])
        self.assertIn('档案不会自动更新', result[0])

    async def test_arena_is_asked_for_by_name(self):
        # Maintainer: Arena is queried with "/tarkov 竞技场 <...>"; it is not looked at otherwise by default.
        self.profiles[('arena', 7)] = arena_profile(aid=7)
        self.profiles[('pve', 7)] = profile(aid=7)
        plugin = self.plugin()
        for text in ('/tarkov 竞技场 7', '/tarkov arena 7', '/塔科夫 竞技场 7'):
            with self.subTest(text=text):
                self.fetched.clear()
                result = await self.run_command(plugin, Event(text, sender=text))
                self.assertTrue(result[0].startswith('【塔科夫竞技场档案】Test_Player（账号 ID 7）'), result[0])
                self.assertIn('非排位：对局 200', result[0])
                self.assertIn('https://tarkov.dev/players/arena/7', result[0])
        plugin.cache.clear()
        self.fetched.clear()
        result = await self.run_command(plugin, Event('/tarkov 竞技场 7'))
        self.assertEqual(self.fetched, [('arena', 7)])
        self.fetched.clear()
        result = await self.run_command(plugin, Event('/tarkov 7'))
        self.assertEqual(sorted(self.fetched), [('profile', 7), ('pve', 7), ('pvp-season', 7)])
        self.assertNotIn('竞技场', result[0])

    async def test_arena_by_default_setting(self):
        self.profiles[('arena', 7)] = arena_profile(aid=7)
        plugin = self.plugin({'arena_by_default': True})
        self.assertIn('（也会看竞技场）', plugin.help_text())
        self.assertIn('竞技场要写明', self.plugin().help_text())
        result = await self.run_command(plugin, Event('/tarkov 7'))
        self.assertEqual(sorted(self.fetched), [('arena', 7), ('profile', 7), ('pve', 7), ('pvp-season', 7)])
        self.assertTrue(result[0].startswith('【塔科夫竞技场档案】'))
        self.assertIn('tarkov.dev 上还没有这个账号的 PVP 档案', result[0])
        self.profiles[('pve', 7)] = profile(aid=7)
        plugin.cache.clear()
        result = await self.run_command(plugin, Event('/tarkov 7'))
        self.assertTrue(result[0].startswith('【塔科夫 PVE 档案】'))
        self.assertIn('这个账号在 竞技场 模式下也有档案，查询时写上模式即可（例如 /tarkov 竞技场 7）', result[0])
        del self.profiles[('pve', 7)], self.profiles[('arena', 7)]
        plugin.cache.clear()
        result = await self.run_command(plugin, Event('/tarkov 7'))
        self.assertIn('（PVP、PVE、赛季、竞技场都没有）', result[0])

    async def test_arena_is_not_a_default_mode(self):
        # The schema offers only the game modes; a hand-edited "arena" default falls back to PVP.
        self.assertEqual(self.plugin({'default_mode': 'arena'}).default_mode(), 'pvp')
        self.assertEqual(self.plugin({'default_mode': 'pve'}).default_mode(), 'pve')

    async def test_arena_links_binding_and_names(self):
        self.profiles[('arena', 7)] = arena_profile(aid=7, info_nickname='Arena_Only')
        plugin = self.plugin()
        result = await self.run_command(plugin, Event('/tarkov https://tarkov.dev/players/arena/7'))
        self.assertTrue(result[0].startswith('【塔科夫竞技场档案】Arena_Only'))
        result = await self.run_command(plugin, Event('/tarkov pve https://tarkov.dev/players/arena/7'))
        self.assertIn('网址里的模式是 竞技场，和你写的 PVE 不一致', result[0])
        # The name learned from the Arena profile works like any other.
        result = await self.run_command(plugin, Event('/tarkov 竞技场 arena_only'))
        self.assertTrue(result[0].startswith('【塔科夫竞技场档案】Arena_Only'))
        result = await self.run_command(plugin, Event('/tarkov 绑定 https://tarkov.dev/players/arena/7'))
        self.assertIn('已绑定：Arena_Only（账号 ID 7），目前在 竞技场 模式下有档案', result[0])
        result = await self.run_command(plugin, Event('/tarkov 竞技场'))
        self.assertTrue(result[0].startswith('【塔科夫竞技场档案】Arena_Only'))
        result = await self.run_command(plugin, Event('/tarkov https://tarkov.dev/players/unknown-mode/7'))
        self.assertIn('插件不认识这个网址里的模式', result[0])

    async def test_arena_missing_and_outputs(self):
        result = await self.run_command(self.plugin(), Event('/tarkov 竞技场 8'))
        self.assertIn('tarkov.dev 上还没有账号 ID 8 的竞技场档案', result[0])
        self.assertIn('请先在 tarkov.dev 用 竞技场 模式搜索一次这名玩家（https://tarkov.dev/players?gameMode=arena）', result[0])
        result = await self.run_command(self.plugin(), Event('/tarkov 绑定 8'))
        self.assertNotIn('竞技场', result[0])
        self.profiles[('arena', 7)] = arena_profile(aid=999)
        result = await self.run_command(self.plugin(output='markdown'), Event('/tarkov 竞技场 7'))
        self.assertIn('## 塔科夫竞技场档案', result[0])
        self.assertIn('**账号 ID** 7', result[0])
        # Image output (the default): the Arena Masters' registry picture, then the refresh link.
        result = await self.run_command(self.plugin(output=None), Event('/tarkov 竞技场 7'))
        image, line = result[0]
        self.assertEqual(image.data[:2], b'\xff\xd8')
        self.assertEqual(line.text, '刷新数据：在 tarkov.dev 打开 https://tarkov.dev/players/arena/7')
        def broken(card):
            raise OSError('font missing')
        with patch.object(self.module, 'render_arena_image', broken):
            result = await self.run_command(self.plugin(output=None), Event('/tarkov 竞技场 7', sender='x'))
        self.assertTrue(result[0].startswith('【塔科夫竞技场档案】'))
        self.assertIn('（图片生成失败，已改为文字）', result[0])

    async def test_names_differing_by_underscore_or_hyphen_are_suggested(self):
        # Maintainer's live test: a name with '_' was asked for; the Arena player's name has '-' instead.
        self.profiles[('arena', 5)] = arena_profile(aid=5, info_nickname='Arena-Fan')
        plugin = self.plugin()
        await self.run_command(plugin, Event('/tarkov 竞技场 5'))
        self.fetched.clear()
        result = await self.run_command(plugin, Event('/tarkov 竞技场 arena_fan'))
        self.assertIn('还不知道 arena_fan 的账号 ID。你是不是要找：Arena-Fan（账号 ID 5）？可以发送 /tarkov 竞技场 Arena-Fan。'
                      '如果都不是，请先在 tarkov.dev 用 竞技场 模式搜索一次这名玩家', result[0])
        self.assertEqual(self.fetched, [])
        result = await self.run_command(plugin, Event('/tarkov arena_fan'))
        self.assertIn('可以发送 /tarkov Arena-Fan。', result[0])
        result = await self.run_command(plugin, Event('/tarkov 绑定 arena_fan'))
        self.assertIn('不能确定 arena_fan 是哪个账号。你是不是要找：Arena-Fan（账号 ID 5）？可以发送 /tarkov 绑定 Arena-Fan。', result[0])
        result = await self.run_command(plugin, Event('/tarkov 竞技场 nobody_here'))
        self.assertNotIn('你是不是要找', result[0])
        # Maintainer's live case: the name belongs to a known account without an Arena profile, and an account spelled
        # with '-' has one.
        self.profiles[('profile', 6)] = profile(aid=6, info_nickname='arena_fan')
        await self.run_command(plugin, Event('/tarkov 6'))
        result = await self.run_command(plugin, Event('/tarkov 竞技场 arena_fan'))
        self.assertIn('arena_fan 的账号 ID 是 6。tarkov.dev 上还没有账号 ID 6 的竞技场档案。', result[0])
        self.assertIn('名字相近的还有：Arena-Fan（账号 ID 5），可以发送 /tarkov 竞技场 Arena-Fan 试试。', result[0])
        # The account itself is never suggested, even when it was once seen under a near spelling.
        await asyncio.to_thread(plugin.accounts.remember, 6, 'ARENA-FAN')
        result = await self.run_command(plugin, Event('/tarkov 竞技场 arena_fan'))
        self.assertNotIn('（账号 ID 6），', result[0])
        self.assertIn('名字相近的还有：Arena-Fan（账号 ID 5）', result[0])

    async def test_suggestions_from_the_downloaded_index(self):
        names = FakeNames({'pve': [(7, 'Some-Player'), (8, 'some_player')], 'profile': [(9, 'Some-Player')]})
        plugin = self.plugin(names=names)
        result = await self.run_command(plugin, Event('/tarkov pve SOME-PLAYER-'))
        self.assertNotIn('你是不是要找', result[0])
        result = await self.run_command(plugin, Event('/tarkov pve Some_Player-x'))
        self.assertNotIn('你是不是要找', result[0])
        names.table['pve'] = [(7, 'Some-Player-x')]
        result = await self.run_command(plugin, Event('/tarkov pve Some_Player_x'))
        self.assertIn('你是不是要找：Some-Player-x（账号 ID 7）？可以发送 /tarkov pve Some-Player-x。', result[0])
        self.assertEqual(names.similar_calls[-1], ('pve', 'Some_Player_x'))
        # With the index off, the downloaded index is not consulted.
        calls = len(names.similar_calls)
        result = await self.run_command(self.plugin({'name_index_enabled': False}, names=names), Event('/tarkov pve Some_Player_x'))
        self.assertNotIn('你是不是要找', result[0])
        self.assertEqual(len(names.similar_calls), calls)

    async def test_bind_accepts_a_profile_in_any_mode(self):
        self.profiles[('pve', 7)] = profile(aid=7, info_nickname='Seven')
        plugin = self.plugin()
        result = await self.run_command(plugin, Event('/tarkov 绑定 https://tarkov.dev/players/pvp-season/7'))
        self.assertIn('已绑定：Seven（账号 ID 7），目前在 PVE 模式下有档案', result[0])
        result = await self.run_command(plugin, Event('/tarkov'))
        self.assertTrue(result[0].startswith('【塔科夫 PVE 档案】Seven'))

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
