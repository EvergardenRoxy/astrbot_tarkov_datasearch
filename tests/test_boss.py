import asyncio
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from tarkov_core.boss_monitor import BOSS_SCOPE, DAILY_BOSSES, build_table, normalize_bosses, percent, probability, state_fingerprint
from tarkov_core.boss_notifications import BossNotifications, BossStore, PAGES_PER_CYCLE, QUERY_SECONDS
from tarkov_core import boss_table_image
from tarkov_core.boss_table_image import BOSS_ROWS, FONT_SIZE, MAPS_PER_PAGE, MAX_CHANGE_LINES, RED, TEXT as INK, change_lines, compact_rows, notes_for, paginate_table, render_boss_page
from tarkov_core.image import font
from tarkov_core.public_api import DataError, ResourceKey, decode
from tarkov_core.transport import QueryError


def boss_data(chance=0.35, count=1):
    return {'maps': {'factory': {'name': 'factory-name', 'bosses': [
        {'mob': 'bossTagilla', 'spawnChance': chance, 'spawnTime': i, 'spawnTimeRandom': False, 'spawnTrigger': None,
         'spawnLocations': [{'name': 'zone', 'spawnKey': 'zone', 'chance': 1, 'positions': [{'x': 1, 'y': 2}]}]}
        for i in range(count)]}, 'empty': {'name': 'Empty Map', 'bosses': []}},
        'mobs': {'bossTagilla': {'name': 'Alpha'}, 'bossBully': {'name': 'Beta'}}}


def boss_card(chance=0.35, mode='pvp', count=1, checked=100):
    data = boss_data(chance, count)
    if count > 2:
        records = data['maps'].pop('factory')['bosses']
        data['maps'].update({f'factory-{i}':{'name':f'Factory {i}','bosses':[row]} for i,row in enumerate(records)})
    return {'mode': mode, 'state': normalize_bosses(data), 'checked_at': checked,
            'fetched_at': checked, 'stale': False, 'translation_warning': False}


class BossModelTests(unittest.TestCase):
    def test_scope_only_contains_daily_bosses_and_goons_and_excludes_icebreaker(self):
        data = boss_data()
        data['maps']['factory']['bosses'] = [{'mob':mob,'spawnChance':0.4} for mob in DAILY_BOSSES]
        data['mobs'].update({mob:{'name':mob} for mob in DAILY_BOSSES})
        for mob in ('bear','usec','pmcBEAR','pmcUSEC','bossTagillaAgro','followerBully'):
            data['mobs'][mob] = {'name':'BEAR' if 'bear' in mob.lower() else 'USEC'}
            data['maps']['factory']['bosses'].append({'mob':mob,'spawnChance':0.5 if mob == 'bossTagillaAgro' else None})
        excluded = {'69af492a4819ea4ba10a69c5':'icebreaker', '653e6760052c01c1c805532f':'ground-zero',
                    '6a294a5b5eb5f9a1700417b7':'the-lab-dark', '6733700029c367a3d40b02af':'the-labyrinth',
                    '68236e8153654e8c1200798a':'ground-zero-tutorial', '65cc8f81a9aac3e77d0cfd3e':'terminal'}
        for map_id, name in excluded.items():
            data['maps'][map_id] = copy.deepcopy(data['maps']['factory'])
            data['maps']['other-'+name] = {**copy.deepcopy(data['maps']['factory']), 'normalizedName':name}
        data['maps']['65b8d6f5cdde2479cb2a3125'] = {'name':'中心区 21+', 'normalizedName':'ground-zero-21',
                                               'bosses':[{'mob':'sectantPriest','spawnChance':0.02}]}
        state = normalize_bosses(data)
        self.assertEqual({m['map_id'] for m in state}, {'factory','empty','65b8d6f5cdde2479cb2a3125'})
        center = next(m for m in state if m['map_id'] == '65b8d6f5cdde2479cb2a3125')
        self.assertEqual(center['map_name'],'中心区')
        self.assertEqual(center['rows'][0]['chance'],'0.02')
        display_rows, _ = build_table(None,state)
        columns = [column for page in paginate_table({'mode':'pve'},display_rows) for column in page['columns']]
        self.assertEqual(columns[0]['name'],'中心区')
        self.assertEqual({column['map_id'] for column in columns},{m['map_id'] for m in state})
        entries = next(m['rows'] for m in state if m['map_id'] == 'factory')
        self.assertEqual({r['mob_id'] for r in entries if not r['event']}, DAILY_BOSSES)
        # Anything else that is no ordinary soldier is an event boss (here the unnamed Shadow of Tagilla).
        self.assertEqual({r['mob_id'] for r in entries if r['event']}, {'bossTagillaAgro'})
        self.assertEqual(len(entries),13)
        self.assertEqual(next(r['boss_name'] for r in entries if r['mob_id']=='bossKnight'),'三狗 GOONS')

    def test_invalid_or_missing_probabilities_and_records_are_not_zero(self):
        for value in (None, True, '0.3', float('nan'), float('inf'), -0.1, 1.1):
            with self.subTest(value=value), self.assertRaises(QueryError): normalize_bosses(boss_data(value))
        for data in ({}, {'maps': {}, 'mobs': {}}, {**boss_data(), 'mobs': {}},
                     {**boss_data(), 'maps': {'bad': {'name': 'bad'}}}):
            with self.subTest(data=data), self.assertRaises(QueryError): normalize_bosses(data)
        invalid = boss_data()
        invalid['maps']['factory']['bosses'][0].pop('spawnChance')
        with self.assertRaises(DataError): decode(ResourceKey('maps', 'pve'), json.dumps({'data': invalid}).encode())

    def test_order_coordinates_translation_and_unrelated_fields_do_not_change_rates(self):
        data = boss_data(count=2)
        before = normalize_bosses(data)
        modified = copy.deepcopy(data)
        modified['maps']['factory']['bosses'].reverse()
        for row in modified['maps']['factory']['bosses']:
            row['spawnLocations'][0]['positions'] = [{'x': 999}]
            row['spawnLocations'][0]['chance'] = 1.0
            row['spawnTime'] = float(row['spawnTime'])
            row['escorts'] = [{'mob': 'follower', 'equipment': 'changed'}]
        after = normalize_bosses(modified, [{'factory-name': '工厂', 'Alpha': 'Boss Alpha', 'zone': '区域'}])
        self.assertEqual(state_fingerprint(before), state_fingerprint(after))
        self.assertFalse(build_table(before, after)[1])

    def test_duplicates_keep_multiset_identity_and_probability_changes_have_percentage_points(self):
        data = boss_data()
        data['maps']['factory']['bosses'].append(copy.deepcopy(data['maps']['factory']['bosses'][0]))
        before = normalize_bosses(data)
        changed = copy.deepcopy(data)
        changed['maps']['factory']['bosses'][0]['spawnChance'] = 0.4
        rows, difference = build_table(before, normalize_bosses(changed))
        self.assertTrue(difference)
        entries = [row for row in rows if row['boss_name'] == 'Alpha']
        self.assertEqual(len(entries), 2)
        self.assertEqual([r['change'] for r in entries if r['changed']], ['+5 个百分点'])
        self.assertEqual(sum(not r['changed'] for r in entries), 1)

    def test_rule_changes_keep_removed_and_added_conditions_and_empty_maps(self):
        data = boss_data()
        before = normalize_bosses(data)
        data['maps']['factory']['bosses'][0]['spawnTime'] = 900
        rows, changed = build_table(before, normalize_bosses(data))
        self.assertTrue(changed)
        entries = [r for r in rows if r['boss_name'] == 'Alpha']
        self.assertEqual(len(entries), 2)
        self.assertEqual({r['change'] for r in entries}, {'新增记录/规则', '已移除记录/规则'})
        self.assertTrue(any(r['map_id'] == 'empty' and r['condition'] == '本次数据未列出日常 Boss' for r in rows))
        del data['maps']['factory']
        self.assertTrue(any(r['current'] is None and r['previous'] == '0.35' for r in build_table(before, normalize_bosses(data))[0]))

    def test_floating_point_tails_small_real_changes_and_exact_guarantees(self):
        self.assertEqual(probability(0.35000000000000003), '0.35')
        self.assertEqual(percent(probability(0.3500001)), '35.00001%')
        self.assertEqual(probability(1.0), '1')
        self.assertNotEqual(probability(0.99999999999999), '1')
        self.assertNotEqual(percent(probability(0.99999999999999)), '100%')


class BossImageTests(unittest.TestCase):
    def test_unchanged_guaranteed_boss_and_probability_are_red_bold_in_actual_renderer(self):
        card = boss_card(1)
        rows, changed = build_table(None, card['state'])
        self.assertFalse(changed)
        page = paginate_table({k:v for k,v in card.items() if k != 'state'}, rows)[0]
        captured = []
        real = ImageDraw.ImageDraw.text
        def capture(draw, xy, text, *args, **kwargs):
            captured.append((text, kwargs.get('fill'), kwargs.get('font')))
            return real(draw, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', capture):
            output = render_boss_page(page)
        self.assertIn(('Tagilla', RED, font('sc', 23, 800)), captured)
        self.assertIn(('100%', RED, font('sc', FONT_SIZE, 800)), captured)
        image = Image.open(io.BytesIO(output))
        self.assertEqual((image.width, image.format), (560, 'JPEG'))
        self.assertLessEqual(image.height, 2200)

    def test_changed_rate_is_red_and_unrelated_rows_remain_normal(self):
        data = boss_data()
        data['maps']['factory']['bosses'].append({**copy.deepcopy(data['maps']['factory']['bosses'][0]), 'mob':'bossBully'})
        before = normalize_bosses(data)
        data['maps']['factory']['bosses'][0]['spawnChance'] = 0.4
        rows, _ = build_table(before, normalize_bosses(data))
        page = paginate_table({'mode':'pvp','compared_at':100}, rows)[0]
        captured = []
        real = ImageDraw.ImageDraw.text
        def capture(draw, xy, text, *args, **kwargs):
            captured.append((text,kwargs.get('fill')))
            return real(draw,xy,text,*args,**kwargs)
        with patch.object(ImageDraw.ImageDraw,'text',capture): render_boss_page(page)
        self.assertIn(('40%',RED),captured)
        self.assertFalse(any('百分点' in text for text,_ in captured))
        self.assertIn(('35%',INK),captured)
        self.assertIn(('Reshala',INK),captured)

    def test_matrix_pages_keep_all_map_columns_and_fixed_boss_rows_without_condition_fields(self):
        rows, _ = build_table(None, boss_card(count=80)['state'])
        rows[0]['condition'] = 'hidden spawn condition '*500
        pages = paginate_table({'mode': 'pvp'}, rows)
        self.assertGreater(len(pages), 1)
        self.assertEqual(len({column['map_id'] for page in pages for column in page['columns']}),81)
        self.assertTrue(all(len(page['columns']) <= MAPS_PER_PAGE for page in pages))
        for page in pages:
            self.assertEqual([(r['mob_id'],r['boss_name']) for r in page['rows']],list(BOSS_ROWS))
            self.assertTrue(all(len(row['cells']) == len(page['columns']) for row in page['rows']))
            self.assertTrue(all('condition' not in row and 'change' not in row for row in page['rows']))
        for page in (pages[0], pages[-1]):
            self.assertLessEqual(Image.open(io.BytesIO(render_boss_page(page))).height, 2200)

    def test_multiple_conditional_rates_are_listed_without_summing_or_bolding_non_guaranteed_values(self):
        data = boss_data(count=2)
        data['maps']['factory']['bosses'][1]['spawnChance'] = 1
        rows, _ = build_table(None, normalize_bosses(data))
        summary = next(r for r in compact_rows(rows) if r['mob_id'])
        self.assertEqual(summary['rates'],['0.35','1'])
        captured = []
        real = ImageDraw.ImageDraw.text
        def capture(draw,xy,text,*args,**kwargs):
            captured.append((text,kwargs.get('font'),kwargs.get('fill')))
            return real(draw,xy,text,*args,**kwargs)
        with patch.object(ImageDraw.ImageDraw,'text',capture): render_boss_page(paginate_table({'mode':'pvp'},rows)[0])
        self.assertIn(('100%',font('sc',FONT_SIZE,800),RED),captured)
        self.assertTrue(any('35%' in text and color == INK and f == font('sc',FONT_SIZE,600) for text,f,color in captured))
        self.assertFalse(any('135%' in text for text,_,_ in captured))


class ChangeSummaryTests(unittest.TestCase):
    def rows(self, **override):
        row = {'map_id': 'm', 'map_name': '海岸线', 'mob_id': 'bossKnight', 'boss_name': '三狗 GOONS',
               'previous': '0.6', 'current': '0.8', 'change': '+20 个百分点', 'changed': True}
        return [{**row, **override}]

    def test_one_short_statement_per_change_kind(self):
        self.assertEqual(change_lines(self.rows()), ['三狗 GOONS（海岸线）：60%→80%'])
        self.assertEqual(change_lines(self.rows(previous=None, current='1')), ['三狗 GOONS（海岸线）：新增 100%'])
        self.assertEqual(change_lines(self.rows(previous='1', current=None)), ['三狗 GOONS（海岸线）：移除（原 100%）'])
        self.assertEqual(change_lines(self.rows(mob_id=None, previous=None, current=None, change='地图新增')), ['地图新增：海岸线'])

    def test_unchanged_rows_and_other_pages_are_left_out(self):
        self.assertEqual(change_lines(self.rows(changed=False)), [])
        self.assertEqual(change_lines(self.rows(), {'other'}), [])
        self.assertEqual(change_lines(self.rows(), {'m'}), ['三狗 GOONS（海岸线）：60%→80%'])

    def test_long_lists_are_capped_with_a_count(self):
        lines = change_lines(self.rows()*(MAX_CHANGE_LINES+5))
        self.assertEqual(len(lines), MAX_CHANGE_LINES+1)
        self.assertEqual(lines[-1], '另有 5 项变化未列出')

    def test_summary_is_drawn_below_the_table_and_only_when_given(self):
        page = paginate_table({'mode': 'pve'}, build_table(None, normalize_bosses(boss_data()))[0])[0]
        plain = Image.open(io.BytesIO(render_boss_page(page)))
        with_summary = Image.open(io.BytesIO(render_boss_page({**page, 'change_lines': ['A：1%→2%', 'B：新增 3%']})))
        self.assertGreater(with_summary.height, plain.height+60)
        self.assertEqual(with_summary.width, plain.width)


def feed(mobs, map_id='5b0fc42d86f7744a585f9105'):
    """Feed-shaped data: every mob has the language-independent normalizedName the real feed gives."""
    return {'maps': {map_id: {'name': 'Lab', 'bosses': [{'mob': mob, 'spawnChance': 1} for mob in mobs]}},
            'mobs': {mob: {'name': mob, 'normalizedName': norm} for mob, norm in mobs.items()}}


class EventBossTests(unittest.TestCase):
    SOLDIERS = {'PmcBot': 'raider', 'pmcBotBlackDiv': 'black-div', 'pmcBotBlackDivSeason': 'black-div', 'pmcUSEC': 'usec',
                'pmcBEAR': 'bear', 'exUsecFree': 'rogue', 'Sentry': 'af'}

    def mobs(self, state):
        return {(r['mob_id'], r['event']) for m in state for r in m['rows']}

    def test_ordinary_soldier_types_are_never_events(self):
        self.assertEqual(self.mobs(normalize_bosses(feed(self.SOLDIERS))), set())

    def test_resident_bosses_stay_rows_and_unknown_bosses_become_events(self):
        mobs = {**self.SOLDIERS, 'bossTagilla': 'tagilla', 'bossScourgeEventLab': 'scourge'}
        self.assertEqual(self.mobs(normalize_bosses(feed(mobs))), {('bossTagilla', False), ('bossScourgeEventLab', True)})

    def test_ignore_list_and_id_fallback(self):
        mobs = {'bossScourgeEventLab': 'scourge'}
        self.assertEqual(self.mobs(normalize_bosses(feed(mobs), (), {'scourge'})), set())
        # No normalizedName: soldier-looking ids and followers are not events, other ids are.
        data = feed({'a': 'x'}); data['mobs'] = {m: {'name': m} for m in ('pmcFoo', 'exUsecBar', 'followerX', 'bossNew')}
        data['maps']['5b0fc42d86f7744a585f9105']['bosses'] = [{'mob': m, 'spawnChance': 1} for m in data['mobs']]
        self.assertEqual(self.mobs(normalize_bosses(data)), {('bossNew', True)})

    def test_event_rows_follow_the_fixed_rows_label_and_cap(self):
        mobs = {f'bossEvent{i}': f'event{i}' for i in range(5)}
        rows, _ = build_table(None, normalize_bosses(feed(mobs)))
        page = paginate_table({'mode': 'pve'}, rows)[0]
        labels = [r['label'] for r in page['rows']]
        self.assertEqual(len(page['rows']), len(BOSS_ROWS)+3)
        self.assertEqual(labels[len(BOSS_ROWS):], ['活动 bossEvent0', '活动 bossEvent1', '活动 bossEvent2'])
        self.assertEqual(page['event_overflow'], 2)
        self.assertIn('另有 2 个活动 Boss 未显示', notes_for(page, '', 1, 1, ''))
        Image.open(io.BytesIO(render_boss_page(page))).verify()

    def test_event_rows_without_content_on_a_page_are_left_out(self):
        rows, _ = build_table(None, normalize_bosses(feed({'bossTagilla': 'tagilla'})))
        page = paginate_table({'mode': 'pve'}, rows)[0]
        self.assertEqual(len(page['rows']), len(BOSS_ROWS))
        self.assertNotIn('event_overflow', page)

    def test_event_appearing_and_ending_are_changes(self):
        base = normalize_bosses(feed({'bossTagilla': 'tagilla'}))
        withevent = normalize_bosses(feed({'bossTagilla': 'tagilla', 'bossScourgeEventLab': 'scourge'}))
        rows, changed = build_table(base, withevent)
        self.assertTrue(changed)
        self.assertEqual(change_lines(rows), ['活动 bossScourgeEventLab（Lab）：新增 100%'])
        rows, changed = build_table(withevent, base)
        self.assertEqual(change_lines(rows), ['活动 bossScourgeEventLab（Lab）：移除（原 100%）'])


class BackgroundTests(unittest.TestCase):
    def page(self):
        return paginate_table({'mode': 'pve'}, build_table(None, normalize_bosses(boss_data()))[0])[0]

    def setUp(self): boss_table_image.background_source.cache_clear()

    def tearDown(self): boss_table_image.background_source.cache_clear()

    def test_bundled_background_is_small_and_readable(self):
        self.assertTrue(boss_table_image.BACKGROUND.exists())
        self.assertLess(boss_table_image.BACKGROUND.stat().st_size, 250_000)
        self.assertIsNotNone(boss_table_image.background_source())

    def test_background_shows_outside_the_table_and_the_image_is_stable(self):
        data = render_boss_page(self.page())
        self.assertEqual(data, render_boss_page(self.page()))
        img = Image.open(io.BytesIO(data)).convert('L')
        # The strip left of the table (x < MARGIN) is texture, not one flat colour.
        strip = img.crop((0, 0, 12, img.height)).getdata()
        self.assertGreater(len(set(strip)), 8)

    def test_missing_background_falls_back_to_plain_black(self):
        with patch.object(boss_table_image, 'BACKGROUND', Path('/nonexistent/background.jpg')):
            boss_table_image.background_source.cache_clear()
            self.assertIsNone(boss_table_image.background_source())
            img = Image.open(io.BytesIO(render_boss_page(self.page()))).convert('RGB')
        self.assertEqual(img.getpixel((2, 2)), (12, 12, 12))

    def test_table_text_stays_legible_on_the_panels(self):
        img = Image.open(io.BytesIO(render_boss_page(self.page()))).convert('L')
        # Inside the table body the panel keeps the texture dark: all but the sparse text pixels are very dark.
        values = sorted(img.crop((305, 110, 540, 370)).getdata())
        self.assertLess(values[len(values)//2], 32)
        self.assertLess(values[int(len(values)*.95)], 40)


class FooterTests(unittest.TestCase):
    def test_footer_is_one_source_line_unless_something_needs_saying(self):
        page = {'mode': 'pve', 'checked_at': 100, 'fetched_at': 90, 'compared_at': 50}
        notes = notes_for(page, 'abc123def456', 1, 1, '')
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith('数据 json.tarkov.dev · 头像 assets.tarkov.dev · astrbot_tarkov_datasearch v'))
        self.assertIn('GPL-3.0-or-later', notes[0])
        for removed in ('第 1/1 页', '检查：', '对比：', '取得：', '空白为上游未列出'):
            self.assertFalse(any(removed in note for note in notes), removed)

    def test_warnings_and_continuation_stay(self):
        notes = notes_for({'stale': True, 'translation_warning': True}, 'abc123def456', 2, 3, 'boss 页 abc123def456 3')
        text = '\n'.join(notes)
        for kept in ('获取失败', '部分翻译未取得', '第 2/3 页', 'boss 页 abc123def456 3'):
            self.assertIn(kept, text)
        self.assertEqual(len(notes), 4)


class BossStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = BossStore(Path(self.temp.name)/'boss.sqlite3')
        self.targets = ['bot:GroupMessage:1', 'bot:GroupMessage:2']

    def tearDown(self): self.temp.cleanup()

    def event(self, count=80):
        self.store.observe(boss_card(count=count), self.targets, now=100)
        return self.store.observe(boss_card(0.4, count=count, checked=200), self.targets, now=200)

    def test_a_different_scope_retires_old_baselines_silently(self):
        self.store.observe(boss_card(), self.targets, now=100)
        self.store.observe(boss_card(0.4, checked=200), self.targets, now=200)
        self.assertIsNotNone(self.store.latest('pvp', now=200))
        other = BossStore(self.store.path); other.scope = BOSS_SCOPE+':scourge'
        self.assertIsNone(other.latest('pvp', now=200))
        # The first observation under the new scope is a silent baseline, not a change.
        self.assertIsNone(other.observe(boss_card(0.9, checked=300), self.targets, now=300))

    def test_only_change_events_carry_the_summary(self):
        self.store.observe(boss_card(), self.targets, now=100)
        self.store.observe(boss_card(0.4, checked=200), self.targets, now=200)
        pages = self.store.latest('pvp', now=200)
        self.assertEqual(pages[0]['change_lines'], ['Alpha（factory-name）：35%→40%'])
        # An ordinary full-table query has no previous snapshot, so nothing is announced as changed.
        rows, _ = build_table(None, normalize_bosses(boss_data()))
        self.assertNotIn('change_lines', paginate_table({'mode': 'pvp'}, rows)[0])

    def test_first_silent_restart_stable_latest_and_new_modes_are_independent(self):
        self.assertIsNone(self.store.observe(boss_card(), self.targets, now=100))
        self.assertIsNotNone(self.store.observe(boss_card(0.4, checked=200), self.targets, now=200))
        latest = self.store.latest('pvp', now=200)
        self.store = BossStore(self.store.path)
        self.assertIsNone(self.store.observe(boss_card(0.4, checked=300), self.targets, now=300))
        self.assertEqual(self.store.latest('pvp', now=300), latest)
        self.assertIsNone(self.store.observe(boss_card(0.9, mode='pve'), self.targets, now=300))
        self.assertIsNone(self.store.latest('pve', now=300))
        self.assertEqual(latest[0]['compared_at'], 100)

    def test_page_submission_retry_is_per_target_per_page_and_bounded(self):
        eid = self.event()
        due = self.store.due(self.targets, ['pvp'], now=200)
        first = [r for r in due if r[1] == self.targets[0]]
        self.assertEqual(len(first), min(PAGES_PER_CYCLE, len(first[0][3])))
        self.store.record_page(eid, self.targets[0], 0, 'submitted', now=200)
        self.store.record_page(eid, self.targets[0], 1, 'TimeoutError', now=200)
        waiting = self.store.due(self.targets, ['pvp'], now=201)
        self.assertFalse(any(row[1] == self.targets[0] for row in waiting))
        retry = [r for r in self.store.due(self.targets, ['pvp'], now=321) if r[1] == self.targets[0]]
        self.assertEqual(retry[0][2], 1)
        for stamp in (321, 600): self.store.record_page(eid, self.targets[0], 1, 'TimeoutError', now=stamp)
        self.assertTrue(all(r[2] > 1 for r in self.store.due(self.targets[:1], ['pvp'], now=900)))
        self.assertTrue(any(row[1] == self.targets[1] and row[2] == 0 for row in waiting))

    def test_pause_remove_mode_and_reset_retire_backlogs(self):
        eid = self.event()
        self.store.record_page(eid, self.targets[0], 0, 'blocked', now=200)
        self.assertFalse(any(r[1] == self.targets[0] for r in self.store.due(self.targets, ['pvp'], now=300)))
        self.store.reconcile(['pvp'], self.targets[:1])
        self.assertEqual(self.store.due(self.targets, ['pvp'], now=300), [])
        self.store.reconcile(['pve'], self.targets)
        self.assertIsNone(self.store.latest('pvp', now=300))
        self.store.retire()
        self.assertIsNone(self.store.observe(boss_card(1), self.targets, now=400))

    def test_query_tokens_freeze_pages_expire_and_have_capacity_limit(self):
        pages = [{'mode': 'pvp', 'rows': ['original']}]
        token = self.store.save_query(pages, now=100)
        pages[0]['rows'].append('mutated')
        self.assertEqual(self.store.query(token, now=101)[0]['rows'], ['original'])
        self.assertIsNone(self.store.query(token, now=101+QUERY_SECONDS))
        tokens = [self.store.save_query([], now=200+i) for i in range(17)]
        self.assertIsNone(self.store.query(tokens[0], now=220))
        self.assertEqual(self.store.query(tokens[-1], now=220), [])

    def test_event_failure_rolls_back_baseline(self):
        self.store.observe(boss_card(), self.targets, now=100)
        with sqlite3.connect(self.store.path) as db:
            db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.store.observe(boss_card(0.4), self.targets, now=200)
        with sqlite3.connect(self.store.path) as db: db.execute('DROP TRIGGER fail_event')
        self.store.observe(boss_card(0.4), self.targets, now=300)
        self.assertEqual(self.store.latest('pvp', now=300)[0]['compared_at'], 100)

    def test_old_scope_tables_and_baseline_are_retired_without_a_false_removal_alert(self):
        self.event()
        token = self.store.save_query([{'rows':['BEAR','USEC','old columns']}], now=200)
        with sqlite3.connect(self.store.path) as db:
            db.execute("UPDATE metadata SET value='all-mobs-old-layout' WHERE key='scope'")
        self.assertIsNone(self.store.latest('pvp',now=201))
        self.assertIsNone(self.store.query(token,now=201))
        self.assertIsNone(self.store.observe(boss_card(0.4), self.targets, now=300))

    def test_page_budget_can_submit_all_three_modes_without_waiting_another_poll(self):
        modes = ['pvp','pve','season']
        for mode in modes:
            self.store.observe(boss_card(mode=mode),self.targets,now=100)
            self.store.observe(boss_card(0.4,mode=mode),self.targets,now=200)
        due = self.store.due(self.targets,modes,now=200)
        self.assertEqual([pages[page]['mode'] for _,target,page,pages in due if target == self.targets[0]],modes)
        first = due[0]
        self.store.record_page(first[0],first[1],first[2],'TimeoutError',now=200)
        self.assertFalse(any(target == first[1] for _,target,_,_ in self.store.due(self.targets,modes,now=201)))


class BossNotificationsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = BossStore(Path(self.temp.name)/'boss.sqlite3')
        self.targets, self.modes = ['bot:GroupMessage:1', 'bot:GroupMessage:2'], ['pvp', 'pve']
        self.chance, self.failed = 0.35, False
        self.sent, self.prepared, self.errors = [], [], []
        async def fetch(mode):
            if mode == 'pve' and self.failed: raise TimeoutError()
            return boss_card(self.chance, mode=mode, count=40)
        async def prepare(page, index, total):
            self.prepared.append((page['mode'], index)); return (page['mode'], index)
        async def send(target, image): self.sent.append((target, image)); return True
        self.monitor = BossNotifications(self.store, fetch, prepare, send, lambda:self.modes,
                                         lambda:self.targets, lambda:True, self.errors.append)

    async def asyncTearDown(self):
        await self.store.close(); self.temp.cleanup()

    async def test_mode_failures_do_not_block_successful_changes_and_rendering_is_shared(self):
        await self.monitor.cycle()
        self.assertEqual(self.sent, [])
        self.chance, self.failed = 0.4, True
        with self.assertRaises(TimeoutError): await self.monitor.cycle()
        self.assertTrue(self.sent)
        self.assertEqual(len(self.sent), 2*len(self.prepared))
        self.assertTrue(all(image[0] == 'pvp' for _, image in self.sent))
        latest = self.store.latest('pvp')
        self.failed = False
        await self.monitor.cycle()
        self.assertEqual(self.store.latest('pvp'), latest)

    async def test_partial_failure_retries_only_unfinished_pages_after_restart(self):
        self.modes = ['pvp']
        await self.monitor.cycle()
        self.chance = 0.4
        attempted = []
        async def send(target, image):
            attempted.append((target, image[1]))
            if target == self.targets[0] and image[1] == 2: raise TimeoutError()
            return True
        self.monitor.send = send
        await self.monitor.cycle()
        self.assertIn((self.targets[0], 1), attempted)
        self.assertIn((self.targets[0], 2), attempted)
        await self.store.thread(lambda: None)
        with sqlite3.connect(self.store.path) as db: db.execute('UPDATE deliveries SET next_at=0')
        self.monitor.store = BossStore(self.store.path)
        attempted.clear()
        await self.monitor.cycle()
        self.assertNotIn((self.targets[0], 1), attempted)
        self.assertEqual(next(page for target, page in attempted if target == self.targets[0]), 2)
        await self.monitor.store.close()

    async def test_permission_pause_and_unknown_page_are_not_reported_as_complete_delivery(self):
        self.modes = ['pvp']
        await self.monitor.cycle()
        self.chance = 0.4
        async def send(target, image):
            if target == self.targets[0]: raise PermissionError()
            return None
        self.monitor.send = send
        await self.monitor.cycle()
        with sqlite3.connect(self.store.path) as db:
            states = {row[0] for row in db.execute('SELECT state FROM deliveries')}
        self.assertTrue({'blocked','unknown','pending'} <= states)
        self.assertNotIn('submitted',states)
        due = self.store.due(self.targets, self.modes)
        self.assertTrue(due)
        self.assertTrue(all(target == self.targets[1] and page > 0 for _,target,page,_ in due))
