"""The content of one profile reply. The text, Markdown and image outputs are all built from this card, so they
always show the same facts."""
import datetime
import time

# Chat users of this plugin are mostly in China; times are shown in Beijing time and say so.
TZ = datetime.timezone(datetime.timedelta(hours=8))
STALE_DAYS = 30
# Exit statuses other than survived, in the game's Chinese wording and order (maintainer): 撤离 (survived),
# 匆匆逃离, 转移, KIA, 行动中失踪.
SURVIVED = '撤离'
EXITS = (('runner', '匆匆逃离'), ('transit', '转移'), ('killed', 'KIA'), ('mia', '行动中失踪'))


def when(seconds):
    """Beijing time of an epoch timestamp, or None when it is outside what datetime can represent."""
    try:
        return datetime.datetime.fromtimestamp(seconds, TZ).strftime('%Y-%m-%d %H:%M')
    except (OverflowError, OSError, ValueError):
        return None


def ratio(part, whole):
    return f'{part/whole*100:.1f}%' if whole else '—'


def stale_note(days):
    return f'这份档案已有 {days} 天没有更新：可能是该玩家很久没玩这个模式，也可能是 tarkov.dev 很久没有刷新过他的档案。'


BOT_LIMIT = 'tarkov.dev 和 Battlestate Games 目前都不向机器人开放玩家档案的查询'


def refresh_note(url):
    # Searching the player on the website was observed to rewrite the snapshot (handoff §16); opening the page by
    # account id runs the same live lookup in the site's code.
    return f'由于 {BOT_LIMIT}，数据不会自动更新：在 tarkov.dev 用该模式搜索一次这名玩家，或打开 {url}，即可刷新档案，几分钟后再查。'


ATLAS_URL = 'https://eftforge.com/atlas'


def is_atlas(card):
    return card.get('source') == 'atlas'


def source_line(card):
    """Where the data comes from and when it was taken, one line for text and Markdown replies."""
    if is_atlas(card):
        when_text = f"，查询于 {card['updated']}（北京时间）" if card['updated'] else ''
        cached = '，Atlas 60 秒内的缓存' if card.get('atlas_cached') else ''
        return f'数据来自 Atlas（{ATLAS_URL}）实时查询{when_text}{cached}'
    if card['updated']:
        return f"数据来自 tarkov.dev，档案更新于 {card['updated']}（北京时间）"
    return '数据来自 tarkov.dev（时间为北京时间）'


def image_source_line(card):
    """The image footer's data line for Atlas data; None keeps the tarkov.dev wording."""
    if not is_atlas(card):
        return None
    cached = ' · Atlas 缓存' if card.get('atlas_cached') else ''
    return f"数据来自 Atlas 实时查询 · 查询于 GMT+8 {card['updated'] or '—'}{cached}"


def tail_notes(card):
    """The notes after the facts: stale warning, the reply's notes, how to refresh, the player page (Atlas)."""
    notes = ([stale_note(card['stale_days'])] if card['stale_days'] else [])+list(card['notes'])
    if card.get('refresh_url'):
        notes.append(refresh_note(card['refresh_url']))
    if card.get('refresh_hint'):
        notes.append(card['refresh_hint'])
    if card.get('page_url'):
        notes.append(f"tarkov.dev 玩家页：{card['page_url']}")
    return notes


def side(title, stats):
    return {
        'title': title,
        'stats': stats,
        'empty': not stats['raids'] and not stats['kills'] and not stats['deaths'],
        'rate': ratio(stats['survived'], stats['raids']),
        'kd': f"{stats['kills']/stats['deaths']:.2f}" if stats['deaths'] else None,
        # PMC kills per death (maintainer): PMC kills / deaths.
        'pmc_kd': f"{stats['pmc_kills']/stats['deaths']:.2f}" if stats['deaths'] else None,
    }


def freshness(summary, now, refresh_url, refresh_hint, page_url):
    """The card fields about where the data comes from and how old it is (shared by the game and Arena cards)."""
    now = time.time() if now is None else now
    updated = when(summary['updated']) if summary['updated'] else None
    atlas = summary.get('source') == 'atlas'
    # Atlas asks the game backend at query time, so the tarkov.dev stale warning does not apply.
    days = (now-summary['updated'])/86400 if updated and not atlas else 0
    return {
        'source': 'atlas' if atlas else 'tarkov.dev',
        'atlas_cached': bool(summary.get('cached')) if atlas else False,
        'updated': updated,
        'stale_days': int(days) if days > STALE_DAYS else None,
        # The player's tarkov.dev page; a tarkov.dev profile only changes when someone looks the player up there.
        'refresh_url': refresh_url,
        # Atlas: how to ask for fresh data, and the player's page as a plain link.
        'refresh_hint': refresh_hint,
        'page_url': page_url,
    }


def build_card(summary, mode_label, skill_limit=10, notes=(), now=None, refresh_url=None, refresh_hint=None,
               page_url=None):
    shown = summary['skills'] if skill_limit > 0 else []
    elite = [(name, level, True) for name, level, is_elite in shown if is_elite]
    rest = [(name, level, False) for name, level, is_elite in shown if not is_elite][:skill_limit]
    return {
        **freshness(summary, now, refresh_url, refresh_hint, page_url),
        'mode': mode_label,
        'nickname': summary['nickname'],
        'aid': int(summary['aid']) if summary['aid'] is not None else None,
        'side': summary['side'],
        'level': summary['level'],
        'prestige': int(summary['prestige']) if summary['prestige'] else None,
        # Account-wide, not per mode: a pvp-season profile showed 7,141 h against 90 raids (review F-16).
        'play_hours': f"{summary['play_seconds']/3600:.0f}" if summary['play_seconds'] else None,
        'achievements': summary['achievements'],
        'last_active': when(summary['last_active']) if summary['last_active'] else None,
        'sides': [side('PMC', summary['pmc']), side('Scav', summary['scav'])],
        # Elite skills are always listed in full; skill_limit caps the others; 0 hides skills.
        'skills': elite+rest,
        'notes': list(notes),
    }
