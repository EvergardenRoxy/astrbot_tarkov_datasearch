"""The Atlas player picture (PVP, PVE, season): a dark board after the Atlas website's player page.

Sections, top to bottom: the mode title and query time, the player header (level badge, level, name, experience;
the Atlas logo and link on its right, required by the Atlas author on every output of its data), equipment with tarkov.dev's item pictures (no weapon, holster,
melee or secure container slots) beside the mode's headline numbers, the rare achievements as one full-width row (name and rarity; no player share),
the horizontal combat table and exit bars, enlarged two-column skills,
the ten latest achievements (no player share column), and the footer with the thanks to Atlas. Drawn locally with Pillow from atlas_card.build()
and the pictures atlas_assets.py has on disk; a missing picture draws an empty placeholder box, never a substitute.
The tarkov.dev renderers remain separate; their survival calculation is aligned in the shared snapshot card.
"""
import io

from PIL import Image, ImageDraw

from . import NAME, NOTICE, REPO, VERSION
from .atlas_card import when
from .image import jpeg, wrap
from .fonts import basic_font as font
from .matching_image import ATLAS_NAME, ATLAS_URL, logo, right

W = 1600
M = 32
BG, PANEL, PANEL_2, BAND, RULE = (13, 14, 16), (22, 24, 28), (28, 31, 36), (16, 30, 46), (44, 48, 56)
TEXT, DIM, FAINT = (228, 230, 232), (150, 155, 162), (98, 102, 110)
ACCENT, BAR, BAR_BG, GOLD = (96, 178, 240), (66, 120, 168), (34, 38, 44), (200, 164, 72)
TONES = {'common': (214, 216, 220), 'rare': (186, 140, 246), 'legendary': (240, 198, 64), 'seasonal': (92, 172, 244)}
LEFT_W, RIGHT_X = 960, M+960+24
CELL_W, CELL_H, CELL_GAP, LABEL_H = 186, 150, 12, 26


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
    """Logo row and the player header; the y below."""
    mid(d, M, 48, f"{card['mode_label']} 玩家档案", font('sc', 34, 800), TEXT)
    stamp = f"查询于 {card['looked_up'] or '—'}（UTC+8）" + ('，Atlas 缓存' if card['cached'] else '')
    mid(d, W-M, 48, stamp, font('sc', 20, 400), DIM, 'r')
    d.line([0, 96, W, 96], fill=RULE)

    top = 112
    d.rectangle([M, top, W-M, top+132], fill=PANEL, outline=RULE)
    badge_drawn = paste_center(img, icons.get('level_badge'), M+20, top+22, 88, 88)
    if not badge_drawn:
        placeholder(d, M+20, top+22, 88, 88)
    d.text((M+124, top+20), str(card['level'] if card['level'] is not None else '—'), font=font('osw', 72, 500), fill=TEXT)
    x = M+320
    d.text((x, top+20), str(card['nickname']), font=fitted(d, str(card['nickname']), 'sc', 38, 520, 800), fill=ACCENT)
    d.text((x, top+76), f"{card['side']}", font=font('osw', 22, 500), fill=DIM)
    exp = f"EXP {card['experience']:,}".replace(',', ' ') if card['experience'] is not None else 'EXP —'
    d.text((x+90, top+78), exp, font=font('mono', 20), fill=TEXT)
    if card['progress'] is not None:
        bar_y = top+112
        d.rectangle([x, bar_y, x+520, bar_y+8], fill=BAR_BG)
        d.rectangle([x, bar_y, x+round(520*card['progress']), bar_y+8], fill=BAR)
        if card['to_next'] is not None:
            right(d, x+520, top+80, f"距下一级 {card['to_next']:,}".replace(',', ' '), font('sc', 15, 400), DIM)
    # The Atlas logo and source link (required by the Atlas author) sit where the small numbers used to be.
    mark = logo(48)
    img.paste(mark, (W-M-24-mark.width, top+22), mark)
    label = f'数据来源：{ATLAS_NAME}  '
    url_w = d.textlength(ATLAS_URL, font=font('sc', 20, 400))
    mid(d, W-M-24, top+98, ATLAS_URL, font('sc', 20, 400), ACCENT, 'r')
    mid(d, W-M-24-url_w, top+98, label, font('sc', 20, 600), TEXT, 'r')
    return top+132+20


GRID_ROW = LABEL_H+CELL_H+16


def panel_height(card):
    """The shared height of the equipment panel and the headline panel."""
    return max(330, 16+len(card['equipment'])*GRID_ROW+78)


def equipment(img, d, card, icons, top, height):
    """Faction/prestige column, the equipment grid and the info row."""
    d.rectangle([M, top, M+LEFT_W, top+height], fill=PANEL, outline=RULE)
    side_x = M+16
    d.text((side_x, top+16), '阵营', font=font('sc', 15, 600), fill=DIM)
    d.rectangle([side_x, top+40, side_x+118, top+110], fill=PANEL_2)
    side = str(card['side'])
    d.text((side_x+59-d.textlength(side, font=font('osw', 30, 600))/2, top+52), side, font=font('osw', 30, 600), fill=TEXT)
    d.text((side_x, top+128), '转生', font=font('sc', 15, 600), fill=DIM)
    d.rectangle([side_x, top+152, side_x+118, top+270], fill=PANEL_2)
    if card['prestige']:
        if not paste_center(img, icons.get('prestige'), side_x+9, top+158, 100, 84):
            placeholder(d, side_x+9, top+158, 100, 84)
        text = f"{card['prestige']} 级"
        d.text((side_x+59-d.textlength(text, font=font('sc', 15, 600))/2, top+246), text, font=font('sc', 15, 600), fill=TEXT)
    else:
        d.text((side_x+59-d.textlength('无', font=font('sc', 22, 600))/2, top+196), '无', font=font('sc', 22, 600), fill=FAINT)

    grid_x = M+160
    y = top+16
    for row in card['equipment']:
        x = grid_x
        for key, label, wide, item in row:
            w = CELL_W*wide+CELL_GAP*(wide-1)
            d.rectangle([x, y, x+w, y+LABEL_H], fill=PANEL_2)
            d.line([x, y, x, y+LABEL_H], fill=GOLD, width=3)
            d.text((x+10, y+4), label, font=font('sc', 15, 600), fill=TEXT)
            box_y = y+LABEL_H+2
            d.rectangle([x, box_y, x+w, box_y+CELL_H], fill=(30, 33, 38) if item else (19, 21, 24), outline=RULE)
            if item:
                drawn = paste_center(img, icons.get('items', {}).get(item['image']), x+8, box_y+22, w-16, CELL_H-30)
                if not drawn:
                    d.text((x+w/2-d.textlength('图片暂缺', font=font('sc', 14, 400))/2, box_y+CELL_H/2-6), '图片暂缺',
                           font=font('sc', 14, 400), fill=FAINT)
                name = clip(d, item['name'], font('sc', 14, 500), w-12)
                right(d, x+w-6, box_y+4, name, font('sc', 14, 500), TEXT)
            x += w+CELL_GAP
        y += GRID_ROW
    info = (('账号 ID', card['aid'] or '—'), ('最高段位', card['league']), ('最近活跃', card['last_active'] or '—'),
            ('查询时间', card['looked_up'] or '—'))
    y = top+height-62
    for i, (label, value) in enumerate(info):
        x = grid_x+i*196
        d.text((x, y), label, font=font('sc', 14, 600), fill=DIM)
        d.text((x, y+22), clip(d, value, font('sc', 17, 500), 186), font=font('sc', 17, 500), fill=TEXT)


def headline(d, card, top, height):
    """Mode name and the twelve numbers (three rows of four), spread over the panel's height."""
    width = W-M-RIGHT_X
    d.rectangle([RIGHT_X, top, W-M, top+height], fill=PANEL, outline=RULE)
    d.rectangle([RIGHT_X, top, W-M, top+44], fill=PANEL_2)
    d.line([RIGHT_X, top+44, W-M, top+44], fill=GOLD, width=2)
    label = card['mode_label']
    d.text((RIGHT_X+width/2-d.textlength(label, font=font('sc', 20, 800))/2, top+9), label, font=font('sc', 20, 800), fill=GOLD)
    cell, pitch = width/4, (height-56)/3
    for i, (name, value) in enumerate(card['headline']):
        cx, block = RIGHT_X+cell*(i % 4)+cell/2, top+48+(i//4)*pitch
        centered(d, cx, block+pitch*0.40, value, 'osw', 44, cell-14, TEXT, 500)
        centered(d, cx, block+pitch*0.76, name, 'sc', 22, cell-10, DIM, 500)


RARE_H = 250


def rare(img, d, card, icons, top):
    """A full-width bar: completion line and bar, then the rare achievements in one row (icon, name, share)."""
    d.rectangle([M, top, W-M, top+RARE_H], fill=PANEL, outline=RULE)
    d.text((M+16, top+12), '稀有成就', font=font('sc', 19, 800), fill=ACCENT)
    data = card['achievements']
    total = data['total']
    share = f"（{round(data['count']/total*100)}%）" if total else ''
    line = f"已达成 {total} 个成就中的 {data['count']} 个{share}" if total else f"已达成 {data['count']} 个成就"
    d.text((M+120, top+14), line, font=font('sc', 16, 400), fill=DIM)
    big = f"{data['count']}" + (f'/{total}' if total else '')
    right(d, W-M-16, top+6, big, font('osw', 36, 500), TEXT)
    bar_x0, bar_x1 = M+16, W-M-170
    d.rectangle([bar_x0, top+50, bar_x1, top+60], fill=BAR_BG)
    if total:
        d.rectangle([bar_x0, top+50, bar_x0+round((bar_x1-bar_x0)*min(1, data['count']/total)), top+60], fill=BAR)
    if not data['rare']:
        d.text((M+16, top+100), '暂无成就', font=font('sc', 16, 400), fill=FAINT)
        return top+RARE_H
    cell_w = (W-2*M-32)/max(len(data['rare']), 9)
    for i, entry in enumerate(data['rare']):
        cx = M+16+cell_w*i+cell_w/2
        if not paste_center(img, icons.get('achievements', {}).get(entry['id']), cx-40, top+78, 80, 80):
            placeholder(d, cx-40, top+78, 80, 80)
        # No completion share (maintainer, 2026-11-08: Atlas reports 0 % for every PVE achievement).
        centered(d, cx, top+186, entry['name'], 'sc', 24, cell_w-10, TONES[entry['tone']], 700, minimum=18)
        centered(d, cx, top+222, entry['rarity'], 'sc', 20, cell_w-10, TONES[entry['tone']], 500, minimum=16)
    return top+RARE_H


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


def centered(d, cx, cy, value, face, size, width, color, weight=None, align='m', minimum=10):
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
        f = fitted(d, line, 'sc', 25, legend_width-55, 500)
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
        name_font = fitted(d, str(name), 'sc', 34, 225, 600)
        mid(d, x+14, yy+34, clip(d, name, name_font, 225), name_font, TEXT)
        for k in range(10):
            xx = x+255+k*35
            d.rectangle((xx, yy+21, xx+27, yy+48), outline=DIM,
                        fill=(GOLD if elite else BAR) if level >= (k+1)*5.1 else None)
        mid(d, x+colw-14, yy+34, '精英' if elite else str(level),
            font('sc', 35, 700) if elite else font('mono', 35), GOLD if elite else TEXT, 'r')
    return y+((len(card['skills'])+1)//2)*78+28


def latest(img, d, card, icons, top):
    """The table of the latest achievements: icon, name, description, rarity and time (no player share column,
    maintainer 2026-11-08), at a size readable on phones."""
    entries = card['achievements']['latest']
    y = band(d, top, f'最新获得的 {len(entries)} 个成就' if entries else '最新获得的成就')
    cols = {'name': M+88, 'desc': M+420, 'rarity': W-M-340, 'time': W-M-16}
    d.rectangle([M, y, W-M, y+46], fill=PANEL_2)
    for key, label in (('name', '成就'), ('desc', '成就描述'), ('rarity', '稀有度')):
        mid(d, cols[key], y+23, label, font('sc', 22, 700), ACCENT)
    mid(d, cols['time'], y+23, '达成时间（UTC+8）', font('sc', 22, 700), ACCENT, 'r')
    y += 52
    if not entries:
        mid(d, cols['name'], y+24, '暂无成就', font('sc', 24, 400), FAINT)
        return y+70
    desc_font = font('sc', 22, 400)
    for entry in entries:
        lines = wrap(d, str(entry['description']), desc_font, cols['rarity']-cols['desc']-30)[:2]
        h = max(84, 26+len(lines)*32)
        if not paste_center(img, icons.get('achievements', {}).get(entry['id']), M+12, y+(h-60)/2, 60, 60):
            placeholder(d, M+12, y+(h-60)/2, 60, 60)
        name_font = fitted(d, str(entry['name']), 'sc', 26, cols['desc']-cols['name']-20, 600, 20)
        mid(d, cols['name'], y+h/2, clip(d, entry['name'], name_font, cols['desc']-cols['name']-20), name_font, TEXT)
        for k, text in enumerate(lines):
            mid(d, cols['desc'], y+h/2+(k-(len(lines)-1)/2)*32, text, desc_font, DIM)
        mid(d, cols['rarity'], y+h/2, entry['rarity'], font('sc', 24, 700), TONES[entry['tone']])
        mid(d, cols['time'], y+h/2, when(entry['at'], '%Y-%m-%d %H:%M:%S') or '—' if entry['at'] else '—',
            font('mono', 24), TEXT, 'r')
        y += h
        d.line([M, y, W-M, y], fill=(32, 35, 40))
    return y+24


def footer(d, top):
    d.line([0, top, W, top], fill=RULE)
    f = font('sc', 15, 400)
    lines = (f'数据来源：{ATLAS_NAME} {ATLAS_URL} · 物品与成就图片来自 tarkov.dev',
             '特别致谢：Atlas（EFTForge 项目的一部分，作者 Morph1ne / SouthHorizons76）',
             f"{NAME} v{VERSION} · {REPO.removeprefix('https://')}", NOTICE)
    for i, text in enumerate(lines):
        d.text((W/2-d.textlength(str(text), font=f)/2, top+12+i*26), str(text), font=f, fill=FAINT)
    return top+12+len(lines)*26+16


def render_atlas_image(card, icons=None):
    """JPEG bytes of the player picture. icons: {'level_badge': PNG bytes, 'prestige': PNG bytes,
    'items': {url: PNG bytes}, 'achievements': {achievement id: PNG bytes}}; anything missing draws a placeholder."""
    if card.get('arena'):
        from .atlas_arena_image import render_atlas_arena_image
        return render_atlas_arena_image(card)
    icons = icons or {}
    img = Image.new('RGB', (W, 6000), BG)
    d = ImageDraw.Draw(img)
    y = header(img, d, card, icons)
    height = panel_height(card)
    equipment(img, d, card, icons, y, height)
    headline(d, card, y, height)
    y = rare(img, d, card, icons, y+height+24)+24
    y = statistics(d, card, y)
    y = skills(d, card, y)
    y = latest(img, d, card, icons, y)
    end = footer(d, y)
    return jpeg(img.crop((0, 0, W, end)))
