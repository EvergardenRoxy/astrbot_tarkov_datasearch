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
        plugin = self.plugin()
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
