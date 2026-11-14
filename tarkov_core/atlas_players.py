"""Atlas player lookups for the chat commands: local cache, shared requests, one busy retry, per-user quotas.

Every HTTP request goes through the plugin's single AtlasClient, so the shared start interval, the upstream
Retry-After pause and the minute/hour budget (atlas_cooldown.py) apply to all of them. Nothing here polls or
prefetches; requests happen for a user's command. The one background caller is the weekly report's nightly sampler
(weekly_tasks.py), which paces itself and goes through this same client.
"""
import asyncio
import collections
import math
import time

from .atlas_client import AtlasDataError, AtlasHTTPError
from .atlas_cooldown import AtlasAccessPaused, AtlasBudgetError, AtlasCooldownError, AtlasNotSent
from .atlas_profile import AtlasProfileError, search_results, summarize
from .modes import label_of
from .transport import ConnectionFailure, NotFound, QueryError

# Atlas keeps a result for 60 seconds itself; a shorter local cache would only cost requests.
MIN_CACHE_SECONDS = 60
# "No profile" / "no such account" is remembered this long.
NOT_FOUND_SECONDS = 60
CACHE_ENTRIES = 256
MAX_PENDING = 32
# Users whose quota record is kept at most (oldest forgotten first).
QUOTA_USERS = 10000
# Refusals before any request reaches Atlas (the plugin's budget, the shared gate, a 401/403 pause): the user's
# quota is not counted.
NOT_SENT = (AtlasBudgetError, AtlasCooldownError, AtlasAccessPaused, AtlasNotSent)


class AtlasNotFound(NotFound):
    """Atlas answered: no such account (`no_account`), or no profile in this mode. Never used for a failed request."""
    no_account = False


class AtlasNoAccount(AtlasNotFound):
    no_account = True


class QuotaError(QueryError):
    """A user's own hourly quota or cooldown (no request was sent)."""


def failure_text(exc):
    """Why an Atlas request failed, for the chat (never raw upstream text)."""
    if isinstance(exc, AtlasHTTPError):
        if exc.status in (401, 403):
            return 'Atlas 拒绝了访问，稍后会自动重试'
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
    return isinstance(exc, (AtlasAccessPaused, AtlasCooldownError, ConnectionFailure, AtlasDataError,
                            AtlasProfileError, TimeoutError))


class AtlasPlayers:
    def __init__(self, client, *, cache_seconds=lambda: 600, clock=time.monotonic, wall_clock=time.time):
        self.client = client
        self.cache_seconds = cache_seconds
        self.clock, self.wall_clock = clock, wall_clock
        # (mode, aid, include_art) -> (monotonic time, summary or None for "no profile")
        self.cache = {}
        self.pending = {}

    def ttl(self):
        return max(MIN_CACHE_SECONDS, self.cache_seconds())

    def cached(self, mode, aid, *, include_art=True):
        """The cached summary, AtlasNotFound when "no profile" is remembered, or None (a request is needed)."""
        entry = self.cache.get((mode, aid, include_art))
        if entry is None:
            return None
        age = self.clock()-entry[0]
        if not 0 <= age < (self.ttl() if entry[1] is not None else min(self.ttl(), NOT_FOUND_SECONDS)):
            return None
        if entry[1] is None:
            return AtlasNotFound(f'账号 ID {aid} 在 {label_of(mode)} 模式下没有档案（Atlas）')
        return dict(entry[1], from_cache=True, cache_age=age)

    async def profile(self, mode, aid, *, refresh=False, ticket=None, include_art=True):
        """One summary. `refresh` skips the local cache (Atlas may still answer from its own 60-second cache).
        `ticket` (UserQuota.ticket) is checked before a request is needed and counted once one was sent; queries
        for the same profile at the same time share one request, counted for the first of them."""
        if not refresh:
            hit = self.cached(mode, aid, include_art=include_art)
            if isinstance(hit, AtlasNotFound):
                raise hit
            if hit is not None:
                return hit
        key = (mode, aid, include_art)
        task = self.pending.get(key)
        if task is None:
            if len(self.pending) >= MAX_PENDING:
                raise QueryError('玩家查询请求较多，请稍后再试')
            if ticket:
                ticket.check()
            task = asyncio.ensure_future(self._fetch(mode, aid, include_art=include_art, ticket=ticket))
            self.pending[key] = task
            task.add_done_callback(lambda done: (self.pending.pop(key, None),
                                                 done.cancelled() or done.exception()))
        # Shielded: one user giving up must not cancel the request others wait for.
        return dict(await asyncio.shield(task))

    async def profile_once(self, mode, aid, *, deadline=None, include_art=False):
        """One background lookup for the nightly sampler: no busy retry, no user quota, gives up at `deadline`."""
        return await self._fetch(mode, aid, retry=False, deadline=deadline, include_art=include_art)

    async def _fetch(self, mode, aid, *, retry=True, deadline=None, include_art=True, ticket=None):
        try:
            doc = await self._request(lambda: self.client.lookup(aid, mode, deadline=deadline, include_art=include_art),
                                      retry, ticket)
        except AtlasHTTPError as exc:
            if exc.code == 'not_found':
                raise AtlasNoAccount(f'Atlas 找不到账号 ID {aid}') from None
            if exc.code == 'no_profile':
                # Remembered briefly, so repeated queries for a missing profile cost one request a minute.
                self._remember((mode, aid, include_art), None)
                raise AtlasNotFound(f'账号 ID {aid} 在 {label_of(mode)} 模式下没有档案（Atlas）') from None
            raise
        if not isinstance(doc, dict) or doc.get('mode') not in (None, mode):
            raise AtlasProfileError()
        summary = summarize(doc, mode)
        # The raw document: the Atlas picture and the weekly sample read it.
        summary['doc'] = doc
        summary['fetched'] = self.wall_clock()
        self._remember((mode, aid, include_art), summary)
        return summary

    async def search(self, nickname, mode, *, ticket=None, live=False):
        """[(aid, nickname)] from Atlas' nickname search (one request; not cached)."""
        if ticket:
            ticket.check()
        return await self._search(nickname, mode, True, ticket, None, live)

    async def search_once(self, nickname, mode, *, deadline=None):
        """One background name search for the nightly sampler."""
        return await self._search(nickname, mode, False, None, deadline, False)

    async def _search(self, nickname, mode, retry, ticket, deadline, live):
        try:
            doc = await self._request(lambda: self.client.search(nickname, mode, deadline=deadline, live=live),
                                      retry, ticket)
        except AtlasHTTPError as exc:
            if exc.code in ('not_found', 'no_profile'):
                return []
            raise
        return search_results(doc)

    @staticmethod
    async def _request(request, retry, ticket):
        """Run `request`, once more after a `busy` answer when `retry` (Atlas' eight shared lookup slots were full,
        not our limit; the client's gate waits for the pause it recorded). Counts the user's quota as soon as a
        request reached Atlas, whatever came back."""
        sent = ticket.sent if ticket else lambda: None
        try:
            try:
                result = await request()
            except AtlasHTTPError as exc:
                if not retry or exc.code != 'busy':
                    raise
                sent()
                result = await request()
        except (*NOT_SENT, asyncio.CancelledError):
            raise
        except Exception:
            sent()
            raise
        sent()
        return result

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


class Ticket:
    """One command's use of a user's quota: checked before each request it needs, counted once."""
    def __init__(self, quota, owner, refresh):
        self.quota, self.owner, self.refresh = quota, owner, refresh
        self.counted = False

    def check(self):
        if not self.counted:
            self.quota.check(self.owner, self.refresh)

    def sent(self):
        if not self.counted:
            self.counted = True
            self.quota.record(self.owner, self.refresh)


class UserQuota:
    """Per-user hourly query and refresh counts with minimum intervals, in memory (keyed by the plugin's HMAC of the
    user). Only queries that sent an Atlas request are counted; cached answers and locally refused ones are free."""

    def __init__(self, limits, *, clock=time.monotonic):
        # limits() -> (queries per hour, refreshes per hour, seconds between queries, seconds between refreshes)
        self.limits = limits
        self.clock = clock
        # owner -> [(time, was a refresh)] of the last hour
        self.used = collections.OrderedDict()

    def ticket(self, owner, refresh):
        return Ticket(self, owner, refresh)

    def recent(self, owner):
        now = self.clock()
        return [entry for entry in self.used.get(owner, ()) if now-entry[0] < 3600]

    def check(self, owner, refresh):
        """Raise QuotaError when `owner` may not send an Atlas query (a refresh when `refresh`) now."""
        per_hour, refreshes, gap, refresh_gap = self.limits()
        now = self.clock()
        entries = self.recent(owner)

        def minutes(seconds):
            return f'{max(1, math.ceil(seconds/60))} 分钟'
        if len(entries) >= per_hour:
            raise QuotaError(f'你本小时的 Atlas 查询次数（{per_hour} 次）已用完，约 {minutes(3600-(now-entries[-per_hour][0]))}后再试；'
                             '已经查过的玩家在缓存时间内可以继续查看')
        if entries and now-entries[-1][0] < gap:
            raise QuotaError(f'查询太快了，请 {math.ceil(gap-(now-entries[-1][0]))} 秒后再试')
        if refresh:
            done = [entry for entry in entries if entry[1]]
            if len(done) >= refreshes:
                raise QuotaError(f'你本小时的刷新次数（{refreshes} 次）已用完，约 {minutes(3600-(now-done[-refreshes][0]))}后再试；'
                                 '去掉“刷新”可以查看缓存的数据')
            if done and now-done[-1][0] < refresh_gap:
                raise QuotaError(f'刷新太频繁了，请约 {minutes(refresh_gap-(now-done[-1][0]))}后再试；去掉“刷新”可以查看缓存的数据')

    def record(self, owner, refresh):
        entries = self.recent(owner)
        self.used.pop(owner, None)
        self.used[owner] = [*entries, (self.clock(), bool(refresh))]
        while len(self.used) > QUOTA_USERS:
            self.used.popitem(last=False)
