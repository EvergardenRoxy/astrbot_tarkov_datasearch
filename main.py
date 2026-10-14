import asyncio
import hashlib
import hmac
import re
import sqlite3
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools, register

from .tarkov_core import COMMAND_STYLES, transport
from .tarkov_core.accounts import Accounts, load_secret, owner_key
from .tarkov_core.modes import GAME_MODES, MODES, label_of, page_url, parse_mode, parse_player_url, path_of, search_url
from .tarkov_core.names import NAME, SUGGESTIONS, NameIndex
from .tarkov_core.arena import build_arena_card, render_arena_markdown, render_arena_text, summarize_arena
from .tarkov_core.arena_image import render_arena_image
from .tarkov_core.card import BOT_LIMIT, build_card
from .tarkov_core.help_image import render_help_image
from .tarkov_core.image import render_image
from .tarkov_core.output import render_markdown, render_text
from .tarkov_core.profile import summarize
from .tarkov_core.transport import NotFound, QueryError

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


@register('astrbot_tarkov_datasearch', 'Evergarden_Roxy', '绑定并查询塔科夫玩家档案（数据来自 tarkov.dev）', '0.4.4')
class TarkovPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.root = StarTools.get_data_dir('astrbot_tarkov_datasearch')
        # The downloaded name index is optional (off by default): names are mainly learned from the users (accounts.py).
        self.names = NameIndex(self.root, self.download_index, self.int_setting('name_index_refresh_hours', 168, 1, 720))
        self.accounts = Accounts(self.root/'accounts.sqlite3')
        self.root.mkdir(parents=True, exist_ok=True)
        # Keys the binding keys and the log trace ids, so neither can be turned back into a user id (review R-11).
        self.secret = load_secret(self.root/'binding.key')
        # (mode, account id) -> (monotonic time, profile summary)
        self.cache = {}
        # (mode, account id) -> the request in flight, shared by every query that needs it (review R-08).
        self.pending = {}
        self.fetches = asyncio.Semaphore(FETCHES_AT_ONCE)
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
        mode = parse_mode(self.config.get('default_mode', 'pvp'))
        return mode if mode in GAME_MODES else 'pvp'

    def arena_by_default(self):
        return bool(self.config.get('arena_by_default', False))

    def looked_at(self, mode):
        """The modes a query about `mode` fetches, `mode` first. Arena is a separate game: asked for, it is fetched
        alone; it joins the main game's modes only with the "arena_by_default" setting (maintainer)."""
        if mode == 'arena':
            return ['arena']
        modes = list(GAME_MODES)+(['arena'] if self.arena_by_default() else [])
        return [mode]+[other for other in modes if other != mode]

    def index_enabled(self):
        return bool(self.config.get('name_index_enabled', False))

    def binding_key(self, event):
        # unified_msg_origin is "<platform id>:<message type>:<session>"; a binding holds in every chat of a platform.
        return owner_key(self.secret, event.unified_msg_origin.split(':', 1)[0], event.get_sender_id())

    def style(self):
        """The wording of the "command_style" setting (unknown values: the default)."""
        return COMMAND_STYLES.get(str(self.config.get('command_style', 'mention')), COMMAND_STYLES['mention'])

    def cmd(self, *words):
        """A command as the user should type it: the style's command, then `words` (cmd('绑定') is the bind command)."""
        return ' '.join([self.style()['command'], *map(str, words)])

    def help_text(self):
        index = f'；另外会查 tarkov.dev 的昵称名单（{every(self.names.refresh_seconds)}更新一次）' if self.index_enabled() else ''
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
                + self.search_line())

    def search_line(self):
        """The search page as a text line. Every help reply ends with it, the image included (maintainer: the link
        is always given as text, whatever the output)."""
        return f'tarkov.dev 玩家搜索：{search_url(self.default_mode())}'

    def help_card(self):
        """What the help image shows; the same settings as help_text."""
        mode = self.default_mode()
        return {'command': self.style()['command'], 'note': self.style()['note'], 'mode': mode, 'label': label_of(mode), 'search': search_url(mode), 'example': page_url(mode, 1234567),
                'arena_by_default': self.arena_by_default(),
                'index': every(self.names.refresh_seconds) if self.index_enabled() else None}

    async def help_reply(self, event):
        """The help in the configured output: an image (maintainer: the text is hard to read on phones) followed by
        the search page as a text line (`search_line`), or the text, which ends with the same line, when the output is
        text or Markdown or drawing fails."""
        if self.output_mode() == 'image':
            try:
                data = await asyncio.to_thread(render_help_image, self.help_card())
                return event.chain_result([Image.fromBytes(data), Plain(self.search_line())])
            except Exception as exc:
                logger.warning('Tarkov help image failed, sending text: %s', type(exc).__name__)
        return event.plain_result(self.help_text())

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
            yield await self.help_reply(event)
            return
        if words and words[0].lower() in UNBIND_WORDS and len(words) == 1:
            action, words = 'unbind', []
        elif words and words[0].lower() in BIND_WORDS:
            action, words = 'bind', words[1:]
        mode = parse_mode(words[0]) if words else None
        if mode:
            words = words[1:]
        if len(words) > 1:
            yield await self.help_reply(event)
            return
        target = words[0] if words else None
        if action == 'bind' and target is None:
            yield event.plain_result(f"用法：{self.cmd('绑定 <tarkov.dev 玩家页网址或账号ID>')}，"
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
        logger.info('Tarkov [%s] %s mode=%s target=%s', trace, action, mode, target)
        try:
            if action == 'unbind':
                yield await self.unbind(event)
            elif action == 'bind':
                yield await self.bind(event, mode, target, url, trace)
            else:
                async for result in self.query(event, mode, explicit, target, url, trace):
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

    async def bind(self, event, mode, target, url, trace):
        if url or ACCOUNT_ID.fullmatch(target):
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
            return event.plain_result(self.none_found(aid, mode)
                                      + self.first_time(mode, '自己', self.cmd('绑定'), explicit=mode == 'arena'))
        nickname = (found.get(mode) or next(iter(found.values())))['nickname']
        await asyncio.to_thread(self.accounts.bind, self.binding_key(event), aid, nickname)
        missing = [m for m in self.looked_at(mode) if m not in found]
        later = (f'{self.modes_text(missing)} 模式的档案要在 tarkov.dev 用该模式搜索一次自己才会出现。' if missing else '')
        unknown = f'{self.failure_text(failed)}。' if failed else ''
        return event.plain_result(f'已绑定：{nickname}（账号 ID {aid}），目前在 {self.modes_text(found)} 模式下有档案。'
                                  f'{unknown}{later}'
                                  f'之后发送 {self.cmd()} 即可查询自己（先看 {label_of(self.default_mode())}，没有时自动换成有档案的模式），'
                                  f"也可以写模式，例如 {self.cmd('pve')}、{self.cmd('赛季')}。"
                                  f'由于 {BOT_LIMIT}，档案不会自动更新：要看最新数据，请先在 tarkov.dev 上重新搜索或打开自己的页面。')

    async def query(self, event, mode, explicit, target, url, trace):
        if target is None:
            bound = await asyncio.to_thread(self.accounts.bound, self.binding_key(event))
            if not bound:
                yield event.plain_result(f"你还没有绑定账号。先在 tarkov.dev 搜索自己的游戏名，再发送：{self.cmd('绑定 <跳转后的网址>')}。\n"
                                         f"查别人：{self.cmd('[模式] <昵称、账号ID 或网址>')}；完整用法：{self.cmd('帮助')}")
                return
            aid, nickname = bound
            reply, missing = await self.show(event, aid, mode, explicit, trace, who='自己')
            yield reply or event.plain_result(f'你绑定的账号 {nickname}：'+missing)
            return
        if url or ACCOUNT_ID.fullmatch(target):
            aid = url[1] if url else int(target)
            # A link usually means the user just opened the page (which refreshes the snapshot): skip the cache.
            reply, missing = await self.show(event, aid, mode, explicit, trace, fresh=bool(url))
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

    async def by_name(self, event, mode, explicit, target, trace, id_missing=None):
        label = label_of(mode)
        matches, total = await asyncio.to_thread(self.accounts.lookup, target)
        where = '记录'
        # An account id tried as a name uses only the record: no index download for it (review R-03).
        if not matches and self.index_enabled() and id_missing is None:
            # The downloaded index is per mode; the default (or chosen) mode's index is used.
            where = '名单'
            if self.names.needs_refresh(path_of(mode)):
                if self.names.has_index(path_of(mode)):
                    yield event.plain_result(f'正在检查 {label} 玩家名单的更新（{every(self.names.refresh_seconds)}一次），请稍候。')
                else:
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

    async def arena_reply(self, event, summary, aid, notes, refresh, trace):
        card = build_arena_card(summary, notes, refresh_url=refresh)
        if card['aid'] != aid:
            logger.warning('Tarkov [%s] arena profile %s names account %s', trace, aid, card['aid'])
            card['aid'] = aid
        mode = self.output_mode()
        if mode == 'image':
            try:
                data = await asyncio.to_thread(render_arena_image, card)
                return event.chain_result([Image.fromBytes(data), Plain(f'刷新数据：在 tarkov.dev 打开 {refresh}')])
            except Exception as exc:
                logger.warning('Tarkov [%s] arena image failed, sending text: %s', trace, type(exc).__name__)
                card['notes'].append('（图片生成失败，已改为文字）')
        if mode == 'markdown':
            return event.plain_result(render_arena_markdown(card))
        return event.plain_result(render_arena_text(card))

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
            task = asyncio.ensure_future(self.fetch(mode, aid, ttl))
            self.pending[key] = task
            task.add_done_callback(lambda done: (self.pending.pop(key, None),
                                                 done.cancelled() or done.exception()))
        # Shielded: one user giving up must not cancel the request the others wait for.
        return await asyncio.shield(task)

    async def fetch(self, mode, aid, ttl):
        key = (mode, aid)
        try:
            async with self.fetches:
                raw = await transport.fetch_profile(path_of(mode), aid, timeout_seconds=min(60, self.timeout()), proxy=self.proxy())
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
            return await transport.fetch_index(mode_path, target, etag=etag, timeout_seconds=self.timeout(), proxy=self.proxy())
        except NotFound:
            raise QueryError(f'tarkov.dev 上没有 {LABEL_OF_PATH.get(mode_path, mode_path)} 模式的玩家名单') from None
        except TimeoutError as exc:
            raise IndexTimeout(str(exc)) from exc

    async def terminate(self):
        self.cache.clear()
        for task in list(self.pending.values()):
            task.cancel()

