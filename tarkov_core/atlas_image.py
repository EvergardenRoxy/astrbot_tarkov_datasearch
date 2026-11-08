"""The Atlas player picture (PVP, PVE, season): a dark board after the Atlas website's player page, sized to be read
on a phone (the 1600-pixel board is usually shown about a quarter size, so text is 21 pixels or larger).

Sections, top to bottom: the Atlas logo and link (required by the Atlas author on every output of its data), the
player panel (level badge, level, name, side, experience; prestige, account ID, best league and last activity), the
mode's twelve combined numbers, equipment (four slots per row with tarkov.dev's item pictures; no weapon, holster,
melee or secure container slots; one line when Atlas sent none), the rare achievements as a 3 x 3 grid, the
horizontal combat table and exit bars, two-column skills, the ten latest achievements, and the footer with the
thanks to Atlas. Drawn locally with Pillow from atlas_card.build() and the pictures atlas_assets.py has on disk; a
missing picture draws an empty placeholder box, never a substitute. Text is ink-centred (mid()), fitted down to a
readable minimum and then clipped. The tarkov.dev renderers and the Arena picture are separate.
"""
import io

from PIL import Image, ImageDraw

from . import NAME, NOTICE, REPO, VERSION
from .atlas_card import when
from .image import jpeg
from .fonts import basic_font as font
from .matching_image import ATLAS_NAME, ATLAS_URL, logo

W = 1600
M = 32
BG, PANEL, PANEL_2, BAND, RULE = (13, 14, 16), (22, 24, 28), (28, 31, 36), (16, 30, 46), (44, 48, 56)
TEXT, DIM, FAINT = (228, 230, 232), (150, 155, 162), (98, 102, 110)
ACCENT, BAR, BAR_BG, GOLD = (96, 178, 240), (66, 120, 168), (34, 38, 44), (200, 164, 72)
TONES = {'common': (214, 216, 220), 'rare': (186, 140, 246), 'legendary': (240, 198, 64), 'seasonal': (92, 172, 244)}


def picture(data, box):
    """An RGBA picture scaled to fit `box` (w, h), or None when `data` is missing or unreadable."""
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert('RGBA')
    except Exception:
        return None
    scale = min(box[0]/image.width, box[1]/image.height)
    size = (max(1, round(image.width*scale)), max(1, round(image.height*scale)))
    return image.resize(size, Image.LANCZOS)


def paste_center(img, data, x, y, w, h):
    """Paste a picture centred in the box; False (nothing drawn) when there is no usable picture."""
    image = picture(data, (w, h))
    if image is None:
        return False
    img.paste(image, (round(x+(w-image.width)/2), round(y+(h-image.height)/2)), image)
    return True


def placeholder(d, x, y, w, h):
    d.rectangle([x, y, x+w, y+h], outline=RULE, width=1)


def mid(d, x, cy, text, f, fill, align='l'):
    """Draw `text` with its ink centred on the line y = cy. Digits/Latin are centred by 'H0' and Chinese by one
    ideograph, so mixed fonts (Oswald, Share Tech Mono, Noto Sans SC) in one row line up visually. align: l, m or r."""
    text = str(text)
    _, top, _, bottom = f.getbbox('H0' if text.isascii() else '技', anchor='ls')
    d.text((x, cy-(top+bottom)/2), text, font=f, fill=fill, anchor=align+'s')


def band(d, y, title):
    """A section title bar across the board; the y below it."""
    d.rectangle([M, y, W-M, y+44], fill=BAND, outline=(30, 52, 76))
    mid(d, M+20, y+22, title, font('sc', 20, 800), ACCENT)
    return y+60


def fitted(d, text, name, size, width, weight=None, minimum=10):
    """The largest font up to `size` that fits `width`, never below `minimum` (clip() the rest)."""
    while size > minimum and d.textlength(text, font=font(name, size, weight)) > width:
        size -= 1
    return font(name, size, weight)


def clip(d, text, f, width):
    text = str(text)
    if d.textlength(text, font=f) <= width:
        return text
    while text and d.textlength(text+'…', font=f) > width:
        text = text[:-1]
    return text+'…'


def header(img, d, card, icons):
    """Logo row and the player panel (badge, level, name, side, experience; prestige, account, league, last active
    on the right). The duplicate small numbers of the old header are gone: the overview panel shows them large."""
    mark = logo(64)
    img.paste(mark, (M, 26), mark)
    x = M+mark.width+24
    label = f'数据来源：{ATLAS_NAME}  '
    mid(d, x, 58, label, font('sc', 28, 700), TEXT)
    mid(d, x+d.textlength(label, font=font('sc', 28, 700)), 58, ATLAS_URL, font('sc', 28, 400), ACCENT)
    mid(d, W-M, 52, f"{card['mode_label']} 玩家档案", font('sc', 46, 800), TEXT, 'r')
    stamp = f"查询于 {card['looked_up'] or '—'}（UTC+8）" + ('，Atlas 缓存' if card['cached'] else '')
    mid(d, W-M, 110, stamp, font('sc', 24, 400), DIM, 'r')
    d.line([0, 146, W, 146], fill=RULE)

    top = 166
    d.rectangle([M, top, W-M, top+PLAYER_H], fill=PANEL, outline=RULE)
    if not paste_center(img, icons.get('level_badge'), *BADGE):
        placeholder(d, *BADGE)
    level = str(card['level'] if card['level'] is not None else '—')
    mid(d, M+184, top+110, level, fitted(d, level, 'osw', 110, 200, 500), TEXT)
    x, bar_w = M+400, 620
    name_font = fitted(d, str(card['nickname']), 'sc', 60, bar_w, 800, 36)
    mid(d, x, top+58, clip(d, card['nickname'], name_font, bar_w), name_font, ACCENT)
    side = str(card['side'])
    mid(d, x, top+122, side, font('osw', 36, 500), DIM)
    exp = f"EXP {card['experience']:,}".replace(',', ' ') if card['experience'] is not None else 'EXP —'
    mid(d, x+d.textlength(side, font=font('osw', 36, 500))+28, top+122, exp, font('mono', 32), TEXT)
    if card['progress'] is not None:
        d.rectangle([x, top+156, x+bar_w, top+170], fill=BAR_BG)
        d.rectangle([x, top+156, x+round(bar_w*card['progress']), top+170], fill=BAR)
        if card['to_next'] is not None:
            mid(d, x+bar_w, top+196, f"距下一级 {card['to_next']:,}".replace(',', ' '), font('sc', 24, 400), DIM, 'r')
    info_x, info_r = M+1090, W-M-24
    d.line([info_x-36, top+24, info_x-36, top+PLAYER_H-24], fill=RULE)
    prestige = f"{card['prestige']} 级" if card['prestige'] else '无'
    rows = (('转生', prestige), ('账号 ID', card['aid'] or '—'), ('最高段位', card['league']),
            ('最近活跃', card['last_active'] or '—'))
    for i, (name, value) in enumerate(rows):
        cy = top+38+i*48
        mid(d, info_x, cy, name, font('sc', 24, 500), DIM)
        f = fitted(d, str(value), 'sc', 28, info_r-info_x-130, 600, 22)
        mid(d, info_r, cy, clip(d, value, f, info_r-info_x-130), f, TEXT, 'r')
        if i == 0 and card['prestige']:
            vx = info_r-d.textlength(prestige, font=f)-50
            if not paste_center(img, icons.get('prestige'), vx, cy-20, 40, 40):
                placeholder(d, vx, cy-20, 40, 40)
    return top+PLAYER_H+24


PLAYER_H = 220
BADGE = (M+24, 166+40, 140, 140)


def headline(d, card, top):
    """The mode's twelve combined numbers, two rows of six, large."""
    y = large_band(d, top, f"{card['mode_label']} 总览（PMC＋SCAV）")
    cell, row_h = (W-2*M)/6, 128
    d.rectangle([M, y, W-M, y+2*row_h], fill=PANEL, outline=RULE)
    d.line([M, y+row_h, W-M, y+row_h], fill=RULE)
    for i, (name, value) in enumerate(card['headline']):
        cx, ry = M+cell*(i % 6)+cell/2, y+(i//6)*row_h
        accent = name in ('幸存率', 'K/D')
        centered(d, cx, ry+50, value, 'osw', 54, cell-20, ACCENT if accent else TEXT, 500)
        centered(d, cx, ry+100, name, 'sc', 26, cell-16, DIM, 500)
    return y+2*row_h+28


EQ_GAP, EQ_LABEL, EQ_BOX, EQ_NAME = 16, 46, 170, 44


def equipment(img, d, card, icons, top):
    """Eight slots, four per row, full width; one line when Atlas sent no equipment at all."""
    y = large_band(d, top, '装备')
    if not any(item for row in card['equipment'] for *_, item in row):
        d.rectangle([M, y, W-M, y+76], fill=PANEL, outline=RULE)
        mid(d, M+24, y+38, '暂无装备数据', font('sc', 28, 500), FAINT)
        return y+76+28
    w = (W-2*M-3*EQ_GAP)/4
    for row in card['equipment']:
        x = M
        for key, label, wide, item in row:
            d.rectangle([x, y, x+w, y+EQ_LABEL], fill=PANEL_2)
            d.line([x, y, x, y+EQ_LABEL], fill=GOLD, width=4)
            mid(d, x+16, y+EQ_LABEL/2, label, font('sc', 28, 600), TEXT)
            box_y = y+EQ_LABEL
            d.rectangle([x, box_y, x+w, box_y+EQ_BOX+EQ_NAME], fill=(30, 33, 38) if item else (19, 21, 24), outline=RULE)
            if item:
                if not paste_center(img, icons.get('items', {}).get(item['image']), x+16, box_y+12, w-32, EQ_BOX-16):
                    mid(d, x+w/2, box_y+EQ_BOX/2, '图片暂缺', font('sc', 24, 400), FAINT, 'm')
                centered(d, x+w/2, box_y+EQ_BOX+EQ_NAME/2-4, item['name'], 'sc', 26, w-24, TEXT, 500)
            else:
                mid(d, x+w/2, box_y+(EQ_BOX+EQ_NAME)/2, '空', font('sc', 28, 500), FAINT, 'm')
            x += w+EQ_GAP
        y += EQ_LABEL+EQ_BOX+EQ_NAME+EQ_GAP
    return y+12


def rare(img, d, card, icons, top):
    """Completion count and bar, then the rare achievements as a 3 x 3 grid of cards (icon, name, rarity, share)."""
    y = large_band(d, top, '稀有成就')
    data = card['achievements']
    total = data['total']
    big = f"{data['count']}" + (f' / {total}' if total else '')
    mid(d, M+8, y+30, big, font('osw', 52, 500), TEXT)
    note = (f"已达成成就（{round(data['count']/total*100)}%）" if total else '已达成成就')
    mid(d, M+8+d.textlength(big, font=font('osw', 52, 500))+24, y+32, note, font('sc', 28, 500), DIM)
    d.rectangle([M, y+70, W-M, y+86], fill=BAR_BG)
    if total:
        d.rectangle([M, y+70, M+round((W-2*M)*min(1, data['count']/total)), y+86], fill=BAR)
    y += 110
    if not data['rare']:
        mid(d, M+8, y+20, '暂无成就', font('sc', 28, 500), FAINT)
        return y+68
    gap, h = 16, 132
    w = (W-2*M-2*gap)/3
    for i, entry in enumerate(data['rare']):
        x, yy = M+(i % 3)*(w+gap), y+(i//3)*(h+gap)
        d.rectangle([x, yy, x+w, yy+h], fill=PANEL, outline=RULE)
        d.line([x, yy, x, yy+h], fill=TONES[entry['tone']], width=4)
        if not paste_center(img, icons.get('achievements', {}).get(entry['id']), x+18, yy+18, 96, 96):
            placeholder(d, x+18, yy+18, 96, 96)
        f = fitted(d, str(entry['name']), 'sc', 30, w-150, 700, 24)
        mid(d, x+132, yy+46, clip(d, entry['name'], f, w-150), f, TONES[entry['tone']])
        share = f"{entry['percent']:g}% 玩家" if entry['percent'] is not None else '—'
        mid(d, x+132, yy+94, f"{entry['rarity']} · {share}", font('sc', 25, 400), DIM)
    return y+((len(data['rare'])+2)//3)*(h+gap)+12


# Widths sum to the drawable board width. Rows follow the old dossier's reading order; the all-kills ratio says so,
# because the header and headline K/D count player kills only.
STAT_COLUMNS = (('', 140), ('战局', 140), ('撤离', 140), ('幸存率', 175), ('击杀', 150),
                ('阵亡', 130), ('K/D（全部击杀）', 265), ('击杀 PMC', 180), ('PMC K/D', 216))
EXIT_FIELDS = (('survived', '撤离', (96, 157, 101)), ('runner', '匆匆逃离', (197, 153, 58)),
               ('transit', '转移', (72, 130, 179)), ('killed', '阵亡', (169, 72, 72)),
               ('mia', '行动中失踪', (111, 111, 117)), ('left', '擅离职守', (155, 105, 165)))


def large_band(d, top, title):
    d.rectangle((M, top, W-M, top+66), fill=BAND, outline=(30, 52, 76))
    mid(d, M+20, top+33, title, font('sc', 34, 800), ACCENT)
    return top+86


def centered(d, cx, cy, value, face, size, width, color, weight=None, align='m', minimum=20):
    """Fit (down to `minimum`, then clip) `value` into `width`, ink-centred on cy."""
    value = str(value)
    f = fitted(d, value, face, size, width, weight, minimum)
    mid(d, cx, cy, clip(d, value, f, width), f, color, align)


def statistics(d, card, top):
    """Large horizontal PMC/Scav rows; header and headline use the same model's survival formula."""
    y = large_band(d, top, '统计数据')
    d.rectangle((M, y, W-M, y+62), fill=PANEL_2)
    x = M
    for label, width in STAT_COLUMNS:
        centered(d, x+width/2, y+31, label, 'sc', 30, width-12, DIM, 700)
        x += width
    y += 62
    for i, row in enumerate(card['combat_rows']):
        d.rectangle((M, y, W-M, y+88), fill=PANEL if i % 2 == 0 else BG)
        x = M
        for k, ((_, width), value) in enumerate(zip(STAT_COLUMNS, [row['title'], *row['values']])):
            centered(d, x+width/2, y+44, value, 'osw' if k == 0 else 'mono', 40 if k == 3 else 44,
                     width-12, ACCENT if k == 3 else TEXT)
            x += width
        d.line((M, y+88, W-M, y+88), fill=RULE)
        y += 88
    mid(d, M+10, y+34, '幸存率 =（撤离＋转移）÷总战局数', font('sc', 27, 500), ACCENT)
    return exit_bars(d, card, y+79)


def exit_bars(d, card, top):
    rows = card['combat_rows']
    mid(d, M+8, top+20, '撤离状态', font('sc', 30, 700), TEXT)
    pmc, scav = (row['stats'] for row in rows)
    note = f"图例数字为 PMC / SCAV · 最长连续幸存：PMC {pmc['streak']} 局，SCAV {scav['streak']} 局"
    mid(d, M+210, top+20, clip(d, note, font('sc', 24, 500), W-2*M-210), font('sc', 24, 500), DIM)
    x, bw, y = M+145, W-2*M-145, top+65
    for percent in (0, 25, 50, 75, 100):
        xx = x+bw*percent/100
        # The end labels hang inwards so "100%" stays inside the right margin.
        centered(d, xx-2 if percent == 100 else xx, y+3, f'{percent}%', 'mono', 21, 80, DIM,
                 align={0: 'l', 100: 'r'}.get(percent, 'm'))
        d.line((xx, y+17, xx, y+27), fill=DIM)
    y += 40
    for row in rows:
        stats = row['stats']
        mid(d, M+8, y+22, row['title'], font('osw', 34, 500), TEXT)
        d.rectangle((x, y, x+bw, y+44), fill=BAR_BG)
        start = x
        if stats['raids']:
            for key, _, color in EXIT_FIELDS:
                # Keep inconsistent upstream totals inside the bar; legend values remain the original counters.
                width = min(max(0, bw*stats[key]/stats['raids']), max(0, x+bw-start))
                if width > 0:
                    d.rectangle((start, y, start+width, y+44), fill=color)
                start += width
        else:
            mid(d, x+14, y+22, '暂无记录', font('sc', 23, 500), DIM)
        d.rectangle((x, y, x+bw, y+44), outline=DIM)
        y += 66
    legend_width = (W-2*M)/3
    for i, (key, label, color) in enumerate(EXIT_FIELDS):
        xx, yy = M+12+(i % 3)*legend_width, y+(i//3)*40
        d.rectangle((xx, yy+7, xx+22, yy+29), fill=color)
        line = f"{label} {pmc[key]}/{scav[key]}"
        f = fitted(d, line, 'sc', 25, legend_width-55, 500, 21)
        mid(d, xx+35, yy+18, clip(d, line, f, legend_width-55), f, DIM)
    return y+104


def skills(d, card, top):
    """Two columns, larger names/levels and ten 28-pixel squares per skill; elite remains gold."""
    if not card['skills']:
        return top
    y = large_band(d, top, '技能')
    gap = 36
    colw = (W-2*M-gap)/2
    for i, (name, level, elite) in enumerate(card['skills']):
        x, yy = M+(i % 2)*(colw+gap), y+(i//2)*78
        d.rectangle((x, yy, x+colw, yy+68), fill=PANEL if (i//2) % 2 == 0 else BG)
        name_font = fitted(d, str(name), 'sc', 34, 225, 600, 24)
        mid(d, x+14, yy+34, clip(d, name, name_font, 225), name_font, TEXT)
        for k in range(10):
            xx = x+255+k*35
            d.rectangle((xx, yy+21, xx+27, yy+48), outline=DIM,
                        fill=(GOLD if elite else BAR) if level >= (k+1)*5.1 else None)
        mid(d, x+colw-14, yy+34, '精英' if elite else str(level),
            font('sc', 35, 700) if elite else font('mono', 35), GOLD if elite else TEXT, 'r')
    return y+((len(card['skills'])+1)//2)*78+28


def latest(img, d, card, icons, top):
    """One large row per achievement: icon, name and rarity, then the description and share/time."""
    entries = card['achievements']['latest']
    y = large_band(d, top, f'最新获得的 {len(entries)} 个成就' if entries else '最新获得的成就')
    if not entries:
        mid(d, M+8, y+20, '暂无成就', font('sc', 28, 500), FAINT)
        return y+68
    h, text_x, side_w = 116, M+124, 380
    width = W-M-20-side_w-text_x
    for i, entry in enumerate(entries):
        d.rectangle([M, y, W-M, y+h], fill=PANEL if i % 2 == 0 else BG)
        if not paste_center(img, icons.get('achievements', {}).get(entry['id']), M+18, y+18, 80, 80):
            placeholder(d, M+18, y+18, 80, 80)
        f = fitted(d, str(entry['name']), 'sc', 32, width, 700, 26)
        mid(d, text_x, y+38, clip(d, entry['name'], f, width), f, TEXT)
        description = str(entry['description'] or '')
        mid(d, text_x, y+82, clip(d, description, font('sc', 24, 400), width), font('sc', 24, 400), DIM)
        mid(d, W-M-20, y+38, entry['rarity'], font('sc', 30, 700), TONES[entry['tone']], 'r')
        share = f"{entry['percent']:g}% 玩家" if entry['percent'] is not None else '—'
        stamp = when(entry['at']) if entry['at'] else None
        mid(d, W-M-20, y+82, f"{share} · {stamp or '—'}", font('sc', 24, 400), DIM, 'r')
        y += h
    return y+28


def footer(d, top):
    d.line([0, top, W, top], fill=RULE)
    lines = (f'数据来源：{ATLAS_NAME} {ATLAS_URL} · 物品与成就图片来自 tarkov.dev',
             '特别致谢：Atlas（EFTForge 项目的一部分，作者 Morph1ne / SouthHorizons76）',
             f"{NAME} v{VERSION} · {REPO.removeprefix('https://')}", NOTICE)
    for i, text in enumerate(lines):
        text = str(text)
        centered(d, W/2, top+36+i*38, text, 'sc', 24, W-2*M, DIM if i < 2 else FAINT, 400)
    return top+36+len(lines)*38+12


def render_atlas_image(card, icons=None):
    """JPEG bytes of the player picture. icons: {'level_badge': PNG bytes, 'prestige': PNG bytes,
    'items': {url: PNG bytes}, 'achievements': {achievement id: PNG bytes}}; anything missing draws a placeholder."""
    if card.get('arena'):
        from .atlas_arena_image import render_atlas_arena_image
        return render_atlas_arena_image(card)
    icons = icons or {}
    img = Image.new('RGB', (W, 7000), BG)
    d = ImageDraw.Draw(img)
    y = header(img, d, card, icons)
    y = headline(d, card, y)
    y = equipment(img, d, card, icons, y)
    y = rare(img, d, card, icons, y)
    y = statistics(d, card, y)
    y = skills(d, card, y)
    y = latest(img, d, card, icons, y)
    end = footer(d, y)
    return jpeg(img.crop((0, 0, W, end)))
