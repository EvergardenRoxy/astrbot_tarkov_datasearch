import asyncio
import io
import json
import types
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

import test_plugin as boundary
from test_status import status_card, status_data


class StatusPluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.boundary = boundary.PluginTests()
        self.boundary.setUp()
        self.module = self.boundary.module
        self.plugins = []

    async def asyncTearDown(self):
        for plugin in self.plugins:
            await plugin.terminate()
        self.boundary.tearDown()

    def plugin(self, config=None):
        plugin = self.boundary.plugin(config)
        self.plugins.append(plugin)
        plugin.context.platform_manager = types.SimpleNamespace(platform_insts=[types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(id='bot', name='telegram', support_proactive_message=True))])
        plugin.context.send_message = AsyncMock(return_value=True)
        return plugin

    async def test_disabled_denied_failure_and_success_queries_all_return_images(self):
        for config, error in [({}, '尚未开启'), ({'status_enabled': True, 'access_mode': 'admin_only'}, '没有塔科夫查询权限'),
                              ({'status_enabled': True}, None)]:
            plugin = self.plugin(config)
            plugin.status_snapshot = AsyncMock(return_value=status_card())
            cards = []
            with patch.object(self.module, 'render_status_image', lambda card: cards.append(card) or b'image'):
                result = [r async for r in plugin.status_command(boundary.Event('/status'))]
            self.assertIsInstance(result[0][0], boundary.ImageStub)
            if error:
                self.assertIn(error, cards[0]['error'])
                plugin.status_snapshot.assert_not_awaited()
            else:
                self.assertIn('state', cards[0])
                plugin.status_snapshot.assert_awaited_once()
        plugin = self.plugin({'status_enabled': True})
        plugin.status_snapshot = AsyncMock(side_effect=TimeoutError())
        cards = []
        with patch.object(self.module, 'render_status_image', lambda card: cards.append(card) or b'image'):
            await plugin.status_query(boundary.Event('/status'))
        self.assertIn('不表示游戏停服', cards[0]['error'])

    async def test_namespaced_qq_arguments_and_image_failure_paths(self):
        plugin = self.plugin({'status_enabled': True})
        plugin.status_snapshot = AsyncMock(return_value=status_card())
        for text in ('/tarkov status', '/塔科夫 状态'):
            result = [r async for r in plugin.tarkov_command(boundary.Event(text))]
            self.assertEqual(Image.open(io.BytesIO(result[0][0].data)).format, 'JPEG')
        event = boundary.Event('/status')
        event.platform_name = 'qq_official'
        self.assertIsInstance([r async for r in plugin.qq_status_command(event)][0][0], boundary.ImageStub)
        with patch.object(self.module, 'render_status_image', side_effect=RuntimeError()):
            self.assertIn('图片生成失败', await plugin.status_query(boundary.Event('/status')))
        cards = []
        with patch.object(self.module, 'render_status_image', lambda card: cards.append(card) or b'image'):
            await plugin.status_query(boundary.Event('/status x'), arguments='x')
        self.assertIn('不需要参数', cards[0]['error'])

    async def test_active_owner_is_held_until_image_render_completes(self):
        plugin = self.plugin({'status_enabled': True})
        plugin.status_snapshot = AsyncMock(return_value=status_card())
        started, gate = asyncio.Event(), asyncio.Event()
        async def picture(event, card):
            if 'state' in card:
                started.set()
                await gate.wait()
            return card
        plugin.status_picture = picture
        first = asyncio.create_task(plugin.status_query(boundary.Event('/status')))
        await started.wait()
        second = await plugin.status_query(boundary.Event('/status'))
        self.assertIn('还没完成', second['error'])
        gate.set()
        await first
        self.assertFalse(plugin.active)

    async def test_target_validation_interval_switches_and_qq_capability_gate(self):
        config = {'status_enabled': True, 'status_broadcast_enabled': True,
                  'status_notify_targets': [' bot:GroupMessage:1 ', 'bot:GroupMessage:1', 'invalid', 42]}
        plugin = self.plugin(config)
        self.assertEqual(plugin.status_targets(), ['bot:GroupMessage:1'])
        self.assertTrue(plugin.status_broadcast_active())
        for value, minutes in ((2, 2), (30, 30), (1, 2), (31, 30), ('bad', 5), (float('inf'), 5)):
            plugin.config['status_poll_minutes'] = value
            self.assertEqual(plugin.status_interval(), minutes*60)
        plugin.config['allowed_origins'] = ['bot:GroupMessage:other']
        self.assertFalse(plugin.status_broadcast_active())
        plugin.config['allowed_origins'] = []
        plugin.context.platform_manager.platform_insts[0].meta = lambda: types.SimpleNamespace(
            id='bot', name='qq_official', support_proactive_message=True)
        self.assertFalse(plugin.status_broadcast_active())
        plugin.config['status_qq_push_enabled'] = True
        self.assertTrue(plugin.status_broadcast_active())
        plugin.context.platform_manager.platform_insts[0].meta = lambda: types.SimpleNamespace(
            id='bot', name='qq_official', support_proactive_message=False)
        self.assertFalse(plugin.status_broadcast_active())
        plugin.config['status_notify_targets'] = 'bot:GroupMessage:1'
        self.assertEqual(plugin.status_targets(), [])

    async def test_initialize_disabled_is_lazy_and_reinitialization_has_one_poller(self):
        plugin = self.plugin()
        await plugin.initialize()
        self.assertIsNone(plugin.status_poller.task)
        self.assertFalse(plugin.status_store.path.exists())
        self.assertFalse(plugin.public_data.path.exists())
        plugin.config.update(status_enabled=True, status_broadcast_enabled=True,
                             status_notify_targets=['bot:GroupMessage:1'])
        started, release = asyncio.Event(), asyncio.Event()
        async def cycle():
            started.set()
            await release.wait()
        plugin.status_poller.cycle = cycle
        await plugin.initialize()
        await started.wait()
        first = plugin.status_poller.task
        await plugin.initialize()
        self.assertIs(plugin.status_poller.task, first)
        plugin.config['status_broadcast_enabled'] = False
        await plugin.initialize()
        self.assertIsNone(plugin.status_poller.task)

    async def test_cache_status_validation_and_broadcast_submission(self):
        plugin = self.plugin({'status_enabled': True, 'status_broadcast_enabled': True,
                              'status_notify_targets': ['bot:GroupMessage:1']})
        fetch = AsyncMock(side_effect=[({'status': 200, 'etag': '1'}, json.dumps({'data': status_data()}).encode()),
                                      ({'status': 200, 'etag': '2'}, json.dumps({'data': status_data('Unstable')}).encode()),
                                      ({'status': 304, 'etag': '2'}, None)])
        with patch.object(self.module, 'fetch_public', fetch):
            await plugin.status_notifications.cycle()
            plugin.context.send_message.assert_not_awaited()
            await plugin.status_notifications.cycle()
            plugin.context.send_message.assert_awaited_once()
            chain = plugin.context.send_message.call_args.args[1]
            self.assertIsInstance(chain.chain[0], boundary.ImageStub)
            await plugin.status_notifications.cycle()
            plugin.context.send_message.assert_awaited_once()
        self.assertEqual(fetch.await_count, 3)
        self.assertEqual(fetch.call_args.kwargs['etag'], '2')

    async def test_short_command_disable_sets_actual_handler_metadata(self):
        first = types.SimpleNamespace(handler_name='status_command', enabled=True)
        qq = types.SimpleNamespace(handler_name='qq_status_command', enabled=True)
        original = types.SimpleNamespace(handler_name='tarkov_command', enabled=True)
        import sys
        registry = sys.modules['astrbot.core.star.star_handler'].star_handlers_registry
        with patch.object(registry, 'get_handlers_by_module_name', return_value=[first, qq, original]):
            plugin = self.plugin({'short_commands_enabled': False, 'status_enabled': True})
        self.assertFalse(first.enabled)
        self.assertFalse(qq.enabled)
        self.assertTrue(original.enabled)
        self.assertEqual([r async for r in plugin.status_command(boundary.Event('/status'))], [])

    async def test_malformed_success_cannot_replace_last_good_status_or_advance_baseline(self):
        plugin = self.plugin({'status_enabled': True, 'status_broadcast_enabled': True,
                              'status_notify_targets': ['bot:GroupMessage:1']})
        invalid = status_data('Down')
        invalid['generalStatus'].pop('statusCode')
        fetch = AsyncMock(side_effect=[({'status': 200, 'etag': 'good'}, json.dumps({'data': status_data()}).encode()),
                                      ({'status': 200, 'etag': 'bad'}, json.dumps({'data': invalid}).encode()),
                                      ({'status': 200, 'etag': 'bad'}, json.dumps({'data': invalid}).encode()),
                                      ({'status': 200, 'etag': 'recovery'}, json.dumps({'data': status_data('Down')}).encode())])
        with patch.object(self.module, 'fetch_public', fetch):
            await plugin.status_notifications.cycle()
            with self.assertRaises(self.module.QueryError):
                await plugin.status_notifications.cycle()
            stale = await plugin.status_snapshot(fresh=True, allow_stale=True)
            self.assertTrue(stale['stale'])
            self.assertEqual(stale['state']['overall']['code'], 'ok')
            plugin.context.send_message.assert_not_awaited()
            await plugin.status_notifications.cycle()
            plugin.context.send_message.assert_awaited_once()
            self.assertEqual(fetch.call_args.kwargs['etag'], 'good')
