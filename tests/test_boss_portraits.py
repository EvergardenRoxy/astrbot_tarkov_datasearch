import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from PIL import Image

from tarkov_core import boss_portraits
from tarkov_core.boss_portraits import BossPortraits, CACHE_SECONDS, PORTRAITS, sanitize
from tarkov_core.transport import QueryError


def portrait():
    output = io.BytesIO(); Image.new('RGBA',(300,300),(100,100,100,255)).save(output,format='PNG')
    return output.getvalue()


class PortraitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.now = [1800000000]; self.calls = []
        async def download(path,**kwargs): self.calls.append((path,kwargs)); return portrait()
        self.store = BossPortraits(Path(self.temp.name)/'portraits',download,clock=lambda:self.now[0])

    async def asyncTearDown(self):
        await self.store.close(); self.temp.cleanup()

    async def test_only_manual_refresh_downloads_and_one_year_expiry_never_triggers_network(self):
        self.assertEqual(await self.store.all(),{})
        self.assertEqual(self.calls,[])
        self.assertFalse(self.store.root.exists())
        self.assertEqual(await self.store.refresh(proxy='http://proxy.example'),12)
        self.assertEqual({path for path,_ in self.calls},set(PORTRAITS.values()))
        self.assertEqual(len(await self.store.all()),12)
        self.assertEqual(len(self.calls),12)
        for body in (await self.store.all()).values():
            self.assertLessEqual(max(Image.open(io.BytesIO(body)).size),128)
        self.now[0] += CACHE_SECONDS+1
        self.assertEqual(await self.store.all(),{})
        self.assertEqual(len(self.calls),12)
        self.assertEqual(await self.store.refresh(),12)
        self.assertEqual(len(await self.store.all()),12)
        self.assertEqual(len(self.calls),24)

    async def test_restart_uses_fresh_disk_cache_without_downloading(self):
        await self.store.refresh()
        await self.store.close()
        async def forbidden(*args,**kwargs): self.fail('ordinary read attempted network')
        self.store = BossPortraits(Path(self.temp.name)/'portraits',forbidden,clock=lambda:self.now[0])
        self.assertEqual(len(await self.store.all()),12)
        self.now[0] += CACHE_SECONDS+1
        self.assertEqual(await self.store.all(),{})

    async def test_failed_updates_preserve_valid_old_thumbnails_and_do_not_retry_on_queries(self):
        await self.store.refresh(); before = await self.store.all()
        async def broken(*args,**kwargs): raise TimeoutError()
        self.store.download = broken
        self.assertEqual(await self.store.refresh(),0)
        self.assertEqual(await self.store.all(),before)

    async def test_close_cancels_explicit_update_and_waits_for_workers(self):
        started = asyncio.Event()
        async def blocked(*args,**kwargs): started.set(); await asyncio.Event().wait()
        self.store.download = blocked
        task = asyncio.create_task(self.store.refresh())
        await started.wait(); await self.store.close()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(self.store.workers)

    async def test_invalid_media_is_rejected_before_cache_publication(self):
        for body in (b'not an image',b'x'*(1024*1024+1)):
            with self.assertRaises((ValueError,OSError)): sanitize(body)

    async def test_manual_transport_accepts_images_and_rejects_non_image_responses(self):
        seen, bad, content_type = [], [False], ['image/png']
        async def serve(request):
            seen.append(request.headers['Accept'])
            return web.Response(body=b'<html>failure</html>' if bad[0] else portrait(),
                                content_type='text/html' if bad[0] else content_type[0])
        app = web.Application(); app.router.add_get('/tagilla-portrait.png',serve)
        runner = web.AppRunner(app); await runner.setup()
        site = web.TCPSite(runner,'127.0.0.1',0); await site.start()
        try:
            with patch.object(boss_portraits,'HOST',f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'):
                body = await BossPortraits.fetch('tagilla-portrait.png')
                self.assertEqual(Image.open(io.BytesIO(body)).format,'PNG')
                self.assertIn('image/png',seen[0])
                content_type[0] = 'application/octet-stream'
                self.assertEqual(Image.open(io.BytesIO(await BossPortraits.fetch('tagilla-portrait.png'))).format,'PNG')
                bad[0] = True
                with self.assertRaises(QueryError): await BossPortraits.fetch('tagilla-portrait.png')
        finally:
            await runner.cleanup()
