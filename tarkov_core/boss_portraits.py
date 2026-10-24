"""Year-long local portrait cache; only the explicit settings action downloads assets."""
import asyncio
import io
import os
import tempfile
import time

from PIL import Image
from astrbot.api import logger

from . import transport

HOST = 'https://assets.tarkov.dev'
CACHE_SECONDS = 365*86400
PORTRAITS = {'bossTagilla':'tagilla-portrait.png', 'bossBully':'reshala-portrait.webp',
    'bossKojaniy':'shturman-portrait.png', 'bossSanitar':'sanitar-portrait.png', 'bossKilla':'killa-portrait.png',
    'bossGluhar':'glukhar-portrait.png', 'bossZryachiy':'zryachiy-portrait.png', 'bossBoar':'kaban-portrait.png',
    'bossKolontay':'kollontay-portrait.png', 'bossPartisan':'partisan-portrait.png',
    'sectantPriest':'cultist-priest-portrait.webp', 'bossKnight':'knight-portrait.png'}


def sanitize(body):
    if len(body) > 1024*1024: raise ValueError('portrait too large')
    with Image.open(io.BytesIO(body)) as source:
        if source.width*source.height > 4*1024*1024: raise ValueError('portrait dimensions too large')
        source.load()
        thumb = source.convert('RGBA'); thumb.thumbnail((128,128))
        output = io.BytesIO(); thumb.save(output,format='PNG')
        return output.getvalue()


class BossPortraits:
    def __init__(self,root,download=None,clock=time.time):
        self.root,self.download,self.clock = root,download or self.fetch,clock
        self.cache,self.pending,self.workers,self.downloads = {},{},set(),set()
        self.slots,self.lock = asyncio.Semaphore(3),asyncio.Lock()
        self.closed = False

    async def thread(self,function,*args):
        task = asyncio.create_task(asyncio.to_thread(function,*args))
        self.workers.add(task); task.add_done_callback(self.workers.discard)
        task.add_done_callback(lambda done:None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    @staticmethod
    async def fetch(path,proxy=None):
        buffer = io.BytesIO()
        result = await transport.http_get(HOST,'/'+path,buffer,limits=(512*1024,1024*1024),
                                          timeout_seconds=10,proxy=proxy,
                                          content_types=('image/png','image/webp','image/jpeg','application/octet-stream'))
        if result['status'] != 200: raise ValueError('portrait response')
        return buffer.getvalue()

    def read(self,path):
        if not path.exists(): return None
        stat = path.stat()
        if stat.st_size > 256*1024 or not 0 <= self.clock()-stat.st_mtime < CACHE_SECONDS: return None
        return sanitize(path.read_bytes()),stat.st_mtime

    def write(self,path,body):
        if self.closed: return
        self.root.mkdir(parents=True,exist_ok=True)
        descriptor,temporary = tempfile.mkstemp(prefix='.portrait-',dir=self.root)
        try:
            with os.fdopen(descriptor,'wb') as output: output.write(body)
            os.utime(temporary,(self.clock(),self.clock()))
            if not self.closed: os.replace(temporary,path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    async def load_local(self,mob_id):
        try:
            result = await self.thread(self.read,self.root/(mob_id+'.png'))
        except Exception:
            result = None
        if not self.closed: self.cache[mob_id] = result or (None,self.clock())

    async def all(self):
        """Disk/memory only, including when missing or expired. Never follows an upstream URL."""
        async with self.lock:
            if self.closed: return {}
            for mob_id in PORTRAITS:
                cached = self.cache.get(mob_id)
                if cached and 0 <= self.clock()-cached[1] < CACHE_SECONDS: continue
                if mob_id not in self.pending:
                    task = asyncio.create_task(self.load_local(mob_id))
                    self.pending[mob_id] = task
                    task.add_done_callback(lambda done,key=mob_id:self.pending.pop(key,None))
            await asyncio.gather(*(asyncio.shield(task) for task in list(self.pending.values())))
            return {key:value[0] for key,value in self.cache.items() if value[0] is not None}

    async def refresh_one(self,mob_id,proxy):
        try:
            async with self.slots:
                raw = await self.download(PORTRAITS[mob_id],proxy=proxy)
                body = await self.thread(sanitize,raw)
                await self.thread(self.write,self.root/(mob_id+'.png'),body)
                if not self.closed: self.cache[mob_id] = (body,self.clock())
                return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('Tarkov portrait update failed: %s %s',mob_id,type(exc).__name__)
            return False

    async def refresh(self,proxy=None):
        """Explicit one-shot settings action, even for currently fresh files."""
        async with self.lock:
            if self.closed: return 0
            tasks = [asyncio.create_task(self.refresh_one(mob_id,proxy)) for mob_id in PORTRAITS]
            self.downloads.update(tasks)
            for task in tasks: task.add_done_callback(self.downloads.discard)
            return sum(await asyncio.gather(*tasks))

    async def close(self):
        self.closed = True
        pending = [*self.pending.values(),*self.downloads]
        for task in pending: task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
        await asyncio.gather(*list(self.workers),return_exceptions=True)
        self.cache.clear()
