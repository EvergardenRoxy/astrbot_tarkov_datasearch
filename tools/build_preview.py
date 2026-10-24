"""Rebuild docs/preview.jpg, arena_preview.jpg, help_preview.jpg, boss_preview.jpg, boss_changes_preview.jpg and status_preview.jpg, and print the README text example, from invented profiles.

    python tools/build_preview.py

Every value comes from tests/fixtures.py plus the invented skills and achievements below: no real player's name, account
id or numbers may appear in the documentation.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'), str(ROOT)]
# transport.py imports AstrBot's logger; nothing here logs.
api = types.ModuleType('astrbot.api')
api.logger = types.SimpleNamespace(warning=print, info=print, debug=print)
sys.modules.setdefault('astrbot', types.ModuleType('astrbot'))
sys.modules.setdefault('astrbot.api', api)

from fixtures import arena_profile, profile  # noqa: E402
from tarkov_core import COMMAND_STYLES  # noqa: E402
from tarkov_core.arena import build_arena_card, summarize_arena  # noqa: E402
from tarkov_core.arena_image import render_arena_image  # noqa: E402
from tarkov_core.boss_monitor import build_table, normalize_bosses  # noqa: E402
from tarkov_core.boss_table_image import MAP_LABELS, change_lines, paginate_table, render_boss_page  # noqa: E402
from tarkov_core.card import build_card  # noqa: E402
from tarkov_core.help_image import render_help_image  # noqa: E402
from tarkov_core.image import render_image  # noqa: E402
from tarkov_core.modes import page_url, search_url  # noqa: E402
from tarkov_core.output import render_text  # noqa: E402
from tarkov_core.profile import summarize  # noqa: E402
from tarkov_core.server_status import normalize_status  # noqa: E402
from tarkov_core.status_image import render_status_image  # noqa: E402

SKILLS = {'Endurance': 5100, 'Strength': 5100, 'Metabolism': 5100, 'Attention': 5100, 'Charisma': 4000,
          'Perception': 3500, 'Search': 3000, 'Surgery': 2600, 'Vitality': 2500, 'Throwing': 2000, 'MagDrills': 1800,
          'Health': 1500, 'StressResistance': 1200, 'Assault': 1000}

NOW = 1791000000
# Invented Boss rates for the documentation pictures (not real game data).
BOSS_SAMPLE = {
    '55f2d3fd4bdc2d5f408b4567': {'bossBully': ['0.35']}, '59fc81d786f774390775787e': {'bossBully': ['0.35']},
    '56f40101d2720b2a4d8b45d6': {'bossBully': ['0.35']}, '5704e3c2d2720bac5b8b4567': {'bossKojaniy': ['0.4'], 'bossBully': ['0.3']},
    '5704e554d2720bac5b8b456e': {'bossKnight': ['0.6'], 'bossBully': ['0.25']}, '5714dbc024597771384a510d': {'bossBoar': ['0.4', '1']},
    '5704e5fad2720bc05b8b4567': {'bossGluhar': ['0.35'], 'bossBully': ['0.3']},
    '5704e4dad2720bb55b8b4567': {'bossZryachiy': ['1'], 'bossPartisan': ['0.2'], 'bossKnight': ['0.5']},
    '5714dc692459777137212e12': {'bossSanitar': ['0.25'], 'bossKilla': ['0.4'], 'bossTagilla': ['0.3']},
    '5b0fc42d86f7744a585f9105': {'bossSanitar': ['0.1'], 'bossKilla': ['0.5'], 'bossScourgeEventLab': ['1']},
    '65b8d6f5cdde2479cb2a3125': {'sectantPriest': ['0.02'], 'bossKolontay': ['0.1']}}
BOSS_NAMES = {'bossTagilla': 'Tagilla', 'bossBully': 'Reshala', 'bossKojaniy': 'Shturman', 'bossSanitar': 'Sanitar',
              'bossKilla': 'Killa', 'bossGluhar': 'Glukhar', 'bossZryachiy': 'Zryachiy', 'bossBoar': 'Kaban',
              'bossKolontay': 'Kollontay', 'bossPartisan': 'Partisan', 'sectantPriest': 'Priest', 'bossKnight': 'Knight',
              'bossScourgeEventLab': 'Scourge'}


def boss_state(sample):
    maps = {}
    for map_id, name in MAP_LABELS:
        records = [{'mob': mob, 'spawnChance': float(rate), 'spawnTime': i, 'spawnTimeRandom': False, 'spawnLocations': []}
                   for mob, rates in sample.get(map_id, {}).items() for i, rate in enumerate(rates)]
        maps[map_id] = {'name': name, 'bosses': records}
    mobs = {mob: {'name': name, 'normalizedName': 'scourge' if mob == 'bossScourgeEventLab' else mob.lower()}
            for mob, name in BOSS_NAMES.items()}
    return normalize_bosses({'maps': maps, 'mobs': mobs})


def boss_pictures():
    meta = {'mode': 'pve', 'checked_at': NOW, 'fetched_at': NOW-300, 'stale': False}
    before = boss_state(BOSS_SAMPLE)
    rows, _ = build_table(None, before)
    query = render_boss_page(paginate_table(meta, rows)[0])
    changed = {map_id: {mob: list(rates) for mob, rates in mobs.items()} for map_id, mobs in BOSS_SAMPLE.items()}
    changed['5704e554d2720bac5b8b456e']['bossKnight'] = ['0.8']
    changed['5714dbc024597771384a510d']['bossBoar'] = ['0.4']
    changed['5704e4dad2720bb55b8b4567']['bossZryachiy'] = ['0.5']
    changed['5b0fc42d86f7744a585f9105']['bossTagilla'] = ['1']
    rows, _ = build_table(before, boss_state(changed))
    page = paginate_table({**meta, 'compared_at': NOW-600}, rows)[0]
    page['change_lines'] = change_lines(rows, {column['map_id'] for column in page['columns']})
    return query, render_boss_page(page)


def status_picture():
    data = {'generalStatus': {'name': 'Global', 'statusCode': 'OK'},
            'currentStatuses': [{'_id': 'auth', 'name': 'Authentication', 'statusCode': 'OK'},
                                {'_id': 'match', 'name': 'Matchmaking', 'statusCode': 'OK'},
                                {'_id': 'trading', 'name': 'Trading', 'statusCode': 'OK'}],
            'messages': []}
    return render_status_image({'state': normalize_status(data), 'checked_at': NOW, 'fetched_at': NOW-60,
                                'from_cache': False, 'stale': False, 'last_modified': None})


def demo():
    raw = profile()
    raw['skills']['Common'] = [{'Id': key, 'Progress': value, 'LastAccess': 1790000000} for key, value in SKILLS.items()]
    raw['achievements'] = {f'a{i}': 1760000000+i for i in range(30)}
    summary = summarize(raw)
    return build_card(summary, 'PVE', now=summary['updated']+3600, refresh_url=page_url('pve', summary['aid']))


if __name__ == '__main__':
    card = demo()
    (ROOT/'docs'/'preview.jpg').write_bytes(render_image(card))
    arena = summarize_arena(arena_profile())
    arena_card = build_arena_card(arena, now=arena['updated']+3600, refresh_url=page_url('arena', arena['aid']))
    (ROOT/'docs'/'arena_preview.jpg').write_bytes(render_arena_image(arena_card))
    style = COMMAND_STYLES['mention']
    help_card = {'command': style['command'], 'note': style['note'], 'mode': 'pve', 'label': 'PVE', 'search': search_url('pve'), 'example': page_url('pve', 1234567),
                 'arena_by_default': False, 'index': None}
    (ROOT/'docs'/'help_preview.jpg').write_bytes(render_help_image(help_card))
    boss_query, boss_changes = boss_pictures()
    (ROOT/'docs'/'boss_preview.jpg').write_bytes(boss_query)
    (ROOT/'docs'/'boss_changes_preview.jpg').write_bytes(boss_changes)
    (ROOT/'docs'/'status_preview.jpg').write_bytes(status_picture())
    print(render_text(card))
