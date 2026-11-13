"""Boss portraits for the spawn table, kept on disk. Only the one-shot settings action downloads them."""
import asyncio
import io

from PIL import Image
from astrbot.api import logger

from . import transport

HOST = 'https://assets.tarkov.dev'
PORTRAITS = {'bossTagilla': 'tagilla-portrait.png', 'bossBully': 'reshala-portrait.webp',
             'bossKojaniy': 'shturman-portrait.png', 'bossSanitar': 'sanitar-portrait.png',
             'bossKilla': 'killa-portrait.png', 'bossGluhar': 'glukhar-portrait.png',
             'bossZryachiy': 'zryachiy-portrait.png', 'bossBoar': 'kaban-portrait.png',
             'bossKolontay': 'kollontay-portrait.png', 'bossPartisan': 'partisan-portrait.png',
             'sectantPriest': 'cultist-priest-portrait.webp', 'bossKnight': 'knight-portrait.png'}
LIMITS = (512*1024, 1024*1024)
MAX_PIXELS = 4*1024*1024


def sanitize(body):
    """PNG bytes of a 128 px thumbnail; raises on anything that is not a sane image."""
    with Image.open(io.BytesIO(body)) as source:
        if source.width*source.height > MAX_PIXELS:
            raise ValueError('portrait dimensions too large')
        thumb = source.convert('RGBA')
        thumb.thumbnail((128, 128))
        output = io.BytesIO()
        thumb.save(output, format='PNG')
        return output.getvalue()


class BossPortraits:
    def __init__(self, root, client, proxy=lambda: None):
        self.root, self.client, self.proxy = root, client, proxy
        self.cache = None

    async def download(self, name):
        buffer = io.BytesIO()
        await transport.http_get(HOST, '/'+name, buffer, client=self.client, limits=LIMITS, timeout_seconds=10,
                                 proxy=self.proxy(),
                                 content_types=('image/png', 'image/webp', 'image/jpeg', 'application/octet-stream'))
        return buffer.getvalue()

    def read_all(self):
        found = {}
        for mob_id in PORTRAITS:
            path = self.root/(mob_id+'.png')
            if path.exists():
                found[mob_id] = path.read_bytes()
        return found

    async def all(self):
        """{mob id: PNG bytes} of the portraits on disk (read once); never downloads."""
        if self.cache is None:
            try:
                self.cache = await asyncio.to_thread(self.read_all)
            except OSError:
                self.cache = {}
        return self.cache

    async def refresh_one(self, mob_id):
        try:
            body = await asyncio.to_thread(sanitize, await self.download(PORTRAITS[mob_id]))
            await asyncio.to_thread((self.root/(mob_id+'.png')).write_bytes, body)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('Tarkov portrait update failed: %s %s', mob_id, type(exc).__name__)
            return False

    async def refresh(self):
        """The one-shot settings action: download every portrait again. Returns how many succeeded."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)
        done = sum(await asyncio.gather(*(self.refresh_one(mob_id) for mob_id in PORTRAITS)))
        self.cache = None
        return done
