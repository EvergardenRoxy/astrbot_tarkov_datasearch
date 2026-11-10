"""Weekly report maths and pictures. Every number is invented (fixtures.atlas_profile)."""
import copy
import datetime
import io
import unittest

from PIL import Image

from fixtures import TEST_FONT, atlas_profile
from tarkov_core import atlas_card
from tarkov_core.fonts import use_chinese_font
from tarkov_core.weekly_image import render_weekly_image
from tarkov_core.weekly_report import (WEEK, build_card, build_ranking, build_report, extract_sample, game_day,
                                       increase, window)

# Monday 2026-11-09 03:00 UTC+8: the newest nightly sample of the examples.
LAST = 1794164400
DAY = 86400


def iso(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat()


def sample(at, raids, survived, transit, killed, runner, kills, players, **more):
    document = atlas_profile('pve', looked_up=iso(at))
    document['stats']['pmc']['counters'] = [
        [['Sessions', 'Pmc'], raids], [['ExitStatus', 'Survived', 'Pmc'], survived],
        [['ExitStatus', 'Transit', 'Pmc'], transit], [['ExitStatus', 'Killed', 'Pmc'], killed],
        [['ExitStatus', 'Runner', 'Pmc'], runner], [['Kills'], kills], [['KilledPmc'], players],
        [['LongestWinStreak', 'Pmc'], more.pop('streak', 40)]]
    document.update(more)
    return extract_sample(document, 'pve')


def week():
    """Nightly samples from the Sunday nine days before LAST to LAST; only some nights have play."""
    return [
        sample(LAST-8*DAY, 990, 795, 40, 118, 30, 4950, 890),
        sample(LAST-7*DAY, 1000, 800, 40, 120, 30, 5000, 900),                       # Monday 03:00, a week before
        sample(LAST-6*DAY, 1002, 801, 40, 121, 30, 5010, 902),
        sample(LAST-4*DAY, 1012, 808, 41, 123, 30, 5060, 915),                       # one night missed
        sample(LAST-2*DAY, 1030, 820, 42, 127, 31, 5150, 940, level=51, experience=12500000),
        sample(LAST-DAY, 1030, 820, 42, 127, 31, 5150, 940, level=51, experience=12500000),
        sample(LAST, 1037, 825, 42, 129, 31, 5180, 950, level=51, experience=12600000, streak=44),
    ]


class SampleTests(unittest.TestCase):
    def test_a_document_becomes_compact_numbers(self):
        first = extract_sample(atlas_profile('pve'), 'pve')
        self.assertEqual((first['looked_up'], first['nickname'], first['level'], first['play']), (1793505600.0, 'Test_Player', 50, 3600000))
        self.assertEqual(first['pmc'], {'raids': 1000, 'survived': 800, 'killed': 120, 'mia': 10, 'left': 0, 'runner': 30,
                                        'transit': 40, 'kills': 5000, 'player_kills': 900, 'streak': 40})
        self.assertEqual((first['scav']['raids'], first['skills'], first['achievements'], first['recent']),
                         (50, {'Endurance': 5100, 'Strength': 2500}, 2, []))

    def test_recent_achievements_keep_their_names(self):
        doc = atlas_profile('pve', looked_up='2026-11-08T04:00:00+00:00')
        doc['achievements']['done'].append({'id': 'a3', 'at': 1794000000, 'rarity': 'Legendary', 'percent': 1, 'ch': {'name': '新成就'}})
        self.assertEqual(extract_sample(doc, 'pve')['recent'],
                         [{'id': 'a3', 'name': '新成就', 'rarity': '传奇', 'tone': 'legendary', 'at': 1794000000}])

    def test_unusable_documents_and_odd_fields(self):
        # Arena is planned, not built.
        self.assertIsNone(extract_sample(atlas_profile('arena'), 'arena'))
        for bad in (None, [], {}, {'nickname': ''}, atlas_profile('pve', looked_up='x'), atlas_profile('pve', looked_up=None)):
            self.assertIsNone(extract_sample(bad, 'pve'))
        for value in (None, 'x', 1, 1.5, True, [], {}, [[None]], float('inf')):
            for field in ('stats', 'skills', 'achievements', 'experience', 'level', 'prestigeLevel'):
                with self.subTest(field=field, value=value):
                    result = extract_sample(atlas_profile('pve', **{field: value}), 'pve')
                    if field == 'stats':
                        self.assertIsNone(result)
                    else:
                        self.assertEqual(result['nickname'], 'Test_Player')


class ReportTests(unittest.TestCase):
    def test_missing_statistics_cannot_turn_a_gap_into_a_reset(self):
        docs = [atlas_profile('pve', looked_up=iso(LAST-offset*DAY)) for offset in (2, 1, 0)]
        docs[0]['stats']['pmc']['counters'] = [[['Sessions'], 1000]]
        docs[1]['stats']['pmc'] = None
        docs[2]['stats']['pmc']['counters'] = [[['Sessions'], 1005]]
        samples = [extract_sample(doc, 'pve') for doc in docs]
        self.assertIsNone(samples[1])
        report = build_report([s for s in samples if s is not None], 'pve', LAST)
        self.assertEqual(report['pmc']['raids'], 5)
        self.assertFalse(report['reset'])

    def test_sparse_and_empty_counters_are_valid_but_corruption_is_not(self):
        doc = atlas_profile('pve')
        doc['stats']['pmc']['counters'] = [[['Sessions'], 0]]
        doc['stats']['scav']['counters'] = []
        self.assertEqual(extract_sample(doc, 'pve')['scav']['raids'], 0)
        for side in ('pmc', 'scav'):
            for bad in (None, {}, [], {'counters': None}, {'counters': {}}, {'counters': [None]},
                        {'counters': [[['Sessions'], None]]}, {'counters': [[['Sessions'], True]]},
                        {'counters': [[['Sessions'], -1]]}, {'counters': [[['Sessions'], float('inf')]]},
                        {'counters': [[['Sessions', {}], 1]]}):
                with self.subTest(side=side, bad=bad):
                    broken = copy.deepcopy(doc)
                    broken['stats'][side] = bad
                    self.assertIsNone(extract_sample(broken, 'pve'))

    def test_a_counter_that_drops_restarted_from_zero(self):
        self.assertEqual(increase([1000, 1010, 3, 9]), (19, True))
        self.assertEqual(increase([5, None, 7]), (2, False))
        self.assertEqual((increase([]), increase([4])), ((0, False), (0, False)))

    def test_rolling_seven_days_back_from_the_newest_sample(self):
        report = build_report(list(reversed(week())), 'pve', LAST+7*3600)
        self.assertEqual((report['covered_from'], report['covered_to'], report['samples'], report['span_days']),
                         (LAST-7*DAY, LAST, 6, 7))
        self.assertEqual(report['pmc'], {'raids': 37, 'survived': 25, 'killed': 9, 'mia': 0, 'left': 0, 'runner': 1,
                                         'transit': 2, 'kills': 180, 'player_kills': 50})
        self.assertEqual((report['total'], report['scav']['raids']), (report['pmc'], 0))
        self.assertEqual((report['career']['raids'], report['career']['player_kills']), (1087, 970))
        self.assertEqual((report['level'], report['experience'], report['streak_record'], report['reset']),
                         ((50, 51), 600000, 44, False))
        # A 03:00 sample closes the day before; a step over a missed night lands on the later day.
        self.assertEqual(report['days'][0], ('周一', '11-02'))
        self.assertEqual(report['days'][-1], ('周日', '11-08'))
        self.assertEqual(report['daily'], [2, 0, 10, 0, 18, 0, 7])
        self.assertEqual(report['daily_sampled'], [True, False, True, False, True, True, True])
        self.assertEqual(atlas_card.combat_row('PMC', dict(report['pmc'], streak=0))['values'],
                         ['37', '25', '73.0%', '180', '9', '20.00', '50', '5.56'])

    def test_the_start_is_the_sample_nearest_to_a_week_before(self):
        samples = week()
        # Without the exact one, the neighbour within two days starts the week (here the day after).
        without = [s for s in samples if s['looked_up'] != LAST-7*DAY]
        self.assertEqual(build_report(without, 'pve', LAST)['covered_from'], LAST-8*DAY)
        self.assertEqual(window(samples[2:], LAST)[0]['looked_up'], LAST-6*DAY)
        # Listed three days ago: the oldest sample inside the week starts it, and the picture says so.
        short = build_report(samples[4:], 'pve', LAST)
        self.assertEqual((short['span_days'], short['pmc']['raids']), (2, 7))
        self.assertEqual(build_card(short)['notes'], ['样本只覆盖 2 天'])

    def test_no_report_without_two_spread_samples_or_with_stale_ones(self):
        samples = week()
        self.assertIsNone(build_report([], 'pve', LAST))
        self.assertIsNone(build_report(samples[-1:], 'pve', LAST))
        # Two lookups a few minutes apart are not a week.
        close = [samples[-1], sample(LAST+600, 1038, 826, 42, 129, 31, 5190, 951)]
        self.assertIsNone(build_report(close, 'pve', LAST+700))
        # Sampling stopped: after three days there is no report any more.
        self.assertIsNotNone(build_report(samples, 'pve', LAST+3*DAY))
        self.assertIsNone(build_report(samples, 'pve', LAST+3*DAY+1))
        # Samples from the future (a wrong clock) are ignored.
        self.assertEqual(build_report(samples+[sample(LAST+5*DAY, 9, 9, 9, 9, 9, 9, 9)], 'pve', LAST)['covered_to'], LAST)

    def test_a_wipe_in_the_week(self):
        wiped = [sample(LAST-7*DAY, 1000, 800, 40, 120, 30, 5000, 900), sample(LAST-6*DAY, 1010, 805, 40, 122, 30, 5040, 910),
                 sample(LAST-3*DAY, 3, 2, 0, 1, 0, 9, 1, level=1, experience=5000),
                 sample(LAST, 9, 6, 0, 2, 0, 30, 4, level=3, experience=20000)]
        report = build_report(wiped, 'pve', LAST)
        self.assertTrue(report['reset'])
        self.assertEqual((report['pmc']['raids'], report['pmc']['kills'], report['experience']), (19, 70, 20000))
        self.assertEqual(build_card(report)['notes'], ['期间数据有过重置（删档、赛季重置或声望）'])

    def test_achievements_and_skills(self):
        samples = week()
        ending = copy.deepcopy(samples[-1])
        ending['recent'] = [{'id': 'x', 'name': '甲', 'rarity': '传奇', 'tone': 'legendary', 'at': LAST-DAY},
                            {'id': 'y', 'name': '乙', 'rarity': '普通', 'tone': 'common', 'at': LAST-8*DAY}]
        ending['skills'] = {'Endurance': 5100, 'Strength': 2650, 'Vitality': 120}
        report = build_report(samples[:-1]+[ending], 'pve', LAST)
        self.assertEqual([a['id'] for a in report['achievements']], ['x'])
        self.assertEqual(report['skills'], [('Strength', 2500, 2650), ('Vitality', 0, 120)])
        card = build_card(report)
        self.assertEqual(card['skills'], [('力量', 'Lv 25 → 26', '+1.50'), ('活力', 'Lv 0 → 1', '+1.20')])
        self.assertEqual(card['achievements'], [{'name': '甲', 'rarity': '传奇', 'tone': 'legendary', 'when': '11-08 03:00'}])

    def test_the_card(self):
        card = build_card(build_report(week(), 'pve', LAST))
        self.assertEqual((card['mode_label'], card['range'], card['level'], card['experience'], card['play'], card['notes']),
                         ('PVE', '11-02 ～ 11-08', '50 → 51', '+600,000', '—', []))
        self.assertEqual(card['tiles'][0], ('战局', '37', '生涯 1,087'))
        self.assertEqual([tile[:2] for tile in card['tiles'][1:4]], [('幸存率', '73.0%'), ('击杀 PMC', '50'), ('PMC K/D', '5.56')])
        self.assertEqual([row['title'] for row in card['combat_rows']], ['PMC', 'SCAV'])
        self.assertEqual(card['daily'][0], ('周一', '11-02', 2, True))
        self.assertEqual(game_day(LAST).isoformat(), '2026-11-08')
        self.assertEqual(WEEK, 7*DAY)


class RankingTests(unittest.TestCase):
    def reports(self):
        busy = build_report(week(), 'pve', LAST)
        calm = copy.deepcopy(busy)
        calm.update(nickname='Calm_One', level=(30, 30))
        calm['total'] = dict(busy['total'], raids=6, survived=6, transit=0, killed=0, player_kills=4, kills=20)
        idle = copy.deepcopy(busy)
        idle.update(nickname='Idle_One')
        idle['total'] = {key: 0 for key in busy['total']}
        return busy, calm, idle

    def test_most_raids_first_and_the_highlights(self):
        busy, calm, idle = self.reports()
        card = build_ranking([('Idle_One', idle), ('Calm_One', calm), ('Nobody', None), ('Test_Player', busy)], 'pve')
        self.assertEqual([(row['place'], row['nickname'], row['raids']) for row in card['rows']],
                         [(1, 'Test_Player', '37'), (2, 'Calm_One', '6'), (3, 'Idle_One', '0')])
        self.assertEqual((card['rows'][0]['survival'], card['rows'][0]['kd'], card['rows'][0]['level']), ('73.0%', '5.56', '50→51'))
        self.assertEqual(card['rows'][1]['kd'], '—')
        self.assertEqual((card['missing'], card['more'], card['range'], card['mode_label']), (['Nobody'], 0, '11-02 ～ 11-08', 'PVE'))
        self.assertEqual(card['highlights'], [('战局最多', 'Test_Player', '37 局'), ('幸存率最高', 'Calm_One', '100.0%'),
                                              ('击杀 PMC 最多', 'Test_Player', '50'), ('总击杀最多', 'Test_Player', '180')])

    def test_nobody_with_a_report_and_long_lists(self):
        busy, _, idle = self.reports()
        self.assertIsNone(build_ranking([('A', None), ('B', None)], 'pve'))
        self.assertIsNone(build_ranking([], 'pve'))
        # Nobody played: no winner is invented.
        self.assertEqual(build_ranking([('Idle_One', idle)], 'pve')['highlights'][0], ('战局最多', '—', ''))
        # The best survival rate needs at least five raids.
        few = copy.deepcopy(busy)
        few['total'] = dict(busy['total'], raids=4, survived=4, transit=0)
        self.assertEqual(build_ranking([('Few', few)], 'pve')['highlights'][1], ('幸存率最高', '—', ''))
        many = build_ranking([(f'P{i}', dict(copy.deepcopy(busy), nickname=f'P{i}')) for i in range(23)], 'pve')
        self.assertEqual((len(many['rows']), many['more']), (20, 3))


class PictureTests(unittest.TestCase):
    def setUp(self):
        use_chinese_font(TEST_FONT)

    def test_personal_pictures_draw_for_every_kind_of_week(self):
        samples = week()
        quiet = build_report([samples[1], dict(copy.deepcopy(samples[1]), looked_up=LAST)], 'pve', LAST)
        self.assertEqual(quiet['total']['raids'], 0)
        for report in (build_report(samples, 'pve', LAST), build_report(samples[4:], 'pve', LAST), quiet):
            card = build_card(report)
            picture = Image.open(io.BytesIO(render_weekly_image(card)))
            self.assertEqual((picture.format, picture.width), ('JPEG', 1600))
            self.assertTrue(1500 < picture.height < 4000)
        card = build_card(build_report(samples, 'pve', LAST))
        # Byte-stable, and the Atlas logo is on it (bright pixels at the header's right end).
        self.assertEqual(render_weekly_image(card), render_weekly_image(card))
        header = Image.open(io.BytesIO(render_weekly_image(card))).crop((1300, 40, 1560, 100)).convert('L')
        self.assertGreater(header.getextrema()[1], 200)
        # A note makes the header taller, never overlapping the tiles.
        plain = Image.open(io.BytesIO(render_weekly_image(card))).height
        noted = Image.open(io.BytesIO(render_weekly_image(dict(card, notes=['样本只覆盖 2 天'])))).height
        self.assertEqual(noted-plain, 36)

    def test_ranking_picture_and_long_names(self):
        busy = build_report(week(), 'pve', LAST)
        entries = [(f'Player_{i}', dict(copy.deepcopy(busy), nickname='W'*24 if i == 0 else f'Player_{i}')) for i in range(22)]
        card = build_ranking(entries+[('Missing_One', None)], 'pve')
        picture = Image.open(io.BytesIO(render_weekly_image(card)))
        self.assertEqual((picture.format, picture.width), ('JPEG', 1600))
        short = Image.open(io.BytesIO(render_weekly_image(build_ranking(entries[:2], 'pve'))))
        self.assertEqual(picture.height-short.height, 18*72+2*36)
        header = picture.crop((1300, 40, 1560, 100)).convert('L')
        self.assertGreater(header.getextrema()[1], 200)


if __name__ == '__main__':
    unittest.main()
