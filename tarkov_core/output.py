"""Plain chat text for a profile summary."""
import datetime
import time

# Chat users of this plugin are mostly in China; times are shown in Beijing time and say so.
TZ = datetime.timezone(datetime.timedelta(hours=8))
STALE_DAYS = 30


def when(seconds):
    """Beijing time of an epoch timestamp, or None when it is outside what datetime can represent."""
    try:
        return datetime.datetime.fromtimestamp(seconds, TZ).strftime('%Y-%m-%d %H:%M')
    except (OverflowError, OSError, ValueError):
        return None


def ratio(part, whole):
    return f'{part/whole*100:.1f}%' if whole else '—'


def side_lines(title, stats):
    if not stats['raids'] and not stats['kills'] and not stats['deaths']:
        return [f'{title}：暂无记录']
    kd = f"{stats['kills']/stats['deaths']:.2f}" if stats['deaths'] else '—（无死亡记录）'
    lines = [
        f"{title}：战局 {stats['raids']}，存活 {stats['survived']}，存活率 {ratio(stats['survived'], stats['raids'])}",
        f"　击杀 {stats['kills']}，死亡 {stats['deaths']}，K/D {kd}，击杀 PMC {stats['pmc_kills']}",
    ]
    exits = [(label, stats[key]) for label, key in (('阵亡', 'killed'), ('失踪', 'mia'), ('Run Through', 'runner'), ('转图', 'transit'))]
    detail = [f'{label} {value}' for label, value in exits if value]
    if stats['streak']:
        detail.append(f"最长连续存活 {stats['streak']} 局")
    if detail:
        lines.append('　'+'，'.join(detail))
    return lines


def render(summary, mode_label, skill_limit=10, notes=(), now=None):
    now = time.time() if now is None else now
    aid = f"（账号 ID {int(summary['aid'])}）" if summary['aid'] is not None else ''
    lines = [f"【塔科夫 {mode_label} 档案】{summary['nickname']}{aid}"]
    basics = [f"阵营 {summary['side']}"]
    if summary['level'] is not None:
        basics.append(f"等级 {summary['level']}")
    if summary['prestige']:
        basics.append(f"声望 {int(summary['prestige'])}")
    lines.append('｜'.join(basics))
    extra = []
    if summary['play_seconds']:
        # Account-wide, not per mode: a pvp-season profile showed 7,141 h against 90 raids (review F-16).
        extra.append(f"账号总游戏时长 {summary['play_seconds']/3600:.0f} 小时")
    extra.append(f"成就 {summary['achievements']} 个")
    lines.append('｜'.join(extra))
    active = when(summary['last_active']) if summary['last_active'] else None
    if active:
        lines.append(f'最近活跃 {active}')
    lines.append('')
    lines += side_lines('PMC', summary['pmc'])
    lines += side_lines('Scav', summary['scav'])
    if skill_limit > 0 and summary['skills']:
        elite = [name for name, _, is_elite in summary['skills'] if is_elite]
        rest = [(name, level) for name, level, is_elite in summary['skills'] if not is_elite][:skill_limit]
        if elite:
            lines.append(f"精英技能（{len(elite)} 项）：{'、'.join(elite)}")
        if rest:
            lines.append(f"其他技能（前 {len(rest)} 项）：{'、'.join(f'{name} {level}' for name, level in rest)}")
    lines.append('')
    updated = when(summary['updated']) if summary['updated'] else None
    if updated:
        lines.append(f'数据来自 tarkov.dev，档案更新于 {updated}（北京时间）')
        days = (now-summary['updated'])/86400
        if days > STALE_DAYS:
            lines.append(f'这份档案已有 {int(days)} 天没有更新：可能是该玩家很久没玩这个模式，也可能是 tarkov.dev 很久没有刷新过他的档案。')
    else:
        lines.append('数据来自 tarkov.dev（时间为北京时间）')
    lines += list(notes)
    return '\n'.join(lines)
