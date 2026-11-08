"""The Atlas player picture: card model, picture cache, history database and the renderer. Invented data only."""
import asyncio
import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from fixtures import atlas_profile
from tarkov_core import atlas_assets
from tarkov_core.atlas_assets import AtlasAssets, build_catalog, icon_urls, level_badge, sanitize, valid
from tarkov_core.atlas_card import build
from tarkov_core.atlas_history import AtlasHistory
from tarkov_core.atlas_image import render_atlas_image
from tarkov_core.atlas_profile import AtlasProfileError
from test_atlas_cooldown import Clock


def png(size=(40, 30), colour=(200, 50, 50, 255)):
    out = io.BytesIO()
    Image.new('RGBA', size, colour).save(out, format='PNG')
    return out.getvalue()


def rows(card, column):
    return dict(dict(card['columns'])[column])


class CardTests(unittest.TestCase):
    def test_columns_follow_the_atlas_page(self):
        doc = atlas_profile('pvp')
        doc['stats']['pmc']['counters'].append([['ExitStatus', 'Left', 'Pmc'], 20])
        card = build(doc, 'pvp')
        pmc, scav, total = rows(card, 'PMC'), rows(card, 'SCAV'), rows(card, '总览')
        # K/D = player kills / (killed + left); all kills / (killed + left); AI = all - player kills.
        self.assertEqual(pmc['K/D'], f'{900/140:.2f}')
        self.assertEqual(pmc['K/D（全部击杀）'], f'{5000/140:.2f}')
        self.assertEqual(pmc['击杀数（AI）'], '4,100')
        self.assertEqual(pmc['擅离职守'], '20')
        self.assertEqual(pmc['擅离率'], '2 %')
        self.assertEqual(scav['擅离职守'], '0')
        self.assertEqual(total['战局数'], '1,050')
        self.assertEqual(total['连续幸存数'], '45')
        self.assertEqual(pmc['在线'], '1000h00m')
        self.assertEqual(len(card['headline']), 12)
        self.assertNotIn('平均存活时间', pmc)

    def test_header_league_and_equipment(self):
        doc = atlas_profile('season', league={'rank': 2, 'name': {'en': 'A', 'ch': '甲'}, 'max': 3,
                                              'maxName': {'en': 'B', 'ch': '乙'}}, prestigeLevel=2)
        doc['slots'] = [{'slot': 'FirstPrimaryWeapon', 'en': {'short': 'RIFLE'}, 'ch': {'short': '步枪'},
                         'image': 'https://assets.tarkov.dev/abc-base-image.webp', 'render': '/atlas/api/lookup-art/x'},
                        {'slot': 'Headwear', 'en': {'short': 'HAT'}, 'image': 'https://evil.example/x.png'}]
        card = build(doc, 'season')
        self.assertEqual((card['mode_label'], card['league'], card['prestige'], card['level']), ('PVP 赛季', '乙', 2, 50))
        self.assertAlmostEqual(card['progress'], 0.5)
        self.assertEqual(card['to_next'], 1000000)
        equipment = {key: item for row in card['equipment'] for key, _, _, item in row}
        self.assertEqual(equipment['FirstPrimaryWeapon'], {'name': 'RIFLE', 'image': 'https://assets.tarkov.dev/abc-base-image.webp'})
        # Only assets.tarkov.dev pictures are used; never Atlas' own weapon render.
        self.assertIsNone(equipment['Headwear']['image'])
        self.assertIsNone(equipment['Earpiece'])
        self.assertEqual(card['assets'], ['https://assets.tarkov.dev/abc-base-image.webp'])

    def test_rare_and_latest_achievements(self):
        done = [{'id': f'a{i}', 'at': 1700000000+i, 'rarity': ('Common', 'Rare', 'Legendary', 'Seasonal')[i % 4],
                 'percent': 50-i, 'ch': {'name': f'成就{i}', 'description': '描述'}} for i in range(12)]
        done.append({'id': 'x', 'at': 1600000000, 'rarity': 'Odd', 'percent': None, 'en': {'name': 'No share'}})
        card = build(atlas_profile('pve', achievements={'count': 13, 'total': 100, 'done': done}), 'pve')
        data = card['achievements']
        self.assertEqual([a['id'] for a in data['rare']], ['a11', 'a10', 'a9', 'a8', 'a7', 'a6', 'a5', 'a4', 'a3'])
        self.assertEqual([a['id'] for a in data['latest']][:3], ['a11', 'a10', 'a9'])
        self.assertEqual(len(data['latest']), 10)
        self.assertEqual((data['rare'][0]['rarity'], data['rare'][0]['tone']), ('赛季', 'seasonal'))
        self.assertEqual((data['rare'][1]['rarity'], data['rare'][1]['tone']), ('传奇', 'legendary'))
        self.assertEqual((data['count'], data['total']), (13, 100))

    def test_skills_like_the_old_card(self):
        card = build(atlas_profile('pve'), 'pve', skill_limit=0)
        self.assertEqual(card['skills'], [])
        self.assertEqual(build(atlas_profile('pve'), 'pve')['skills'], [('耐力', 51, True), ('力量', 25, False)])

    def test_odd_fields_never_raise(self):
        fields = ('stats', 'slots', 'achievements', 'skills', 'levelExp', 'league', 'experience', 'level', 'looked_up',
                  'prestigeLevel', 'side', 'accountId')
        for value in (None, 'x', 5, [], {}, [None], {'done': 'x'}):
            for field in fields:
                with self.subTest(field=field, value=value):
                    build(atlas_profile('pve', **{field: value}), 'pve')
            # One picture per odd value with every field odd at once (drawing is slow).
            render_atlas_image(build(atlas_profile('pve', **{field: value for field in fields}), 'pve'))
        with self.assertRaises(AtlasProfileError):
            build({'nickname': None}, 'pve')


class RenderTests(unittest.TestCase):
    def test_jpeg_with_and_without_pictures(self):
        card = build(atlas_profile('pvp'), 'pvp')
        plain = render_atlas_image(card)
        self.assertTrue(plain.startswith(b'\xff\xd8'))
        icons = {'level_badge': png((64, 64)), 'items': {}, 'achievements': {'a1': png((64, 64)), 'a2': b'broken'}}
        with Image.open(io.BytesIO(render_atlas_image(card, icons))) as image:
            self.assertEqual(image.width, 1600)
            self.assertGreater(image.height, 2000)
            # The level badge (red test picture) is drawn in the header.
            self.assertGreater(image.convert('RGB').getpixel((32+20+44, 112+22+44))[0], 150)


class CatalogTests(unittest.TestCase):
    def test_catalog_from_public_data(self):
        tasks = {'achievements': {'a1': {'id': 'a1', 'imageLink': 'https://assets.tarkov.dev/achievement-a1-icon.webp'},
                                  'a2': {'id': 'a2', 'imageLink': 'https://other.example/a2.webp'}},
                 'prestige': [{'prestigeLevel': 1, 'iconLink': 'https://assets.tarkov.dev/prestige-1-icon.webp'}]}
        items = {'playerLevels': [{'level': 1, 'levelBadgeImageLink': 'https://assets.tarkov.dev/player-level-group-1.png'},
                                  {'level': 5, 'levelBadgeImageLink': 'https://assets.tarkov.dev/player-level-group-2.png'}]}
        catalog = build_catalog([tasks, None, items])
        self.assertEqual(catalog['achievements'], {'a1': 'https://assets.tarkov.dev/achievement-a1-icon.webp'})
        self.assertEqual(level_badge(catalog, 4), 'https://assets.tarkov.dev/player-level-group-1.png')
        self.assertEqual(level_badge(catalog, 79), 'https://assets.tarkov.dev/player-level-group-2.png')
        self.assertIsNone(level_badge(catalog, None))
        self.assertEqual(len(icon_urls(catalog)), 4)

    def test_only_fixed_host_picture_links(self):
        self.assertTrue(valid('https://assets.tarkov.dev/abc-icon.webp'))
        for url in ('http://assets.tarkov.dev/a.png', 'https://assets.tarkov.dev/../a.png', 'https://assets.tarkov.dev/a.svg',
                    'https://assets.tarkov.dev.evil.example/a.png', 'https://assets.tarkov.dev/a/b.png', None, 5):
            self.assertFalse(valid(url))

    def test_sanitize(self):
        with Image.open(io.BytesIO(sanitize(png((2000, 1000))))) as image:
            self.assertEqual(image.size, (512, 256))
        for body in (b'not an image', b'x'*(5*1024*1024)):
            with self.assertRaises(Exception):
                sanitize(body)


class AssetCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.downloads = []
        self.broken = set()
        self.feeds = []
        async def download(url):
            self.downloads.append(url)
            if url in self.broken:
                raise OSError('down')
            return png()
        async def feed(resource, mode):
            self.feeds.append((resource, mode))
            if resource == 'tasks':
                return {'achievements': [{'id': 'a1', 'imageLink': 'https://assets.tarkov.dev/achievement-a1-icon.webp'}],
                        'prestige': [{'prestigeLevel': 1, 'iconLink': 'https://assets.tarkov.dev/prestige-1-icon.webp'}]}
            return {'playerLevels': [{'level': 1, 'levelBadgeImageLink': 'https://assets.tarkov.dev/player-level-group-1.png'}]}
        self.root = Path(self.temp.name)/'atlas-assets'
        self.assets = AtlasAssets(self.root, feed, download=download, clock=self.clock)

    async def asyncTearDown(self):
        await self.assets.close()
        self.temp.cleanup()

    async def test_first_use_downloads_once_and_keeps_it(self):
        url = 'https://assets.tarkov.dev/item-base-image.webp'
        found = await self.assets.images([url, url, 'https://evil.example/x.png'])
        self.assertEqual(list(found), [url])
        again = AtlasAssets(self.root, None, download=None)
        self.assertEqual(list(await again.images([url])), [url])
        self.assertEqual(self.downloads, [url])

    async def test_failures_draw_placeholders_and_wait_before_retrying(self):
        url = 'https://assets.tarkov.dev/missing.png'
        self.broken.add(url)
        self.assertEqual(await self.assets.images([url]), {})
        self.assertEqual(await self.assets.images([url]), {})
        self.assertEqual(len(self.downloads), 1)
        self.clock.advance(atlas_assets.FAILURE_SECONDS)
        self.broken.clear()
        self.assertEqual(list(await self.assets.images([url])), [url])

    async def test_prefetch_catalog_and_clear(self):
        done, total = await self.assets.prefetch()
        self.assertEqual((done, total), (3, 3))
        self.assertEqual(self.feeds, [('tasks', 'pvp'), ('tasks', 'pve'), ('items', 'pvp')])
        reloaded = AtlasAssets(self.root, None)
        self.assertEqual((await reloaded.catalog())['prestige'], {'1': 'https://assets.tarkov.dev/prestige-1-icon.webp'})
        self.assertEqual(await self.assets.clear(), 4)
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_concurrent_requests_share_one_download(self):
        url = 'https://assets.tarkov.dev/shared.png'
        results = await asyncio.gather(*(self.assets.images([url]) for _ in range(5)))
        self.assertTrue(all(url in result for result in results))
        self.assertEqual(self.downloads, [url])


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.clock.now = 1800000000.
        self.hours, self.limit = 180, 64*1024*1024
        self.history = AtlasHistory(Path(self.temp.name)/'atlas-history.sqlite3', hours=lambda: self.hours,
                                    max_bytes=lambda: self.limit, clock=self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def doc(self, minute, mode='pve', **changes):
        return atlas_profile(mode, **{'looked_up': f'2027-01-15T08:{minute:02d}:00+00:00', **changes})

    def test_one_row_per_atlas_lookup(self):
        self.assertTrue(self.history.record(1234567, 'pve', self.doc(0)))
        self.assertFalse(self.history.record(1234567, 'pve', self.doc(0, cached=True)))
        self.assertTrue(self.history.record(1234567, 'pve', self.doc(5)))
        self.assertTrue(self.history.record(1234567, 'pvp', self.doc(5, 'pvp')))
        latest = self.history.latest(1234567, 'pve')
        self.assertEqual(latest['document']['looked_up'], '2027-01-15T08:05:00+00:00')
        self.assertEqual(len(self.history.history(1234567, 'pve')), 2)
        older, newer = self.history.compare(1234567, 'pve')
        self.assertLess(older['looked_up'], newer['looked_up'])
        self.assertIsNone(self.history.compare(1234567, 'pvp'))
        self.assertEqual(self.history.accounts(), [(1234567, 'pve', 'Test_Player'), (1234567, 'pvp', 'Test_Player')])

    def test_unstorable_documents(self):
        for account, mode, doc in ((1, 'pve', self.doc(0, looked_up=None)), (1, 'regular', self.doc(0)),
                                   ('1', 'pve', self.doc(0)), (1, 'pve', self.doc(0, nickname=None))):
            self.assertFalse(self.history.record(account, mode, doc))
        self.assertIsNone(self.history.latest(1, 'pve'))

    def test_retention_hours_and_size_cap(self):
        self.history.record(1, 'pve', self.doc(0))
        self.clock.advance(181*3600)
        self.history.record(2, 'pve', self.doc(1))
        self.assertIsNone(self.history.latest(1, 'pve'))
        self.assertIsNotNone(self.history.latest(2, 'pve'))
        size = len(gzip.compress(json.dumps(self.doc(2), ensure_ascii=False, separators=(',', ':')).encode(), 6))
        self.limit = size*2+10
        for minute in range(3, 8):
            self.clock.advance(1)
            self.history.record(3, 'pve', self.doc(minute))
        self.assertEqual(len(self.history.history(3, 'pve')), 2)
        self.assertIsNone(self.history.latest(2, 'pve'))

    def test_delete_all(self):
        self.history.record(1, 'pve', self.doc(0))
        self.history.delete_all()
        self.assertFalse(self.history.path.exists())
        self.assertEqual(self.history.history(1, 'pve'), [])
