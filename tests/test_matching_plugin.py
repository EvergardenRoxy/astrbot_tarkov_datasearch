import unittest
from unittest.mock import AsyncMock

import test_plugin as boundary


class MatchingPluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.boundary = boundary.PluginTests()
        self.boundary.setUp()
        self.plugins = []

    async def asyncTearDown(self):
        for plugin in self.plugins:
            await plugin.terminate()
        self.boundary.tearDown()

    def plugin(self, config=None):
        plugin = self.boundary.plugin(config)
        self.plugins.append(plugin)
        return plugin

    async def test_disabled_and_lazy_startup_and_pool_ownership(self):
        self.assertTrue(self.plugin().matching.enabled())  # On by default (maintainer, 0.5.0).
        plugin = self.plugin({'atlas': {'matching_enabled': False}})
        self.assertIsNone(plugin.http.session)
        plugin.atlas.matching = AsyncMock()
        with self.assertRaises(self.boundary.module.QueryError):
            await plugin.matching.get()
        plugin.atlas.matching.assert_not_awaited()
        self.assertIs(plugin.matching.client, plugin.atlas)
        self.assertIs(plugin.atlas.http, plugin.http)
        await plugin.terminate()
        self.assertTrue(plugin.http.closed)
        self.assertTrue(plugin.matching.closed)
        self.assertTrue(plugin.atlas.closed)

    async def test_grouped_config_and_strict_interval_validation(self):
        for value in (True, '1', -1, 301, float('inf'), float('nan'), None):
            plugin = self.plugin({'atlas': {'matching_enabled': True, 'atlas_query_cooldown_seconds': value}})
            self.assertTrue(plugin.matching.enabled())
            self.assertEqual(plugin.atlas.cooldown.interval, 5)
        plugin = self.plugin({'atlas': {'matching_enabled': True, 'atlas_query_cooldown_seconds': 20}})
        self.assertEqual(plugin.atlas.cooldown.interval, 20)


class MatchingCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.boundary = boundary.PluginTests()
        self.boundary.setUp()
        self.plugins = []

    async def asyncTearDown(self):
        for plugin in self.plugins:
            await plugin.terminate()
        self.boundary.tearDown()

    def plugin(self, config=None):
        plugin = self.boundary.plugin(config)
        self.plugins.append(plugin)
        return plugin

    async def command(self, plugin, text):
        return [r async for r in plugin.tarkov_command(boundary.Event(text))]

    async def test_image_only_with_atlas_link_whatever_the_output_setting(self):
        from test_matching_image import result
        for output in ('text', 'markdown', 'image'):
            plugin = self.plugin({'output_mode': output})
            plugin.atlas.matching = AsyncMock()
            plugin.matching.get = AsyncMock(return_value=result())
            for text in ('/tarkov 匹配', '/塔科夫 matching'):
                reply = (await self.command(plugin, text))[0]
                self.assertIsInstance(reply[0], boundary.ImageStub)
                self.assertTrue(reply[0].data.startswith(b'\xff\xd8'))
                self.assertEqual(reply[1].text, '数据来源：Atlas https://eftforge.com/atlas')
        event = boundary.Event('tmatch')
        event.platform_name = 'qq_official'
        self.assertIsInstance([r async for r in plugin.qq_tmatch(event)][0][0], boundary.ImageStub)

    async def test_errors_are_text_and_never_masquerade_as_zero(self):
        module = self.boundary.module
        plugin = self.plugin()
        plugin.matching.get = AsyncMock(side_effect=module.QueryError('匹配时间查询失败，请稍后再试'))
        self.assertIn('匹配时间查询失败', (await self.command(plugin, '/tarkov 匹配'))[0])
        plugin.matching.get = AsyncMock(side_effect=RuntimeError('bug'))
        self.assertEqual((await self.command(plugin, '/tarkov 匹配'))[0], '匹配时间查询失败，请稍后再试。')
        self.assertIn('不需要参数', (await self.command(plugin, '/tarkov 匹配 6'))[0])
        from test_matching_image import result
        plugin.matching.get = AsyncMock(return_value=result())
        with unittest.mock.patch.object(module, 'render_matching_image', side_effect=OSError()):
            self.assertIn('图片生成失败', (await self.command(plugin, '/tarkov 匹配'))[0])
        disabled = self.plugin({'matching_enabled': False})
        disabled.matching.get = AsyncMock()
        self.assertIn('尚未开启', (await self.command(disabled, '/tarkov 匹配'))[0])
        disabled.matching.get.assert_not_awaited()
        self.assertNotIn('匹配', disabled.help_text())
        self.assertIn('tarkov 匹配', plugin.help_text())
        self.assertIn('https://eftforge.com/atlas', plugin.help_text())

    async def test_access_and_one_query_per_person(self):
        plugin = self.plugin({'access_mode': 'whitelist'})
        plugin.matching.get = AsyncMock()
        self.assertIn('没有塔科夫查询权限', (await self.command(plugin, '/tarkov 匹配'))[0])
        plugin.matching.get.assert_not_awaited()
        plugin = self.plugin()
        plugin.active.add(plugin.owner(boundary.Event('/tarkov 匹配')))
        plugin.matching.get = AsyncMock()
        self.assertIn('上一次查询还没完成', (await self.command(plugin, '/tarkov 匹配'))[0])
        plugin.matching.get.assert_not_awaited()
