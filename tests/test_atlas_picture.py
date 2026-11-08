"""The Atlas player picture: card model, picture cache, history database and the renderer. Invented data only."""
import asyncio
import gzip
import io
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from PIL import Image, ImageDraw

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


class CardTests(unittest.TestCase):
    def test_combat_rows_preserve_kd_rules_and_exit_counters(self):
        doc = atlas_profile('pvp')
        doc['stats']['pmc']['counters'].append([['ExitStatus', 'Left', 'Pmc'], 20])
        card = build(doc, 'pvp')
        pmc, scav = card['combat_rows']
        # K/D (all kills) and PMC K/D both use killed + left, as before.
        self.assertEqual(pmc['values'], ['1,000', '800', '84.0%', '5,000', '120', '35.71', '900', '6.43'])
        self.assertEqual(pmc['stats']['left'], 20)
        self.assertEqual(pmc['stats']['transit'], 40)
        self.assertEqual(scav['stats']['left'], 0)
        self.assertEqual(scav['values'][2], '60.0%')
        headline = dict(card['headline'])
        self.assertEqual(headline['战局数'], '1,050')
        self.assertEqual(headline['连续幸存数'], '45')
        self.assertEqual(headline['在线'], '1000h00m')
        self.assertEqual(headline['幸存率'], '82.9%')
        # Equal labels mean equal numbers: the headline's player kills are "击杀 PMC", as in the table.
        self.assertEqual(headline['击杀 PMC'], '920')
        self.assertNotIn('击杀数', headline)
        self.assertEqual(headline['擅离率'], '2%')
        self.assertEqual(len(card['headline']), 12)

    def test_transits_count_towards_survival_in_all_three_modes(self):
        pmc = {'counters': [[['Sessions'], 37], [['ExitStatus', 'Survived'], 33],
                            [['ExitStatus', 'Transit'], 2], [['ExitStatus', 'Killed'], 2]]}
        scav = {'counters': [[['Sessions'], 8], [['ExitStatus', 'Survived'], 5],
                             [['ExitStatus', 'Transit'], 1]]}
        for mode in ('pvp', 'pve', 'season'):
            with self.subTest(mode=mode):
                card = build(atlas_profile(mode, stats={'pmc': pmc, 'scav': scav}), mode)
                self.assertEqual(card['combat_rows'][0]['values'][2], '94.6%')
                self.assertEqual(card['combat_rows'][1]['values'][2], '75.0%')
                self.assertEqual(dict(card['headline'])['幸存率'], '91.1%')
        empty = build(atlas_profile('pve', stats={}), 'pve')
        self.assertEqual(dict(empty['headline'])['幸存率'], '—')
        self.assertTrue(all(row['values'][2] == '—' for row in empty['combat_rows']))
        self.assertTrue(all(row['values'][5] == '—' and row['values'][7] == '—' for row in empty['combat_rows']))

    def test_header_league_and_equipment(self):
        doc = atlas_profile('season', league={'rank': 2, 'name': {'en': 'A', 'ch': '甲'}, 'max': 3,
                                              'maxName': {'en': 'B', 'ch': '乙'}}, prestigeLevel=2)
        doc['slots'] = [{'slot': 'FirstPrimaryWeapon', 'en': {'short': 'RIFLE'}, 'ch': {'short': '步枪'},
                         'image': 'https://assets.tarkov.dev/abc-base-image.webp', 'render': '/atlas/api/lookup-art/x',
                         'children': [
                             {'slot': 'mod_reciever', 'slot_name': 'Receiver', 'en': {'short': 'UPPER'},
                              'image': 'https://assets.tarkov.dev/upper-base-image.webp',
                              'children': [{'slot': 'mod_scope', 'slot_name': 'Scope', 'en': {'short': 'SCOPE'},
                                            'icon': 'https://assets.tarkov.dev/scope-icon.webp'}]},
                             {'slot': 'patron_in_weapon', 'en': {'short': 'BP'}, 'image': 'https://assets.tarkov.dev/bp.webp'},
                             'junk']},
                        {'slot': 'Headwear', 'en': {'short': 'HAT'}, 'image': 'https://evil.example/x.png'},
                        {'slot': 'Backpack', 'en': {'short': 'BAG'}, 'image': 'https://assets.tarkov.dev/bag-base-image.webp'}]
        card = build(doc, 'season')
        self.assertEqual((card['mode_label'], card['league'], card['prestige'], card['level']), ('PVP 赛季', '乙', 2, 50))
        self.assertAlmostEqual(card['progress'], 0.5)
        self.assertEqual(card['to_next'], 1000000)
        equipment = {key: item for row in card['equipment'] for key, _, _, item in row}
        # Weapon, holster, melee and secure container slots are not shown (maintainer); no attachment list.
        self.assertEqual([key for row in card['equipment'] for key, _, _, _ in row],
                         ['Earpiece', 'Headwear', 'FaceCover', 'TacticalVest', 'ArmBand', 'ArmorVest', 'Eyewear', 'Backpack'])
        self.assertNotIn('weapons', card)
        self.assertEqual(equipment['Backpack'], {'name': 'BAG', 'image': 'https://assets.tarkov.dev/bag-base-image.webp'})
        # Only assets.tarkov.dev pictures are used.
        self.assertIsNone(equipment['Headwear']['image'])
        self.assertIsNone(equipment['Earpiece'])
        self.assertEqual(card['assets'], ['https://assets.tarkov.dev/bag-base-image.webp'])
        self.assertTrue(render_atlas_image(card).startswith(b'\xff\xd8'))

    def test_rare_and_latest_achievements(self):
        done = [{'id': f'a{i}', 'at': 1700000000+i, 'rarity': ('Common', 'Rare', 'Legendary', 'Seasonal')[i % 4],
                 'percent': 50-i, 'ch': {'name': f'成就{i}', 'description': '描述'}} for i in range(12)]
        done.append({'id': 'x', 'at': 1600000000, 'rarity': 'Odd', 'percent': None, 'en': {'name': 'No share'}})
        card = build(atlas_profile('pve', achievements={'count': 13, 'total': 100, 'done': done}), 'pve')
        data = card['achievements']
        # Rarity first (seasonal, legendary, rare, common, others), newest first within a rarity; the share does not
        # rank (every PVE share is 0 in Atlas' data).
        self.assertEqual([a['id'] for a in data['rare']], ['a11', 'a7', 'a3', 'a10', 'a6', 'a2', 'a9', 'a5', 'a1'])
        self.assertEqual([a['id'] for a in data['latest']][:3], ['a11', 'a10', 'a9'])
        self.assertEqual(len(data['latest']), 10)
        self.assertEqual((data['rare'][0]['rarity'], data['rare'][0]['tone']), ('赛季', 'seasonal'))
        self.assertEqual((data['rare'][3]['rarity'], data['rare'][3]['tone']), ('传奇', 'legendary'))
        zero = [dict(entry, percent=0) for entry in done]
        ranked = build(atlas_profile('pve', achievements={'count': 13, 'total': 100, 'done': zero}), 'pve')
        self.assertEqual([a['id'] for a in ranked['achievements']['rare']], [a['id'] for a in data['rare']])
        self.assertEqual((data['count'], data['total']), (13, 100))

    def test_skills_like_the_old_card(self):
        card = build(atlas_profile('pve'), 'pve', skill_limit=0)
        self.assertEqual(card['skills'], [])
        self.assertEqual(build(atlas_profile('pve'), 'pve')['skills'], [('耐力', 51, True), ('力量', 25, False)])
        # Two full columns: 16 places, elite first, whatever the snapshot's "other skills" setting says.
        keys = ['Endurance', 'Strength', 'Vitality', 'Health', 'StressResistance', 'Metabolism', 'Immunity',
                'Perception', 'Intellect', 'Attention', 'Charisma', 'Search', 'Surgery', 'CovertMovement', 'Throwing',
                'AimDrills', 'HideoutManagement', 'Crafting']
        many = {'common': [{'id': key, 'progress': (5100 if i < 3 else 3000-i*100), 'lastAccess': 1700000000}
                           for i, key in enumerate(keys)]}
        shown = build(atlas_profile('pve', skills=many), 'pve', skill_limit=10)['skills']
        self.assertEqual(len(shown), 16)
        self.assertEqual([elite for _, _, elite in shown[:4]], [True, True, True, False])
        self.assertEqual([level for _, level, _ in shown[3:]], sorted([level for _, level, _ in shown[3:]], reverse=True))

    def test_achievement_chinese_is_from_atlas_with_per_field_english_fallback(self):
        done = [
            {'id': 'cn', 'ch': {'name': '中文成就', 'description': '中文描述'},
             'en': {'name': 'English name', 'description': 'English description'}},
            {'id': 'mixed', 'ch': {'name': '中文名称', 'description': ' '},
             'en': {'name': 'English name', 'description': 'English description'}},
            {'id': 'en', 'ch': {'name': None}, 'en': {'name': 'English only'}},
            {'id': 'missing'},
        ]
        for mode in ('pvp', 'pve', 'season', 'arena'):
            with self.subTest(mode=mode):
                card = build(atlas_profile(mode, achievements={'count': 4, 'total': 86, 'done': done}), mode)
                entries = card['achievement_details'] if mode == 'arena' else card['achievements']['rare']
                by_id = {entry['id']: entry for entry in entries}
                self.assertEqual((by_id['cn']['name'], by_id['cn']['description']), ('中文成就', '中文描述'))
                self.assertEqual((by_id['mixed']['name'], by_id['mixed']['description']), ('中文名称', 'English description'))
                self.assertEqual(by_id['en']['name'], 'English only')
                self.assertEqual(by_id['missing']['name'], '—')

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
    def test_horizontal_statistics_and_larger_two_column_skills(self):
        from tarkov_core import atlas_image as ui
        card = build(atlas_profile('pve'), 'pve')
        image = Image.new('RGB', (ui.W, 1200), ui.BG)
        drawn, boxes = [], []
        original_text, original_rectangle = ImageDraw.ImageDraw.text, ImageDraw.ImageDraw.rectangle
        inked = []
        def record_text(self, xy, text, *args, **kwargs):
            drawn.append((xy, text, kwargs.get('font')))
            inked.append((text, self.textbbox(xy, text, font=kwargs.get('font'), anchor=kwargs.get('anchor'))))
            return original_text(self, xy, text, *args, **kwargs)
        def record_rectangle(self, xy, *args, **kwargs):
            boxes.append(xy)
            return original_rectangle(self, xy, *args, **kwargs)
        with unittest.mock.patch.object(ImageDraw.ImageDraw, 'text', record_text), \
                unittest.mock.patch.object(ImageDraw.ImageDraw, 'rectangle', record_rectangle):
            draw = ImageDraw.Draw(image)
            end = ui.statistics(draw, card, 0)
            ui.skills(draw, card, end)
        texts = [text for _, text, _ in drawn]
        self.assertIn('84.0%', texts)
        self.assertIn('PMC K/D', texts)
        self.assertIn('K/D（全部击杀）', texts)
        # Every label, including the "100%" tick at the bar's right end, stays inside the margins.
        self.assertTrue(all(ui.M <= box[0] and box[2] <= ui.W-ui.M for _, box in inked), inked)
        self.assertNotIn('存活率', ''.join(map(str, texts)))
        self.assertIn('幸存率 =（撤离＋转移）÷总战局数', texts)
        self.assertIn('擅离职守 0/0', texts)
        self.assertTrue(any(text == '1,000' and font.size == 44 for _, text, font in drawn))
        skills = [(xy, font) for xy, text, font in drawn if text in ('耐力', '力量')]
        self.assertEqual(skills[0][0][1], skills[1][0][1])
        self.assertGreater(skills[1][0][0], ui.W/2)
        self.assertTrue(all(font.size == 34 for _, font in skills))
        self.assertEqual(sum(1 for x0, y0, x1, y1 in boxes if x1-x0 == 27 and y1-y0 == 27), 20)

    def test_header_carries_the_atlas_link_and_no_player_share_is_drawn(self):
        done = [{'id': f'a{i}', 'at': 1700000000+i, 'rarity': 'Rare', 'percent': 12.5,
                 'ch': {'name': f'成就{i}', 'description': '描述'}} for i in range(3)]
        drawn = []
        original = ImageDraw.ImageDraw.text
        def record(self, xy, text, *args, **kwargs):
            drawn.append(str(text))
            return original(self, xy, text, *args, **kwargs)
        for mode in ('pvp', 'pve', 'season'):
            card = build(atlas_profile(mode, achievements={'count': 3, 'total': 9, 'done': done}), mode)
            with unittest.mock.patch.object(ImageDraw.ImageDraw, 'text', record):
                render_atlas_image(card)
        # The completion share is not shown anywhere (maintainer, 2026-11-08), in any mode.
        self.assertFalse([text for text in drawn if '玩家' in text and '%' in text])
        self.assertNotIn('% 玩家', drawn)
        self.assertIn('数据来源：Atlas  ', drawn)
        self.assertIn('达成时间（UTC+8）', drawn)
        self.assertNotIn('击杀 PMC：', drawn)  # The header's small numbers were replaced by the Atlas link.

    def test_empty_and_inconsistent_exit_counters_stay_inside_the_bar(self):
        from tarkov_core import atlas_image as ui
        for stats in ({}, {'pmc': {'counters': [[['Sessions'], 1], [['ExitStatus', 'Survived'], 10],
                                               [['ExitStatus', 'Transit'], 9]]}}):
            card = build(atlas_profile('pve', stats=stats), 'pve')
            image = Image.new('RGB', (ui.W, 600), ui.BG)
            boxes = []
            original = ImageDraw.ImageDraw.rectangle
            def record(self, xy, *args, **kwargs):
                boxes.append(xy)
                return original(self, xy, *args, **kwargs)
            with unittest.mock.patch.object(ImageDraw.ImageDraw, 'rectangle', record):
                ui.exit_bars(ImageDraw.Draw(image), card, 0)
            self.assertTrue(all(0 <= x0 <= x1 <= ui.W-ui.M for x0, _, x1, _ in boxes))

    def test_jpeg_with_and_without_pictures(self):
        card = build(atlas_profile('pvp'), 'pvp')
        plain = render_atlas_image(card)
        self.assertTrue(plain.startswith(b'\xff\xd8'))
        icons = {'level_badge': png((64, 64)), 'items': {}, 'achievements': {'a1': png((64, 64)), 'a2': b'broken'}}
        with Image.open(io.BytesIO(render_atlas_image(card, icons))) as image:
            self.assertEqual(image.width, 1600)
            self.assertGreater(image.height, 2000)
            # The level badge (red test picture) is drawn in the header.
            from tarkov_core.atlas_image import HEADER_TOP
            self.assertGreater(image.convert('RGB').getpixel((32+20+44, HEADER_TOP+22+44))[0], 150)


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

    async def test_an_unknown_achievement_rebuilds_the_catalog_at_most_every_few_hours(self):
        await self.assets.catalog()
        self.assertEqual(len(self.feeds), 3)
        self.assertIn('a1', (await self.assets.catalog_for(['a1']))['achievements'])
        self.assertEqual(len(self.feeds), 3)  # Known achievements need no rebuild.
        await self.assets.catalog_for(['new'])
        self.assertEqual(len(self.feeds), 6)  # A new achievement in the game: rebuilt once.
        await self.assets.catalog_for(['new'])
        self.assertEqual(len(self.feeds), 6)
        self.clock.advance(atlas_assets.REBUILD_SECONDS)
        await self.assets.catalog_for(['new'])
        self.assertEqual(len(self.feeds), 9)

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


class FullFontTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tarkov_core import font_download
        self.module = font_download
        self.temp = tempfile.TemporaryDirectory()
        self.body = b'font-bytes'*10
        self.patches = [unittest.mock.patch.object(font_download, 'SIZE', len(self.body)),
                        unittest.mock.patch.object(font_download, 'SHA256', __import__('hashlib').sha256(self.body).hexdigest())]
        for patcher in self.patches:
            patcher.start()
        self.clock = Clock()
        self.asked = []
        self.answers = {}
        async def download(host, path):
            self.asked.append(host)
            answer = self.answers.get(host, OSError('blocked'))
            if isinstance(answer, BaseException):
                raise answer
            return answer
        self.mirror = ''
        self.font = font_download.FullFont(Path(self.temp.name)/'fonts', download=download, mirror=lambda: self.mirror,
                                           clock=self.clock)

    async def asyncTearDown(self):
        await self.font.close()
        for patcher in self.patches:
            patcher.stop()
        self.temp.cleanup()

    def test_mirror_setting(self):
        parse = self.module.custom_mirror
        self.assertEqual(parse(' https://mirror.example:8443/fonts/NotoSansSC.ttf?x=1 '),
                         ('https://mirror.example:8443', '/fonts/NotoSansSC.ttf?x=1'))
        for value in ('', None, 'http://mirror.example/f.ttf', 'ftp://x/f', 'https://user:pw@mirror.example/f.ttf', 'not a url'):
            self.assertIsNone(parse(value))

    async def test_mirrors_in_order_and_only_the_pinned_file(self):
        self.mirror = 'https://mirror.example/f.ttf'
        self.answers = {'https://mirror.example': b'wrong file', 'https://fastly.jsdelivr.net': self.body}
        await self.font.ensure()
        self.assertEqual(self.asked, ['https://mirror.example', 'https://cdn.jsdelivr.net', 'https://fastly.jsdelivr.net'])
        found = await self.font.ensure()
        self.assertEqual(found.read_bytes(), self.body)
        # A new process finds the verified file on disk without downloading.
        again = self.module.FullFont(self.font.root, download=None)
        self.assertEqual(await again.available(), found)

    async def test_all_mirrors_failing_waits_before_the_next_try(self):
        with self.assertRaises(self.module.FontDownloadError):
            await self.font.ensure()
        self.assertEqual(len(self.asked), 4)
        with self.assertRaises(self.module.FontDownloadError):
            await self.font.ensure()
        self.assertEqual(len(self.asked), 4)
        self.clock.advance(self.module.RETRY_SECONDS)
        self.answers['https://raw.githubusercontent.com'] = self.body
        await self.font.ensure()
        self.assertIsNotNone(await self.font.available())

    async def test_a_damaged_file_on_disk_is_not_used(self):
        self.font.root.mkdir(parents=True)
        self.font.path.write_bytes(b'x'*len(self.body))
        self.assertIsNone(await self.font.available())

    async def test_initial_font_download_is_shared_and_cancelling_one_waiter_isolated(self):
        started, release = asyncio.Event(), asyncio.Event()
        asked = []
        async def download(host, path):
            asked.append(host)
            started.set()
            await release.wait()
            return self.body
        self.font.download = download
        first = asyncio.create_task(self.font.ensure())
        await started.wait()
        second = asyncio.create_task(self.font.ensure())
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(self.font.task.cancelled())
        release.set()
        self.assertEqual((await second).read_bytes(), self.body)
        self.assertEqual(len(asked), 1)

    async def test_close_cancels_pending_download_without_publishing_a_partial_file(self):
        started = asyncio.Event()
        async def download(host, path):
            started.set()
            await asyncio.Event().wait()
        self.font.download = download
        waiting = asyncio.create_task(self.font.ensure())
        await started.wait()
        await self.font.close()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        self.assertFalse(self.font.path.exists())

    async def test_unwritable_font_cache_has_a_clear_error_and_retry_wait(self):
        self.answers['https://cdn.jsdelivr.net'] = self.body
        with unittest.mock.patch.object(self.font, 'write', side_effect=PermissionError('private path')):
            with self.assertRaises(self.module.FontDownloadError) as caught:
                await self.font.ensure()
        self.assertIn('写入权限', str(caught.exception))
        self.assertNotIn('private path', str(caught.exception))
        self.assertIsNone(self.font.checked)
        count = len(self.asked)
        with self.assertRaises(self.module.FontDownloadError):
            await self.font.ensure()
        self.assertEqual(len(self.asked), count)

    def test_every_renderer_shares_the_complete_font_without_a_subset_fallback(self):
        from tarkov_core import fonts, atlas_image, matching_image, image
        original = fonts.chinese_font_path()
        try:
            fonts.use_chinese_font(None)
            for loader in (atlas_image.font, matching_image.font, image.font):
                with self.assertRaises(fonts.FontUnavailable):
                    loader('sc', 20)
            fonts.use_chinese_font(original)
            for loader in (atlas_image.font, matching_image.font, image.font):
                self.assertEqual(Path(loader('sc', 20).path), Path(original))
            self.assertTrue(render_atlas_image(build(atlas_profile('pve'), 'pve')).startswith(b'\xff\xd8'))
        finally:
            fonts.use_chinese_font(original)
