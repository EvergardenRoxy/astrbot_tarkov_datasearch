"""Rebuild tarkov_core/data/static.json from tarkov.dev's public JSON (development only, not run by the plugin).

The plugin needs two small tables that a profile does not carry: the experience per level (a profile stores total
experience only) and the skill names. Both come from https://json.tarkov.dev/regular/items, the same file
TarkovMonitor reads, plus its Chinese translation file items_zh.
"""
from pathlib import Path
import datetime
import gzip
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT/'tarkov_core'/'data'/'static.json'
SOURCE = 'https://json.tarkov.dev/regular/items'
HEADERS = {'User-Agent': 'astrbot_tarkov_datasearch-build/1 (+https://github.com/EvergardenRoxy/astrbot_tarkov_datasearch)',
           'Accept-Encoding': 'gzip'}
# Translations that are wrong as a skill name (the game text is a progress label).
ZH_OVERRIDES = {'Lockpicking': '撬锁'}


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as response:
        raw = response.read()
        if response.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
    return json.loads(raw)


def main():
    data = fetch(SOURCE)['data']
    zh = fetch(SOURCE+'_zh')['data']
    levels = [[int(row['level']), int(row['exp'])] for row in data['playerLevels']]
    if [level for level, _ in levels] != list(range(1, len(levels)+1)):
        raise SystemExit('playerLevels is not 1..N in order')
    skills = {}
    for skill in data['skills']:
        name = skill['name']
        skills[skill['id']] = {'en': name, 'zh': ZH_OVERRIDES.get(skill['id'], zh.get(name, name))}
    out = {'source': SOURCE, 'fetched': datetime.date.today().isoformat(),
           # exp is the experience needed to go from the previous level to this one; level 1 is 0.
           'player_levels': levels, 'skills': skills}
    DEST.parent.mkdir(parents=True, exist_ok=True)
    # One line per level and per skill: small diffs when the upstream data changes.
    lines = ['{', f' "source": {json.dumps(out["source"])},', f' "fetched": {json.dumps(out["fetched"])},', ' "player_levels": [']
    lines += [f'  {json.dumps(row)}{"," if i < len(levels)-1 else ""}' for i, row in enumerate(levels)]
    lines += [' ],', ' "skills": {']
    lines += [f'  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}{"," if i < len(skills)-1 else ""}'
              for i, (key, value) in enumerate(skills.items())]
    lines += [' }', '}']
    DEST.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    assert json.loads(DEST.read_text(encoding='utf-8')) == out
    print(DEST, 'levels:', len(levels), 'skills:', len(skills))


if __name__ == '__main__':
    main()
