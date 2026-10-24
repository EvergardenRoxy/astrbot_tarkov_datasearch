"""Atomic SQLite item/name index and frozen image-page queries, built on the public resource cache."""
import asyncio
import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import closing

from .items import normalized, project_item, validate_items
from .notifications import NotificationStore
from .public_api import ITEM_ID, DataError
from .transport import ConnectionFailure, HTTPError

SNAPSHOT_SECONDS = 3600
MAX_SNAPSHOT_BYTES = 16*1024*1024
CATALOG_VERSION = b'item-catalog-v1'


class ItemCatalog(NotificationStore):
    def __init__(self,path):
        super().__init__(path)
        self.pending = {}
        self.build_slots = asyncio.Semaphore(1)

    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db = sqlite3.connect(self.path,timeout=15)
        db.executescript('''
            CREATE TABLE IF NOT EXISTS versions(mode TEXT PRIMARY KEY, signature TEXT, metadata TEXT);
            CREATE TABLE IF NOT EXISTS items(mode TEXT, id TEXT, payload TEXT, PRIMARY KEY(mode,id));
            CREATE TABLE IF NOT EXISTS names(mode TEXT,id TEXT,name TEXT,key TEXT);
            CREATE INDEX IF NOT EXISTS item_name_key ON names(mode,key);
            CREATE INDEX IF NOT EXISTS item_name_exact ON names(mode,name);
            CREATE TABLE IF NOT EXISTS queries(token TEXT PRIMARY KEY,payload TEXT,created_at REAL);
        ''')
        return db

    def version(self,mode):
        with closing(self.connect()) as db:
            row = db.execute('SELECT signature,metadata FROM versions WHERE mode=?',(mode,)).fetchone()
            return (row[0],json.loads(row[1])) if row else None

    def publish(self,mode,signature,metadata,rows=None):
        if self.closed: return
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if rows is not None:
                db.execute('DELETE FROM items WHERE mode=?',(mode,)); db.execute('DELETE FROM names WHERE mode=?',(mode,))
                db.executemany('INSERT INTO items VALUES (?,?,?)',[(mode,row['id'],json.dumps(row,ensure_ascii=False)) for row in rows])
                db.executemany('INSERT INTO names VALUES (?,?,?,?)',[(mode,row['id'],name,normalized(name)) for row in rows for name in row['aliases']])
            db.execute('INSERT OR REPLACE INTO versions VALUES (?,?,?)',(mode,signature,json.dumps(metadata,ensure_ascii=False)))

    async def ensure(self,mode,loader):
        if self.closed: raise RuntimeError('物品目录已关闭')
        task = self.pending.get(mode)
        if task is None:
            task = asyncio.create_task(self.rebuild(mode,loader)); self.pending[mode] = task
            task.add_done_callback(lambda done,key=mode:self.pending.pop(key,None))
            task.add_done_callback(lambda done:None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    async def rebuild(self,mode,loader):
        previous = await self.thread(self.version,mode)
        try:
            bundle = await loader(mode)
            snapshot = bundle['base']
            translations = bundle.get('translations',[])
            # Serialize large JSON parses/builds; downloads remain shared by ResourceCache's own limits.
            async with self.build_slots:
                signature = await self.thread(lambda:hashlib.sha256(b'\0'.join([CATALOG_VERSION,snapshot.body,*[s.body if s else b'' for s in translations]])).hexdigest())
                metadata = {'mode':mode,'checked_at':snapshot.checked_at,'fetched_at':snapshot.fetched_at,
                    'last_modified':snapshot.last_modified,'stale':snapshot.stale,'from_cache':snapshot.from_cache,
                    'translation_warning':any(t is None or t.stale for t in translations)}
                if previous and signature == previous[0]:
                    metadata['flea'] = previous[1].get('flea',{})
                    await self.thread(self.publish,mode,signature,metadata)
                    return metadata
                document = await snapshot.json()
                dictionaries = [(await t.json())['data'] for t in translations if t is not None]
                def build():
                    data = document['data']; validate_items(data)
                    if not data['items']: raise DataError('物品目录为空，保留上次成功索引')
                    return [project_item(item_id,item,data,dictionaries) for item_id,item in data['items'].items()]
                rows = await self.thread(build)
                metadata['flea'] = document['data'].get('fleaMarket') or {}
                await self.thread(self.publish,mode,signature,metadata,rows)
                return metadata
        except asyncio.CancelledError:
            raise
        except (DataError,ConnectionFailure,HTTPError,TimeoutError):
            if not previous: raise
            return {**previous[1],'stale':True,'index_fallback':True}

    def search(self,query,modes,limit=25):
        if not modes: return [],0
        marks = ','.join('?' for _ in modes)
        with closing(self.connect()) as db:
            if ITEM_ID.fullmatch(query.lower()):
                ids = db.execute(f'SELECT DISTINCT id FROM items WHERE mode IN ({marks}) AND id=?',(*modes,query.lower())).fetchall()
                return [row[0] for row in ids],len(ids)
            for expression,value in (('name=?',query),('key=?',normalized(query)),('instr(key,?)>0',normalized(query))):
                where = f'mode IN ({marks}) AND {expression}'
                count = db.execute('SELECT COUNT(DISTINCT id) FROM names WHERE '+where,(*modes,value)).fetchone()[0]
                if count:
                    ids = db.execute('SELECT id FROM names WHERE '+where+" GROUP BY id ORDER BY MIN(CASE "
                        "WHEN instr(key,?)=1 THEN 0 WHEN instr(' '||key,?)>0 THEN 1 ELSE 2 END),id LIMIT ?",
                        (*modes,value,normalized(query),' '+normalized(query),limit)).fetchall()
                    return [row[0] for row in ids],count
        return [],0

    def get(self,item_id,modes):
        if not modes: return {}
        with closing(self.connect()) as db:
            return {mode:json.loads(payload) for mode,payload in db.execute(
                f"SELECT mode,payload FROM items WHERE id=? AND mode IN ({','.join('?' for _ in modes)})",(item_id,*modes))}

    def prune_queries(self,db,now):
        db.execute('DELETE FROM queries WHERE created_at<?',(now-SNAPSHOT_SECONDS,))
        while True:
            count,size = db.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0) FROM queries').fetchone()
            if count <= 16 and size <= MAX_SNAPSHOT_BYTES: break
            db.execute('DELETE FROM queries WHERE rowid=(SELECT rowid FROM queries ORDER BY created_at,rowid LIMIT 1)')

    def save_pages(self,pages,now=None):
        now = time.time() if now is None else now
        token = secrets.token_hex(6); payload = json.dumps(pages,ensure_ascii=False)
        if len(payload.encode()) > MAX_SNAPSHOT_BYTES: raise DataError('物品查询结果超过快照上限，请缩小查询范围')
        with closing(self.connect()) as db,db:
            db.execute('INSERT INTO queries VALUES (?,?,?)',(token,payload,now)); self.prune_queries(db,now)
        return token

    def pages(self,token,now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as db,db:
            self.prune_queries(db,now)
            row = db.execute('SELECT payload FROM queries WHERE token=?',(token,)).fetchone()
            return json.loads(row[0]) if row else None

    async def close(self):
        self.closed = True
        tasks = list(self.pending.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await super().close()
