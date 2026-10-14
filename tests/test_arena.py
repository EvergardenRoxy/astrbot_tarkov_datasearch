import copy
import io
import itertools
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from fixtures import arena_profile
from tarkov_core import arena_image
from tarkov_core.arena import MAX_MODES, TOP_LABEL, arp_text, best_arp, losses, rank, record, build_arena_card, render_arena_markdown, render_arena_text, summarize_arena
from tarkov_core.transport import QueryError

NOW = 1791000000 + 3600


def card(raw=None, **kwargs):
    return build_arena_card(summarize_arena(raw or arena_profile()), now=NOW, **kwargs)


class ArenaSummaryTests(unittest.TestCase):
    def test_sections_split_ranked_and_unranked(self):
        s = summarize_arena(arena_profile())
        self.assertEqual((s['nickname'], s['aid'], s['achievements'], s['updated']), ('Test_Player', 1234567, 2, 1791000000))
        unranked, ranked = s['sections']
        self.assertEqual((unranked['title'], unranked['ranked'], ranked['title'], ranked['ranked']), ('非排位', False, '排位', True))
        # Website labels for the modes it names, the key itself otherwise; the game's order, not the document's.
        self.assertEqual([label for label, _ in unranked['modes']], ['Team Fight', 'Last Hero', 'Checkpoint', 'ShootOutDuo'])
        self.assertEqual([label for label, _ in ranked['modes']], ['Team Fight', 'ShootOutDuo'])
        self.assertEqual(unranked['overall'], {'games': 200, 'kills': 1500, 'deaths': 600, 'best_arp': 1200,
                                               'max_killstreak': 12, 'win_streak': 7})
        self.assertEqual(dict(ranked['modes'])['Team Fight'], {'games': 20, 'wins': 12, 'losses': 8, 'kills': 150,
                                                               'deaths': 70, 'mvp': 2})

    def test_hostile_counters(self):
        raw = arena_profile()
        counters = raw['stat']['arenaOverAllCounters']
        counters['UnrankedTeamFight']['Counters'].update(Kills=-5, Deaths=True, GamesCount='9', ArenaWins=10**400, MatchMvpCount=2.9)
        counters['Bad Key'] = {'Counters': {'Kills': 1}}
        counters['UnrankedNothing'] = {'Counters': {'Unknown': 3}}
        counters['UnrankedList'] = ['x']
        counters[''] = {'Counters': {'Kills': 1}}
        for i in range(30):
            counters[f'UnrankedExtra{chr(65+i % 26)}{chr(65+i // 26)}'] = {'Counters': {'Kills': i}}
        unranked = summarize_arena(raw)['sections'][0]
        modes = dict(unranked['modes'])
        self.assertEqual(modes['Team Fight'], {'kills': 0, 'mvp': 2})
        self.assertNotIn('Nothing', modes)
        self.assertNotIn('List', modes)
        self.assertEqual(len(unranked['modes']), MAX_MODES)
        self.assertFalse([section for section in summarize_arena(raw)['sections'] if any(' ' in key for key, _ in section['modes'] if key not in ('Team Fight', 'Last Hero', 'Blast Gang'))])

    def test_odd_shapes_never_raise(self):
        values = (None, 1, 'x', [], {}, {'Counters': None}, {'Counters': []}, {'Counters': {'Kills': None}})
        for stat, entry in itertools.product((None, [], {}, {'arenaOverAllCounters': None}, {'arenaOverAllCounters': []}), values):
            with self.subTest(stat=stat, entry=entry):
                raw = arena_profile(stat=stat)
                summarize_arena(raw)
                raw = arena_profile()
                raw['stat']['arenaOverAllCounters']['UnrankedTeamFight'] = copy.deepcopy(entry)
                render_arena_text(card(raw))
                render_arena_markdown(card(raw))

    def test_not_a_profile(self):
        for raw in ({}, {'info': None}, {'info': {'nickname': 5}}):
            with self.subTest(raw=raw):
                with self.assertRaises(QueryError):
                    summarize_arena(raw)


class ArenaOutputTests(unittest.TestCase):
    def test_text(self):
        text = render_arena_text(card(refresh_url='https://tarkov.dev/players/arena/1234567'))
        self.assertEqual(text.split('\n')[:9], [
            '【塔科夫竞技场档案】Test_Player（账号 ID 1234567）',
            '成就 2 个｜最近活跃 2026-09-10 08:26',
            '',
            '非排位：对局 200，击杀 1500，死亡 600，K/D 2.50，最佳 ARP 1200（Jackal），最长连杀 12，最长连胜 7',
            '　Team Fight：对局 100，胜 60（60.0%），击杀 800，死亡 300，K/D 2.67，MVP 10',
            '　Last Hero：对局 50，胜 10（20.0%），击杀 400，死亡 200，K/D 2.00，MVP 4',
            '　Checkpoint：对局 40，胜 20（50.0%），击杀 250，死亡 80，K/D 3.12，MVP 3',
            '　ShootOutDuo：对局 10，胜 5（50.0%），击杀 50，死亡 20，K/D 2.50',
            '排位：对局 30，击杀 200，死亡 100，K/D 2.00，最佳 ARP 1500（Jackal）',
        ])
        self.assertIn('数据不会自动更新', text)
        self.assertIn('https://tarkov.dev/players/arena/1234567', text)
        # Maintainer: no level for Arena.
        self.assertNotIn('等级', text)

    def test_markdown(self):
        text = render_arena_markdown(card(notes=('a_b',), refresh_url='https://tarkov.dev/players/arena/1'))
        self.assertIn('## 塔科夫竞技场档案：Test\\_Player', text)
        self.assertIn('### 非排位', text)
        self.assertIn('### 排位', text)
        self.assertIn('| Team Fight | 100 | 60 | 60.0% | 800 | 300 | 2.67 | 10 |', text)
        self.assertIn('| Team Fight | 20 | 12 | 60.0% | 150 | 70 | 2.14 | 2 |', text)
        self.assertIn('> a\\_b', text)
        self.assertIn('[这名玩家的页面](https://tarkov.dev/players/arena/1)', text)
        self.assertNotIn('等级', text)

    def test_no_games(self):
        raw = arena_profile(stat={'arenaOverAllCounters': {}})
        self.assertIn('暂无竞技场对局记录', render_arena_text(card(raw)))
        self.assertIn('暂无竞技场对局记录', render_arena_markdown(card(raw)))

    def test_stale(self):
        self.assertIn('天没有更新', render_arena_text(build_arena_card(summarize_arena(arena_profile()), now=NOW+40*86400)))


def picture(c):
    data = arena_image.render_arena_image(c)
    img = Image.open(io.BytesIO(data))
    img.load()
    return data, img


class ArenaImageTests(unittest.TestCase):
    def test_renders_a_stable_jpeg(self):
        data, img = picture(card(refresh_url='https://tarkov.dev/players/arena/1234567'))
        self.assertEqual((img.format, img.width), ('JPEG', arena_image.W))
        self.assertTrue(800 < img.height < 2000)
        self.assertLess(len(data), 1024*1024)
        self.assertEqual(data, picture(card(refresh_url='https://tarkov.dev/players/arena/1234567'))[0])

    def test_registry_content(self):
        drawn = []
        real = ImageDraw.ImageDraw.text
        def record_text(self, xy, text, *args, **kwargs):
            drawn.append(text)
            return real(self, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', record_text):
            picture(card(notes=('这个账号在 PVE 模式下也有档案',), refresh_url='https://tarkov.dev/players/arena/1'))
        self.assertIn('ARENA MASTERS', drawn)
        self.assertIn('Test_Player', drawn)
        self.assertIn('1500', drawn)  # best ARP on the coin: the highest of all sections
        self.assertIn('非排位  UNRANKED · 不计 ARP', drawn)
        self.assertIn('排位  RANKED · 计入 ARP', drawn)
        self.assertIn('95 W  ·  105 L', drawn)
        self.assertIn('TEAM FIGHT', drawn)
        self.assertIn('60 – 40', drawn)
        self.assertIn('这个账号在 PVE 模式下也有档案', drawn)
        self.assertTrue(any('数据不会自动更新' in text for text in drawn))
        # Maintainer: no level; the footer is the last thing on the page.
        self.assertFalse([text for text in drawn if '等级' in text or 'LVL' in text])
        self.assertIn('Jackal', drawn)  # rank under the ARP on the coin
        self.assertEqual(drawn[-1], '本插件在 GitHub 开源，遵循 GPL-3.0-or-later 许可；与 Battlestate Games 无直接关系')

    def test_odd_cards_still_render(self):
        raw = arena_profile(stat={'arenaOverAllCounters': {f'UnrankedMode{chr(65+i)}': {'Counters': {'Kills': 10**15, 'GamesCount': 3, 'ArenaWins': 9}}
                                                            for i in range(20)}}, info_nickname='名字\u202e'+'W'*40)
        _, img = picture(card(raw))
        self.assertLess(img.height, 2000)  # at most MAX_MODES rows per section
        for raw in (arena_profile(stat={}), arena_profile(aid=None, updated=None, achievements=None)):
            with self.subTest(raw=list(raw)):
                picture(card(raw))

    def test_helpers(self):
        self.assertEqual(losses({'games': 10, 'wins': 4}), 6)
        self.assertEqual(losses({'games': 10, 'wins': 4, 'losses': 5}), 5)
        self.assertIsNone(losses({'games': 10}))
        self.assertEqual(losses({'games': 3, 'wins': 9}), 0)
        c = card()
        self.assertEqual([record(section) for section in c['sections']], [(95, 105), (16, 14)])
        self.assertIsNone(record({'modes': [('X', {'games': 1})]}))
        self.assertEqual(best_arp(c), 1500)
        self.assertIsNone(best_arp(card(arena_profile(stat={}))))


class ArenaRankTests(unittest.TestCase):
    def test_ranks_by_best_arp(self):
        # Maintainer's table; neighbouring ranks overlap by 100, a rank counts once the previous one's limit is passed.
        cases = {1: 'Marauder', 1000: 'Marauder', 1001: 'Jackal', 2000: 'Jackal', 2001: 'Mercenary', 3001: 'Fighter',
                 4001: 'Warrior', 5001: 'Gladiator', 6001: 'Veteran', 7000: 'Veteran', 7001: 'Professional', 7900: 'Professional'}
        for arp, name in cases.items():
            with self.subTest(arp=arp):
                self.assertEqual(rank(arp)[1], name)
        self.assertEqual(rank(1001)[0], 'D+')
        self.assertEqual(rank(7901), ('TOP', TOP_LABEL))
        self.assertIsNone(rank(None))
        self.assertIsNone(rank(0))

    def test_top_ranks_hide_the_arp(self):
        self.assertEqual(arp_text(1610), '1610（Jackal）')
        self.assertEqual(arp_text(9000), '竞技场前100名玩家')
        raw = arena_profile()
        raw['stat']['arenaOverAllCounters']['Overall']['Counters']['BestArp'] = 8200
        c = card(raw)
        self.assertIn('排位：对局 30，击杀 200，死亡 100，K/D 2.00，最佳 ARP 竞技场前100名玩家', render_arena_text(c))
        drawn = []
        real = ImageDraw.ImageDraw.text
        def record_text(self, xy, text, *args, **kwargs):
            drawn.append(text)
            return real(self, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', record_text):
            picture(c)
        self.assertIn('前100名玩家', drawn)
        self.assertNotIn('8200', drawn)

    def test_arena_text_and_markdown_end_with_the_source(self):
        notice = '本插件在 GitHub 开源，遵循 GPL-3.0-or-later 许可；与 Battlestate Games 无直接关系'
        self.assertTrue(render_arena_text(card()).endswith(notice))
        self.assertTrue(render_arena_markdown(card()).endswith('> '+notice))
