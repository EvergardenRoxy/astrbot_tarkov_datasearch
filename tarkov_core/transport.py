"""The plugin's HTTP reading: fixed hosts, no redirects, bounded size and time. Also players.tarkov.dev."""
import asyncio
import datetime
import email.utils
import io
import json
import time
import zlib

import aiohttp
from yarl import URL

from astrbot.api import logger

from . import VERSION

HOST = 'https://players.tarkov.dev'
# Cloudflare in front of the bucket blocks generic clients (curl's and browser-like agents got a 403 "you have been
# blocked" page; probed 2026-10-13) but serves a client that names itself. Identify the plugin honestly.
USER_AGENT = f'astrbot_tarkov_datasearch/{VERSION} (+https://github.com/EvergardenRoxy/astrbot_tarkov_datasearch)'

# A profile is 3-35 KB; the regular-mode name index is ~30 MB gzip, ~67 MB decoded (2026-10-13).
PROFILE_LIMITS = (4*1024*1024, 16*1024*1024)
# Index limits are about three times the 2026-10-13 sizes; the whole decoded index is read into memory to build it.
INDEX_LIMITS = (64*1024*1024, 192*1024*1024)

BLOCKED = ('tarkov.dev 拒绝了这台服务器的访问（HTTP 403，Cloudflare 拦截）。'
           '可以在插件配置中开启“访问数据接口时使用代理”后重试')


class QueryError(Exception):
    """A failure whose message is safe and meant for the chat."""


class ConnectionFailure(QueryError):
    """A transport failure, eligible for an explicitly labelled cached fallback."""


class NotFound(QueryError):
    def __init__(self, message='tarkov.dev 上没有这份数据'):
        super().__init__(message)


class HTTPError(QueryError):
    """A refused request, retaining status and Retry-After without exposing connection details."""
    def __init__(self, message, status, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def retry_seconds(value):
    if not value:
        return None
    try:
        if value.isascii() and value.isdigit():
            return min(86400, int(value))
        deadline = email.utils.parsedate_to_datetime(value)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=datetime.timezone.utc)
        return min(86400, max(0, deadline.timestamp()-time.time()))
    except (ValueError, TypeError, OverflowError):
        return None


def validate_proxy(value):
    try:
        url = URL(value)
        if url.scheme not in ('http', 'https') or not url.host or url.explicit_port is None or url.path not in ('', '/') or url.query_string or url.fragment:
            raise ValueError()
    except (ValueError, TypeError):
        raise QueryError('代理地址无效；请填写 HTTP/HTTPS 代理地址，不支持 SOCKS 或路径参数') from None
    return str(url)


class HTTPClient:
    """Lazy plugin-owned connection pool; never uses ambient proxies or cookies."""
    def __init__(self):
        self.session = None
        self.closed = False

    def get_session(self):
        if self.closed:
            raise RuntimeError('HTTP client closed')
        if self.session is None:
            self.session = aiohttp.ClientSession(auto_decompress=False, trust_env=False,
                                                cookie_jar=aiohttp.DummyCookieJar())
        return self.session

    async def close(self):
        self.closed = True
        if self.session is not None:
            await self.session.close()


async def decoded_chunks(response, limits):
    """The response body in pieces, gunzipped on the fly, refusing more than `limits` (compressed, decoded) bytes."""
    compressed_limit, decoded_limit = limits
    encoding = response.headers.get('Content-Encoding', '').strip().lower()
    if encoding not in ('', 'identity', 'gzip'):
        raise QueryError('服务器使用了不支持的压缩格式')
    decoder = zlib.decompressobj(16+zlib.MAX_WBITS) if encoding == 'gzip' else None
    received = written = 0
    try:
        async for chunk in response.content.iter_chunked(65536):
            received += len(chunk)
            data = decoder.decompress(chunk, decoded_limit-written+1) if decoder else chunk
            written += len(data)
            if received > compressed_limit or written > decoded_limit or (decoder and decoder.unconsumed_tail):
                raise QueryError('服务器返回的数据超过大小上限')
            yield data
    except zlib.error:
        raise QueryError('服务器返回的数据无法解压，请稍后再试') from None
    if not written or (decoder and (not decoder.eof or decoder.unused_data)):
        raise QueryError('服务器返回的数据不完整，请稍后再试')


async def http_get(host, path, sink, *, client, limits, timeout_seconds, proxy=None, etag=None, last_modified=None,
                   content_types=('application/json',)):
    """GET host+path into the binary file-like `sink`. Returns {'status': 200 or 304, 'etag', 'last_modified',
    'cache_control'}; 304 only answers a request that sent `etag` or `last_modified`.
    Raises NotFound for 404, HTTPError for other refusals, QueryError for unusable bodies, TimeoutError."""
    proxy = validate_proxy(proxy) if proxy else None
    headers = {'User-Agent': USER_AGENT, 'Accept': ', '.join(content_types), 'Accept-Encoding': 'gzip'}
    if etag:
        headers['If-None-Match'] = etag
    elif last_modified:
        headers['If-Modified-Since'] = last_modified
    started = time.monotonic()
    timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=min(30, timeout_seconds))
    try:
        async with client.get_session().get(host+path, allow_redirects=False, proxy=proxy, headers=headers,
                                            timeout=timeout) as response:
            metadata = {'status': response.status, 'etag': response.headers.get('ETag') or etag,
                        'last_modified': response.headers.get('Last-Modified') or last_modified,
                        'cache_control': response.headers.get('Cache-Control')}
            if response.status == 304 and (etag or last_modified):
                return metadata
            if response.status == 404:
                raise NotFound()
            if response.status == 403:
                raise HTTPError(BLOCKED, 403)
            if response.status != 200:
                message = ('tarkov.dev 暂时限制了访问频率（HTTP 429），请过几分钟再试' if response.status == 429
                           else f'tarkov.dev 返回 HTTP {response.status}，请稍后再试')
                raise HTTPError(message, response.status, retry_seconds(response.headers.get('Retry-After')))
            if response.headers.get('Content-Type', '').split(';')[0].strip().lower() not in content_types:
                raise QueryError('tarkov.dev 返回的不是数据文件，请稍后再试')
            async for data in decoded_chunks(response, limits):
                sink.write(data)
            return metadata
    except TimeoutError as exc:
        detail = f'耗时={time.monotonic()-started:.1f}秒'
        logger.warning('Tarkov download timeout: %s', detail)
        raise TimeoutError(detail) from exc
    except aiohttp.ClientError as exc:
        # aiohttp messages can include the proxy URL (with credentials); show the type only.
        raise ConnectionFailure(f'连接 tarkov.dev 失败（{type(exc).__name__}），请稍后再试') from None


async def fetch_profile(mode_path, aid, *, client, timeout_seconds=30, proxy=None):
    """The profile JSON of account `aid` in a mode. Raises NotFound when the mode has no profile for it."""
    buffer = io.BytesIO()
    await http_get(HOST, f'/{mode_path}/{int(aid)}.json', buffer, limits=PROFILE_LIMITS, timeout_seconds=timeout_seconds, proxy=proxy, client=client)
    try:
        # Off the event loop: the decoded limit allows a 16 MiB document.
        data = await asyncio.to_thread(json.loads, buffer.getvalue())
    except (ValueError, UnicodeDecodeError):
        raise QueryError('tarkov.dev 返回的档案无法解析') from None
    if not isinstance(data, dict):
        raise QueryError('tarkov.dev 返回的档案格式无法识别')
    return data


async def fetch_index(mode_path, target, *, client, etag=None, timeout_seconds=120, proxy=None):
    """Download a mode's name index (JSON object account id -> nickname) to the file `target`."""
    with open(target, 'wb') as sink:
        return await http_get(HOST, f'/{mode_path}/index.json', sink, limits=INDEX_LIMITS, timeout_seconds=timeout_seconds,
                         proxy=proxy, etag=etag, client=client)
