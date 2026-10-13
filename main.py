import asyncio
import hashlib
import re
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .tarkov_core import transport
from .tarkov_core.modes import label_of, parse_mode, path_of
from .tarkov_core.names import NAME, NameIndex
from .tarkov_core.output import render
from .tarkov_core.profile import summarize
from .tarkov_core.transport import NotFound, QueryError

COMMAND = re.compile(r'^/?(?:tarkov|塔科夫)(?:\s+|$)', re.I)
# ASCII digits only: \d would also accept full-width and other Unicode digits.
ACCOUNT_ID = re.compile(r'[0-9]{1,12}')
# Profiles kept in memory at most, whatever the cache time.
CACHE_ENTRIES = 128


@register('astrbot_tarkov_datasearch', 'Evergarden_Roxy', '查询塔科夫玩家档案（数据来自 tarkov.dev）', '0.1.0')
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
        return str(self.config.get('proxy_url', '')).strip() if self.config.get('proxy_enabled', False) else None

    def owner(self, event):
        return hashlib.sha256((event.unified_msg_origin+'\0'+event.get_sender_id()).encode()).hexdigest()

    def config_list(self, key):
        """A list setting as strings. A string counts as one item (`in` on a string would be a substring test);
        any other type returns None, which denies access."""
        value = self.config.get(key, [])
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
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
                '昵称名单每周更新一次，新改名或新收录的玩家可能查不到，用账号 ID 查询最准确。')

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
            yield event.plain_result('昵称只能包含字母、数字、下划线和连字符，长度 3～15；账号 ID 只能是数字。')
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
            yield event.plain_result(f'查询失败：连接 tarkov.dev 超时（{exc}），请稍后再试，或在配置中调大“下载最长等待”。')
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
                yield event.plain_result(render(summary, label, self.skill_count(), notes))
                return
        if self.names.needs_refresh(path_of(mode)):
            size = '，约 30 MB' if mode == 'pvp' else ''
            yield event.plain_result(f'正在更新 {label} 玩家名单（每周一次{size}，可能需要几十秒），请稍候。')
        matches = await self.names.lookup(path_of(mode), target)
        logger.info('Tarkov [%s] name matches=%s', trace, len(matches))
        if not matches:
            if ACCOUNT_ID.fullmatch(target):
                yield event.plain_result(f'{label} 模式下没有账号 ID {target} 的档案，名单里也没有叫 {target} 的玩家。'
                                         'tarkov.dev 只有被查询过的玩家的档案，可以换一个模式试试。')
                return
            yield event.plain_result(f'{label} 名单里找不到 {target}。名单每周更新一次，只收录在 tarkov.dev 被查询过的玩家，'
                                     '新改名的玩家也可能查不到；知道账号 ID 时请用 ID 查询。')
            return
        exact = [match for match in matches if match[1] == target]
        if len(exact) != 1 and len(matches) > 1:
            listed = '\n'.join(f'{name}（ID {aid}）' for aid, name in matches[:5])
            yield event.plain_result(f'{label} 名单里有多个名为 {target} 的玩家（不区分大小写），请用账号 ID 查询：\n{listed}')
            return
        aid = (exact or matches)[0][0]
        try:
            summary = await self.profile(mode, aid)
        except NotFound:
            yield event.plain_result(f'名单里 {target} 的账号 ID 是 {aid}，但 tarkov.dev 上没有这个 ID 的 {label} 档案。')
            return
        if summary['nickname'].lower() != target.lower():
            notes.append(f'注意：名单中的 {target} 对应账号 ID {aid}，但档案里的昵称是 {summary["nickname"]}，可能改过名或名单已过时。')
        yield event.plain_result(render(summary, label, self.skill_count(), notes))

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
        return await transport.fetch_index(mode_path, target, etag=etag, timeout_seconds=self.timeout(), proxy=self.proxy())

    async def terminate(self):
        self.cache.clear()

