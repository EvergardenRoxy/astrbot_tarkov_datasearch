"""Bounded, anonymous Atlas HTTP access. No startup requests or automatic retries."""
import asyncio
import datetime
import email.utils
import json
import math
import time
import zlib

import aiohttp

from .atlas_cooldown import AtlasCooldown
from .transport import ConnectionFailure, HTTPClient, HTTPError, QueryError, USER_AGENT, validate_proxy

HOST = 'https://www.eftforge.com'
LIMITS = (2*1024*1024, 8*1024*1024)
ERROR_LIMIT = 64*1024


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


class AtlasClient:
    def __init__(self, *, client=None, cooldown=None, proxy=lambda: None):
        self.http = client if client is not None else HTTPClient()
        self.owns_http = client is None
        self.cooldown = cooldown if cooldown is not None else AtlasCooldown()
        self.proxy = proxy
        self.closed = False
        self.tasks = set()

    def validated_proxy(self):
        value = self.proxy()
        return validate_proxy(value) if value else None

    async def matching(self):
        # Only this fixed path is exposed. Future lookup methods must use _get too.
        return await self._get('/atlas/api/matching', {'hours': '24'})

    async def _get(self, path, params):
        if self.closed:
            raise RuntimeError('Atlas client closed')
        if path != '/atlas/api/matching' or params != {'hours': '24'}:
            raise ValueError('unsupported Atlas request')
        proxy = self.validated_proxy()  # Invalid configuration fails before admission.
        task = asyncio.current_task()
        async with self.cooldown.claim():
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
                    if status != 200:
                        code = 'http'
                        try:
                            error = decode_json(await read_body(response, (ERROR_LIMIT, ERROR_LIMIT)))
                            if isinstance(error, dict):
                                detail = error.get('detail', error)
                                candidate = detail.get('error') if isinstance(detail, dict) else None
                                if candidate in ('busy', 'rate', 'session', 'backend', 'not_found', 'no_profile',
                                                 'mode', 'bad_account', 'bad_nickname', 'lookup', 'unavailable'):
                                    code = candidate
                        except AtlasDataError:
                            pass
                        raise AtlasHTTPError(status, code, pause)
                    if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                        raise AtlasDataError()
                    return await asyncio.to_thread(decode_json, await read_body(response, LIMITS))
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
