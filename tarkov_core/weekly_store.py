"""Weekly report data in its own SQLite file: resolved roster names, each night's sampling progress, compact
samples and broadcast records. Never the bindings database.

The roster itself (account ids or nicknames per mode) lives in the plugin settings (maintainer, 2026-11-10).
Names retain their entry keys; nightly progress uses (mode, account id) once resolved, merging legacy entry keys.
Nothing is deleted when an entry leaves the settings:
samples simply expire (`keep_days`). No chat user ids are stored.
"""
import json
import math
import sqlite3
import threading
import time
from enum import Enum

from .modes import GAME_MODES

SCHEMA = '''
CREATE TABLE IF NOT EXISTS players (
    mode TEXT NOT NULL, entry TEXT NOT NULL, account_id INTEGER NOT NULL, nickname TEXT NOT NULL,
    resolved_at REAL NOT NULL, PRIMARY KEY (mode, entry));
CREATE TABLE IF NOT EXISTS pulls (
    mode TEXT NOT NULL, entry TEXT NOT NULL, day TEXT NOT NULL, attempts INTEGER NOT NULL, done INTEGER NOT NULL,
    error TEXT, at REAL NOT NULL, PRIMARY KEY (mode, entry));
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, mode TEXT NOT NULL, looked_up REAL NOT NULL,
    fetched_at REAL NOT NULL, body TEXT NOT NULL, UNIQUE (account_id, mode, looked_up));
CREATE TABLE IF NOT EXISTS pushes (
    slot TEXT NOT NULL, target TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, at REAL NOT NULL, PRIMARY KEY (slot, target, mode));
'''
MAX_BODY = 64*1024
# Attempts per resolved account (or unresolved entry) and night: one retry after a failure, then tomorrow.
ATTEMPTS = 2
# Broadcast records are kept this long (they only stop a slot from being sent twice).
PUSH_KEEP = 60*86400


def key_of(entry):
    return str(entry).strip().lower()


class SampleResult(Enum):
    ADDED = 'added'
    DUPLICATE = 'duplicate'
    INVALID = 'invalid'


class WeeklyStore:
    def __init__(self, path, *, keep_days=lambda: 30, clock=time.time):
        self.path, self.keep_days, self.clock = path, keep_days, clock
        self.lock = threading.Lock()

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.executescript(SCHEMA)
        return connection

    def _run(self, work, default=None, *, create=True):
        """Run work(connection) in one transaction. Without `create`, a missing file returns `default`."""
        if not create and not self.path.exists():
            return default
        with self.lock:
            connection = self.connect()
            try:
                with connection:
                    return work(connection)
            finally:
                connection.close()

    # ---- roster names ----

    def resolved(self, mode, entry):
        """(account id, nickname) a roster entry was resolved to, or None."""
        row = self._run(lambda c: c.execute('SELECT account_id, nickname FROM players WHERE mode = ? AND entry = ?',
                                            (mode, key_of(entry))).fetchone(), None, create=False)
        return tuple(row) if row else None

    def resolve(self, mode, entry, account_id, nickname):
        if mode not in GAME_MODES or type(account_id) is not int:
            raise ValueError('invalid roster entry')
        def work(connection):
            connection.execute('INSERT OR REPLACE INTO players VALUES (?, ?, ?, ?, ?)',
                               (mode, key_of(entry), account_id, str(nickname)[:64], self.clock()))
            self._merge_pulls(connection)
        self._run(work)

    def roster(self, mode, entries):
        """[(entry, account id or None, nickname or None)] for the settings' entries of a mode, in their order."""
        known = {row[0]: (row[1], row[2]) for row in self._run(
            lambda c: c.execute('SELECT entry, account_id, nickname FROM players WHERE mode = ?', (mode,)).fetchall(),
            [], create=False)}
        return [(entry, *known.get(key_of(entry), (None, None))) for entry in entries]

    # ---- the night's progress ----

    @staticmethod
    def _identity(connection, mode, entry):
        key = key_of(entry)
        if key.startswith('id:'):
            return key
        if key.isascii() and key.isdigit():
            return f'id:{int(key)}'
        row = connection.execute('SELECT account_id FROM players WHERE mode=? AND entry=?', (mode, key)).fetchone()
        return f'id:{row[0]}' if row else key

    def _merge_pulls(self, connection):
        """Lazily migrate legacy entry-keyed progress and newly resolved aliases, in the caller's transaction.

        The latest day wins; its attempts are combined and any terminal result remains terminal. Canonical keys
        cannot be player nicknames. Repeating this migration, including after a restart, does not recount attempts.
        """
        groups = {}
        for mode, entry, day, attempts, done, error, at in connection.execute('SELECT * FROM pulls').fetchall():
            identity = self._identity(connection, mode, entry)
            groups.setdefault((mode, identity), []).append((entry, day, attempts, done, error, at))
        for (mode, identity), rows in groups.items():
            if len(rows) == 1 and rows[0][0] == identity:
                continue
            day = max(row[1] for row in rows)
            current = [row for row in rows if row[1] == day]
            latest = max(current, key=lambda row: row[5])
            connection.executemany('DELETE FROM pulls WHERE mode=? AND entry=?', [(mode, row[0]) for row in rows])
            connection.execute('INSERT INTO pulls VALUES (?, ?, ?, ?, ?, ?, ?)',
                               (mode, identity, day, sum(row[2] for row in current),
                                int(any(row[3] for row in current)), latest[4], latest[5]))

    def pending(self, entries, day):
        """[(mode, entry)] still to pull on `day`, in the given order: not attempted today first, then those whose
        single retry is left. entries: [(mode, entry)]."""
        def work(connection):
            self._merge_pulls(connection)
            rows = connection.execute('SELECT mode, entry, attempts, done FROM pulls WHERE day=?', (day,)).fetchall()
            state = {(row[0], row[1]): (row[2], row[3]) for row in rows}
            fresh, again, seen = [], [], set()
            for mode, entry in entries:
                identity = (mode, self._identity(connection, mode, entry))
                if identity in seen:
                    continue
                seen.add(identity)
                previous = state.get(identity)
                if previous is None:
                    fresh.append((mode, entry))
                elif not previous[1] and previous[0] < ATTEMPTS:
                    again.append((mode, entry))
            return fresh+again
        # The first step has no progress to merge yet; do not create a database merely to read it.
        return self._run(work, list(dict.fromkeys(entries)), create=False)

    def attempted(self, mode, entry, day, done, error=None):
        """Count an attempt on `day`; resolved aliases share the account's budget and terminal state."""
        self._run(lambda c: self._attempted(c, mode, entry, day, done, error))

    def _attempted(self, connection, mode, entry, day, done, error=None):
        self._merge_pulls(connection)
        identity = self._identity(connection, mode, entry)
        row = connection.execute('SELECT day, attempts, done FROM pulls WHERE mode=? AND entry=?',
                                 (mode, identity)).fetchone()
        if row and row[0] == day and row[2]:
            return  # Terminal results are idempotent, including duplicate sample saves.
        attempts = (row[1] if row and row[0] == day else 0)+1
        connection.execute('INSERT OR REPLACE INTO pulls VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (mode, identity, day, attempts, int(done),
                            None if error is None else str(error)[:64], self.clock()))

    # ---- samples ----

    def add_sample(self, account_id, mode, sample, fetched_at=None, *, day=None):
        """Return ADDED, DUPLICATE or INVALID; storage failures raise. With `day`, commit the sample and the
        account's nightly completion in the same transaction. User-fed samples do not complete a nightly pull."""
        if mode not in GAME_MODES or type(account_id) is not int or not isinstance(sample, dict) \
                or not isinstance(sample.get('looked_up'), (int, float)) or isinstance(sample.get('looked_up'), bool):
            return SampleResult.INVALID
        if not math.isfinite(sample['looked_up']):
            return SampleResult.INVALID
        body = json.dumps(sample, ensure_ascii=False, separators=(',', ':'))
        if len(body.encode()) > MAX_BODY:
            return SampleResult.INVALID
        def work(connection):
            now = self.clock()
            added = connection.execute(
                'INSERT OR IGNORE INTO samples (account_id, mode, looked_up, fetched_at, body) VALUES (?, ?, ?, ?, ?)',
                (account_id, mode, sample['looked_up'], now if fetched_at is None else fetched_at, body)).rowcount == 1
            self._prune(connection, now)
            if day is not None:
                self._attempted(connection, mode, str(account_id), day, True)
            return SampleResult.ADDED if added else SampleResult.DUPLICATE
        return self._run(work)

    def samples(self, account_id, mode, since):
        """Stored samples with Atlas' time from `since` on, oldest first."""
        rows = self._run(lambda c: c.execute('SELECT body FROM samples WHERE account_id = ? AND mode = ? AND looked_up >= ? '
                                             'ORDER BY looked_up', (account_id, mode, since)).fetchall(), [], create=False)
        return [json.loads(row[0]) for row in rows]

    def counts(self):
        """(accounts with samples, samples)."""
        return tuple(self._run(lambda c: (c.execute('SELECT COUNT(*) FROM (SELECT DISTINCT account_id, mode FROM samples)').fetchone()[0],
                                          c.execute('SELECT COUNT(*) FROM samples').fetchone()[0]), (0, 0), create=False))

    def _prune(self, connection, now):
        connection.execute('DELETE FROM samples WHERE looked_up < ?', (now-self.keep_days()*86400,))
        connection.execute('DELETE FROM pushes WHERE at < ?', (now-PUSH_KEEP,))

    def prune(self):
        self._run(lambda c: self._prune(c, self.clock()), create=False)

    # ---- broadcasts ----

    def push_state(self, slot, target, mode):
        """(state, attempts) of one broadcast picture, or None when it was never tried."""
        row = self._run(lambda c: c.execute('SELECT state, attempts FROM pushes WHERE slot = ? AND target = ? AND mode = ?',
                                            (slot, target, mode)).fetchone(), None, create=False)
        return tuple(row) if row else None

    def push_record(self, slot, target, mode, state):
        def work(connection):
            row = connection.execute('SELECT attempts FROM pushes WHERE slot = ? AND target = ? AND mode = ?',
                                     (slot, target, mode)).fetchone()
            connection.execute('INSERT OR REPLACE INTO pushes VALUES (?, ?, ?, ?, ?, ?)',
                               (slot, target, mode, state, (row[0] if row else 0)+1, self.clock()))
        self._run(work)
