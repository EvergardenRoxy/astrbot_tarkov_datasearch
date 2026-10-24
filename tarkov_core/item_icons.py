"""Optional on-demand item thumbnails; separate from the manual-only Boss portrait cache."""
import asyncio
import threading

from .boss_portraits import BossPortraits, sanitize
from .public_api import ITEM_ID

TTL = 7*86400


class ItemIcons(BossPortraits):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.disk_lock = threading.Lock()

    def store_icon(self,path,body):
        with self.disk_lock:
            self.write(path,body)
            self.prune()

    def remember(self,item_id,value):
        if self.closed: return
        self.cache[item_id] = value
        while len(self.cache) > 128: self.cache.pop(next(iter(self.cache)))

    def prune(self):
        files = sorted((p for p in self.root.glob('*.png') if ITEM_ID.fullmatch(p.stem)),key=lambda p:p.stat().st_mtime)
        size = sum(p.stat().st_size for p in files)
        while files and (len(files) > 128 or size > 32*1024*1024):
            oldest = files.pop(0); size -= oldest.stat().st_size; oldest.unlink(missing_ok=True)

    async def get(self,item_id,proxy=None):
        if self.closed or not ITEM_ID.fullmatch(item_id): return None
        cached = self.cache.get(item_id)
        if cached and 0 <= self.clock()-cached[1] < (TTL if cached[0] else 60): return cached[0]
        task = self.pending.get(item_id)
        if task is None:
            if len(self.pending) >= 8: return None
            task = asyncio.create_task(self.load_icon(item_id,proxy)); self.pending[item_id] = task
            task.add_done_callback(lambda done:self.pending.pop(item_id,None))
        return await asyncio.shield(task)

    async def load_icon(self,item_id,proxy):
        try:
            async with self.slots:
                path = self.root/(item_id+'.png')
                try: local = await self.thread(self.read,path)
                except (OSError,ValueError): local = None
                if local and 0 <= self.clock()-local[1] < TTL:
                    body,stamp = local
                else:
                    try: raw = await self.download(item_id+'-512.webp',proxy=proxy)
                    except Exception: raw = await self.download(item_id+'-icon.webp',proxy=proxy)
                    body = await self.thread(sanitize,raw)
                    await self.thread(self.store_icon,path,body)
                    stamp = self.clock()
                self.remember(item_id,(body,stamp))
                return body
        except asyncio.CancelledError:
            raise
        except Exception:
            self.remember(item_id,(None,self.clock()))
            return None
