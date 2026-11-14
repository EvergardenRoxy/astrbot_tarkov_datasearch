"""Anonymous Atlas HTTP access: three fixed requests, no startup requests, no automatic retries."""
import asyncio
import datetime
import email.utils
import json
import math
import re
import time
from contextlib import nullcontext

import aiohttp

from .atlas_cooldown import AtlasAccessPaused, AtlasCooldown, AtlasDeadlineError, AtlasNotSent
from .transport import ConnectionFailure, HTTPError, QueryError, USER_AGENT, decoded_chunks, validate_proxy

HOST = 'https://www.eftforge.com'
LIMITS = (2*1024*1024, 8*1024*1024)
# A player document was 9-24 KB decoded (2026-11-08); a search answer 132 bytes.
PLAYER_LIMITS = (1024*1024, 4*1024*1024)
ERROR_LIMIT = (64*1024, 64*1024)
MATCHING_PATH = '/atlas/api/matching'
LOOKUP_PATH = '/atlas/api/lookup'
SEARCH_PATH = '/atlas/api/lookup/search'
# Atlas' nickname rule (public EFTForge code): longer generated names cannot be searched there.
NICKNAME = re.compile(r'[A-Za-z0-9_.-]{3,15}')
# Longest wait for the shared gate in a player query. A nickname query is a search and, one shared interval later,
# a lookup, so the wait is never shorter than the interval plus the margin.
PLAYER_WAIT_SECONDS = 20
PLAYER_WAIT_MARGIN = 10
# After a 401/403 Atlas is left alone this long, then asked again (maintainer: rare, no manual reset).
REFUSED_SECONDS = 30*60
# Error codes of the public EFTForge relay; anything else is reported as 'http'.
ERROR_CODES = ('busy', 'rate', 'session', 'backend', 'not_found', 'no_profile', 'mode', 'bad_account', 'bad_nickname',
               'lookup', 'unavailable')


class AtlasDataError(QueryError):
    def __init__(self):
        super().__init__('Atlas 返回的数据格式无法识别')


class AtlasHTTPError(HTTPError):
    def __init__(self, status, code='http', retry_after=None):
        message = (f'Atlas 拒绝了访问（HTTP {status}），{REFUSED_SECONDS//60} 分钟后自动重试'
                   if status in (401, 403) else f'Atlas 查询失败（HTTP {status}），请稍后再试')
        super().__init__(message, status, retry_after)
        self.code = code


def retry_after(value):
    """Seconds of a Retry-After header (a number or an HTTP date); 60 when it is missing or unusable. An upstream
    pause is never shortened: a number too long to mean anything is "do not ask again" (infinity)."""
    try:
        if value and value.isascii() and value.isdigit():
            return int(value) if len(value) < 19 else math.inf
        date = email.utils.parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=datetime.timezone.utc)
        return max(0, date.timestamp()-time.time())
    except (ValueError, TypeError, OverflowError):
        return 60


def not_json(constant):
    """NaN and Infinity are not JSON: a document holding them is refused rather than drawn."""
    raise ValueError(constant)


async def read_json(response, limits):
    try:
        body = b''.join([chunk async for chunk in decoded_chunks(response, limits)])
        return await asyncio.to_thread(lambda: json.loads(body, parse_constant=not_json))
    except (QueryError, ValueError, RecursionError):
        raise AtlasDataError() from None


async def error_code(response):
    """The relay's error word of a refused request, or 'http'."""
    try:
        error = await read_json(response, ERROR_LIMIT)
    except AtlasDataError:
        return 'http'
    detail = error.get('detail', error) if isinstance(error, dict) else None
    code = detail.get('error') if isinstance(detail, dict) else None
    return code if code in ERROR_CODES else 'http'


class AtlasClient:
    def __init__(self, client, *, cooldown=None, proxy=lambda: None, budget=None):
        self.http = client
        self.cooldown = cooldown if cooldown is not None else AtlasCooldown()
        self.proxy = proxy
        # The minute/hour budget of player requests (matching is not counted: Atlas limits player lookups).
        self.budget = budget
        # scope ('players' or 'matching') -> when a 401/403 pause ends (the cooldown's clock)
        self.refused_until = {}
        self.closed = False
        self.tasks = set()

    def validated_proxy(self):
        value = self.proxy()
        return validate_proxy(value) if value else None

    def refused(self, scope):
        """Seconds left of the pause after a 401/403 in `scope`, 0 when there is none."""
        return max(0, self.refused_until.get(scope, 0)-self.cooldown.clock())

    def player_wait(self):
        return max(PLAYER_WAIT_SECONDS, self.cooldown.interval+PLAYER_WAIT_MARGIN)

    async def matching(self):
        return await self._get('matching', MATCHING_PATH, {'hours': '24'}, limits=LIMITS)

    async def lookup(self, account_id, mode, *, deadline=None, include_art=True):
        """One player document (`mode` is pvp, pve, season or arena)."""
        params = {'accountId': str(int(account_id)), 'mode': mode}
        if not include_art:
            params['art'] = '0'
        return await self._get('players', LOOKUP_PATH, params, max_wait=self.player_wait(), deadline=deadline)

    async def search(self, nickname, mode, *, deadline=None, live=False):
        """Atlas' nickname search: {nickname, players: [{accountId, nickname, side, level}], cached, mode}."""
        if not NICKNAME.fullmatch(nickname):
            raise AtlasNotSent('Atlas 只能搜索 3～15 个字符的昵称（字母、数字、_ -），更长的名字请用账号 ID 或玩家页网址查询')
        params = {'nickname': nickname, 'mode': mode}
        if live:
            params['live'] = '1'
        return await self._get('players', SEARCH_PATH, params, max_wait=self.player_wait(), deadline=deadline)

    async def _get(self, scope, path, params, *, limits=PLAYER_LIMITS, max_wait=None, deadline=None):
        """Admission, then one request. `max_wait`: seconds to wait for the shared gate (None: do not wait);
        `deadline` (the cooldown's clock): a background request gives up, unsent, when it passes."""
        if self.closed:
            raise AtlasNotSent('插件正在关闭')
        try:
            proxy = self.validated_proxy()
        except QueryError as exc:
            raise AtlasNotSent(str(exc)) from None
        if self.refused(scope):
            raise AtlasAccessPaused(self.refused(scope))
        counted = scope == 'players' and self.budget is not None
        if counted:
            self.budget.check()
        remaining = None if deadline is None else deadline-self.cooldown.clock()
        if remaining is not None and remaining <= 0:
            raise AtlasDeadlineError()
        gate = self.cooldown.claim() if max_wait is None else self.cooldown.claim_within(max_wait, deadline=deadline)
        sent = False
        try:
            # The window also cancels a background request still in flight when its night ends.
            async with asyncio.timeout(remaining) if remaining is not None else nullcontext():
                async with gate:
                    if self.refused(scope):
                        # Refused while this request waited for its turn.
                        raise AtlasAccessPaused(self.refused(scope))
                    # Counted only now, so the windows hold request starts, not time spent waiting.
                    token = self.budget.reserve() if counted else None
                    sent = True
                    return await self._send(scope, path, params, proxy, limits, token)
        except TimeoutError:
            if not sent:
                raise AtlasDeadlineError() from None
            raise

    async def _send(self, scope, path, params, proxy, limits, token):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            timeout = aiohttp.ClientTimeout(total=30, connect=15)
            headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json', 'Accept-Encoding': 'gzip'}
            async with self.http.get_session().get(HOST+path, params=params, allow_redirects=False, proxy=proxy,
                                                   timeout=timeout, headers=headers) as response:
                status = response.status
                if status == 200:
                    if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                        raise AtlasDataError()
                    return await read_json(response, limits)
                pause = None
                if status in (401, 403):
                    self.refused_until[scope] = self.cooldown.clock()+REFUSED_SECONDS
                elif status == 429 or (status >= 500 and 'Retry-After' in response.headers):
                    pause = retry_after(response.headers.get('Retry-After'))
                    self.cooldown.pause(pause)
                if status == 429 and token is not None:
                    # Atlas does not count refused requests ("busy" or "rate") against the IP limit.
                    self.budget.refund(token)
                raise AtlasHTTPError(status, await error_code(response), pause)
        except (aiohttp.ClientError, TimeoutError) as exc:
            # Never include URLs, proxy credentials, or raw upstream text.
            raise ConnectionFailure(f'连接 Atlas 失败（{type(exc).__name__}），请稍后再试') from None
        finally:
            self.tasks.discard(task)

    async def close(self):
        self.closed = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
