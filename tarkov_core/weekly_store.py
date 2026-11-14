"""Weekly report data in its own SQLite file: each chat's roster, resolved roster names, each night's sampling
progress, compact samples and broadcast records. Never the bindings database.

Every chat (AstrBot session id) keeps its own roster per mode: the players its reports and rankings are about and
its weekly broadcast shows. What is known about a player is shared: a nickname resolves to one account for every
chat, and an account listed in several chats is sampled once a night. Nightly progress uses (mode, account id)
once resolved. Samples of players no chat lists any more simply expire (`keep_days`). No chat user ids are stored.
"""
import json
import math
import sqlite3
import threading
import time
from enum import Enum

from .modes import GAME_MODES

SCHEMA = '''
CREATE TABLE IF NOT EXISTS rosters (
    chat TEXT NOT NULL, mode TEXT NOT NULL, entry TEXT NOT NULL, label TEXT NOT NULL, added_at REAL NOT NULL,
    PRIMARY KEY (chat, mode, entry));
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
# Listed players per chat and mode, and sampled per mode for all chats together: 3 x 40 pulls of about 160 seconds
# fit the 03:00-09:00 window.
CHAT_PLAYERS = 40
MODE_PLAYERS = 40
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
        def work(connection):
            connection.execute('INSERT OR REPLACE INTO players VALUES (?, ?, ?, ?, ?)',
                               (mode, key_of(entry), account_id, str(nickname)[:64], self.clock()))
            self._merge_pulls(connection)
        self._run(work)

    # ---- each chat's roster ----

    def add(self, chat, mode, entry):
        """List `entry` (an account id or a nickname) in a chat's roster. 'added', 'listed' (it already was),
        'chat_full' or 'mode_full' (the night cannot sample more players of this mode)."""
        def work(connection):
            key = key_of(entry)
            if connection.execute('SELECT 1 FROM rosters WHERE chat=? AND mode=? AND entry=?', (chat, mode, key)).fetchone():
                return 'listed'
            if connection.execute('SELECT COUNT(*) FROM rosters WHERE chat=? AND mode=?', (chat, mode)).fetchone()[0] >= CHAT_PLAYERS:
                return 'chat_full'
            sampled = {row[0] for row in connection.execute('SELECT DISTINCT entry FROM rosters WHERE mode=?', (mode,))}
            if key not in sampled and len(sampled) >= MODE_PLAYERS:
                return 'mode_full'
            connection.execute('INSERT INTO rosters VALUES (?, ?, ?, ?, ?)', (chat, mode, key, str(entry).strip(), self.clock()))
            return 'added'
        return self._run(work)

    def remove(self, chat, mode, entry):
        """Take an entry off a chat's roster, by what was listed, its account id or its nickname. True when found."""
        def work(connection):
            wanted = key_of(entry)
            for listed, account_id, nickname in self._roster(connection, chat, mode):
                if wanted in (key_of(listed), key_of(nickname or '')) or (account_id is not None and wanted == str(account_id)):
                    connection.execute('DELETE FROM rosters WHERE chat=? AND mode=? AND entry=?', (chat, mode, key_of(listed)))
                    return True
            return False
        return self._run(work, False, create=False)

    @staticmethod
    def _roster(connection, chat, mode):
        rows = connection.execute(
            'SELECT r.label, p.account_id, p.nickname FROM rosters r LEFT JOIN players p ON p.mode=r.mode AND p.entry=r.entry '
            'WHERE r.chat=? AND r.mode=? ORDER BY r.added_at, r.entry', (chat, mode)).fetchall()
        # An account id is its own account, resolved or not.
        return [(label, int(label) if account_id is None and label.isascii() and label.isdigit() else account_id, name)
                for label, account_id, name in rows]

    def roster(self, chat, mode):
        """[(entry as listed, account id or None, nickname or None)] of a chat's roster, in the order added."""
        return self._run(lambda c: self._roster(c, chat, mode), [], create=False)

    def entries(self, chats):
        """[(mode, entry)] listed by any of `chats`, without repeats: what the nightly sampler pulls."""
        chats = list(chats)
        marks = ','.join('?'*len(chats))
        return self._run(lambda c: [tuple(row) for row in c.execute(
            f'SELECT mode, MIN(label) FROM rosters WHERE chat IN ({marks}) GROUP BY mode, entry '
            'ORDER BY MIN(added_at), mode, entry', chats)], [], create=False)

    def chats(self):
        """{chat: [modes it lists players in]}: who gets the weekly broadcast."""
        found = {}
        for chat, mode in self._run(lambda c: c.execute('SELECT DISTINCT chat, mode FROM rosters ORDER BY chat').fetchall(),
                                    [], create=False):
            found.setdefault(chat, []).append(mode)
        return {chat: [mode for mode in GAME_MODES if mode in modes] for chat, modes in found.items()}

    def listed(self, mode, account_id, nickname):
        """Whether any chat lists this account in `mode`. A listed nickname not resolved yet that equals `nickname`
        is resolved to the account on the way (a user's own lookup settles it before the nightly sampler does)."""
        def work(connection):
            key = key_of(nickname)
            if (connection.execute('SELECT 1 FROM rosters WHERE mode=? AND entry=?', (mode, key)).fetchone()
                    and not connection.execute('SELECT 1 FROM players WHERE mode=? AND entry=?', (mode, key)).fetchone()):
                connection.execute('INSERT INTO players VALUES (?, ?, ?, ?, ?)', (mode, key, account_id, str(nickname)[:64], self.clock()))
                self._merge_pulls(connection)
            return connection.execute(
                'SELECT 1 FROM rosters r LEFT JOIN players p ON p.mode=r.mode AND p.entry=r.entry '
                'WHERE r.mode=? AND (r.entry=? OR p.account_id=?) LIMIT 1', (mode, str(account_id), account_id)).fetchone() is not None
        return self._run(work, False, create=False)

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
