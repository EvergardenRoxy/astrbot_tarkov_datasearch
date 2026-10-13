"""Bindings (chat user -> game account) and the shared nickname -> account id record, in one SQLite file.

players.tarkov.dev only has a profile after someone looked the player up on the tarkov.dev website, and its daily
name index lags behind (handoff §16), so the plugin learns names from its users instead: every binding and every
successful query by account id or player-page link records the nickname the profile shows.
"""
import hashlib
import sqlite3
import time

# Recorded names kept at most; the least recently seen are dropped beyond this.
MAX_NAMES = 200000
MAX_MATCHES = 6


def owner_key(platform, sender):
    """The binding key of a chat user: one per platform, the same in every group (maintainer's choice). Hashed so
    the database holds no platform user ids."""
    return hashlib.sha256(f'{platform}\0{sender}'.encode()).hexdigest()


class Accounts:
    def __init__(self, path):
        self.path = path

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS bindings (owner TEXT PRIMARY KEY, aid INTEGER NOT NULL, nickname TEXT NOT NULL, bound_at REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS names (key TEXT NOT NULL, aid INTEGER NOT NULL, name TEXT NOT NULL, seen_at REAL NOT NULL, PRIMARY KEY (key, aid))')
        db.execute('CREATE INDEX IF NOT EXISTS names_seen ON names (seen_at)')
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
