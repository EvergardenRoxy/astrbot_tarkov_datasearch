"""Build the complete Atlas PVP/PVE/season preview from invented data; no player queries.

    python tools/build_atlas_player_preview.py [pvp|pve|season]
"""
import sys

import build_preview  # Existing AstrBot stub, cached complete font, and test fixture import path.
from fixtures import atlas_profile
from tarkov_core.atlas_card import build
from tarkov_core.atlas_image import render_atlas_image

ROOT = build_preview.ROOT


def demo(mode='pve'):
    def block(raids, survived, transit, killed, runner, kills, players, streak):
        return {'time': 60000, 'counters': [[['Sessions'], raids], [['ExitStatus', 'Survived'], survived],
                [['ExitStatus', 'Transit'], transit], [['ExitStatus', 'Killed'], killed],
                [['ExitStatus', 'Runner'], runner], [['Kills'], kills], [['KilledPmc'], players],
                [['LongestWinStreak'], streak]]}
    stats = {'pmc': block(37, 33, 2, 2, 0, 303, 96, 30), 'scav': block(8, 5, 1, 1, 1, 24, 3, 4)}
    names = ['赛季挑战示例', '团队协作示例', '胜利之路示例', '生存考验示例', '探索挑战示例',
             '精准射击示例', '撤离挑战示例', '战术行动示例', '远行者示例', '整装待发示例']
    done = [{'id': f'example-{i}', 'rarity': ('Seasonal', 'Legendary', 'Rare', 'Common')[min(3, i//3)],
             'percent': round(.5+i*1.7, 1), 'at': 1794000000-i*86400,
             'ch': {'name': name, 'description': '虚构成就，仅用于确认完整档案的排版效果。'}}
            for i, name in enumerate(names)]
    levels = [('Endurance', 51), ('Strength', 38), ('Metabolism', 31), ('Search', 26), ('Perception', 22),
              ('HideoutManagement', 20), ('Charisma', 18), ('Attention', 15), ('Assault', 12), ('MagDrills', 9)]
    skills = {'common': [{'id': key, 'progress': level*100, 'lastAccess': 1794000000} for key, level in levels]}
    doc = atlas_profile(mode, nickname='Example_Player', slots=[], stats=stats, skills=skills,
                        looked_up='2026-11-08T09:00:00+00:00', achievements={'count': len(done), 'total': 106, 'done': done})
    return build(doc, mode)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'pve'
    if mode not in ('pvp', 'pve', 'season'):
        raise SystemExit('Expected pvp, pve or season')
    out = ROOT/'docs'/f'atlas_{mode}_preview.jpg'
    out.write_bytes(render_atlas_image(demo(mode)))
    print(out)
