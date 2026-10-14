"""The profile card as an image: an intel-dossier page with a dog tag and exit-status bars (handoff §14.2).

Drawn locally with Pillow (an AstrBot dependency); nothing is sent to a rendering service. The fonts are bundled in
fonts/ under the SIL Open Font License (see THIRD_PARTY.md). Game references are drawn, no game assets are used.
"""
import io
import math
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import NAME, NOTICE, REPO, VERSION
from .card import EXITS, SURVIVED, refresh_note, stale_note

FONTS = Path(__file__).with_name('fonts')
FONT_FILES = {'sc': 'NotoSansSC-Subset.ttf', 'osw': 'Oswald-Variable.ttf', 'mono': 'ShareTechMono-Regular.ttf'}
# Characters of the subset CJK font, written by tools/build_fonts.py next to the font.
CHARSET_FILE = 'NotoSansSC-Subset.chars.txt'
ASCII = frozenset(chr(code) for code in range(32, 127))

W = 820
PAPER, INK, FADED, RED, LINE = (204, 192, 160), (44, 40, 34), (110, 100, 82), (156, 44, 36), (150, 138, 110)
TAG_METAL, TAG_EDGE, TAG_TEXT = (156, 156, 148), (186, 186, 178), (50, 52, 50)
EXIT_COLORS = {'survived': (86, 122, 64), 'killed': (150, 46, 38), 'runner': (184, 134, 44),
               'transit': (62, 96, 128), 'mia': (112, 104, 88)}
EXIT_LABELS = (('survived', SURVIVED),)+EXITS
MODE_EN = {'PVP': 'PVP', 'PVE': 'PVE', 'PVP 赛季': 'PVP SEASON'}
JPEG_QUALITY = 85


@lru_cache(maxsize=None)
def font(name, size, weight=None):
    f = ImageFont.truetype(str(FONTS/FONT_FILES[name]), size)
    if weight is not None:
        f.set_variation_by_axes([weight])
    return f


@lru_cache(maxsize=1)
def charset():
    return frozenset((FONTS/CHARSET_FILE).read_text(encoding='utf-8')) | ASCII


def safe(text, latin=False):
    """`text` with every character the bundled fonts cannot draw replaced by '?'. latin: the Latin-only fonts."""
    allowed = ASCII if latin else charset()
    return ''.join(char if char in allowed else '?' for char in str(text))


def fit(d, text, name, size, width, weight=None, smallest=10):
    """The largest font of `name` up to `size` in which `text` fits in `width`."""
    while size > smallest and d.textlength(text, font=font(name, size, weight)) > width:
        size -= 1
    return font(name, size, weight)


def wrap(d, text, f, width):
    lines, line = [], ''
    for char in text:
        if d.textlength(line+char, font=f) > width and line:
            lines.append(line)
            line = ''
        line += char
    return lines+[line] if line else lines


def right(d, x, y, text, f, fill):
    d.text((x-d.textlength(text, font=f), y), text, font=f, fill=fill)


@lru_cache(maxsize=1)
def grain():
    """A fixed paper-grain tile (deterministic, so the same profile always gives the same picture)."""
    rnd = random.Random(7)
    tile = Image.new('L', (256, 256))
    tile.putdata([max(0, min(255, int(rnd.gauss(128, 6)))) for _ in range(256*256)])
    return tile


def paper(height):
    layer = Image.new('L', (W, height))
    for x in range(0, W, 256):
        for y in range(0, height, 256):
            layer.paste(grain(), (x, y))
    img = ImageChops.add(Image.new('RGB', (W, height), PAPER), Image.merge('RGB', (layer,)*3), 1, -128)
    edge = Image.new('L', (W, height), 0)
    ed = ImageDraw.Draw(edge)
    for i in range(40):
        ed.rectangle([i, i, W-1-i, height-1-i], outline=int(90*(1-i/40)))
    img.paste((120, 104, 76), (0, 0), edge.filter(ImageFilter.GaussianBlur(8)))
    return img


def stamp(img, side):
    mark = Image.new('RGBA', (300, 120), (0, 0, 0, 0))
    md = ImageDraw.Draw(mark)
    md.rectangle([4, 4, 295, 115], outline=RED+(210,), width=5)
    md.rectangle([12, 12, 287, 107], outline=RED+(170,), width=2)
    # "Snapshot", not "verified": the data is whatever tarkov.dev stored last (review R-13).
    md.text((30, 22), '存档快照', font=font('sc', 40, 900), fill=RED+(210,))
    md.text((32, 74), 'SNAPSHOT · '+safe(side, latin=True)[:12], font=font('mono', 20), fill=RED+(210,))
    mark = mark.rotate(-12, expand=True, resample=Image.BICUBIC)
    img.paste(mark, (W-345, 18), mark)


def dog_tag(img, card, mode_en, x, y, angle=-4):
    """A drawn military tag with the player's identity, a hole showing the paper and a ball chain up to a clip."""
    tw, th = 330, 196
    tag = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
    td = ImageDraw.Draw(tag)
    td.rounded_rectangle([0, 0, tw-1, th-1], 36, fill=TAG_METAL)
    td.rounded_rectangle([6, 6, tw-7, th-7], 31, outline=TAG_EDGE, width=2)
    td.ellipse([20, 20, 42, 42], fill=(0, 0, 0, 0))
    td.ellipse([20, 20, 42, 42], outline=(110, 110, 104), width=2)
    name = safe(card['nickname'], latin=True)
    td.text((60, 20), name, font=fit(td, name, 'osw', 36, tw-80, 700), fill=TAG_TEXT)
    level = card['level'] if card['level'] is not None else '?'
    prestige = f"  ·  P{card['prestige']}" if card['prestige'] else ''
    line = f"{safe(card['side'], latin=True)}  ·  LVL {level}{prestige}"
    td.text((60, 68), line, font=fit(td, line, 'osw', 26, tw-80, 500), fill=TAG_TEXT)
    ident = f"ID {card['aid']}" if card['aid'] is not None else 'ID —'
    td.text((60, 106), ident, font=fit(td, ident, 'osw', 24, tw-80, 400), fill=TAG_TEXT)
    # "{n} 成就数" (maintainer); the Chinese word needs the CJK font.
    head = f"{mode_en}  ·  {card['achievements']} "
    td.text((60, 142), head, font=font('osw', 19, 400), fill=(84, 86, 82))
    td.text((60+td.textlength(head, font=font('osw', 19, 400)), 144), '成就数', font=font('sc', 17, 500), fill=(84, 86, 82))
    tag = tag.filter(ImageFilter.SMOOTH)
    turned = tag.rotate(angle, expand=True, resample=Image.BICUBIC)
    shadow = Image.new('RGBA', turned.size, (0, 0, 0, 0))
    shadow.paste((70, 56, 34, 150), (0, 0), turned.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(7))
    img.paste(shadow, (x+6, y+9), shadow)
    img.paste(turned, (x, y), turned)
    # Where the hole is after turning about the centre.
    a = math.radians(-angle)
    hx0, hy0 = 31-tw/2, 31-th/2
    hx = x+turned.width/2+hx0*math.cos(a)-hy0*math.sin(a)
    hy = y+turned.height/2+hx0*math.sin(a)+hy0*math.cos(a)
    d = ImageDraw.Draw(img)
    clip_x, clip_y = hx+26, y-38
    d.rounded_rectangle([clip_x-10, clip_y-14, clip_x+12, clip_y+44], 10, outline=(122, 122, 118), width=4)
    d.rounded_rectangle([clip_x-4, clip_y-6, clip_x+6, clip_y+32], 6, outline=(140, 140, 136), width=3)
    for i in range(15):
        t = i/14
        bx = hx+(clip_x-hx)*t+6*math.sin(t*math.pi)
        by = hy+(clip_y+30-hy)*t
        d.ellipse([bx-3.2, by-3.2, bx+3.2, by+3.2], fill=(140, 140, 132), outline=(76, 74, 68))


def exit_bars(d, sides, x, y, width):
    lx, bw = x+70, width-70
    for k in range(5):
        tx = lx+bw*k/4
        d.line([tx, y, tx, y+6], fill=INK)
        t = f'{k*25}%'
        d.text((tx-d.textlength(t, font=font('mono', 14))/2, y-18), t, font=font('mono', 14), fill=FADED)
    y += 12
    for card_side in sides:
        stats = card_side['stats']
        d.text((x, y+2), card_side['title'].upper(), font=font('mono', 20), fill=INK)
        total = sum(stats[key] for key, _ in EXIT_LABELS)
        if total > 0:
            cx = lx
            for key, _ in EXIT_LABELS:
                # Counters are never negative (profile.counter), so w <= bw; clamped again because the hatch loop
                # below runs w/9 times.
                w = min(bw, max(0, bw*stats[key]/total))
                if w >= 1:
                    d.rectangle([cx, y, cx+w, y+24], fill=EXIT_COLORS[key])
                    shade = tuple(max(0, c-22) for c in EXIT_COLORS[key])
                    for hx in range(int(cx)+2, int(cx+w)-8, 9):
                        d.line([hx, y+24, hx+8, y], fill=shade)
                cx += w
        else:
            d.text((lx+12, y+2), '暂无记录', font=font('sc', 15, 400), fill=FADED)
        d.rectangle([lx, y, lx+bw, y+24], outline=INK, width=2)
        y += 36
    legend_x = lx
    d.text((x, y), '图例', font=font('sc', 15, 600), fill=FADED)
    for key, label in EXIT_LABELS:
        t = f"{label} {sides[0]['stats'][key]}/{sides[1]['stats'][key]}"
        f = font('sc', 15, 400)
        if legend_x+18+d.textlength(t, font=f) > x+width:
            legend_x, y = lx, y+24
        d.rectangle([legend_x, y+5, legend_x+12, y+17], fill=EXIT_COLORS[key], outline=INK)
        d.text((legend_x+18, y), t, font=f, fill=INK)
        legend_x += 18+d.textlength(t, font=f)+20
    return y+28


def render_image(card):
    """JPEG bytes of the card."""
    skill_rows = (len(card['skills'])+1)//2
    notes = (([stale_note(card['stale_days'])] if card['stale_days'] else [])+card['notes']
             +([refresh_note(card['refresh_url'])] if card['refresh_url'] else []))
    height = 1340+skill_rows*34+sum(26*(1+len(note)//34) for note in notes)
    img = paper(height)
    d = ImageDraw.Draw(img)
    mode_en = MODE_EN.get(card['mode'], safe(card['mode'], latin=True))

    # header
    d.rectangle([40, 30, 300, 70], fill=(186, 172, 136))
    d.text((56, 38), f"FILE NO. {card['aid'] if card['aid'] is not None else '—'}", font=font('mono', 22), fill=INK)
    d.text((40, 90), '塔科夫市 · 绝密情报档案', font=font('sc', 34, 800), fill=INK)
    d.text((42, 138), 'TARKOV INTELLIGENCE DOSSIER  /  MODE: '+mode_en, font=font('mono', 18), fill=FADED)
    d.line([40, 170, W-40, 170], fill=INK, width=2)
    d.line([40, 175, W-40, 175], fill=INK, width=1)
    stamp(img, card['side'])

    # identity: dog tag on the left, the facts the tag does not show on the right
    dog_tag(img, card, mode_en, 34, 232)
    d = ImageDraw.Draw(img)
    fields = [('模式', card['mode']), ('账号总游戏时长', f"{card['play_hours']} 小时" if card['play_hours'] else '—'),
              ('成就', f"{card['achievements']} 个"), ('最近活跃', card['last_active'] or '—'), ('档案更新', card['updated'] or '—')]
    for i, (key, value) in enumerate(fields):
        y = 214+i*42
        d.text((410, y), key, font=font('sc', 16, 600), fill=FADED)
        value = safe(value)
        d.text((410, y+18), value, font=font('mono', 20) if value.isascii() else font('sc', 18, 500), fill=INK)
        d.line([410, y+40, W-40, y+40], fill=LINE)

    number = iter('一二三四')
    y = 460
    d.text((40, y), f'{next(number)}、作战记录', font=font('sc', 22, 800), fill=INK)
    y += 40
    head = ['', '战局', SURVIVED, '存活率', '击杀', '死亡', 'K/D', '击杀 PMC', 'PMC K/D']
    xs = [40, 135, 210, 285, 385, 470, 540, 615, 705, W-40]
    for x, h in zip(xs, head):
        d.text((x, y), h, font=fit(d, h, 'sc', 16, xs[xs.index(x)+1]-x-6, 700), fill=INK)
    y += 30
    d.line([40, y, W-40, y], fill=INK)
    for card_side in card['sides']:
        y += 8
        stats = card_side['stats']
        d.text((xs[0], y), card_side['title'].upper(), font=font('mono', 22), fill=INK)
        if card_side['empty']:
            d.text((xs[1], y+2), '暂无记录', font=font('sc', 18, 400), fill=FADED)
        else:
            values = [stats['raids'], stats['survived'], card_side['rate'], stats['kills'], stats['deaths'],
                      card_side['kd'] or '—', stats['pmc_kills'], card_side['pmc_kd'] or '—']
            for i, value in enumerate(values, 1):
                text = str(value)
                d.text((xs[i], y), text, font=fit(d, text, 'mono', 22, xs[i+1]-xs[i]-8), fill=INK)
        y += 34
        d.line([40, y, W-40, y], fill=LINE)
    y += 22
    pmc, scav = (card_side['stats'] for card_side in card['sides'])
    d.text((40, y), '撤离状态', font=font('sc', 18, 700), fill=INK)
    d.text((124, y+3), f"图例数字为 PMC/Scav · 最长连续存活：PMC {pmc['streak']} 局，Scav {scav['streak']} 局",
           font=font('sc', 15, 400), fill=FADED)
    y = exit_bars(d, card['sides'], 40, y+52, W-80)+26

    if card['skills']:
        d.text((40, y), f'{next(number)}、技能评估', font=font('sc', 22, 800), fill=INK)
        y += 44
        colw = (W-80-40)/2
        for i, (name, level, elite) in enumerate(card['skills']):
            x0, yy = 40+(i % 2)*(colw+40), y+(i//2)*34
            d.text((x0, yy), safe(name), font=font('sc', 17, 500), fill=INK)
            for k in range(10):
                on = level >= (k+1)*5.1
                d.rectangle([x0+130+k*18, yy+6, x0+142+k*18, yy+18], outline=INK, width=1,
                            fill=(RED if elite else INK) if on else None)
            right(d, x0+colw, yy, '精英' if elite else f'{level:>2}', font('sc', 17, 800) if elite else font('mono', 20),
                  RED if elite else INK)
        y += skill_rows*34+20

    d.text((40, y), f'{next(number)}、备注', font=font('sc', 22, 800), fill=INK)
    y += 40
    d.text((40, y), '下次撤离点：', font=font('sc', 17, 400), fill=INK)
    d.rectangle([146, y+2, 330, y+24], fill=(30, 28, 24))
    d.text((340, y), '（权限不足）', font=font('sc', 17, 400), fill=FADED)
    y += 34
    for note in notes:
        for line in wrap(d, safe(note), font('sc', 16, 400), W-80):
            d.text((40, y), line, font=font('sc', 16, 400), fill=INK)
            y += 26
    y += 16
    d.line([40, y, W-40, y], fill=INK)
    return jpeg(img.crop((0, 0, W, footer(d, y, card['updated'], FADED, FADED))))


def footer(d, y, updated, fill, small_fill, line=None):
    """The data-source line (`line` instead, when given) and the plugin's small print, from `y`; the y where the
    picture ends."""
    if line is None:
        updated = f'档案更新于 GMT+8 {updated}' if updated else '时间为 GMT+8'
        line = f'数据来自 tarkov.dev 公开快照（非实时） · {updated}'
    d.text((W/2-d.textlength(line, font=font('sc', 14, 400))/2, y+10), line, font=font('sc', 14, 400), fill=fill)
    # Plugin name, repository and the open-source statement, centred like the line above and a little larger
    # (maintainer).
    small = font('sc', 15, 400)
    for i, text in enumerate((f"{NAME} v{VERSION} · {REPO.removeprefix('https://')}", NOTICE)):
        d.text((W/2-d.textlength(text, font=small)/2, y+38+i*24), text, font=small, fill=small_fill)
    return y+96


def jpeg(img):
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=JPEG_QUALITY, subsampling=0, optimize=True)
    return out.getvalue()
