import unittest

from fixtures import profile
from tarkov_core.output import render
from tarkov_core.profile import summarize

NOW = 1791903208.537 + 3600


class OutputTests(unittest.TestCase):
    def test_full_profile(self):
        text = render(summarize(profile()), 'PVE', now=NOW)
        for part in ('【塔科夫 PVE 档案】Test_Player（账号 ID 1234567）', '阵营 USEC｜等级 66', '游戏时长 1305 小时｜成就 2 个',
                     'PMC：战局 1472，存活 1234，存活率 83.8%', '击杀 12115，死亡 164，K/D 73.87，击杀 PMC 1894',
                     '阵亡 155，失踪 1，Run Through 27，转图 55，最长连续存活 60 局',
                     'Scav：战局 58，存活 42，存活率 72.4%', '精英技能（2 项）：耐力、力量', '其他技能（前 2 项）：魅力 48、活力 29',
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

    def test_prestige_and_notes(self):
        text = render(summarize(profile(info_prestigeLevel=2)), 'PVP', notes=['注意：测试'], now=NOW)
        self.assertIn('声望 2', text)
        self.assertTrue(text.endswith('注意：测试'))
