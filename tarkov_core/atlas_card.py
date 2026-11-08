"""What the Atlas player picture shows, taken from one raw Atlas document (PVP, PVE or season).

Layout and wording follow the Atlas website's player page (maintainer's reference picture, 2026-11-08) with the
maintainer's changes: no favourite weapons/items, no endings, no edition fields, enlarged two-column skills, rare
achievements (by rarity, then newest; with names and global completion share), the ten latest achievements, item pictures from tarkov.dev.

Counter rules read off the reference picture: K/D = player kills / (killed + left the raid); K/D (all kills) =
all kills / (killed + left); survival rate (幸存率) = (survived + transit) / sessions. The headline counts player
kills as "击杀 PMC" and the table labels its all-kills ratio "K/D（全部击杀）", so equal labels mean equal numbers.
The headline adds PMC and Scav (also the longest survival streaks, as Atlas shows); the horizontal table and exit
bars show each side separately.
A counter Atlas leaves out means 0. Nothing here raises on odd field
types except a document without a nickname (atlas_profile.AtlasProfileError).
"""
import datetime
import time

from . import profile
from .atlas_profile import achievements as achievement_count, common_skills, items, nickname, timestamp
from .profile import SIDES, number
from .text import clean

TZ = datetime.timezone(datetime.timedelta(hours=8))
MODE_LABELS = {'pvp': 'PVP', 'pve': 'PVE', 'season': 'PVP 赛季'}
RARE_COUNT = 9
LATEST_COUNT = 10
# Equipment grid: (slot key, label, columns wide). The weapon, holster, melee and secure container slots are not
# shown (maintainer, 2026-11-08).
SLOT_ROWS = (
    (('Earpiece', '耳机', 1), ('Headwear', '头部装备', 1), ('FaceCover', '面部装备', 1), ('TacticalVest', '战术胸挂', 1)),
    (('ArmBand', '臂带', 1), ('ArmorVest', '身体护甲', 1), ('Eyewear', '眼部装备', 1), ('Backpack', '背包', 1)),
)
SHOWN_SLOTS = frozenset(key for row in SLOT_ROWS for key, _, _ in row)
# Atlas rarity -> (label, colour key used by the picture).
RARITIES = {'common': ('普通', 'common'), 'rare': ('罕见', 'rare'), 'legendary': ('传奇', 'legendary'),
            'seasonal': ('赛季', 'seasonal')}
# Rare achievements are ranked by rarity, then newest first (maintainer, 2026-11-08: Atlas' completion share is 0 for
# every PVE achievement, so it cannot rank; seasonal first).
RARITY_ORDER = {'seasonal': 0, 'legendary': 1, 'rare': 2, 'common': 3}
ASSET_PREFIX = 'https://assets.tarkov.dev/'


def when(seconds, pattern='%Y-%m-%d %H:%M'):
    try:
        return datetime.datetime.fromtimestamp(seconds, TZ).strftime(pattern)
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def online(seconds):
    """2466h32m, like Atlas."""
    if not seconds or seconds < 0:
        return '—'
    minutes = int(seconds//60)
    return f'{minutes//60}h{minutes % 60:02d}m'


def percent(part, whole):
    return f'{round(part/whole*100)}%' if whole else '—'


def ratio(part, whole):
    return f'{part/whole:.2f}' if whole else '—'


def counts(block):
    data = items(block)
    count = lambda *parts: profile.counter(data, *parts)
    return {
        'raids': count('Sessions'),
        'survived': count('ExitStatus', 'Survived'),
        'killed': count('ExitStatus', 'Killed'),
        'mia': count('ExitStatus', 'MissingInAction'),
        'left': count('ExitStatus', 'Left'),
        'runner': count('ExitStatus', 'Runner'),
        'transit': count('ExitStatus', 'Transit'),
        'kills': count('Kills'),
        'player_kills': count('KilledPmc'),
        'streak': count('LongestWinStreak'),
    }


def survival_rate(stats):
    """Transit counts as a successful exit, as requested by the maintainer."""
    return f"{(stats['survived']+stats['transit'])/stats['raids']*100:.1f}%" if stats['raids'] else '—'


def combat_row(title, stats):
    """Horizontal combat-table values, plus raw counters for the exit bars and legend."""
    lost = stats['killed']+stats['left']
    return {'title': title, 'stats': stats, 'values': [
        f"{stats['raids']:,}", f"{stats['survived']:,}", survival_rate(stats),
        f"{stats['kills']:,}", f"{stats['killed']:,}", ratio(stats['kills'], lost),
        f"{stats['player_kills']:,}", ratio(stats['player_kills'], lost),
    ]}


def headline(total, play):
    """The twelve numbers under the mode name (two rows of six)."""
    lost = total['killed']+total['left']
    return [
        ('战局数', f"{total['raids']:,}"), ('幸存数', f"{total['survived']:,}"), ('阵亡次数', f"{total['killed']:,}"),
        ('行动中失踪', f"{total['mia']:,}"), ('擅离职守', f"{total['left']:,}"), ('击杀 PMC', f"{total['player_kills']:,}"),
        ('匆匆逃离', f"{total['runner']:,}"), ('幸存率', survival_rate(total)),
        ('连续幸存数', f"{total['streak']:,}"), ('擅离率', percent(total['left'], total['raids'])),
        ('K/D', ratio(total['player_kills'], lost)), ('在线', online(play)),
    ]


def text_of(block, key='name'):
    """Chinese text of an {en, ch} block, else English, else ''."""
    if not isinstance(block, dict):
        return ''
    for language in ('ch', 'en'):
        part = block.get(language)
        value = part.get(key) if isinstance(part, dict) else (part if key == 'name' else None)
        if isinstance(value, str) and value.strip():
            return clean(value, 160)
    return ''


def asset(url):
    """`url` when it is an https link on assets.tarkov.dev (the only picture host used), else None."""
    return url if isinstance(url, str) and url.startswith(ASSET_PREFIX) and len(url) <= 300 else None


def short_name(entry):
    """The English short name, as on the Atlas page (ComTac VI, ULACH)."""
    short = entry.get('en', {}).get('short') if isinstance(entry.get('en'), dict) else None
    return clean(short, 40) if isinstance(short, str) and short.strip() else '—'


def slots(doc):
    """{slot key: {'name', 'image'}} of the equipped items in the shown slots; tarkov.dev's plain item picture."""
    found = {}
    for entry in doc.get('slots') if isinstance(doc.get('slots'), list) else []:
        if isinstance(entry, dict) and entry.get('slot') in SHOWN_SLOTS:
            found[entry['slot']] = {'name': short_name(entry), 'image': asset(entry.get('image')) or asset(entry.get('icon'))}
    return found


def achievement_list(doc):
    block = doc.get('achievements') if isinstance(doc.get('achievements'), dict) else {}
    found = []
    for entry in block.get('done') if isinstance(block.get('done'), list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            continue
        rarity = entry.get('rarity').lower() if isinstance(entry.get('rarity'), str) else ''
        label, tone = RARITIES.get(rarity, (clean(entry.get('rarity') or '', 12) or '—', 'common'))
        share = number(entry.get('percent'))
        found.append({'id': clean(entry['id'], 40), 'name': text_of(entry) or '—', 'rank': RARITY_ORDER.get(rarity, len(RARITY_ORDER)),
                      'description': text_of(entry, 'description'), 'rarity': label, 'tone': tone,
                      'percent': share if share is not None and share >= 0 else None,
                      'at': number(entry.get('at'))})
    return found


def league(doc):
    block = doc.get('league')
    if not isinstance(block, dict):
        return '—'
    return text_of({'ch': block.get('maxName', {}).get('ch') if isinstance(block.get('maxName'), dict) else None,
                    'en': block.get('maxName', {}).get('en') if isinstance(block.get('maxName'), dict) else None}) or '—'


def achievement_data(doc):
    """Shared achievement text and ordering for the main game and Arena. Text comes from Atlas, ch before en."""
    count, _ = achievement_count(doc)
    block = doc.get('achievements') if isinstance(doc.get('achievements'), dict) else {}
    available = number(block.get('total'))
    done = achievement_list(doc)
    return {'count': count, 'total': int(available) if available is not None and available >= count else None,
            'rare': sorted(done, key=lambda a: (a['rank'], -(a['at'] or 0)))[:RARE_COUNT],
            'latest': sorted(done, key=lambda a: -(a['at'] or 0))[:LATEST_COUNT]}


def build(doc, mode, *, skill_limit=10, now=None):
    """The picture's content. Picture links are collected in 'assets' (item pictures only; icons that come from
    tarkov.dev's public data are looked up by the caller)."""
    if mode == 'arena':
        from .atlas_arena_image import build_arena
        return build_arena(doc, now=now)
    name = nickname(doc)
    now = time.time() if now is None else now
    stats = doc.get('stats') if isinstance(doc.get('stats'), dict) else {}
    pmc, scav = counts(stats.get('pmc')), counts(stats.get('scav'))
    total = {key: pmc[key]+scav[key] for key in pmc}
    play_block = stats.get('pmc') if isinstance(stats.get('pmc'), dict) else {}
    play = number(play_block.get('time'))
    experience = number(doc.get('experience'))
    level = number(doc.get('level'))
    if level is None or not 0 <= level <= 1000:
        level = profile.level_of(experience) if experience is not None and experience >= 0 else None
    level_exp = doc.get('levelExp') if isinstance(doc.get('levelExp'), dict) else {}
    start, goal = number(level_exp.get('current')), number(level_exp.get('next'))
    progress = None
    if experience is not None and start is not None and goal is not None and goal > start:
        progress = min(1, max(0, (experience-start)/(goal-start)))
    side = clean(doc.get('side') if isinstance(doc.get('side'), str) else '', 16)
    achievement = achievement_data(doc)
    done = achievement_list(doc)
    rare, latest = achievement['rare'], achievement['latest']
    skills = profile.skills({'Common': common_skills(doc)})
    shown = skills if skill_limit > 0 else []
    elite = [s for s in shown if s[2]]
    rest = [s for s in shown if not s[2]][:skill_limit]
    equipment = slots(doc)
    prestige = number(doc.get('prestigeLevel'))
    aid = doc.get('accountId')
    return {
        'mode': mode, 'mode_label': MODE_LABELS.get(mode, mode),
        'nickname': name,
        'aid': aid if isinstance(aid, str) and aid.isascii() and aid.isdigit() and len(aid) <= 12 else None,
        'side': SIDES.get(side.lower(), side or '—'),
        'level': int(level) if level is not None else None,
        'experience': int(experience) if experience is not None and experience >= 0 else None,
        'to_next': int(goal-experience) if goal is not None and experience is not None and goal > experience else None,
        'progress': progress,
        'prestige': int(prestige) if prestige and prestige > 0 else 0,
        'league': league(doc),
        'looked_up': when(timestamp(doc.get('looked_up')), '%Y-%m-%d %H:%M:%S') if timestamp(doc.get('looked_up')) else None,
        'cached': doc.get('cached') is True,
        'last_active': when(max([number(s.get('LastAccess')) or 0 for s in common_skills(doc)]
                                + [a['at'] or 0 for a in done]+[0]) or None),
        'headline': headline(total, play),
        'combat_rows': [combat_row('PMC', pmc), combat_row('SCAV', scav)],
        'achievements': achievement,
        'skills': elite+rest,
        'equipment': [[(key, label, wide, equipment.get(key)) for key, label, wide in row] for row in SLOT_ROWS],
        'assets': sorted({item['image'] for item in equipment.values() if item['image']}),
        'achievement_ids': sorted({a['id'] for a in rare+latest}),
        'now': now,
    }
