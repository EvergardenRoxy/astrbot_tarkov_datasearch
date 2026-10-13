import io
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from fixtures import profile
from tarkov_core import image
from tarkov_core.card import build_card
from tarkov_core.profile import summarize

ROOT = Path(__file__).resolve().parents[1]
NOW = 1791000000 + 3600


def draw(summary, **kwargs):
    data = image.render_image(build_card(summary, kwargs.pop('mode', 'PVE'), now=NOW, **kwargs))
    picture = Image.open(io.BytesIO(data))
    picture.load()
    return data, picture


class ImageTests(unittest.TestCase):
    def test_renders_a_jpeg(self):
        data, picture = draw(summarize(profile()))
        self.assertEqual(data[:2], b'\xff\xd8')
        self.assertEqual((picture.format, picture.width), ('JPEG', image.W))
        self.assertTrue(900 < picture.height < 2000)
        self.assertLess(len(data), 1024*1024)

    def test_same_card_same_picture(self):
        self.assertEqual(draw(summarize(profile()))[0], draw(summarize(profile()))[0])

    def test_same_picture_in_another_process(self):
        # The paper grain is seeded; a fresh process (simulated by clearing the cache) draws the same bytes.
        first = draw(summarize(profile()))[0]
        image.grain.cache_clear()
        self.assertEqual(draw(summarize(profile()))[0], first)

    def test_long_text_is_shrunk_to_fit(self):
        d = ImageDraw.Draw(Image.new('RGB', (10, 10)))
        f = image.fit(d, 'W'*32, 'osw', 36, 250, 700)
        self.assertLessEqual(d.textlength('W'*32, font=f), 250)
        self.assertEqual(image.fit(d, 'Ab', 'osw', 36, 250, 700).size, 36)

    def test_empty_sides_say_so(self):
        s = summarize(profile())
        s['scav'] = dict.fromkeys(s['scav'], 0)
        drawn = []
        real = ImageDraw.ImageDraw.text
        def record(self, xy, text, *args, **kwargs):
            drawn.append(text)
            return real(self, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', record):
            draw(s)
        # once in the combat table, once on the exit-status bar
        self.assertEqual(drawn.count('暂无记录'), 2)

    def test_exit_legend_uses_the_game_wording(self):
        drawn = []
        real = ImageDraw.ImageDraw.text
        def record(self, xy, text, *args, **kwargs):
            drawn.append(text)
            return real(self, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', record):
            draw(summarize(profile()))
        legend = [t for t in drawn if ' ' in t and t.split(' ')[0] in ('撤离', '匆匆逃离', '转移', 'KIA', '行动中失踪')]
        self.assertEqual(legend, ['撤离 800/30', '匆匆逃离 30/5', '转移 40/0', 'KIA 120/15', '行动中失踪 10/0'])
        self.assertIn('撤离', drawn)  # the combat-table header

    def test_height_follows_skills_and_notes(self):
        s = summarize(profile())
        none = draw(s, skill_limit=0)[1].height
        some = draw(s, skill_limit=10)[1].height
        noted = draw(s, skill_limit=10, notes=['注意：'+'很长的提示'*40])[1].height
        self.assertLess(none, some)
        self.assertLess(some, noted)

    def test_odd_cards_still_render(self):
        # Every mode label, missing values, empty sides, long and foreign names, huge numbers, prestige.
        odd = summarize({'info': {'nickname': 'X'}})
        long_name = summarize(profile(info_nickname='W'*32, info_prestigeLevel=3))
        foreign = summarize(profile(info_nickname='名字üЖ', info_side='Савage'))
        huge = summarize(profile())
        huge['pmc'] = dict.fromkeys(huge['pmc'], 10**15)
        for summary, mode in ((odd, 'PVP'), (long_name, 'PVP 赛季'), (foreign, 'PVE'), (huge, 'PVE')):
            with self.subTest(nickname=summary['nickname'][:8], mode=mode):
                self.assertEqual(draw(summary, mode=mode, skill_limit=49)[1].width, image.W)

    def test_characters_the_fonts_lack_become_question_marks(self):
        self.assertEqual(image.safe('Ab_1 耐力'), 'Ab_1 耐力')
        self.assertEqual(image.safe('名字ü'), '名字?')
        self.assertEqual(image.safe('耐力', latin=True), '??')

    def test_font_covers_every_character_of_the_code(self):
        # Fails when Chinese text is added to the code without re-running tools/build_fonts.py.
        chars = image.charset()
        wanted = set()
        for path in [ROOT/'main.py', *(ROOT/'tarkov_core').glob('*.py')]:
            wanted |= {char for char in path.read_text(encoding='utf-8') if ord(char) > 126 and char.isprintable()}
        static = json.loads((ROOT/'tarkov_core'/'data'/'static.json').read_text(encoding='utf-8'))
        for skill in static['skills'].values():
            wanted |= set(skill['zh'])
        self.assertFalse(sorted(wanted-chars))

    def test_listed_characters_have_glyphs(self):
        # A character without a glyph draws the same box as an unassigned private-use code point.
        f = image.font('sc', 20, 400)
        missing = bytes(f.getmask(''))
        listed = (image.FONTS/image.CHARSET_FILE).read_text(encoding='utf-8')
        self.assertGreater(len(listed), 300)
        blank = [char for char in listed if not char.isspace() and bytes(f.getmask(char)) == missing]
        self.assertEqual(blank, [])

    def test_fonts_ship_with_their_licences(self):
        for name in ('OFL-NotoSansSC.txt', 'OFL-Oswald.txt', 'OFL-ShareTechMono.txt'):
            self.assertIn('SIL Open Font License', (image.FONTS/name).read_text(encoding='utf-8'))
        # Share Tech Mono reserves its name, so the bundled file must be the unmodified original.
        self.assertTrue(re.search(rb'Share Tech Mono', (image.FONTS/'ShareTechMono-Regular.ttf').read_bytes().replace(b'\x00', b'')))
