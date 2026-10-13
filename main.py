import asyncio
import hashlib
import re
import sqlite3
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools, register

from .tarkov_core import transport
from .tarkov_core.accounts import Accounts, owner_key
from .tarkov_core.modes import MODES, label_of, page_url, parse_mode, parse_player_url, path_of, search_url
from .tarkov_core.names import NAME, NameIndex
from .tarkov_core.card import build_card
from .tarkov_core.image import render_image
from .tarkov_core.output import render_markdown, render_text
from .tarkov_core.profile import summarize
from .tarkov_core.transport import NotFound, QueryError

COMMAND = re.compile(r'^/?(?:tarkov|塔科夫)(?:\s+|$)', re.I)
# ASCII digits only: \d would also accept full-width and other Unicode digits.
ACCOUNT_ID = re.compile(r'[0-9]{1,12}')
# Profiles kept in memory at most, whatever the cache time.
CACHE_ENTRIES = 128
LABEL_OF_PATH = {path: label for path, label, _ in MODES.values()}
OUTPUT_MODES = ('image', 'text', 'markdown')
HELP_WORDS = ('help', '帮助')
BIND_WORDS = ('bind', '绑定')
UNBIND_WORDS = ('unbind', '解绑')


class IndexTimeout(TimeoutError):
    """A name index download timed out (the one download the timeout setting can help with)."""


def every(seconds):
    """The refresh interval in words: 每周, 每 2 周, 每天, 每 3 天, 每 5 小时."""
    hours = max(1, round(seconds/3600))
    for size, unit, one in ((168, '周', '每周'), (24, '天', '每天')):
        if hours % size == 0:
            return one if hours == size else f'每 {hours//size} {unit}'
    return f'每 {hours} 小时'


@register('astrbot_tarkov_datasearch', 'Evergarden_Roxy', '绑定并查询塔科夫玩家档案（数据来自 tarkov.dev）', '0.3.0')
class TarkovPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.root = StarTools.get_data_dir('astrbot_tarkov_datasearch')
        # The downloaded name index is optional (off by default): names are mainly learned from the users (accounts.py).
        self.names = NameIndex(self.root, self.download_index, self.int_setting('name_index_refresh_hours', 168, 1, 720))
        self.accounts = Accounts(self.root/'accounts.sqlite3')
        # (mode, account id) -> (monotonic time, profile summary)
        self.cache = {}
        self.active = set()

    def int_setting(self, key, default, low, high):
        try:
            return max(low, min(high, int(self.config.get(key, default))))
        except (TypeError, ValueError, OverflowError):
            return default

    def proxy(self):
        if not self.config.get('proxy_enabled', False):
            return None
        url = str(self.config.get('proxy_url', '')).strip()
        if not url:
            # Fail closed: an empty address must not turn into a direct connection.
            raise QueryError('已开启“访问 tarkov.dev 时使用代理”，但没有填写代理地址，请在插件配置中填写或关闭代理')
        return url

    def owner(self, event):
        return hashlib.sha256((event.unified_msg_origin+'\0'+event.get_sender_id()).encode()).hexdigest()

    def config_list(self, key):
        """A list setting as strings. A string counts as one item (`in` on a string would be a substring test);
        any other type returns None, which denies access."""
        value = self.config.get(key, [])
        if isinstance(value, str):
            value = [value]
        if isinstance(value, (list, tuple, set)):
            # Surrounding spaces and empty rows (an added but unfilled row on the config page) are ignored.
            return [text for text in (str(item).strip() for item in value) if text]
        logger.warning('Tarkov setting %s is not a list (%s); access denied', key, type(value).__name__)
        return None

    def allowed(self, event):
        allow = self.config_list('allowed_origins')
        if allow is None or (allow and event.unified_msg_origin not in allow):
            return False
        mode = self.config.get('access_mode', 'all')
        admin = event.is_admin()
        if mode == 'admin_only':
            return admin
        if mode == 'admin_and_whitelist':
            return admin or str(event.get_sender_id()) in (self.config_list('user_whitelist') or [])
        return mode == 'all'

    def default_mode(self):
        mode = parse_mode(self.config.get('default_mode', 'pvp'))
        return mode or 'pvp'

    def index_enabled(self):
        return bool(self.config.get('name_index_enabled', False))

    def binding_key(self, event):
        # unified_msg_origin is "<platform id>:<message type>:<session>"; a binding holds in every chat of a platform.
        return owner_key(event.unified_msg_origin.split(':', 1)[0], event.get_sender_id())

    def help_text(self):
        index = f'；另外会查 tarkov.dev 的昵称名单（{every(self.names.refresh_seconds)}更新一次）' if self.index_enabled() else ''
        return ('塔科夫玩家档案查询（数据来自 tarkov.dev）\n'
                '绑定自己：/tarkov 绑定 <tarkov.dev 玩家页网址或账号ID>　解绑：/tarkov 解绑\n'
                '查自己：/tarkov [模式]\n'
                '查别人：/tarkov [模式] <昵称、账号ID 或玩家页网址>\n'
                f'模式：pvp、pve、赛季（pvp-season）；不写时用 {label_of(self.default_mode())}\n'
                f'第一次查某个玩家前，请先在 tarkov.dev 用对应模式搜索他一次（{search_url(self.default_mode())}），'
                '再把跳转后的网址发给我；之后就能直接用名字查（按 ID 或网址查到过的名字会被记住' + index + '）。\n'
                '档案不会自动更新：要看最新数据，请先在 tarkov.dev 上重新搜索或打开这名玩家的页面。')

    @filter.command('tarkov', alias={'塔科夫'})
    async def tarkov_command(self, event: AstrMessageEvent):
        text = COMMAND.sub('', event.message_str.strip(), count=1).strip()
        if not self.allowed(event):
            logger.info('Tarkov access denied; mode=%s', self.config.get('access_mode', 'all'))
            yield event.plain_result('此会话或用户没有塔科夫查询权限。')
            event.stop_event()
            return
        async for result in self.handle(event, text):
            yield result
        event.stop_event()

    async def handle(self, event, text):
        words = text.split()
        action = 'query'
        if words and words[0].lower() in HELP_WORDS:
            yield event.plain_result(self.help_text())
            return
        if words and words[0].lower() in UNBIND_WORDS and len(words) == 1:
            action, words = 'unbind', []
        elif words and words[0].lower() in BIND_WORDS:
            action, words = 'bind', words[1:]
        mode = parse_mode(words[0]) if words else None
        if mode:
            words = words[1:]
        if len(words) > 1:
            yield event.plain_result(self.help_text())
            return
        target = words[0] if words else None
        if action == 'bind' and target is None:
            yield event.plain_result('用法：/tarkov 绑定 <tarkov.dev 玩家页网址或账号ID>，例如 /tarkov 绑定 https://tarkov.dev/players/pve/1234567')
            return
        url = parse_player_url(target) if target else None
        if url:
            if url[0] is None:
                yield event.plain_result('插件不支持竞技场（arena）模式的档案，请发送 PVP、PVE 或赛季模式的玩家页网址。')
                return
            if mode and mode != url[0]:
                yield event.plain_result(f'网址里的模式是 {label_of(url[0])}，和你写的 {label_of(mode)} 不一致，请只保留一个。')
                return
            mode = url[0]
        elif target and not ACCOUNT_ID.fullmatch(target) and not NAME.fullmatch(target):
            yield event.plain_result('请发送昵称（字母、数字、下划线和连字符，长度 3～24）、账号 ID（数字）或 tarkov.dev 玩家页网址。')
            return
        mode = mode or self.default_mode()
        owner = self.owner(event)
        if owner in self.active:
            yield event.plain_result('你的上一次查询还没完成，请稍候。')
            return
        self.active.add(owner)
        trace = owner[:8]
        logger.info('Tarkov [%s] %s mode=%s target=%s', trace, action, mode, target)
        try:
            if action == 'unbind':
                yield await self.unbind(event)
            elif action == 'bind':
                yield await self.bind(event, mode, target, url)
            else:
                async for result in self.query(event, mode, target, url, trace):
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
        finally:
            self.active.discard(owner)

    @staticmethod
    def search_hint(mode, who='这名玩家'):
        return f'请先在 tarkov.dev 用 {label_of(mode)} 模式搜索一次{who}（{search_url(mode)}）'

    def first_time(self, mode, who='这名玩家', command='/tarkov'):
        return self.search_hint(mode, who) + f'，再把跳转后的网址发给我（例如 {command} {page_url(mode, 1234567)}）。'

    async def unbind(self, event):
        removed = await asyncio.to_thread(self.accounts.unbind, self.binding_key(event))
        if not removed:
            return event.plain_result('你还没有绑定账号。')
        return event.plain_result(f'已解除绑定：{removed[1]}（账号 ID {removed[0]}）。')

    async def bind(self, event, mode, target, url):
        label = label_of(mode)
        if url or ACCOUNT_ID.fullmatch(target):
            aid = url[1] if url else int(target)
        else:
            rows, _ = await asyncio.to_thread(self.accounts.lookup, target)
            exact = [row for row in rows if row[1] == target]
            if len(rows) != 1 and len(exact) != 1:
                return event.plain_result(f'不能确定 {target} 是哪个账号。' + self.first_time(mode, '自己', '/tarkov 绑定'))
            aid = (exact or rows)[0][0]
        try:
            summary = await self.profile(mode, aid)
        except NotFound:
            return event.plain_result(f'{label} 模式下还没有账号 ID {aid} 的档案。' + self.first_time(mode, '自己', '/tarkov 绑定'))
        await asyncio.to_thread(self.accounts.bind, self.binding_key(event), aid, summary['nickname'])
        return event.plain_result(f"已绑定：{summary['nickname']}（账号 ID {aid}）。之后发送 /tarkov 即可查询自己的档案"
                                  f"（默认 {label_of(self.default_mode())}，也可以写 /tarkov pve 等）。")

    async def query(self, event, mode, target, url, trace):
        label = label_of(mode)
        if target is None:
            bound = await asyncio.to_thread(self.accounts.bound, self.binding_key(event))
            if not bound:
                yield event.plain_result('你还没有绑定账号。先在 tarkov.dev 搜索自己的游戏名，再发送：/tarkov 绑定 <跳转后的网址>。\n'
                                         '查别人：/tarkov [模式] <昵称、账号ID 或网址>；完整用法：/tarkov 帮助')
                return
            aid, nickname = bound
            try:
                summary = await self.profile(mode, aid)
            except NotFound:
                yield event.plain_result(f'你绑定的账号 {nickname}（ID {aid}）在 {label} 模式下还没有档案。'
                                         + self.search_hint(mode, '自己') + '，几分钟后再查。')
                return
            yield await self.reply(event, summary, mode, aid, [], trace)
            return
        if url or ACCOUNT_ID.fullmatch(target):
            aid = url[1] if url else int(target)
            try:
                summary = await self.profile(mode, aid)
            except NotFound:
                # An all-digit nickname is possible; try it as a name before giving up.
                if url or not NAME.fullmatch(target):
                    yield event.plain_result(f'{label} 模式下还没有账号 ID {aid} 的档案。tarkov.dev 只保存在网站上被搜索过的玩家，'
                                             + self.search_hint(mode) + '，几分钟后再查。')
                    return
                summary = None
            if summary:
                yield await self.reply(event, summary, mode, aid, [], trace)
                return
        async for result in self.by_name(event, mode, target, trace):
            yield result

    async def by_name(self, event, mode, target, trace):
        label = label_of(mode)
        matches, total = await asyncio.to_thread(self.accounts.lookup, target)
        where = '记录'
        if not matches and self.index_enabled():
            where = '名单'
            if self.names.needs_refresh(path_of(mode)):
                if self.names.has_index(path_of(mode)):
                    yield event.plain_result(f'正在检查 {label} 玩家名单的更新（{every(self.names.refresh_seconds)}一次），请稍候。')
                else:
                    size = '，约 30 MB' if mode == 'pvp' else ''
                    yield event.plain_result(f'正在下载 {label} 玩家名单（第一次按昵称查询这个模式时需要{size}，可能需要几十秒），请稍候。')
            matches, total = await self.names.lookup(path_of(mode), target)
        logger.info('Tarkov [%s] name matches=%s source=%s', trace, total, where)
        if not matches:
            digits = f'{label} 模式下没有账号 ID {target} 的档案，' if ACCOUNT_ID.fullmatch(target) else ''
            unknown = (f'记录和 tarkov.dev 的昵称名单（{every(self.names.refresh_seconds)}更新一次）里都没有 {target}。'
                       if self.index_enabled() else f'还不知道 {target} 的账号 ID。')
            yield event.plain_result(digits+unknown+self.first_time(mode)+'查到一次后，之后就能直接用名字查询。')
            return
        exact = [match for match in matches if match[1] == target]
        if len(exact) != 1 and len(matches) > 1:
            listed = '\n'.join(f'{name}（ID {aid}）' for aid, name in matches[:5])
            more = f'\n等，共 {total} 个' if total > 5 else ''
            yield event.plain_result(f'有多个名为 {target} 的玩家（不区分大小写），请用账号 ID 或网址查询：\n{listed}{more}')
            return
        aid = (exact or matches)[0][0]
        try:
            summary = await self.profile(mode, aid)
        except NotFound:
            yield event.plain_result(f'{target} 的账号 ID 是 {aid}，但 {label} 模式下还没有档案。'
                                     f'请先在 tarkov.dev 打开 {page_url(mode, aid)}（或用这个模式搜索一次），几分钟后再查。')
            return
        notes = []
        if summary['nickname'].lower() != target.lower():
            notes.append(f'注意：{where}中的 {target} 对应账号 ID {aid}，档案里现在的昵称是 {summary["nickname"]}，可能改过名。')
        yield await self.reply(event, summary, mode, aid, notes, trace)

    def output_mode(self):
        mode = str(self.config.get('output_mode', 'image'))
        return mode if mode in OUTPUT_MODES else 'image'

    async def reply(self, event, summary, game_mode, aid, notes, trace):
        """The profile in the configured output: an image (text if drawing fails), plain text or Markdown.
        Every reply says the data is a snapshot and links the player's page, where it can be refreshed."""
        refresh = page_url(game_mode, aid)
        card = build_card(summary, label_of(game_mode), self.skill_count(), notes, refresh_url=refresh)
        mode = self.output_mode()
        if mode == 'image':
            try:
                data = await asyncio.to_thread(render_image, card)
                # The image cannot carry a clickable link; one text line after it does.
                return event.chain_result([Image.fromBytes(data), Plain(f'刷新数据：在 tarkov.dev 打开 {refresh}')])
            except Exception as exc:
                logger.warning('Tarkov [%s] image rendering failed, sending text: %s', trace, type(exc).__name__)
                card['notes'].append('（图片生成失败，已改为文字）')
                return event.plain_result(render_text(card))
        if mode == 'markdown':
            return event.plain_result(render_markdown(card))
        return event.plain_result(render_text(card))

    def skill_count(self):
        return self.int_setting('skill_count', 10, 0, 49)

    async def profile(self, mode, aid):
        """Summary of one profile, from the in-memory cache when it is recent enough."""
        key = (mode, aid)
        ttl = self.int_setting('profile_cache_minutes', 10, 0, 1440)*60
        cached = self.cache.get(key)
        if cached and time.monotonic()-cached[0] < ttl:
            return cached[1]
        raw = await transport.fetch_profile(path_of(mode), aid, timeout_seconds=min(60, self.timeout()), proxy=self.proxy())
        summary = summarize(raw)
        # Every profile fetched teaches the shared name record (maintainer: names come from the users).
        try:
            await asyncio.to_thread(self.accounts.remember, aid, summary['nickname'])
        except sqlite3.Error as exc:
            logger.warning('Tarkov name record not saved: %s', type(exc).__name__)
        if ttl:
            self.cache.pop(key, None)
            if len(self.cache) >= CACHE_ENTRIES:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic(), summary)
        return summary

    def timeout(self):
        return self.int_setting('download_timeout_seconds', 120, 15, 900)

    async def download_index(self, mode_path, target, etag):
        try:
            return await transport.fetch_index(mode_path, target, etag=etag, timeout_seconds=self.timeout(), proxy=self.proxy())
        except NotFound:
            raise QueryError(f'tarkov.dev 上没有 {LABEL_OF_PATH.get(mode_path, mode_path)} 模式的玩家名单') from None
        except TimeoutError as exc:
            raise IndexTimeout(str(exc)) from exc

    async def terminate(self):
        self.cache.clear()

