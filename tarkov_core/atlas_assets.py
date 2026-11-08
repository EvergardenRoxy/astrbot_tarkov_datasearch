"""Local picture cache for the Atlas player picture: item pictures, achievement icons, level badges, prestige icons.

Pictures come only from https://assets.tarkov.dev (links in Atlas' documents, or in tarkov.dev's public `tasks` /
`items` data). Two ways in (maintainer, 2026-11-08):
- the one-shot setting downloads every common icon at once (all achievement icons, the level badges, the prestige
  icons), like the Boss portraits;
- anything else (item pictures, an icon the prefetch missed) is downloaded the first time a picture needs it.
Cached files never expire; only the one-shot "clear" setting removes them. A failed download draws a placeholder and
is not retried for FAILURE_SECONDS.
"""
import asyncio
import hashlib
import io
import json
import os
import re
import tempfile
import time

from PIL import Image

from . import transport

HOST = 'https://assets.tarkov.dev'
URL = re.compile(r'https://assets\.tarkov\.dev(/[A-Za-z0-9._-]{1,200}\.(?:png|webp|jpe?g))')
DOWNLOAD_LIMITS = (4*1024*1024, 4*1024*1024)
MAX_PIXELS = 16*1024*1024
# Stored pictures are scaled down to fit this box (item pictures are drawn at most ~300 px wide).
STORE_SIZE = 512
FAILURE_SECONDS = 10*60
# An achievement the saved catalog does not know (a new one in the game) rebuilds it, at most this often.
REBUILD_SECONDS = 6*3600
# Downloads at once, and at most per picture (the rest draw placeholders and come next time).
SLOTS = 4
PER_PICTURE = 48
MEMORY_ENTRIES = 256
CATALOG = 'catalog.json'
# Public data the catalog is built from: (resource, mode). Arena achievements are not in tarkov.dev's data.
FEEDS = (('tasks', 'pvp'), ('tasks', 'pve'), ('items', 'pvp'))


def sanitize(body):
    """PNG bytes of a downloaded picture, scaled into STORE_SIZE; raises on anything that is not a sane image."""
    if len(body) > DOWNLOAD_LIMITS[1]:
        raise ValueError('picture too large')
    with Image.open(io.BytesIO(body)) as source:
        if source.width*source.height > MAX_PIXELS:
            raise ValueError('picture dimensions too large')
        source.load()
        picture = source.convert('RGBA')
        picture.thumbnail((STORE_SIZE, STORE_SIZE))
        output = io.BytesIO()
        picture.save(output, format='PNG')
        return output.getvalue()


def valid(url):
    return isinstance(url, str) and bool(URL.fullmatch(url))


def build_catalog(feeds):
    """{'achievements': {id: url}, 'levels': [[level, url]], 'prestige': {level: url}} from parsed `data` objects of
    tarkov.dev's tasks and items feeds; links that are not on assets.tarkov.dev are dropped."""
    catalog = {'achievements': {}, 'levels': [], 'prestige': {}}
    for data in feeds:
        if not isinstance(data, dict):
            continue
        found = data.get('achievements')
        for entry in found.values() if isinstance(found, dict) else found if isinstance(found, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get('id'), str) and valid(entry.get('imageLink')):
                catalog['achievements'].setdefault(entry['id'][:40], entry['imageLink'])
        for entry in data.get('prestige') if isinstance(data.get('prestige'), list) else []:
            level = entry.get('prestigeLevel') if isinstance(entry, dict) else None
            link = entry.get('iconLink') if isinstance(entry, dict) else None
            if type(level) is int and 0 < level < 100 and valid(link):
                catalog['prestige'].setdefault(str(level), link)
        levels = data.get('playerLevels')
        if isinstance(levels, list) and not catalog['levels']:
            catalog['levels'] = [[entry['level'], entry['levelBadgeImageLink']] for entry in levels
                                 if isinstance(entry, dict) and type(entry.get('level')) is int
                                 and valid(entry.get('levelBadgeImageLink'))]
    return catalog


def level_badge(catalog, level):
    """The badge link of the highest table level not above `level`, or None."""
    best = None
    for row_level, link in catalog.get('levels', []) if level is not None else []:
        if row_level <= level and (best is None or row_level > best[0]):
            best = (row_level, link)
    return best[1] if best else None


def icon_urls(catalog):
    """Every common icon: achievement icons, level badges (each distinct), prestige icons."""
    return sorted(set(catalog.get('achievements', {}).values()) | {link for _, link in catalog.get('levels', [])}
                  | set(catalog.get('prestige', {}).values()))


class AtlasAssets:
    def __init__(self, root, feed, *, proxy=lambda: None, client=None, download=None, clock=time.monotonic):
        # feed(resource, mode) -> parsed `data` of a tarkov.dev public resource (the plugin's public cache).
        self.root, self.feed, self.proxy, self.client = root, feed, proxy, client
        self.download = download or self.fetch
        self.clock = clock
        self.memory, self.failed, self.pending = {}, {}, {}
        self.catalog_data = None
        self.rebuild_after = 0
        self.slots = asyncio.Semaphore(SLOTS)
        self.lock = asyncio.Lock()
        self.closed = False

    def path(self, url):
        return self.root/(hashlib.sha256(url.encode()).hexdigest()[:32]+'.png')

    async def fetch(self, url):
        buffer = io.BytesIO()
        await transport.http_get(HOST, URL.fullmatch(url).group(1), buffer, limits=DOWNLOAD_LIMITS, timeout_seconds=15,
                                 proxy=self.proxy(), client=self.client,
                                 content_types=('image/png', 'image/webp', 'image/jpeg', 'application/octet-stream'))
        return buffer.getvalue()

    # ---- catalog of icon links (from tarkov.dev's public data) ----

    def read_catalog(self):
        path = self.root/CATALOG
        if not path.exists() or path.stat().st_size > 4*1024*1024:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        return build_catalog([{'achievements': [{'id': k, 'imageLink': v} for k, v in data.get('achievements', {}).items()],
                               'prestige': [{'prestigeLevel': int(k), 'iconLink': v} for k, v in data.get('prestige', {}).items()
                                            if str(k).isdigit()],
                               'playerLevels': [{'level': l, 'levelBadgeImageLink': v} for l, v in data.get('levels', [])]}])

    def write_catalog(self, catalog):
        self.write(self.root/CATALOG, json.dumps(catalog, ensure_ascii=False).encode())

    async def catalog(self, *, rebuild=False):
        """The icon links: memory, then the saved catalog, then built from the public feeds (and saved)."""
        if self.catalog_data is not None and not rebuild:
            return self.catalog_data
        if not rebuild:
            try:
                saved = await asyncio.to_thread(self.read_catalog)
            except (OSError, ValueError, TypeError, AttributeError):
                saved = None
            if saved and saved['achievements']:
                self.catalog_data = saved
                return saved
        feeds = []
        for resource, mode in FEEDS:
            feeds.append(await self.feed(resource, mode))
        catalog = await asyncio.to_thread(build_catalog, feeds)
        if not self.closed:
            await asyncio.to_thread(self.write_catalog, catalog)
            self.catalog_data = catalog
        return catalog

    async def catalog_for(self, achievement_ids):
        """The catalog, rebuilt from the public feeds when it lacks one of `achievement_ids` (at most every
        REBUILD_SECONDS; a failed rebuild keeps the old catalog)."""
        catalog = await self.catalog()
        known = catalog.get('achievements', {})
        if any(key not in known for key in achievement_ids) and self.clock() >= self.rebuild_after:
            self.rebuild_after = self.clock()+REBUILD_SECONDS
            try:
                async with self.lock:
                    catalog = await self.catalog(rebuild=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        return catalog

    # ---- pictures ----

    def read(self, url):
        path = self.path(url)
        if not path.exists() or path.stat().st_size > 2*1024*1024:
            return None
        return path.read_bytes()

    def write(self, path, body):
        if self.closed:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.asset-', dir=self.root)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(body)
            if not self.closed:
                os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def remember(self, url, body):
        self.memory.pop(url, None)
        while len(self.memory) >= MEMORY_ENTRIES:
            self.memory.pop(next(iter(self.memory)))
        self.memory[url] = body

    async def load(self, url, *, download=True):
        """PNG bytes of one picture from memory or disk, else downloaded once (shared by concurrent callers)."""
        if url in self.memory:
            return self.memory[url]
        try:
            body = await asyncio.to_thread(self.read, url)
        except OSError:
            body = None
        if body:
            self.remember(url, body)
            return body
        if not download or self.clock() < self.failed.get(url, 0):
            return None
        task = self.pending.get(url)
        if task is None:
            task = asyncio.ensure_future(self.get_one(url))
            self.pending[url] = task
            task.add_done_callback(lambda done: (self.pending.pop(url, None), done.cancelled() or done.exception()))
        return await asyncio.shield(task)

    async def get_one(self, url):
        try:
            async with self.slots:
                raw = await self.download(url)
            body = await asyncio.to_thread(sanitize, raw)
            await asyncio.to_thread(self.write, self.path(url), body)
            self.failed.pop(url, None)
            self.remember(url, body)
            return body
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failed[url] = self.clock()+FAILURE_SECONDS
            return None

    async def images(self, urls):
        """{url: PNG bytes} for the valid links among `urls`; missing ones are downloaded (at most PER_PICTURE)."""
        wanted = [url for url in dict.fromkeys(urls) if valid(url)]
        found, missing = {}, []
        for url in wanted:
            body = await self.load(url, download=False)
            if body:
                found[url] = body
            else:
                missing.append(url)
        results = await asyncio.gather(*(self.load(url) for url in missing[:PER_PICTURE]), return_exceptions=True)
        for url, body in zip(missing, results):
            if isinstance(body, asyncio.CancelledError):
                raise body
            if isinstance(body, bytes):
                found[url] = body
        return found

    async def prefetch(self):
        """The one-shot setting: rebuild the catalog and download every common icon again. (downloaded, total)."""
        async with self.lock:
            catalog = await self.catalog(rebuild=True)
            urls = icon_urls(catalog)
            self.failed.clear()
            results = await asyncio.gather(*(self.get_one(url) for url in urls), return_exceptions=True)
            for result in results:
                if isinstance(result, asyncio.CancelledError):
                    raise result
            return sum(isinstance(result, bytes) for result in results), len(urls)

    def remove_all(self):
        removed = 0
        if self.root.exists():
            for path in self.root.iterdir():
                if path.is_file() and (path.suffix == '.png' or path.name == CATALOG or path.name.startswith('.asset-')):
                    path.unlink()
                    removed += 1
        return removed

    async def clear(self):
        """The one-shot "clear" setting: every cached picture and the catalog."""
        async with self.lock:
            self.memory.clear()
            self.failed.clear()
            self.catalog_data = None
            return await asyncio.to_thread(self.remove_all)

    async def close(self):
        self.closed = True
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.memory.clear()
