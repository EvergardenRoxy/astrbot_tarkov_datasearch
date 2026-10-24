import asyncio
import io
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

import test_plugin as boundary
from test_boss import boss_card, boss_data


class BossPluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.boundary = boundary.PluginTests(); self.boundary.setUp()
        self.module, self.plugins = self.boundary.module, []

    async def asyncTearDown(self):
        for plugin in self.plugins: await plugin.terminate()
        self.boundary.tearDown()

    def plugin(self, config=None):
        plugin = self.boundary.plugin(config)
        self.plugins.append(plugin)
        plugin.boss_portraits.all = AsyncMock(return_value={})
        plugin.context.platform_manager = types.SimpleNamespace(platform_insts=[types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(id='bot', name='telegram', support_proactive_message=True))])
        plugin.context.send_message = AsyncMock(return_value=True)
        return plugin

    async def test_disabled_denied_invalid_and_missing_event_all_return_images(self):
        for config, arguments, expected in [({}, '', '尚未开启'),
                ({'boss_enabled': True, 'access_mode': 'admin_only'}, '', '没有塔科夫查询权限'),
                ({'boss_enabled': True}, 'arena', '请选择'),
                ({'boss_enabled': True}, 'pve 变化', '尚无已记录'),
                ({'boss_enabled': True}, '页 aaaaaaaaaaaa 1', '快照已过期')]:
            plugin = self.plugin(config)
            plugin.boss_snapshot = AsyncMock(return_value=boss_card())
            captured = []
            with patch.object(self.module, 'render_boss_page', lambda page,*args:captured.append(page) or b'image'):
                result = await plugin.boss_query(boundary.Event('/boss'), arguments)
            self.assertIsInstance(result[0], boundary.ImageStub)
            self.assertIn(expected, captured[0]['error'])
            plugin.boss_snapshot.assert_not_awaited()

    async def test_complete_tables_are_frozen_and_continuation_does_not_refetch(self):
        plugin = self.plugin({'boss_enabled': True, 'boss_modes': ['pvp']})
        plugin.boss_snapshot = AsyncMock(return_value=boss_card(1, count=80))
        captured = []
        async def prepare(page, index=1, total=1, token=None):
            captured.append((page, index, total, token)); return b'image'
        plugin.prepare_boss_image = prepare
        await plugin.boss_query(boundary.Event('/boss'))
        _, index, total, token = captured[-1]
        self.assertGreater(total, 1)
        pages = plugin.boss_store.query(token)
        self.assertEqual(len({column['map_id'] for page in pages for column in page['columns']}),81)
        self.assertTrue(all(len(page['rows']) == 12 for page in pages))
        plugin.boss_snapshot.reset_mock()
        await plugin.boss_query(boundary.Event('/boss'), f'页 {token} 2')
        self.assertEqual(captured[-1][1], 2)
        self.assertEqual(captured[-1][0], pages[1])
        plugin.boss_snapshot.assert_not_awaited()

    async def test_namespaced_short_qq_and_explicit_mode_queries(self):
        plugin = self.plugin({'boss_enabled': True})
        plugin.boss_snapshot = AsyncMock(side_effect=lambda mode,**kwargs:boss_card(1, mode=mode))
        for command in ('/tarkov boss pve', '/塔科夫 刷率 pve'):
            result = [r async for r in plugin.tarkov_command(boundary.Event(command))]
            self.assertEqual(Image.open(io.BytesIO(result[0][0].data)).format, 'JPEG')
        event = boundary.Event('/boss season'); event.platform_name = 'qq_official'
        result = [r async for r in plugin.qq_boss_command(event)]
        self.assertIsInstance(result[0][0], boundary.ImageStub)
        self.assertEqual(plugin.boss_snapshot.call_args.args[0], 'season')

    async def test_failed_mode_is_an_error_page_and_other_modes_remain_in_full_snapshot(self):
        plugin = self.plugin({'boss_enabled': True, 'boss_modes': ['pvp','pve']})
        async def snapshot(mode,**kwargs):
            if mode == 'pve': raise TimeoutError()
            return boss_card(mode=mode)
        plugin.boss_snapshot = snapshot
        captured = []
        async def prepare(page, index=1, total=1, token=None): captured.append(token); return b'image'
        plugin.prepare_boss_image = prepare
        await plugin.boss_query(boundary.Event('/boss'))
        pages = plugin.boss_store.query(captured[0])
        self.assertEqual({page['mode'] for page in pages}, {'pvp','pve'})
        self.assertIn('不表示刷率为零', pages[-1]['error'])

    async def test_modes_interval_target_switches_and_lazy_lifecycle(self):
        plugin = self.plugin()
        await plugin.initialize()
        self.assertFalse(plugin.boss_store.path.exists())
        self.assertIsNone(plugin.boss_poller.task)
        for setting, expected in ((2,120), (30,1800), (1,120), (99,1800), ('bad',600)):
            plugin.config['boss_poll_minutes'] = setting
            self.assertEqual(plugin.boss_interval(), expected)
        plugin.config.update(boss_enabled=True, boss_broadcast_enabled=True,
                             boss_notify_targets=['bot:GroupMessage:1'], boss_modes=['regular','pvp','赛季','arena'])
        self.assertEqual(plugin.boss_modes(), ['pvp','season'])
        self.assertTrue(plugin.boss_broadcast_active())
        # Two switches: the feature switch rules everything, the push switch only the broadcasts.
        plugin.config['boss_enabled'] = False
        self.assertFalse(plugin.boss_broadcast_active())
        plugin.config['boss_enabled'] = True
        plugin.config['boss_broadcast_enabled'] = False
        self.assertFalse(plugin.boss_broadcast_active())
        self.assertTrue(plugin.boss_enabled())
        plugin.config['boss_broadcast_enabled'] = True
        started, gate = asyncio.Event(), asyncio.Event()
        async def cycle(): started.set(); await gate.wait()
        plugin.boss_poller.cycle = cycle
        await plugin.initialize(); await started.wait()
        task = plugin.boss_poller.task
        await plugin.initialize()
        self.assertIs(plugin.boss_poller.task, task)
        plugin.context.platform_manager.platform_insts[0].meta = lambda:types.SimpleNamespace(
            id='bot',name='qq_official',support_proactive_message=True)
        self.assertFalse(plugin.boss_broadcast_active())
        plugin.config['status_qq_push_enabled'] = True
        self.assertFalse(plugin.boss_broadcast_active())
        plugin.config['boss_qq_push_enabled'] = True
        self.assertTrue(plugin.boss_broadcast_active())
        plugin.config['boss_broadcast_enabled'] = False
        await plugin.initialize()
        self.assertIsNone(plugin.boss_poller.task)

    async def test_real_cache_revalidates_without_duplicate_events_and_bad_200_preserves_baseline(self):
        plugin = self.plugin({'boss_enabled': True, 'boss_broadcast_enabled': True,
                              'boss_notify_targets': ['bot:GroupMessage:1'], 'boss_modes':['pvp']})
        version, calls = [0], []
        async def fetch(key, **kwargs):
            calls.append((key, kwargs))
            if key.language: return {'status':200}, json.dumps({'data':{'factory-name':'工厂'}}).encode()
            if version[0] == 2: return {'status':304}, None
            body = boss_data(0.35 if version[0] == 0 else 0.4)
            if version[0] == 3: body['maps']['factory']['bosses'][0]['spawnChance'] = None
            return {'status':200,'etag':str(version[0])}, json.dumps({'data':body}).encode()
        with patch.object(self.module, 'fetch_public', fetch):
            await plugin.boss_notifications.cycle()
            plugin.context.send_message.assert_not_awaited()
            version[0] = 1
            await plugin.boss_notifications.cycle()
            plugin.context.send_message.assert_awaited_once()
            version[0] = 2
            await plugin.boss_notifications.cycle()
            plugin.context.send_message.assert_awaited_once()
            latest = plugin.boss_store.latest('pvp')
            version[0] = 3
            with self.assertRaises(self.module.QueryError): await plugin.boss_notifications.cycle()
            stale = await plugin.boss_snapshot('pvp', fresh=True, allow_stale=True)
            self.assertTrue(stale['stale'])
            self.assertEqual(plugin.boss_store.latest('pvp'), latest)
            self.assertEqual(stale['state'][-1]['rows'][0]['chance'], '0.4')
        self.assertEqual([kw['etag'] for key,kw in calls if not key.language][-1], '1')

    async def test_short_opt_out_disables_actual_boss_handlers_only(self):
        handlers = [types.SimpleNamespace(handler_name=name,enabled=True) for name in
                    ('boss_command','qq_boss_command','tarkov_command')]
        registry = sys.modules['astrbot.core.star.star_handler'].star_handlers_registry
        with patch.object(registry,'get_handlers_by_module_name',return_value=handlers):
            plugin = self.plugin({'short_commands_enabled':False})
        self.assertEqual([h.enabled for h in handlers], [False,False,True])
        self.assertEqual([r async for r in plugin.boss_command(boundary.Event('/boss'))], [])

    async def test_portrait_setting_is_a_persisted_one_shot_and_ordinary_queries_never_refresh(self):
        class Config(dict):
            def save_config(config): config.saved = dict(config)
        plugin = self.plugin({'boss_enabled':True,'boss_modes':['pvp']})
        plugin.config = Config(plugin.config)
        plugin.boss_portraits.refresh = AsyncMock(return_value=12)
        await plugin.initialize()
        plugin.boss_portraits.refresh.assert_not_awaited()
        plugin.boss_snapshot = AsyncMock(return_value=boss_card())
        await plugin.boss_query(boundary.Event('/boss'))
        plugin.boss_portraits.refresh.assert_not_awaited()
        plugin.config['boss_portraits_update'] = True
        await plugin.initialize()
        self.assertFalse(plugin.config['boss_portraits_update'])
        self.assertFalse(plugin.config.saved['boss_portraits_update'])
        plugin.boss_portraits.refresh.assert_awaited_once()
        await plugin.initialize()
        plugin.boss_portraits.refresh.assert_awaited_once()
