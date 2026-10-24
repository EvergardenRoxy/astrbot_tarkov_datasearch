"""Lazy SQLite cache for public JSON resources. No poller and no network work at startup."""
import asyncio
import math
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, replace

from . import public_api
from .transport import ConnectionFailure, HTTPError, NotFound, QueryError

MAX_ENTRIES = 32
MAX_BYTES = 128*1024*1024
MAX_PENDING = 32


def cache_lifetime(header, configured):
    directives = [part.strip().lower() for part in (header or '').split(',')]
    if 'no-cache' in directives or 'no-store' in directives:
        return 0
    for part in directives:
        name, _, value = part.partition('=')
        if name == 'max-age' and value.strip('"').isascii() and value.strip('"').isdigit():
            return min(configured, int(value.strip('"')))
    return configured


@dataclass(frozen=True)
class CachedResource:
    key: public_api.ResourceKey
    body: bytes
    etag: str | None
    last_modified: str | None
    cache_control: str | None
    fetched_at: float
    checked_at: float
    from_cache: bool = True
    stale: bool = False
    error: str | None = None

    async def json(self):
        # Each reader gets its own mutable document. Shared in-flight results contain immutable bytes only.
        return await asyncio.to_thread(public_api.decode, self.key, self.body)


class ResourceCache:
    def __init__(self, root, download=public_api.fetch, *, max_entries=MAX_ENTRIES, max_bytes=MAX_BYTES):
        if max_entries < 1 or max_bytes < 1:
            raise ValueError('公共数据缓存上限必须为正数')
        self.path = root/'resources.sqlite3'
        self.download = download
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.pending = {}
        self._force_checks = set()
        self.cooldowns = {}
        self.fetches = asyncio.Semaphore(2)
        self._workers = set()
        self._closed = False

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('''CREATE TABLE IF NOT EXISTS resources (
            path TEXT PRIMARY KEY, body BLOB NOT NULL, etag TEXT, last_modified TEXT, cache_control TEXT,
            fetched_at REAL NOT NULL, checked_at REAL NOT NULL, accessed_at REAL NOT NULL)''')
        return db

    def _read(self, key):
        with closing(self._connect()) as db, db:
            row = db.execute('SELECT body, etag, last_modified, cache_control, fetched_at, checked_at '
                             'FROM resources WHERE path=?', (key.path,)).fetchone()
            if row is None:
                return None
            try:
                if not isinstance(row[0], bytes):
                    raise ValueError()
                if not all(isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 for value in row[-2:]):
                    raise ValueError()
                if not all(value is None or isinstance(value, str) for value in row[1:4]):
                    raise ValueError()
                public_api.decode(key, row[0])
            except (ValueError, QueryError):
                # A partial/corrupt entry must never supply validators for a 304 with no usable body.
                db.execute('DELETE FROM resources WHERE path=?', (key.path,))
                return None
            db.execute('UPDATE resources SET accessed_at=? WHERE path=?', (time.time(), key.path))
            return CachedResource(key, *row)

    def _write(self, snapshot):
        if self._closed:
            return
        if len(snapshot.body) > self.max_bytes:
            raise QueryError('公共数据超过本地缓存容量上限')
        with closing(self._connect()) as db, db:
            if 'no-store' in [part.strip().lower() for part in (snapshot.cache_control or '').split(',')]:
                db.execute('DELETE FROM resources WHERE path=?', (snapshot.key.path,))
                return
            db.execute('INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?,?,?,?)',
                       (snapshot.key.path, snapshot.body, snapshot.etag, snapshot.last_modified,
                        snapshot.cache_control, snapshot.fetched_at, snapshot.checked_at, time.time()))
            count, size = db.execute('SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0) FROM resources').fetchone()
            while count > self.max_entries or size > self.max_bytes:
                path, length = db.execute('SELECT path, LENGTH(body) FROM resources ORDER BY accessed_at, path LIMIT 1').fetchone()
                db.execute('DELETE FROM resources WHERE path=?', (path,))
                count, size = count-1, size-length

    def _delete(self, key):
        with closing(self._connect()) as db, db:
            db.execute('DELETE FROM resources WHERE path=?', (key.path,))

    async def _thread(self, function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self._workers.add(task)
        task.add_done_callback(self._workers.discard)
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    async def get(self, key, *, ttl_seconds=300, fresh=False, allow_stale=False):
        """Use a recent entry or conditionally revalidate. Stale fallback is explicit and labelled.

        `fresh` bypasses the local TTL (not validators); concurrent callers still share an in-flight check.
        No fallback occurs for an unconfigured proxy or local/programming errors.
        """
        if self._closed:
            raise RuntimeError('公共数据缓存已关闭')
        path = key.path
        if not isinstance(ttl_seconds, (int, float)) or not math.isfinite(ttl_seconds) or ttl_seconds < 0:
            raise ValueError('缓存有效期必须是有限的非负秒数')
        task = self.pending.get(path)
        if task is not None and task.done():
            # Its callback may not have run yet. A new fresh request must not join an already finished TTL hit.
            self.pending.pop(path)
            self._force_checks.discard(path)
            task = None
        if fresh:
            self._force_checks.add(path)
        if task is None:
            if len(self.pending) >= MAX_PENDING:
                self._force_checks.discard(path)
                raise QueryError('公共数据请求较多，请稍后再试')
            task = asyncio.create_task(self._get(key, ttl_seconds, fresh))
            self.pending[path] = task
            def finished(done):
                if self.pending.get(path) is done:
                    self.pending.pop(path)
                    self._force_checks.discard(path)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finished)
        try:
            # A cancelled query must not cancel a download still needed by another query.
            return await asyncio.shield(task)
        except (HTTPError, ConnectionFailure, public_api.DataError, TimeoutError) as exc:
            # Configuration errors are not covered: an invalid proxy must not silently look successful.
            if not allow_stale:
                raise
            previous = await self._thread(self._read, key)
            if previous is None:
                raise
            return replace(previous, stale=True, error=str(exc))

    async def _get(self, key, ttl_seconds, fresh):
        previous = await self._thread(self._read, key)
        age = time.time()-previous.checked_at if previous else None
        if previous is not None and not fresh and key.path not in self._force_checks and 0 <= age < cache_lifetime(previous.cache_control, ttl_seconds):
            return previous
        async with self.fetches:
            if self._closed:
                raise asyncio.CancelledError()
            cooldown = self.cooldowns.get(key.path)
            if cooldown and time.monotonic() < cooldown[0]:
                raise HTTPError(cooldown[1], cooldown[2], max(0, cooldown[0]-time.monotonic()))
            self.cooldowns.pop(key.path, None)
            try:
                metadata, body = await self.download(key, etag=previous.etag if previous else None,
                                                     last_modified=previous.last_modified if previous else None)
            except NotFound:
                # A confirmed deletion must not reappear on the next non-fresh query as a TTL-valid entry.
                await self._thread(self._delete, key)
                raise
            except HTTPError as exc:
                if exc.retry_after is not None:
                    if len(self.cooldowns) >= MAX_ENTRIES:
                        self.cooldowns.pop(next(iter(self.cooldowns)))
                    self.cooldowns[key.path] = (time.monotonic()+exc.retry_after, str(exc), exc.status)
                raise
            now = time.time()
            if metadata['status'] == 304:
                if previous is None:
                    raise public_api.DataError('公共数据返回 304，但没有可用的本地缓存')
                snapshot = replace(previous, checked_at=now,
                                   etag=metadata.get('etag') or previous.etag,
                                   last_modified=metadata.get('last_modified') or previous.last_modified,
                                   cache_control=metadata.get('cache_control') or previous.cache_control)
            elif metadata['status'] == 200 and isinstance(body, bytes):
                await self._thread(public_api.decode, key, body)
                snapshot = CachedResource(key, body, metadata.get('etag'), metadata.get('last_modified'),
                                          metadata.get('cache_control'), now, now, from_cache=False)
            else:
                raise public_api.DataError('公共数据响应无法识别')
            if self._closed:
                raise asyncio.CancelledError()
            await self._thread(self._write, snapshot)
            return snapshot

    async def close(self):
        """Cancel requests and wait for SQLite workers before a replacement plugin starts using the cache."""
        self._closed = True
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*list(self._workers), return_exceptions=True)
        self.pending.clear()
        self._force_checks.clear()
        self.cooldowns.clear()
