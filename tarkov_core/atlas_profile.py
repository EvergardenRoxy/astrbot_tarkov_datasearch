"""Turn Atlas player documents into the summaries the existing cards show.

Shapes from the 2026-11-08 probes (docs/atlas-probe-record.md §7): raid counters are `[[key parts], value]` pairs with
the same keys as tarkov.dev's snapshots; `stats.*.time` is the account-wide play time; skills carry `progress` (level
x 100) and `lastAccess`; the Arena document has `arenaStats.modes` = {mode key: {counter: int}} (tarkov.dev's
`arenaOverAllCounters` without the `Counters` level). `looked_up` (ISO 8601, UTC) is when Atlas asked the game
backend; `cached` says whether Atlas answered from its own 60-second cache.

Like profile.summarize(), nothing here raises on odd field types except a document that is not a profile at all.
"""
import datetime

from . import arena, profile
from .profile import SIDES, number
from .text import clean
from .transport import QueryError

SOURCE = 'atlas'


class AtlasProfileError(QueryError):
    def __init__(self):
        super().__init__('Atlas 返回的档案格式无法识别')


def timestamp(value):
    """Epoch seconds of an ISO 8601 time with a zone, or None."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    try:
        return moment.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def items(block):
    """Atlas counters as the tarkov.dev counter items profile.counter() reads."""
    raw = block.get('counters') if isinstance(block, dict) else None
    found = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], list):
            found.append({'Key': [part for part in entry[0] if isinstance(part, str)], 'Value': entry[1]})
    return found


def side_stats(block):
    data = items(block)
    count = lambda *parts: profile.counter(data, *parts)
    return {
        'raids': count('Sessions'),
        'survived': count('ExitStatus', 'Survived'),
        'killed': count('ExitStatus', 'Killed'),
        'mia': count('ExitStatus', 'MissingInAction') or count('ExitStatus', 'Left'),
        'runner': count('ExitStatus', 'Runner'),
        'transit': count('ExitStatus', 'Transit'),
        'kills': count('Kills'),
        'deaths': count('Deaths'),
        'pmc_kills': count('KilledPmc'),
        'streak': count('LongestWinStreak'),
    }


def common_skills(doc):
    """Atlas' common skills as the tarkov.dev skill entries profile.skills() reads."""
    block = doc.get('skills')
    common = block.get('common') if isinstance(block, dict) else None
    return [{'Id': skill.get('id'), 'Progress': skill.get('progress'), 'LastAccess': skill.get('lastAccess')}
            for skill in (common if isinstance(common, list) else []) if isinstance(skill, dict)]


def achievements(doc):
    """(number completed, [completion times])."""
    block = doc.get('achievements')
    if not isinstance(block, dict):
        return 0, []
    done = block.get('done')
    times = [number(entry.get('at')) for entry in done if isinstance(entry, dict)] if isinstance(done, list) else []
    count = number(block.get('count'))
    total = int(count) if count is not None and count >= 0 else (len(done) if isinstance(done, list) else 0)
    return total, [value for value in times if value]


def last_active(doc, achieved):
    """Latest skill access or achievement time in seconds, as profile.last_active() computes it."""
    latest = max([number(skill.get('LastAccess')) or 0 for skill in common_skills(doc)]+[0])
    return max([latest]+achieved) or None


def nickname(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get('nickname'), str) or not doc['nickname'].strip():
        raise AtlasProfileError()
    return clean(doc['nickname'], 32) or '未知'


def common(doc):
    """The fields every mode shares. Raises AtlasProfileError when the document is not a profile."""
    name = nickname(doc)
    count, achieved = achievements(doc)
    cached = doc.get('cached')
    return {
        'source': SOURCE,
        'aid': int(doc['accountId']) if isinstance(doc.get('accountId'), str) and doc['accountId'].isascii()
               and doc['accountId'].isdigit() and len(doc['accountId']) <= 12 else None,
        'nickname': name,
        'achievements': count,
        'last_active': last_active(doc, achieved),
        # When Atlas asked the game backend; the card's "data time".
        'updated': timestamp(doc.get('looked_up')),
        'cached': cached is True,
    }


def summarize_atlas(doc):
    """The display summary of a PVP, PVE or season document (same keys as profile.summarize())."""
    summary = common(doc)
    experience = number(doc.get('experience'))
    level = number(doc.get('level'))
    if level is None or level < 0 or level > 1000:
        level = profile.level_of(experience) if experience is not None and experience >= 0 else None
    stats = doc.get('stats') if isinstance(doc.get('stats'), dict) else {}
    pmc = stats.get('pmc') if isinstance(stats.get('pmc'), dict) else {}
    side = clean(doc.get('side') if isinstance(doc.get('side'), str) else '', 16)
    play = number(pmc.get('time'))
    summary.update({
        'side': SIDES.get(side.lower(), side or '未知'),
        'experience': experience,
        'level': int(level) if level is not None else None,
        'prestige': number(doc.get('prestigeLevel')),
        'play_seconds': play if play is not None and play >= 0 else None,
        'pmc': side_stats(pmc),
        'scav': side_stats(stats.get('scav')),
        'skills': profile.skills({'Common': common_skills(doc)}),
    })
    return summary


def summarize_atlas_arena(doc):
    """The display summary of an Arena document (same keys as arena.summarize_arena())."""
    summary = common(doc)
    block = doc.get('arenaStats')
    modes = block.get('modes') if isinstance(block, dict) else None
    wrapped = {key: {'Counters': value} for key, value in modes.items()} if isinstance(modes, dict) else {}
    summary.update({'arena': True, 'sections': arena.sections({'arenaOverAllCounters': wrapped})})
    return summary


def summarize(doc, mode):
    return summarize_atlas_arena(doc) if mode == 'arena' else summarize_atlas(doc)


def search_results(doc):
    """[(account id, nickname)] of a search answer, in Atlas' order; malformed entries are skipped."""
    players = doc.get('players') if isinstance(doc, dict) else None
    if not isinstance(players, list):
        raise AtlasProfileError()
    found = []
    for entry in players[:50]:
        if not isinstance(entry, dict):
            continue
        aid, name = entry.get('accountId'), entry.get('nickname')
        if isinstance(aid, (str, int)) and not isinstance(aid, bool) and str(aid).isascii() and str(aid).isdigit() \
                and 0 < len(str(aid)) <= 12 and int(aid) > 0 and isinstance(name, str) and name.strip():
            found.append((int(aid), clean(name, 32)))
    return found
