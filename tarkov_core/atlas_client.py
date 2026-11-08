"""Bounded, anonymous Atlas HTTP access. No startup requests or automatic retries."""
import asyncio
import datetime
import email.utils
import json
import math
import re
import time
import zlib

import aiohttp

from .atlas_cooldown import AtlasCooldown
from .transport import ConnectionFailure, HTTPClient, HTTPError, QueryError, USER_AGENT, validate_proxy

HOST = 'https://www.eftforge.com'
LIMITS = (2*1024*1024, 8*1024*1024)
# A player document was 9-24 KB decoded (2026-11-08); a search answer 132 bytes.
PLAYER_LIMITS = (1024*1024, 4*1024*1024)
ERROR_LIMIT = 64*1024
MATCHING_PATH = '/atlas/api/matching'
LOOKUP_PATH = '/atlas/api/lookup'
SEARCH_PATH = '/atlas/api/lookup/search'
# Atlas modes (author, 2026-11-08); the plugin's mode keys are the same words.
ATLAS_MODES = ('pvp', 'pve', 'season', 'arena')
ACCOUNT = re.compile(r'[1-9][0-9]{0,11}')
# Atlas' nickname rule (public EFTForge code): longer generated names cannot be searched there.
NICKNAME = re.compile(r'[A-Za-z0-9_.-]{3,15}')
# Longest wait for the shared gate in a player query (a lookup right after a search, a busy retry).
PLAYER_WAIT_SECONDS = 20
# Error codes of the public EFTForge relay; anything else is reported as 'http'.
ERROR_CODES = ('busy', 'rate', 'session', 'backend', 'not_found', 'no_profile', 'mode', 'bad_account', 'bad_nickname',
               'lookup', 'unavailable')


class AtlasDataError(QueryError):
    def __init__(self):
        super().__init__('Atlas 返回的数据格式无法识别')


class AtlasHTTPError(HTTPError):
    def __init__(self, status, code='http', retry_after=None):
        super().__init__(f'Atlas 查询失败（HTTP {status}），请稍后再试', status, retry_after)
        self.code = code


def retry_after(value, *, now=None):
    """Do not shorten upstream pauses; huge positive values fail closed."""
    try:
        if not value:
            return 60
        if value.isascii() and value.isdigit():
            # Avoid Python's integer string length limit and float overflow.
            return float(value) if len(value) < 309 else math.inf
        date = email.utils.parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=datetime.timezone.utc)
        return max(0, date.timestamp()-(time.time() if now is None else now))
    except (ValueError, TypeError, OverflowError):
        return 60


def decode_json(body):
    def invalid(value):
        raise ValueError('non-finite JSON')
    try:
        return json.loads(body, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError):
        raise AtlasDataError() from None


async def read_body(response, limits):
    encoding = response.headers.get('Content-Encoding', '').strip().lower()
    if encoding not in ('', 'identity', 'gzip'):
        raise AtlasDataError()
    decoder = zlib.decompressobj(16+zlib.MAX_WBITS) if encoding == 'gzip' else None
    body = bytearray()
    received = 0
    try:
        async for chunk in response.content.iter_chunked(65536):
            received += len(chunk)
            if received > limits[0]:
                raise AtlasDataError()
            data = decoder.decompress(chunk, limits[1]-len(body)+1) if decoder else chunk
            body.extend(data)
            if len(body) > limits[1] or (decoder and decoder.unconsumed_tail):
                raise AtlasDataError()
        if not body or (decoder and (not decoder.eof or decoder.unused_data)):
            raise AtlasDataError()
    except zlib.error:
        raise AtlasDataError() from None
    return bytes(body)


def allowed(path, params):
    """The only requests this client sends: fixed paths, validated parameters, nothing a user or a response chose."""
    if path == MATCHING_PATH:
        return params == {'hours': '24'}
    if not isinstance(params, dict) or params.get('mode') not in ATLAS_MODES:
        return False
    if path == LOOKUP_PATH:
        return set(params) == {'accountId', 'mode'} and isinstance(params['accountId'], str) and bool(ACCOUNT.fullmatch(params['accountId']))
    if path == SEARCH_PATH:
        return set(params) == {'nickname', 'mode'} and isinstance(params['nickname'], str) and bool(NICKNAME.fullmatch(params['nickname']))
    return False


class AtlasClient:
    def __init__(self, *, client=None, cooldown=None, proxy=lambda: None, budget=None):
        self.http = client if client is not None else HTTPClient()
        self.owns_http = client is None
        self.cooldown = cooldown if cooldown is not None else AtlasCooldown()
        self.proxy = proxy
        # The minute/hour budget of player requests (matching is not counted: Atlas limits player lookups).
        self.budget = budget
        self.closed = False
        self.tasks = set()

    def validated_proxy(self):
        value = self.proxy()
        return validate_proxy(value) if value else None

    async def matching(self):
        return await self._get(MATCHING_PATH, {'hours': '24'})

    async def lookup(self, account_id, mode, *, max_wait=PLAYER_WAIT_SECONDS):
        """One player document (`mode` is pvp, pve, season or arena). Waits at most `max_wait` for the shared gate."""
        return await self._get(LOOKUP_PATH, {'accountId': str(account_id), 'mode': mode}, max_wait=max_wait,
                               limits=PLAYER_LIMITS, counted=True)

    async def search(self, nickname, mode, *, max_wait=PLAYER_WAIT_SECONDS):
        """Atlas' nickname search: {nickname, players: [{accountId, nickname, side, level}], cached, mode}."""
        return await self._get(SEARCH_PATH, {'nickname': nickname, 'mode': mode}, max_wait=max_wait,
                               limits=PLAYER_LIMITS, counted=True)

    async def _get(self, path, params, *, max_wait=None, limits=LIMITS, counted=False):
        if self.closed:
            raise RuntimeError('Atlas client closed')
        if not allowed(path, params):
            raise ValueError('unsupported Atlas request')
        proxy = self.validated_proxy()  # Invalid configuration fails before admission.
        task = asyncio.current_task()
        gate = self.cooldown.claim() if max_wait is None else self.cooldown.claim_within(max_wait)
        # Fail fast during a local budget pause. Reserve only after admission so sliding windows count HTTP
        # request starts rather than time spent waiting; recheck then because other waiters may have used slots.
        if counted and self.budget is not None:
            self.budget.check()
        async with gate:
            token = self.budget.reserve() if counted and self.budget is not None else None
            return await self._send(path, params, proxy, limits, token, task)

    async def _send(self, path, params, proxy, limits, token, task):
        self.tasks.add(task)
        try:
            session = self.http.get_session()
            timeout = aiohttp.ClientTimeout(total=30, connect=15)
            async with session.get(HOST+path, params=params, allow_redirects=False, proxy=proxy,
                                   timeout=timeout, headers={'User-Agent': USER_AGENT,
                                   'Accept': 'application/json', 'Accept-Encoding': 'gzip'}) as response:
                status = response.status
                pause = None
                if status == 429 or (500 <= status <= 599 and 'Retry-After' in response.headers):
                    pause = retry_after(response.headers.get('Retry-After'))
                    self.cooldown.pause(pause)
                if status == 429 and token is not None:
                    # Atlas does not count refused requests ("busy" or "rate") against the IP limit.
                    self.budget.refund(token)
                if status != 200:
                    code = 'http'
                    try:
                        error = decode_json(await read_body(response, (ERROR_LIMIT, ERROR_LIMIT)))
                        if isinstance(error, dict):
                            detail = error.get('detail', error)
                            candidate = detail.get('error') if isinstance(detail, dict) else None
                            if candidate in ERROR_CODES:
                                code = candidate
                    except AtlasDataError:
                        pass
                    raise AtlasHTTPError(status, code, pause)
                if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                    raise AtlasDataError()
                return await asyncio.to_thread(decode_json, await read_body(response, limits))
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
        if self.owns_http:
            await self.http.close()
