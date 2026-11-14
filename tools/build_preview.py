"""Rebuild docs/preview.jpg, arena_preview.jpg and help_preview.jpg from invented profiles.

    python tools/build_preview.py

The status and Boss pictures come from tools/build_public_preview.py (live public data). Every value here comes from tests/fixtures.py plus the invented skills and achievements below: no real player's name, account
id or numbers may appear in the documentation.
"""
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'), str(ROOT)]
# transport.py imports AstrBot's logger; nothing here logs.
api = types.ModuleType('astrbot.api')
api.logger = types.SimpleNamespace(warning=print, info=print, debug=print)
sys.modules.setdefault('astrbot', types.ModuleType('astrbot'))
sys.modules.setdefault('astrbot.api', api)

from download_font import prepare  # noqa: E402
prepare()

from fixtures import arena_profile, profile  # noqa: E402
from tarkov_core import COMMAND_STYLES  # noqa: E402
from tarkov_core.arena import build_arena_card, summarize_arena  # noqa: E402
from tarkov_core.arena_image import render_arena_image  # noqa: E402
from tarkov_core.card import build_card  # noqa: E402
from tarkov_core.help_image import render_help_image  # noqa: E402
from tarkov_core.image import render_image  # noqa: E402
from tarkov_core.help_text import help_card  # noqa: E402
from tarkov_core.modes import page_url  # noqa: E402
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
    # The help of a default installation: the Atlas source, matching on, QQ single-word commands on.
    app = SimpleNamespace(style=lambda: COMMAND_STYLES['mention'], default_mode=lambda: 'pve',
                          player_source=lambda: 'atlas', config=SimpleNamespace(flag=lambda key: False))
    snapshot = SimpleNamespace(index_enabled=lambda: True, names=SimpleNamespace(refresh_seconds=168*3600))
    players = SimpleNamespace(
        refresh_rule=lambda: '跳过缓存重新查询；每人每小时 3 次，两次至少间隔 5 分钟',
        source_rule=lambda: ('数据来自 Atlas（https://eftforge.com/atlas），按需实时查询，不用先在 tarkov.dev 搜索；查过的玩家缓存 '
                             '10 分钟，每人每小时可联网查询 10 次、刷新 3 次。Atlas 总额度用完时暂停联网查询，到期自动恢复；'
                             '缓存仍可查看。Atlas 请求故障时可改用 tarkov.dev 快照，回复里会注明。'))
    card = help_card(app, {'matching', 'qq_words'}, snapshot, players)
    (ROOT/'docs'/'help_preview.jpg').write_bytes(render_help_image(card))
