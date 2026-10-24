"""Rebuild the bundled fonts in tarkov_core/fonts/ (development only; needs `pip install fonttools`).

- Noto Sans SC (variable, ~17 MB) is cut down to the characters the plugin can draw: printable ASCII plus every
  non-ASCII character in main.py, tarkov_core/*.py, skill names in static.json and the optional item-label glyph
  corpus. The list is written next to
  the font; image.py draws any other character as '?'. Re-run this script after adding Chinese text to the code.
- Oswald (variable) and Share Tech Mono are copied unmodified. Share Tech Mono's licence reserves the name "Share",
  so it must not be subset (a modified copy could not keep its name); Oswald is small enough as it is.
All three are SIL Open Font License 1.1, from https://github.com/google/fonts .

Usage: python tools/build_fonts.py [directory with already downloaded source files]
"""
from pathlib import Path
import hashlib
import json
import sys
import urllib.request

from fontTools import subset

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT/'tarkov_core'/'fonts'
BASE = 'https://raw.githubusercontent.com/google/fonts/main/ofl/'
SOURCES = {  # local file name: path in google/fonts
    'NotoSansSC[wght].ttf': 'notosanssc/NotoSansSC%5Bwght%5D.ttf',
    'Oswald[wght].ttf': 'oswald/Oswald%5Bwght%5D.ttf',
    'ShareTechMono-Regular.ttf': 'sharetechmono/ShareTechMono-Regular.ttf',
    'OFL-NotoSansSC.txt': 'notosanssc/OFL.txt',
    'OFL-Oswald.txt': 'oswald/OFL.txt',
    'OFL-ShareTechMono.txt': 'sharetechmono/OFL.txt',
}


def source(name, cache):
    path = cache/name if cache else None
    if path and path.exists():
        return path.read_bytes()
    with urllib.request.urlopen(BASE+SOURCES[name], timeout=300) as response:
        data = response.read()
    if path:
        path.write_bytes(data)
    return data


def characters():
    chars = {chr(code) for code in range(32, 127)}
    for path in [ROOT/'main.py', *sorted((ROOT/'tarkov_core').glob('*.py'))]:
        chars |= {char for char in path.read_text(encoding='utf-8') if ord(char) > 126 and char.isprintable()}
    static = json.loads((ROOT/'tarkov_core'/'data'/'static.json').read_text(encoding='utf-8'))
    for skill in static['skills'].values():
        chars |= set(skill['zh'])
    return ''.join(sorted(chars))


def main():
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    DEST.mkdir(parents=True, exist_ok=True)
    for name in SOURCES:
        data = source(name, cache)
        print(f'{name}: {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}')
    chars = characters()
    options = subset.Options()
    options.hinting = False
    options.name_IDs = ['*']
    options.notdef_outline = True
    full = DEST/'NotoSansSC-source.tmp.ttf'
    full.write_bytes(source('NotoSansSC[wght].ttf', cache))
    try:
        font = subset.load_font(str(full), options)
    finally:
        full.unlink()
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=chars)
    subsetter.subset(font)
    subset.save_font(font, str(DEST/'NotoSansSC-Subset.ttf'), options)
    covered = {chr(code) for code in font.getBestCmap()}
    missing = [char for char in chars if char not in covered and char != ' ']
    if missing:
        print('not in Noto Sans SC:', ''.join(missing))
    (DEST/'NotoSansSC-Subset.chars.txt').write_text(''.join(char for char in chars if char in covered), encoding='utf-8')
    (DEST/'Oswald-Variable.ttf').write_bytes(source('Oswald[wght].ttf', cache))
    (DEST/'ShareTechMono-Regular.ttf').write_bytes(source('ShareTechMono-Regular.ttf', cache))
    for name in ('OFL-NotoSansSC.txt', 'OFL-Oswald.txt', 'OFL-ShareTechMono.txt'):
        (DEST/name).write_bytes(source(name, cache))
    for path in sorted(DEST.iterdir()):
        print(path.name, path.stat().st_size)


if __name__ == '__main__':
    main()
