"""Player profiles from tarkov.dev's public snapshots: the "tarkov-dev" player source, and the stand-in when Atlas
fails. A snapshot exists only after someone searched the player on the tarkov.dev website and is not updated on its
own, so the replies say how to get one and how to refresh it.
"""
import asyncio
import re
import sqlite3
import time

from astrbot.api import logger
from astrbot.api.message_components import Image, Plain

from . import transport
from .arena import build_arena_card, summarize_arena
from .arena_image import render_arena_image
from .card import BOT_LIMIT, build_card
from .image import render_image
from .modes import GAME_MODES, MODES, label_of, page_url, path_of, search_url
from .names import NAME, SUGGESTIONS, NameIndex, every
from .profile import summarize
from .transport import NotFound, QueryError

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
MODE_WORDS = {'pvp': 'pvp', 'pve': 'pve', 'season': '赛季', 'arena': '竞技场'}
LABEL_OF_PATH = {path: label for path, label, _ in MODES.values()}


class IndexTimeout(TimeoutError):
    """A name index download timed out (the one download the timeout setting can help with)."""


def pick(matches, target):
    """(account id, name) when `matches` settle who `target` is: one match, or one spelled exactly so. Else None."""
    exact = [match for match in matches if match[1] == target]
    if len(exact) == 1 or len(matches) == 1:
        return (exact or matches)[0]
    return None


def ambiguous(target, matches, total):
    listed = '\n'.join(f'{name}（ID {aid}）' for aid, name in matches[:5])
    more = f'\n等，共 {total} 个' if total > 5 else ''
    return f'有多个名为 {target} 的玩家（不区分大小写），请用账号 ID 或网址查询：\n{listed}{more}'


def modes_text(modes):
    return '、'.join(label_of(mode) for mode in modes)


def failure_text(failed):
    """Why the requests of these modes failed, in one sentence."""
    def why(exc):
        if isinstance(exc, QueryError):
            return str(exc)
        return '连接超时' if isinstance(exc, TimeoutError) else type(exc).__name__
    return '；'.join(f'{label_of(mode)} 模式这次没有查到（{why(exc)}）' for mode, exc in failed.items())


def search_hint(mode, who='这名玩家', explicit=True):
    if explicit:
        return f'请先在 tarkov.dev 用 {label_of(mode)} 模式搜索一次{who}（{search_url(mode)}）'
    return f'请先在 tarkov.dev 搜索一次{who}（{search_url(mode)}，页面上可以切换 PVP / PVE / 赛季，请选对应的模式）'


class SnapshotCommands:
    def __init__(self, app):
        self.app = app
        self.names = NameIndex(app.root, self.download_index, app.config.int('name_index_refresh_hours'))
        # (mode, account id) -> (monotonic time, profile summary or None for "no profile")
        self.cache = {}
        # (mode, account id) -> the request in flight, shared by every query that needs it.
        self.pending = {}
        self.fetches = asyncio.Semaphore(FETCHES_AT_ONCE)

    # ---- settings ----

    def index_enabled(self):
        return self.app.config.flag('name_index_enabled')

    def looked_at(self, mode):
        """The modes a query about `mode` fetches, `mode` first. Arena is a separate game: asked for, it is fetched
        alone; it joins the main game's modes only with the "arena_by_default" setting."""
        if mode == 'arena':
            return ['arena']
        modes = list(GAME_MODES)+(['arena'] if self.app.config.flag('arena_by_default') else [])
        return [mode]+[other for other in modes if other != mode]

    # ---- names ----

    async def download_index(self, mode_path, target, etag):
        try:
            return await transport.fetch_index(mode_path, target, etag=etag, timeout_seconds=self.app.timeout(),
                                               proxy=self.app.proxy(), client=self.app.http)
        except NotFound:
            raise QueryError(f'tarkov.dev 上没有 {LABEL_OF_PATH.get(mode_path, mode_path)} 模式的玩家名单') from None
        except TimeoutError as exc:
            raise IndexTimeout(str(exc)) from exc

    def index_notice(self, mode):
        """What to tell the user before a name lookup that has to download the mode's index first, or None."""
        if self.names.needs_refresh(path_of(mode)) and not self.names.has_index(path_of(mode)):
            size = '，约 30 MB' if mode == 'pvp' else ''
            return f'正在下载 {label_of(mode)} 玩家名单（第一次按昵称查询这个模式时需要{size}，可能需要几十秒），请稍候。'
        return None

    async def remember_name(self, aid, nickname):
        """Every profile fetched teaches the shared name record (names come from the users)."""
        try:
            await asyncio.to_thread(self.app.accounts.remember, aid, nickname)
        except sqlite3.Error as exc:
            logger.warning('Tarkov name record not saved: %s', type(exc).__name__)

    async def similar_accounts(self, mode, target, exclude=None):
        """[(aid, name)] whose name differs from `target` only by '_' and '-' (players mix them up), from the shared
        record and, when it is on, the index already downloaded for the mode (nothing is fetched)."""
        found = await asyncio.to_thread(self.app.accounts.similar, target)
        if self.index_enabled():
            found += await self.names.similar(path_of(mode), target)
        unique = list({aid: (aid, name) for aid, name in reversed(found) if aid != exclude}.values())[::-1]
        return unique[:SUGGESTIONS]

    async def suggestion(self, mode, target, command):
        """A "did you mean" sentence for a name nobody knows, or ''."""
        found = await self.similar_accounts(mode, target)
        if not found:
            return ''
        listed = '、'.join(f'{name}（账号 ID {aid}）' for aid, name in found)
        return f'你是不是要找：{listed}？可以发送 {command} {found[0][1]}。如果都不是，'

    # ---- profiles ----

    async def profile(self, mode, aid, fresh=False):
        """Summary of one profile, from the in-memory cache when it is recent enough (`fresh` skips it). A missing
        profile is cached too, but briefly: a player searched on the website a moment ago should show up soon.
        Queries needing the same profile at the same time share one request."""
        key = (mode, aid)
        ttl = self.app.config.int('profile_cache_minutes')*60
        cached = self.cache.get(key)
        if (not fresh and cached
                and time.monotonic()-cached[0] < (ttl if cached[1] is not None else min(ttl, NOT_FOUND_SECONDS))):
            if cached[1] is None:
                raise NotFound()
            return cached[1]
        task = self.pending.get(key)
        if task is None:
            if len(self.pending) >= MAX_PENDING_PROFILES:
                raise QueryError('玩家查询请求较多，请稍后再试')
            task = asyncio.ensure_future(self.fetch(mode, aid, ttl))
            self.pending[key] = task
            task.add_done_callback(lambda done: (self.pending.pop(key, None), done.cancelled() or done.exception()))
        # Shielded: one user giving up must not cancel the request the others wait for.
        return await asyncio.shield(task)

    async def fetch(self, mode, aid, ttl):
        try:
            await asyncio.wait_for(self.fetches.acquire(), PROFILE_QUEUE_SECONDS)
        except TimeoutError:
            raise QueryError('玩家查询排队超时，请稍后再试') from None
        try:
            raw = await transport.fetch_profile(path_of(mode), aid, timeout_seconds=min(60, self.app.timeout()),
                                                proxy=self.app.proxy(), client=self.app.http)
        except NotFound:
            self.remember_profile((mode, aid), None, ttl)
            raise
        finally:
            self.fetches.release()
        summary = summarize_arena(raw) if mode == 'arena' else summarize(raw)
        summary['fetched'] = time.time()
        await self.remember_name(aid, summary['nickname'])
        self.remember_profile((mode, aid), summary, ttl)
        return summary

    def remember_profile(self, key, summary, ttl):
        if ttl:
            self.cache.pop(key, None)
            if len(self.cache) >= CACHE_ENTRIES:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic(), summary)

    async def profiles(self, aid, first, trace, fresh=False):
        """({mode: summary}, {mode: error}) for account `aid`, `first` first. The modes are fetched together; "no
        profile" is in neither. A failed request is kept apart from "no profile", so a 429 is never reported as
        "this player has no profile"."""
        order = self.looked_at(first)
        results = await asyncio.gather(*(self.profile(mode, aid, fresh) for mode in order), return_exceptions=True)
        found, failed = {}, {}
        for mode, result in zip(order, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, NotFound):
                continue
            if isinstance(result, BaseException):
                logger.warning('Tarkov [%s] %s profile failed: %s', trace, mode, type(result).__name__)
                failed[mode] = result
            else:
                found[mode] = result
        return found, failed

    # ---- replies ----

    def none_found(self, aid, mode):
        """"No profile in any mode looked at", one sentence."""
        modes = self.looked_at(mode)
        if modes == ['arena']:
            return f'tarkov.dev 上还没有账号 ID {aid} 的竞技场档案。'
        words = '、'.join({'pvp': 'PVP', 'pve': 'PVE', 'season': '赛季', 'arena': '竞技场'}[m] for m in MODES if m in modes)
        return f'tarkov.dev 上还没有账号 ID {aid} 的档案（{words}都没有）。'

    def first_time(self, mode, who='这名玩家', command=None, explicit=True):
        return (search_hint(mode, who, explicit)
                + f'，再把跳转后的网址发给我（例如 {command or self.app.cmd()} {page_url(mode, 1234567)}）。')

    def command_for(self, mode, explicit):
        return self.app.cmd(MODE_WORDS[mode]) if explicit else self.app.cmd()

    async def reply(self, event, summary, mode, aid, notes, trace):
        """The profile picture, then the player's page as a text line (a picture cannot carry a link); opening that
        page is how the snapshot is refreshed."""
        refresh = page_url(mode, aid)
        if mode == 'arena':
            card, draw = build_arena_card(summary, notes, refresh_url=refresh), render_arena_image
        else:
            card = build_card(summary, label_of(mode), self.app.config.int('skill_count'), notes, refresh_url=refresh)
            draw = render_image
        # The account asked for, not the document's own "aid" field, so the id and the link always agree.
        card['aid'] = aid
        link = f'刷新数据：在 tarkov.dev 打开 {refresh}'
        try:
            picture = await self.app.render(draw, card)
        except Exception as exc:
            logger.warning('Tarkov [%s] snapshot picture failed', trace)
            return event.plain_result('\n'.join([self.app.render_failure(exc, f"{summary['nickname']} 的档案"), *notes, link]))
        return event.chain_result([Image.fromBytes(picture), Plain(link)])

    async def show(self, event, aid, mode, explicit, trace, who='这名玩家', asked=None, where='记录', fresh=False):
        """(reply, None) for account `aid`: the profile in `mode`, or (mode not chosen by the user) in the first other
        mode that has one; the reply also names the other modes with a profile. (None, why) when nothing can be shown.
        A failed request is raised when nothing else can be shown, so the user reads the error, not "no profile".
        `asked`: the name the account was looked up by, to note a rename."""
        cmd = self.app.cmd
        found, failed = await self.profiles(aid, mode, trace, fresh)
        notes = []
        if mode in found:
            shown = mode
        elif found and not explicit:
            shown = next(iter(found))
            if mode in failed:
                notes.append(f'{failure_text({mode: failed.pop(mode)})}，下面是 {label_of(shown)} 模式的档案。')
            else:
                notes.append(f'tarkov.dev 上还没有这个账号的 {label_of(mode)} 档案（要有的话，请在 tarkov.dev 用 '
                             f'{label_of(mode)} 模式搜索一次：{search_url(mode)}），下面是 {label_of(shown)} 模式的档案。')
        elif mode in failed or (failed and not found):
            raise failed.get(mode) or next(iter(failed.values()))
        else:
            unknown = f'；{failure_text(failed)}，请稍后再试' if failed else ''
            if found:
                others = '、'.join(f'{label_of(m)} 模式（{cmd(MODE_WORDS[m], aid)}）' for m in found)
                return None, (f'{label_of(mode)} 模式下还没有账号 ID {aid} 的档案，但 {others}下有档案{unknown}。'
                              f'要看 {label_of(mode)} 模式，' + search_hint(mode, who) + '，几分钟后再查。')
            return None, (self.none_found(aid, mode) + 'tarkov.dev 只保存在网站上被搜索过的玩家，'
                          + search_hint(mode, who, explicit or mode == 'arena') + '，几分钟后再查。')
        summary = found[shown]
        nickname = summary['nickname']
        if asked and nickname.lower() != asked.lower():
            notes.append(f'注意：{where}中的 {asked} 对应账号 ID {aid}，档案里现在的昵称是 {nickname}，可能改过名。')
        others = [m for m in found if m != shown]
        if others:
            notes.append(f'这个账号在 {modes_text(others)} 模式下也有档案，查询时写上模式即可'
                         f'（例如 {cmd(MODE_WORDS[others[0]], aid)}）。')
        if failed:
            notes.append(failure_text(failed)+'，请稍后再试。')
        age = time.time()-summary.get('fetched', time.time())
        if age >= CACHE_NOTE_SECONDS:
            # The website refresh the reply asks for is not seen while the profile is cached.
            notes.append(f'这是插件 {max(1, round(age/60))} 分钟前从 tarkov.dev 取得的数据。刚在网站上刷新过的话，'
                         '发送这名玩家的页面网址查询，即可立即看到新数据。')
        return await self.reply(event, summary, shown, aid, notes, trace), None

    # ---- the commands ----

    async def bind(self, event, mode, target, url, trace, aid=None, asked=None, note=''):
        """Bind the sender to an account, confirmed by a snapshot in any mode. `aid`: the account when the caller
        already settled it (the Atlas flow); `asked`: the nickname it was found by, which the snapshot must still
        show (a renamed account is not bound by its old name); `note`: a sentence put before the reply."""
        cmd = self.app.cmd
        if aid is not None:
            pass
        elif url or ACCOUNT_ID.fullmatch(target):
            aid = url[1] if url else int(target)
        else:
            rows, _ = await asyncio.to_thread(self.app.accounts.lookup, target)
            found = pick(rows, target)
            if not found:
                hint = await self.suggestion(mode, target, cmd('绑定')) if not rows else ''
                return event.plain_result(f'不能确定 {target} 是哪个账号。' + hint
                                          + self.first_time(mode, '自己', cmd('绑定'), explicit=False))
            aid, asked = found[0], target
        # A binding is for the account, whatever the mode: a profile in any mode confirms the account exists.
        found, failed = await self.profiles(aid, mode, trace, fresh=True)
        if not found and failed:
            raise next(iter(failed.values()))
        if not found:
            return event.plain_result(note+self.none_found(aid, mode)
                                      + self.first_time(mode, '自己', cmd('绑定'), explicit=mode == 'arena'))
        nickname = (found.get(mode) or next(iter(found.values())))['nickname']
        if asked and nickname.lower() != asked.lower():
            return event.plain_result(f'{note}无法确认昵称 {asked} 对应的账号；账号 ID {aid} 现在的昵称是 {nickname}。'
                                      '请检查昵称，或使用账号 ID / 玩家页网址绑定。')
        await asyncio.to_thread(self.app.accounts.bind, self.app.binding_key(event), aid, nickname)
        missing = [m for m in self.looked_at(mode) if m not in found and m not in failed]
        later = f'{modes_text(missing)} 模式的档案要在 tarkov.dev 用该模式搜索一次自己才会出现。' if missing else ''
        unknown = f'{failure_text(failed)}。' if failed else ''
        return event.plain_result(f'{note}已绑定：{nickname}（账号 ID {aid}），目前在 {modes_text(found)} 模式下有档案。'
                                  f'{unknown}{later}'
                                  f'之后发送 {cmd()} 即可查询自己（先看 {label_of(self.app.default_mode())}，没有时自动换成有档案的模式），'
                                  f"也可以写模式，例如 {cmd('pve')}、{cmd('赛季')}。"
                                  f'由于 {BOT_LIMIT}，档案不会自动更新：要看最新数据，请先在 tarkov.dev 上重新搜索或打开自己的页面。')

    async def query(self, event, mode, explicit, target, url, trace, refresh=False):
        """Yields the replies of a profile query: the sender's bound account, an account id or page link, or a name."""
        cmd = self.app.cmd
        if target is None:
            bound = await asyncio.to_thread(self.app.accounts.bound, self.app.binding_key(event))
            if not bound:
                yield event.plain_result(f"你还没有绑定账号。先在 tarkov.dev 搜索自己的游戏名，再发送：{cmd('绑定 <跳转后的网址>')}。\n"
                                         f"查别人：{cmd('[模式] <昵称、账号ID 或网址>')}；完整用法：{cmd('帮助')}")
                return
            reply, missing = await self.show(event, bound[0], mode, explicit, trace, who='自己', fresh=refresh)
            yield reply or event.plain_result(f'你绑定的账号 {bound[1]}：'+missing)
            return
        id_missing = None
        if url or ACCOUNT_ID.fullmatch(target):
            aid = url[1] if url else int(target)
            # A link usually means the user just opened the page (which refreshes the snapshot): skip the cache.
            reply, id_missing = await self.show(event, aid, mode, explicit, trace, fresh=bool(url) or refresh)
            # An all-digit nickname is possible; try it as a name before giving up.
            if reply or url or not NAME.fullmatch(target):
                yield reply or event.plain_result(id_missing)
                return
        async for result in self.by_name(event, mode, explicit, target, trace, id_missing):
            yield result

    async def by_name(self, event, mode, explicit, target, trace, id_missing=None):
        label = label_of(mode)
        matches, total = await asyncio.to_thread(self.app.accounts.lookup, target)
        where = '记录'
        # An account id tried as a name uses only the record: no index download for it.
        if not matches and self.index_enabled() and id_missing is None:
            where = '名单'
            notice = self.index_notice(mode)
            if notice:
                yield event.plain_result(notice)
            matches, total = await self.names.lookup(path_of(mode), target)
        logger.info('Tarkov [%s] name matches=%s source=%s', trace, total, where)
        if not matches and id_missing:
            # Nobody recorded an all-digit name like this: what is known about the account id is the answer.
            yield event.plain_result(id_missing)
            return
        if not matches:
            # The index is per mode and only the chosen (or default) mode's was searched.
            other = '' if explicit else f"其他模式的名单要写上模式查询，例如 {self.app.cmd('pve', target)}。"
            unknown = (f'记录和 tarkov.dev 的 {label} 昵称名单（{every(self.names.refresh_seconds)}更新一次）里都没有 {target}。{other}'
                       if self.index_enabled() else f'还不知道 {target} 的账号 ID。')
            hint = await self.suggestion(mode, target, self.command_for(mode, explicit))
            yield event.plain_result(unknown+hint+self.first_time(mode, explicit=explicit)+'查到一次后，之后就能直接用名字查询。')
            return
        found = pick(matches, target)
        if not found:
            yield event.plain_result(ambiguous(target, matches, total))
            return
        aid = found[0]
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

    async def close(self):
        await self.names.close()
        self.cache.clear()
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

