"""Weekly report data in its own SQLite file: resolved roster names, each night's sampling progress, compact
samples and broadcast records. Never the bindings database.

The roster itself (account ids or nicknames per mode) lives in the plugin settings (maintainer, 2026-11-10); a roster
entry is identified here by (mode, the entry lower-cased). Nothing is deleted when an entry leaves the settings:
samples simply expire (`keep_days`). No chat user ids are stored.
"""
import json
import sqlite3
import threading
import time

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
# Attempts per roster entry and night: one retry after a failure, then tomorrow.
ATTEMPTS = 2
# Broadcast records are kept this long (they only stop a slot from being sent twice).
PUSH_KEEP = 60*86400


def key_of(entry):
    return str(entry).strip().lower()


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
        self._run(lambda c: c.execute('INSERT OR REPLACE INTO players VALUES (?, ?, ?, ?, ?)',
                                      (mode, key_of(entry), account_id, str(nickname)[:64], self.clock())))

    def roster(self, mode, entries):
        """[(entry, account id or None, nickname or None)] for the settings' entries of a mode, in their order."""
        known = {row[0]: (row[1], row[2]) for row in self._run(
            lambda c: c.execute('SELECT entry, account_id, nickname FROM players WHERE mode = ?', (mode,)).fetchall(),
            [], create=False)}
        return [(entry, *known.get(key_of(entry), (None, None))) for entry in entries]

    # ---- the night's progress ----

    def pending(self, entries, day):
        """[(mode, entry)] still to pull on `day`, in the given order: not attempted today first, then those whose
        single retry is left. entries: [(mode, entry)]."""
        rows = self._run(lambda c: c.execute('SELECT mode, entry, attempts, done FROM pulls WHERE day = ?', (day,)).fetchall(),
                         [], create=False)
        state = {(row[0], row[1]): (row[2], row[3]) for row in rows}
        fresh = [pair for pair in entries if (pair[0], key_of(pair[1])) not in state]
        again = [pair for pair in entries if not state.get((pair[0], key_of(pair[1])), (0, 1))[1]
                 and state[(pair[0], key_of(pair[1]))][0] < ATTEMPTS]
        return fresh+again

    def attempted(self, mode, entry, day, done, error=None):
        """Count one attempt of the entry on `day`; `done` ends the entry for the day."""
        def work(connection):
            row = connection.execute('SELECT day, attempts FROM pulls WHERE mode = ? AND entry = ?',
                                     (mode, key_of(entry))).fetchone()
            attempts = (row[1] if row and row[0] == day else 0)+1
            connection.execute('INSERT OR REPLACE INTO pulls VALUES (?, ?, ?, ?, ?, ?, ?)',
                               (mode, key_of(entry), day, attempts, 1 if done else 0,
                                None if error is None else str(error)[:64], self.clock()))
        self._run(work)

    # ---- samples ----

    def add_sample(self, account_id, mode, sample, fetched_at=None):
        """Store a sample; False when it is not storable or the same Atlas time is already stored. Prunes."""
        if mode not in GAME_MODES or type(account_id) is not int or not isinstance(sample, dict) \
                or not isinstance(sample.get('looked_up'), (int, float)) or isinstance(sample.get('looked_up'), bool):
            return False
        body = json.dumps(sample, ensure_ascii=False, separators=(',', ':'))
        if len(body.encode()) > MAX_BODY:
            return False
        def work(connection):
            now = self.clock()
            added = connection.execute(
                'INSERT OR IGNORE INTO samples (account_id, mode, looked_up, fetched_at, body) VALUES (?, ?, ?, ?, ?)',
                (account_id, mode, sample['looked_up'], now if fetched_at is None else fetched_at, body)).rowcount == 1
            self._prune(connection, now)
            return added
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
