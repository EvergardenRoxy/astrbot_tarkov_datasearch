"""Tarkov Arena profiles: reading the document, the card, and the text and Markdown replies.

An Arena profile on players.tarkov.dev (`arena/<aid>.json`) has the same account id and nickname as the main game but
no PMC/Scav statistics and no skills: `stat.arenaOverAllCounters` maps a mode key to `{"Counters": {name: int}}`
(handoff §19.2). Keys starting with "Unranked" are unranked play, the others ranked; "Overall" sums a section.
Mode names stay in English, as in the game (maintainer); the level is not shown (maintainer: the website derives it
from the main game's level table).
"""
import re
import time

from .card import STALE_DAYS, refresh_note, stale_note, when
from .output import markdown_refresh, md
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


def overall_text(stats):
    parts = [f"对局 {stats.get('games', 0)}", f"击杀 {stats.get('kills', 0)}", f"死亡 {stats.get('deaths', 0)}",
             f"K/D {kd(stats) or '—'}"]
    for key, label in (('best_arp', '最佳 ARP'), ('max_killstreak', '最长连杀'), ('win_streak', '最长连胜')):
        if stats.get(key):
            parts.append(f'{label} {stats[key]}')
    return '，'.join(parts)


def mode_text(label, stats):
    parts = [f"对局 {stats.get('games', 0)}"]
    if stats.get('wins') is not None:
        rate = win_rate(stats)
        parts.append(f"胜 {stats['wins']}" + (f'（{rate}）' if rate else ''))
    parts += [f"击杀 {stats.get('kills', 0)}", f"死亡 {stats.get('deaths', 0)}", f"K/D {kd(stats) or '—'}"]
    if stats.get('mvp'):
        parts.append(f"MVP {stats['mvp']}")
    return f'　{label}：' + '，'.join(parts)


def footer(card, quote=''):
    lines = []
    if card['updated']:
        lines.append(f"数据来自 tarkov.dev，档案更新于 {card['updated']}（北京时间）")
    else:
        lines.append('数据来自 tarkov.dev（时间为北京时间）')
    if card['stale_days']:
        lines.append(stale_note(card['stale_days']))
    lines += [md(note) if quote else note for note in card['notes']]
    if card['refresh_url']:
        lines.append(markdown_refresh(card['refresh_url']) if quote else refresh_note(card['refresh_url']))
    return [quote+line for line in lines]


def render_arena_text(card):
    aid = f"（账号 ID {card['aid']}）" if card['aid'] is not None else ''
    lines = [f"【塔科夫竞技场档案】{card['nickname']}{aid}"]
    extra = [f"成就 {card['achievements']} 个"]
    if card['last_active']:
        extra.append(f"最近活跃 {card['last_active']}")
    lines += ['｜'.join(extra), '']
    if not card['sections']:
        lines.append('暂无竞技场对局记录')
    for section in card['sections']:
        head = f"{section['title']}：" + (overall_text(section['overall']) if section['overall'] else '（无总计）')
        lines.append(head)
        lines += [mode_text(label, stats) for label, stats in section['modes']]
    return '\n'.join(lines+['']+footer(card))


def render_arena_markdown(card):
    lines = [f"## 塔科夫竞技场档案：{md(card['nickname'])}", '']
    basics = [f"**账号 ID** {card['aid']}"] if card['aid'] is not None else []
    basics.append(f"成就 {card['achievements']} 个")
    if card['last_active']:
        basics.append(f"最近活跃 {card['last_active']}")
    lines += [' · '.join(basics), '']
    if not card['sections']:
        lines += ['暂无竞技场对局记录', '']
    for section in card['sections']:
        lines.append(f"### {section['title']}")
        if section['overall']:
            lines.append(overall_text(section['overall']))
        lines += ['', '| 模式 | 对局 | 胜 | 胜率 | 击杀 | 死亡 | K/D | MVP |', '|---|---:|---:|---:|---:|---:|---:|---:|']
        for label, stats in section['modes']:
            wins = stats.get('wins')
            lines.append(f"| {md(label)} | {stats.get('games', 0)} | {'—' if wins is None else wins} | "
                         f"{win_rate(stats) or '—'} | {stats.get('kills', 0)} | {stats.get('deaths', 0)} | "
                         f"{kd(stats) or '—'} | {stats.get('mvp', 0)} |")
        lines.append('')
    return '\n'.join(lines+footer(card, '> '))
