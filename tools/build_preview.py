"""Rebuild docs/preview.jpg, arena_preview.jpg and help_preview.jpg, and print the README text example, from invented profiles.

    python tools/build_preview.py

The status and Boss pictures come from tools/build_public_preview.py (live public data). Every value here comes from tests/fixtures.py plus the invented skills and achievements below: no real player's name, account
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
from tarkov_core.card import build_card  # noqa: E402
from tarkov_core.help_image import render_help_image  # noqa: E402
from tarkov_core.image import render_image  # noqa: E402
from tarkov_core.modes import page_url, search_url  # noqa: E402
from tarkov_core.output import render_text  # noqa: E402
from tarkov_core.profile import summarize  # noqa: E402

SKILLS = {'Endurance': 5100, 'Strength': 5100, 'Metabolism': 5100, 'Attention': 5100, 'Charisma': 4000,
          'Perception': 3500, 'Search': 3000, 'Surgery': 2600, 'Vitality': 2500, 'Throwing': 2000, 'MagDrills': 1800,
          'Health': 1500, 'StressResistance': 1200, 'Assault': 1000}


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
    print(render_text(card))
