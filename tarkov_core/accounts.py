"""Bindings (chat user -> game account) and the shared nickname -> account id record, in one SQLite file.

players.tarkov.dev only has a profile after someone looked the player up on the tarkov.dev website, and its daily
name index lags behind (handoff §16), so the plugin learns names from its users instead: every binding and every
successful query by account id or player-page link records the nickname the profile shows.
"""
from contextlib import closing
import hashlib
import hmac
import os
import secrets
import sqlite3
import time

from .names import SUGGESTIONS, spelling_variants

# Recorded names kept at most; the least recently seen are dropped beyond this.
MAX_NAMES = 200000
MAX_MATCHES = 6
# Group sessions that used a command, kept as push targets when no target list is configured; the oldest are dropped.
MAX_SESSIONS = 200


SECRET_BYTES = 32


def load_secret(path):
    """This installation's random key for owner keys, created on first use (readable by the owner only).

    A plain hash of a QQ number can be reversed by trying every number (review R-11: 0.6 s for one); with a key kept
    outside the database, the database alone does not give the user ids away."""
    try:
        data = path.read_bytes()
        if len(data) >= SECRET_BYTES:
            return data
    except FileNotFoundError:
        pass
    data = secrets.token_bytes(SECRET_BYTES)
    temporary = path.with_name(path.name+'.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(data)
    os.replace(temporary, path)
    return data


def owner_key(secret, platform, sender):
    """The binding key of a chat user: one per platform, the same in every group (maintainer's choice). A keyed hash,
    so the database holds no platform user ids and they cannot be recovered from it alone."""
    return hmac.new(secret, f'{platform}\0{sender}'.encode(), hashlib.sha256).hexdigest()


class Accounts:
    def __init__(self, path):
        self.path = path

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS bindings (owner TEXT PRIMARY KEY, aid INTEGER NOT NULL, nickname TEXT NOT NULL, bound_at REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS names (key TEXT NOT NULL, aid INTEGER NOT NULL, name TEXT NOT NULL, seen_at REAL NOT NULL, PRIMARY KEY (key, aid))')
        db.execute('CREATE INDEX IF NOT EXISTS names_seen ON names (seen_at)')
        db.execute('CREATE TABLE IF NOT EXISTS sessions (umo TEXT PRIMARY KEY, seen_at REAL NOT NULL)')
        return db

    def bind(self, owner, aid, nickname):
        db = self.connect()
        try:
            with db:
                db.execute('INSERT OR REPLACE INTO bindings VALUES (?,?,?,?)', (owner, int(aid), nickname, time.time()))
        finally:
            db.close()

    def unbind(self, owner):
        """The (aid, nickname) that was bound, or None."""
        db = self.connect()
        try:
            with db:
                row = db.execute('SELECT aid, nickname FROM bindings WHERE owner=?', (owner,)).fetchone()
                db.execute('DELETE FROM bindings WHERE owner=?', (owner,))
            return row
        finally:
            db.close()

    def bound(self, owner):
        """(aid, nickname) bound to `owner`, or None."""
        db = self.connect()
        try:
            return db.execute('SELECT aid, nickname FROM bindings WHERE owner=?', (owner,)).fetchone()
        finally:
            db.close()

    def remember(self, aid, nickname):
        """Record that account `aid` showed `nickname`. Older names of the account are kept, so a player can still be
        found by a name they had before renaming."""
        name = str(nickname).strip()
        if not name:
            return
        db = self.connect()
        try:
            with db:
                db.execute('INSERT OR REPLACE INTO names VALUES (?,?,?,?)', (name.lower(), int(aid), name, time.time()))
                count = db.execute('SELECT COUNT(*) FROM names').fetchone()[0]
                if count > MAX_NAMES:
                    db.execute('DELETE FROM names WHERE rowid IN (SELECT rowid FROM names ORDER BY seen_at LIMIT ?)',
                               (count-MAX_NAMES,))
        finally:
            db.close()

    def similar(self, name):
        """[(aid, nickname)] recorded under a name that differs from `name` only by '_' and '-', most recent first."""
        variants = spelling_variants(name)
        if not variants:
            return []
        db = self.connect()
        try:
            marks = ','.join('?'*len(variants))
            return db.execute(f'SELECT aid, name FROM names WHERE key IN ({marks}) ORDER BY seen_at DESC LIMIT ?',
                              (*variants, SUGGESTIONS)).fetchall()
        finally:
            db.close()

    def lookup(self, name):
        """([(aid, nickname)], total) recorded for `name` ignoring case: exact-case first, then most recently seen."""
        key = name.strip().lower()
        db = self.connect()
        try:
            rows = db.execute('SELECT aid, name FROM names WHERE key=? ORDER BY name<>? , seen_at DESC LIMIT ?',
                              (key, name.strip(), MAX_MATCHES)).fetchall()
            total = db.execute('SELECT COUNT(*) FROM names WHERE key=?', (key,)).fetchone()[0]
            return rows, total
        finally:
            db.close()

    def touch_session(self, umo, now=None):
        """Remember a group session (its AstrBot conversation id, no user id) that used the plugin."""
        now = time.time() if now is None else now
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO sessions VALUES (?,?) ON CONFLICT(umo) DO UPDATE SET seen_at=excluded.seen_at', (umo, now))
            db.execute('DELETE FROM sessions WHERE umo NOT IN (SELECT umo FROM sessions ORDER BY seen_at DESC LIMIT ?)', (MAX_SESSIONS,))

    def sessions(self, limit=50):
        """The most recently active remembered sessions, newest first."""
        with closing(self.connect()) as db:
            return [row[0] for row in db.execute('SELECT umo FROM sessions ORDER BY seen_at DESC LIMIT ?', (limit,))]
