"""Weekly report maths: compact samples of Atlas documents and the change between them. Pure functions, no I/O.

A sample keeps only the numbers a report needs (1-3 KB). Atlas has no list of raids, so a report is the sum of the
steps between consecutive samples of the last seven days (rolling, counted back from the newest sample; maintainer,
2026-11-10). A counter that goes down restarted from zero (wipe, season reset, prestige), so that step counts the
new value. Samples are normally taken once a night (weekly_tasks.py); a step is put on the "game day" its later
sample closes, i.e. the date twelve hours before that sample (a 03:00 sample closes the day before).
Nothing here raises on odd field types. Arena is not covered yet (docs/weekly-report-plan.md).
"""
import datetime

from .atlas_card import MODE_LABELS, achievement_list, combat_row, counts, online, ratio, survival_rate
from .atlas_history import looked_up_seconds
from .atlas_profile import AtlasProfileError, achievements as achievement_count, common_skills, nickname
from .modes import GAME_MODES
from .profile import ELITE_PROGRESS, STATIC, number

TZ = datetime.timezone(datetime.timedelta(hours=8))
WEEK = 7*86400
# A sample this close to "seven days before the newest one" starts the week; else the oldest one inside it does.
SLACK = 48*3600
# No report when the newest sample is older than this, or when the samples span less than MIN_SPAN.
STALE = 3*86400
MIN_SPAN = 20*3600
# A step belongs to the date this long before its later sample.
DAY_SHIFT = 12*3600
# Achievements newer than this (before the lookup) are kept in a sample, with their names.
RECENT = 21*86400
MAX_RECENT = 40
SUM_KEYS = ('raids', 'survived', 'killed', 'mia', 'left', 'runner', 'transit', 'kills', 'player_kills')
WEEKDAYS = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')
RANK_ROWS = 20
# The best survival rate is only named among players with at least this many raids.
RATE_RAIDS = 5


def extract_sample(doc, mode):
    """The compact sample of one Atlas document of the main game, or None when it cannot be used."""
    if mode not in GAME_MODES or not isinstance(doc, dict):
        return None
    moment = looked_up_seconds(doc)
    try:
        name = nickname(doc)
    except AtlasProfileError:
        return None
    if moment is None:
        return None
    stats = doc.get('stats') if isinstance(doc.get('stats'), dict) else {}
    pmc_block = stats.get('pmc') if isinstance(stats.get('pmc'), dict) else {}
    level, experience = number(doc.get('level')), number(doc.get('experience'))
    prestige, play = number(doc.get('prestigeLevel')), number(pmc_block.get('time'))
    skills = {}
    for skill in common_skills(doc):
        progress, access = number(skill.get('Progress')), number(skill.get('LastAccess'))
        if isinstance(skill.get('Id'), str) and skill['Id'] in STATIC['skills'] and progress and progress > 0 \
                and access and access > 0:
            skills[skill['Id']] = max(skills.get(skill['Id'], 0), int(min(progress, ELITE_PROGRESS)))
    recent = [{'id': a['id'], 'name': a['name'], 'rarity': a['rarity'], 'tone': a['tone'], 'at': a['at']}
              for a in achievement_list(doc) if a['at'] and moment-RECENT <= a['at'] <= moment+86400]
    recent.sort(key=lambda a: -a['at'])
    return {
        'v': 1, 'looked_up': moment, 'nickname': name,
        'level': int(level) if level is not None and 0 <= level <= 1000 else None,
        'experience': int(experience) if experience is not None and experience >= 0 else None,
        'prestige': int(prestige) if prestige and prestige > 0 else 0,
        'play': int(play) if play is not None and play >= 0 else None,
        'pmc': counts(pmc_block), 'scav': counts(stats.get('scav')),
        'achievements': achievement_count(doc)[0], 'recent': recent[:MAX_RECENT], 'skills': skills,
    }


def increase(values):
    """(sum of the steps, whether a counter restarted). None values are skipped."""
    values = [value for value in values if value is not None]
    total, reset = 0, False
    for before, after in zip(values, values[1:]):
        if after >= before:
            total += after-before
        else:
            total, reset = total+after, True
    return total, reset


def game_day(seconds):
    return datetime.datetime.fromtimestamp(seconds-DAY_SHIFT, TZ).date()


def window(samples, now):
    """The samples of the last seven days, oldest first: from the one nearest to seven days before the newest (within
    SLACK, else the oldest inside the week) to the newest. None when the newest is stale or nothing lies at least
    MIN_SPAN before it."""
    usable = sorted((s for s in samples if s['looked_up'] <= now+3600), key=lambda s: s['looked_up'])
    if not usable or now-usable[-1]['looked_up'] > STALE:
        return None
    last = usable[-1]
    target = last['looked_up']-WEEK
    earlier = [s for s in usable if target-SLACK <= s['looked_up'] <= last['looked_up']-MIN_SPAN]
    if not earlier:
        return None
    near = [s for s in earlier if abs(s['looked_up']-target) <= SLACK]
    first = min(near, key=lambda s: (abs(s['looked_up']-target), s['looked_up'])) if near else earlier[0]
    return [s for s in usable if first['looked_up'] <= s['looked_up'] <= last['looked_up']]


def build_report(samples, mode, now):
    """The numbers of one account's last seven days, or None when the samples do not describe them."""
    series = window(samples, now)
    if series is None:
        return None
    first, last = series[0], series[-1]
    reset = False
    sides = {}
    for side in ('pmc', 'scav'):
        sides[side] = {}
        for key in SUM_KEYS:
            sides[side][key], dropped = increase([s[side][key] for s in series])
            reset = reset or dropped
    total = {key: sides['pmc'][key]+sides['scav'][key] for key in SUM_KEYS}
    career = {key: last['pmc'][key]+last['scav'][key] for key in SUM_KEYS}
    end = game_day(last['looked_up'])
    days = [end-datetime.timedelta(days=6-i) for i in range(7)]
    daily, sampled = [0]*7, [False]*7
    for before, after in zip(series, series[1:]):
        day = min(6, max(0, (game_day(after['looked_up'])-days[0]).days))
        sampled[day] = True
        for side in ('pmc', 'scav'):
            daily[day] += increase([before[side]['raids'], after[side]['raids']])[0]
    skills = []
    for key, progress in last['skills'].items():
        before = first['skills'].get(key, 0)
        if progress > before:
            skills.append((key, before, progress))
    skills.sort(key=lambda item: (-(item[2]-item[1]), item[0]))
    best = max(last['pmc']['streak'], last['scav']['streak'])
    return {
        'mode': mode, 'nickname': last['nickname'],
        'covered_from': first['looked_up'], 'covered_to': last['looked_up'], 'samples': len(series),
        'span_days': max(1, round((last['looked_up']-first['looked_up'])/86400)), 'reset': reset,
        'pmc': sides['pmc'], 'scav': sides['scav'], 'total': total, 'career': career,
        'level': (first['level'], last['level']),
        'experience': increase([s['experience'] for s in series])[0],
        'play_seconds': increase([s['play'] for s in series])[0],
        'streak_record': best if best > max(first['pmc']['streak'], first['scav']['streak']) else None,
        'days': [(WEEKDAYS[day.weekday()], day.strftime('%m-%d')) for day in days],
        'daily': daily, 'daily_sampled': sampled,
        'achievements': [a for a in last['recent'] if first['looked_up'] <= a['at'] <= last['looked_up']],
        'skills': skills[:6],
    }


def stamp(seconds, pattern='%m-%d %H:%M'):
    return datetime.datetime.fromtimestamp(seconds, TZ).strftime(pattern)


def notes(report):
    found = []
    if report['span_days'] < 6:
        found.append(f"样本只覆盖 {report['span_days']} 天")
    if report['reset']:
        found.append('期间数据有过重置（删档、赛季重置或声望）')
    return found


def build_card(report):
    """What the personal weekly picture shows (display strings), from build_report()'s numbers."""
    total, career = report['total'], report['career']
    lost, career_lost = total['killed']+total['left'], career['killed']+career['left']
    before, after = report['level']
    level = '—' if after is None else str(after) if before in (None, after) else f'{before} → {after}'
    skills = [(STATIC['skills'][key]['zh'], f'Lv {old//100} → {new//100}', f'+{(new-old)/100:.2f}')
              for key, old, new in report['skills'] if key in STATIC['skills']]
    gained = sorted(report['achievements'], key=lambda a: -a['at'])
    return {
        'weekly': True, 'mode': report['mode'], 'mode_label': MODE_LABELS.get(report['mode'], report['mode']),
        'nickname': report['nickname'], 'range': f"{report['days'][0][1]} ～ {report['days'][-1][1]}",
        'notes': notes(report), 'level': level,
        'experience': f"+{report['experience']:,}" if report['experience'] else '—',
        'play': '+'+online(report['play_seconds']) if report['play_seconds'] else '—',
        'streak_record': report['streak_record'],
        'tiles': [
            ('战局', f"{total['raids']:,}", f"生涯 {career['raids']:,}"),
            ('幸存率', survival_rate(total), f'生涯 {survival_rate(career)}'),
            ('击杀 PMC', f"{total['player_kills']:,}", f"生涯 {career['player_kills']:,}"),
            ('PMC K/D', ratio(total['player_kills'], lost), f"生涯 {ratio(career['player_kills'], career_lost)}"),
            ('总击杀', f"{total['kills']:,}", f"生涯 {career['kills']:,}"),
            ('阵亡', f"{total['killed']:,}", f"失踪 {total['mia']} · 擅离 {total['left']}"),
        ],
        # atlas_image.statistics() and exit_bars() read these two rows (the streak is not a weekly number).
        'combat_rows': [combat_row('PMC', dict(report['pmc'], streak=0)), combat_row('SCAV', dict(report['scav'], streak=0))],
        'daily': [(weekday, date, raids, sampled) for (weekday, date), raids, sampled
                  in zip(report['days'], report['daily'], report['daily_sampled'])],
        'achievements': [{'name': a['name'], 'rarity': a['rarity'], 'tone': a['tone'], 'when': stamp(a['at'])} for a in gained[:8]],
        'achievement_more': max(0, len(gained)-8), 'skills': skills,
    }


def build_ranking(entries, mode):
    """The ranking picture's content for one mode. entries: [(name, report or None)], one per listed player.
    None when nobody has a report. Most raids first, then most PMC kills."""
    ranked = sorted((report for _, report in entries if report), key=lambda r: (-r['total']['raids'],
                    -r['total']['player_kills'], r['nickname'].lower()))
    if not ranked:
        return None
    def row(place, report):
        total = report['total']
        before, after = report['level']
        return {'place': place, 'nickname': report['nickname'], 'raids': f"{total['raids']:,}",
                'survival': survival_rate(total), 'player_kills': f"{total['player_kills']:,}",
                'kd': ratio(total['player_kills'], total['killed']+total['left']), 'kills': f"{total['kills']:,}",
                'killed': f"{total['killed']:,}",
                'level': '—' if after is None else str(after) if before in (None, after) else f'{before}→{after}'}
    def best(label, key, among=ranked, text=lambda value: f'{value:,}'):
        pool = [report for report in among if key(report) > 0]
        if not pool:
            return (label, '—', '')
        top = max(pool, key=key)
        return (label, top['nickname'], text(key(top)))
    rate = lambda r: (r['total']['survived']+r['total']['transit'])/r['total']['raids']
    newest = max(ranked, key=lambda r: r['covered_to'])
    return {
        'ranking': True, 'mode': mode, 'mode_label': MODE_LABELS.get(mode, mode),
        'range': f"{newest['days'][0][1]} ～ {newest['days'][-1][1]}",
        'rows': [row(place, report) for place, report in enumerate(ranked[:RANK_ROWS], 1)],
        'more': max(0, len(ranked)-RANK_ROWS),
        'missing': [name for name, report in entries if not report],
        'highlights': [
            best('战局最多', lambda r: r['total']['raids'], text=lambda value: f'{value:,} 局'),
            best('幸存率最高', rate, [r for r in ranked if r['total']['raids'] >= RATE_RAIDS], lambda value: f'{value*100:.1f}%'),
            best('击杀 PMC 最多', lambda r: r['total']['player_kills']),
            best('总击杀最多', lambda r: r['total']['kills']),
        ],
    }
