"""What the Atlas player picture shows, taken from one raw Atlas document (PVP, PVE or season).

Layout and wording follow the Atlas website's player page (maintainer's reference picture, 2026-11-08) with the
maintainer's changes: no favourite weapons/items, no endings, no edition fields, skills drawn the old way, rare
achievements (by rarity, then newest; with names and global completion share), the ten latest achievements, item pictures from tarkov.dev.

Counter rules read off the reference picture: K/D = player kills / (killed + left the raid); K/D (all kills) =
all kills / (killed + left); AI kills = all kills - player kills; the overview column adds PMC and Scav (also the
longest survival streaks, as Atlas shows). A counter Atlas leaves out means 0. Nothing here raises on odd field
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
# Equipment grid, as on the Atlas page: (slot key, label, columns wide).
SLOT_ROWS = (
    (('Earpiece', '耳机', 1), ('Headwear', '头部装备', 1), ('FaceCover', '面部装备', 1), ('TacticalVest', '战术胸挂', 1)),
    (('ArmBand', '臂带', 1), ('ArmorVest', '身体护甲', 1), ('Eyewear', '眼部装备', 1), ('Backpack', '背包', 1)),
    (('FirstPrimaryWeapon', '主武器（吊索）', 2), ('Holster', '枪套', 1), ('SecuredContainer', '安全箱', 1)),
    (('SecondPrimaryWeapon', '主武器（背部）', 2), ('Scabbard', '刀鞘', 1)),
)
# Atlas rarity -> (label, colour key used by the picture).
RARITIES = {'common': ('普通', 'common'), 'rare': ('罕见', 'rare'), 'legendary': ('传奇', 'legendary'),
            'seasonal': ('赛季', 'seasonal')}
# Rare achievements are ranked by rarity, then newest first (maintainer, 2026-11-08: Atlas' completion share is 0 for
# every PVE achievement, so it cannot rank). The place of "seasonal" in this order is a choice to confirm.
RARITY_ORDER = {'legendary': 0, 'seasonal': 1, 'rare': 2, 'common': 3}
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
    return f'{round(part/whole*100)} %' if whole else '—'


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


def column(stats, play):
    """The rows of one statistics column (Atlas' order, minus the two values Atlas does not provide)."""
    lost = stats['killed']+stats['left']
    return [
        ('战局数', f"{stats['raids']:,}"), ('幸存数', f"{stats['survived']:,}"),
        ('幸存率', percent(stats['survived'], stats['raids'])),
        ('K/D', ratio(stats['player_kills'], lost)), ('K/D（全部击杀）', ratio(stats['kills'], lost)),
        ('击杀数（玩家）', f"{stats['player_kills']:,}"),
        ('击杀数（AI）', f"{max(0, stats['kills']-stats['player_kills']):,}"),
        ('阵亡次数', f"{stats['killed']:,}"), ('行动中失踪', f"{stats['mia']:,}"), ('擅离职守', f"{stats['left']:,}"),
        ('匆匆逃离', f"{stats['runner']:,}"), ('转移', f"{stats['transit']:,}"),
        ('擅离率', percent(stats['left'], stats['raids'])), ('连续幸存数', f"{stats['streak']:,}"),
        ('在线', online(play)),
    ]


def headline(total, play):
    """The twelve numbers under the mode name (two rows of six)."""
    lost = total['killed']+total['left']
    return [
        ('战局数', f"{total['raids']:,}"), ('幸存数', f"{total['survived']:,}"), ('阵亡次数', f"{total['killed']:,}"),
        ('行动中失踪', f"{total['mia']:,}"), ('擅离职守', f"{total['left']:,}"), ('击杀数', f"{total['player_kills']:,}"),
        ('匆匆逃离', f"{total['runner']:,}"), ('幸存率', percent(total['survived'], total['raids'])),
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


def slots(doc):
    """{slot key: {'name', 'image'}} of the equipped items; tarkov.dev's plain item picture, never Atlas' render."""
    found = {}
    for entry in doc.get('slots') if isinstance(doc.get('slots'), list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get('slot'), str):
            continue
        # The English short name, as on the Atlas page (ComTac VI, ULACH); Chinese names would need many more glyphs.
        short = entry.get('en', {}).get('short') if isinstance(entry.get('en'), dict) else None
        name = clean(short, 40) if isinstance(short, str) and short.strip() else '—'
        found[entry['slot']] = {'name': name, 'image': asset(entry.get('image')) or asset(entry.get('icon'))}
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


def build(doc, mode, *, skill_limit=10, now=None):
    """The picture's content. Picture links are collected in 'assets' (item pictures only; icons that come from
    tarkov.dev's public data are looked up by the caller)."""
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
    count, total_achievements = achievement_count(doc)
    block = doc.get('achievements') if isinstance(doc.get('achievements'), dict) else {}
    available = number(block.get('total'))
    done = achievement_list(doc)
    rare = sorted(done, key=lambda a: (a['rank'], -(a['at'] or 0)))[:RARE_COUNT]
    latest = sorted(done, key=lambda a: -(a['at'] or 0))[:LATEST_COUNT]
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
        'columns': [('总览', column(total, play)), ('PMC', column(pmc, play)), ('SCAV', column(scav, play))],
        'achievements': {'count': count, 'total': int(available) if available and available >= count else None,
                         'rare': rare, 'latest': latest},
        'skills': elite+rest,
        'equipment': [[(key, label, wide, equipment.get(key)) for key, label, wide in row] for row in SLOT_ROWS],
        'assets': sorted({item['image'] for item in equipment.values() if item['image']}),
        'achievement_ids': sorted({a['id'] for a in rare+latest}),
        'now': now,
    }
