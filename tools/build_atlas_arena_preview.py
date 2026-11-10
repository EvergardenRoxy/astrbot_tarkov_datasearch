"""Build a layout reference from invented Arena data, without querying any player.

    python tools/build_atlas_arena_preview.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools')]
# Reuse the preview tool's minimal AstrBot logger stub and test fixture imports.
import build_preview  # noqa: E402, F401  (imported for its effect)
from fixtures import atlas_profile  # noqa: E402
from tarkov_core.atlas_card import build  # noqa: E402
from tarkov_core.atlas_image import render_atlas_image  # noqa: E402


def demo():
    doc = atlas_profile('arena', nickname='Arena_Example', looked_up='2026-11-08T09:00:00+00:00')
    modes = doc['arenaStats']['modes']
    modes.clear()
    for prefix, scale in (('Unranked', 4), ('', 1)):
        modes[prefix+'Overall'] = dict(GamesCount=100*scale, Kills=800*scale, Deaths=400*scale,
                                      BestArp=2400 if prefix else 3200, MaxKillsWithoutDeaths=14,
                                      LongestWinStreak=8)
        for key, games, wins, kills, deaths, mvp in (
            ('TeamFight', 40, 24, 320, 160, 5), ('LastHero', 15, 3, 140, 70, 2),
            ('CheckPoint', 20, 12, 150, 80, 3), ('BlastGang', 15, 9, 100, 50, 2),
            ('ShootOutDuo', 10, 6, 90, 40, 1)):
            modes[prefix+key] = dict(GamesCount=games*scale, ArenaWins=wins*scale,
                                    ArenaLoses=(games-wins)*scale, Kills=kills*scale,
                                    Deaths=deaths*scale, MatchMvpCount=mvp*scale)
    doc['achievements']['count'] = 24
    doc['battlePasses'][-1].update(title='Season 3', level=38)
    return doc


if __name__ == '__main__':
    out = ROOT/'docs'/'atlas_arena_reference.jpg'
    card = build(demo(), 'arena')
    card['reference_label'] = '虚构数据预览'
    out.write_bytes(render_atlas_image(card))
    print(out)
