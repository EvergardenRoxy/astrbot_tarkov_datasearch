"""Turn a players.tarkov.dev profile into the numbers the plugin shows.

Field meanings follow tarkov.dev's own player page (src/pages/player/index.jsx): level from total experience via the
playerLevels table, raid counters from pmcStats/scavStats.eft.overAllCounters.Items, skill level = Progress / 100.
"""
import json
import math
from pathlib import Path

from .text import clean
from .transport import QueryError

STATIC = json.loads((Path(__file__).with_name('data')/'static.json').read_text(encoding='utf-8'))
ELITE_PROGRESS = 5100
SIDES = {'bear': 'BEAR', 'usec': 'USEC', 'savage': 'Scav'}


def number(value):
    """A finite JSON number, or None (booleans are not numbers here). JSON integers beyond float range also give
    None: math.isfinite() raises OverflowError on them."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def level_of(experience, levels=None):
    """Player level for a total experience. Each table row is (level, experience from the previous level)."""
    levels = STATIC['player_levels'] if levels is None else levels
    total, level = 0, 0
    for row_level, needed in levels:
        total += needed
        if total > experience:
            break
        level = row_level
    return level


def counter(items, *parts):
    """Value of the counter whose key holds every part; the shortest such key wins (["Kills"] over longer ones)."""
    best = None
    for item in items:
        key = item.get('Key') if isinstance(item, dict) else None
        if not isinstance(key, list) or not all(part in key for part in parts):
            continue
        if best is None or len(key) < len(best[0]):
            best = (key, number(item.get('Value')))
    # Every counter shown is a count: a negative value is malformed (and made the image's exit bars draw for hours,
    # review R-09), so it reads as 0.
    return max(0, best[1] or 0) if best else 0


def counters(block):
    eft = block.get('eft') if isinstance(block, dict) else None
    overall = eft.get('overAllCounters') if isinstance(eft, dict) else None
    items = overall.get('Items') if isinstance(overall, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def side_stats(block):
    items = counters(block)
    return {
        'raids': counter(items, 'Sessions'),
        'survived': counter(items, 'ExitStatus', 'Survived'),
        'killed': counter(items, 'ExitStatus', 'Killed'),
        # tarkov.dev's page reads "Left"; current profiles say "MissingInAction".
        'mia': counter(items, 'ExitStatus', 'MissingInAction') or counter(items, 'ExitStatus', 'Left'),
        'runner': counter(items, 'ExitStatus', 'Runner'),
        'transit': counter(items, 'ExitStatus', 'Transit'),
        'kills': counter(items, 'Kills'),
        'deaths': counter(items, 'Deaths'),
        'pmc_kills': counter(items, 'KilledPmc'),
        'streak': counter(items, 'LongestWinStreak'),
    }


def skills(raw):
    """Trained skills the static table knows, highest first: [(Chinese name, level, elite)]."""
    common = raw.get('Common') if isinstance(raw, dict) else None
    found = {}
    for skill in common if isinstance(common, list) else []:
        if not isinstance(skill, dict):
            continue
        skill_id = skill.get('Id')
        known = STATIC['skills'].get(skill_id) if isinstance(skill_id, str) else None
        progress, access = number(skill.get('Progress')), number(skill.get('LastAccess'))
        # The same filter as tarkov.dev: untouched skills have no progress or a negative access time.
        if not known or not progress or progress <= 0 or not access or access <= 0:
            continue
        # One entry per skill (a document repeating an id thousands of times made a 18 MB image, review R-10), so
        # the list is never longer than the static skill table.
        found[skill_id] = max(found.get(skill_id, (None, 0)), (known['zh'], progress), key=lambda item: item[1])
    ranked = sorted(found.values(), key=lambda item: -item[1])
    return [(name, int(min(progress, ELITE_PROGRESS)//100), progress >= ELITE_PROGRESS) for name, progress in ranked]


def last_active(raw):
    """Latest skill or achievement timestamp in seconds, as tarkov.dev's page computes "last active"."""
    latest = 0
    block = raw.get('skills')
    common = block.get('Common') if isinstance(block, dict) else None
    for skill in common if isinstance(common, list) else []:
        value = number(skill.get('LastAccess')) if isinstance(skill, dict) else None
        latest = max(latest, value or 0)
    achievements = raw.get('achievements')
    for value in achievements.values() if isinstance(achievements, dict) else []:
        latest = max(latest, number(value) or 0)
    return latest or None


def summarize(raw):
    """The display summary of one profile. Raises QueryError when the document is not a profile."""
    info = raw.get('info')
    if not isinstance(info, dict) or not isinstance(info.get('nickname'), str):
        raise QueryError('tarkov.dev 返回的档案格式无法识别')
    experience = number(info.get('experience'))
    pmc_block = raw.get('pmcStats')
    pmc_eft = pmc_block.get('eft') if isinstance(pmc_block, dict) else None
    play = number(pmc_eft.get('totalInGameTime')) if isinstance(pmc_eft, dict) else None
    achievements = raw.get('achievements')
    updated = number(raw.get('updated'))
    side = clean(info.get('side') or '', 16)
    return {
        'aid': number(raw.get('aid')),
        'nickname': clean(info['nickname'], 32) or '未知',
        'side': SIDES.get(side.lower(), side or '未知'),
        'experience': experience,
        'level': level_of(experience) if experience is not None and experience >= 0 else None,
        'prestige': number(info.get('prestigeLevel')),
        'play_seconds': play,
        'achievements': len(achievements) if isinstance(achievements, (dict, list)) else 0,
        'pmc': side_stats(pmc_block),
        'scav': side_stats(raw.get('scavStats')),
        'skills': skills(raw.get('skills')),
        'last_active': last_active(raw),
        # Milliseconds since the epoch when tarkov.dev last fetched this profile.
        'updated': updated/1000 if updated else None,
    }
