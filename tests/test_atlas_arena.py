"""Atlas Arena integration and register contents, using invented documents only."""
import io
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from PIL import Image, ImageDraw

from fixtures import atlas_profile, arena_profile
from tarkov_core.arena import build_arena_card, summarize_arena
from tarkov_core.arena_image import render_arena_image
from tarkov_core.atlas_card import build
from tarkov_core.atlas_image import render_atlas_image


class ArenaCardTests(unittest.TestCase):
    def test_shared_rarity_order_total_and_no_synthetic_ranked_totals(self):
        done = [{'id': f'a{i}', 'at': 1700000000+i, 'rarity': ('Common', 'Rare', 'Legendary', 'Seasonal')[i % 4],
                 'percent': 0, 'en': {'name': f'Example {i}'}} for i in range(16)]
        doc = atlas_profile('arena', achievements={'count': 16, 'total': 86, 'done': done})
        card = build(doc, 'arena')
        self.assertEqual(card['achievement_total'], 86)
        self.assertEqual(card['achievements'], 16)
        self.assertEqual([a['id'] for a in card['achievement_details']],
                         ['a15', 'a11', 'a7', 'a3', 'a14', 'a10', 'a6', 'a2', 'a13'])
        self.assertEqual([s['ranked'] for s in card['sections']], [False])
        doc['arenaStats']['modes'] = {'TeamFight': {'GamesCount': 2, 'ArenaWins': 1}}
        card = build(doc, 'arena')
        self.assertEqual([s['ranked'] for s in card['sections']], [True])
        self.assertIsNone(card['sections'][0]['overall'])

    def test_odd_and_missing_fields_still_draw(self):
        for value in (None, [], 'x', 1, {}, {'done': []}, {'count': 0, 'total': 86, 'done': []}):
            with self.subTest(value=value):
                card = build(atlas_profile('arena', arenaStats=value, achievements=value, looked_up=value,
                                           accountId=value, cached=value), 'arena')
                image = Image.open(io.BytesIO(render_atlas_image(card)))
                image.load()
                self.assertEqual((image.format, image.width), ('JPEG', 820))
                self.assertFalse(card['stale_days'])

    def test_drawn_source_tables_and_achievement_count(self):
        card = build(atlas_profile('arena', cached=True), 'arena')
        drawn = []
        real = ImageDraw.ImageDraw.text
        def record(self, xy, text, *args, **kwargs):
            drawn.append(text)
            return real(self, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', record):
            data = render_atlas_image(card)
        self.assertIn('数据来源：Atlas', drawn)
        self.assertIn('https://eftforge.com/atlas', drawn)
        self.assertIn('1 / 80', drawn)
        self.assertIn('最近成就 LAST HONOUR', drawn)
        self.assertNotIn('最近出场 LAST BOUT', drawn)
        self.assertIn('最长连杀', drawn)  # An individual table header, not the former single summary line.
        self.assertFalse(any('出场 200' in text for text in drawn))
        self.assertTrue(any('2026-11-01 12:00:00' in text and 'Atlas 缓存' in text for text in drawn))
        self.assertTrue(any('Morph1ne / SouthHorizons76' in text for text in drawn))
        self.assertFalse(any('tarkov.dev 公开快照' in text or '示例' in text for text in drawn))
        image = Image.open(io.BytesIO(data))
        self.assertLess(image.height, 2000)

    def test_legacy_and_atlas_can_render_concurrently_without_patches(self):
        legacy = build_arena_card(summarize_arena(arena_profile()))
        arena = build(atlas_profile('arena'), 'arena')
        expected = render_arena_image(legacy), render_atlas_image(arena)
        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(render_arena_image, legacy)
            new = pool.submit(render_atlas_image, arena)
            self.assertEqual((old.result(), new.result()), expected)
