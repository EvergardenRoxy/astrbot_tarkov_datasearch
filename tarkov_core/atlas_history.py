"""Local history of Atlas player documents, for later scheduled pulls, weekly reports and comparisons over time.

Stores Atlas' raw response (gzip-compressed JSON) of every successful lookup; tarkov.dev snapshots are never stored
(maintainer, 2026-11-08). One row per (account, mode, looked_up): a lookup Atlas answered from its own cache adds
nothing. Retention (hours) and the total size cap (MiB) come from the settings; the oldest rows go first. Only this
plugin's own data directory is written; the privacy switch turns recording off and deletes the file.

The read side (latest, history, compare, accounts) is the interface for future features. The weekly report keeps its
own compact samples (weekly_store.py); its nightly pulls are recorded here like any other successful lookup.
"""
import datetime
import gzip
import json
import sqlite3
import threading
import time

SCHEMA = '''
CREATE TABLE IF NOT EXISTS lookups (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    mode TEXT NOT NULL,
    looked_up REAL NOT NULL,
    fetched_at REAL NOT NULL,
    nickname TEXT NOT NULL,
    size INTEGER NOT NULL,
    body BLOB NOT NULL,
    UNIQUE (account_id, mode, looked_up)
);
CREATE INDEX IF NOT EXISTS lookups_time ON lookups (fetched_at);
CREATE INDEX IF NOT EXISTS lookups_account ON lookups (account_id, mode, looked_up);
'''
MODES = ('pvp', 'pve', 'season', 'arena')
MAX_BODY = 1024*1024


def looked_up_seconds(doc):
    value = doc.get('looked_up') if isinstance(doc, dict) else None
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment.timestamp() if moment.tzinfo else None


class AtlasHistory:
    def __init__(self, path, *, hours=lambda: 180, max_bytes=lambda: 64*1024*1024, clock=time.time):
        self.path, self.hours, self.max_bytes, self.clock = path, hours, max_bytes, clock
        self.lock = threading.Lock()

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.executescript(SCHEMA)
        return connection

    def record(self, account_id, mode, doc, fetched_at=None):
        """Store one Atlas document; False when it is not storable or already stored. Prunes afterwards."""
        moment = looked_up_seconds(doc)
        name = doc.get('nickname') if isinstance(doc, dict) else None
        if mode not in MODES or type(account_id) is not int or moment is None or not isinstance(name, str):
            return False
        body = gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode(), 6)
        if len(body) > MAX_BODY:
            return False
        with self.lock:
            connection = self.connect()
            try:
                with connection:
                    cursor = connection.execute(
                        'INSERT OR IGNORE INTO lookups (account_id, mode, looked_up, fetched_at, nickname, size, body) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?)',
                        (account_id, mode, moment, self.clock() if fetched_at is None else fetched_at, name[:64],
                         len(body), body))
                    self._prune(connection)
                return cursor.rowcount == 1
            finally:
                connection.close()

    def _prune(self, connection):
        connection.execute('DELETE FROM lookups WHERE fetched_at < ?', (self.clock()-self.hours()*3600,))
        total = connection.execute('SELECT COALESCE(SUM(size), 0) FROM lookups').fetchone()[0]
        limit = self.max_bytes()
        while total > limit:
            row = connection.execute('SELECT id, size FROM lookups ORDER BY fetched_at, id LIMIT 1').fetchone()
            if row is None:
                break
            connection.execute('DELETE FROM lookups WHERE id = ?', (row[0],))
            total -= row[1]

    def prune(self):
        if not self.path.exists():
            return
        with self.lock:
            connection = self.connect()
            try:
                with connection:
                    self._prune(connection)
            finally:
                connection.close()

    def _rows(self, sql, args):
        if not self.path.exists():
            return []
        with self.lock:
            connection = self.connect()
            try:
                return connection.execute(sql, args).fetchall()
            finally:
                connection.close()

    @staticmethod
    def _entry(row):
        return {'account_id': row[0], 'mode': row[1], 'looked_up': row[2], 'fetched_at': row[3], 'nickname': row[4],
                'document': json.loads(gzip.decompress(row[5]))}

    def latest(self, account_id, mode):
        """The newest stored document of an account in a mode, or None."""
        rows = self._rows('SELECT account_id, mode, looked_up, fetched_at, nickname, body FROM lookups '
                          'WHERE account_id = ? AND mode = ? ORDER BY looked_up DESC LIMIT 1', (account_id, mode))
        return self._entry(rows[0]) if rows else None

    def history(self, account_id, mode, since=None, until=None):
        """Stored documents of an account in a mode, oldest first, with Atlas' time in [since, until]."""
        rows = self._rows('SELECT account_id, mode, looked_up, fetched_at, nickname, body FROM lookups '
                          'WHERE account_id = ? AND mode = ? AND looked_up >= ? AND looked_up <= ? ORDER BY looked_up',
                          (account_id, mode, -1e18 if since is None else since, 1e18 if until is None else until))
        return [self._entry(row) for row in rows]

    def compare(self, account_id, mode, since=None, until=None):
        """(oldest, newest) stored document in the time range, for a later "change over time" feature; None when
        fewer than two are stored."""
        found = self.history(account_id, mode, since, until)
        return (found[0], found[-1]) if len(found) >= 2 else None

    def accounts(self, since=None):
        """[(account id, mode, latest nickname)] seen since `since` (Atlas time), for later scheduled pulls."""
        rows = self._rows('SELECT account_id, mode, nickname FROM lookups WHERE looked_up >= ? '
                          'GROUP BY account_id, mode HAVING looked_up = MAX(looked_up) ORDER BY account_id, mode',
                          (-1e18 if since is None else since,))
        return [tuple(row) for row in rows]

    def delete_all(self):
        """The privacy switch is off: remove the whole file."""
        with self.lock:
            for suffix in ('', '-journal', '-wal', '-shm'):
                candidate = self.path.with_name(self.path.name+suffix)
                if candidate.exists():
                    candidate.unlink()
