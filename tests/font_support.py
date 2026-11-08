"""Verified complete font fixture, cached before the suite. Tests never download fonts or player data."""
import os
from pathlib import Path

from tarkov_core.font_download import FILE, verify
from tarkov_core.fonts import use_chinese_font

ROOT = Path(__file__).resolve().parents[1]
TEST_FONT = Path(os.environ.get('TARKOV_TEST_FONT', ROOT/'.cache'/'fonts'/FILE)).resolve()
if not TEST_FONT.is_file() or not verify(TEST_FONT.read_bytes()):
    raise RuntimeError('Run python tools/download_font.py before image tests, or set TARKOV_TEST_FONT to the pinned font')
use_chinese_font(TEST_FONT)
