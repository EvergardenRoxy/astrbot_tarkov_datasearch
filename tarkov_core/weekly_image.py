"""The weekly report pictures on the dark Atlas board (atlas_image.py): one account's last seven days, and the
ranking of a mode's listed players.

Both carry the Atlas logo and link in the header (required by the Atlas author on every output of its data) and
the thanks in the footer. The personal picture reuses atlas_image's large PMC/SCAV table and exit bars with the
week's numbers. Drawn locally with Pillow from weekly_report.build_card() / build_ranking(); no pictures are fetched.
"""
from PIL import Image, ImageDraw

from . import NAME, NOTICE, REPO, VERSION
from .atlas_image import (ACCENT, BAR, BG, DIM, FAINT, GOLD, M, PANEL, PANEL_2, RULE, TEXT, TONES, W, centered, clip,
                          fitted, large_band, mid, statistics)
from .fonts import basic_font as font
from .image import jpeg
from .matching_image import ATLAS_NAME, ATLAS_URL, logo

WARN = (232, 168, 72)
TOP = 24
TILE_H, TILE_GAP = 150, 16
# Ranking table: (label, width, row key); widths sum to the board's drawable width.
RANK_COLUMNS = (('#', 80, 'place'), ('玩家', 400, 'nickname'), ('战局', 140, 'raids'), ('幸存率', 180, 'survival'),
                ('击杀 PMC', 180, 'player_kills'), ('PMC K/D', 170, 'kd'), ('总击杀', 160, 'kills'),
                ('阵亡', 110, 'killed'), ('等级', 116, 'level'))


def source(img, d, top):
    """The Atlas logo and link at the right of a header panel, as on the player picture."""
    mark = logo(48)
    img.paste(mark, (W-M-24-mark.width, top+22), mark)
    label = f'数据来源：{ATLAS_NAME}  '
    width = d.textlength(ATLAS_URL, font=font('sc', 20, 400))
    mid(d, W-M-24, top+98, ATLAS_URL, font('sc', 20, 400), ACCENT, 'r')
    mid(d, W-M-24-width, top+98, label, font('sc', 20, 600), TEXT, 'r')


def header(img, d, card):
    height = 132+36*len(card['notes'])
    d.rectangle([M, TOP, W-M, TOP+height], fill=PANEL, outline=RULE)
    name = str(card['nickname'])
    name_font = fitted(d, name, 'sc', 42, 560, 800)
    mid(d, M+24, TOP+44, clip(d, name, name_font, 560), name_font, ACCENT)
    mid(d, M+24, TOP+100, f"等级 {card['level']}", font('sc', 26, 600), TEXT)
    mid(d, M+250, TOP+100, '经验', font('sc', 26, 600), DIM)
    mid(d, M+316, TOP+100, card['experience'], font('mono', 26), TEXT)
    x = M+640
    mid(d, x, TOP+34, f"{card['mode_label']} 周报 · 近 7 天", font('sc', 30, 800), TEXT)
    mid(d, x, TOP+74, card['range'], font('sc', 24, 500), DIM)
    extra = f"账号总在线 {card['play']}" + (f"　连续幸存新纪录 {card['streak_record']} 局" if card['streak_record'] else '')
    mid(d, x, TOP+108, clip(d, extra, font('sc', 20, 500), 470), font('sc', 20, 500), GOLD if card['streak_record'] else DIM)
    source(img, d, TOP)
    for i, note in enumerate(card['notes']):
        mid(d, M+24, TOP+146+i*36, clip(d, '※ '+note, font('sc', 22, 500), W-2*M-48), font('sc', 22, 500), WARN)
    return TOP+height+20


def tiles(d, entries, top, accent=None):
    """A row of equal tiles: (label, value, small line). `accent` is the index drawn in the accent colour."""
    width = (W-2*M-TILE_GAP*(len(entries)-1))/len(entries)
    for i, (label, value, sub) in enumerate(entries):
        x = M+i*(width+TILE_GAP)
        d.rectangle([x, top, x+width, top+TILE_H], fill=PANEL, outline=RULE)
        mid(d, x+18, top+28, label, font('sc', 22, 600), DIM)
        face = 'osw' if str(value).isascii() else 'sc'
        value_font = fitted(d, str(value), face, 58 if face == 'osw' else 40, width-36, 500 if face == 'osw' else 800, 18)
        mid(d, x+18, top+80, clip(d, value, value_font, width-36), value_font, ACCENT if i == accent else TEXT)
        mid(d, x+18, top+126, clip(d, sub, font('sc', 18, 400), width-36), font('sc', 18, 400), FAINT)
    return top+TILE_H+24


def daily(d, card, top):
    y = large_band(d, top, '每日战局')
    height, base = 170, y+44+170
    most = max([raids for _, _, raids, _ in card['daily']]+[1])
    step = (W-2*M)/7
    for i, (weekday, date, raids, sampled) in enumerate(card['daily']):
        cx = M+step*i+step/2
        bar = round(height*raids/most)
        if raids:
            d.rectangle([cx-56, base-bar, cx+56, base], fill=BAR)
        d.line([cx-70, base, cx+70, base], fill=DIM)
        centered(d, cx, base-bar-24, str(raids) if sampled or raids else '—', 'mono', 30, step-20, TEXT if raids else FAINT)
        centered(d, cx, base+28, weekday, 'sc', 26, step-20, DIM, 600)
        centered(d, cx, base+60, date, 'mono', 20, step-20, FAINT)
    return base+96


def listing(d, top, title, rows, empty):
    """A band and one line per row: (left text, left colour, middle text, right text)."""
    y = large_band(d, top, title)
    if not rows:
        mid(d, M+14, y+16, empty, font('sc', 26, 500), DIM)
        return y+60
    for i, (left, tone, middle, last) in enumerate(rows):
        d.rectangle([M, y, W-M, y+60], fill=PANEL if i % 2 == 0 else BG)
        mid(d, M+18, y+30, clip(d, left, font('sc', 28, 600), 760), font('sc', 28, 600), tone)
        mid(d, M+820, y+30, middle, font('sc', 25, 500), DIM)
        mid(d, W-M-18, y+30, last, font('mono', 26), TEXT, 'r')
        y += 60
    return y+24


def footer(d, top):
    d.line([0, top, W, top], fill=RULE)
    f = font('sc', 15, 400)
    lines = (f'数据来源：{ATLAS_NAME} {ATLAS_URL} · 周报按每日采样之间的差值计算，仅供娱乐',
             '特别致谢：Atlas（EFTForge 项目的一部分，作者 Morph1ne / SouthHorizons76）',
             f"{NAME} v{VERSION} · {REPO.removeprefix('https://')}", NOTICE)
    for i, text in enumerate(lines):
        d.text((W/2-d.textlength(text, font=f)/2, top+12+i*26), text, font=f, fill=FAINT)
    return top+12+len(lines)*26+16


def render_weekly_image(card):
    """JPEG bytes of one account's weekly report (weekly_report.build_card), or of a ranking (build_ranking)."""
    if card.get('ranking'):
        return render_ranking_image(card)
    img = Image.new('RGB', (W, 5000), BG)
    d = ImageDraw.Draw(img)
    y = header(img, d, card)
    y = tiles(d, card['tiles'], y, accent=1)
    y = statistics(d, card, y, title='近 7 天战绩', note='图例数字为 PMC / SCAV，均为近 7 天新增')
    y = daily(d, card, y)
    gained = [(a['name'], TONES.get(a['tone'], TEXT), a['rarity'], a['when']) for a in card['achievements']]
    if card['achievement_more']:
        gained.append((f"另有 {card['achievement_more']} 项未列出", DIM, '', ''))
    y = listing(d, y, '新成就', gained, '近 7 天没有新成就')
    y = listing(d, y, '技能成长', [(name, TEXT, levels, gain) for name, levels, gain in card['skills']], '近 7 天没有技能提升')
    return jpeg(img.crop((0, 0, W, footer(d, y))))


def render_ranking_image(card):
    """JPEG bytes of a mode's ranking: highlights, then the listed players by raids."""
    img = Image.new('RGB', (W, 400+72*len(card['rows'])+700), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([M, TOP, W-M, TOP+132], fill=PANEL, outline=RULE)
    mid(d, M+24, TOP+46, f"{card['mode_label']} 周报排行", font('sc', 42, 800), ACCENT)
    mid(d, M+24, TOP+100, f"近 7 天 · {card['range']} · {len(card['rows'])+card['more']} 名玩家", font('sc', 24, 500), DIM)
    source(img, d, TOP)
    y = tiles(d, card['highlights'], TOP+152)
    d.rectangle((M, y, W-M, y+62), fill=PANEL_2)
    x = M
    for label, width, _ in RANK_COLUMNS:
        centered(d, x+width/2, y+31, label, 'sc', 28, width-12, DIM, 700)
        x += width
    y += 62
    for i, row in enumerate(card['rows']):
        d.rectangle((M, y, W-M, y+72), fill=PANEL if i % 2 == 0 else BG)
        x = M
        for label, width, key in RANK_COLUMNS:
            value = str(row[key])
            if key == 'nickname':
                name_font = fitted(d, value, 'sc', 32, width-24, 700, 18)
                mid(d, x+12, y+36, clip(d, value, name_font, width-24), name_font, TEXT)
            else:
                color = GOLD if key == 'place' and row['place'] <= 3 else ACCENT if key == 'survival' else TEXT
                # The level may hold an arrow (41→42), which the digit font lacks.
                face = 'osw' if key == 'place' else 'sc' if key == 'level' else 'mono'
                centered(d, x+width/2, y+36, value, face, 30 if key == 'level' else 34, width-12, color,
                         500 if key == 'level' else None)
            x += width
        d.line((M, y+72, W-M, y+72), fill=RULE)
        y += 72
    y += 20
    lines = []
    if card['more']:
        lines.append(f"另有 {card['more']} 名玩家未列出")
    if card['missing']:
        lines.append('样本不足：'+'、'.join(card['missing']))
    lines.append('按近 7 天战局数排序；幸存率 =（撤离＋转移）÷战局数，PMC K/D = 击杀 PMC ÷（阵亡＋擅离职守）')
    for text in lines:
        mid(d, M+10, y+16, clip(d, text, font('sc', 22, 500), W-2*M-20), font('sc', 22, 500), DIM)
        y += 36
    return jpeg(img.crop((0, 0, W, footer(d, y+12))))
