from font_support import TEST_FONT  # noqa: F401  (imported for its effect: the complete font fixture; no network access)

import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from types import SimpleNamespace

from tarkov_core import COMMAND_STYLES, help_image, image
from tarkov_core.help_text import help_card, help_text


def card(mode='pve', label='PVE', arena=False, index=None, style='mention', source='tarkov-dev', features=()):
    """The real help card (help_text.help_card) of a plugin with these settings."""
    app = SimpleNamespace(style=lambda: COMMAND_STYLES[style], default_mode=lambda: mode, player_source=lambda: source,
                          config=SimpleNamespace(flag=lambda key: arena))
    snapshot = SimpleNamespace(index_enabled=lambda: index is not None,
                               names=SimpleNamespace(refresh_seconds={None: 0, '每 3 天': 3*86400}[index]))
    players = SimpleNamespace(refresh_rule=lambda: '刷新规则', source_rule=lambda: '数据来自 Atlas 的规则')
    return help_card(app, set(features), snapshot, players)


def drawn_texts(help_card):
    texts = []
    real = ImageDraw.ImageDraw.text
    def record(self, xy, text, *args, **kwargs):
        texts.append(text)
        return real(self, xy, text, *args, **kwargs)
    with patch.object(ImageDraw.ImageDraw, 'text', record):
        data = help_image.render_help_image(help_card)
    return data, texts


class HelpImageTests(unittest.TestCase):
    def test_renders_a_jpeg(self):
        data, _ = drawn_texts(card())
        picture = Image.open(io.BytesIO(data))
        self.assertEqual((picture.format, picture.width), ('JPEG', image.W))
        self.assertTrue(1200 < picture.height < 2400)
        self.assertEqual(data, help_image.render_help_image(card()))

    def test_shows_every_command_and_the_settings(self):
        _, texts = drawn_texts(card())
        for command in ('@机器人 tarkov [模式]', '@机器人 tarkov [模式] <昵称、账号ID 或网址>', '@机器人 tarkov 竞技场 <昵称、账号ID 或网址>',
                        '@机器人 tarkov 绑定 <玩家页网址或账号ID>', '@机器人 tarkov 解绑', '@机器人 tarkov 帮助',
                        'tarkov.dev/players?gameMode=pve', '@机器人 tarkov https://tarkov.dev/players/pve/1234567'):
            self.assertIn(command, texts)
        joined = '\n'.join(texts)
        self.assertIn('DEFAULT: PVE', joined)
        self.assertIn('竞技场要写明', joined)
        self.assertNotIn('也会看竞技场', joined)
        self.assertNotIn('昵称名单', joined)
        self.assertIn('都不向机器人开放档案查询', joined)
        self.assertEqual(texts[-1], '本插件在 GitHub 开源，遵循 GPL-3.0-or-later 许可；与 Battlestate Games 无直接关系')
        self.assertIn('数据来自 tarkov.dev 公开快照（非实时）', texts)

    def test_follows_the_default_mode_and_options(self):
        _, texts = drawn_texts(card('pvp', 'PVP', arena=True, index='每 3 天'))
        joined = ''.join(texts)
        self.assertIn('DEFAULT: PVP', joined)
        self.assertIn('@机器人 tarkov https://tarkov.dev/players/regular/1234567', texts)
        self.assertIn('也会看竞技场', joined)
        self.assertNotIn('竞技场要写明', joined)
        self.assertIn('昵称名单（每 3 天更新一次）', joined)

    def test_default_mode_is_marked(self):
        for mode, label in (('pvp', 'PVP'), ('pve', 'PVE'), ('season', 'PVP 赛季')):
            with self.subTest(mode=mode):
                _, texts = drawn_texts(card(mode, label))
                self.assertEqual(texts.count('默认'), 1)
                # The mark is drawn right after the default mode's chip (its label and words).
                chip = next(i for i, (key, *_) in enumerate(help_image.MODE_CHIPS) if key == mode)
                at = texts.index('默认')
                self.assertEqual(texts[at-2], help_image.MODE_CHIPS[chip][1])

    def test_the_atlas_source_and_the_features_switched_on(self):
        atlas = card(source='atlas', index='每 3 天', features=('status', 'boss', 'matching', 'weekly', 'qq_words'))
        _, texts = drawn_texts(atlas)
        for command in ('@机器人 tarkov 绑定 <昵称、账号ID 或网址>', '@机器人 tarkov [模式] <昵称、账号ID 或网址> 刷新',
                        '@机器人 tarkov status', '@机器人 tarkov boss [模式] [变化]', '@机器人 tarkov 匹配',
                        '@机器人 tarkov 周报 [模式] [昵称或排行]', '@机器人 tarkov 周报 添加|移除|名单 [模式] [昵称或账号ID]'):
            self.assertIn(command, texts)
        joined = '
'.join(texts)
        self.assertIn('每次只查一个模式', joined)
        self.assertIn('tmatch · tweekly', joined)
        self.assertNotIn('先在 tarkov.dev 选好模式', joined)
        self.assertIn('数据来自 Atlas（实时查询）；故障时改用 tarkov.dev 快照', texts)
        # Features switched off are not offered.
        self.assertNotIn('status', '
'.join(drawn_texts(card(source='atlas'))[1]))
        # The text sent when the picture cannot be drawn says the same.
        text = help_text(atlas)
        for row in atlas['rows']:
            self.assertIn(row[1], text)
        self.assertIn(atlas['rule'], text)

    def test_command_style(self):
        # Maintainer: the help shows "@机器人 tarkov" (QQ official bots) or "/tarkov" (platforms woken by "/").
        _, texts = drawn_texts(card(style='slash'))
        for command in ('/tarkov [模式]', '/tarkov 竞技场 <昵称、账号ID 或网址>', '/tarkov 帮助',
                        '/tarkov https://tarkov.dev/players/pve/1234567', COMMAND_STYLES['slash']['note']):
            self.assertIn(command, texts)
        self.assertFalse([text for text in texts if '@机器人' in text])
        _, texts = drawn_texts(card(style='mention'))
        self.assertIn(COMMAND_STYLES['mention']['note'], texts)
        self.assertFalse([text for text in texts if text.startswith('/tarkov')])
