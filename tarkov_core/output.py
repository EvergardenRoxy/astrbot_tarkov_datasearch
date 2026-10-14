"""Plain-text and Markdown replies, built from a card (card.py)."""
import re

from . import NAME, NOTICE, REPO, VERSION
from .card import BOT_LIMIT, EXITS, SURVIVED, build_card, refresh_note, stale_note


def side_lines(card_side):
    title, stats = card_side['title'], card_side['stats']
    if card_side['empty']:
        return [f'{title}：暂无记录']
    kd = card_side['kd'] or '—（无死亡记录）'
    lines = [
        f"{title}：战局 {stats['raids']}，{SURVIVED} {stats['survived']}，存活率 {card_side['rate']}",
        f"　击杀 {stats['kills']}，死亡 {stats['deaths']}，K/D {kd}，击杀 PMC {stats['pmc_kills']}，PMC K/D {card_side['pmc_kd'] or '—'}",
    ]
    detail = [f'{label} {stats[key]}' for key, label in EXITS if stats[key]]
    if stats['streak']:
        detail.append(f"最长连续存活 {stats['streak']} 局")
    if detail:
        lines.append('　'+'，'.join(detail))
    return lines


def render_text(card):
    aid = f"（账号 ID {card['aid']}）" if card['aid'] is not None else ''
    lines = [f"【塔科夫 {card['mode']} 档案】{card['nickname']}{aid}"]
    basics = [f"阵营 {card['side']}"]
    if card['level'] is not None:
        basics.append(f"等级 {card['level']}")
    if card['prestige']:
        basics.append(f"声望 {card['prestige']}")
    lines.append('｜'.join(basics))
    extra = [f"账号总游戏时长 {card['play_hours']} 小时"] if card['play_hours'] else []
    extra.append(f"成就 {card['achievements']} 个")
    lines.append('｜'.join(extra))
    if card['last_active']:
        lines.append(f"最近活跃 {card['last_active']}")
    lines.append('')
    for card_side in card['sides']:
        lines += side_lines(card_side)
    elite = [name for name, _, is_elite in card['skills'] if is_elite]
    rest = [(name, level) for name, level, is_elite in card['skills'] if not is_elite]
    if elite:
        lines.append(f"精英技能（{len(elite)} 项）：{'、'.join(elite)}")
    if rest:
        lines.append(f"其他技能（前 {len(rest)} 项）：{'、'.join(f'{name} {level}' for name, level in rest)}")
    lines.append('')
    if card['updated']:
        lines.append(f"数据来自 tarkov.dev，档案更新于 {card['updated']}（北京时间）")
    else:
        lines.append('数据来自 tarkov.dev（时间为北京时间）')
    if card['stale_days']:
        lines.append(stale_note(card['stale_days']))
    lines += card['notes']
    if card['refresh_url']:
        lines.append(refresh_note(card['refresh_url']))
    return '\n'.join(lines+source_lines())


def source_lines():
    """The repository and the open-source statement, after every text profile reply (maintainer)."""
    return [f'{NAME} v{VERSION} · {REPO}', NOTICE]


def render(summary, mode_label, skill_limit=10, notes=(), now=None):
    """The plain-text reply for a profile summary."""
    return render_text(build_card(summary, mode_label, skill_limit, notes, now))


MARKDOWN_SPECIAL = re.compile(r'([\\`*_{}\[\]()#+\-.!|>~<])')


def md(text):
    """Escape third-party or user text so it shows literally (nicknames such as Test_Player contain '_')."""
    return MARKDOWN_SPECIAL.sub(r'\\\1', str(text))


def markdown_refresh(url):
    # The link is built by the plugin from a mode and a number, so it is not escaped.
    return f"由于 {BOT_LIMIT}，数据不会自动更新：在 tarkov.dev 用该模式搜索一次这名玩家，或打开[这名玩家的页面]({url})，即可刷新档案，几分钟后再查。"


def render_markdown(card):
    lines = [f"## 塔科夫 {card['mode']} 档案：{md(card['nickname'])}", '']
    basics = []
    if card['aid'] is not None:
        basics.append(f"**账号 ID** {card['aid']}")
    basics.append(f"**阵营** {md(card['side'])}")
    if card['level'] is not None:
        basics.append(f"**等级** {card['level']}")
    if card['prestige']:
        basics.append(f"**声望** {card['prestige']}")
    lines.append(' · '.join(basics)+'  ')
    extra = [f"账号总游戏时长 {card['play_hours']} 小时"] if card['play_hours'] else []
    extra.append(f"成就 {card['achievements']} 个")
    if card['last_active']:
        extra.append(f"最近活跃 {card['last_active']}")
    lines += [' · '.join(extra), '']
    lines.append(f'| | 战局 | {SURVIVED} | 存活率 | 击杀 | 死亡 | K/D | 击杀 PMC | PMC K/D | 最长连续存活 |')
    lines.append('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
    for card_side in card['sides']:
        stats = card_side['stats']
        if card_side['empty']:
            lines.append(f"| {card_side['title']} | 暂无记录 | | | | | | | | |")
            continue
        lines.append(f"| {card_side['title']} | {stats['raids']} | {stats['survived']} | {card_side['rate']} | "
                     f"{stats['kills']} | {stats['deaths']} | {card_side['kd'] or '—'} | {stats['pmc_kills']} | {card_side['pmc_kd'] or '—'} | {stats['streak']} |")
    lines.append('')
    for card_side in card['sides']:
        if not card_side['empty']:
            stats = card_side['stats']
            exits = ' · '.join([f"{SURVIVED} {stats['survived']}"]+[f'{label} {stats[key]}' for key, label in EXITS])
            # Two trailing spaces: a Markdown line break, so the two sides stay on separate lines.
            lines.append(f"**撤离状态（{card_side['title']}）**：{exits}  ")
    elite = [name for name, _, is_elite in card['skills'] if is_elite]
    rest = [(name, level) for name, level, is_elite in card['skills'] if not is_elite]
    if elite or rest:
        lines.append('')
    if elite:
        lines.append(f"**精英技能（{len(elite)} 项）**：{'、'.join(elite)}  ")
    if rest:
        lines.append(f"**其他技能（前 {len(rest)} 项）**：{'、'.join(f'{name} {level}' for name, level in rest)}")
    lines.append('')
    if card['updated']:
        lines.append(f"> 数据来自 tarkov.dev，档案更新于 {card['updated']}（北京时间）")
    else:
        lines.append('> 数据来自 tarkov.dev（时间为北京时间）')
    if card['stale_days']:
        lines.append('> '+stale_note(card['stale_days']))
    lines += ['> '+md(note) for note in card['notes']]
    if card['refresh_url']:
        lines.append('> '+markdown_refresh(card['refresh_url']))
    return '\n'.join(lines+markdown_source_lines())


def markdown_source_lines():
    return ['>', f'> {md(NAME)} v{VERSION} · {REPO}', f'> {NOTICE}']
