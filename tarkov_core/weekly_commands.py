"""`tarkov 周报`: each chat's own roster of players, their last seven days as pictures, and the weekly broadcast.

A chat's roster is kept by AstrBot administrators with commands sent in that chat; its reports, ranking and weekly
broadcast are about those players only. Sampling is shared: an account listed in several chats is pulled once a
night. Everything is read from the stored samples; the query commands never ask Atlas.
"""
import asyncio
import re
import sqlite3
import time

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Plain

from .atlas_client import NICKNAME as ATLAS_NICKNAME
from .matching_commands import SOURCE_LINE
from .modes import GAME_MODES, label_of, parse_mode
from .names import NAME
from .notifications import PermanentDeliveryError
from .status_commands import push_error
from .weekly_image import render_weekly_image
from .weekly_report import SLACK, WEEK, build_card, build_ranking, build_report, extract_sample
from .weekly_store import CHAT_PLAYERS, MODE_PLAYERS, SampleResult, WeeklyStore
from .weekly_tasks import WeeklyBroadcast, WeeklySampler, parse_schedule

ADD_WORDS = ('add', '添加')
REMOVE_WORDS = ('remove', '移除', '删除')
LIST_WORDS = ('list', '名单')
RANK_WORDS = ('rank', 'ranking', '排行')
ACCOUNT_ID = re.compile(r'[1-9][0-9]{0,11}')
MODE_WORDS = {'pvp': 'pvp', 'pve': 'pve', 'season': '赛季'}
TOO_FEW = '插件每天凌晨采样一次，至少要有隔天的两次样本。'


class WeeklyCommands:
    def __init__(self, app, push):
        self.app, self.push = app, push
        self.clock = time.time
        self.on_error = push_error('weekly')
        self.store = WeeklyStore(app.root/'weekly.sqlite3', keep_days=lambda: app.config.int('weekly_keep_days'))
        self.sampler = WeeklySampler(self.store, self.store.entries, self.resolve, self.fetch, self.save,
                                     self.sampling, self.on_error, paused=lambda: app.atlas.refused('players'))
        self.broadcast = WeeklyBroadcast(self.store, self.schedule, self.chats, self.ranking_picture, self.send,
                                         self.broadcasting, self.on_error)

    # ---- settings and lifecycle ----

    def enabled(self):
        return self.app.config.flag('weekly_enabled') and self.app.player_source() == 'atlas'

    def sampling(self):
        return not self.app.terminated and self.enabled()

    def broadcasting(self):
        return self.sampling() and self.app.config.flag('weekly_push_enabled')

    def schedule(self):
        """(weekday 0-6, hour, minute) of the broadcast, UTC+8; Monday 10:00 when the settings are unusable."""
        config = self.app.config
        return parse_schedule(config.get('weekly_push_weekday', '周一'), config.get('weekly_push_time', '10:00')) or (0, 10, 0)

    async def chats(self):
        """{chat: [modes]} of the chats that get the broadcast: those with a roster a push may be sent to."""
        listed = await asyncio.to_thread(self.store.chats)
        return {chat: listed[chat] for chat in self.push.usable(listed, qq_enabled=True)}

    async def start(self):
        try:
            await asyncio.to_thread(self.store.prune)
            await asyncio.to_thread(self.adopt_settings_rosters)
        except (OSError, sqlite3.Error) as exc:
            logger.warning('Tarkov weekly data maintenance failed: %s', type(exc).__name__)
        if self.app.config.flag('weekly_enabled') and self.app.player_source() != 'atlas':
            logger.info('Tarkov weekly report idle: it needs the Atlas player source')
        for task, active in ((self.sampler, self.sampling()), (self.broadcast, self.broadcasting())):
            if active:
                task.start()
            else:
                await task.close()

    def adopt_settings_rosters(self):
        """Before 0.7.0 the rosters were three lists in the settings, broadcast to listed chats. Each of those chats
        starts with those lists as its own roster (once: only while no chat has a roster)."""
        old = self.app.config.raw.get('weekly')
        if not isinstance(old, dict) or self.store.chats():
            return
        chats = self.push.usable(old.get('weekly_notify_targets') or [], qq_enabled=True)
        for chat in chats:
            for mode in GAME_MODES:
                for entry in old.get(f'weekly_{mode}_players') or []:
                    if usable_entry(str(entry).strip()):
                        self.store.add(chat, mode, str(entry).strip())
        if chats:
            logger.info('Tarkov weekly rosters of the old settings adopted by %s chats', len(chats))

    # ---- what the nightly sampler calls ----

    async def resolve(self, mode, entry, *, deadline=None):
        """(account id, nickname, whether Atlas was asked) of a listed nickname: the shared name record first, then
        Atlas' search (one request). (None, None, asked) when it cannot be settled; an account id is the safe entry."""
        rows, _ = await asyncio.to_thread(self.app.accounts.lookup, entry)
        exact = [row for row in rows if row[1] == entry]
        if len(exact) == 1 or len(rows) == 1:
            account_id, name = (exact or rows)[0]
            return int(account_id), name, False
        if not ATLAS_NICKNAME.fullmatch(entry):
            logger.warning('Tarkov weekly roster: %s (%s) cannot be searched on Atlas; list the account ID instead', entry, mode)
            return None, None, False
        found = await self.app.atlas_players.search_once(entry, mode, deadline=deadline)
        exact = [item for item in found if item[1] == entry]
        folded = [item for item in found if item[1].lower() == entry.lower()]
        pick = exact if len(exact) == 1 else folded if len(folded) == 1 else None
        if not pick:
            logger.warning('Tarkov weekly roster: %s (%s) not found or ambiguous on Atlas; list the account ID instead', entry, mode)
            return None, None, True
        return pick[0][0], pick[0][1], True

    async def fetch(self, mode, account_id, *, deadline=None):
        """One fresh Atlas document for the nightly sample (no user quota: the sampler paces itself)."""
        summary = await self.app.atlas_players.profile_once(mode, account_id, deadline=deadline, include_art=False)
        try:
            await asyncio.to_thread(self.app.accounts.remember, account_id, summary['nickname'])
        except sqlite3.Error as exc:
            logger.warning('Tarkov name record not saved: %s', type(exc).__name__)
        return summary

    async def save(self, account_id, mode, summary, *, day=None):
        sample = await asyncio.to_thread(extract_sample, summary.get('doc'), mode)
        if sample is None:
            return SampleResult.INVALID
        return await asyncio.to_thread(self.store.add_sample, account_id, mode, sample, summary.get('fetched'), day=day)

    async def feed(self, account_id, mode, summary):
        """A user's fresh Atlas lookup of a listed player is one more sample, at no cost."""
        if not self.enabled() or mode not in GAME_MODES:
            return
        try:
            if await asyncio.to_thread(self.store.listed, mode, account_id, summary['nickname']):
                await self.save(account_id, mode, summary)
        except (OSError, sqlite3.Error) as exc:
            logger.warning('Tarkov weekly sample not saved: %s', type(exc).__name__)

    # ---- reports ----

    async def report(self, account_id, mode):
        now = self.clock()
        samples = await asyncio.to_thread(self.store.samples, account_id, mode, now-WEEK-SLACK-86400)
        return build_report(samples, mode, now)

    async def ranking(self, chat, mode):
        """The ranking card of a chat's roster in a mode, or None when none of its players has a report yet."""
        entries, seen = [], set()
        for entry, account_id, name in await asyncio.to_thread(self.store.roster, chat, mode):
            # Two entries may be the same account (an id and a nickname): rank it once.
            if account_id in seen:
                continue
            if account_id is not None:
                seen.add(account_id)
            entries.append((name or entry, await self.report(account_id, mode) if account_id is not None else None))
        return build_ranking(entries, mode)

    async def ranking_picture(self, chat, mode):
        card = await self.ranking(chat, mode)
        return await self.app.render(render_weekly_image, card) if card else None

    async def send(self, chat, picture):
        if not self.broadcasting() or chat not in await self.chats():
            raise PermanentDeliveryError('目标不再允许周报播报')
        return await self.app.context.send_message(chat, MessageChain(chain=[Image.fromBytes(picture), Plain(SOURCE_LINE)]))

    # ---- the command ----

    async def query(self, event, arguments):
        """`周报 [模式] [昵称、账号ID 或 排行]`, and for administrators `周报 添加|移除 [模式] <昵称或账号ID>`,
        `周报 名单 [模式]`."""
        if not self.app.config.flag('weekly_enabled'):
            return event.plain_result('玩家周报尚未开启，请联系管理员在设置中启用。')
        if self.app.player_source() != 'atlas':
            return event.plain_result('玩家周报需要 Atlas 数据来源，当前设置的是 tarkov.dev 快照。')
        words = arguments.split()
        action = None
        if words and words[0].lower() in ADD_WORDS+REMOVE_WORDS+LIST_WORDS:
            action, words = words[0].lower(), words[1:]
        mode = parse_mode(words[0]) if words else None
        if mode:
            words = words[1:]
        if mode == 'arena':
            return event.plain_result('竞技场周报还没有实现，目前支持 pvp、pve、赛季。')
        if len(words) > 1:
            return event.plain_result(self.usage())
        mode = mode or self.app.default_mode()
        target = words[0] if words else None
        chat = event.unified_msg_origin
        with self.app.busy(event):
            try:
                if action in LIST_WORDS:
                    return event.plain_result(await self.listing(chat, mode))
                if action:
                    return event.plain_result(await self.change(event, action in ADD_WORDS, chat, mode, target))
                return await self.show(event, chat, mode, target)
            except (OSError, sqlite3.Error) as exc:
                logger.warning('Tarkov weekly query failed: %s', type(exc).__name__)
                return event.plain_result('周报数据读取失败，请稍后再试。')

    def usage(self):
        cmd = self.app.cmd
        return (f"用法：{cmd('周报 [模式] [昵称、账号ID 或 排行]')}\n"
                f"名单（每个聊天各自维护，仅 AstrBot 管理员可改）：{cmd('周报 添加 [模式] <昵称或账号ID>')}、"
                f"{cmd('周报 移除 [模式] <昵称或账号ID>')}、{cmd('周报 名单 [模式]')}")

    async def change(self, event, adding, chat, mode, target):
        """Add a player to this chat's roster of a mode, or take one off; the reply."""
        label = label_of(mode)
        if not event.is_admin():
            return '只有 AstrBot 管理员可以修改周报名单。'
        if target is None:
            return self.usage()
        if not adding:
            removed = await asyncio.to_thread(self.store.remove, chat, mode, target)
            return (f'已把 {target} 移出本聊天的 {label} 周报名单；已有的样本到期后自动删除。' if removed
                    else f'{target} 不在本聊天的 {label} 周报名单内。')
        if not usable_entry(target):
            return '请填写账号 ID（数字，推荐）或昵称（字母、数字、_ -，长度 3～24）。重名、改过名或超过 15 个字符的昵称请用账号 ID。'
        outcome = await asyncio.to_thread(self.store.add, chat, mode, target)
        if outcome == 'chat_full':
            return f'本聊天的 {label} 周报名单已满（{CHAT_PLAYERS} 名），请先移除不需要的玩家。'
        if outcome == 'mode_full':
            return f'{label} 模式每晚最多采样 {MODE_PLAYERS} 名玩家（所有聊天合计），名额已满。'
        if outcome == 'listed':
            return f'{target} 已经在本聊天的 {label} 周报名单内。'
        self.sampler.start()
        return (f'已把 {target} 加入本聊天的 {label} 周报名单。{TOO_FEW}'
                f"查看名单：{self.app.cmd('周报 名单', MODE_WORDS[mode])}")

    async def listing(self, chat, mode):
        players = await asyncio.to_thread(self.store.roster, chat, mode)
        if not players:
            return self.empty(mode)
        lines = [f'{name}（账号 ID {account_id}）' if name else f'{entry}（尚未采样）' for entry, account_id, name in players]
        return f'本聊天的 {label_of(mode)} 周报名单（{len(players)} 名）：\n'+'\n'.join(lines)

    def empty(self, mode):
        return (f"本聊天的 {label_of(mode)} 周报名单是空的。AstrBot 管理员可以发送 "
                f"{self.app.cmd('周报 添加', MODE_WORDS[mode], '<昵称或账号ID>')} 添加玩家。")

    async def show(self, event, chat, mode, target):
        """A listed player's report (the sender's own bound account when no name is given and it is listed), else
        the ranking of this chat's roster."""
        label = label_of(mode)
        players = await asyncio.to_thread(self.store.roster, chat, mode)
        if not players:
            return event.plain_result(self.empty(mode))
        chosen = None
        if target and target.lower() not in RANK_WORDS:
            wanted = target.lower()
            chosen = next((player for player in players
                           if wanted in (player[0].lower(), str(player[1]), (player[2] or '').lower())), None)
            if chosen is None:
                return event.plain_result(f"{target} 不在本聊天的 {label} 周报名单内。"
                                          f"看排行：{self.app.cmd('周报', MODE_WORDS[mode], '排行')}")
        elif target is None:
            bound = await asyncio.to_thread(self.app.accounts.bound, self.app.binding_key(event))
            chosen = next((player for player in players if bound and player[1] == bound[0]), None)
        if chosen is not None:
            report = await self.report(chosen[1], mode) if chosen[1] is not None else None
            if report is None:
                return event.plain_result(f'{chosen[2] or chosen[0]} 的 {label} 周报样本还不够：{TOO_FEW}')
            card = build_card(report)
        else:
            card = await self.ranking(chat, mode)
            if card is None:
                return event.plain_result(f'本聊天 {label} 周报名单里的玩家样本还不够：{TOO_FEW}')
        try:
            picture = await self.app.render(render_weekly_image, card)
        except Exception as exc:
            return event.plain_result(self.app.render_failure(exc, '周报'))
        return event.chain_result([Image.fromBytes(picture), Plain(SOURCE_LINE)])

    async def close(self):
        await self.sampler.close()
        await self.broadcast.close()


def usable_entry(text):
    """Whether a roster entry is an account id or something that can be a nickname."""
    return bool(ACCOUNT_ID.fullmatch(text) or NAME.fullmatch(text) or ATLAS_NICKNAME.fullmatch(text))
