"""Server status, Boss spawn rates and matching times through the chat command, and the status and Boss pushes.
The game data is invented (test_status, test_boss, test_matching); nothing reaches tarkov.dev or Atlas."""
import asyncio
import re
import unittest

from plugin_harness import Event, PluginCase, PublicResource
from test_boss import boss_data
from test_matching import matching_data
from test_status import status_data

GROUP_1, GROUP_2 = 'bot:GroupMessage:g1', 'bot:GroupMessage:g2'


class StatusTests(PluginCase):
    async def test_off_by_default_then_a_picture(self):
        self.assertIn('服务器状态查询尚未开启', (await self.ask(self.plugin(), '/tarkov status'))[0])
        plugin = self.plugin({'status_enabled': True})
        self.public[('status', None, None)] = PublicResource(status_data())
        for text in ('/tarkov status', '/塔科夫 状态'):
            self.assertEqual(await self.ask(plugin, text, sender=text), ['[picture]'])
        name, (card,) = self.drawn[-1]
        self.assertEqual((name, card['stale'], card['error']), ('render_status_image', False, None))
        self.assertIn('状态查询不需要参数', (await self.ask(plugin, '/tarkov status now'))[0])

    async def test_a_failure_is_text_that_never_reads_as_the_game_being_down(self):
        plugin = self.plugin({'status_enabled': True})
        [reply] = await self.ask(plugin, '/tarkov status')
        self.assertEqual(reply, '无法取得服务器状态：没有这份数据 此结果不表示游戏停服。')
        # An older copy is shown, labelled, when tarkov.dev cannot be reached.
        self.public[('status', None, None)] = PublicResource(status_data(), stale=True)
        self.assertEqual(await self.ask(plugin, '/tarkov status'), ['[picture]'])
        self.assertEqual(self.drawn[-1][1][0]['error'], '连接失败，展示上次成功数据：连接失败')
        self.font.side_effect = RuntimeError('broken')
        self.assertEqual(await self.ask(plugin, '/tarkov status'), ['服务器状态图片生成失败，请稍后再试。'])

    async def test_a_change_is_pushed_once_and_a_refused_chat_does_not_stop_the_others(self):
        plugin = self.plugin({'status_enabled': True, 'status_broadcast_enabled': True,
                              'status_notify_targets': [GROUP_1, GROUP_2, 'not a session']})
        self.assertEqual(plugin.status.targets(), [GROUP_1, GROUP_2])
        sent = []

        async def send(target, chain):
            sent.append(target)
            if target == GROUP_1:
                raise PermissionError('no proactive messages')
            return True
        plugin.context.send_message = send
        self.public[('status', None, None)] = PublicResource(status_data())
        await plugin.status.notifications.cycle()
        self.assertEqual(sent, [])  # The first look only records the state.
        self.public[('status', None, None)] = PublicResource(status_data('Unstable'), checked_at=200)
        await plugin.status.notifications.cycle()
        self.assertEqual(sent, [GROUP_1, GROUP_2])
        # Nothing new: nothing sent. The next change skips the chat that refused.
        await plugin.status.notifications.cycle()
        self.public[('status', None, None)] = PublicResource(status_data(), checked_at=300)
        await plugin.status.notifications.cycle()
        self.assertEqual(sent, [GROUP_1, GROUP_2, GROUP_2])
        self.assertEqual({call[3] for call in self.public_calls}, {True})  # The monitor always asks tarkov.dev.

    async def test_without_a_target_list_pushes_go_to_the_groups_that_used_the_plugin(self):
        plugin = self.plugin({'status_enabled': True, 'status_broadcast_enabled': True})
        self.assertFalse(plugin.status.broadcasting())
        self.public[('status', None, None)] = PublicResource(status_data())
        await self.ask(plugin, '/tarkov status', origin=GROUP_2)
        await self.ask(plugin, '/tarkov status', origin='bot:FriendMessage:alice')
        self.assertEqual(plugin.status.targets(), [GROUP_2])
        self.assertTrue(plugin.status.broadcasting())
        await plugin.status.poller.close()
        # Remembered across a restart; chats outside "allowed_origins" are never pushed to.
        again = self.plugin({'status_enabled': True, 'status_broadcast_enabled': True})
        self.assertEqual(again.status.targets(), [GROUP_2])
        limited = self.plugin({'status_enabled': True, 'status_broadcast_enabled': True, 'allowed_origins': [GROUP_1]})
        self.assertEqual(limited.status.targets(), [])

    async def test_qq_official_chats_need_their_own_switch_and_a_platform_that_can_push(self):
        config = {'status_enabled': True, 'status_broadcast_enabled': True, 'status_notify_targets': [GROUP_1]}
        plugin = self.plugin(dict(config))
        platform = plugin.context.platform_manager.platform_insts[0].meta()
        plugin.context.platform_manager.platform_insts[0].meta = lambda: platform
        platform.name = 'qq_official'
        self.assertEqual(plugin.status.targets(), [])
        plugin.app.config['status_qq_push_enabled'] = True
        self.assertEqual(plugin.status.targets(), [GROUP_1])
        platform.support_proactive_message = False
        self.assertEqual(plugin.status.targets(), [])


class BossTests(PluginCase):
    def feeds(self, chance=0.35, modes=('pvp', 'pve', 'season'), checked_at=100):
        for mode in modes:
            self.public[('maps', mode, None)] = PublicResource(boss_data(chance), checked_at=checked_at)

    async def test_off_by_default_then_one_stored_table_for_the_watched_modes(self):
        self.assertIn('Boss 查询尚未开启', (await self.ask(self.plugin(), '/tarkov boss'))[0])
        plugin = self.plugin({'boss_enabled': True})
        self.feeds()
        self.assertEqual(await self.ask(plugin, '/tarkov boss'), ['[picture]'])
        name, (page, token, index, total, following, portraits) = self.drawn[-1]
        self.assertEqual((name, page['mode'], index, total), ('render_boss_page', 'pvp', 1, 3))
        self.assertEqual(following, f'@机器人 tarkov boss 页 {token} 2')
        # The translation dictionaries are missing here: the table is still shown, with the warning.
        self.assertTrue(page['translation_warning'])
        asked = len(self.public_calls)
        self.assertEqual(await self.ask(plugin, f'/tarkov boss 页 {token} 3'), ['[picture]'])
        self.assertEqual((self.drawn[-1][1][0]['mode'], self.drawn[-1][1][2]), ('season', 3))
        self.assertEqual(len(self.public_calls), asked)  # A stored page is read, not fetched again.
        self.assertIn('页码超出范围，此快照共 3 页', (await self.ask(plugin, f'/tarkov boss 页 {token} 4'))[0])
        self.assertIn('表格快照已过期或被清理', (await self.ask(plugin, '/tarkov boss 页 000000000000 1'))[0])
        self.assertIn('续查用法', (await self.ask(plugin, '/tarkov boss 页 1'))[0])

    async def test_modes_aliases_and_the_personal_default(self):
        plugin = self.plugin({'boss_enabled': True})
        self.feeds()
        for words, mode in (('pve', 'pve'), ('PVP S', 'season'), ('赛季', 'season')):
            await self.ask(plugin, f'/tarkov boss {words}')
            self.assertEqual((self.drawn[-1][1][0]['mode'], self.drawn[-1][1][3]), (mode, 1))
        self.assertIn('请选择 pvp、pve 或赛季模式', (await self.ask(plugin, '/tarkov boss arena'))[0])
        self.assertEqual(await self.ask(plugin, '/tarkov boss 默认 pve'),
                         ['Boss 默认模式已设为 PVE。之后发送 @机器人 tarkov boss 即可查询。'])
        await self.ask(plugin, '/tarkov boss')
        self.assertEqual((self.drawn[-1][1][0]['mode'], self.drawn[-1][1][3]), ('pve', 1))
        # The default is the user's own.
        await self.ask(plugin, '/tarkov boss', sender='bob')
        self.assertEqual(self.drawn[-1][1][3], 3)

    async def test_a_mode_that_fails_is_an_error_page_among_the_others(self):
        plugin = self.plugin({'boss_enabled': True})
        self.feeds(modes=('pvp', 'season'))
        await self.ask(plugin, '/tarkov boss')
        token = self.drawn[-1][1][1]
        await self.ask(plugin, f'/tarkov boss 页 {token} 2')
        page = self.drawn[-1][1][0]
        self.assertEqual((page['mode'], page['error']), ('pve', '没有这份数据 此结果不表示刷率为零。'))
        # Without monitoring there is no recorded change to show.
        await self.ask(plugin, '/tarkov boss pvp 变化')
        self.assertIn('尚无已记录的刷率变化', self.drawn[-1][1][0]['error'])

    async def test_a_changed_rate_is_pushed_with_its_table_and_can_be_asked_for(self):
        plugin = self.plugin({'boss_enabled': True, 'boss_broadcast_enabled': True, 'boss_modes': ['pvp'],
                              'boss_notify_targets': [GROUP_1, GROUP_2]})
        sent = []

        async def send(target, chain):
            sent.append(target)
            if target == GROUP_1:
                raise PermissionError('no proactive messages')
            return True
        plugin.context.send_message = send
        self.feeds(0.35, ('pvp',))
        await plugin.boss.notifications.cycle()
        self.assertEqual(sent, [])
        self.feeds(0.5, ('pvp',), checked_at=200)
        await plugin.boss.notifications.cycle()
        self.assertEqual(sent, [GROUP_1, GROUP_2])
        await plugin.boss.notifications.cycle()
        self.assertEqual(sent, [GROUP_1, GROUP_2])
        await self.ask(plugin, '/tarkov boss pvp 变化')
        self.assertTrue(self.drawn[-1][1][0]['change_lines'])

    async def test_portraits_are_downloaded_only_by_the_one_shot_setting(self):
        saved = []
        config = type('Config', (dict,), {'save_config': lambda self: saved.append(self['boss_portraits_update'])})(
            {'boss_enabled': True, 'boss_portraits_update': True})
        plugin = self.plugin(config)
        asked = []

        async def download(name):
            asked.append(name)
            raise self.core('transport').QueryError('offline')
        plugin.boss.portraits.download = download
        self.feeds()
        await self.ask(plugin, '/tarkov boss pvp')
        self.assertEqual(asked, [])
        await plugin.initialize()
        await plugin.initialize()
        self.assertEqual((len(asked), saved), (12, [False]))


class MatchingTests(PluginCase):
    async def test_one_shared_picture_with_the_atlas_link(self):
        plugin = self.plugin()
        self.atlas.matching_doc = matching_data()
        for sender in ('alice', 'bob'):
            [reply] = await self.ask(plugin, '/tarkov 匹配', sender=sender)
            self.assertTrue(reply.startswith('[picture]\n数据来源：') and reply.endswith('https://eftforge.com/atlas'), reply)
        self.assertEqual(self.atlas.calls, [('matching',)])
        self.assertEqual(self.drawn[-1][0], 'render_matching_image')
        self.assertIn('匹配时间查询不需要参数', (await self.ask(plugin, '/tarkov matching now'))[0])
        self.assertIn('匹配时间查询尚未开启', (await self.ask(self.plugin({'matching_enabled': False}), '/tarkov 匹配'))[0])

    async def test_failures_are_text(self):
        plugin = self.plugin()
        self.atlas.matching_doc = self.error(502)
        self.assertEqual(await self.ask(plugin, '/tarkov 匹配'), ['匹配时间查询失败：匹配时间查询失败，请稍后再试'])
        refused = self.plugin()
        self.atlas.matching_doc = self.error(403)
        self.assertRegex((await self.ask(refused, '/tarkov 匹配'))[0], r'匹配时间查询失败：Atlas 拒绝了访问（HTTP 403），30 分钟后自动重试')
        drawn = self.plugin()
        self.atlas.matching_doc = matching_data()
        self.font.side_effect = RuntimeError('broken')
        self.assertEqual(await self.ask(drawn, '/tarkov 匹配'), ['匹配时间图片生成失败，请稍后再试。'])


if __name__ == '__main__':
    unittest.main()
