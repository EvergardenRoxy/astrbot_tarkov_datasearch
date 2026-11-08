"""The Atlas player picture (PVP, PVE, season): a dark board after the Atlas website's player page.

Sections, top to bottom: the Atlas logo and link (required by the Atlas author on every output of its data), the
player header (level badge, level, name, experience), equipment with tarkov.dev's item pictures beside the
mode's headline numbers and the rare achievements, the statistics columns, skills (drawn like the old dossier card),
the ten latest achievements, and the footer with the thanks to Atlas. Drawn locally with Pillow from atlas_card.build()
and the pictures atlas_assets.py has on disk; a missing picture draws an empty placeholder box, never a substitute.
The old tarkov.dev cards (image.py, arena_image.py) are separate and unchanged.
"""
import io
import threading
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from . import NAME, NOTICE, REPO, VERSION
from .atlas_card import when
from .image import FONTS, FONT_FILES, jpeg, safe as subset_safe, wrap
from .matching_image import ATLAS_NAME, ATLAS_URL, logo, right

# The full Noto Sans SC file once font_download.py has it on disk; None draws with the bundled subset.
FULL_FONT = None
FONT_LOCK = threading.Lock()

W = 1600
M = 32
BG, PANEL, PANEL_2, BAND, RULE = (13, 14, 16), (22, 24, 28), (28, 31, 36), (16, 30, 46), (44, 48, 56)
TEXT, DIM, FAINT = (228, 230, 232), (150, 155, 162), (98, 102, 110)
ACCENT, BAR, BAR_BG, GOLD = (96, 178, 240), (66, 120, 168), (34, 38, 44), (200, 164, 72)
TONES = {'common': (214, 216, 220), 'rare': (186, 140, 246), 'legendary': (240, 198, 64), 'seasonal': (92, 172, 244)}
LEFT_W, RIGHT_X = 960, M+960+24
CELL_W, CELL_H, CELL_GAP, LABEL_H = 186, 150, 12, 26


def use_full_font(path):
    """Draw Chinese text with the full font at `path` (None: back to the bundled subset)."""
    global FULL_FONT
    with FONT_LOCK:
        if path != FULL_FONT:
            FULL_FONT = path
            font.cache_clear()


@lru_cache(maxsize=None)
def font(name, size, weight=None):
    """The bundled fonts with basic layout (as the matching picture); 'sc' is the full font when available."""
    path = FULL_FONT if name == 'sc' and FULL_FONT else FONTS/FONT_FILES[name]
    f = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)
    if weight is not None:
        f.set_variation_by_axes([weight])
    return f


def safe(text, latin=False):
    """Text as drawn: unchanged with the full font, else characters outside the subset become '?'."""
    return str(text) if FULL_FONT and not latin else subset_safe(text, latin)


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


def band(d, y, title):
    """A section title bar across the board; the y below it."""
    d.rectangle([M, y, W-M, y+44], fill=BAND, outline=(30, 52, 76))
    d.text((M+20, y+10), title, font=font('sc', 20, 800), fill=ACCENT)
    return y+60


def fitted(d, text, name, size, width, weight=None):
    while size > 10 and d.textlength(text, font=font(name, size, weight)) > width:
        size -= 1
    return font(name, size, weight)


def clip(d, text, f, width):
    text = safe(text)
    if d.textlength(text, font=f) <= width:
        return text
    while text and d.textlength(text+'…', font=f) > width:
        text = text[:-1]
    return text+'…'


def header(img, d, card, icons):
    """Logo row and the player header; the y below."""
    mark = logo(48)
    img.paste(mark, (M, 22), mark)
    x = M+mark.width+20
    label = f'数据来源：{ATLAS_NAME}  '
    d.text((x, 30), label, font=font('sc', 18, 600), fill=TEXT)
    d.text((x+d.textlength(label, font=font('sc', 18, 600)), 30), ATLAS_URL, font=font('sc', 18, 400), fill=ACCENT)
    right(d, W-M, 20, f"{card['mode_label']} 玩家档案", font('sc', 30, 800), TEXT)
    stamp = f"查询于 {card['looked_up'] or '—'}（UTC+8）" + ('，Atlas 缓存' if card['cached'] else '')
    right(d, W-M, 62, stamp, font('sc', 16, 400), DIM)
    d.line([0, 96, W, 96], fill=RULE)

    top = 112
    d.rectangle([M, top, W-M, top+132], fill=PANEL, outline=RULE)
    badge_drawn = paste_center(img, icons.get('level_badge'), M+20, top+22, 88, 88)
    if not badge_drawn:
        placeholder(d, M+20, top+22, 88, 88)
    d.text((M+124, top+20), str(card['level'] if card['level'] is not None else '—'), font=font('osw', 72, 500), fill=TEXT)
    x = M+320
    d.text((x, top+20), safe(card['nickname']), font=fitted(d, safe(card['nickname']), 'sc', 38, 520, 800), fill=ACCENT)
    d.text((x, top+76), f"{card['side']}", font=font('osw', 22, 500), fill=DIM)
    exp = f"EXP {card['experience']:,}".replace(',', ' ') if card['experience'] is not None else 'EXP —'
    d.text((x+90, top+78), exp, font=font('mono', 20), fill=TEXT)
    if card['progress'] is not None:
        bar_y = top+112
        d.rectangle([x, bar_y, x+520, bar_y+8], fill=BAR_BG)
        d.rectangle([x, bar_y, x+round(520*card['progress']), bar_y+8], fill=BAR)
        if card['to_next'] is not None:
            right(d, x+520, top+80, f"距下一级 {card['to_next']:,}".replace(',', ' '), font('sc', 15, 400), DIM)
    # Small numbers on the right, as on the Atlas page.
    picks = {label: value for label, value in card['headline']}
    small = (('战局数', picks['战局数']), ('幸存率', picks['幸存率']), ('击杀数', picks['击杀数']),
             ('K/D', picks['K/D']), ('擅离率', picks['擅离率']), ('在线', picks['在线']))
    for i, (label, value) in enumerate(small):
        cx, cy = W-M-430+(i % 3)*145, top+26+(i//3)*48
        d.text((cx, cy), label+'：', font=font('sc', 16, 400), fill=DIM)
        d.text((cx+d.textlength(label+'：', font=font('sc', 16, 400)), cy-2), value, font=font('mono', 19), fill=TEXT)
    return top+132+20


def equipment(img, d, card, icons, top):
    """Faction/prestige column, the equipment grid and the info row; the y below."""
    d.rectangle([M, top, M+LEFT_W, top+4*(LABEL_H+CELL_H+16)+120], fill=PANEL, outline=RULE)
    side_x = M+16
    d.text((side_x, top+16), '阵营', font=font('sc', 15, 600), fill=DIM)
    d.rectangle([side_x, top+40, side_x+118, top+110], fill=PANEL_2)
    side = safe(card['side'], latin=True)
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
        y += LABEL_H+CELL_H+16
    info = (('账号 ID', card['aid'] or '—'), ('最高段位', card['league']), ('最近活跃', card['last_active'] or '—'),
            ('查询时间', card['looked_up'] or '—'))
    y += 12
    for i, (label, value) in enumerate(info):
        x = grid_x+i*196
        d.text((x, y), label, font=font('sc', 14, 600), fill=DIM)
        d.text((x, y+22), clip(d, value, font('sc', 17, 500), 186), font=font('sc', 17, 500), fill=TEXT)
    return top+4*(LABEL_H+CELL_H+16)+120


def headline(d, card, top):
    """Mode name and the twelve numbers (three rows of four); the y below."""
    width = W-M-RIGHT_X
    d.rectangle([RIGHT_X, top, W-M, top+330], fill=PANEL, outline=RULE)
    d.rectangle([RIGHT_X, top, W-M, top+44], fill=PANEL_2)
    d.line([RIGHT_X, top+44, W-M, top+44], fill=GOLD, width=2)
    label = card['mode_label']
    d.text((RIGHT_X+width/2-d.textlength(label, font=font('sc', 20, 800))/2, top+9), label, font=font('sc', 20, 800), fill=GOLD)
    cell = width/4
    for i, (name, value) in enumerate(card['headline']):
        cx, cy = RIGHT_X+cell*(i % 4)+cell/2, top+64+(i//4)*88
        f = fitted(d, value, 'osw', 30, cell-12, 500)
        d.text((cx-d.textlength(value, font=f)/2, cy), value, font=f, fill=TEXT)
        d.text((cx-d.textlength(name, font=font('sc', 14, 400))/2, cy+44), name, font=font('sc', 14, 400), fill=DIM)
    return top+330


def rare(img, d, card, icons, top, bottom):
    """Completion bar and the rarest achievements with name and global share."""
    width = W-M-RIGHT_X
    d.rectangle([RIGHT_X, top, W-M, bottom], fill=PANEL, outline=RULE)
    d.text((RIGHT_X+16, top+12), '稀有成就', font=font('sc', 19, 800), fill=ACCENT)
    data = card['achievements']
    total = data['total']
    share = f"（{round(data['count']/total*100)}%）" if total else ''
    line = f"已达成 {total} 个成就中的 {data['count']} 个{share}" if total else f"已达成 {data['count']} 个成就"
    d.text((RIGHT_X+16, top+48), line, font=font('sc', 16, 400), fill=DIM)
    big = f"{data['count']}" + (f'/{total}' if total else '')
    right(d, W-M-16, top+36, big, font('osw', 36, 500), TEXT)
    bar_w = width-32-150
    d.rectangle([RIGHT_X+16, top+80, RIGHT_X+16+bar_w, top+90], fill=BAR_BG)
    if total:
        d.rectangle([RIGHT_X+16, top+80, RIGHT_X+16+round(bar_w*min(1, data['count']/total)), top+90], fill=BAR)
    cell_w, cell_h = (width-32)/3, (bottom-top-112)/3
    if not data['rare']:
        d.text((RIGHT_X+16, top+120), '暂无成就', font=font('sc', 16, 400), fill=FAINT)
    for i, entry in enumerate(data['rare']):
        cx, cy = RIGHT_X+16+cell_w*(i % 3), top+108+cell_h*(i//3)
        if not paste_center(img, icons.get('achievements', {}).get(entry['id']), cx+cell_w/2-32, cy+4, 64, 64):
            placeholder(d, cx+cell_w/2-32, cy+4, 64, 64)
        name = clip(d, entry['name'], font('sc', 15, 600), cell_w-8)
        d.text((cx+cell_w/2-d.textlength(name, font=font('sc', 15, 600))/2, cy+74), name, font=font('sc', 15, 600),
               fill=TONES[entry['tone']])
        detail = f"{entry['percent']:g}% 玩家" if entry['percent'] is not None else '—'
        d.text((cx+cell_w/2-d.textlength(detail, font=font('sc', 13, 400))/2, cy+96), detail, font=font('sc', 13, 400), fill=DIM)


def statistics(d, card, top):
    y = band(d, top, '统计数据')
    gap = 16
    col_w = (W-2*M-2*gap)/3
    rows = max(len(rows) for _, rows in card['columns'])
    for i, (title, entries) in enumerate(card['columns']):
        x = M+i*(col_w+gap)
        d.rectangle([x, y, x+col_w, y+44+rows*30+12], fill=PANEL, outline=RULE)
        d.rectangle([x, y, x+col_w, y+36], fill=PANEL_2)
        d.text((x+16, y+8), title, font=font('sc', 16, 800), fill=ACCENT)
        for k, (label, value) in enumerate(entries):
            ry = y+48+k*30
            d.text((x+16, ry), label, font=font('sc', 16, 400), fill=DIM)
            right(d, x+col_w-16, ry-1, value, font('mono', 18), TEXT)
            d.line([x+12, ry+26, x+col_w-12, ry+26], fill=(32, 35, 40))
    return y+44+rows*30+12+24


def skills(d, card, top):
    """Two columns of name, ten squares and the level, as on the old dossier card (elite in gold)."""
    if not card['skills']:
        return top
    y = band(d, top, '技能')
    colw = (W-2*M-24-60)/2
    for i, (name, level, elite) in enumerate(card['skills']):
        x0, yy = M+12+(i % 2)*(colw+60), y+(i//2)*38
        d.text((x0, yy), safe(name), font=font('sc', 19, 500), fill=TEXT)
        for k in range(10):
            on = level >= (k+1)*5.1
            d.rectangle([x0+170+k*24, yy+6, x0+186+k*24, yy+22], outline=DIM, width=1,
                        fill=(GOLD if elite else BAR) if on else None)
        right(d, x0+colw, yy, '精英' if elite else str(level), font('sc', 19, 800) if elite else font('mono', 21),
              GOLD if elite else TEXT)
    return y+((len(card['skills'])+1)//2)*38+20


def latest(img, d, card, icons, top):
    entries = card['achievements']['latest']
    y = band(d, top, f'最新获得的 {len(entries)} 个成就' if entries else '最新获得的成就')
    cols = {'name': M+76, 'desc': M+380, 'rarity': W-M-380, 'percent': W-M-250, 'time': W-M-16}
    d.rectangle([M, y, W-M, y+34], fill=PANEL_2)
    for key, label in (('name', '成就'), ('desc', '成就描述'), ('rarity', '稀有度')):
        d.text((cols[key], y+7), label, font=font('sc', 15, 700), fill=ACCENT)
    right(d, cols['percent'], y+7, '% 玩家', font('sc', 15, 700), ACCENT)
    right(d, cols['time'], y+7, '达成时间（UTC+8）', font('sc', 15, 700), ACCENT)
    y += 40
    if not entries:
        d.text((cols['name'], y+10), '暂无成就', font=font('sc', 16, 400), fill=FAINT)
        return y+60
    for entry in entries:
        lines = wrap(d, safe(entry['description']), font('sc', 15, 400), cols['rarity']-cols['desc']-24)[:2]
        h = max(64, 18+len(lines)*24)
        if not paste_center(img, icons.get('achievements', {}).get(entry['id']), M+12, y+(h-48)/2, 48, 48):
            placeholder(d, M+12, y+(h-48)/2, 48, 48)
        d.text((cols['name'], y+h/2-12), clip(d, entry['name'], font('sc', 18, 600), cols['desc']-cols['name']-16),
               font=font('sc', 18, 600), fill=TEXT)
        for k, text in enumerate(lines):
            d.text((cols['desc'], y+(h-len(lines)*24)/2+k*24), text, font=font('sc', 15, 400), fill=DIM)
        d.text((cols['rarity'], y+h/2-12), entry['rarity'], font=font('sc', 17, 700), fill=TONES[entry['tone']])
        right(d, cols['percent'], y+h/2-12, f"{entry['percent']:g}%" if entry['percent'] is not None else '—',
              font('mono', 18), TEXT)
        right(d, cols['time'], y+h/2-12, when(entry['at'], '%Y-%m-%d %H:%M:%S') or '—' if entry['at'] else '—',
              font('mono', 18), TEXT)
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
        d.text((W/2-d.textlength(safe(text), font=f)/2, top+12+i*26), safe(text), font=f, fill=FAINT)
    return top+12+len(lines)*26+16


def render_atlas_image(card, icons=None):
    """JPEG bytes of the player picture. icons: {'level_badge': PNG bytes, 'prestige': PNG bytes,
    'items': {url: PNG bytes}, 'achievements': {achievement id: PNG bytes}}; anything missing draws a placeholder."""
    icons = icons or {}
    img = Image.new('RGB', (W, 6000), BG)
    d = ImageDraw.Draw(img)
    y = header(img, d, card, icons)
    left_bottom = equipment(img, d, card, icons, y)
    right_top = headline(d, card, y)
    rare(img, d, card, icons, right_top+16, left_bottom)
    y = statistics(d, card, left_bottom+24)
    y = skills(d, card, y)
    y = latest(img, d, card, icons, y)
    end = footer(d, y)
    return jpeg(img.crop((0, 0, W, end)))
