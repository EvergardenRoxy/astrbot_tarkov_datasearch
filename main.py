import asyncio
import hashlib
import hmac
import re
import sqlite3
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools, register

from .tarkov_core import COMMAND_STYLES, transport
from .tarkov_core.atlas_client import NICKNAME as ATLAS_NICKNAME, AtlasClient
from .tarkov_core.atlas_cooldown import AtlasBudget, AtlasCooldown
from .tarkov_core.atlas_players import AtlasNotFound, AtlasPlayers, UserQuota, can_fall_back, failure_text
from .tarkov_core.atlas_assets import AtlasAssets, level_badge
from .tarkov_core.atlas_card import build as build_atlas_card
from .tarkov_core.atlas_history import AtlasHistory
from .tarkov_core.atlas_image import render_atlas_image
from .tarkov_core.font_download import FontDownloadError, FullFont
from .tarkov_core.fonts import use_chinese_font
from .tarkov_core.matching import MatchingService
from .tarkov_core.matching_image import ATLAS_NAME, ATLAS_URL, render_matching_image
from .tarkov_core.accounts import Accounts, load_secret, owner_key
from .tarkov_core.modes import GAME_MODES, MODES, label_of, page_url, parse_mode, parse_player_url, path_of, search_url
from .tarkov_core.names import NAME, SUGGESTIONS, NameIndex
from .tarkov_core.arena import build_arena_card, render_arena_markdown, render_arena_text, summarize_arena
from .tarkov_core.arena_image import render_arena_image
from .tarkov_core.card import BOT_LIMIT, build_card
from .tarkov_core.boss_monitor import BOSS_SCOPE, build_table, normalize_bosses
from .tarkov_core.boss_notifications import BossNotifications, BossStore
from .tarkov_core.boss_portraits import BossPortraits
from .tarkov_core.boss_table_image import paginate_table, render_boss_page
from .tarkov_core.help_image import render_help_image
from .tarkov_core.image import render_image
from .tarkov_core.output import render_markdown, render_text
from .tarkov_core.platform_compat import QQ_OFFICIAL, is_qq_official
from .tarkov_core.settings import Settings
from .tarkov_core.notifications import NotificationStore, PermanentDeliveryError, StatusNotifications
from .tarkov_core.polling import PollingTask
from .tarkov_core.profile import summarize
from .tarkov_core.public_api import ResourceKey, fetch as fetch_public, mode_path as public_mode_path
from .tarkov_core.resource_cache import ResourceCache
from .tarkov_core.server_status import normalize_status
from .tarkov_core.status_image import render_status_image
from .tarkov_core.transport import NotFound, QueryError
from .tarkov_core.weekly_image import render_weekly_image
from .tarkov_core.weekly_report import SLACK as WEEKLY_SLACK, WEEK, build_card as build_weekly_card, build_ranking, build_report, extract_sample
from .tarkov_core.weekly_store import SampleResult, WeeklyStore
from .tarkov_core.weekly_tasks import WeeklyBroadcast, WeeklySampler, parse_schedule

COMMAND = re.compile(r'^/?(?:tarkov|塔科夫)(?:\s+|$)', re.I)
# ASCII digits only: \d would also accept full-width and other Unicode digits.
ACCOUNT_ID = re.compile(r'[0-9]{1,12}')
# Profiles kept in memory at most, whatever the cache time.
CACHE_ENTRIES = 128
# How long "no profile in this mode" is remembered (seconds).
NOT_FOUND_SECONDS = 60
# A cached profile older than this is labelled with its age in the reply (seconds).
CACHE_NOTE_SECONDS = 60
# Profile requests in flight at once, for all users together (one query asks for three modes).
FETCHES_AT_ONCE = 6
MAX_PENDING_PROFILES = 64
PROFILE_QUEUE_SECONDS = 30
LABEL_OF_PATH = {path: label for path, label, _ in MODES.values()}
OUTPUT_MODES = ('image', 'text', 'markdown')
HELP_WORDS = ('help', '帮助')
BIND_WORDS = ('bind', '绑定')
UNBIND_WORDS = ('unbind', '解绑')
STATUS_WORDS = ('status', '状态')
BOSS_WORDS = ('boss', '刷率')
MATCHING_WORDS = ('matching', '匹配')
WEEKLY_WORDS = ('weekly', '周报')
RANK_WORDS = ('rank', 'ranking', '排行')
# The weekly report's roster settings, one list of account ids or nicknames per mode (Arena is planned, not built).
WEEKLY_LISTS = {'pvp': 'weekly_pvp_players', 'pve': 'weekly_pve_players', 'season': 'weekly_season_players'}
# Listed players sampled per mode at most: 3 x 40 pulls of about 160 seconds fit the 03:00-09:00 window.
WEEKLY_MAX_PLAYERS = 40
# The last word of a player query: skip the local cache and ask Atlas again (maintainer: refresh is part of the query).
REFRESH_WORDS = ('refresh', '刷新')
PLAYER_SOURCES = ('atlas', 'tarkov-dev')


class IndexTimeout(TimeoutError):
    """A name index download timed out (the one download the timeout setting can help with)."""


class Resolved:
    """A name resolved to an account (yielded by atlas_resolve among its chat messages)."""
    def __init__(self, aid, where):
        self.aid, self.where = aid, where


class QQOfficialFilter(filter.CustomFilter):
    def filter(self, event, config):
        return is_qq_official(event)


def every(seconds):
    """The refresh interval in words: 每周, 每 2 周, 每天, 每 3 天, 每 5 小时."""
    hours = max(1, round(seconds/3600))
    for size, unit, one in ((168, '周', '每周'), (24, '天', '每天')):
        if hours % size == 0:
            return one if hours == size else f'每 {hours//size} {unit}'
    return f'每 {hours} 小时'


@register('astrbot_tarkov_datasearch', 'Evergarden_Roxy', '查询塔科夫玩家档案（默认来自 Atlas，可改用 tarkov.dev 快照）、服务器状态、Boss 刷率和匹配时间', '0.6.1')
class TarkovPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = Settings(config)
        self.root = StarTools.get_data_dir('astrbot_tarkov_datasearch')
        # The downloaded name index is optional (on by default): names are mainly learned from the users (accounts.py).
        self.names = NameIndex(self.root, self.download_index, self.int_setting('name_index_refresh_hours', 168, 1, 720))
        self.accounts = Accounts(self.root/'accounts.sqlite3')
        self.root.mkdir(parents=True, exist_ok=True)
        # Group sessions that used the plugin, newest first: the push targets when no target list is configured.
        self.session_ids, self.session_written = self.load_sessions(), {}
        # Keys the binding keys and the log trace ids, so neither can be turned back into a user id (review R-11).
        self.secret = load_secret(self.root/'binding.key')
        # (mode, account id) -> (monotonic time, profile summary)
        self.cache = {}
        # (mode, account id) -> the request in flight, shared by every query that needs it (review R-08).
        self.pending = {}
        self.http = transport.HTTPClient()
        self.atlas_budget = AtlasBudget(lambda: self.int_setting('atlas_budget_per_minute', 10, 1, 10),
                                        lambda: self.int_setting('atlas_budget_per_hour', 150, 1, 200))
        self.atlas = AtlasClient(client=self.http, proxy=self.proxy,
            cooldown=AtlasCooldown(self.atlas_interval()), budget=self.atlas_budget)
        self.atlas_players = AtlasPlayers(self.atlas,
            cache_seconds=lambda: self.int_setting('profile_cache_minutes', 10, 0, 1440)*60)
        self.quota = UserQuota(self.quota_limits)
        self.atlas_assets = AtlasAssets(self.root/'atlas-assets', self.public_feed, proxy=self.proxy, client=self.http)
        self.atlas_history = AtlasHistory(self.root/'atlas-history.sqlite3',
            hours=lambda: self.int_setting('atlas_history_hours', 180, 1, 8760),
            max_bytes=lambda: self.int_setting('atlas_history_max_mb', 64, 16, 128)*1024*1024)
        self.atlas_image_slots = asyncio.Semaphore(2)
        self.full_font = FullFont(self.root/'fonts', proxy=self.proxy, client=self.http,
                                  mirror=lambda: self.config.get('font_download_url', ''))
        self.matching = MatchingService(self.atlas, enabled=self.matching_enabled)
        self.fetches = asyncio.Semaphore(FETCHES_AT_ONCE)
        self.active = set()
        self.public_data = ResourceCache(self.root/'public-data', self.download_public)
        self.status_store = NotificationStore(self.root/'notifications.sqlite3')
        self.status_image_slots = asyncio.Semaphore(2)
        self.matching_image_slots = asyncio.Semaphore(2)
        self._terminated = False
        self.status_notifications = StatusNotifications(self.status_store,
            self.fetch_status_for_monitor, self.prepare_status_image, self.send_status_notification,
            self.status_targets, self.status_broadcast_active, self.status_monitor_error)
        self.status_poller = PollingTask(self.status_notifications.cycle, self.status_broadcast_active,
                                        self.status_interval, self.status_monitor_error)
        self.boss_store = BossStore(self.root/'boss-notifications.sqlite3')
        self.boss_store.scope = BOSS_SCOPE+':'+','.join(sorted(self.boss_event_ignore()))
        self.boss_image_slots = asyncio.Semaphore(2)
        self.boss_portraits = BossPortraits(self.root/'boss-portraits')
        self.boss_normalized = {}
        self.boss_notifications = BossNotifications(self.boss_store,
            self.fetch_boss_for_monitor, self.prepare_boss_image, self.send_boss_notification,
            self.boss_modes, self.boss_targets, self.boss_broadcast_active, self.boss_monitor_error)
        self.boss_poller = PollingTask(self.boss_notifications.cycle, self.boss_broadcast_active,
                                      self.boss_interval, self.boss_monitor_error)
        self.weekly_clock = time.time
        self.weekly_store = WeeklyStore(self.root/'weekly.sqlite3',
                                        keep_days=lambda: self.int_setting('weekly_keep_days', 30, 14, 180))
        self.weekly_image_slots = asyncio.Semaphore(2)
        self.weekly_sampler = WeeklySampler(self.weekly_store, self.weekly_entries, self.weekly_resolve,
                                            self.weekly_fetch, self.weekly_save, self.weekly_sampling_active,
                                            self.weekly_error)
        self.weekly_broadcast = WeeklyBroadcast(self.weekly_store, self.weekly_schedule, self.weekly_targets,
                                                self.weekly_modes, self.weekly_ranking_image, self.send_weekly,
                                                self.weekly_broadcast_active, self.weekly_error)

    async def initialize(self):
        await self.atlas_settings_actions()
        if self.config.get('boss_portraits_update', False) is True:
            # Consume the administrator's one-shot action before any network work, preventing reload loops.
            self.config['boss_portraits_update'] = False
            save = getattr(self.config, 'save_config', None)
            try:
                if callable(save):
                    await asyncio.to_thread(save)
                count = await self.boss_portraits.refresh(proxy=self.proxy())
                logger.info('Tarkov Boss portraits manually updated: %s/12; local cache lifetime is 365 days', count)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('Tarkov manual portrait update failed: %s', type(exc).__name__)
        if self.status_broadcast_active():
            self.status_poller.reconfigure()
        else:
            await self.status_poller.close()
            if self.status_enabled() and self.config.get('status_broadcast_enabled', False) is True:
                logger.info('Tarkov status broadcasts idle: no eligible configured targets; check origins, platform capability and QQ push setting')
            if self.status_store.path.exists():
                # Disabled monitoring establishes a new silent baseline when enabled again.
                await self.status_store.thread(self.status_store.retire)
        if self.boss_broadcast_active():
            await self.boss_store.thread(self.boss_store.reconcile, self.boss_modes(), self.boss_targets())
            self.boss_poller.reconfigure()
        else:
            await self.boss_poller.close()
            if self.boss_store.path.exists():
                await self.boss_store.thread(self.boss_store.retire)
            if self.boss_enabled() and self.config.get('boss_broadcast_enabled', False) is True:
                logger.info('Tarkov Boss broadcasts idle: no eligible configured modes/targets; check settings and platform capability')
        await self.weekly_start()

    async def atlas_settings_actions(self):
        """The one-shot picture actions and the history switch. Each one-shot setting is switched off and saved
        before any network work, so a reload never repeats it (as the Boss portraits do)."""
        wanted = {'atlas_assets_clear': self.config.get('atlas_assets_clear', False),
                  'atlas_assets_update': self.config.get('atlas_assets_update', False)}
        actions = [key for key, value in wanted.items() if value is True]
        for key in actions:
            self.config[key] = False
        if actions:
            save = getattr(self.config, 'save_config', None)
            try:
                if callable(save):
                    await asyncio.to_thread(save)
            except Exception as exc:
                logger.warning('Tarkov settings not saved: %s', type(exc).__name__)
        if 'atlas_assets_clear' in actions:
            try:
                logger.info('Tarkov Atlas pictures cleared: %s files', await self.atlas_assets.clear())
            except Exception as exc:
                logger.warning('Tarkov Atlas picture clear failed: %s', type(exc).__name__)
        if 'atlas_assets_update' in actions:
            try:
                done, total = await self.atlas_assets.prefetch()
                logger.info('Tarkov Atlas common icons downloaded: %s/%s', done, total)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('Tarkov Atlas icon download failed: %s', type(exc).__name__)
        try:
            if self.history_enabled():
                await asyncio.to_thread(self.atlas_history.prune)
            else:
                # Privacy switch off: nothing is recorded and earlier records are deleted.
                await asyncio.to_thread(self.atlas_history.delete_all)
        except (OSError, sqlite3.Error) as exc:
            logger.warning('Tarkov Atlas history maintenance failed: %s', type(exc).__name__)

    def history_enabled(self):
        return self.config.get('atlas_history_enabled', True) is True

    async def public_feed(self, resource, mode):
        """The parsed `data` of a tarkov.dev public resource (the icon catalog of the Atlas picture)."""
        snapshot = await self.public_resource(resource, mode=mode, allow_stale=True)
        document = await snapshot.json()
        return document['data']

    async def record_history(self, aid, mode, summary, trace):
        """Keep Atlas' raw document in the local history (privacy switch on; never tarkov.dev snapshots)."""
        if not self.history_enabled() or summary.get('from_cache') or 'doc' not in summary:
            return
        try:
            await asyncio.to_thread(self.atlas_history.record, aid, mode, summary['doc'], summary.get('fetched'))
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.warning('Tarkov [%s] Atlas history not saved: %s', trace, type(exc).__name__)

    def int_setting(self, key, default, low, high):
        try:
            return max(low, min(high, int(self.config.get(key, default))))
        except (TypeError, ValueError, OverflowError):
            return default

    async def prepare_font(self):
        """Every image waits for the same verified complete Chinese font; no trimmed fallback."""
        use_chinese_font(await self.full_font.ensure())

    def atlas_interval(self):
        value = self.config.get('atlas_query_cooldown_seconds', 10)
        if type(value) is not int or not 1 <= value <= 300:
            logger.warning('Invalid Atlas cooldown setting; using 10 seconds')
            return 10
        return max(10, value)

    def proxy(self):
        if not self.config.get('proxy_enabled', False):
            return None
        url = str(self.config.get('proxy_url', '')).strip()
        if not url:
            # Fail closed: an empty address must not turn into a direct connection.
            raise QueryError('已开启“访问 tarkov.dev 时使用代理”，但没有填写代理地址，请在插件配置中填写或关闭代理')
        return url

    def owner(self, event):
        return hmac.new(self.secret, (event.unified_msg_origin+'\0'+event.get_sender_id()).encode(), hashlib.sha256).hexdigest()

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
        mode = parse_mode(self.config.get('default_mode', 'pve'))
        return mode if mode in GAME_MODES else 'pve'

    def player_source(self):
        """'atlas' (default: live lookups, tarkov.dev snapshots as the fallback) or 'tarkov-dev' (snapshots only)."""
        value = str(self.config.get('player_source', 'atlas')).strip().lower()
        return value if value in PLAYER_SOURCES else 'atlas'

    def quota_limits(self):
        # Hourly counts are fixed policy; retired config keys cannot override them.
        return (10, 3, self.int_setting('user_query_cooldown_seconds', 30, 30, 3600),
                self.int_setting('user_refresh_cooldown_seconds', 300, 300, 86400))

    def atlas_cache_minutes(self):
        return max(1, self.int_setting('profile_cache_minutes', 10, 0, 1440))

    def arena_by_default(self):
        return bool(self.config.get('arena_by_default', False))

    def looked_at(self, mode):
        """The modes a query about `mode` fetches, `mode` first. Arena is a separate game: asked for, it is fetched
        alone; it joins the main game's modes only with the "arena_by_default" setting (maintainer)."""
        if mode == 'arena':
            return ['arena']
        modes = list(GAME_MODES)+(['arena'] if self.arena_by_default() else [])
        return [mode]+[other for other in modes if other != mode]

    def qq_words_enabled(self):
        return self.config.get('qq_single_word_commands', True) is True

    def index_enabled(self):
        return bool(self.config.get('name_index_enabled', True))

    def binding_key(self, event):
        # unified_msg_origin is "<platform id>:<message type>:<session>"; a binding holds in every chat of a platform.
        return owner_key(self.secret, event.unified_msg_origin.split(':', 1)[0], event.get_sender_id())

    def style(self):
        """The wording of the "command_style" setting (unknown values: the default)."""
        return COMMAND_STYLES.get(str(self.config.get('command_style', 'mention')), COMMAND_STYLES['mention'])

    def cmd(self, *words):
        """A command as the user should type it: the style's command, then `words` (cmd('绑定') is the bind command)."""
        return ' '.join([self.style()['command'], *map(str, words)])

    def atlas_help(self):
        """The Atlas source's rule sentence (help text and help image)."""
        queries, refreshes, _, _ = self.quota_limits()
        refresh = f'刷新 {refreshes} 次' if refreshes else '不能刷新'
        return (f'数据来自 Atlas（{ATLAS_URL}），按需实时查询，不用先在 tarkov.dev 搜索；查过的玩家缓存 '
                f'{self.atlas_cache_minutes()} 分钟，每人每小时可联网查询 {queries} 次、{refresh}。'
                'Atlas 总额度用完时暂停联网查询，到期自动恢复；缓存仍可查看。'
                'Atlas 请求故障时可改用 tarkov.dev 快照，回复里会注明。')

    def refresh_rule(self):
        _, refreshes, _, gap = self.quota_limits()
        if not refreshes:
            return '管理员已关闭刷新'
        return f'跳过缓存重新查询；每人每小时 {refreshes} 次，两次至少间隔 {max(1, round(gap/60))} 分钟' if gap >= 60 else \
            f'跳过缓存重新查询；每人每小时 {refreshes} 次'

    def help_text(self):
        if self.player_source() == 'atlas':
            return self.atlas_help_text()
        index = f'；另外会查 tarkov.dev 的昵称名单（{every(self.names.refresh_seconds)}更新一次）' if self.index_enabled() else ''
        status = f"服务器状态图片：{self.cmd('status')}\n" if self.status_enabled() else ''
        boss = (f"Boss 全表图片：{self.cmd('boss [模式]')}；最近变化：{self.cmd('boss [模式] 变化')}\n"
                f"Boss 默认模式：{self.cmd('boss 默认 <模式>')}；支持 pvp、pve、PVP S。\n") if self.boss_enabled() else ''
        matching = (f"服务器平均匹配时间图片：{self.cmd('匹配')}（数据来自 {ATLAS_NAME}：{ATLAS_URL}）\n"
                    if self.matching_enabled() else '')
        words = ('QQ 官方机器人单词命令（后面的参数可以带空格）：tpvp、tpve、tseason、tarena [昵称、账号ID 或网址]；'
                 'tbind <网址或账号ID>、tunbind、thelp；tstatus、tboss [模式] [变化]'
                 + ('、tmatch' if self.matching_enabled() else '') + '\n') if self.qq_words_enabled() else ''
        return ('塔科夫玩家档案查询（数据来自 tarkov.dev）\n'
                f"{self.style()['help']}\n"
                f"绑定自己：{self.cmd('绑定 <tarkov.dev 玩家页网址或账号ID>')}　解绑：{self.cmd('解绑')}\n"
                f"查自己：{self.cmd('[模式]')}\n"
                f"查别人：{self.cmd('[模式] <昵称、账号ID 或玩家页网址>')}\n"
                f'模式：pvp、pve、赛季（pvp-season）、竞技场（arena）；不写时先看 {label_of(self.default_mode())}，没有档案就自动换成有档案的模式'
                + ('（也会看竞技场）' if self.arena_by_default() else f"（竞技场要写明：{self.cmd('竞技场 <昵称、账号ID 或网址>')}）") + '\n'
                f'由于 {BOT_LIMIT}，插件只能读取 tarkov.dev 上已有的档案，且档案不会自动更新。\n'
                '第一次查某个玩家前，请先在 tarkov.dev 用对应模式搜索他（链接见最后一行），'
                '再把跳转后的网址发给我；之后就能直接用名字查（按 ID 或网址查到过的名字会被记住' + index + '）。\n'
                '如果需要查询最新数据，请先在 tarkov.dev 上重新搜索或打开这名玩家的页面。\n'
                + status + boss + matching + words + self.search_line())

    def help_extras(self):
        status = f"服务器状态图片：{self.cmd('status')}\n" if self.status_enabled() else ''
        boss = (f"Boss 全表图片：{self.cmd('boss [模式]')}；最近变化：{self.cmd('boss [模式] 变化')}\n"
                f"Boss 默认模式：{self.cmd('boss 默认 <模式>')}；支持 pvp、pve、PVP S。\n") if self.boss_enabled() else ''
        matching = (f"服务器平均匹配时间图片：{self.cmd('匹配')}（数据来自 {ATLAS_NAME}：{ATLAS_URL}）\n"
                    if self.matching_enabled() else '')
        weekly = (f"玩家周报图片：{self.cmd('周报 [模式] [昵称、账号ID 或 排行]')}（名单内玩家近 7 天的变化，每天凌晨采样）\n"
                  if self.weekly_enabled() else '')
        words = ('QQ 官方机器人单词命令（后面的参数可以带空格）：tpvp、tpve、tseason、tarena [昵称、账号ID 或网址] [刷新]；'
                 'tbind <昵称、账号ID 或网址>、tunbind、thelp；tstatus、tboss [模式] [变化]'
                 + ('、tmatch' if self.matching_enabled() else '')
                 + ('、tweekly [模式] [昵称]' if self.weekly_enabled() else '') + '\n') if self.qq_words_enabled() else ''
        return status+boss+matching+weekly+words

    def atlas_help_text(self):
        index = '和 tarkov.dev 昵称名单' if self.index_enabled() else ''
        return ('塔科夫玩家档案查询（数据来自 Atlas，实时查询）\n'
                f"{self.style()['help']}\n"
                f"绑定自己：{self.cmd('绑定 <昵称、账号ID 或玩家页网址>')}　解绑：{self.cmd('解绑')}\n"
                f"查自己：{self.cmd('[模式]')}\n"
                f"查别人：{self.cmd('[模式] <昵称、账号ID 或玩家页网址>')}\n"
                f"要最新数据：在命令最后加“刷新”，例如 {self.cmd('pve', 'Player_One', '刷新')}（{self.refresh_rule()}）\n"
                f'模式：pvp、pve、赛季（pvp-season）、竞技场（arena）；不写时查 {label_of(self.default_mode())}，每次只查一个模式。\n'
                f'{self.atlas_help()}\n'
                f'按昵称查询时先查插件记录{index}，找不到再用 Atlas 搜索（昵称最长 15 个字符，更长的名字请用账号 ID）。\n'
                + self.help_extras() + self.search_line())

    def search_line(self):
        """The search page as a text line. Every help reply ends with it, the image included (maintainer: the link
        is always given as text, whatever the output)."""
        return f'tarkov.dev 玩家搜索：{search_url(self.default_mode())}'

    def help_card(self):
        """What the help image shows; the same settings as help_text."""
        mode = self.default_mode()
        card = {'command': self.style()['command'], 'note': self.style()['note'], 'mode': mode, 'label': label_of(mode), 'search': search_url(mode), 'example': page_url(mode, 1234567),
                'arena_by_default': self.arena_by_default(),
                'index': every(self.names.refresh_seconds) if self.index_enabled() else None}
        if self.status_enabled():
            card['status_enabled'] = True
        if self.boss_enabled():
            card['boss_enabled'] = True
        if self.matching_enabled():
            card['matching_enabled'] = True
        if self.weekly_enabled():
            card['weekly_enabled'] = True
        if self.qq_words_enabled():
            card['qq_words'] = True
        if self.player_source() == 'atlas':
            card.update(source='atlas', refresh_rule=self.refresh_rule(), atlas_rule=self.atlas_help())
        return card

    async def help_reply(self, event):
        """The help in the configured output: an image (maintainer: the text is hard to read on phones) followed by
        the search page as a text line (`search_line`), or the text, which ends with the same line, when the output is
        text or Markdown or drawing fails."""
        if self.output_mode() == 'image':
            try:
                await self.prepare_font()
                data = await asyncio.to_thread(render_help_image, self.help_card())
                return event.chain_result([Image.fromBytes(data), Plain(self.search_line())])
            except Exception as exc:
                logger.warning('Tarkov help image failed, sending text: %s', type(exc).__name__)
                if isinstance(exc, FontDownloadError):
                    return event.plain_result(f'{exc}\n\n{self.help_text()}')
        return event.plain_result(self.help_text())

    @filter.command('tarkov', alias={'塔科夫'})
    async def tarkov_command(self, event: AstrMessageEvent):
        text = COMMAND.sub('', event.message_str.strip(), count=1).strip()
        async for result in self.dispatch(event, text):
            yield result

    async def dispatch(self, event, text):
        """Access check, then the command text; every entry (tarkov, a slash after a QQ mention, the QQ single words)."""
        if not self.allowed(event):
            logger.info('Tarkov access denied; mode=%s', self.config.get('access_mode', 'all'))
            if text.split() and text.split()[0].lower() in STATUS_WORDS:
                yield await self.status_picture(event, {'error': '此会话或用户没有塔科夫查询权限。'})
            elif text.split() and text.split()[0].lower() in BOSS_WORDS:
                yield await self.boss_picture(event, {'error': '此会话或用户没有塔科夫查询权限。'})
            else:
                yield event.plain_result('此会话或用户没有塔科夫查询权限。')
            event.stop_event()
            return
        await self.remember_session(event)
        async for result in self.handle(event, text):
            yield result
        event.stop_event()

    @filter.command('/'+'tarkov', alias={'/'+'塔科夫'})
    @filter.custom_filter(QQOfficialFilter)
    async def qq_slash_command(self, event: AstrMessageEvent):
        # QQ mentions wake AstrBot even when '/' is not configured as a wake prefix. In that case the
        # standard command filter still sees the slash. Its normal wake/permission/plugin checks apply here too.
        if not is_qq_official(event):
            return
        async for result in self.tarkov_command(event):
            yield result

    # QQ official bots cannot register a command that contains a space, so each action also has a one-word command; what
    # follows the word may contain spaces (a nickname after tpve, say). Only on QQ official platforms, with the setting on.
    async def qq_word(self, event, prefix):
        if not is_qq_official(event) or self.config.get('qq_single_word_commands', True) is not True:
            return
        words = event.message_str.strip().split(maxsplit=1)
        async for result in self.dispatch(event, (prefix+' '+(words[1] if len(words) > 1 else '')).strip()):
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

    @staticmethod
    def status_arguments(event):
        words = event.message_str.strip().split(maxsplit=1)
        return words[1] if len(words) > 1 else ''

    async def handle(self, event, text):
        words = text.split()
        if words and words[0].lower() in STATUS_WORDS:
            yield await self.status_query(event, arguments=' '.join(words[1:]))
            return
        if words and words[0].lower() in BOSS_WORDS:
            yield await self.boss_query(event, ' '.join(words[1:]))
            return
        if words and words[0].lower() in MATCHING_WORDS:
            yield await self.matching_query(event, ' '.join(words[1:]))
            return
        if words and words[0].lower() in WEEKLY_WORDS:
            yield await self.weekly_query(event, ' '.join(words[1:]))
            return
        action = 'query'
        if words and words[0].lower() in HELP_WORDS:
            yield await self.help_reply(event)
            return
        if words and words[0].lower() in UNBIND_WORDS and len(words) == 1:
            action, words = 'unbind', []
        elif words and words[0].lower() in BIND_WORDS:
            action, words = 'bind', words[1:]
        refresh = False
        if action == 'query' and words and words[-1].lower() in REFRESH_WORDS:
            refresh, words = True, words[:-1]
        mode = parse_mode(words[0]) if words else None
        if mode:
            words = words[1:]
        if len(words) > 1:
            yield await self.help_reply(event)
            return
        target = words[0] if words else None
        if action == 'bind' and target is None:
            # Atlas resolves nicknames itself; the snapshot source needs the website link or the id.
            what = '<昵称、账号ID 或玩家页网址>' if self.player_source() == 'atlas' else '<tarkov.dev 玩家页网址或账号ID>'
            yield event.plain_result(f"用法：{self.cmd('绑定 '+what)}，"
                                     f"例如 {self.cmd('绑定', page_url('pve', 1234567))}")
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
        elif target and not ACCOUNT_ID.fullmatch(target) and not NAME.fullmatch(target):
            yield event.plain_result('请发送昵称（字母、数字、下划线和连字符，长度 3～24）、账号 ID（数字）或 tarkov.dev 玩家页网址。')
            return
        # A mode word or a link chooses the mode; otherwise every mode is looked at, the default one first.
        explicit = mode is not None
        mode = mode or self.default_mode()
        owner = self.owner(event)
        if owner in self.active:
            yield event.plain_result('你的上一次查询还没完成，请稍候。')
            return
        self.active.add(owner)
        trace = owner[:8]
        logger.info('Tarkov [%s] %s mode=%s target=%s refresh=%s', trace, action, mode, target, refresh)
        try:
            if action == 'unbind':
                yield await self.unbind(event)
            elif self.player_source() == 'atlas':
                async for result in self.atlas_player(event, action, mode, target, url, trace, refresh, owner):
                    yield result
            elif action == 'bind':
                yield await self.bind(event, mode, target, url, trace)
            else:
                async for result in self.query(event, mode, explicit, target, url, trace, refresh):
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
    def search_hint(mode, who='这名玩家', explicit=True):
        if explicit:
            return f'请先在 tarkov.dev 用 {label_of(mode)} 模式搜索一次{who}（{search_url(mode)}）'
        return (f'请先在 tarkov.dev 搜索一次{who}（{search_url(mode)}，页面上可以切换 PVP / PVE / 赛季，'
                '请选对应的模式）')

    def first_time(self, mode, who='这名玩家', command=None, explicit=True):
        return (self.search_hint(mode, who, explicit)
                + f'，再把跳转后的网址发给我（例如 {command or self.cmd()} {page_url(mode, 1234567)}）。')

    async def profiles(self, aid, first, trace, fresh=False):
        """({mode: summary}, {mode: error}) for account `aid`, `first` first. The modes are fetched together; "no
        profile" is in neither. A failed request is kept apart from "no profile", so a 429 is never reported as
        "this player has no profile" (review R-01). `fresh` skips the cache (the user just came from the website)."""
        order = self.looked_at(first)
        results = await asyncio.gather(*(self.profile(mode, aid, fresh) for mode in order), return_exceptions=True)
        found, failed = {}, {}
        for mode, result in zip(order, results):
            if isinstance(result, NotFound):
                continue
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                logger.warning('Tarkov [%s] %s profile failed: %s', trace, mode, type(result).__name__)
                failed[mode] = result
                continue
            found[mode] = result
        return found, failed

    @staticmethod
    def failure_text(failed):
        """Why the requests of these modes failed, in one sentence."""
        def why(exc):
            if isinstance(exc, QueryError):
                return str(exc)
            return '连接超时' if isinstance(exc, TimeoutError) else type(exc).__name__
        return '；'.join(f'{label_of(mode)} 模式这次没有查到（{why(exc)}）' for mode, exc in failed.items())

    @staticmethod
    def modes_text(modes):
        return '、'.join(label_of(mode) for mode in modes)

    @staticmethod
    def mode_word(mode):
        return {'pvp': 'pvp', 'pve': 'pve', 'season': '赛季', 'arena': '竞技场'}[mode]

    def none_found(self, aid, mode):
        """"No profile in any mode looked at", one sentence."""
        modes = self.looked_at(mode)
        if modes == ['arena']:
            return f'tarkov.dev 上还没有账号 ID {aid} 的竞技场档案。'
        words = '、'.join({'pvp': 'PVP', 'pve': 'PVE', 'season': '赛季', 'arena': '竞技场'}[m] for m in MODES if m in modes)
        return f'tarkov.dev 上还没有账号 ID {aid} 的档案（{words}都没有）。'

    async def show(self, event, aid, mode, explicit, trace, who='这名玩家', asked=None, where='记录', fresh=False):
        """(reply, None) for account `aid`: the profile in `mode`, or (mode not chosen by the user) in the first other
        mode that has one; the reply also names the other modes with a profile. (None, why) when nothing can be shown.
        A failed request is raised when nothing else can be shown, so the user reads the error, not "no profile".
        `asked`: the name the account was looked up by, to note a rename."""
        found, failed = await self.profiles(aid, mode, trace, fresh)
        notes = []
        if mode in found:
            shown = mode
        elif found and not explicit:
            shown = next(iter(found))
            if mode in failed:
                notes.append(f'{self.failure_text({mode: failed.pop(mode)})}，下面是 {label_of(shown)} 模式的档案。')
            else:
                notes.append(f'tarkov.dev 上还没有这个账号的 {label_of(mode)} 档案（要有的话，请在 tarkov.dev 用 '
                             f'{label_of(mode)} 模式搜索一次：{search_url(mode)}），下面是 {label_of(shown)} 模式的档案。')
        elif mode in failed or (failed and not found):
            raise failed.get(mode) or next(iter(failed.values()))
        else:
            unknown = f'；{self.failure_text(failed)}，请稍后再试' if failed else ''
            if found:
                others = '、'.join(f'{label_of(m)} 模式（{self.cmd(self.mode_word(m), aid)}）' for m in found)
                return None, (f'{label_of(mode)} 模式下还没有账号 ID {aid} 的档案，但 {others}下有档案{unknown}。'
                              f'要看 {label_of(mode)} 模式，' + self.search_hint(mode, who) + '，几分钟后再查。')
            return None, (self.none_found(aid, mode) + 'tarkov.dev 只保存在网站上被搜索过的玩家，'
                          + self.search_hint(mode, who, explicit or mode == 'arena') + '，几分钟后再查。')
        summary = found[shown]
        nickname = summary['nickname']
        if asked and nickname.lower() != asked.lower():
            notes.append(f'注意：{where}中的 {asked} 对应账号 ID {aid}，档案里现在的昵称是 {nickname}，可能改过名。')
        others = [m for m in found if m != shown]
        if others:
            notes.append(f'这个账号在 {self.modes_text(others)} 模式下也有档案，查询时写上模式即可'
                         f'（例如 {self.cmd(self.mode_word(others[0]), aid)}）。')
        if failed:
            notes.append(self.failure_text(failed)+'，请稍后再试。')
        age = time.time()-summary.get('fetched', time.time())
        if age >= CACHE_NOTE_SECONDS:
            # Review R-02: the website refresh the reply asks for is not seen while the profile is cached.
            notes.append(f'这是插件 {max(1, round(age/60))} 分钟前从 tarkov.dev 取得的数据。刚在网站上刷新过的话，'
                         '发送这名玩家的页面网址查询，即可立即看到新数据。')
        return await self.reply(event, summary, shown, aid, notes, trace), None

    async def unbind(self, event):
        removed = await asyncio.to_thread(self.accounts.unbind, self.binding_key(event))
        if not removed:
            return event.plain_result('你还没有绑定账号。')
        return event.plain_result(f'已解除绑定：{removed[1]}（账号 ID {removed[0]}）。')

    async def bind(self, event, mode, target, url, trace, aid=None, note=''):
        """Bind through the tarkov.dev snapshots. `aid`: the account when the caller already settled it (the Atlas
        flow resolved the name by the name list or Atlas' search, which the local record does not know); `note`: a
        sentence put before the reply (why the snapshot was used)."""
        if aid is not None:
            pass
        elif url or ACCOUNT_ID.fullmatch(target):
            aid = url[1] if url else int(target)
        else:
            rows, _ = await asyncio.to_thread(self.accounts.lookup, target)
            exact = [row for row in rows if row[1] == target]
            if len(rows) != 1 and len(exact) != 1:
                hint = await self.suggestion(mode, True, target, command=self.cmd('绑定')) if not rows else ''
                return event.plain_result(f'不能确定 {target} 是哪个账号。' + hint
                                          + self.first_time(mode, '自己', self.cmd('绑定'), explicit=False))
            aid = (exact or rows)[0][0]
        # A binding is for the account, whatever the mode: a profile in any mode confirms the account exists.
        found, failed = await self.profiles(aid, mode, trace, fresh=True)
        if not found and failed:
            raise next(iter(failed.values()))
        if not found:
            return event.plain_result(note+self.none_found(aid, mode)
                                      + self.first_time(mode, '自己', self.cmd('绑定'), explicit=mode == 'arena'))
        nickname = (found.get(mode) or next(iter(found.values())))['nickname']
        await asyncio.to_thread(self.accounts.bind, self.binding_key(event), aid, nickname)
        missing = [m for m in self.looked_at(mode) if m not in found and m not in failed]
        later = (f'{self.modes_text(missing)} 模式的档案要在 tarkov.dev 用该模式搜索一次自己才会出现。' if missing else '')
        unknown = f'{self.failure_text(failed)}。' if failed else ''
        return event.plain_result(f'{note}已绑定：{nickname}（账号 ID {aid}），目前在 {self.modes_text(found)} 模式下有档案。'
                                  f'{unknown}{later}'
                                  f'之后发送 {self.cmd()} 即可查询自己（先看 {label_of(self.default_mode())}，没有时自动换成有档案的模式），'
                                  f"也可以写模式，例如 {self.cmd('pve')}、{self.cmd('赛季')}。"
                                  f'由于 {BOT_LIMIT}，档案不会自动更新：要看最新数据，请先在 tarkov.dev 上重新搜索或打开自己的页面。')

    async def query(self, event, mode, explicit, target, url, trace, refresh=False):
        if target is None:
            bound = await asyncio.to_thread(self.accounts.bound, self.binding_key(event))
            if not bound:
                yield event.plain_result(f"你还没有绑定账号。先在 tarkov.dev 搜索自己的游戏名，再发送：{self.cmd('绑定 <跳转后的网址>')}。\n"
                                         f"查别人：{self.cmd('[模式] <昵称、账号ID 或网址>')}；完整用法：{self.cmd('帮助')}")
                return
            aid, nickname = bound
            reply, missing = await self.show(event, aid, mode, explicit, trace, who='自己', fresh=refresh)
            yield reply or event.plain_result(f'你绑定的账号 {nickname}：'+missing)
            return
        if url or ACCOUNT_ID.fullmatch(target):
            aid = url[1] if url else int(target)
            # A link usually means the user just opened the page (which refreshes the snapshot): skip the cache.
            reply, missing = await self.show(event, aid, mode, explicit, trace, fresh=bool(url) or refresh)
            if reply:
                yield reply
                return
            # An all-digit nickname is possible; try it as a name before giving up.
            if url or not NAME.fullmatch(target):
                yield event.plain_result(missing)
                return
        else:
            missing = None
        async for result in self.by_name(event, mode, explicit, target, trace, id_missing=missing):
            yield result

    # ---- Atlas player source (docs/atlas-integration-plan.md §3) ----

    async def atlas_player(self, event, action, mode, target, url, trace, refresh, owner):
        """Query or bind through Atlas: one mode per query (the default one unless written), names resolved by the
        shared record, then the tarkov.dev name list, then Atlas' search. When Atlas fails (not "no such player"),
        the tarkov.dev snapshot stands in, labelled as such."""
        charge = self.quota.ticket(owner, refresh)
        asked = where = None
        if target is None:
            bound = await asyncio.to_thread(self.accounts.bound, self.binding_key(event))
            if not bound:
                yield event.plain_result(f"你还没有绑定账号。发送 {self.cmd('绑定 <昵称、账号ID 或玩家页网址>')} 绑定自己。\n"
                                         f"查别人：{self.cmd('[模式] <昵称、账号ID 或网址>')}；完整用法：{self.cmd('帮助')}")
                return
            aid = bound[0]
        elif url or ACCOUNT_ID.fullmatch(target):
            aid = url[1] if url else int(target)
        else:
            aid = None
            async for item in self.atlas_resolve(event, mode, target, trace, charge):
                if isinstance(item, Resolved):
                    aid, where = item.aid, item.where
                else:
                    yield item
            if aid is None:
                return
            asked = target
        try:
            summary = await self.atlas_players.profile(mode, aid, refresh=refresh, charge=charge)
        except AtlasNotFound as exc:
            yield event.plain_result(self.atlas_missing(exc, action, mode, aid, target))
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not can_fall_back(exc):
                raise
            if action == 'bind':
                logger.warning('Tarkov [%s] Atlas bind lookup failed, using tarkov.dev: %s', trace, type(exc).__name__)
                # The account is already settled (perhaps by Atlas' search): bind() must not resolve the name again.
                yield await self.bind(event, mode, target, url, trace, aid=aid,
                                      note=f'{failure_text(exc)}，已改用 tarkov.dev 快照确认账号。')
            else:
                yield await self.atlas_fallback(event, mode, aid, exc, trace)
            return
        notes = []
        if asked and summary['nickname'].lower() != asked.lower():
            # The record or the name list may be out of date (renamed players): Atlas' own search decides.
            if where != 'Atlas 搜索' and ATLAS_NICKNAME.fullmatch(asked):
                other = await self.atlas_rename_check(mode, asked, aid, trace, charge)
                if other is not None:
                    notes.append(f'{where}中的 {asked} 对应账号 ID {aid}（现在叫 {summary["nickname"]}）；'
                                 f'Atlas 搜索到的 {asked} 是账号 ID {other[0]}，下面是它的档案。')
                    aid, summary = other
            if summary['nickname'].lower() != asked.lower():
                notes.append(f'注意：{where}中的 {asked} 对应账号 ID {aid}，档案里现在的昵称是 {summary["nickname"]}，可能改过名。')
        if not summary.get('from_cache'):
            await self.record_history(aid, mode, summary, trace)
            await self.weekly_feed(aid, mode, summary)
            try:
                await asyncio.to_thread(self.accounts.remember, aid, summary['nickname'])
            except sqlite3.Error as exc:
                logger.warning('Tarkov name record not saved: %s', type(exc).__name__)
        if action == 'bind':
            await asyncio.to_thread(self.accounts.bind, self.binding_key(event), aid, summary['nickname'])
            yield event.plain_result(f"已绑定：{summary['nickname']}（账号 ID {aid}）。之后发送 {self.cmd()} 即可查询自己"
                                     f"（查 {label_of(self.default_mode())}），也可以写模式，例如 {self.cmd('pvp')}、{self.cmd('赛季')}；"
                                     f"要最新数据，在命令最后加“刷新”。")
            return
        age = summary.get('cache_age', 0)
        if summary.get('from_cache') and age >= CACHE_NOTE_SECONDS:
            notes.append(f'这是插件 {max(1, round(age/60))} 分钟前从 Atlas 取得的数据（缓存）。')
        yield await self.reply(event, summary, mode, aid, notes, trace)

    async def atlas_rename_check(self, mode, asked, aid, trace, charge):
        """(aid, summary) of the account Atlas' search finds under `asked` when it is not `aid`, else None. Failures
        only lose the check (the profile already found is shown)."""
        try:
            found = await self.atlas_players.search(asked, mode, charge=charge)
            exact = [entry for entry in found if entry[1].lower() == asked.lower() and entry[0] != aid]
            if len(exact) != 1:
                return None
            return exact[0][0], await self.atlas_players.profile(mode, exact[0][0], charge=charge)
        except asyncio.CancelledError:
            raise
        except QueryError as exc:
            logger.info('Tarkov [%s] Atlas rename check skipped: %s', trace, type(exc).__name__)
            return None

    def atlas_missing(self, exc, action, mode, aid, target):
        """The reply when Atlas says there is no such account or no profile in the mode."""
        other = next(m for m in ('pvp', 'pve', 'season') if m != mode)
        if str(exc).startswith('Atlas 找不到'):
            return f'{exc}，请检查账号 ID 或昵称。'
        if action == 'bind':
            return f"{exc}。绑定时可以写上有档案的模式，例如 {self.cmd('绑定', self.mode_word(other), target or aid)}。"
        return f'{exc}。可以换个模式查询，例如 {self.cmd(self.mode_word(other), aid)}。'

    async def atlas_resolve(self, event, mode, target, trace, charge):
        """Yield chat messages, and a Resolved when the name is settled: the shared record, then (when enabled) the
        tarkov.dev name list of the mode, then Atlas' nickname search (one request)."""
        label = label_of(mode)
        matches, total = await asyncio.to_thread(self.accounts.lookup, target)
        where = '记录'
        if not matches and self.index_enabled():
            where = '名单'
            if self.names.needs_refresh(path_of(mode)) and not self.names.has_index(path_of(mode)):
                size = '，约 30 MB' if mode == 'pvp' else ''
                yield event.plain_result(f'正在下载 {label} 玩家名单（第一次按昵称查询这个模式时需要{size}，可能需要几十秒），请稍候。')
            try:
                matches, total = await self.names.lookup(path_of(mode), target)
            except (QueryError, TimeoutError) as exc:
                # The list only saves requests; Atlas' search can still find the name.
                logger.warning('Tarkov [%s] name list unavailable: %s', trace, type(exc).__name__)
                matches, total = [], 0
        logger.info('Tarkov [%s] name matches=%s source=%s', trace, total, where)
        exact = [match for match in matches if match[1] == target]
        if len(exact) == 1 or len(matches) == 1:
            yield Resolved((exact or matches)[0][0], where)
            return
        if not ATLAS_NICKNAME.fullmatch(target):
            if matches:
                yield event.plain_result(self.ambiguous(target, matches, total))
            else:
                yield event.plain_result(f'记录和名单里都没有 {target}；Atlas 只能搜索 3～15 个字符的昵称（字母、数字、_ -），'
                                         '更长的名字请用账号 ID 或 tarkov.dev 玩家页网址查询。')
            return
        try:
            found = await self.atlas_players.search(target, mode, charge=charge)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not can_fall_back(exc):
                raise
            logger.warning('Tarkov [%s] Atlas search failed: %s', trace, type(exc).__name__)
            yield event.plain_result(f'{failure_text(exc)}，暂时无法按昵称查找 {target}，请稍后再试，或改用账号 ID / 玩家页网址查询。')
            return
        exact = [entry for entry in found if entry[1] == target]
        folded = [entry for entry in found if entry[1].lower() == target.lower()]
        pick = exact if len(exact) == 1 else folded if len(folded) == 1 else None
        if pick:
            yield Resolved(pick[0][0], 'Atlas 搜索')
            return
        if folded or len(matches) > 1:
            listed = folded or matches
            yield event.plain_result(self.ambiguous(target, listed, len(listed)))
            return
        hint = ''
        if found:
            listed = '、'.join(f'{name}（账号 ID {aid}）' for aid, name in found[:SUGGESTIONS])
            hint = f'Atlas 搜索到的相近名字：{listed}。'
        else:
            hint = await self.suggestion(mode, True, target)
            hint = hint.removesuffix('如果都不是，') if hint else ''
        yield event.plain_result(f'Atlas 在 {label} 模式下没有找到名为 {target} 的玩家。{hint}'
                                 '请检查拼写，或改用账号 ID / tarkov.dev 玩家页网址查询。')

    @staticmethod
    def ambiguous(target, matches, total):
        listed = '\n'.join(f'{name}（ID {aid}）' for aid, name in matches[:5])
        more = f'\n等，共 {total} 个' if total > 5 else ''
        return f'有多个名为 {target} 的玩家（不区分大小写），请用账号 ID 或网址查询：\n{listed}{more}'

    async def atlas_fallback(self, event, mode, aid, exc, trace):
        """The tarkov.dev snapshot when Atlas failed (maintainer decision), said so in the reply."""
        reason = failure_text(exc)
        logger.warning('Tarkov [%s] Atlas %s lookup failed, using tarkov.dev: %s', trace, mode, type(exc).__name__)
        try:
            summary = await self.profile(mode, aid)
        except NotFound:
            raise QueryError(f'{reason}；tarkov.dev 上也没有账号 ID {aid} 的 {label_of(mode)} 快照，请稍后再试') from None
        except asyncio.CancelledError:
            raise
        except Exception as second:
            logger.warning('Tarkov [%s] tarkov.dev fallback failed: %s', trace, type(second).__name__)
            raise QueryError(f'{reason}；改用 tarkov.dev 快照也没有成功，请稍后再试') from None
        return await self.reply(event, summary, mode, aid, [f'{reason}，下面是 tarkov.dev 的快照（不是实时数据）。'], trace)

    async def by_name(self, event, mode, explicit, target, trace, id_missing=None):
        label = label_of(mode)
        matches, total = await asyncio.to_thread(self.accounts.lookup, target)
        where = '记录'
        # An account id tried as a name uses only the record: no index download for it (review R-03).
        if not matches and self.index_enabled() and id_missing is None:
            # The downloaded index is per mode; the default (or chosen) mode's index is used.
            where = '名单'
            if self.names.needs_refresh(path_of(mode)) and not self.names.has_index(path_of(mode)):
                size = '，约 30 MB' if mode == 'pvp' else ''
                yield event.plain_result(f'正在下载 {label} 玩家名单（第一次按昵称查询这个模式时需要{size}，可能需要几十秒），请稍候。')
            matches, total = await self.names.lookup(path_of(mode), target)
        logger.info('Tarkov [%s] name matches=%s source=%s', trace, total, where)
        if not matches and id_missing:
            # Nobody recorded an all-digit name like this: what is known about the account id is the answer (it may
            # have a profile in another mode).
            yield event.plain_result(id_missing)
            return
        if not matches:
            # The index is per mode and only the chosen (or default) mode's was searched (review R-04).
            other = '' if explicit else f"其他模式的名单要写上模式查询，例如 {self.cmd('pve', target)}。"
            unknown = (f'记录和 tarkov.dev 的 {label} 昵称名单（{every(self.names.refresh_seconds)}更新一次）里都没有 {target}。{other}'
                       if self.index_enabled() else f'还不知道 {target} 的账号 ID。')
            hint = await self.suggestion(mode, explicit, target)
            yield event.plain_result(unknown+hint+self.first_time(mode, explicit=explicit)+'查到一次后，之后就能直接用名字查询。')
            return
        exact = [match for match in matches if match[1] == target]
        if len(exact) != 1 and len(matches) > 1:
            listed = '\n'.join(f'{name}（ID {aid}）' for aid, name in matches[:5])
            more = f'\n等，共 {total} 个' if total > 5 else ''
            yield event.plain_result(f'有多个名为 {target} 的玩家（不区分大小写），请用账号 ID 或网址查询：\n{listed}{more}')
            return
        aid = (exact or matches)[0][0]
        reply, missing = await self.show(event, aid, mode, explicit, trace, asked=target, where=where)
        if reply:
            yield reply
            return
        # The name is someone else's than the player meant, perhaps: name the accounts spelled almost the same.
        others = await self.similar_accounts(mode, target, exclude=aid)
        hint = ''
        if others:
            listed = '、'.join(f'{name}（账号 ID {other}）' for other, name in others)
            hint = f'名字相近的还有：{listed}，可以发送 {self.command_for(mode, explicit)} {others[0][1]} 试试。'
        yield event.plain_result(f'{target} 的账号 ID 是 {aid}。'+missing+hint)

    async def similar_accounts(self, mode, target, exclude=None):
        """[(aid, name)] whose name differs from `target` only by '_' and '-' (players mix them up), from the shared
        record and, when it is on, the index already downloaded for the mode (nothing is fetched)."""
        found = await asyncio.to_thread(self.accounts.similar, target)
        if self.index_enabled():
            found += await self.names.similar(path_of(mode), target)
        unique = list({aid: (aid, name) for aid, name in reversed(found) if aid != exclude}.values())[::-1]
        return unique[:SUGGESTIONS]

    def command_for(self, mode, explicit):
        return self.cmd(self.mode_word(mode)) if explicit else self.cmd()

    async def suggestion(self, mode, explicit, target, command=None):
        """A "did you mean" sentence for a name nobody knows, or ''."""
        found = await self.similar_accounts(mode, target)
        if not found:
            return ''
        listed = '、'.join(f'{name}（账号 ID {aid}）' for aid, name in found)
        return f'你是不是要找：{listed}？可以发送 {command or self.command_for(mode, explicit)} {found[0][1]}。如果都不是，'

    def output_mode(self):
        mode = str(self.config.get('output_mode', 'image'))
        return mode if mode in OUTPUT_MODES else 'image'

    async def reply(self, event, summary, game_mode, aid, notes, trace):
        """The profile in the configured output: an image (text if drawing fails), plain text or Markdown.
        Every reply says the data is a snapshot and links the player's page, where it can be refreshed."""
        if summary.get('source') == 'atlas':
            return await self.atlas_picture(event, summary, game_mode, aid, notes, trace)
        refresh = page_url(game_mode, aid)
        if game_mode == 'arena':
            return await self.arena_reply(event, summary, aid, notes, refresh, trace)
        card = build_card(summary, label_of(game_mode), self.skill_count(), notes, refresh_url=refresh)
        # The account asked for, not the document's own "aid" field, so the id and the link always agree (review R-06).
        if card['aid'] != aid:
            logger.warning('Tarkov [%s] profile %s names account %s', trace, aid, card['aid'])
            card['aid'] = aid
        mode = self.output_mode()
        if mode == 'image':
            try:
                await self.prepare_font()
                data = await asyncio.to_thread(render_image, card)
                # The image cannot carry a clickable link; one text line after it does.
                return event.chain_result([Image.fromBytes(data), Plain(f'刷新数据：在 tarkov.dev 打开 {refresh}')])
            except Exception as exc:
                logger.warning('Tarkov [%s] image rendering failed, sending text: %s', trace, type(exc).__name__)
                card['notes'].append(str(exc) if isinstance(exc, FontDownloadError) else '（图片生成失败，已改为文字）')
                return event.plain_result(render_text(card))
        if mode == 'markdown':
            return event.plain_result(render_markdown(card))
        return event.plain_result(render_text(card))

    async def arena_reply(self, event, summary, aid, notes, refresh, trace):
        card = build_arena_card(summary, notes, refresh_url=refresh)
        if card['aid'] != aid:
            logger.warning('Tarkov [%s] arena profile %s names account %s', trace, aid, card['aid'])
            card['aid'] = aid
        mode = self.output_mode()
        if mode == 'image':
            try:
                await self.prepare_font()
                data = await asyncio.to_thread(render_arena_image, card)
                return event.chain_result([Image.fromBytes(data), Plain(f'刷新数据：在 tarkov.dev 打开 {refresh}')])
            except Exception as exc:
                logger.warning('Tarkov [%s] arena image failed, sending text: %s', trace, type(exc).__name__)
                card['notes'].append(str(exc) if isinstance(exc, FontDownloadError) else '（图片生成失败，已改为文字）')
        if mode == 'markdown':
            return event.plain_result(render_arena_markdown(card))
        return event.plain_result(render_arena_text(card))

    def refresh_hint(self, mode, aid):
        """How to get fresh Atlas data, for the profile reply."""
        _, refreshes, _, _ = self.quota_limits()
        if not refreshes:
            return f'插件会缓存 {self.atlas_cache_minutes()} 分钟，之后再查会重新向 Atlas 查询。'
        return (f"要看最新数据，在命令最后加“刷新”，例如 {self.cmd(self.mode_word(mode), aid, '刷新')}"
                f'（每人每小时 {refreshes} 次）。')

    async def atlas_picture(self, event, summary, mode, aid, notes, trace):
        """The Atlas player picture (always an image: text and Markdown are no longer maintained, maintainer
        2026-11-08), then one text line with the links and the reply's notes."""
        page = page_url(mode, aid)
        lines = [f'数据来源：Atlas {ATLAS_URL}　tarkov.dev 玩家页：{page}', *notes, self.refresh_hint(mode, aid)]
        try:
            card = await asyncio.to_thread(build_atlas_card, summary['doc'], mode, skill_limit=self.skill_count())
            # Always display the requested account id, matching the reply's links even with a malformed upstream id.
            card['aid'] = aid if mode == 'arena' else str(aid)
            # Arena achievements have no known icons in tarkov.dev's catalog; the register uses text cards.
            icons = {} if mode == 'arena' else await self.atlas_icons(card, trace)
            await self.prepare_font()
            async with self.atlas_image_slots:
                data = await asyncio.to_thread(render_atlas_image, card, icons)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('Tarkov [%s] Atlas picture failed: %s', trace, type(exc).__name__)
            if isinstance(exc, FontDownloadError):
                return event.plain_result('\n'.join([str(exc), *lines]))
            return event.plain_result('\n'.join([f"{summary['nickname']}（账号 ID {aid}）的 {label_of(mode)} 档案图片生成失败"
                                                 f'（{type(exc).__name__}），请稍后再试。', *lines]))
        return event.chain_result([Image.fromBytes(data), Plain('\n'.join(lines))])

    async def atlas_icons(self, card, trace):
        """The pictures the card needs from the local cache, downloading missing ones (failures draw placeholders)."""
        try:
            catalog = await self.atlas_assets.catalog_for(card['achievement_ids'])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Without tarkov.dev's public data only the item pictures (linked in Atlas' document) are known.
            logger.warning('Tarkov [%s] icon catalog unavailable: %s', trace, type(exc).__name__)
            catalog = {}
        achievements = {key: catalog.get('achievements', {}).get(key) for key in card['achievement_ids']}
        badge = level_badge(catalog, card['level'])
        prestige = catalog.get('prestige', {}).get(str(card['prestige'])) if card['prestige'] else None
        # (`filter` is AstrBot's event filter in this module, not the builtin.)
        links = [*card['assets'], *achievements.values(), badge, prestige]
        found = await self.atlas_assets.images([link for link in links if link])
        return {'level_badge': found.get(badge), 'prestige': found.get(prestige), 'items': found,
                'achievements': {key: found.get(url) for key, url in achievements.items() if url}}

    def skill_count(self):
        return self.int_setting('skill_count', 10, 0, 49)

    async def profile(self, mode, aid, fresh=False):
        """Summary of one profile, from the in-memory cache when it is recent enough (`fresh` skips it). A missing
        profile is cached too, but for at most NOT_FOUND_SECONDS: every query looks at all modes, and a player
        searched on the website a moment ago should show up soon. Queries needing the same profile at the same time
        share one request."""
        key = (mode, aid)
        ttl = self.int_setting('profile_cache_minutes', 10, 0, 1440)*60
        cached = self.cache.get(key)
        if (not fresh and cached
                and time.monotonic()-cached[0] < (ttl if cached[1] is not None else min(ttl, NOT_FOUND_SECONDS))):
            if cached[1] is None:
                raise NotFound()
            return cached[1]
        task = self.pending.get(key)
        if task is None:
            if len(self.pending) >= MAX_PENDING_PROFILES:
                raise QueryError("玩家查询请求较多，请稍后再试")
            task = asyncio.ensure_future(self.fetch(mode, aid, ttl))
            self.pending[key] = task
            task.add_done_callback(lambda done: (self.pending.pop(key, None),
                                                 done.cancelled() or done.exception()))
        # Shielded: one user giving up must not cancel the request the others wait for.
        return await asyncio.shield(task)

    async def fetch(self, mode, aid, ttl):
        key = (mode, aid)
        try:
            try:
                await asyncio.wait_for(self.fetches.acquire(), PROFILE_QUEUE_SECONDS)
            except TimeoutError:
                raise QueryError('玩家查询排队超时，请稍后再试') from None
            try:
                raw = await transport.fetch_profile(path_of(mode), aid, timeout_seconds=min(60, self.timeout()), proxy=self.proxy(), client=self.http)
            finally:
                self.fetches.release()
        except NotFound:
            self.remember_profile(key, None, ttl)
            raise
        summary = summarize_arena(raw) if mode == 'arena' else summarize(raw)
        summary['fetched'] = time.time()
        # Every profile fetched teaches the shared name record (maintainer: names come from the users).
        try:
            await asyncio.to_thread(self.accounts.remember, aid, summary['nickname'])
        except sqlite3.Error as exc:
            logger.warning('Tarkov name record not saved: %s', type(exc).__name__)
        self.remember_profile(key, summary, ttl)
        return summary

    def remember_profile(self, key, summary, ttl):
        """Cache a summary, or None for "no profile in this mode"."""
        if ttl:
            self.cache.pop(key, None)
            if len(self.cache) >= CACHE_ENTRIES:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic(), summary)

    def timeout(self):
        return self.int_setting('download_timeout_seconds', 120, 15, 900)

    async def download_index(self, mode_path, target, etag):
        try:
            return await transport.fetch_index(mode_path, target, etag=etag, timeout_seconds=self.timeout(), proxy=self.proxy(), client=self.http)
        except NotFound:
            raise QueryError(f'tarkov.dev 上没有 {LABEL_OF_PATH.get(mode_path, mode_path)} 模式的玩家名单') from None
        except TimeoutError as exc:
            raise IndexTimeout(str(exc)) from exc

    async def download_public(self, key, *, etag=None, last_modified=None):
        return await fetch_public(key, etag=etag, last_modified=last_modified,
                                  timeout_seconds=min(60, self.timeout()), proxy=self.proxy(), client=self.http, validate=False)

    async def public_resource(self, resource, *, mode=None, language=None, item_id=None, fresh=False, allow_stale=False):
        """Shared infrastructure for the forthcoming feature-gated queries; never downloads on initialization."""
        key = ResourceKey(resource, public_mode_path(mode) if mode is not None else None, language, item_id)
        return await self.public_data.get(key,
                                          ttl_seconds=self.int_setting('public_data_cache_minutes', 5, 0, 1440)*60,
                                          fresh=fresh, allow_stale=allow_stale)

    def status_enabled(self):
        return self.config.get('status_enabled', False) is True

    def status_interval(self):
        return self.int_setting('status_poll_minutes', 30, 10, 90)*60

    def target_metadata(self, target):
        platforms = getattr(getattr(self.context, 'platform_manager', None), 'platform_insts', [])
        for platform in platforms:
            metadata = platform.meta()
            if metadata.id == target.split(':', 1)[0]:
                return metadata
        return None

    def load_sessions(self):
        try:
            return self.accounts.sessions()
        except sqlite3.Error as exc:
            logger.warning('Tarkov remembered sessions unavailable: %s', type(exc).__name__)
            return []

    async def remember_session(self, event):
        """Remember a group session that was allowed to use the plugin, so pushes without a target list can reach it.
        Only the conversation id is stored (no user id), at most once an hour per session."""
        origin = event.unified_msg_origin
        if not isinstance(origin, str) or origin.split(':', 2)[1:2] != ['GroupMessage']:
            return
        now = time.monotonic()
        if origin in self.session_ids and now-self.session_written.get(origin, -3600) < 3600:
            return
        self.session_written[origin] = now
        try:
            await asyncio.to_thread(self.accounts.touch_session, origin)
        except sqlite3.Error as exc:
            logger.warning('Tarkov session record failed: %s', type(exc).__name__)
            return
        new = origin not in self.session_ids
        self.session_ids = [origin]+[other for other in self.session_ids if other != origin][:49]
        if new:
            # The first group may arrive after startup, when no poller was running for lack of targets.
            self.status_poller.start()
            self.boss_poller.start()

    def status_targets(self):
        return self.notification_targets('status_notify_targets', self.config.get('status_qq_push_enabled', False) is True)

    def notification_targets(self, setting, qq_enabled):
        targets = self.config.get(setting, [])
        if not isinstance(targets, list) or len(targets) > 50:
            return []
        if not targets:
            # No list configured: every group session that has used the plugin (each is checked like a listed target).
            targets = list(self.session_ids)
        allowed = self.config_list('allowed_origins')
        if allowed is None:
            return []
        valid = []
        for target in targets:
            if not isinstance(target, str):
                continue
            target = target.strip()
            parts = target.split(':', 2)
            if len(parts) != 3 or not all(parts) or parts[1] not in ('GroupMessage', 'FriendMessage', 'OtherMessage') or any(c.isspace() for c in target):
                continue
            if allowed and target not in allowed:
                continue
            metadata = self.target_metadata(target)
            if metadata is not None:
                if getattr(metadata, 'support_proactive_message', True) is False:
                    continue
                if metadata.name in QQ_OFFICIAL and not qq_enabled:
                    continue
            valid.append(target)
        return list(dict.fromkeys(valid))

    def status_broadcast_active(self):
        return (not self._terminated and self.status_enabled()
                and self.config.get('status_broadcast_enabled', False) is True and bool(self.status_targets()))

    @staticmethod
    def status_monitor_error(exc):
        if isinstance(exc, (PermanentDeliveryError, PermissionError)):
            logger.warning('Tarkov status broadcast target paused; check platform permissions, then disable and re-enable broadcasts to reset')
            return
        logger.warning('Tarkov status monitor failed: %s', type(exc).__name__)

    async def status_snapshot(self, *, fresh=False, allow_stale=False):
        snapshot = await self.public_resource('status', fresh=fresh, allow_stale=allow_stale)
        document = await snapshot.json()
        state = normalize_status(document['data'])
        return {'state': state, 'checked_at': snapshot.checked_at, 'fetched_at': snapshot.fetched_at,
                'last_modified': snapshot.last_modified, 'from_cache': snapshot.from_cache,
                'stale': snapshot.stale, 'error': ('连接失败，展示上次成功数据：'+snapshot.error) if snapshot.stale else None}

    async def fetch_status_for_monitor(self):
        return await self.status_snapshot(fresh=True)

    async def prepare_status_image(self, card):
        await self.prepare_font()
        async with self.status_image_slots:
            return await asyncio.to_thread(render_status_image, card)

    async def status_picture(self, event, card):
        try:
            data = await self.prepare_status_image(card)
            return event.chain_result([Image.fromBytes(data)])
        except Exception as exc:
            self.status_monitor_error(exc)
            if isinstance(exc, FontDownloadError):
                return event.plain_result(str(exc))
            return event.plain_result('服务器状态图片生成失败，请稍后再试。')

    async def status_query(self, event, arguments=''):
        if not self.allowed(event):
            return await self.status_picture(event, {'error': '此会话或用户没有塔科夫查询权限。'})
        if not self.status_enabled():
            return await self.status_picture(event, {'error': '服务器状态查询尚未开启，请联系管理员在设置中启用。'})
        if arguments:
            return await self.status_picture(event, {'error': '状态查询不需要参数。用法：'+self.cmd('status')})
        owner = self.owner(event)
        if owner in self.active:
            return await self.status_picture(event, {'error': '你的上一次查询还没完成，请稍候。'})
        self.active.add(owner)
        try:
            try:
                card = await self.status_snapshot(allow_stale=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status_monitor_error(exc)
                message = str(exc) if isinstance(exc, QueryError) else '连接超时，请稍后再试。' if isinstance(exc, TimeoutError) else '本次获取失败，请稍后再试。'
                card = {'error': '无法取得服务器状态：'+message+' 此结果不表示游戏停服。'}
            return await self.status_picture(event, card)
        finally:
            self.active.discard(owner)

    async def send_status_notification(self, target, image_data):
        if not self.status_broadcast_active() or target not in self.status_targets():
            raise PermanentDeliveryError('目标不再允许播报')
        metadata = self.target_metadata(target)
        if metadata is not None and metadata.name in QQ_OFFICIAL and self.config.get('status_qq_push_enabled', False) is not True:
            raise PermanentDeliveryError('QQ 主动播报尚未确认可用')
        return await self.context.send_message(target, MessageChain(chain=[Image.fromBytes(image_data)]))

    def boss_enabled(self):
        return self.config.get('boss_enabled', False) is True

    def boss_event_ignore(self):
        """normalizedName values of mobs never shown as event bosses (settings; lower-cased, bounded)."""
        values = self.config.get('boss_event_ignore', [])
        if isinstance(values, str): values = [values]
        if not isinstance(values, (list, tuple, set)): return set()
        return {text.lower()[:64] for text in (str(v).strip() for v in values) if text}

    def boss_modes(self):
        values = self.config.get('boss_modes', ['pvp', 'pve', 'season'])
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(mode for mode in map(parse_mode, values) if mode in GAME_MODES))

    def boss_interval(self):
        return self.int_setting('boss_poll_minutes', 30, 10, 90)*60

    def boss_targets(self):
        return self.notification_targets('boss_notify_targets', self.config.get('boss_qq_push_enabled', False) is True)

    def boss_broadcast_active(self):
        return (not self._terminated and self.boss_enabled() and bool(self.boss_modes())
                and self.config.get('boss_broadcast_enabled', False) is True and bool(self.boss_targets()))

    @staticmethod
    def boss_monitor_error(exc):
        if isinstance(exc, (PermanentDeliveryError, PermissionError)):
            logger.warning('Tarkov Boss target paused; fix platform permissions, then disable/save/reload and enable broadcasts')
        else:
            logger.warning('Tarkov Boss monitor failed: %s', type(exc).__name__)

    async def boss_snapshot(self, mode, *, fresh=False, allow_stale=False):
        snapshot = await self.public_resource('maps', mode=mode, fresh=fresh, allow_stale=allow_stale)
        translations = await asyncio.gather(*(self.public_resource('maps', mode=mode, language=language,
            allow_stale=True) for language in ('zh', 'en')), return_exceptions=True)
        dictionaries, warning, signatures = [], False, []
        for translation in translations:
            if isinstance(translation, asyncio.CancelledError): raise translation
            if isinstance(translation, BaseException):
                warning = True; signatures.append(None)
            else:
                dictionaries.append(translation)
                warning |= translation.stale
                signatures.append(await asyncio.to_thread(hash, translation.body))
        signature = (await asyncio.to_thread(hash, snapshot.body), *signatures, tuple(sorted(self.boss_event_ignore())))
        cached = self.boss_normalized.get(mode)
        if cached and cached[0] == signature:
            state = cached[1]
        else:
            dictionaries = [(await translation.json())['data'] for translation in dictionaries]
            document = await snapshot.json()
            state = await asyncio.to_thread(normalize_bosses, document['data'], dictionaries, self.boss_event_ignore())
            if not self._terminated:
                self.boss_normalized[mode] = (signature, state)
        return {'mode': mode, 'state': state, 'checked_at': snapshot.checked_at, 'fetched_at': snapshot.fetched_at,
                'last_modified': snapshot.last_modified, 'stale': snapshot.stale, 'from_cache': snapshot.from_cache,
                'translation_warning': warning}

    async def fetch_boss_for_monitor(self, mode):
        # Freezing the notification's pages measures labels with the same font as the final image.
        await self.prepare_font()
        return await self.boss_snapshot(mode, fresh=True)

    async def prepare_boss_image(self, page, index=1, total=1, token=None):
        await self.prepare_font()
        token = page.get('snapshot_token', '') if token is None else token
        command = self.cmd('boss', '页', token, index+1 if index < total else 1) if token else ''
        async with self.boss_image_slots:
            portraits = await self.boss_portraits.all() if not page.get('error') else {}
            return await asyncio.to_thread(render_boss_page, page, token, index, total, command, portraits)

    async def boss_picture(self, event, page, index=1, total=1, token=None):
        try:
            image = await self.prepare_boss_image(page, index, total, token)
            return event.chain_result([Image.fromBytes(image)])
        except Exception as exc:
            self.boss_monitor_error(exc)
            if isinstance(exc, FontDownloadError):
                return event.plain_result(str(exc))
            return event.plain_result('Boss 表格图片生成失败，请稍后再试。')

    async def boss_query(self, event, arguments=''):
        if not self.allowed(event):
            return await self.boss_picture(event, {'error': '此会话或用户没有塔科夫查询权限。'})
        if not self.boss_enabled():
            return await self.boss_picture(event, {'error': 'Boss 查询尚未开启，请联系管理员在设置中启用。'})
        owner = self.owner(event)
        if owner in self.active:
            return await self.boss_picture(event, {'error': '你的上一次查询还没完成，请稍候。'})
        self.active.add(owner)
        try:
            words = arguments.lower().split()
            if words and words[0] in ('页', 'page'):
                if len(words) != 3 or not re.fullmatch('[0-9a-f]{12}', words[1]) or not re.fullmatch('[0-9]{1,4}', words[2]):
                    raise QueryError('续查用法：'+self.cmd('boss 页 <快照编号> <页码>'))
                pages = await self.boss_store.thread(self.boss_store.query, words[1])
                index = int(words[2])
                if not pages: raise QueryError('表格快照已过期或被清理，请重新查询。')
                if not 1 <= index <= len(pages): raise QueryError(f'页码超出范围，此快照共 {len(pages)} 页。')
                return await self.boss_picture(event, pages[index-1], index, len(pages), words[1])
            # Boss-only aliases: keep player query parsing and its defaults independent.
            def boss_mode(tokens):
                token = ' '.join(tokens)
                return 'season' if token in ('pvp s', 'pvps') else parse_mode(token)

            if words and words[0] in ('默认', 'default'):
                mode = boss_mode(words[1:])
                if mode not in GAME_MODES:
                    raise QueryError('请选择 pvp、pve 或赛季模式。用法：'+self.cmd('boss 默认 <模式>'))
                await asyncio.to_thread(self.accounts.set_boss_mode, self.binding_key(event), mode)
                return event.plain_result(f'Boss 默认模式已设为 {label_of(mode)}。之后发送 '+self.cmd('boss')+' 即可查询。')
            changed = any(word in ('变化', 'changes') for word in words)
            words = [word for word in words if word not in ('变化', 'changes')]
            if words:
                mode = boss_mode(words)
                modes = [mode] if mode in GAME_MODES else []
            else:
                preferred = await asyncio.to_thread(self.accounts.boss_mode, self.binding_key(event))
                modes = [preferred] if preferred else self.boss_modes()
            if not modes:
                raise QueryError('请选择 pvp、pve 或赛季模式。用法：'+self.cmd('boss [模式] [变化]'))
            pages = []
            for mode in modes:
                try:
                    if changed:
                        previous = await self.boss_store.thread(self.boss_store.latest, mode)
                        if not previous: raise QueryError('尚无已记录的刷率变化；开启监控后首次成功只建立基线。')
                        pages.extend(previous)
                    else:
                        card = await self.boss_snapshot(mode, allow_stale=True)
                        rows, _ = await asyncio.to_thread(build_table, None, card['state'])
                        meta = {key: value for key, value in card.items() if key != 'state'}
                        await self.prepare_font()
                        pages.extend(await asyncio.to_thread(paginate_table, meta, rows))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.boss_monitor_error(exc)
                    message = str(exc) if isinstance(exc, QueryError) else '本次数据获取失败，请稍后再试。'
                    pages.append({'mode': mode, 'error': message+' 此结果不表示刷率为零。'})
            token = await self.boss_store.thread(self.boss_store.save_query, pages)
            return await self.boss_picture(event, pages[0], 1, len(pages), token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.boss_monitor_error(exc)
            message = str(exc) if isinstance(exc, QueryError) else '本次表格查询失败，请稍后再试。'
            return await self.boss_picture(event, {'error': message})
        finally:
            self.active.discard(owner)

    async def send_boss_notification(self, target, image_data):
        if not self.boss_broadcast_active() or target not in self.boss_targets():
            raise PermanentDeliveryError('目标不再允许 Boss 播报')
        return await self.context.send_message(target, MessageChain(chain=[Image.fromBytes(image_data)]))

    def matching_enabled(self):
        return self.config.get('matching_enabled', True) is True

    async def matching_query(self, event, arguments=''):
        """Average matching times as one picture for everyone (Atlas data; image only, whatever the output setting:
        maintainer). The picture carries the Atlas logo and link; the link is repeated as text because an image cannot
        be clicked. Errors are one text line."""
        if not self.allowed(event):
            return event.plain_result('此会话或用户没有塔科夫查询权限。')
        if not self.matching_enabled():
            return event.plain_result('匹配时间查询尚未开启，请联系管理员在设置中启用。')
        if arguments:
            return event.plain_result('匹配时间查询不需要参数。用法：'+self.cmd('匹配'))
        owner = self.owner(event)
        if owner in self.active:
            return event.plain_result('你的上一次查询还没完成，请稍候。')
        self.active.add(owner)
        try:
            try:
                result = await self.matching.get()
            except asyncio.CancelledError:
                raise
            except QueryError as exc:
                return event.plain_result(f'匹配时间查询失败：{exc}')
            except Exception as exc:
                logger.warning('Tarkov matching query failed: %s', type(exc).__name__)
                return event.plain_result('匹配时间查询失败，请稍后再试。')
            try:
                await self.prepare_font()
                async with self.matching_image_slots:
                    data = await asyncio.to_thread(render_matching_image, result)
            except Exception as exc:
                logger.warning('Tarkov matching image failed: %s', type(exc).__name__)
                if isinstance(exc, FontDownloadError):
                    return event.plain_result(str(exc))
                return event.plain_result('匹配时间图片生成失败，请稍后再试。')
            return event.chain_result([Image.fromBytes(data), Plain(f'数据来源：{ATLAS_NAME} {ATLAS_URL}')])
        finally:
            self.active.discard(owner)

    # ---- Weekly report (docs/weekly-report.md): nightly samples of the listed players, a rolling seven days ----

    def weekly_enabled(self):
        return self.config.get('weekly_enabled', False) is True and self.player_source() == 'atlas'

    def weekly_roster(self, mode):
        """The listed players of a mode as written in the settings: account ids or nicknames, without repeats."""
        seen, found = set(), []
        for text in self.config_list(WEEKLY_LISTS[mode]) or []:
            usable = (ACCOUNT_ID.fullmatch(text) and int(text) > 0) or NAME.fullmatch(text) or ATLAS_NICKNAME.fullmatch(text)
            if usable and text.lower() not in seen:
                seen.add(text.lower())
                found.append(text)
        return found[:WEEKLY_MAX_PLAYERS]

    def weekly_entries(self):
        return [(mode, entry) for mode in WEEKLY_LISTS for entry in self.weekly_roster(mode)]

    def weekly_modes(self):
        return [mode for mode in WEEKLY_LISTS if self.weekly_roster(mode)]

    def weekly_sampling_active(self):
        return not self._terminated and self.weekly_enabled() and bool(self.weekly_entries())

    def weekly_schedule(self):
        """(weekday 0-6, hour, minute) of the broadcast, UTC+8; Monday 10:00 when the settings are unusable."""
        return parse_schedule(self.config.get('weekly_push_weekday', '周一'), self.config.get('weekly_push_time', '10:00')) or (0, 10, 0)

    def weekly_targets(self):
        """Broadcast targets must be listed (maintainer): an empty list sends nothing, unlike status and Boss.
        QQ official targets need no extra switch (the maintainer verified proactive push on a deployed bot)."""
        targets = self.config.get('weekly_notify_targets', [])
        if not isinstance(targets, list) or not targets:
            return []
        return self.notification_targets('weekly_notify_targets', True)

    def weekly_broadcast_active(self):
        return (not self._terminated and self.weekly_enabled() and self.config.get('weekly_push_enabled', False) is True
                and bool(self.weekly_modes()) and bool(self.weekly_targets()))

    @staticmethod
    def weekly_error(exc):
        if isinstance(exc, (PermanentDeliveryError, PermissionError)):
            logger.warning('Tarkov weekly broadcast target refused; check the target and platform permissions')
            return
        logger.warning('Tarkov weekly task failed: %s', type(exc).__name__)

    async def weekly_start(self):
        """Start (or stop) the nightly sampler and the weekly broadcast according to the settings."""
        try:
            await asyncio.to_thread(self.weekly_store.prune)
        except (OSError, sqlite3.Error) as exc:
            logger.warning('Tarkov weekly data maintenance failed: %s', type(exc).__name__)
        if self.config.get('weekly_enabled', False) is True and self.player_source() != 'atlas':
            logger.info('Tarkov weekly report idle: it needs the Atlas player source')
        for task, active in ((self.weekly_sampler, self.weekly_sampling_active()), (self.weekly_broadcast, self.weekly_broadcast_active())):
            if active:
                task.start()
            else:
                await task.close()
        if self.weekly_enabled() and self.config.get('weekly_push_enabled', False) is True and not self.weekly_broadcast_active():
            logger.info('Tarkov weekly broadcast idle: it needs listed players and listed targets')

    async def weekly_resolve(self, mode, entry, *, deadline=None):
        """(account id, nickname, whether Atlas was asked) of a listed nickname: the shared name record first, then
        Atlas' search (one request). (None, None, asked) when it cannot be settled; an account id is the safe entry."""
        rows, _ = await asyncio.to_thread(self.accounts.lookup, entry)
        exact = [row for row in rows if row[1] == entry]
        if len(exact) == 1 or len(rows) == 1:
            aid, name = (exact or rows)[0]
            return int(aid), name, False
        if not ATLAS_NICKNAME.fullmatch(entry):
            logger.warning('Tarkov weekly roster: %s (%s) cannot be searched on Atlas; list the account ID instead', entry, mode)
            return None, None, False
        found = await self.atlas_players.search_once(entry, mode, deadline=deadline)
        exact = [item for item in found if item[1] == entry]
        folded = [item for item in found if item[1].lower() == entry.lower()]
        pick = exact if len(exact) == 1 else folded if len(folded) == 1 else None
        if not pick:
            logger.warning('Tarkov weekly roster: %s (%s) not found or ambiguous on Atlas; list the account ID instead', entry, mode)
            return None, None, True
        return pick[0][0], pick[0][1], True

    async def weekly_fetch(self, mode, aid, *, deadline=None):
        """One fresh Atlas document for the nightly sample (no user quota: the sampler paces itself)."""
        summary = await self.atlas_players.profile_once(mode, aid, deadline=deadline)
        await self.record_history(aid, mode, summary, 'weekly')
        try:
            await asyncio.to_thread(self.accounts.remember, aid, summary['nickname'])
        except sqlite3.Error as exc:
            logger.warning('Tarkov name record not saved: %s', type(exc).__name__)
        return summary

    async def weekly_save(self, aid, mode, summary, *, day=None):
        sample = await asyncio.to_thread(extract_sample, summary.get('doc'), mode)
        if sample is None:
            return SampleResult.INVALID
        return await asyncio.to_thread(self.weekly_store.add_sample, aid, mode, sample, summary.get('fetched'), day=day)

    async def weekly_players(self, mode):
        """[(entry, account id or None, nickname or None)] of a mode's roster; an id entry is its own account."""
        rows = await asyncio.to_thread(self.weekly_store.roster, mode, self.weekly_roster(mode))
        return [(entry, int(entry) if aid is None and ACCOUNT_ID.fullmatch(entry) else aid, name) for entry, aid, name in rows]

    async def weekly_feed(self, aid, mode, summary):
        """A user's fresh Atlas lookup of a listed player is one more sample, at no cost."""
        if not self.weekly_enabled() or mode not in WEEKLY_LISTS or not self.weekly_roster(mode):
            return
        try:
            if aid in {known for _, known, _ in await self.weekly_players(mode)}:
                await self.weekly_save(aid, mode, summary)
        except (OSError, sqlite3.Error) as exc:
            logger.warning('Tarkov weekly sample not saved: %s', type(exc).__name__)

    async def weekly_report(self, aid, mode):
        now = self.weekly_clock()
        samples = await asyncio.to_thread(self.weekly_store.samples, aid, mode, now-WEEK-WEEKLY_SLACK-86400)
        return build_report(samples, mode, now)

    async def weekly_ranking(self, mode):
        """The ranking card of a mode, or None when no listed player has a report yet."""
        entries, seen = [], set()
        for entry, aid, name in await self.weekly_players(mode):
            # Keep every alias available to personal queries, but rank each resolved account only once.
            if aid is not None:
                if aid in seen:
                    continue
                seen.add(aid)
            entries.append((name or entry, await self.weekly_report(aid, mode) if aid is not None else None))
        return build_ranking(entries, mode)

    async def weekly_picture(self, card):
        await self.prepare_font()
        async with self.weekly_image_slots:
            return await asyncio.to_thread(render_weekly_image, card)

    async def weekly_ranking_image(self, mode):
        card = await self.weekly_ranking(mode)
        return await self.weekly_picture(card) if card else None

    async def send_weekly(self, target, image_data):
        if not self.weekly_broadcast_active() or target not in self.weekly_targets():
            raise PermanentDeliveryError('目标不再允许周报播报')
        return await self.context.send_message(target, MessageChain(chain=[
            Image.fromBytes(image_data), Plain(f'数据来源：{ATLAS_NAME} {ATLAS_URL}')]))

    async def weekly_query(self, event, arguments=''):
        """`周报 [模式] [昵称、账号ID 或 排行]`: a listed player's last seven days, or the mode's ranking. Read from the
        nightly samples only; the command never asks Atlas. Image only (Atlas data); errors are one text line."""
        if not self.allowed(event):
            return event.plain_result('此会话或用户没有塔科夫查询权限。')
        if self.config.get('weekly_enabled', False) is not True:
            return event.plain_result('玩家周报尚未开启，请联系管理员在设置中启用。')
        if self.player_source() != 'atlas':
            return event.plain_result('玩家周报需要 Atlas 数据来源，当前设置的是 tarkov.dev 快照。')
        words = arguments.split()
        mode = parse_mode(words[0]) if words else None
        if mode:
            words = words[1:]
        if mode == 'arena':
            return event.plain_result('竞技场周报还没有实现，目前支持 pvp、pve、赛季。')
        if len(words) > 1:
            return event.plain_result(f"用法：{self.cmd('周报 [模式] [昵称、账号ID 或 排行]')}")
        target = words[0] if words else None
        mode = mode or self.default_mode()
        label = label_of(mode)
        owner = self.owner(event)
        if owner in self.active:
            return event.plain_result('你的上一次查询还没完成，请稍候。')
        self.active.add(owner)
        try:
            players = await self.weekly_players(mode)
            if not players:
                return event.plain_result(f'{label} 周报名单是空的，请管理员在插件设置的“玩家周报”里填写玩家。')
            chosen = None
            if target and target.lower() not in RANK_WORDS:
                wanted = target.lower()
                chosen = next((player for player in players if wanted in (player[0].lower(), str(player[1]), (player[2] or '').lower())), None)
                if chosen is None:
                    return event.plain_result(f"{target} 不在 {label} 周报名单内（名单由管理员在插件设置里维护）。"
                                              f"看排行：{self.cmd('周报', self.mode_word(mode), '排行')}")
            elif target is None:
                bound = await asyncio.to_thread(self.accounts.bound, self.binding_key(event))
                chosen = next((player for player in players if bound and player[1] == bound[0]), None)
            if chosen is not None:
                name = chosen[2] or chosen[0]
                report = await self.weekly_report(chosen[1], mode) if chosen[1] is not None else None
                if report is None:
                    return event.plain_result(f'{name} 的 {label} 周报样本还不够：插件每天凌晨采样一次，至少要有隔天的两次样本。')
                card = build_weekly_card(report)
            else:
                card = await self.weekly_ranking(mode)
                if card is None:
                    return event.plain_result(f'{label} 周报名单里的玩家样本还不够：插件每天凌晨采样一次，至少要有隔天的两次样本。')
            try:
                data = await self.weekly_picture(card)
            except Exception as exc:
                logger.warning('Tarkov weekly image failed: %s', type(exc).__name__)
                if isinstance(exc, FontDownloadError):
                    return event.plain_result(str(exc))
                return event.plain_result('周报图片生成失败，请稍后再试。')
            return event.chain_result([Image.fromBytes(data), Plain(f'数据来源：{ATLAS_NAME} {ATLAS_URL}')])
        except (OSError, sqlite3.Error) as exc:
            logger.warning('Tarkov weekly query failed: %s', type(exc).__name__)
            return event.plain_result('周报数据读取失败，请稍后再试。')
        finally:
            self.active.discard(owner)

    async def terminate(self):
        self._terminated = True
        await self.weekly_sampler.close()
        await self.weekly_broadcast.close()
        await self.matching.close()
        await self.atlas_players.close()
        await self.atlas_assets.close()
        await self.full_font.close()
        await self.atlas.close()
        await self.names.close()
        await self.status_poller.close()
        await self.boss_poller.close()
        await self.status_store.close()
        await self.boss_store.close()
        await self.boss_portraits.close()
        self.boss_normalized.clear()
        await self.public_data.close()
        self.cache.clear()
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.http.close()

