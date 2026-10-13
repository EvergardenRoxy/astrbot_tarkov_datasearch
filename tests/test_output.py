import unittest

from fixtures import profile
from tarkov_core.card import build_card
from tarkov_core.output import render, render_markdown
from tarkov_core.profile import summarize

NOW = 1791903208.537 + 3600


class OutputTests(unittest.TestCase):
    def test_full_profile(self):
        text = render(summarize(profile()), 'PVE', now=NOW)
        for part in ('【塔科夫 PVE 档案】Test_Player（账号 ID 1234567）', '阵营 USEC｜等级 66', '账号总游戏时长 1305 小时｜成就 2 个',
                     'PMC：战局 1472，撤离 1234，存活率 83.8%', '击杀 12115，死亡 164，K/D 73.87，击杀 PMC 1894',
                     '匆匆逃离 27，转移 55，KIA 155，行动中失踪 1，最长连续存活 60 局',
                     'Scav：战局 58，撤离 42，存活率 72.4%', '精英技能（2 项）：耐力、力量', '其他技能（前 2 项）：魅力 48、活力 29',
                     '档案更新于 2026-10-13 22:53（北京时间）'):
            with self.subTest(part=part):
                self.assertIn(part, text)
        self.assertNotIn('声望', text)
        self.assertNotIn('过时', text)
        self.assertNotIn('{', text)

    def test_no_deaths_and_no_raids(self):
        s = summarize(profile())
        s['pmc'].update(deaths=0)
        s['scav'] = dict.fromkeys(s['scav'], 0)
        text = render(s, 'PVP', now=NOW)
        self.assertIn('K/D —（无死亡记录）', text)
        self.assertIn('Scav：暂无记录', text)

    def test_skill_limit(self):
        s = summarize(profile())
        self.assertNotIn('技能', render(s, 'PVP', skill_limit=0, now=NOW))
        self.assertIn('其他技能（前 1 项）：魅力 48', render(s, 'PVP', skill_limit=1, now=NOW))

    def test_stale_profile_is_flagged(self):
        text = render(summarize(profile()), 'PVP', now=NOW+40*86400)
        self.assertIn('已有 40 天没有更新', text)

    def test_out_of_range_timestamps_are_left_out(self):
        for value in (1e20, -1e20):
            with self.subTest(value=value):
                s = summarize(profile(updated=value, achievements={'a': value}))
                text = render(s, 'PVE', now=NOW)
                self.assertIn('数据来自 tarkov.dev', text)
                self.assertNotIn('档案更新于', text)
                self.assertNotIn('天没有更新', text)

    def test_exit_statuses_use_the_game_wording(self):
        # Maintainer: 撤离 / 匆匆逃离 / 转移 / KIA / 行动中失踪, in this order.
        card = build_card(summarize(profile()), 'PVE', now=NOW)
        text, markdown = render(summarize(profile()), 'PVE', now=NOW), render_markdown(card)
        self.assertIn('撤离 1234', text)
        self.assertIn('**撤离状态（PMC）**：撤离 1234 · 匆匆逃离 27 · 转移 55 · KIA 155 · 行动中失踪 1', markdown)
        self.assertIn('| | 战局 | 撤离 | 存活率 |', markdown)
        for old in ('阵亡', 'Run Through', '转图'):
            self.assertNotIn(old, text+markdown)
        self.assertNotRegex(text+markdown, '(?<!行动中)失踪')

    def test_markdown(self):
        s = summarize(profile(info_nickname='A_b*c'))
        s['scav'] = dict.fromkeys(s['scav'], 0)
        text = render_markdown(build_card(s, 'PVE', notes=['注意：x_y'], now=NOW))
        self.assertTrue(text.startswith('## 塔科夫 PVE 档案：A\\_b\\*c'))
        self.assertIn('| PMC | 1472 | 1234 | 83.8% | 12115 | 164 | 73.87 | 1894 | 60 |', text)
        self.assertIn('| Scav | 暂无记录 |', text)
        self.assertNotIn('撤离状态（Scav）', text)
        self.assertIn('**精英技能（2 项）**：耐力、力量', text)
        self.assertIn('> 注意：x\\_y', text)
        self.assertNotIn('技能', render_markdown(build_card(s, 'PVE', skill_limit=0, now=NOW)))

    def test_text_is_unchanged_by_the_card_model(self):
        # The text reply existed before the card model; it must read the same through it.
        s = summarize(profile())
        self.assertEqual(render(s, 'PVE', notes=['n'], now=NOW), '\n'.join([
            '【塔科夫 PVE 档案】Test_Player（账号 ID 1234567）', '阵营 USEC｜等级 66', '账号总游戏时长 1305 小时｜成就 2 个',
            '最近活跃 2026-10-04 19:25', '', 'PMC：战局 1472，撤离 1234，存活率 83.8%',
            '　击杀 12115，死亡 164，K/D 73.87，击杀 PMC 1894', '　匆匆逃离 27，转移 55，KIA 155，行动中失踪 1，最长连续存活 60 局',
            'Scav：战局 58，撤离 42，存活率 72.4%', '　击杀 110，死亡 15，K/D 7.33，击杀 PMC 66', '　匆匆逃离 1，KIA 15，最长连续存活 8 局',
            '精英技能（2 项）：耐力、力量', '其他技能（前 2 项）：魅力 48、活力 29', '', '数据来自 tarkov.dev，档案更新于 2026-10-13 22:53（北京时间）', 'n']))

    def test_prestige_and_notes(self):
        text = render(summarize(profile(info_prestigeLevel=2)), 'PVP', notes=['注意：测试'], now=NOW)
        self.assertIn('声望 2', text)
        self.assertTrue(text.endswith('注意：测试'))
