"""Atlas player lookups for the chat commands: local cache, shared requests, one busy retry, per-user quotas.

Every HTTP request goes through the plugin's single AtlasClient, so the shared start interval, the upstream
Retry-After pause and the minute/hour budget (atlas_cooldown.py) apply to all of them. Nothing here polls, prefetches
or retries in the background (Atlas author, 2026-11-08); requests happen only for a user's command.
"""
import asyncio
import collections
import math
import time

from .atlas_client import NICKNAME, AtlasDataError, AtlasHTTPError
from .atlas_cooldown import AtlasBudgetError, AtlasCooldownError
from .atlas_profile import AtlasProfileError, search_results, summarize
from .modes import label_of
from .transport import ConnectionFailure, NotFound, QueryError

# Atlas keeps a result for 60 seconds itself; a shorter local cache would only cost requests.
MIN_CACHE_SECONDS = 60
# "No profile" / "no such account" is remembered this long.
NOT_FOUND_SECONDS = 60
CACHE_ENTRIES = 256
MAX_PENDING = 32
# Users whose quota history is kept at most (oldest forgotten first).
QUOTA_USERS = 10000


# Refusals before any request reaches Atlas (the plugin's budget, the shared gate): the user's quota is given back.
NOT_SENT = (AtlasBudgetError, AtlasCooldownError)


def refund(charge):
    undo = getattr(charge, 'refund', None)
    if undo is not None:
        undo()


def keep(charge):
    """A request of this query reached Atlas: a later refusal (the lookup after a nickname search, the rename check
    after a lookup) no longer gives the query back."""
    mark = getattr(charge, 'keep', None)
    if mark is not None:
        mark()


class AtlasNotFound(NotFound):
    """Atlas answered: no such account, or no profile in this mode. Never used for a failed request."""


class QuotaError(QueryError):
    """A user's own hourly quota or cooldown (no request was sent)."""


def failure_text(exc):
    """Why an Atlas request failed, for the chat (never raw upstream text)."""
    if isinstance(exc, AtlasHTTPError):
        if exc.code == 'busy':
            return 'Atlas 正忙（网站同时查询的人太多）'
        if exc.code == 'rate':
            wait = f'，约 {math.ceil(exc.retry_after)} 秒后恢复' if exc.retry_after and math.isfinite(exc.retry_after) else ''
            return f'Atlas 暂时限制了这台服务器的查询次数{wait}'
        if exc.code in ('session', 'backend', 'lookup', 'unavailable'):
            return f'Atlas 暂时无法查询（{exc.code}）'
        return f'Atlas 查询失败（HTTP {exc.status}）'
    if isinstance(exc, AtlasCooldownError):
        wait = f'，约 {exc.retry_after_seconds} 秒后' if exc.retry_after_seconds else ''
        return f'Atlas 查询需要排队{wait}'
    if isinstance(exc, (AtlasDataError, AtlasProfileError)):
        return 'Atlas 返回的数据格式无法识别'
    if isinstance(exc, QueryError):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return '连接 Atlas 超时'
    return type(exc).__name__


def can_fall_back(exc):
    """Whether the tarkov.dev snapshot may stand in: Atlas failed, rather than answering "no such player" or
    refusing the user's own input or quota."""
    if isinstance(exc, (AtlasNotFound, QuotaError, AtlasBudgetError)):
        return False
    if isinstance(exc, AtlasHTTPError):
        return exc.code not in ('not_found', 'no_profile', 'mode', 'bad_account', 'bad_nickname')
    return isinstance(exc, (AtlasCooldownError, ConnectionFailure, AtlasDataError,
                            AtlasProfileError, TimeoutError))


def http_not_found(exc, mode, aid):
    """AtlasNotFound for Atlas' own "not found" answers, else None."""
    if isinstance(exc, AtlasHTTPError) and exc.code == 'not_found':
        return AtlasNotFound(f'Atlas 找不到账号 ID {aid}')
    if isinstance(exc, AtlasHTTPError) and exc.code == 'no_profile':
        return AtlasNotFound(f'账号 ID {aid} 在 {label_of(mode)} 模式下没有档案（Atlas）')
    return None


class AtlasPlayers:
    def __init__(self, client, *, cache_seconds=lambda: 600, clock=time.monotonic, wall_clock=time.time):
        self.client = client
        self.cache_seconds = cache_seconds
        self.clock, self.wall_clock = clock, wall_clock
        # (mode, aid) -> (monotonic time, summary or None for "no profile")
        self.cache = {}
        self.pending = {}

    def ttl(self):
        try:
            return max(MIN_CACHE_SECONDS, int(self.cache_seconds()))
        except (TypeError, ValueError, OverflowError):
            return 600

    def cached(self, mode, aid):
        """The cached summary, AtlasNotFound when "no profile" is remembered, or None (a request is needed)."""
        entry = self.cache.get((mode, aid))
        if entry is None:
            return None
        age = self.clock()-entry[0]
        if not 0 <= age < (self.ttl() if entry[1] is not None else min(self.ttl(), NOT_FOUND_SECONDS)):
            return None
        if entry[1] is None:
            return AtlasNotFound(f'账号 ID {aid} 在 {label_of(mode)} 模式下没有档案（Atlas）')
        return dict(entry[1], from_cache=True, cache_age=age)

    async def profile(self, mode, aid, *, refresh=False, charge=lambda: None):
        """One summary. `refresh` skips the local cache (Atlas may still answer from its own 60-second cache).
        `charge` runs once before a request is needed (the user's quota); equal requests share one task."""
        if not refresh:
            hit = self.cached(mode, aid)
            if isinstance(hit, AtlasNotFound):
                raise hit
            if hit is not None:
                return hit
        key = (mode, aid)
        task = self.pending.get(key)
        charged = False
        if task is None:
            if len(self.pending) >= MAX_PENDING:
                raise QueryError('玩家查询请求较多，请稍后再试')
            charge()
            charged = True
            task = asyncio.ensure_future(self._fetch(mode, aid))
            self.pending[key] = task
            task.add_done_callback(lambda done: (self.pending.pop(key, None),
                                                 done.cancelled() or done.exception()))
        try:
            # Shielded: one user giving up must not cancel the request others wait for.
            summary = dict(await asyncio.shield(task))
        except NOT_SENT:
            if charged:
                refund(charge)
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            if charged:
                keep(charge)
            raise
        if charged:
            keep(charge)
        return summary

    async def _fetch(self, mode, aid):
        try:
            doc = await self._busy_retry(lambda: self.client.lookup(aid, mode))
        except AtlasHTTPError as exc:
            missing = http_not_found(exc, mode, aid)
            if missing:
                self._remember((mode, aid), None)
                raise missing from None
            raise
        if not isinstance(doc, dict) or doc.get('mode') not in (None, mode):
            raise AtlasProfileError()
        summary = summarize(doc, mode)
        # The raw document: the Atlas picture reads it, and the history stores it.
        summary['doc'] = doc
        summary['fetched'] = self.wall_clock()
        self._remember((mode, aid), summary)
        return summary

    async def search(self, nickname, mode, *, charge=lambda: None):
        """[(aid, nickname)] from Atlas' nickname search (one request; not cached)."""
        if not NICKNAME.fullmatch(nickname):
            raise QueryError('Atlas 只能搜索 3～15 个字符的昵称（字母、数字、_ -），更长的名字请用账号 ID 或玩家页网址查询')
        charge()
        try:
            doc = await self._busy_retry(lambda: self.client.search(nickname, mode))
        except NOT_SENT:
            refund(charge)
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            keep(charge)
            if isinstance(exc, AtlasHTTPError) and exc.code in ('not_found', 'no_profile'):
                return []
            raise
        keep(charge)
        return search_results(doc)

    @staticmethod
    async def _busy_retry(request):
        """`busy` means Atlas' eight shared lookup slots are full, not our limit: try once more after Retry-After
        (the client's gate waits for the pause it recorded); a second `busy` is final."""
        try:
            return await request()
        except AtlasHTTPError as exc:
            if exc.code != 'busy':
                raise
        return await request()

    def _remember(self, key, summary):
        self.cache.pop(key, None)
        while len(self.cache) >= CACHE_ENTRIES:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = (self.clock(), summary)

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class UserQuota:
    """Per-user hourly query and refresh counts with minimum intervals, in memory (keyed by the plugin's HMAC of the
    user). Only queries that need an Atlas request are counted; cached answers are free."""

    def __init__(self, limits, *, clock=time.monotonic):
        # limits() -> (queries per hour, refreshes per hour, seconds between queries, seconds between refreshes)
        self.limits = limits
        self.clock = clock
        self.history = collections.OrderedDict()

    def check(self, owner, refresh):
        """Raise QuotaError when `owner` may not send an Atlas query (a refresh when `refresh`) now."""
        per_hour, refreshes, gap, refresh_gap = self.limits()
        now = self.clock()
        entries = [entry for entry in self.history.get(owner, ()) if now-entry[0] < 3600]
        def minutes(seconds):
            return f'{max(1, math.ceil(seconds/60))} 分钟'
        if len(entries) >= per_hour:
            raise QuotaError(f'你本小时的 Atlas 查询次数（{per_hour} 次）已用完，约 {minutes(3600-(now-entries[-per_hour][0]))}后再试；'
                             '已经查过的玩家在缓存时间内可以继续查看')
        if entries and now-entries[-1][0] < gap:
            raise QuotaError(f'查询太快了，请 {math.ceil(gap-(now-entries[-1][0]))} 秒后再试')
        if refresh:
            done = [entry for entry in entries if entry[1]]
            if refreshes <= 0:
                raise QuotaError('管理员关闭了刷新，请去掉“刷新”查询（会显示缓存的数据）')
            if len(done) >= refreshes:
                raise QuotaError(f'你本小时的刷新次数（{refreshes} 次）已用完，约 {minutes(3600-(now-done[-refreshes][0]))}后再试；'
                                 '去掉“刷新”可以查看缓存的数据')
            if done and now-done[-1][0] < refresh_gap:
                raise QuotaError(f'刷新太频繁了，请约 {minutes(refresh_gap-(now-done[-1][0]))}后再试；去掉“刷新”可以查看缓存的数据')

    def record(self, owner, refresh):
        now = self.clock()
        entries = [entry for entry in self.history.pop(owner, ()) if now-entry[0] < 3600]
        entry = [now, bool(refresh)]
        entries.append(entry)
        self.history[owner] = entries
        while len(self.history) > QUOTA_USERS:
            self.history.popitem(last=False)
        return entry

    def forget(self, owner, entry):
        """Take back one recorded query (the request was refused before it was sent)."""
        entries = self.history.get(owner, [])
        for i, candidate in enumerate(entries):
            if candidate is entry:
                del entries[i]
                break

    def ticket(self, owner, refresh):
        """A callable for AtlasPlayers' `charge`: checks and records once, the first time a request is needed.
        `charge.refund()` gives the query back when no request was sent (the plugin's budget or the shared gate);
        after `charge.keep()` (a request of the query reached Atlas) it no longer does."""
        state = {'entry': None, 'kept': False}
        def charge():
            if state['entry'] is None:
                self.check(owner, refresh)
                state['entry'] = self.record(owner, refresh)
        def give_back():
            if state['entry'] is not None and not state['kept']:
                self.forget(owner, state['entry'])
                state['entry'] = None
        def keep_it():
            if state['entry'] is not None:
                state['kept'] = True
        charge.refund, charge.keep = give_back, keep_it
        return charge
