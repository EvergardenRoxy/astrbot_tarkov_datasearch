"""The Arena card as an image: the Arena Masters' fighter registry (maintainer's choice of three, handoff §19.5-19.6).

The setting: Arena fights are organised by the Arena Masters, led by the Host (the trader Ref in the main game); ARP
is the Arena reputation rating, GP coins the Arena currency. Drawn with Pillow and the bundled fonts like image.py;
every element is drawn, no game asset is used.
"""
import math
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from .arena import best_arp, kd, losses, rank, record, win_rate
from .card import refresh_note, stale_note
from .image import W, fit, font, footer, jpeg, right, safe, wrap

LEATHER, PAGE, INK, BROWN, GOLD, GOLD_DARK, WAX = (52, 34, 26), (226, 214, 186), (40, 30, 24), (120, 90, 50), (190, 156, 76), (150, 118, 50), (128, 26, 30)
RULE = (150, 120, 80)
# Table columns: (title, x).
COLUMNS = (('赛事 EVENT', 40), ('出场', 240), ('胜 – 负', 300), ('胜率', 425), ('击杀', 500), ('K/D', 570), ('MVP', 630))


@lru_cache(maxsize=4)
def grain(seed, sigma):
    rnd = random.Random(seed)
    tile = Image.new('L', (256, 256))
    tile.putdata([max(0, min(255, int(rnd.gauss(128, sigma)))) for _ in range(256*256)])
    return tile


def texture(color, seed, sigma, width, height):
    """A deterministic grainy surface (the same card always gives the same picture)."""
    layer = Image.new('L', (width, height))
    for x in range(0, width, 256):
        for y in range(0, height, 256):
            layer.paste(grain(seed, sigma), (x, y))
    return ImageChops.add(Image.new('RGB', (width, height), color), Image.merge('RGB', (layer,)*3), 1, -128)


def centered(d, cx, y, text, f, fill):
    d.text((cx-d.textlength(text, font=f)/2, y), text, font=f, fill=fill)


def coin(d, cx, cy, r, value):
    """A milled medallion like a GP coin, carrying the best ARP and its rank; black and gold for the leaderboard
    ranks, which show no ARP (maintainer)."""
    found = rank(value)
    if found and found[0] == 'TOP':
        for i in range(72):
            a = 2*math.pi*i/72
            d.line([cx+r*math.cos(a), cy+r*math.sin(a), cx+(r-7)*math.cos(a), cy+(r-7)*math.sin(a)], fill=GOLD, width=3)
        d.ellipse([cx-r+6, cy-r+6, cx+r-6, cy+r-6], fill=INK, outline=GOLD, width=3)
        d.ellipse([cx-r+16, cy-r+16, cx+r-16, cy+r-16], outline=GOLD_DARK, width=2)
        centered(d, cx, cy-r*0.56, 'BEST ARP', font('osw', 16, 600), GOLD)
        centered(d, cx, cy-r*0.26, '竞技场', font('sc', 22, 800), GOLD)
        centered(d, cx, cy+r*0.06, '前100名玩家', font('sc', 20, 800), GOLD)
        return
    for i in range(72):
        a = 2*math.pi*i/72
        d.line([cx+r*math.cos(a), cy+r*math.sin(a), cx+(r-7)*math.cos(a), cy+(r-7)*math.sin(a)], fill=GOLD_DARK, width=3)
    d.ellipse([cx-r+6, cy-r+6, cx+r-6, cy+r-6], fill=GOLD, outline=GOLD_DARK, width=3)
    d.ellipse([cx-r+16, cy-r+16, cx+r-16, cy+r-16], outline=(222, 196, 120), width=2)
    centered(d, cx, cy-r*0.56, 'BEST ARP', font('osw', 16, 600), (90, 64, 20))
    text = str(value) if value is not None else '—'
    centered(d, cx, cy-r*0.34, text, fit(d, text, 'osw', int(r*0.46), r*1.3, 700), (70, 48, 14))
    name = found[1] if found else 'REPUTATION'
    centered(d, cx, cy+r*0.28, name, fit(d, name, 'osw', 18, r*1.3, 600), (90, 64, 20))


def wax_seal(d, cx, cy, r):
    rnd = random.Random(11)
    points = [(cx+(r+rnd.randint(-5, 6))*math.cos(2*math.pi*i/28), cy+(r+rnd.randint(-5, 6))*math.sin(2*math.pi*i/28))
              for i in range(28)]
    d.polygon(points, fill=WAX)
    d.ellipse([cx-r*0.72, cy-r*0.72, cx+r*0.72, cy+r*0.72], outline=(170, 60, 60), width=3)
    centered(d, cx, cy-14, 'HOST', font('osw', 24, 700), (230, 190, 180))


def notes_of(card):
    return (([stale_note(card['stale_days'])] if card['stale_days'] else [])+card['notes']
            + ([refresh_note(card['refresh_url'])] if card['refresh_url'] else []))


def page_height(card):
    """Enough room for everything; the picture is cropped to what was drawn."""
    rows = sum(150+38*len(section['modes']) for section in card['sections'])
    return 520+rows+sum(26*(2+len(note)//30) for note in notes_of(card))


def render_arena_image(card):
    """JPEG bytes of the Arena card."""
    pw = W-80
    page = texture(PAGE, 23, 6, pw, page_height(card))
    d = ImageDraw.Draw(page)
    d.text((40, 30), 'ARENA MASTERS', font=font('osw', 40, 700), fill=INK)
    d.text((40, 82), '竞技场主办方 · 选手名册   REGISTRY OF FIGHTERS', font=font('sc', 17, 600), fill=BROWN)
    d.line([40, 116, pw-40, 116], fill=INK, width=2)
    d.line([40, 121, pw-40, 121], fill=INK, width=1)
    rows = [('选手 FIGHTER', card['nickname']), ('登记号 ENTRY', str(card['aid']) if card['aid'] is not None else '—'),
            ('成就 HONOURS', f"{card['achievements']} 个"), ('最近出场 LAST BOUT', card['last_active'] or '—')]
    y = 140
    for key, value in rows:
        d.text((40, y), key, font=font('sc', 14, 600), fill=BROWN)
        value = safe(value)
        f = fit(d, value, 'osw', 26, 300, 600) if value.isascii() else fit(d, value, 'sc', 20, 300, 600)
        d.text((210, y-4), value, font=f, fill=INK)
        d.line([210, y+28, 520, y+28], fill=RULE, width=1)
        y += 44
    coin(d, pw-150, 230, 96, best_arp(card))
    y = 340
    if not card['sections']:
        d.text((40, y), '暂无竞技场对局记录', font=font('sc', 18, 600), fill=INK)
        y += 50
    for section in card['sections']:
        d.rectangle([40, y, pw-40, y+40], fill=INK)
        tag = 'RANKED · 计入 ARP' if section['ranked'] else 'UNRANKED · 不计 ARP'
        d.text((54, y+6), f"{section['title']}  {tag}", font=font('sc', 19, 700), fill=PAGE)
        result = record(section)
        if result:
            right(d, pw-54, y+6, f'{result[0]} W  ·  {result[1]} L', font('osw', 22, 600), GOLD)
        y += 52
        o = section['overall']
        if o:
            total = (f"出场 {o.get('games', 0)}   击杀 {o.get('kills', 0)}   阵亡 {o.get('deaths', 0)}   K/D {kd(o) or '—'}   "
                     f"最长连杀 {o.get('max_killstreak', '—')}   最长连胜 {o.get('win_streak', '—')}")
            d.text((40, y), total, font=fit(d, total, 'sc', 16, pw-80), fill=INK)
            y += 34
        for title, x in COLUMNS:
            d.text((x, y), title, font=font('sc', 13, 700), fill=BROWN)
        y += 22
        for label, stats in section['modes']:
            d.line([40, y, pw-40, y], fill=RULE, width=1)
            wins, lost = stats.get('wins'), losses(stats)
            label = safe(label.upper(), latin=True)
            d.text((40, y+6), label, font=fit(d, label, 'osw', 22, 190, 500), fill=INK)
            values = [stats.get('games', 0), f'{wins} – {lost}' if wins is not None else '—', win_rate(stats) or '—',
                      stats.get('kills', 0), kd(stats) or '—', stats.get('mvp', 0)]
            for (_, x), (_, next_x), value in zip(COLUMNS[1:], COLUMNS[2:]+(('', pw-66),), values):
                text = str(value)
                d.text((x, y+6), text, font=fit(d, text, 'mono', 20, next_x-x-8), fill=INK)
            if wins is not None and lost is not None and wins > lost:
                # A pen tick for a winning record.
                d.line([pw-62, y+18, pw-55, y+26, pw-42, y+8], fill=WAX, width=3)
            y += 38
        d.line([40, y, pw-40, y], fill=RULE, width=1)
        y += 30
    for note in notes_of(card):
        for line in wrap(d, safe(note), font('sc', 15, 400), pw-80):
            d.text((40, y), line, font=font('sc', 15, 400), fill=INK)
            y += 24
    y += 10
    d.text((40, y), '登记人：THE HOST', font=font('sc', 16, 600), fill=BROWN)
    d.line([150, y+24, 330, y+24], fill=INK, width=1)
    page_h = y+70
    height = 40+page_h+14+84+20
    img = texture(LEATHER, 21, 14, W, height)
    d = ImageDraw.Draw(img)
    d.rectangle([14, 14, W-14, height-14], outline=GOLD, width=2)
    img.paste(page.crop((0, 0, pw, page_h)), (40, 40))
    wax_seal(d, W-130, page_h, 46)
    footer(d, 40+page_h+14, card['updated'], (214, 196, 160), (180, 160, 124))
    return jpeg(img)
