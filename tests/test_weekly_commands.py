"""The weekly report through the chat command: each chat's own roster, reports and ranking, the samples a user's
lookup adds, the weekly broadcast and the rosters adopted from the old settings. Samples are invented
(test_weekly_report.week)."""
import asyncio
import unittest

from plugin_harness import PluginCase
from test_weekly_report import DAY, LAST, iso, week

GROUP_1, GROUP_2 = 'bot:GroupMessage:g1', 'bot:GroupMessage:g2'


class WeeklyTests(PluginCase):
    def plugin(self, config=None, names=None):
        plugin = super().plugin({'weekly_enabled': True, **(config or {})}, names)
        plugin.weekly.clock = plugin.weekly.store.clock = lambda: LAST+3600
        return plugin

    def sampled(self, plugin, account_id, samples=None):
        for sample in week() if samples is None else samples:
            plugin.weekly.store.add_sample(account_id, 'pve', sample)

    def card(self):
        name, (card,) = self.drawn[-1]
        self.assertEqual(name, 'render_weekly_image')
        return card

    async def test_off_by_default_and_atlas_only(self):
        off = super().plugin()
        self.assertIn('玩家周报尚未开启', (await self.ask(off, '/tarkov 周报'))[0])
        snapshots = self.plugin({'player_source': 'tarkov-dev'})
        self.assertIn('玩家周报需要 Atlas 数据来源', (await self.ask(snapshots, '/tarkov 周报'))[0])
        plugin = self.plugin()
        self.assertIn('竞技场周报还没有实现', (await self.ask(plugin, '/tarkov 周报 竞技场'))[0])
        self.assertIn('用法：@机器人 tarkov 周报 [模式] [昵称、账号ID 或 排行]', (await self.ask(plugin, '/tarkov 周报 pve a b'))[0])
        self.assertIn('本聊天的 PVE 周报名单是空的', (await self.ask(plugin, '/tarkov weekly'))[0])

    async def test_administrators_keep_the_roster_of_the_chat_they_are_in(self):
        plugin = self.plugin()
        self.assertEqual(await self.ask(plugin, '/tarkov 周报 添加 11'), ['只有 AstrBot 管理员可以修改周报名单。'])
        self.assertIn('已把 11 加入本聊天的 PVE 周报名单', (await self.ask(plugin, '/tarkov 周报 添加 11', admin=True))[0])
        self.assertIn('已把 Some_Name 加入本聊天的 PVP 赛季 周报名单', (await self.ask(plugin, '/tarkov 周报 添加 赛季 Some_Name', admin=True))[0])
        self.assertIn('11 已经在本聊天的 PVE 周报名单内', (await self.ask(plugin, '/tarkov weekly add pve 11', admin=True))[0])
        self.assertIn('请填写账号 ID', (await self.ask(plugin, '/tarkov 周报 添加 a!', admin=True))[0])
        self.assertIn('用法', (await self.ask(plugin, '/tarkov 周报 添加', admin=True))[0])
        # Anyone may read the roster; it names what is not sampled yet.
        self.assertEqual(await self.ask(plugin, '/tarkov 周报 名单'), ['本聊天的 PVE 周报名单（1 名）：\n11（尚未采样）'])
        self.assertIn('本聊天的 PVP 周报名单是空的', (await self.ask(plugin, '/tarkov 周报 名单 pvp'))[0])
        self.assertIn('Some_Name 不在本聊天的 PVE 周报名单内', (await self.ask(plugin, '/tarkov 周报 移除 Some_Name', admin=True))[0])
        self.assertIn('已把 some_name 移出本聊天的 PVP 赛季 周报名单', (await self.ask(plugin, '/tarkov 周报 移除 赛季 some_name', admin=True))[0])
        # What the night will pull is every chat's entries, each once.
        await self.ask(plugin, '/tarkov 周报 添加 11', admin=True, origin=GROUP_2)
        await self.ask(plugin, '/tarkov 周报 添加 22', admin=True, origin=GROUP_2)
        self.assertEqual(sorted(plugin.weekly.sampler.entries()), [('pve', '11'), ('pve', '22')])

    async def test_each_chat_sees_only_its_own_players(self):
        plugin = self.plugin()
        await self.ask(plugin, '/tarkov 周报 添加 11', admin=True)
        await self.ask(plugin, '/tarkov 周报 添加 22', admin=True, origin=GROUP_2)
        self.sampled(plugin, 11)
        self.sampled(plugin, 22)
        [reply] = await self.ask(plugin, '/tarkov 周报 排行')
        self.assertTrue(reply.startswith('[picture]\n数据来源：'))
        card = self.card()
        self.assertEqual((card['ranking'], len(card['rows']), card['missing']), (True, 1, []))
        self.assertIn('22 不在本聊天的 PVE 周报名单内', (await self.ask(plugin, '/tarkov 周报 22'))[0])
        self.assertTrue((await self.ask(plugin, '/tarkov 周报 22', origin=GROUP_2))[0].startswith('[picture]'))
        self.assertNotIn('ranking', self.card())

    async def test_a_report_needs_two_nights_and_the_ranking_marks_short_weeks(self):
        plugin = self.plugin()
        for entry in ('11', '22', '33'):
            await self.ask(plugin, f'/tarkov 周报 添加 {entry}', admin=True)
        self.sampled(plugin, 11)
        self.sampled(plugin, 22, week()[-3:])   # two days of samples
        self.sampled(plugin, 33, week()[-1:])   # one night only
        self.assertIn('33 的 PVE 周报样本还不够：插件每天凌晨采样一次', (await self.ask(plugin, '/tarkov 周报 33'))[0])
        await self.ask(plugin, '/tarkov 周报 排行')
        card = self.card()
        names = sorted(row['nickname'] for row in card['rows'])
        self.assertEqual(names, ['Test_Player', 'Test_Player（2 天）'])
        self.assertEqual(card['missing'], ['33'])
        # Without a name: the sender's own bound account when it is listed, else the ranking.
        await asyncio.to_thread(plugin.app.accounts.bind, plugin.app.binding_key(self.event()), 11, 'Test_Player')
        await self.ask(plugin, '/tarkov 周报')
        self.assertNotIn('ranking', self.card())
        self.font.side_effect = RuntimeError('broken')
        self.assertEqual(await self.ask(plugin, '/tarkov 周报'), ['周报图片生成失败，请稍后再试。'])

    def event(self):
        from plugin_harness import Event
        return Event('')

    async def test_a_users_lookup_of_a_listed_player_adds_a_sample_and_settles_a_listed_nickname(self):
        plugin = self.plugin()
        await self.ask(plugin, '/tarkov 周报 添加 Test_Player', admin=True)
        self.atlas.put(7, 'pve', looked_up=iso(LAST))
        self.atlas.put(8, 'pve', nickname='Stranger', looked_up=iso(LAST))
        await self.ask(plugin, '/tarkov 7')
        await self.ask(plugin, '/tarkov 8', sender='bob')
        store = plugin.weekly.store
        # The nickname was listed but not resolved yet: the lookup by id settles it and its sample is kept.
        self.assertEqual(store.roster(GROUP_1, 'pve'), [('Test_Player', 7, 'Test_Player')])
        self.assertEqual((len(store.samples(7, 'pve', 0)), store.samples(8, 'pve', 0)), (1, []))
        # A cached reply is not a new sample.
        await self.ask(plugin, '/tarkov 7')
        self.assertEqual(len(store.samples(7, 'pve', 0)), 1)

    async def test_the_broadcast_sends_every_chat_the_ranking_of_its_own_roster(self):
        plugin = self.plugin({'weekly_push_enabled': True, 'allowed_origins': [GROUP_1, GROUP_2]})
        other = 'bot:GroupMessage:g3'
        plugin.weekly.store.add(GROUP_1, 'pve', '11')
        plugin.weekly.store.add(GROUP_2, 'pve', '22')
        plugin.weekly.store.add(GROUP_2, 'pvp', '22')
        plugin.weekly.store.add(other, 'pve', '11')
        self.sampled(plugin, 11)
        # A chat outside "allowed_origins" is not sent to, though it has a roster.
        self.assertEqual(await plugin.weekly.chats(), {GROUP_1: ['pve'], GROUP_2: ['pvp', 'pve']})
        await plugin.weekly.broadcast.deliver('2026-11-09 10:00')
        sent = plugin.context.send_message
        self.assertEqual([call.args[0] for call in sent.await_args_list], [GROUP_1])
        store = plugin.weekly.store
        self.assertEqual(store.push_state('2026-11-09 10:00', GROUP_1, 'pve'), ('submitted', 1))
        self.assertEqual(store.push_state('2026-11-09 10:00', GROUP_2, 'pve'), ('empty', 1))
        # The same slot is never sent twice; switched off, nothing is sent.
        await plugin.weekly.broadcast.deliver('2026-11-09 10:00')
        self.assertEqual(sent.await_count, 1)
        quiet = self.plugin()
        self.assertFalse(quiet.weekly.broadcasting())

    async def test_rosters_of_the_old_settings_become_the_rosters_of_their_target_chats(self):
        old = {'weekly': {'weekly_enabled': True, 'weekly_pve_players': ['11', 'Some_Name', 'bad name!'],
                          'weekly_pvp_players': ['11'], 'weekly_notify_targets': [GROUP_1, GROUP_2]}}
        plugin = super().plugin(old)
        await plugin.initialize()
        store = plugin.weekly.store
        for chat in (GROUP_1, GROUP_2):
            self.assertEqual([entry for entry, _, _ in store.roster(chat, 'pve')], ['11', 'Some_Name'])
            self.assertEqual([entry for entry, _, _ in store.roster(chat, 'pvp')], ['11'])
        # Only once: a roster changed in chat is not overwritten by the old settings at the next start.
        store.remove(GROUP_1, 'pve', 'Some_Name')
        await plugin.initialize()
        self.assertEqual([entry for entry, _, _ in store.roster(GROUP_1, 'pve')], ['11'])


if __name__ == '__main__':
    unittest.main()
