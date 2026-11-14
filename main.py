"""The AstrBot plugin: registers the commands, checks who may use them and hands each to its feature
(tarkov_core/*_commands.py). What the features share lives in tarkov_core/app.py."""
import asyncio
import re

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools, register

from .tarkov_core import NAME as PLUGIN_NAME
from .tarkov_core.app import DENIED, App, Busy
from .tarkov_core.boss_commands import BossCommands
from .tarkov_core.help_image import render_help_image
from .tarkov_core.help_text import help_card, help_text, search_line
from .tarkov_core.matching_commands import MatchingCommands
from .tarkov_core.modes import label_of, page_url, parse_mode, parse_player_url
from .tarkov_core.names import NAME as NICKNAME
from .tarkov_core.platform_compat import is_qq_official
from .tarkov_core.player_commands import PlayerCommands
from .tarkov_core.push_targets import PushTargets
from .tarkov_core.settings import Settings
from .tarkov_core.snapshot_commands import ACCOUNT_ID, IndexTimeout, SnapshotCommands
from .tarkov_core.status_commands import StatusCommands
from .tarkov_core.transport import QueryError
from .tarkov_core.weekly_commands import WeeklyCommands

COMMAND = re.compile(r'^/?(?:tarkov|塔科夫)(?:\s+|$)', re.I)
HELP_WORDS = ('help', '帮助')
BIND_WORDS = ('bind', '绑定')
UNBIND_WORDS = ('unbind', '解绑')
# The last word of a player query: skip the local cache and ask Atlas again.
REFRESH_WORDS = ('refresh', '刷新')
# Data files of features that no longer exist, removed at start-up (0.7.0: the raw Atlas lookup history, which held
# player documents, and the saved 401/403 state).
RETIRED_FILES = ('atlas-history.sqlite3', 'atlas-history.sqlite3-journal', 'atlas-history.sqlite3-wal',
                 'atlas-history.sqlite3-shm', 'atlas-access.json')


class QQOfficialFilter(filter.CustomFilter):
    def filter(self, event, config):
        return is_qq_official(event)


@register('astrbot_tarkov_datasearch', 'Evergarden_Roxy', '查询塔科夫玩家档案（默认来自 Atlas，可改用 tarkov.dev 快照）、服务器状态、Boss 刷率和匹配时间', '0.7.0')
class TarkovPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.app = App(context, Settings(config), StarTools.get_data_dir(PLUGIN_NAME))
        self.push = PushTargets(self.app)
        self.snapshot = SnapshotCommands(self.app)
        self.weekly = WeeklyCommands(self.app, self.push)
        self.players = PlayerCommands(self.app, self.snapshot, self.weekly)
        self.status = StatusCommands(self.app, self.push)
        self.boss = BossCommands(self.app, self.push)
        self.matching = MatchingCommands(self.app)
        # First word after `tarkov` -> the feature that answers it; anything else is a player query.
        self.features = {word: feature for words, feature in (
            (('status', '状态'), self.status), (('boss', '刷率'), self.boss),
            (('matching', '匹配'), self.matching), (('weekly', '周报'), self.weekly)) for word in words}

    async def initialize(self):
        await asyncio.to_thread(self.remove_retired_files)
        for feature in (self.players, self.status, self.boss, self.weekly):
            try:
                await feature.start()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('Tarkov %s did not start: %s', type(feature).__name__, type(exc).__name__)

    def remove_retired_files(self):
        for name in RETIRED_FILES:
            try:
                (self.app.root/name).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning('Tarkov retired file %s not removed: %s', name, type(exc).__name__)

    async def terminate(self):
        self.app.terminated = True
        for feature in (self.weekly, self.matching, self.players, self.snapshot, self.status, self.boss):
            await feature.close()
        await self.app.close()

    # ---- command entries ----

    @filter.command('tarkov', alias={'塔科夫'})
    async def tarkov_command(self, event: AstrMessageEvent):
        text = COMMAND.sub('', event.message_str.strip(), count=1).strip()
        async for result in self.dispatch(event, text):
            yield result

    @filter.command('/'+'tarkov', alias={'/'+'塔科夫'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_slash_command(self, event: AstrMessageEvent):
        # A QQ mention wakes AstrBot even when '/' is not a wake prefix; the command filter then still sees the slash.
        if not is_qq_official(event):
            return
        async for result in self.tarkov_command(event):
            yield result

    # QQ official bots cannot register a command that contains a space, so each action also has a one-word command;
    # what follows the word may contain spaces (a nickname after tpve, say). Only on QQ official platforms, with the
    # setting on. One handler each: AstrBot registers a command by its handler function.
    async def qq_word(self, event, word):
        if not is_qq_official(event) or not self.app.config.flag('qq_single_word_commands'):
            return
        arguments = event.message_str.strip().split(maxsplit=1)[1:]
        async for result in self.dispatch(event, ' '.join([word, *arguments])):
            yield result

    @filter.command('tpvp', alias={'/tpvp'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_tpvp(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'pvp'): yield result

    @filter.command('tpve', alias={'/tpve'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_tpve(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'pve'): yield result

    @filter.command('tseason', alias={'/tseason'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_tseason(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'season'): yield result

    @filter.command('tarena', alias={'/tarena'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_tarena(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'arena'): yield result

    @filter.command('tbind', alias={'/tbind'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_tbind(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'bind'): yield result

    @filter.command('tunbind', alias={'/tunbind'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_tunbind(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'unbind'): yield result

    @filter.command('thelp', alias={'/thelp'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_thelp(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'help'): yield result

    @filter.command('tstatus', alias={'/tstatus'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_tstatus(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'status'): yield result

    @filter.command('tboss', alias={'/tboss'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_tboss(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'boss'): yield result

    @filter.command('tmatch', alias={'/tmatch'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_tmatch(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'matching'): yield result

    @filter.command('tweekly', alias={'/tweekly'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_tweekly(self, event: AstrMessageEvent):
        async for result in self.qq_word(event, 'weekly'): yield result

    # ---- from a command text to its feature ----

    async def dispatch(self, event, text):
        """Every entry ends here: the access check, then the feature named by the first word."""
        try:
            if not self.app.allowed(event):
                logger.info('Tarkov access denied; mode=%s', self.app.config.get('access_mode', 'all'))
                yield event.plain_result(DENIED)
                return
            if await self.push.remember(event):
                # The first group chat may arrive after start-up, when a poller had no target to run for.
                self.status.poller.start()
                self.boss.poller.start()
            words = text.split()
            first = words[0].lower() if words else ''
            if first in self.features:
                yield await self.features[first].query(event, ' '.join(words[1:]))
            elif first in HELP_WORDS:
                yield await self.help(event)
            else:
                async for result in self.player(event, words):
                    yield result
        except Busy as exc:
            yield event.plain_result(str(exc))
        finally:
            event.stop_event()

    def enabled_features(self):
        switches = {'status': self.status.enabled(), 'boss': self.boss.enabled(), 'matching': self.matching.enabled(),
                    'weekly': self.weekly.enabled(), 'qq_words': self.app.config.flag('qq_single_word_commands')}
        return {name for name, on in switches.items() if on}

    async def help(self, event):
        """The help picture followed by the search page as a text line; the same help as text when it cannot be drawn."""
        card = help_card(self.app, self.enabled_features(), self.snapshot, self.players)
        try:
            picture = await self.app.render(render_help_image, card)
        except Exception as exc:
            return event.plain_result('\n\n'.join([self.app.render_failure(exc, '帮助'), help_text(card), search_line(self.app)]))
        return event.chain_result([Image.fromBytes(picture), Plain(search_line(self.app))])

    async def player(self, event, words):
        """`[绑定|解绑] [模式] [昵称、账号ID 或玩家页网址] [刷新]`: a profile query, a binding or an unbinding."""
        cmd, atlas = self.app.cmd, self.app.player_source() == 'atlas'
        action = 'query'
        if words and words[0].lower() in UNBIND_WORDS and len(words) == 1:
            action, words = 'unbind', []
        elif words and words[0].lower() in BIND_WORDS:
            action, words = 'bind', words[1:]
        refresh = action == 'query' and bool(words) and words[-1].lower() in REFRESH_WORDS
        if refresh:
            words = words[:-1]
        mode = parse_mode(words[0]) if words else None
        if mode:
            words = words[1:]
        if len(words) > 1:
            yield await self.help(event)
            return
        target = words[0] if words else None
        if action == 'bind' and target is None:
            # Atlas resolves nicknames itself; the snapshot source needs the website link or the id.
            what = '<昵称、账号ID 或玩家页网址>' if atlas else '<tarkov.dev 玩家页网址或账号ID>'
            yield event.plain_result(f"用法：{cmd('绑定 '+what)}，例如 {cmd('绑定', page_url('pve', 1234567))}")
            return
        url = parse_player_url(target) if target else None
        if url:
            if url[0] is None:
                yield event.plain_result('插件不认识这个网址里的模式，请发送 PVP、PVE、赛季或竞技场模式的玩家页网址。')
                return
            if mode and mode != url[0]:
                yield event.plain_result(f'网址里的模式是 {label_of(url[0])}，和你写的 {label_of(mode)} 不一致，请只保留一个。')
                return
            mode = url[0]
        elif target and not ACCOUNT_ID.fullmatch(target) and not NICKNAME.fullmatch(target):
            yield event.plain_result('请发送昵称（字母、数字、下划线和连字符，长度 3～24）、账号 ID（数字）或 tarkov.dev 玩家页网址。')
            return
        # A mode word or a link chooses the mode; otherwise the default one (the snapshot source then also looks at
        # the other modes).
        explicit = mode is not None
        mode = mode or self.app.default_mode()
        with self.app.busy(event) as owner:
            trace = owner[:8]
            logger.info('Tarkov [%s] %s mode=%s target=%s refresh=%s', trace, action, mode, target, refresh)
            try:
                if action == 'unbind':
                    yield await self.unbind(event)
                elif atlas:
                    async for result in self.players.run(event, action, mode, target, url, trace, refresh, owner):
                        yield result
                elif action == 'bind':
                    yield await self.snapshot.bind(event, mode, target, url, trace)
                else:
                    async for result in self.snapshot.query(event, mode, explicit, target, url, trace, refresh):
                        yield result
            except asyncio.CancelledError:
                raise
            except QueryError as exc:
                yield event.plain_result(f'查询失败：{exc}')
            except TimeoutError as exc:
                hint = '，或在配置中调大“下载最长等待”' if isinstance(exc, IndexTimeout) else ''
                yield event.plain_result(f'查询失败：连接 tarkov.dev 超时（{exc}），请稍后再试{hint}。')
            except Exception as exc:
                logger.warning('Tarkov [%s] query failed: %s', trace, type(exc).__name__)
                yield event.plain_result(f'查询失败：{type(exc).__name__}。')

    async def unbind(self, event):
        removed = await asyncio.to_thread(self.app.accounts.unbind, self.app.binding_key(event))
        if not removed:
            return event.plain_result('你还没有绑定账号。')
        return event.plain_result(f'已解除绑定：{removed[1]}（账号 ID {removed[0]}）。')
