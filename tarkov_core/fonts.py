"""Shared typography for every picture. Chinese uses the verified complete Noto Sans SC download."""
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONTS = Path(__file__).with_name('fonts')
FONT_FILES = {'osw': 'Oswald-Variable.ttf', 'mono': 'ShareTechMono-Regular.ttf'}
_chinese_font = None


class FontUnavailable(RuntimeError):
    """No complete Chinese font has been prepared for rendering."""


def use_chinese_font(path):
    global _chinese_font
    _chinese_font = str(path) if path is not None else None


@lru_cache(maxsize=256)
def _load(path, size, weight, basic):
    engine = ImageFont.Layout.BASIC if basic else None
    loaded = ImageFont.truetype(path, size, layout_engine=engine)
    if weight is not None:
        loaded.set_variation_by_axes([weight])
    return loaded


def font(name, size, weight=None, *, basic=False):
    if name == 'sc':
        if _chinese_font is None:
            raise FontUnavailable('完整中文字体尚未准备好，请稍后再试')
        path = _chinese_font
    else:
        path = str(FONTS/FONT_FILES[name])
    return _load(path, size, weight, basic)


def basic_font(name, size, weight=None):
    return font(name, size, weight, basic=True)
