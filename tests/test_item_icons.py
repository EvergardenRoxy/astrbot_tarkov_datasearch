import asyncio
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tarkov_core.item_icons import ItemIcons, TTL


class ItemIconTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.calls = []; self.now = [1800000000]
        async def download(path,**kwargs):
            self.calls.append(path); output=io.BytesIO(); Image.new('RGB',(200,200)).save(output,format='PNG'); return output.getvalue()
        self.icons = ItemIcons(Path(self.temp.name)/'icons',download,clock=lambda:self.now[0])

    async def asyncTearDown(self): await self.icons.close(); self.temp.cleanup()

    async def test_fixed_item_id_paths_lazy_cache_restart_and_expiry(self):
        self.assertFalse(self.icons.root.exists())
        self.assertIsNone(await self.icons.get('../../secret'))
        item_id='a'*24
        self.assertIsNotNone(await self.icons.get(item_id))
        await self.icons.get(item_id); self.assertEqual(self.calls,[item_id+'-512.webp'])
        self.now[0] += TTL+1
        await self.icons.get(item_id); self.assertEqual(len(self.calls),2)

    async def test_failed_thumbnail_requests_are_bounded_and_not_retried_per_query(self):
        async def fail(path,**kwargs): self.calls.append(path); raise TimeoutError()
        self.icons.download=fail
        self.assertIsNone(await self.icons.get('a'*24)); self.assertIsNone(await self.icons.get('a'*24))
        self.assertEqual(len(self.calls),2)
        for i in range(140): self.icons.remember(f'{i:024x}',(None,self.now[0]))
        self.assertLessEqual(len(self.icons.cache),128)
