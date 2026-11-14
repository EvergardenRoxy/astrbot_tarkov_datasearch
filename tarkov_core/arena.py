"""Tarkov Arena profiles: reading the document, the card, and the text and Markdown replies.

An Arena profile on players.tarkov.dev (`arena/<aid>.json`) has the same account id and nickname as the main game but
no PMC/Scav statistics and no skills: `stat.arenaOverAllCounters` maps a mode key to `{"Counters": {name: int}}`
Keys starting with "Unranked" are unranked play, the others ranked; "Overall" sums a section.
Mode names stay in English, as in the game (maintainer); the level is not shown (maintainer: the website derives it
from the main game's level table).
"""
import re
import time

from .card import STALE_DAYS, when
from .profile import last_active, number
from .text import clean
from .transport import QueryError

# Counters kept for each mode: name in the document -> name in the card.
COUNTERS = {
    'GamesCount': 'games', 'ArenaWins': 'wins', 'ArenaLoses': 'losses', 'Kills': 'kills', 'Deaths': 'deaths',
    'Assists': 'assists', 'Headshots': 'headshots', 'MatchMvpCount': 'mvp', 'RoundMvpCount': 'round_mvp',
    'BestArp': 'best_arp', 'MaxKillsWithoutDeaths': 'max_killstreak', 'LongestWinStreak': 'win_streak',
}
# Labels tarkov.dev's player page uses; other modes show their key as it is.
LABELS = {'TeamFight': 'Team Fight', 'LastHero': 'Last Hero', 'CheckPoint': 'Checkpoint', 'BlastGang': 'Blast Gang'}
ORDER = ('TeamFight', 'LastHero', 'CheckPoint', 'BlastGang', 'ShootOutDuo')
MODE_KEY = re.compile(r'[A-Za-z]{1,40}')
# Modes kept per section at most, whatever the document holds.
MAX_MODES = 12
SECTIONS = ((False, '非排位'), (True, '排位'))
# Arena ranks (maintainer's table): (highest ARP of the rank, tier, name shown). Neighbouring ranks overlap by 100 ARP
# (promotion above the upper limit, demotion below the next rank's lower one); the profile only has the best ARP
# ever reached, so a rank counts as reached once the best ARP passed the previous rank's upper limit.
RANKS = ((1000, 'D', 'Marauder'), (2000, 'D+', 'Jackal'), (3000, 'C', 'Mercenary'), (4000, 'C+', 'Fighter'),
         (5000, 'B', 'Warrior'), (6000, 'B+', 'Gladiator'), (7000, 'A', 'Veteran'), (7900, 'A+', 'Professional'))
# Above this the ranks are leaderboard places (Elite 7900+, then Top 1000/100/10/1) the profile cannot tell apart:
# shown as one label without the ARP (maintainer).
TOP_ARP = 7900
TOP_LABEL = '竞技场前100名玩家'


def counters(entry):
    """{card name: non-negative int} of one mode; anything that is not a finite number is left out."""
    values = entry.get('Counters') if isinstance(entry, dict) else None
    if not isinstance(values, dict):
        return {}
    found = {}
    for source, name in COUNTERS.items():
        value = number(values.get(source))
        if value is not None:
            found[name] = max(0, int(value))
    return found


def sections(stat):
    """[{'ranked', 'title', 'overall', 'modes': [(label, counters)]}] for the sections that have data."""
    raw = stat.get('arenaOverAllCounters') if isinstance(stat, dict) else None
    grouped = {False: {}, True: {}}
    for key, entry in raw.items() if isinstance(raw, dict) else ():
        if not isinstance(key, str) or not MODE_KEY.fullmatch(key):
            continue
        ranked = not key.startswith('Unranked')
        base = key if ranked else key[len('Unranked'):]
        stats = counters(entry)
        if base and stats:
            grouped[ranked][base] = stats
    result = []
    for ranked, title in SECTIONS:
        modes = grouped[ranked]
        overall = modes.pop('Overall', None)
        keys = sorted(modes, key=lambda key: (ORDER.index(key) if key in ORDER else len(ORDER), key))[:MAX_MODES]
        if overall or keys:
            result.append({'ranked': ranked, 'title': title, 'overall': overall,
                           'modes': [(LABELS.get(key, key), modes[key]) for key in keys]})
    return result


def summarize_arena(raw):
    """The display summary of an Arena profile. Raises QueryError when the document is not a profile."""
    info = raw.get('info')
    if not isinstance(info, dict) or not isinstance(info.get('nickname'), str):
        raise QueryError('tarkov.dev 返回的竞技场档案格式无法识别')
    achievements = raw.get('achievements')
    updated = number(raw.get('updated'))
    return {
        'arena': True,
        'aid': number(raw.get('aid')),
        'nickname': clean(info['nickname'], 32) or '未知',
        'achievements': len(achievements) if isinstance(achievements, (dict, list)) else 0,
        'last_active': last_active(raw),
        'updated': updated/1000 if updated else None,
        'sections': sections(raw.get('stat')),
    }


def rank(arp):
    """(tier, name) of an ARP, ('TOP', TOP_LABEL) above TOP_ARP, None without an ARP."""
    if not arp:
        return None
    if arp > TOP_ARP:
        return 'TOP', TOP_LABEL
    return next((tier, name) for limit, tier, name in RANKS if arp <= limit)


def kd(stats):
    deaths = stats.get('deaths')
    return f"{stats.get('kills', 0)/deaths:.2f}" if deaths else None


def losses(stats):
    """Losses of a mode: the counter when the document has it, else games minus wins; None without wins."""
    if stats.get('losses') is not None:
        return stats['losses']
    return max(0, stats.get('games', 0)-stats['wins']) if stats.get('wins') is not None else None


def record(section):
    """(wins, losses) summed over a section's modes, or None when no mode counts wins."""
    counted = [(stats['wins'], losses(stats)) for _, stats in section['modes'] if stats.get('wins') is not None]
    return (sum(w for w, _ in counted), sum(l for _, l in counted)) if counted else None


def best_arp(card):
    """The highest ARP in any section or mode, or None."""
    values = [stats.get('best_arp') for section in card['sections']
              for stats in [section['overall'] or {}]+[stats for _, stats in section['modes']]]
    values = [value for value in values if value]
    return max(values) if values else None


def win_rate(stats):
    games, wins = stats.get('games'), stats.get('wins')
    return f'{wins/games*100:.1f}%' if games and wins is not None else None


def build_arena_card(summary, notes=(), now=None, refresh_url=None):
    now = time.time() if now is None else now
    updated = when(summary['updated']) if summary['updated'] else None
    days = (now-summary['updated'])/86400 if updated else 0
    return {
        'arena': True,
        'mode': '竞技场',
        'nickname': summary['nickname'],
        'aid': int(summary['aid']) if summary['aid'] is not None else None,
        'achievements': summary['achievements'],
        'last_active': when(summary['last_active']) if summary['last_active'] else None,
        'updated': updated,
        'stale_days': int(days) if days > STALE_DAYS else None,
        'sections': summary['sections'],
        'notes': list(notes),
        'refresh_url': refresh_url,
    }
