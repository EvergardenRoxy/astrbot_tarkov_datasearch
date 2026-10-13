import unittest

from fixtures import profile
from tarkov_core.output import render
from tarkov_core.profile import summarize

NOW = 1791000000 + 3600


class OutputTests(unittest.TestCase):
    def test_full_profile(self):
        text = render(summarize(profile()), 'PVE', now=NOW)
        for part in ('【塔科夫 PVE 档案】Test_Player（账号 ID 1234567）', '阵营 USEC｜等级 53', '游戏时长 1000 小时｜成就 2 个',
                     'PMC：战局 1000，存活 800，存活率 80.0%', '击杀 5000，死亡 100，K/D 50.00，击杀 PMC 900',
                     '阵亡 120，失踪 10，Run Through 30，转图 40，最长连续存活 40 局',
                     'Scav：战局 50，存活 30，存活率 60.0%', '精英技能（2 项）：耐力、力量', '其他技能（前 2 项）：魅力 40、活力 25',
                     '档案更新于 2026-10-03 12:00（北京时间）'):
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
        self.assertIn('其他技能（前 1 项）：魅力 40', render(s, 'PVP', skill_limit=1, now=NOW))

    def test_stale_profile_is_flagged(self):
        text = render(summarize(profile()), 'PVP', now=NOW+40*86400)
        self.assertIn('已有 40 天没有更新', text)

    def test_prestige_and_notes(self):
        text = render(summarize(profile(info_prestigeLevel=2)), 'PVP', notes=['注意：测试'], now=NOW)
        self.assertIn('声望 2', text)
        self.assertTrue(text.endswith('注意：测试'))
