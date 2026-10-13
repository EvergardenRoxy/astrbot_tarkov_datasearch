import asyncio
import hashlib
import re
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, StarTools, register

from .tarkov_core import transport
from .tarkov_core.modes import MODES, label_of, parse_mode, path_of
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
LABEL_OF_PATH = {path: label for path, label in MODES.values()}
OUTPUT_MODES = ('image', 'text', 'markdown')


class IndexTimeout(TimeoutError):
    """A name index download timed out (the one download the timeout setting can help with)."""


def every(seconds):
    """The refresh interval in words: 每周, 每 2 周, 每天, 每 3 天, 每 5 小时."""
    hours = max(1, round(seconds/3600))
    for size, unit, one in ((168, '周', '每周'), (24, '天', '每天')):
        if hours % size == 0:
            return one if hours == size else f'每 {hours//size} {unit}'
    return f'每 {hours} 小时'


@register('astrbot_tarkov_datasearch', 'Evergarden_Roxy', '查询塔科夫玩家档案（数据来自 tarkov.dev）', '0.2.0')
class TarkovPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.root = StarTools.get_data_dir('astrbot_tarkov_datasearch')
        self.names = NameIndex(self.root, self.download_index, self.int_setting('name_index_refresh_hours', 168, 1, 720))
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

    def help_text(self):
        return ('塔科夫玩家档案查询（数据来自 tarkov.dev）\n'
                '用法：/tarkov [模式] <昵称或账号ID>\n'
                f'模式：pvp、pve、赛季（pvp-season）；不写时用 {label_of(self.default_mode())}\n'
                '例：/tarkov pve Some_Player　/tarkov pvp 1234567\n'
                f'昵称名单{every(self.names.refresh_seconds)}更新一次，新改名或新收录的玩家可能查不到，用账号 ID 查询最准确。')

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
        if not words or words[0].lower() in ('help', '帮助'):
            yield event.plain_result(self.help_text())
            return
        mode = parse_mode(words[0])
        if mode:
            words = words[1:]
        else:
            mode = self.default_mode()
        if len(words) != 1:
            yield event.plain_result(self.help_text())
            return
        target = words[0]
        if not ACCOUNT_ID.fullmatch(target) and not NAME.fullmatch(target):
            yield event.plain_result('昵称只能包含字母、数字、下划线和连字符，长度 3～24；账号 ID 只能是数字。')
            return
        owner = self.owner(event)
        if owner in self.active:
            yield event.plain_result('你的上一次查询还没完成，请稍候。')
            return
        self.active.add(owner)
        trace = owner[:8]
        logger.info('Tarkov [%s] query mode=%s target=%s', trace, mode, target)
        try:
            async for result in self.query(event, mode, target, trace):
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

    async def query(self, event, mode, target, trace):
        label = label_of(mode)
        notes = []
        if ACCOUNT_ID.fullmatch(target):
            try:
                summary = await self.profile(mode, int(target))
            except NotFound:
                # An all-digit nickname is possible; try it as a name before giving up.
                if not NAME.fullmatch(target):
                    yield event.plain_result(f'{label} 模式下没有账号 ID {target} 的档案。tarkov.dev 只有被查询过的玩家的档案，可以换一个模式试试。')
                    return
                summary = None
            if summary:
                yield await self.reply(event, summary, label, notes, trace)
                return
        interval = every(self.names.refresh_seconds)
        if self.names.needs_refresh(path_of(mode)):
            if self.names.has_index(path_of(mode)):
                yield event.plain_result(f'正在检查 {label} 玩家名单的更新（{interval}一次），请稍候。')
            else:
                size = '，约 30 MB' if mode == 'pvp' else ''
                yield event.plain_result(f'正在下载 {label} 玩家名单（第一次按昵称查询这个模式时需要{size}，可能需要几十秒），请稍候。')
        matches, total = await self.names.lookup(path_of(mode), target)
        logger.info('Tarkov [%s] name matches=%s', trace, total)
        if not matches:
            if ACCOUNT_ID.fullmatch(target):
                yield event.plain_result(f'{label} 模式下没有账号 ID {target} 的档案，名单里也没有叫 {target} 的玩家。'
                                         'tarkov.dev 只有被查询过的玩家的档案，可以换一个模式试试。')
                return
            yield event.plain_result(f'{label} 名单里找不到 {target}。名单{interval}更新一次，只收录在 tarkov.dev 被查询过的玩家，'
                                     '新改名的玩家也可能查不到；知道账号 ID 时请用 ID 查询。')
            return
        exact = [match for match in matches if match[1] == target]
        if len(exact) != 1 and len(matches) > 1:
            listed = '\n'.join(f'{name}（ID {aid}）' for aid, name in matches[:5])
            more = f'\n等，共 {total} 个' if total > 5 else ''
            yield event.plain_result(f'{label} 名单里有多个名为 {target} 的玩家（不区分大小写），请用账号 ID 查询：\n{listed}{more}')
            return
        aid = (exact or matches)[0][0]
        try:
            summary = await self.profile(mode, aid)
        except NotFound:
            yield event.plain_result(f'名单里 {target} 的账号 ID 是 {aid}，但 tarkov.dev 上没有这个 ID 的 {label} 档案。')
            return
        if summary['nickname'].lower() != target.lower():
            notes.append(f'注意：名单中的 {target} 对应账号 ID {aid}，但档案里的昵称是 {summary["nickname"]}，可能改过名或名单已过时。')
        yield await self.reply(event, summary, label, notes, trace)

    def output_mode(self):
        mode = str(self.config.get('output_mode', 'image'))
        return mode if mode in OUTPUT_MODES else 'image'

    async def reply(self, event, summary, label, notes, trace):
        """The profile in the configured output: an image (text if drawing fails), plain text or Markdown."""
        card = build_card(summary, label, self.skill_count(), notes)
        mode = self.output_mode()
        if mode == 'image':
            try:
                data = await asyncio.to_thread(render_image, card)
                return event.chain_result([Image.fromBytes(data)])
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

