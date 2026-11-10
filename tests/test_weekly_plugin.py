"""The weekly report in the plugin: roster settings, the query command, samples fed by user queries, the nightly
sampler's callbacks, broadcast targets and the task lifecycle. Invented data; no test reaches Atlas."""
import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_plugin
from test_atlas_players import AtlasPluginTests
from test_plugin import Event, ImageStub
from test_weekly_report import DAY, LAST, iso, week

ON = {'weekly_enabled': True, 'weekly_pve_players': ['1234567', 'Player_Two']}


class WeeklyPluginTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_plugin.PluginTests.setUp
    tearDown = test_plugin.PluginTests.tearDown
    fake_fetch = test_plugin.PluginTests.fake_fetch
    run_command = test_plugin.PluginTests.run_command
    make = AtlasPluginTests.plugin
    text = staticmethod(AtlasPluginTests.text)

    def plugin(self, config=None, **more):
        plugin = self.make(dict(ON if config is None else config), **more)
        plugin.weekly_clock = lambda: LAST+7*3600
        plugin.weekly_store.clock = lambda: LAST+7*3600
        self.cards = []
        def record(card):
            self.cards.append(card)
            return b'\xff\xd8weekly'
        patcher = patch.object(self.module, 'render_weekly_image', record)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addAsyncCleanup(plugin.terminate)
        return plugin

    async def fill(self, plugin, aid=1234567, mode='pve', nickname=None, entry=None):
        for sample in week():
            if nickname:
                sample = dict(sample, nickname=nickname)
            await asyncio.to_thread(plugin.weekly_store.add_sample, aid, mode, sample)
        if entry:
            await asyncio.to_thread(plugin.weekly_store.resolve, mode, entry, aid, nickname or entry)

    async def ask(self, plugin, text, sender='alice'):
        [reply] = await self.run_command(plugin, Event(text, sender=sender))
        return reply

    async def test_off_denied_and_the_snapshot_source(self):
        self.assertIn('尚未开启', await self.ask(self.plugin({}), '/tarkov 周报'))
        self.assertIn('没有塔科夫查询权限', await self.ask(self.plugin(dict(ON, access_mode='admin_only')), '/tarkov 周报'))
        snapshot = self.plugin(dict(ON, player_source='tarkov-dev'))
        self.assertIn('需要 Atlas 数据来源', await self.ask(snapshot, '/tarkov 周报'))
        self.assertFalse(snapshot.weekly_enabled() or snapshot.weekly_sampling_active())
        self.assertEqual(self.atlas.calls, [])

    async def test_roster_settings(self):
        plugin = self.plugin({'weekly_enabled': True, 'weekly_pvp_players': 'Solo_One',
                              'weekly_pve_players': [' 1234567 ', 'Player_Two', 'player_two', '', '0', 'bad name', 'x', 'a.b.c',
                                                     'W'*25, 7],
                              'weekly_season_players': {'not': 'a list'}})
        self.assertEqual(plugin.weekly_roster('pvp'), ['Solo_One'])
        self.assertEqual(plugin.weekly_roster('pve'), ['1234567', 'Player_Two', 'a.b.c', '7'])
        self.assertEqual(plugin.weekly_roster('season'), [])
        self.assertEqual(plugin.weekly_modes(), ['pvp', 'pve'])
        self.assertEqual(plugin.weekly_entries()[0], ('pvp', 'Solo_One'))
        # At most forty per mode; Arena has no roster yet.
        many = self.plugin({'weekly_enabled': True, 'weekly_pve_players': [str(i) for i in range(1, 60)]})
        self.assertEqual(len(many.weekly_roster('pve')), 40)
        self.assertNotIn('arena', self.module.WEEKLY_LISTS)
        self.assertFalse(self.plugin({'weekly_enabled': True}).weekly_sampling_active())
        self.assertTrue(plugin.weekly_sampling_active())

    async def test_a_listed_player_by_id_name_and_own_binding(self):
        plugin = self.plugin()
        await self.fill(plugin)
        await self.fill(plugin, 2345678, nickname='Player_Two', entry='Player_Two')
        reply = await self.ask(plugin, '/tarkov 周报 pve 1234567')
        self.assertIsInstance(reply[0], ImageStub)
        self.assertEqual(self.text(reply), '数据来源：Atlas https://eftforge.com/atlas')
        self.assertEqual((self.cards[-1]['nickname'], self.cards[-1]['mode_label'], self.cards[-1]['tiles'][0][1]),
                         ('Test_Player', 'PVE', '37'))
        # PVE is the default mode; names ignore case; the QQ single word works.
        await self.ask(plugin, '/tarkov 周报 player_two')
        self.assertEqual(self.cards[-1]['nickname'], 'Player_Two')
        await self.ask(plugin, '/tarkov weekly pve Player_Two')
        self.assertEqual(self.cards[-1]['nickname'], 'Player_Two')
        event = Event('tweekly pve 1234567')
        event.platform_name = 'qq_official'
        self.assertIsInstance([r async for r in plugin.qq_tweekly(event)][0][0], ImageStub)
        # Without a target: the caller's own bound account when it is listed.
        await asyncio.to_thread(plugin.accounts.bind, plugin.binding_key(Event()), 2345678, 'Player_Two')
        await self.ask(plugin, '/tarkov 周报')
        self.assertEqual(self.cards[-1]['nickname'], 'Player_Two')
        # The command reads the nightly samples only.
        self.assertEqual(self.atlas.calls, [])
        self.assertEqual(self.font.calls, 5)

    async def test_ranking_for_everyone_else_and_on_request(self):
        plugin = self.plugin()
        await self.fill(plugin)
        # Not bound, or bound to an account that is not listed: the mode's ranking.
        reply = await self.ask(plugin, '/tarkov 周报')
        self.assertIsInstance(reply[0], ImageStub)
        card = self.cards[-1]
        self.assertTrue(card['ranking'])
        self.assertEqual(([row['nickname'] for row in card['rows']], card['missing']), (['Test_Player'], ['Player_Two']))
        await asyncio.to_thread(plugin.accounts.bind, plugin.binding_key(Event()), 999, 'Other')
        await self.ask(plugin, '/tarkov 周报 pve')
        self.assertTrue(self.cards[-1]['ranking'])
        await asyncio.to_thread(plugin.accounts.bind, plugin.binding_key(Event()), 1234567, 'Test_Player')
        for text in ('/tarkov 周报 排行', '/tarkov 周报 pve rank'):
            await self.ask(plugin, text)
            self.assertTrue(self.cards[-1]['ranking'])

    async def test_text_replies(self):
        plugin = self.plugin()
        self.assertIn('Nobody 不在 PVE 周报名单内', await self.ask(plugin, '/tarkov 周报 Nobody'))
        self.assertIn('@机器人 tarkov 周报 pve 排行', await self.ask(plugin, '/tarkov 周报 Nobody'))
        self.assertIn('PVP 周报名单是空的', await self.ask(plugin, '/tarkov 周报 pvp'))
        self.assertIn('竞技场周报还没有实现', await self.ask(plugin, '/tarkov 周报 竞技场'))
        self.assertIn('用法：@机器人 tarkov 周报 [模式]', await self.ask(plugin, '/tarkov 周报 pve a b'))
        # No samples yet, a name that was never resolved, and a single night.
        self.assertIn('样本还不够', await self.ask(plugin, '/tarkov 周报 1234567'))
        self.assertIn('Player_Two 的 PVE 周报样本还不够', await self.ask(plugin, '/tarkov 周报 Player_Two'))
        self.assertIn('名单里的玩家样本还不够', await self.ask(plugin, '/tarkov 周报 排行'))
        await asyncio.to_thread(plugin.weekly_store.add_sample, 1234567, 'pve', week()[-1])
        self.assertIn('Test_Player', await self.ask(plugin, '/tarkov 周报 1234567') + 'Test_Player')
        self.assertEqual(self.cards, [])
        await self.fill(plugin)
        with patch.object(self.module, 'render_weekly_image', side_effect=RuntimeError()):
            self.assertIn('周报图片生成失败', await self.ask(plugin, '/tarkov 周报 1234567'))
        self.assertEqual(plugin.active, set())

    async def test_a_user_query_of_a_listed_player_is_a_free_sample(self):
        plugin = self.plugin()
        self.atlas.put(1234567, 'pve', looked_up=iso(LAST+3600))
        self.atlas.put(7654321, 'pve', looked_up=iso(LAST+3600))
        self.atlas.put(1234567, 'pvp', looked_up=iso(LAST+3600))
        self.quota_clock.advance(30)
        await self.ask(plugin, '/tarkov pve 1234567')
        await self.ask(plugin, '/tarkov pve 7654321', sender='bob')
        await self.ask(plugin, '/tarkov pvp 1234567', sender='carol')
        samples = await asyncio.to_thread(plugin.weekly_store.samples, 1234567, 'pve', 0)
        self.assertEqual([entry['looked_up'] for entry in samples], [LAST+3600])
        # Not listed (another account, another mode): nothing is kept.
        self.assertEqual(await asyncio.to_thread(plugin.weekly_store.counts), (1, 1))
        # A cached answer adds nothing, and nothing is stored with the feature off.
        await self.ask(plugin, '/tarkov pve 1234567', sender='dave')
        self.assertEqual(await asyncio.to_thread(plugin.weekly_store.counts), (1, 1))

    async def test_nothing_is_stored_with_the_feature_off(self):
        off = self.plugin({'weekly_pve_players': ['1234567']})
        self.atlas.put(1234567, 'pve')
        self.quota_clock.advance(30)
        self.assertIsInstance((await self.ask(off, '/tarkov pve 1234567'))[0], ImageStub)
        await off.weekly_start()
        self.assertIsNone(off.weekly_sampler.task)
        self.assertIsNone(off.weekly_broadcast.task)
        self.assertFalse(off.weekly_store.path.exists())

    async def test_sampler_callbacks(self):
        plugin = self.plugin()
        # A nickname: the shared record first (no request), then Atlas' search (one request).
        await asyncio.to_thread(plugin.accounts.remember, 2345678, 'Player_Two')
        self.assertEqual(await plugin.weekly_resolve('pve', 'Player_Two'), (2345678, 'Player_Two', False))
        self.atlas.searches = {'Found_One': [(3, 'Found_One'), (4, 'found_onex')], 'Twin': [(5, 'twin'), (6, 'TWIN')]}
        self.assertEqual(await plugin.weekly_resolve('pve', 'found_one'), (3, 'Found_One', True))
        self.assertEqual(await plugin.weekly_resolve('pve', 'Twin'), (None, None, True))
        self.assertEqual(await plugin.weekly_resolve('pve', 'Missing_One'), (None, None, True))
        self.assertEqual(await plugin.weekly_resolve('pve', 'TarkovCitizen123456'), (None, None, False))
        self.assertEqual([call[0] for call in self.atlas.calls], ['search']*3)
        # The pull skips the local cache, takes nobody's quota, and feeds history, names and the samples.
        self.atlas.put(1234567, 'pve', looked_up=iso(LAST))
        self.atlas.calls.clear()
        summary = await plugin.weekly_fetch('pve', 1234567)
        await plugin.weekly_fetch('pve', 1234567)
        self.assertEqual(self.atlas.calls, [('lookup', 1234567, 'pve')]*2)
        self.assertEqual(plugin.quota.history, {})
        self.assertTrue(await plugin.weekly_save(1234567, 'pve', summary))
        self.assertFalse(await plugin.weekly_save(1234567, 'pve', summary))
        self.assertFalse(await plugin.weekly_save(1234567, 'pve', {'doc': None}))
        self.assertEqual((await asyncio.to_thread(plugin.atlas_history.latest, 1234567, 'pve'))['nickname'], 'Test_Player')
        self.assertEqual((await asyncio.to_thread(plugin.accounts.lookup, 'Test_Player'))[0], [(1234567, 'Test_Player')])
        # Wired into the sampler with the maintainer's pacing.
        self.assertEqual(plugin.weekly_sampler.entries(), [('pve', '1234567'), ('pve', 'Player_Two')])
        self.assertEqual(plugin.weekly_sampler.gap.__func__(types.SimpleNamespace(rand=lambda low, high: high)), 180)

    async def test_broadcast_targets_must_be_listed(self):
        group = 'bot:GroupMessage:g1'
        def ready(config):
            plugin = self.plugin(dict(ON, **config))
            plugin.context.platform_manager = types.SimpleNamespace(platform_insts=[types.SimpleNamespace(
                meta=lambda: types.SimpleNamespace(id='bot', name='qq_official', support_proactive_message=True))])
            plugin.context.send_message = AsyncMock(return_value=True)
            return plugin
        # Remembered groups are never used, unlike status and Boss.
        idle = ready({'weekly_push_enabled': True})
        idle.session_ids = [group]
        self.assertEqual(idle.weekly_targets(), [])
        self.assertFalse(idle.weekly_broadcast_active())
        for config in ({'weekly_push_enabled': False, 'weekly_notify_targets': [group]},
                       {'weekly_push_enabled': True, 'weekly_notify_targets': ['', 'not-a-session']},
                       {'weekly_push_enabled': True, 'weekly_notify_targets': [group], 'allowed_origins': ['bot:GroupMessage:other']},
                       {'weekly_push_enabled': True, 'weekly_notify_targets': [group], 'weekly_pve_players': []},
                       {'weekly_push_enabled': True, 'weekly_notify_targets': [group], 'weekly_enabled': False}):
            with self.subTest(config=config):
                self.assertFalse(ready(config).weekly_broadcast_active())
        # A listed QQ official group needs no further switch.
        plugin = ready({'weekly_push_enabled': True, 'weekly_notify_targets': [group, group]})
        self.assertEqual(plugin.weekly_targets(), [group])
        self.assertTrue(plugin.weekly_broadcast_active())
        await self.fill(plugin)
        data = await plugin.weekly_ranking_image('pve')
        self.assertEqual(data, b'\xff\xd8weekly')
        self.assertIsNone(await plugin.weekly_ranking_image('pvp'))
        self.assertTrue(await plugin.send_weekly(group, data))
        target, chain = plugin.context.send_message.await_args.args
        self.assertEqual(target, group)
        self.assertIsInstance(chain.chain[0], ImageStub)
        self.assertEqual(chain.chain[1].text, '数据来源：Atlas https://eftforge.com/atlas')
        with self.assertRaises(self.module.PermanentDeliveryError):
            await plugin.send_weekly('bot:GroupMessage:other', data)

    async def test_schedule_setting(self):
        self.assertEqual(self.plugin().weekly_schedule(), (0, 10, 0))
        self.assertEqual(self.plugin(dict(ON, weekly_push_weekday='周五', weekly_push_time='21:30')).weekly_schedule(), (4, 21, 30))
        for weekday, clock in (('星期五', '21:30'), ('周五', '25:00'), ('周五', None)):
            self.assertEqual(self.plugin(dict(ON, weekly_push_weekday=weekday, weekly_push_time=clock)).weekly_schedule(), (0, 10, 0))

    async def test_tasks_follow_the_settings_and_stop_with_the_plugin(self):
        group = 'bot:GroupMessage:g1'
        plugin = self.plugin(dict(ON, weekly_push_enabled=True, weekly_notify_targets=[group]))
        started = []
        async def idle(seconds):
            started.append(seconds)
            await asyncio.Event().wait()
        plugin.weekly_sampler.sleep = plugin.weekly_broadcast.sleep = idle
        plugin.weekly_sampler.clock = plugin.weekly_broadcast.clock = lambda: LAST+12*3600
        await plugin.weekly_start()
        sampler, broadcast = plugin.weekly_sampler.task, plugin.weekly_broadcast.task
        self.assertFalse(sampler.done() or broadcast.done())
        await asyncio.sleep(0.05)
        self.assertEqual(len(started), 2)
        # Nothing was requested outside the 03:00-09:00 window, and nothing is caught up.
        self.assertEqual(self.atlas.calls, [])
        await plugin.terminate()
        self.assertTrue(sampler.cancelled() and broadcast.cancelled())
        self.assertFalse(plugin.weekly_sampling_active() or plugin.weekly_broadcast_active())

    async def test_help_names_the_command_only_when_it_is_on(self):
        on, off = self.plugin(), self.plugin({})
        self.assertIn('玩家周报图片：@机器人 tarkov 周报 [模式] [昵称、账号ID 或 排行]', on.help_text())
        self.assertIn('、tweekly [模式] [昵称]', on.help_text())
        self.assertTrue(on.help_card()['weekly_enabled'])
        self.assertNotIn('周报', off.help_text())
        self.assertNotIn('weekly_enabled', off.help_card())
        from tarkov_core.help_image import help_rows
        rows = help_rows(dict(on.help_card(), arena_by_default=False))
        self.assertIn(('玩家周报', '@机器人 tarkov 周报 [模式] [昵称或排行]', '名单内玩家近 7 天的变化；数据来自 Atlas'), rows)
        self.assertIn('tweekly', rows[-1][1])


if __name__ == '__main__':
    unittest.main()
