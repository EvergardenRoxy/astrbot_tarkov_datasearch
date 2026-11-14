import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tarkov_core.boss_portraits import PORTRAITS, BossPortraits, sanitize


def portrait(size=(300, 300)):
    output = io.BytesIO()
    Image.new('RGBA', size, (100, 100, 100, 255)).save(output, format='PNG')
    return output.getvalue()


class PortraitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls, self.bodies = [], {}
        self.store = BossPortraits(Path(self.temp.name)/'portraits', client=None)
        self.store.download = self.download

    async def download(self, name):
        self.calls.append(name)
        return self.bodies.get(name, portrait())

    async def test_only_the_manual_refresh_downloads(self):
        self.assertEqual(await self.store.all(), {})
        self.assertEqual(self.calls, [])
        self.assertFalse(self.store.root.exists())
        self.assertEqual(await self.store.refresh(), 12)
        self.assertEqual(set(self.calls), set(PORTRAITS.values()))
        found = await self.store.all()
        self.assertEqual(set(found), set(PORTRAITS))
        for body in found.values():
            self.assertLessEqual(max(Image.open(io.BytesIO(body)).size), 128)
        # Reading again, in this instance or a new one, never asks tarkov.dev.
        await self.store.all()
        self.assertEqual(len(await BossPortraits(self.store.root, client=None).all()), 12)
        self.assertEqual(len(self.calls), 12)

    async def test_a_bad_portrait_is_skipped_and_the_others_are_kept(self):
        self.bodies[PORTRAITS['bossKilla']] = b'not an image'
        self.assertEqual(await self.store.refresh(), 11)
        self.assertNotIn('bossKilla', await self.store.all())

    def test_oversized_pictures_are_refused(self):
        with self.assertRaises(ValueError):
            sanitize(portrait((4096, 2048)))
        with self.assertRaises(Exception):
            sanitize(b'')


if __name__ == '__main__':
    unittest.main()
