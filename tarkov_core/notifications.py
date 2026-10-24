"""Durable status baselines and per-target submission attempts, separate from account bindings."""
import asyncio
import json
import sqlite3
import time
from contextlib import closing

from .server_status import status_changes

TOPIC = 'status'
MAX_EVENTS = 200
MAX_EVENT_BYTES = 64*1024*1024
RETENTION_SECONDS = 7*86400


class PermanentDeliveryError(Exception):
    pass


class NotificationStore:
    def __init__(self, path):
        self.path = path
        self.workers = set()
        self.closed = False

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS baselines (topic TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS seen_messages (topic TEXT, key TEXT, seen_at REAL, PRIMARY KEY(topic,key));
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, topic TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries (
                event_id INTEGER REFERENCES events(id) ON DELETE CASCADE, target TEXT, state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0, error TEXT,
                PRIMARY KEY(event_id,target));
            CREATE INDEX IF NOT EXISTS delivery_due ON deliveries(state,next_at);
            CREATE TABLE IF NOT EXISTS blocked_targets (target TEXT PRIMARY KEY);
        ''')
        return db

    async def thread(self, function, *args):
        if self.closed:
            raise RuntimeError('通知记录已关闭')
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self.workers.add(task)
        task.add_done_callback(self.workers.discard)
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    def observe(self, card, targets, now=None):
        """Atomically advance a successful baseline and queue an immutable event. First observation is silent."""
        now = time.time() if now is None else now
        state = card['state']
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM baselines WHERE topic=?', (TOPIC,)).fetchone()
            before = json.loads(row[0]) if row else None
            seen = {row[0] for row in db.execute('SELECT key FROM seen_messages WHERE topic=?', (TOPIC,))}
            changes, notices = status_changes(before['state'], state, seen) if before else ([], [])
            db.execute('INSERT OR REPLACE INTO baselines VALUES (?,?)', (TOPIC, json.dumps(card, ensure_ascii=False)))
            db.executemany('INSERT OR REPLACE INTO seen_messages VALUES (?,?,?)',
                           [(TOPIC, message['key'], now) for message in state['messages']])
            db.execute('DELETE FROM seen_messages WHERE rowid IN '
                       '(SELECT rowid FROM seen_messages WHERE topic=? ORDER BY seen_at DESC LIMIT -1 OFFSET 2048)', (TOPIC,))
            event_id = None
            if changes:
                payload = {**card, 'changes': changes, 'notices': notices, 'compared_at': before['checked_at']}
                event_id = db.execute('INSERT INTO events(topic,payload,created_at) VALUES (?,?,?)',
                                      (TOPIC, json.dumps(payload, ensure_ascii=False), now)).lastrowid
                db.executemany('INSERT INTO deliveries(event_id,target,state) VALUES (?,?,?)',
                               [(event_id, target, 'pending') for target in dict.fromkeys(targets)])
            db.execute('DELETE FROM events WHERE created_at<?', (now-RETENTION_SECONDS,))
            while True:
                count, size = db.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0) FROM events').fetchone()
                if count <= MAX_EVENTS and size <= MAX_EVENT_BYTES:
                    break
                db.execute('DELETE FROM events WHERE id=(SELECT MIN(id) FROM events)')
            return event_id

    def due(self, targets, now=None):
        now = time.time() if now is None else now
        if not targets:
            return []
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM events WHERE created_at<?', (now-RETENTION_SECONDS,))
            # Removing targets retires their pending deliveries; re-adding one must not replay an old backlog.
            marks = ','.join('?' for _ in targets)
            db.execute(f"DELETE FROM deliveries WHERE state='pending' AND target NOT IN ({marks})", tuple(targets))
            db.execute(f'DELETE FROM blocked_targets WHERE target NOT IN ({marks})', tuple(targets))
            rows = db.execute("SELECT d.event_id,d.target,e.payload FROM deliveries d JOIN events e ON e.id=d.event_id "
                              "WHERE d.state='pending' AND d.next_at<=? AND e.topic=? "
                              "AND d.target NOT IN (SELECT target FROM blocked_targets) ORDER BY e.id,d.target LIMIT 100",
                              (now, TOPIC)).fetchall()
            return [(event_id, target, json.loads(payload)) for event_id, target, payload in rows if target in targets]

    def record(self, event_id, target, outcome, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as db, db:
            row = db.execute('SELECT attempts FROM deliveries WHERE event_id=? AND target=?', (event_id, target)).fetchone()
            if row is None:
                return
            attempts = row[0]+1
            if outcome in ('submitted', 'unknown', 'blocked'):
                state, next_at = outcome, 0
            else:
                state = 'failed' if attempts >= 3 else 'pending'
                next_at = now+min(1800, 120*2**(attempts-1))
            db.execute('UPDATE deliveries SET state=?,attempts=?,next_at=?,error=? WHERE event_id=? AND target=?',
                       (state, attempts, next_at, None if outcome == 'submitted' else outcome, event_id, target))
            if outcome == 'blocked':
                db.execute('INSERT OR IGNORE INTO blocked_targets VALUES (?)', (target,))

    def retire(self):
        if not self.path.exists():
            return
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM baselines WHERE topic=?', (TOPIC,))
            db.execute('DELETE FROM events WHERE topic=?', (TOPIC,))
            db.execute('DELETE FROM blocked_targets')

    async def close(self):
        self.closed = True
        await asyncio.gather(*list(self.workers), return_exceptions=True)


class StatusNotifications:
    def __init__(self, store, fetch, prepare, send, targets, enabled, on_error):
        self.store, self.fetch, self.prepare, self.send = store, fetch, prepare, send
        self.targets, self.enabled, self.on_error = targets, enabled, on_error
        self.lock = asyncio.Lock()

    async def cycle(self):
        async with self.lock:
            targets = self.targets()
            if not self.enabled() or not targets:
                return
            failure = None
            try:
                card = await self.fetch()
                if card.get('stale'):
                    raise ValueError('stale data cannot advance a notification baseline')
                if self.enabled():
                    await self.store.thread(self.store.observe, card, self.targets())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = exc
            # Previous queued events can still be submitted when a later upstream check fails.
            rows = await self.store.thread(self.store.due, self.targets()) if self.enabled() else []
            artifacts = {}
            used_targets = set()
            for event_id, target, payload in rows:
                if not self.enabled() or target not in self.targets():
                    continue
                # At most one event per target per polling cycle: preserve intermediate incidents without flooding.
                if target in used_targets:
                    continue
                used_targets.add(target)
                try:
                    if event_id not in artifacts:
                        artifacts[event_id] = await self.prepare(payload)
                    result = await asyncio.wait_for(self.send(target, artifacts[event_id]), timeout=20)
                    outcome = 'submitted' if result is True else 'unknown' if result is None else 'platform_unavailable'
                except asyncio.CancelledError:
                    raise
                except (PermanentDeliveryError, PermissionError) as exc:
                    outcome = 'blocked'
                    self.on_error(exc)
                except Exception as exc:
                    outcome = type(exc).__name__
                    self.on_error(exc)
                await self.store.thread(self.store.record, event_id, target, outcome)
            if failure is not None:
                raise failure
