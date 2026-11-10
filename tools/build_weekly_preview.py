"""Build the weekly report examples from invented data; no player queries.

    python tools/build_weekly_preview.py

Writes docs/weekly_report_preview.jpg (one player's last seven days) and docs/weekly_ranking_preview.jpg (a mode's
ranking). Needs the cached complete font (python tools/download_font.py).
"""
import datetime

import build_preview  # noqa: F401  AstrBot stub, cached complete font and the test fixture import path.
from fixtures import atlas_profile
from tarkov_core.weekly_image import render_weekly_image
from tarkov_core.weekly_report import build_card, build_ranking, build_report, extract_sample

ROOT = build_preview.ROOT
# Monday 2026-11-09 03:00 UTC+8: the newest nightly sample.
LAST = 1794164400
DAY = 86400
SKILLS = ('Endurance', 'Strength', 'Search', 'Metabolism')
ACHIEVEMENTS = (('示例成就·远行者', 'Rare'), ('示例成就·整装待发', 'Common'), ('示例成就·赛季挑战', 'Seasonal'))


def player(name, level, nights, pace):
    """Eight nightly samples of an invented player; `nights` are the raids of each of the seven days."""
    samples = []
    raids, survived, killed, kills, players, scav, experience, play = 410, 300, 70, 3900, 610, 60, 9000000, 2000000
    skills = [3120, 2480, 1810, 900]
    done = [{'id': f'e{i}', 'at': LAST-DAY*(5-2*i)-3600*7, 'rarity': rarity, 'percent': 3, 'ch': {'name': title}}
            for i, (title, rarity) in enumerate(ACHIEVEMENTS)]
    for night, played in enumerate([0, *nights]):
        lost = played//5
        raids, survived, killed = raids+played, survived+played-lost, killed+lost
        kills, players, scav = kills+played*pace, players+played*pace//6, scav+(1 if played > 8 else 0)
        experience, play = experience+played*15000, play+played*2400
        skills = [value+played*step for value, step in zip(skills, (5, 3, 2, 1))]
        at = LAST-(7-night)*DAY
        block = lambda sessions, alive, dead, shot, pmc, streak: {'time': play, 'counters': [
            [['Sessions'], sessions], [['ExitStatus', 'Survived'], alive], [['ExitStatus', 'Killed'], dead],
            [['ExitStatus', 'Transit'], 12], [['Kills'], shot], [['KilledPmc'], pmc], [['LongestWinStreak'], streak]]}
        document = atlas_profile(
            'pve', nickname=name, level=level+(1 if night > 3 else 0), experience=experience,
            stats={'pmc': block(raids, survived, killed, kills, players, 14 if night < 5 else 17),
                   'scav': block(scav, scav-18, 15, 240+scav, 22, 6)},
            skills={'common': [{'id': key, 'progress': value, 'lastAccess': at} for key, value in zip(SKILLS, skills)]},
            achievements={'count': 30, 'total': 106, 'done': done},
            looked_up=datetime.datetime.fromtimestamp(at, datetime.timezone.utc).isoformat())
        samples.append(extract_sample(document, 'pve'))
    return build_report(samples, 'pve', LAST+7*3600)


if __name__ == '__main__':
    roster = [('Example_Player', 41, (6, 9, 0, 12, 4, 14, 10), 8), ('Example_Scout', 33, (3, 0, 5, 2, 0, 8, 6), 5),
              ('Example_Medic', 52, (0, 4, 4, 0, 3, 5, 2), 11), ('Example_Rookie', 12, (1, 0, 0, 2, 0, 1, 0), 3)]
    reports = [(name, player(name, level, nights, pace)) for name, level, nights, pace in roster]
    for name, card in (('weekly_report_preview.jpg', build_card(reports[0][1])),
                       ('weekly_ranking_preview.jpg', build_ranking(reports+[('Example_New', None)], 'pve'))):
        out = ROOT/'docs'/name
        out.write_bytes(render_weekly_image(card))
        print(out)
