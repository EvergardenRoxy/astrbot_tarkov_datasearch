"""Nickname -> account id lookup, from the daily name index on players.tarkov.dev.

The index of a mode is one JSON object {"<account id>": "<nickname>", ...} (regular mode: ~3 million entries, ~67 MB).
It is converted once per refresh into a SQLite file per mode, so a lookup is one indexed query instead of a scan.
"""
import asyncio
import json
import mmap
import os
import re
import secrets
import sqlite3
import time

from astrbot.api import logger

from .text import clean
from .transport import QueryError

# Tokenize one string at a time; punctuation and EOF are checked separately.
JSON_STRING = re.compile(rb'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"')
SPACE = re.compile(rb'[ \t\r\n]*')
# Nicknames the plugin searches for: the game allows letters, digits, '_' and '-'. Chosen names are 3-15 characters,
# but generated ones (TarkovCitizen1019349) are longer: up to 21 in the 2026-10-13 indexes.
NAME = re.compile(r'[A-Za-z0-9_-]{3,24}')
MAX_MATCHES = 6
# "Did you mean" suggestions: names that differ only by '_' and '-' (players mix them up). Names with more separators
# than this get no suggestions, so the variants stay few (2**6 at most).
MAX_SEPARATORS = 6
SUGGESTIONS = 3
# After a failed refresh, wait this long before downloading again, with or without an older index to fall back on.
RETRY_SECONDS = 600
# Temporary files older than this at start-up are leftovers of a crash; younger ones may belong to an instance that
# AstrBot is still running after a reload (a config save reloads the plugin without cancelling running queries).
LEFTOVER_SECONDS = 3600


def spelling_variants(name):
    """The other lower-case spellings of `name` with each '_' or '-' swapped for the other; [] when there are none or
    too many separators."""
    key = name.strip().lower()
    spots = [i for i, char in enumerate(key) if char in '_-']
    if not spots or len(spots) > MAX_SEPARATORS:
        return []
    variants = []
    for mask in range(2**len(spots)):
        chars = list(key)
        for bit, i in enumerate(spots):
            chars[i] = '-' if mask >> bit & 1 else '_'
        variant = ''.join(chars)
        if variant != key:
            variants.append(variant)
    return variants


def every(seconds):
    """A refresh interval in words: 每周, 每 2 周, 每天, 每 3 天, 每 5 小时."""
    hours = max(1, round(seconds/3600))
    for size, unit, one in ((168, '周', '每周'), (24, '天', '每天')):
        if hours % size == 0:
            return one if hours == size else f'每 {hours//size} {unit}'
    return f'每 {hours} 小时'


def unique_suffix():
    """Per-refresh file name part, so two plugin instances never write the same temporary file."""
    return f'{os.getpid()}-{secrets.token_hex(4)}'


def read_only(path):
    """A read-only SQLite connection. The URI is built with as_uri() so '#', '?' and '%' in the data directory's
    path are escaped (an unescaped path was cut at '#', losing mode=ro and opening another file)."""
    return sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)


def parse_entries(raw):
    """(lowercase key, account id, nickname) for every entry of an index document."""
    position = 0

    def whitespace():
        nonlocal position
        position = SPACE.match(raw, position).end()

    def take(token):
        nonlocal position
        whitespace()
        if raw[position:position+1] != token:
            raise QueryError('名单格式无法识别')
        position += 1

    def string():
        nonlocal position
        whitespace()
        match = JSON_STRING.match(raw, position)
        if match is None:
            raise QueryError('名单格式无法识别')
        position = match.end()
        try:
            return json.loads(match.group())
        except (ValueError, UnicodeError):
            raise QueryError('名单格式无法识别') from None

    take(b'{')
    whitespace()
    if raw[position:position+1] != b'}':
        while True:
            aid = string()
            take(b':')
            text = string()
            if re.fullmatch(r'[0-9]{1,12}', aid) and len(text) <= 64:
                name = clean(text, 64)
                if name:
                    yield name.lower(), int(aid), name
            whitespace()
            if raw[position:position+1] == b'}':
                break
            take(b',')
    take(b'}')
    whitespace()
    if position != len(raw):
        raise QueryError('名单格式无法识别')


def build(db_path, raw, etag):
    """Write a fresh database for one index document to db_path (replaced atomically). Returns the entry count."""
    temp = db_path.with_name(f'{db_path.name}.{unique_suffix()}.tmp')
    try:
        count = write(temp, raw, etag)
        os.replace(temp, db_path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return count


def write(temp, raw, etag):
    db = sqlite3.connect(temp)
    try:
        db.execute('PRAGMA journal_mode=OFF')
        db.execute('PRAGMA synchronous=OFF')
        db.execute('CREATE TABLE names (key TEXT NOT NULL, aid INTEGER NOT NULL, name TEXT NOT NULL, PRIMARY KEY (key, aid)) WITHOUT ROWID')
        db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
        db.executemany('INSERT OR IGNORE INTO names VALUES (?,?,?)', parse_entries(raw))
        count = db.execute('SELECT COUNT(*) FROM names').fetchone()[0]
        if not count:
            raise QueryError('名单格式无法识别')
        db.executemany('INSERT INTO meta VALUES (?,?)', [('etag', etag or ''), ('checked_at', str(time.time())), ('entries', str(count))])
        db.commit()
    finally:
        db.close()
    return count


class NameIndex:
    def __init__(self, root, download, refresh_hours=168):
        """download(mode_path, target, etag) -> {'status': 200|304, 'etag': ...} fetches an index file."""
        self.root = root
        self.download = download
        self.refresh_seconds = max(1, min(720, float(refresh_hours)))*3600
        self.locks = {}
        self.pending = {}
        self.workers = set()
        self.closed = False
        # mode path -> (time of the last failed refresh, reason shown to users)
        self.failed_at = {}
        for stale in list(root.glob('names-*.tmp'))+list(root.glob('names-*.download')):
            try:
                if time.time()-stale.stat().st_mtime > LEFTOVER_SECONDS:
                    stale.unlink()
            except OSError:
                pass

    def db_path(self, mode_path):
        return self.root/f'names-{mode_path}.sqlite3'

    def meta(self, mode_path):
        """The stored meta values, or None when there is no usable database."""
        path = self.db_path(mode_path)
        if not path.exists():
            return None
        try:
            db = read_only(path)
            try:
                return dict(db.execute('SELECT key, value FROM meta').fetchall())
            finally:
                db.close()
        except sqlite3.Error:
            return None

    def has_index(self, mode_path):
        return self.meta(mode_path) is not None

    def waiting(self, mode_path):
        """Seconds left before a failed refresh may be retried, 0 when there is none."""
        failed = self.failed_at.get(mode_path)
        return max(0, RETRY_SECONDS-(time.time()-failed[0])) if failed else 0

    def needs_refresh(self, mode_path):
        if self.waiting(mode_path):
            return False
        meta = self.meta(mode_path)
        if meta is None:
            return True
        try:
            return time.time()-float(meta.get('checked_at', 0)) >= self.refresh_seconds
        except ValueError:
            return True

    async def lookup(self, mode_path, name):
        """([(account id, nickname)], total) for nicknames equal to `name` ignoring case. At most MAX_MATCHES rows:
        exact-case matches first, then by account id; total counts every case-insensitive match."""
        if self.closed:
            raise RuntimeError('Name index closed')
        if self.needs_refresh(mode_path):
            task = self.start_refresh(mode_path)
            if not self.has_index(mode_path):
                await asyncio.shield(task)
        if not self.has_index(mode_path):
            minutes = max(1, round(self.waiting(mode_path)/60))
            raise QueryError(f'玩家名单暂时无法下载（{self.failed_at[mode_path][1]}），请约 {minutes} 分钟后再试；'
                             '知道账号 ID 时可以先用 ID 查询')
        lock = self.locks.setdefault(mode_path, asyncio.Lock())
        async with lock:
            try:
                return await self.thread(self.query, mode_path, name)
            except sqlite3.DatabaseError as exc:
                logger.warning('Tarkov name index %s unreadable, rebuilding: %s', mode_path, type(exc).__name__)
                self.db_path(mode_path).unlink(missing_ok=True)
        # Never hold the reader lock while waiting for a refresh to publish.
        await self.refresh(mode_path)
        async with lock:
            return await self.thread(self.query, mode_path, name)

    async def thread(self, function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self.workers.add(task)
        task.add_done_callback(self.workers.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # A Python worker cannot be cancelled. Keep its file and reader lock
            # alive until it finishes, including during plugin unload.
            await asyncio.gather(task, return_exceptions=True)
            raise

    def start_refresh(self, mode_path):
        if self.closed:
            raise RuntimeError('Name index closed')
        task = self.pending.get(mode_path)
        if task is None or task.done():
            task = asyncio.create_task(self._refresh(mode_path))
            self.pending[mode_path] = task
            def finished(done):
                if self.pending.get(mode_path) is done:
                    self.pending.pop(mode_path)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finished)
        return task

    async def refresh(self, mode_path):
        await asyncio.shield(self.start_refresh(mode_path))

    async def close(self):
        self.closed = True
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*list(self.workers), return_exceptions=True)
        self.pending.clear()

    async def similar(self, mode_path, name):
        """[(account id, nickname)] whose name differs from `name` only by '_' and '-', from the index already on
        disk: never downloads, [] without an index."""
        variants = spelling_variants(name)
        if self.closed or not variants or not self.has_index(mode_path):
            return []
        async with self.locks.setdefault(mode_path, asyncio.Lock()):
            try:
                return await self.thread(self.query_similar, mode_path, variants)
            except sqlite3.DatabaseError as exc:
                logger.warning('Tarkov name index %s unreadable for suggestions: %s', mode_path, type(exc).__name__)
                return []

    def query_similar(self, mode_path, variants):
        db = read_only(self.db_path(mode_path))
        try:
            marks = ','.join('?'*len(variants))
            return db.execute(f'SELECT aid, name FROM names WHERE key IN ({marks}) ORDER BY aid LIMIT ?',
                              (*variants, SUGGESTIONS)).fetchall()
        finally:
            db.close()

    def query(self, mode_path, name):
        key = name.strip().lower()
        db = read_only(self.db_path(mode_path))
        try:
            exact = db.execute('SELECT aid, name FROM names WHERE key=? AND name=? ORDER BY aid LIMIT ?',
                               (key, name.strip(), MAX_MATCHES)).fetchall()
            rest = db.execute('SELECT aid, name FROM names WHERE key=? AND name<>? ORDER BY aid LIMIT ?',
                              (key, name.strip(), MAX_MATCHES)).fetchall()
            total = db.execute('SELECT COUNT(*) FROM names WHERE key=?', (key,)).fetchone()[0]
            return (exact+rest)[:MAX_MATCHES], total
        finally:
            db.close()

    async def _refresh(self, mode_path):
        meta = self.meta(mode_path)
        target = self.root/f'names-{mode_path}.{unique_suffix()}.download'
        temporary = target.with_suffix('.tmp')
        started = time.monotonic()
        try:
            result = await self.download(mode_path, target, (meta or {}).get('etag') or None)
            if result['status'] == 304:
                async with self.locks.setdefault(mode_path, asyncio.Lock()):
                    await self.thread(self.touch, mode_path)
                logger.info('Tarkov name index %s unchanged', mode_path)
            else:
                count = await self.thread(self.stage, temporary, target, result.get('etag'))
                async with self.locks.setdefault(mode_path, asyncio.Lock()):
                    if self.closed:
                        raise asyncio.CancelledError()
                    await self.thread(os.replace, temporary, self.db_path(mode_path))
                logger.info('Tarkov name index %s rebuilt; entries=%s elapsed=%.1fs', mode_path, count, time.monotonic()-started)
            self.failed_at.pop(mode_path, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Wait before the next download in both cases: with an older index it keeps serving; without one the
            # lookups in the meantime are told to retry later instead of each downloading the whole index again.
            reason = str(exc) if isinstance(exc, QueryError) else '下载超时' if isinstance(exc, TimeoutError) else type(exc).__name__
            self.failed_at[mode_path] = (time.time(), reason)
            if meta is None:
                logger.warning('Tarkov name index %s first download failed: %s', mode_path, type(exc).__name__)
                raise
            logger.warning('Tarkov name index %s refresh failed, using the previous one: %s', mode_path, type(exc).__name__)
        finally:
            target.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)

    @staticmethod
    def stage(temporary, target, etag):
        with target.open('rb') as source:
            if not os.fstat(source.fileno()).st_size:
                raise QueryError('名单格式无法识别')
            with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as raw:
                return write(temporary, raw, etag)

    def touch(self, mode_path):
        db = sqlite3.connect(self.db_path(mode_path))
        try:
            with db:
                db.execute("UPDATE meta SET value=? WHERE key='checked_at'", (str(time.time()),))
        finally:
            db.close()
