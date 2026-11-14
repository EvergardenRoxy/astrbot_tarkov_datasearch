"""Player queries, bindings and the help through the chat command, with both player sources.
Every profile is invented (fixtures.py); nothing reaches Atlas or tarkov.dev."""
import asyncio
import unittest

from fixtures import arena_profile, profile
from plugin_harness import Event, FakeNames, PluginCase, text_of
from test_atlas_cooldown import Clock


class AtlasSourceTests(PluginCase):
    """The default source: one live lookup of one mode per query, always a picture."""

    def plugin(self, config=None, names=None):
        plugin = super().plugin(config, names)
        # The per-user intervals are measured on a clock the tests move.
        self.clock = plugin.players.quota.clock = Clock()
        return plugin

    def card(self, index=-1):
        name, args = self.drawn[index]
        return name, args[0]

    async def test_default_mode_is_pve_and_the_reply_is_a_picture_with_the_links(self):
        plugin = self.plugin()
        self.atlas.put(1, 'pve')
        self.atlas.put(1, 'arena')
        [reply] = await self.ask(plugin, '/tarkov 1')
        self.assertTrue(reply.startswith('[picture]\n数据来源：Atlas https://eftforge.com/atlas'))
        self.assertIn('https://tarkov.dev/players/pve/1', reply)
        self.assertIn('@机器人 tarkov pve 1 刷新', reply)
        name, card = self.card()
        self.assertEqual((name, card['mode_label'], card['aid']), ('render_atlas_image', 'PVE', '1'))
        self.clock.advance(60)
        [reply] = await self.ask(plugin, '/tarkov https://tarkov.dev/players/arena/1')
        self.assertIn('@机器人 tarkov 竞技场 1 刷新', reply)
        # One mode per query, and the snapshots are never read while Atlas answers.
        self.assertEqual(self.atlas.calls, [('lookup', 1, 'pve'), ('lookup', 1, 'arena')])
        self.assertEqual(self.fetched, [])

    async def test_a_name_is_settled_by_the_record_then_the_list_then_atlas_search(self):
        names = FakeNames({'pve': [(2, 'Listed_One')]})
        plugin = self.plugin(names=names)
        for aid in (1, 2, 3):
            self.atlas.put(aid, 'pve', nickname={1: 'Test_Player', 2: 'Listed_One', 3: 'Searched_One'}[aid])
        await self.ask(plugin, '/tarkov 1')
        self.clock.advance(60)
        # Learned from the lookup by id: no list, no search, and the cached profile costs nothing.
        await self.ask(plugin, '/tarkov test_player')
        self.assertEqual((names.lookups, self.atlas.calls), ([], [('lookup', 1, 'pve')]))
        await self.ask(plugin, '/tarkov Listed_One')
        self.clock.advance(60)
        self.atlas.searches['Searched_One'] = [(3, 'Searched_One')]
        await self.ask(plugin, '/tarkov Searched_One')
        self.assertEqual(names.lookups, [('pve', 'Listed_One'), ('pve', 'Searched_One')])
        self.assertEqual(self.atlas.calls[1:], [('lookup', 2, 'pve'), ('search', 'Searched_One', 'pve'), ('lookup', 3, 'pve')])
        # The search and the lookup of one query count once against the user's hour.
        self.assertEqual(len(plugin.players.quota.recent(plugin.app.owner(Event('')))), 3)

    async def test_unknown_ambiguous_and_unsearchable_names(self):
        plugin = self.plugin(names=FakeNames({'pve': [(5, 'Twin_Name'), (6, 'twin_name')]}))
        [reply] = await self.ask(plugin, '/tarkov Nobody_Here')
        self.assertIn('Atlas 在 PVE 模式下没有找到名为 Nobody_Here 的玩家', reply)
        self.clock.advance(60)
        self.atlas.searches['Close_Name'] = [(7, 'Close_Name2')]
        [reply] = await self.ask(plugin, '/tarkov Close_Name')
        self.assertIn('Atlas 搜索到的相近名字：Close_Name2（账号 ID 7）', reply)
        [reply] = await self.ask(plugin, '/tarkov TWIN_NAME', sender='bob')
        self.assertIn('有多个名为 TWIN_NAME 的玩家', reply)
        [reply] = await self.ask(plugin, '/tarkov TarkovCitizen1234567', sender='carol')
        self.assertIn('Atlas 只能搜索 3～15 个字符的昵称', reply)
        self.assertEqual([call[0] for call in self.atlas.calls], ['search', 'search', 'search'])

    async def test_bind_then_query_yourself_and_unbind(self):
        plugin = self.plugin()
        [reply] = await self.ask(plugin, '/tarkov')
        self.assertIn('你还没有绑定账号', reply)
        [reply] = await self.ask(plugin, '/tarkov 绑定')
        self.assertIn('用法：@机器人 tarkov 绑定 <昵称、账号ID 或玩家页网址>', reply)
        self.atlas.put(1, 'pve')
        self.atlas.searches['Test_Player'] = [(1, 'Test_Player')]
        [reply] = await self.ask(plugin, '/tarkov 绑定 Test_Player')
        self.assertIn('已绑定：Test_Player（账号 ID 1）', reply)
        [reply] = await self.ask(plugin, '/tarkov')
        self.assertTrue(reply.startswith('[picture]'))
        # The binding belongs to the user on the platform, in whatever chat.
        [reply] = await self.ask(plugin, '/tarkov', origin='bot:FriendMessage:alice')
        self.assertTrue(reply.startswith('[picture]'))
        self.assertEqual(await self.ask(plugin, '/tarkov 解绑'), ['已解除绑定：Test_Player（账号 ID 1）。'])
        self.assertEqual(await self.ask(plugin, '/tarkov 解绑'), ['你还没有绑定账号。'])

    async def test_cache_refresh_and_the_users_own_limits(self):
        plugin = self.plugin()
        for aid in range(1, 5):
            self.atlas.put(aid, 'pve')
        await self.ask(plugin, '/tarkov 1')
        # A cached profile is free and immediate; another player needs the interval.
        self.assertTrue((await self.ask(plugin, '/tarkov 1'))[0].startswith('[picture]'))
        self.assertIn('查询太快了', (await self.ask(plugin, '/tarkov 2'))[0])
        self.clock.advance(30)
        await self.ask(plugin, '/tarkov 1 刷新')
        self.assertEqual(self.atlas.calls, [('lookup', 1, 'pve')]*2)
        self.clock.advance(60)
        self.assertIn('刷新太频繁了', (await self.ask(plugin, '/tarkov 1 刷新'))[0])
        self.assertIn('只是缓存', '只是缓存' if (await self.ask(plugin, '/tarkov 1'))[0].startswith('[picture]') else '')
        # Other users have their own allowance.
        self.assertTrue((await self.ask(plugin, '/tarkov 2', sender='bob'))[0].startswith('[picture]'))

    async def test_a_query_refused_before_it_is_sent_does_not_count(self):
        plugin = self.plugin()
        self.atlas.docs[(1, 'pve')] = self.core('atlas_cooldown').AtlasBudgetError(60)
        [reply] = await self.ask(plugin, '/tarkov 1')
        self.assertIn('Atlas 玩家查询总额度已用完', reply)
        self.assertEqual(plugin.players.quota.recent(plugin.app.owner(Event(''))), [])
        self.assertEqual(self.fetched, [])  # The plugin's own budget is not a reason to use the snapshot.

    async def test_atlas_failure_uses_the_snapshot_and_says_so_but_no_such_player_does_not(self):
        plugin = self.plugin()
        self.snapshots[('pve', 1)] = profile()
        self.atlas.docs[(1, 'pve')] = self.error(502)
        [reply] = await self.ask(plugin, '/tarkov 1')
        self.assertEqual(reply, '[picture]\n刷新数据：在 tarkov.dev 打开 https://tarkov.dev/players/pve/1')
        name, card = self.card()
        self.assertEqual(name, 'render_image')
        self.assertIn('下面是 tarkov.dev 的快照（不是实时数据）', card['notes'][0])
        [reply] = await self.ask(plugin, '/tarkov 2', sender='bob')
        self.assertIn('Atlas 找不到账号 ID 2，请检查账号 ID 或昵称。', reply)
        self.atlas.docs[(3, 'season')] = self.error(404, 'no_profile')
        [reply] = await self.ask(plugin, '/tarkov 赛季 3', sender='carol')
        self.assertIn('PVP 赛季 模式下没有档案（Atlas）。可以换个模式查询', reply)
        self.assertEqual(self.fetched, [('pve', 1)])
        # No snapshot either: the reply names both failures.
        self.atlas.docs[(4, 'pve')] = self.error(502)
        [reply] = await self.ask(plugin, '/tarkov 4', sender='dave')
        self.assertIn('tarkov.dev 上也没有账号 ID 4 的 PVE 快照', reply)

    async def test_a_renamed_player_is_checked_with_atlas_live_search(self):
        plugin = self.plugin()
        await asyncio.to_thread(plugin.app.accounts.remember, 1, 'Old_Name')
        self.atlas.put(1, 'pve', nickname='New_Name')
        self.atlas.put(2, 'pve', nickname='Old_Name')
        self.atlas.searches['Old_Name'] = [(2, 'Old_Name')]
        [reply] = await self.ask(plugin, '/tarkov Old_Name')
        self.assertIn('记录中的 Old_Name 对应账号 ID 1（现在叫 New_Name）；Atlas 搜索到的 Old_Name 是账号 ID 2', reply)
        self.assertEqual(self.atlas.calls, [('lookup', 1, 'pve'), ('live search', 'Old_Name', 'pve'), ('lookup', 2, 'pve')])
        # Nobody carries the name any more: the recorded account is shown, said to be unverified, and never bound.
        self.atlas.searches['Old_Name'] = []
        await asyncio.to_thread(plugin.app.accounts.remember, 3, 'Gone_Name')
        self.atlas.put(3, 'pve', nickname='Other_Name')
        [reply] = await self.ask(plugin, '/tarkov Gone_Name', sender='bob')
        self.assertIn('本次昵称核验未通过', reply)
        [reply] = await self.ask(plugin, '/tarkov 绑定 Gone_Name', sender='carol')
        self.assertIn('无法确认昵称 Gone_Name 对应的账号', reply)
        self.assertIsNone(await asyncio.to_thread(plugin.app.accounts.bound, plugin.app.binding_key(Event('', sender='carol'))))

    async def test_a_new_name_binds_although_the_cached_profile_shows_the_old_one(self):
        plugin = self.plugin()
        self.atlas.put(1, 'pve', nickname='Old_Name')
        await self.ask(plugin, '/tarkov 1')
        # The player renamed; the plugin still holds the profile it fetched a moment ago.
        self.atlas.put(1, 'pve', nickname='New_Name')
        self.atlas.searches['New_Name'] = [(1, 'New_Name')]
        self.clock.advance(60)
        [reply] = await self.ask(plugin, '/tarkov 绑定 New_Name')
        self.assertIn('已绑定：New_Name（账号 ID 1）', reply)

    async def test_a_snapshot_never_binds_a_renamed_account_by_its_old_name(self):
        plugin = self.plugin()
        await asyncio.to_thread(plugin.app.accounts.remember, 1, 'Old_Name')
        self.atlas.docs[(1, 'pve')] = self.error(502)
        self.snapshots[('pve', 1)] = profile(aid=1, info_nickname='New_Name')
        [reply] = await self.ask(plugin, '/tarkov 绑定 Old_Name')
        self.assertIn('已改用 tarkov.dev 快照确认账号。无法确认昵称 Old_Name 对应的账号', reply)
        self.assertIsNone(await asyncio.to_thread(plugin.app.accounts.bound, plugin.app.binding_key(Event(''))))
        # By account id the snapshot is enough.
        [reply] = await self.ask(plugin, '/tarkov 绑定 1', sender='bob')
        self.assertIn('已绑定：New_Name（账号 ID 1）', reply)

    async def test_a_picture_that_cannot_be_drawn_is_one_text_reply_with_the_links(self):
        plugin = self.plugin()
        self.atlas.put(1, 'pve')
        self.font.side_effect = self.core('font_download').FontDownloadError('完整中文字体下载失败')
        [reply] = await self.ask(plugin, '/tarkov 1')
        self.assertTrue(reply.startswith('完整中文字体下载失败\n数据来源：Atlas'))
        self.font.side_effect = RuntimeError('broken')
        [reply] = await self.ask(plugin, '/tarkov 1')
        self.assertTrue(reply.startswith('Test_Player（账号 ID 1）的 PVE 档案图片生成失败，请稍后再试。\n数据来源'))

    async def test_the_real_pictures_are_drawn(self):
        self.draw_for_real()
        plugin = self.plugin()
        plugin.players.icons = lambda card, trace: asyncio.sleep(0, {})
        self.atlas.put(1, 'pve')
        self.atlas.put(1, 'arena')
        self.snapshots[('pve', 2)] = profile(aid=2)
        self.snapshots[('arena', 2)] = arena_profile(aid=2)
        self.atlas.docs[(2, 'pve')] = self.atlas.docs[(2, 'arena')] = self.error(502)
        for text, sender in (('/tarkov 1', 'a'), ('/tarkov 竞技场 1', 'b'), ('/tarkov 2', 'c'), ('/tarkov 竞技场 2', 'd'),
                             ('/tarkov 帮助', 'e')):
            [reply] = await self.replies(plugin, text, sender=sender)
            self.assertTrue(reply[0].data.startswith(b'\xff\xd8'), text)


class SnapshotSourceTests(PluginCase):
    """The "tarkov-dev" source: public snapshots, every mode looked at unless one is written."""

    def plugin(self, config=None, names=None):
        return super().plugin({'player_source': 'tarkov-dev', 'default_mode': 'pvp', **(config or {})}, names)

    def notes(self):
        return '\n'.join(self.drawn[-1][1][0]['notes'])

    async def test_a_query_without_a_mode_shows_the_first_mode_with_a_profile(self):
        plugin = self.plugin()
        self.snapshots[('pve', 1)] = profile(aid=1)
        [reply] = await self.ask(plugin, '/tarkov 1')
        self.assertEqual(reply, '[picture]\n刷新数据：在 tarkov.dev 打开 https://tarkov.dev/players/pve/1')
        self.assertIn('tarkov.dev 上还没有这个账号的 PVP 档案', self.notes())
        self.assertEqual(sorted(self.fetched), [('profile', 1), ('pve', 1), ('pvp-season', 1)])
        # A mode that was written is the only one shown; Atlas is never asked.
        [reply] = await self.ask(plugin, '/tarkov pvp 1')
        self.assertIn('PVP 模式下还没有账号 ID 1 的档案，但 PVE 模式（@机器人 tarkov pve 1）下有档案', reply)
        [reply] = await self.ask(plugin, '/tarkov 竞技场 1')
        self.assertIn('tarkov.dev 上还没有账号 ID 1 的竞技场档案', reply)
        self.assertEqual(self.atlas.calls, [])

    async def test_a_failed_mode_is_never_reported_as_no_profile(self):
        plugin = self.plugin()
        self.snapshots[('profile', 1)] = self.core('transport').HTTPError('tarkov.dev 暂时限制了访问频率（HTTP 429）', 429)
        [reply] = await self.ask(plugin, '/tarkov 1')
        self.assertEqual(reply, '查询失败：tarkov.dev 暂时限制了访问频率（HTTP 429）')
        self.snapshots[('profile', 2)] = self.snapshots[('profile', 1)]
        self.snapshots[('pve', 2)] = profile(aid=2)
        await self.ask(plugin, '/tarkov 2')
        self.assertIn('PVP 模式这次没有查到（tarkov.dev 暂时限制了访问频率（HTTP 429）），下面是 PVE 模式的档案', self.notes())

    async def test_names_are_learned_from_queries_and_renames_are_noted(self):
        plugin = self.plugin()
        self.snapshots[('profile', 1)] = profile(aid=1)
        [reply] = await self.ask(plugin, '/tarkov Test_Player')
        self.assertIn('还不知道 Test_Player 的账号 ID。', reply)
        self.assertIn('https://tarkov.dev/players?gameMode=regular', reply)
        await self.ask(plugin, '/tarkov https://tarkov.dev/players/regular/1')
        self.assertTrue((await self.ask(plugin, '/tarkov test_player'))[0].startswith('[picture]'))
        self.snapshots[('profile', 1)] = profile(aid=1, info_nickname='New_Name')
        await self.ask(plugin, '/tarkov https://tarkov.dev/players/regular/1')
        await self.ask(plugin, '/tarkov Test_Player')
        self.assertIn('记录中的 Test_Player 对应账号 ID 1，档案里现在的昵称是 New_Name', self.notes())

    async def test_the_name_list_is_asked_only_when_the_record_knows_nothing(self):
        names = FakeNames({'profile': [(1, 'Test_Player'), (2, 'Twin'), (3, 'twin')]}, downloaded=False)
        plugin = self.plugin(names=names)
        self.snapshots[('profile', 1)] = profile(aid=1)
        notice, reply = await self.ask(plugin, '/tarkov Test_Player')
        self.assertIn('正在下载 PVP 玩家名单（第一次按昵称查询这个模式时需要，约 30 MB', notice)
        self.assertTrue(reply.startswith('[picture]'))
        names.downloaded = True
        await self.ask(plugin, '/tarkov Test_Player')
        self.assertEqual(names.lookups, [('profile', 'Test_Player')])
        [reply] = await self.ask(plugin, '/tarkov TWIN')
        self.assertIn('有多个名为 TWIN 的玩家', reply)
        [reply] = await self.ask(plugin, '/tarkov Nobody')
        self.assertIn('记录和 tarkov.dev 的 PVP 昵称名单（每周更新一次）里都没有 Nobody', reply)

    async def test_bind_needs_a_snapshot_in_some_mode(self):
        plugin = self.plugin()
        [reply] = await self.ask(plugin, '/tarkov 绑定 1')
        self.assertIn('tarkov.dev 上还没有账号 ID 1 的档案（PVP、PVE、赛季都没有）', reply)
        self.snapshots[('pve', 1)] = profile(aid=1)
        [reply] = await self.ask(plugin, '/tarkov 绑定 https://tarkov.dev/players/pve/1')
        self.assertIn('已绑定：Test_Player（账号 ID 1），目前在 PVE 模式下有档案', reply)
        self.assertTrue((await self.ask(plugin, '/tarkov'))[0].startswith('[picture]'))

    async def test_profiles_are_cached_and_a_link_skips_the_cache(self):
        plugin = self.plugin()
        self.snapshots[('profile', 1)] = profile(aid=1)
        await self.ask(plugin, '/tarkov pvp 1')
        await self.ask(plugin, '/tarkov pvp 1')
        self.assertEqual(self.fetched.count(('profile', 1)), 1)
        await self.ask(plugin, '/tarkov https://tarkov.dev/players/regular/1')
        self.assertEqual(self.fetched.count(('profile', 1)), 2)


class CommandTests(PluginCase):
    """What every command shares: who may use it, how it is written, one at a time per sender."""

    async def test_bad_input(self):
        plugin = self.plugin()
        for text, part in (('/tarkov a!', '请发送昵称（字母、数字、下划线和连字符，长度 3～24）'),
                           ('/tarkov １２３', '请发送昵称'),
                           ('/tarkov pve https://tarkov.dev/players/regular/1', '网址里的模式是 PVP，和你写的 PVE 不一致'),
                           ('/tarkov https://tarkov.dev/players/unknown/1', '插件不认识这个网址里的模式')):
            with self.subTest(text=text):
                self.assertIn(part, (await self.ask(plugin, text))[0])
        # Too many words: the help.
        [reply] = await self.ask(plugin, '/tarkov pve One Two')
        self.assertTrue(reply.startswith('[picture]\ntarkov.dev 玩家搜索：'))
        self.assertEqual(self.atlas.calls, [])

    async def test_help_is_a_picture_and_text_when_it_cannot_be_drawn(self):
        plugin = self.plugin({'status_enabled': True, 'command_style': 'slash'})
        [reply] = await self.ask(plugin, '/塔科夫 帮助')
        self.assertEqual(reply, '[picture]\ntarkov.dev 玩家搜索：https://tarkov.dev/players?gameMode=pve')
        card = self.drawn[-1][1][0]
        commands = [command for _, command, _ in card['rows']]
        self.assertIn('/tarkov status', commands)
        self.assertNotIn('/tarkov boss [模式] [变化]', commands)
        self.font.side_effect = RuntimeError('broken')
        [reply] = await self.ask(plugin, '/tarkov help')
        self.assertTrue(reply.startswith('帮助图片生成失败，请稍后再试。'))
        self.assertIn('查别人：/tarkov [模式] <昵称、账号ID 或网址>', reply)
        self.assertTrue(reply.endswith('tarkov.dev 玩家搜索：https://tarkov.dev/players?gameMode=pve'))

    async def test_access_modes_and_allowed_chats(self):
        self.atlas.put(1, 'pve')
        denied = ['此会话或用户没有塔科夫查询权限。']
        plugin = self.plugin({'access_mode': 'admin_only', 'status_enabled': True})
        for text in ('/tarkov 1', '/tarkov status', '/tarkov 帮助', '/tarkov 周报'):
            self.assertEqual(await self.ask(plugin, text), denied)
        self.assertTrue((await self.ask(plugin, '/tarkov 1', admin=True))[0].startswith('[picture]'))
        plugin = self.plugin({'access_mode': 'admin_and_whitelist', 'user_whitelist': [' alice ', '']})
        self.assertTrue((await self.ask(plugin, '/tarkov 1'))[0].startswith('[picture]'))
        self.assertEqual(await self.ask(plugin, '/tarkov 1', sender='bob'), denied)
        plugin = self.plugin({'allowed_origins': ['bot:GroupMessage:g2']})
        self.assertEqual(await self.ask(plugin, '/tarkov 1', admin=True), denied)
        self.assertTrue((await self.ask(plugin, '/tarkov 1', origin='bot:GroupMessage:g2'))[0].startswith('[picture]'))
        # A setting of the wrong type refuses everyone instead of allowing everyone.
        plugin = self.plugin({'allowed_origins': {'bot:GroupMessage:g1': True}})
        self.assertEqual(await self.ask(plugin, '/tarkov 1', admin=True), denied)
        # Only the three allowed queries reached Atlas (each plugin has its own cache).
        self.assertEqual(self.atlas.calls, [('lookup', 1, 'pve')]*3)

    async def test_one_command_per_sender_at_a_time_whatever_the_outcome(self):
        plugin = self.plugin({'status_enabled': True})
        gate = asyncio.Event()

        async def slow(aid, mode, **kwargs):
            await gate.wait()
            raise self.error(404, 'not_found')
        self.atlas.lookup = slow
        first = asyncio.ensure_future(self.ask(plugin, '/tarkov 1'))
        await asyncio.sleep(0.05)
        busy = ['你的上一次查询还没完成，请稍候。']
        self.assertEqual(await self.ask(plugin, '/tarkov 2'), busy)
        self.assertEqual(await self.ask(plugin, '/tarkov status'), busy)
        # Someone else is not held up, and the same sender in another chat is someone else's query.
        self.assertNotEqual(await self.ask(plugin, '/tarkov status', sender='bob'), busy)
        gate.set()
        self.assertIn('Atlas 找不到账号 ID 1', (await first)[0])
        self.assertNotEqual(await self.ask(plugin, '/tarkov status'), busy)
        # A cancelled command frees its sender too.
        gate.clear()
        cancelled = asyncio.ensure_future(self.ask(plugin, '/tarkov 3'))
        await asyncio.sleep(0.05)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        self.assertEqual(plugin.app.active, set())

    async def test_qq_single_word_commands(self):
        plugin = self.plugin()
        self.atlas.put(1, 'pve')
        qq = Event('tpve 1', platform='qq_official')
        [reply] = [text_of(reply) async for reply in plugin.qq_tpve(qq)]
        self.assertTrue(reply.startswith('[picture]'))
        self.assertTrue(qq.stopped)
        [reply] = [text_of(reply) async for reply in plugin.qq_thelp(Event('/thelp', platform='qq_official_webhook'))]
        self.assertTrue(reply.startswith('[picture]\ntarkov.dev 玩家搜索'))
        [reply] = [text_of(reply) async for reply in plugin.qq_slash_command(Event('/tarkov 解绑', platform='qq_official'))]
        self.assertEqual(reply, '你还没有绑定账号。')
        # Not on other platforms, and not when switched off.
        self.assertEqual([reply async for reply in plugin.qq_tpve(Event('tpve 1'))], [])
        off = self.plugin({'qq_single_word_commands': False})
        self.assertEqual([reply async for reply in off.qq_tpve(Event('tpve 1', platform='qq_official'))], [])

    async def test_start_up_removes_retired_files_and_runs_one_shot_actions_once(self):
        for name in ('atlas-history.sqlite3', 'atlas-access.json'):
            (self.root/name).write_bytes(b'old')
        saved = []
        config = type('Config', (dict,), {'save_config': lambda self: saved.append(dict(self))})(
            {'atlas_assets_clear': True, 'atlas_history_enabled': False, 'atlas_access_reset': True})
        plugin = self.plugin(config)
        (plugin.players.assets.root).mkdir(parents=True, exist_ok=True)
        (plugin.players.assets.root/'0123.png').write_bytes(b'icon')
        await plugin.initialize()
        self.assertFalse((self.root/'atlas-history.sqlite3').exists() or (self.root/'atlas-access.json').exists())
        self.assertFalse((plugin.players.assets.root/'0123.png').exists())
        # Switched off and saved before the action ran, so a reload does not repeat it.
        self.assertEqual([entry['atlas_assets_clear'] for entry in saved], [False])
        await plugin.initialize()
        self.assertEqual(len(saved), 1)


if __name__ == '__main__':
    unittest.main()
