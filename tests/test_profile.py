import unittest

from fixtures import profile
from tarkov_core.modes import parse_mode, path_of
from tarkov_core.profile import STATIC, counter, level_of, side_stats, summarize
from tarkov_core.transport import QueryError


def cumulative(level):
    """Total experience at which `level` is reached, from the bundled table."""
    return sum(exp for row, exp in STATIC['player_levels'] if row <= level)


class LevelTests(unittest.TestCase):
    def test_level_boundaries(self):
        # Same rule as tarkov.dev's page: a level is reached when total experience equals its cumulative value.
        self.assertEqual(level_of(0), 1)
        self.assertEqual(level_of(999), 1)
        self.assertEqual(level_of(1000), 2)
        self.assertEqual(level_of(cumulative(40)-1), 39)
        self.assertEqual(level_of(cumulative(40)), 40)
        self.assertEqual(level_of(10**12), STATIC['player_levels'][-1][0])

    def test_hand_computed_level(self):
        # 12,000,000 lies between the cumulative experience of level 53 and 54 in the 2026-10-13 table.
        self.assertLessEqual(cumulative(53), 12000000)
        self.assertGreater(cumulative(54), 12000000)
        self.assertEqual(summarize(profile())['level'], 53)

    def test_static_table_shape(self):
        levels = [row for row, _ in STATIC['player_levels']]
        self.assertEqual(levels, list(range(1, len(levels)+1)))
        self.assertEqual(STATIC['player_levels'][0], [1, 0])
        self.assertEqual(STATIC['skills']['Lockpicking']['zh'], '撬锁')


class CounterTests(unittest.TestCase):
    def test_shortest_matching_key_wins(self):
        items = [{'Key': ['Kills', 'Something'], 'Value': 5}, {'Key': ['Kills'], 'Value': 9}]
        self.assertEqual(counter(items, 'Kills'), 9)
        self.assertEqual(counter(items, 'Deaths'), 0)

    def test_side_stats(self):
        stats = side_stats(profile()['pmcStats'])
        self.assertEqual(stats, {'raids': 1000, 'survived': 800, 'killed': 120, 'mia': 10, 'runner': 30, 'transit': 40,
                                 'kills': 5000, 'deaths': 100, 'pmc_kills': 900, 'streak': 40})

    def test_old_left_status_counts_as_mia(self):
        block = {'eft': {'overAllCounters': {'Items': [{'Key': ['ExitStatus', 'Left', 'Pmc'], 'Value': 4}]}}}
        self.assertEqual(side_stats(block)['mia'], 4)

    def test_malformed_counters_are_ignored(self):
        for block in (None, [], {'eft': None}, {'eft': {'overAllCounters': {'Items': 'x'}}},
                      {'eft': {'overAllCounters': {'Items': [None, {'Key': 'Kills', 'Value': 3}, {'Key': ['Kills'], 'Value': True}]}}}):
            with self.subTest(block=block):
                self.assertEqual(side_stats(block)['kills'], 0)


class SummaryTests(unittest.TestCase):
    def test_summary(self):
        s = summarize(profile())
        self.assertEqual((s['aid'], s['nickname'], s['side'], s['prestige'], s['achievements']), (1234567, 'Test_Player', 'USEC', 0, 2))
        self.assertEqual(s['play_seconds'], 3600000)
        self.assertEqual(s['updated'], 1791000000)
        # The latest of skill access and achievement times.
        self.assertEqual(s['last_active'], 1790600000)

    def test_skills_known_trained_and_sorted(self):
        self.assertEqual(summarize(profile())['skills'],
                         [('耐力', 51, True), ('力量', 51, True), ('魅力', 40, False), ('活力', 25, False)])

    def test_not_a_profile(self):
        for raw in ({}, {'info': None}, {'info': {'nickname': 3}}):
            with self.subTest(raw=raw):
                with self.assertRaises(QueryError):
                    summarize(raw)

    def test_missing_optional_fields(self):
        s = summarize({'info': {'nickname': 'X', 'experience': 'many'}})
        self.assertIsNone(s['level'])
        self.assertEqual((s['side'], s['achievements'], s['skills'], s['last_active'], s['updated']), ('未知', 0, [], None, None))
        self.assertEqual(s['pmc']['raids'], 0)


class ModeTests(unittest.TestCase):
    def test_aliases(self):
        for word, mode in (('PVP', 'pvp'), ('regular', 'pvp'), ('常规', 'pvp'), ('pve', 'pve'), ('赛季', 'season'), ('pvp-season', 'season')):
            with self.subTest(word=word):
                self.assertEqual(parse_mode(word), mode)
        self.assertIsNone(parse_mode('Some_Player'))
        self.assertIsNone(parse_mode(None))

    def test_regular_mode_lives_under_profile(self):
        self.assertEqual(path_of('pvp'), 'profile')
        self.assertEqual(path_of('season'), 'pvp-season')
