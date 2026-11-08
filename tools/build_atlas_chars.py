"""Collect the characters of tarkov.dev's Chinese achievement names and descriptions (development only).

The Atlas player picture prints achievement names/descriptions in Chinese (item names use the English short name,
as on the Atlas page, so they need no extra glyphs), text that is not in the
plugin's code; the bundled font subset only has the characters tools/build_fonts.py collects. This writes them to
tarkov_core/data/atlas-text-chars.txt (public game data, no player data), which build_fonts.py adds. Re-run after
game updates, then run tools/build_fonts.py.

Usage: python tools/build_atlas_chars.py
"""
import gzip
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'tarkov_core'/'data'/'atlas-text-chars.txt'
AGENT = 'astrbot_tarkov_datasearch-dev (+https://github.com/EvergardenRoxy/astrbot_tarkov_datasearch)'


def get(path):
    request = urllib.request.Request('https://json.tarkov.dev'+path, headers={'User-Agent': AGENT, 'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(request, timeout=120) as response:
        body = response.read()
        return json.loads(gzip.decompress(body) if response.headers.get('Content-Encoding') == 'gzip' else body)


def main():
    chars = set()
    for mode in ('regular', 'pve'):
        tasks, words = get(f'/{mode}/tasks')['data'], get(f'/{mode}/tasks_zh')['data']
        found = tasks['achievements']
        for entry in found.values() if isinstance(found, dict) else found:
            for key in ('name', 'description'):
                chars |= set(str(words.get(entry.get(key), '')))
    text = ''.join(sorted(char for char in chars if ord(char) > 126 and char.isprintable()))
    OUT.write_text(text, encoding='utf-8')
    print(OUT, len(text))


if __name__ == '__main__':
    main()
