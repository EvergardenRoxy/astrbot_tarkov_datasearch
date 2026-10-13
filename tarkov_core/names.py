"""Nickname -> account id lookup, from the daily name index on players.tarkov.dev.

The index of a mode is one JSON object {"<account id>": "<nickname>", ...} (regular mode: ~3 million entries, ~67 MB).
It is converted once per refresh into a SQLite file per mode, so a lookup is one indexed query instead of a scan.
"""
import asyncio
import json
import os
import re
import sqlite3
import time

from astrbot.api import logger

from .transport import QueryError

# Escape sequences inside a name are decoded; entries longer than 64 characters are junk and skipped.
ENTRY = re.compile(rb'"(\d{1,12})"\s*:\s*"((?:[^"\\]|\\.){0,64})"')
# Nicknames the plugin searches for: the game allows letters, digits, '_' and '-'; tarkov.dev's search asks for 3-15.
NAME = re.compile(r'[A-Za-z0-9_-]{3,15}')
MAX_MATCHES = 6
# After a failed refresh, wait this long before trying again; an older index keeps serving meanwhile.
RETRY_SECONDS = 600


def parse_entries(raw):
    """(lowercase key, account id, nickname) for every entry of an index document."""
    if not raw.lstrip()[:1] == b'{':
        raise QueryError('名单格式无法识别')
    for match in ENTRY.finditer(raw):
        text = match.group(2).decode('utf-8', 'replace')
        if '\\' in text:
            try:
                text = json.loads('"'+text+'"')
            except ValueError:
                continue
        name = text.strip()
        if name:
            yield name.lower(), int(match.group(1)), name


def build(db_path, raw, etag):
    """Write a fresh database for one index document to db_path (replaced atomically). Returns the entry count."""
    temp = db_path.with_name(db_path.name+'.tmp')
    temp.unlink(missing_ok=True)
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
    os.replace(temp, db_path)
    return count


class NameIndex:
    def __init__(self, root, download, refresh_hours=24):
        """download(mode_path, target, etag) -> {'status': 200|304, 'etag': ...} fetches an index file."""
        self.root = root
        self.download = download
        self.refresh_seconds = max(1, min(720, float(refresh_hours)))*3600
        self.locks = {}
        self.failed_at = {}
        for stale in list(root.glob('names-*.tmp'))+list(root.glob('names-*.download')):
            stale.unlink(missing_ok=True)

    def db_path(self, mode_path):
        return self.root/f'names-{mode_path}.sqlite3'

    def meta(self, mode_path):
        """The stored meta values, or None when there is no usable database."""
        path = self.db_path(mode_path)
        if not path.exists():
            return None
        try:
            db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
            try:
                return dict(db.execute('SELECT key, value FROM meta').fetchall())
            finally:
                db.close()
        except sqlite3.Error:
            return None

    def needs_refresh(self, mode_path):
        meta = self.meta(mode_path)
        if meta is None:
            return True
        if time.time()-self.failed_at.get(mode_path, 0) < RETRY_SECONDS:
            return False
        try:
            return time.time()-float(meta.get('checked_at', 0)) >= self.refresh_seconds
        except ValueError:
            return True

    async def lookup(self, mode_path, name):
        """[(account id, nickname)] whose nickname equals `name` ignoring case, at most MAX_MATCHES, by account id."""
        lock = self.locks.setdefault(mode_path, asyncio.Lock())
        # Lookups and refreshes share the lock, so the database is never replaced under an open reader.
        async with lock:
            if self.needs_refresh(mode_path):
                await self.refresh(mode_path)
            return await asyncio.to_thread(self.query, mode_path, name)

    def query(self, mode_path, name):
        db = sqlite3.connect(f'file:{self.db_path(mode_path)}?mode=ro', uri=True)
        try:
            return db.execute('SELECT aid, name FROM names WHERE key=? ORDER BY aid LIMIT ?',
                              (name.strip().lower(), MAX_MATCHES)).fetchall()
        finally:
            db.close()

    async def refresh(self, mode_path):
        meta = self.meta(mode_path)
        target = self.root/f'names-{mode_path}.download'
        started = time.monotonic()
        try:
            result = await self.download(mode_path, target, (meta or {}).get('etag') or None)
            if result['status'] == 304:
                await asyncio.to_thread(self.touch, mode_path)
                logger.info('Tarkov name index %s unchanged', mode_path)
            else:
                count = await asyncio.to_thread(self.rebuild, mode_path, target, result.get('etag'))
                logger.info('Tarkov name index %s rebuilt; entries=%s elapsed=%.1fs', mode_path, count, time.monotonic()-started)
            self.failed_at.pop(mode_path, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if meta is None:
                raise
            # An older index is still useful; retry later instead of failing every lookup.
            self.failed_at[mode_path] = time.time()
            logger.warning('Tarkov name index %s refresh failed, using the previous one: %s', mode_path, type(exc).__name__)
        finally:
            target.unlink(missing_ok=True)

    def rebuild(self, mode_path, target, etag):
        return build(self.db_path(mode_path), target.read_bytes(), etag)

    def touch(self, mode_path):
        db = sqlite3.connect(self.db_path(mode_path))
        try:
            with db:
                db.execute("UPDATE meta SET value=? WHERE key='checked_at'", (str(time.time()),))
        finally:
            db.close()
