"""Boss-specific atomic baselines, immutable tables and per-page submission state."""
import asyncio
import json
import secrets
import sqlite3
import time
from contextlib import closing

from .boss_monitor import BOSS_SCOPE, build_table
from .boss_table_image import change_lines, paginate_table
from .notifications import MAX_EVENT_BYTES, MAX_EVENTS, RETENTION_SECONDS, NotificationStore, PermanentDeliveryError

QUERY_SECONDS = 86400
MAX_QUERY_BYTES = 32*1024*1024
PAGES_PER_CYCLE = 5


class BossStore(NotificationStore):
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS baselines (mode TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, mode TEXT, payload TEXT, created_at REAL);
            CREATE TABLE IF NOT EXISTS deliveries (
                event_id INTEGER REFERENCES events(id) ON DELETE CASCADE, target TEXT, page INTEGER,
                state TEXT, attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0, error TEXT,
                PRIMARY KEY(event_id,target,page));
            CREATE INDEX IF NOT EXISTS boss_delivery_due ON deliveries(state,next_at);
            CREATE TABLE IF NOT EXISTS blocked_targets (target TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS queries (token TEXT PRIMARY KEY, payload TEXT, created_at REAL);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        ''')
        with db:
            db.execute('BEGIN IMMEDIATE')
            scope = db.execute("SELECT value FROM metadata WHERE key='scope'").fetchone()
            if not scope or scope[0] != BOSS_SCOPE:
                # A changed inclusion policy is not an observed rate transition. Retire tables with ordinary AI.
                db.execute('DELETE FROM baselines'); db.execute('DELETE FROM events'); db.execute('DELETE FROM queries')
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('scope',?)", (BOSS_SCOPE,))
        return db

    @staticmethod
    def prune(db, now):
        db.execute('DELETE FROM events WHERE created_at<?', (now-RETENTION_SECONDS,))
        db.execute('DELETE FROM queries WHERE created_at<?', (now-QUERY_SECONDS,))
        for table, count_limit, size_limit in (('events', MAX_EVENTS, MAX_EVENT_BYTES), ('queries', 16, MAX_QUERY_BYTES)):
            while True:
                count, size = db.execute(f'SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0) FROM {table}').fetchone()
                if count <= count_limit and size <= size_limit: break
                db.execute(f'DELETE FROM {table} WHERE rowid=(SELECT rowid FROM {table} ORDER BY created_at,rowid LIMIT 1)')

    def reconcile(self, modes, targets):
        with closing(self.connect()) as db, db:
            for table in ('baselines', 'events'):
                if modes:
                    db.execute(f"DELETE FROM {table} WHERE mode NOT IN ({','.join('?' for _ in modes)})", tuple(modes))
                else:
                    db.execute(f'DELETE FROM {table}')
            if targets:
                marks = ','.join('?' for _ in targets)
                db.execute(f'DELETE FROM deliveries WHERE target NOT IN ({marks})', tuple(targets))
                db.execute(f'DELETE FROM blocked_targets WHERE target NOT IN ({marks})', tuple(targets))
            else:
                db.execute('DELETE FROM deliveries'); db.execute('DELETE FROM blocked_targets')

    def observe(self, card, targets, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT payload FROM baselines WHERE mode=?', (card['mode'],)).fetchone()
            before = json.loads(previous[0]) if previous else None
            rows, changed = build_table(before['state'] if before else None, card['state'])
            db.execute('INSERT OR REPLACE INTO baselines VALUES (?,?)', (card['mode'], json.dumps(card, ensure_ascii=False)))
            event_id = None
            if before is not None and changed:
                meta = {key: value for key, value in card.items() if key != 'state'}
                pages = paginate_table({**meta, 'compared_at': before['checked_at']}, rows)
                token = secrets.token_hex(6)
                for page in pages:
                    page['snapshot_token'] = token
                    page['change_lines'] = change_lines(rows, {column['map_id'] for column in page['columns']})
                payload = json.dumps(pages, ensure_ascii=False)
                db.execute('INSERT INTO queries VALUES (?,?,?)', (token, payload, now))
                event_id = db.execute('INSERT INTO events(mode,payload,created_at) VALUES (?,?,?)',
                                      (card['mode'], payload, now)).lastrowid
                db.executemany('INSERT INTO deliveries(event_id,target,page,state) VALUES (?,?,?,?)',
                               [(event_id, target, page, 'pending') for target in dict.fromkeys(targets) for page in range(len(pages))])
            self.prune(db, now)
            return event_id

    def latest(self, mode, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as db, db:
            self.prune(db, now)
            row = db.execute('SELECT payload FROM events WHERE mode=? ORDER BY id DESC LIMIT 1', (mode,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_query(self, pages, now=None):
        now = time.time() if now is None else now
        token = secrets.token_hex(6)
        payload = json.dumps(pages, ensure_ascii=False)
        if len(payload.encode()) > MAX_QUERY_BYTES:
            raise ValueError('Boss 表格快照超过大小上限')
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO queries VALUES (?,?,?)', (token, payload, now))
            self.prune(db, now)
        return token

    def query(self, token, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as db, db:
            self.prune(db, now)
            row = db.execute('SELECT payload FROM queries WHERE token=?', (token,)).fetchone()
            return json.loads(row[0]) if row else None

    def due(self, targets, modes, now=None):
        now = time.time() if now is None else now
        if not targets or not modes: return []
        with closing(self.connect()) as db, db:
            self.prune(db, now)
            # Preserve event/page order, while using the page budget across modes in the same cycle.
            groups = db.execute("SELECT DISTINCT target FROM deliveries WHERE state='pending' "
                                "AND target NOT IN (SELECT target FROM blocked_targets) GROUP BY target").fetchall()
            result, payloads = [], {}
            for (target,) in groups:
                if target not in targets: continue
                pending = db.execute("SELECT event_id,page,next_at FROM deliveries WHERE target=? "
                                     "AND state='pending' ORDER BY event_id,page LIMIT ?", (target, PAGES_PER_CYCLE)).fetchall()
                for event_id, page, next_at in pending:
                    if next_at > now: break
                    if event_id not in payloads:
                        mode, payload = db.execute('SELECT mode,payload FROM events WHERE id=?', (event_id,)).fetchone()
                        if mode not in modes: continue
                        payloads[event_id] = json.loads(payload)
                        token = payloads[event_id][0].get('snapshot_token')
                        if token: db.execute('INSERT OR REPLACE INTO queries VALUES (?,?,?)', (token, payload, now))
                    result.append((event_id, target, page, payloads[event_id]))
            self.prune(db, now)
            return result

    def record_page(self, event_id, target, page, outcome, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as db, db:
            row = db.execute('SELECT attempts FROM deliveries WHERE event_id=? AND target=? AND page=?',
                             (event_id, target, page)).fetchone()
            if row is None: return
            attempts = row[0]+1
            state = outcome if outcome in ('submitted', 'unknown', 'blocked') else 'failed' if attempts >= 3 else 'pending'
            next_at = now+min(1800, 120*2**(attempts-1)) if state == 'pending' else 0
            db.execute('UPDATE deliveries SET state=?,attempts=?,next_at=?,error=? WHERE event_id=? AND target=? AND page=?',
                       (state, attempts, next_at, None if outcome == 'submitted' else outcome, event_id, target, page))
            if state == 'blocked': db.execute('INSERT OR IGNORE INTO blocked_targets VALUES (?)', (target,))

    def retire(self):
        if not self.path.exists(): return
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM baselines'); db.execute('DELETE FROM events'); db.execute('DELETE FROM blocked_targets')


class BossNotifications:
    def __init__(self, store, fetch, prepare, send, modes, targets, enabled, on_error):
        self.store, self.fetch, self.prepare, self.send = store, fetch, prepare, send
        self.modes, self.targets, self.enabled, self.on_error = modes, targets, enabled, on_error
        self.lock = asyncio.Lock()

    async def cycle(self):
        async with self.lock:
            if not self.enabled(): return
            await self.store.thread(self.store.reconcile, self.modes(), self.targets())
            failure = None
            # Mode failures are isolated: a failed PvE request cannot swallow a successful PvP transition.
            for mode in self.modes():
                if not self.enabled(): break
                try:
                    card = await self.fetch(mode)
                    if card.get('stale'): raise ValueError('stale Boss snapshot')
                    if self.enabled() and mode in self.modes():
                        await self.store.thread(self.store.observe, card, self.targets())
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failure = exc
                    self.on_error(exc)
            due = await self.store.thread(self.store.due, self.targets(), self.modes()) if self.enabled() else []
            artifacts, stopped = {}, set()
            for event_id, target, page, pages in due:
                if target in stopped or not self.enabled() or target not in self.targets() or pages[page]['mode'] not in self.modes(): continue
                try:
                    key = (event_id, page)
                    if key not in artifacts: artifacts[key] = await self.prepare(pages[page], page+1, len(pages))
                    result = await asyncio.wait_for(self.send(target, artifacts[key]), timeout=20)
                    outcome = 'submitted' if result is True else 'unknown' if result is None else 'platform_unavailable'
                except asyncio.CancelledError:
                    raise
                except (PermanentDeliveryError, PermissionError) as exc:
                    outcome = 'blocked'; self.on_error(exc)
                except Exception as exc:
                    outcome = type(exc).__name__; self.on_error(exc)
                await self.store.thread(self.store.record_page, event_id, target, page, outcome)
                if outcome != 'submitted': stopped.add(target)
            if failure: raise failure
