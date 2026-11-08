"""The matching-time picture: a dark board with the Atlas logo, per-region fastest datacenters, the fastest
datacenters and, below them, the fixed Hong Kong / Japan / Korea rows, each with a 24-hour line.

Atlas requires every output of its data to carry its logo and to name "Atlas" with a link to its page (maintainer,
2026-11-08). Drawn locally with Pillow; the logo is the image the Atlas author supplied (THIRD_PARTY.md).
"""
import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import NAME, NOTICE, REPO, VERSION
from .image import FONTS, FONT_FILES, jpeg, safe

ATLAS_NAME = 'Atlas'
ATLAS_URL = 'https://eftforge.com/atlas'
LOGO = Path(__file__).with_name('assets')/'atlas-logo.png'
TZ = datetime.timezone(datetime.timedelta(hours=8))

W = 1600
BG, PANEL, ROW_ALT, RULE = (16, 18, 21), (22, 25, 29), (24, 27, 31), (40, 44, 50)
TEXT, DIM, FAINT = (226, 228, 230), (150, 154, 160), (96, 100, 108)
LINE_BLUE, ACCENT = (90, 176, 240), (90, 176, 240)
FAST, OK, SLOW, WARN = (120, 210, 110), (232, 196, 80), (236, 120, 72), (236, 120, 72)
FAST_SECONDS, OK_SECONDS = 60, 180

# Atlas raw datacenter name -> fixed row label. "China" is the Hong Kong server (maintainer); Chinese only.
FIXED = (('China', '香港'), ('Japan', '日本'), ('Korea', '韩国'))
RANKED = 8
REGIONS = {'RUS': '俄罗斯', 'EUR': '欧洲', 'USA': '北美', 'ASI': '亚洲', 'OCE': '大洋洲', 'SAM': '拉丁美洲',
           'ME': '中东', 'AF': '非洲'}

LEFT_W = 380
X0 = LEFT_W+32                       # table left edge
COLS = {'region': X0, 'name': X0+76, 'current': X0+420, 'spark': X0+444, 'min': W-236, 'avg': W-146, 'max': W-40}
SPARK_W = COLS['min']-100-COLS['spark']
ROW_H = 54
HEADER_H = 128


@lru_cache(maxsize=None)
def font(name, size, weight=None):
    """The bundled fonts with basic layout: at these small sizes the complex layout engine rounds glyph positions
    unevenly (a visible gap in "h ttps"); this picture needs no complex shaping."""
    f = ImageFont.truetype(str(FONTS/FONT_FILES[name]), size, layout_engine=ImageFont.Layout.BASIC)
    if weight is not None:
        f.set_variation_by_axes([weight])
    return f


def wait_text(seconds):
    """95 -> "1:35" (minutes:seconds, like the game and Atlas); missing -> "—"."""
    if seconds is None:
        return '—'
    seconds = round(seconds)
    return f'{seconds//60}:{seconds % 60:02d}'


def tone(seconds):
    if seconds is None:
        return FAINT
    return FAST if seconds <= FAST_SECONDS else OK if seconds <= OK_SECONDS else SLOW


def display_name(center):
    """The name without a two-letter prefix that repeats the region ("Sa Colombia" -> "Colombia"), like Atlas shows."""
    words = center.display_name.split(' ', 1)
    if len(words) == 2 and len(words[0]) == 2 and center.region.upper().startswith(words[0].upper()):
        return words[1]
    return center.display_name


def stats(center):
    values = [v for v in center.history or () if v is not None]
    if not values:
        return None, None, None
    return min(values), sum(values)/len(values), max(values)


def stamp(seconds, pattern='%m/%d %H:%M'):
    return datetime.datetime.fromtimestamp(seconds, TZ).strftime(pattern)


def sections(result):
    """(fixed rows, ranked rows, region summaries) of a MatchingResult."""
    by_name = {center.name: center for center in result.datacenters}
    fixed = [(label, by_name.get(raw)) for raw, label in FIXED]
    fixed_names = {raw for raw, _ in FIXED}
    ranked = sorted((c for c in result.datacenters if c.name not in fixed_names and c.wait is not None),
                    key=lambda c: (c.wait, display_name(c)))[:RANKED]
    regions = {}
    for center in result.datacenters:
        best = regions.get(center.region)
        if center.wait is not None and (best is None or best.wait is None or center.wait < best.wait):
            regions[center.region] = center
        elif best is None:
            regions[center.region] = center
    order = sorted(regions.items(), key=lambda item: (item[1].wait is None, item[1].wait or 0, item[0]))
    return fixed, ranked, order


@lru_cache(maxsize=1)
def logo(height):
    image = Image.open(LOGO).convert('RGBA')
    return image.resize((round(image.width*height/image.height), height), Image.LANCZOS)


def right(d, x, y, text, f, fill):
    d.text((x-d.textlength(text, font=f), y), text, font=f, fill=fill)


def ticks(result):
    """(x, label) of the 3-hourly UTC+8 grid lines across the sparkline column."""
    span = result.history_step*max(1, max((len(c.history or ()) for c in result.datacenters), default=1)-1)
    start = result.history_start
    first = datetime.datetime.fromtimestamp(start, TZ).replace(minute=0, second=0, microsecond=0)
    first += datetime.timedelta(hours=3-first.hour % 3)
    out = []
    moment = first
    while span and moment.timestamp() <= start+span:
        out.append((COLS['spark']+SPARK_W*(moment.timestamp()-start)/span, moment.strftime('%H:%M')))
        moment += datetime.timedelta(hours=3)
    return out, span


def sparkline(d, center, top, result, span, grid):
    for x, _ in grid:
        d.line([x, top+8, x, top+ROW_H-8], fill=RULE)
    points = center.history if center is not None else None
    if not points or not span:
        d.text((COLS['spark'], top+16), '暂无历史数据', font=font('sc', 16, 400), fill=FAINT)
        return
    values = [v for v in points if v is not None]
    if not values:
        d.text((COLS['spark'], top+16), '暂无历史数据', font=font('sc', 16, 400), fill=FAINT)
        return
    low, high = min(values), max(values)
    if high-low < 60:  # Small changes stay small instead of filling the row.
        middle = (high+low)/2
        low, high = middle-30, middle+30
    y0, y1 = top+ROW_H-12, top+12
    segment = []
    for i, value in enumerate(points):
        if value is None:  # A gap is a break in the line, never zero.
            if len(segment) > 1:
                d.line(segment, fill=LINE_BLUE, width=2, joint='curve')
            segment = []
            continue
        x = COLS['spark']+SPARK_W*(i*result.history_step)/span
        segment.append((x, y0+(y1-y0)*(value-low)/(high-low)))
    if len(segment) > 1:
        d.line(segment, fill=LINE_BLUE, width=2, joint='curve')
    elif segment:
        x, y = segment[0]
        d.ellipse([x-2, y-2, x+2, y+2], fill=LINE_BLUE)


def row(d, y, label, center, result, span, grid, shade):
    if shade:
        d.rectangle([LEFT_W, y, W, y+ROW_H-1], fill=ROW_ALT)
    mono, name_font = font('mono', 18), font('sc', 21, 500)
    if center is None:
        d.text((COLS['name'], y+13), safe(label), font=name_font, fill=DIM)
        right(d, COLS['current'], y+12, '—', font('mono', 26), FAINT)
        d.text((COLS['spark'], y+16), 'Atlas 暂未提供这个数据中心', font=font('sc', 16, 400), fill=FAINT)
        return
    d.text((COLS['region'], y+17), safe(center.region, latin=True), font=mono, fill=FAINT)
    d.text((COLS['name'], y+13), safe(label), font=name_font, fill=TEXT)
    right(d, COLS['current'], y+12, wait_text(center.wait), font('mono', 26), tone(center.wait))
    sparkline(d, center, y, result, span, grid)
    for key, value in zip(('min', 'avg', 'max'), stats(center)):
        right(d, COLS[key], y+16, wait_text(value), font('mono', 20), tone(value))


def section_title(d, y, text):
    d.text((X0, y+12), text, font=font('sc', 20, 700), fill=ACCENT)
    return y+46


def render_matching_image(result):
    fixed, ranked, regions = sections(result)
    grid, span = ticks(result)
    table_top = HEADER_H+12
    body_h = 58+46+max(1, len(ranked))*ROW_H+12+46+len(fixed)*ROW_H
    footer_top = table_top+body_h+24
    height = footer_top+100
    img = Image.new('RGB', (W, height), BG)
    d = ImageDraw.Draw(img)

    # Header: title and Atlas sample time on the left; the Atlas logo with its name and link on the right.
    d.text((40, 24), '平均匹配时间', font=font('sc', 40, 800), fill=TEXT)
    updated = f'更新于 {stamp(result.updated)}（UTC+8）'
    d.text((40, 82), updated, font=font('sc', 19, 400), fill=DIM)
    if result.stale:
        x = 40+d.textlength(updated, font=font('sc', 19, 400))+20
        d.text((x, 85), '本次获取失败，显示的是上次成功取得的数据', font=font('sc', 16, 600), fill=WARN)
    mark = logo(56)
    img.paste(mark, (W-40-mark.width, 22), mark)
    label, link = f'数据来源：{ATLAS_NAME}  ', ATLAS_URL
    f_label, f_link = font('sc', 17, 600), font('sc', 17, 400)
    x = W-40-d.textlength(link, font=f_link)
    d.text((x, 88), link, font=f_link, fill=ACCENT)
    right(d, x, 88, label, f_label, TEXT)
    d.line([0, HEADER_H, W, HEADER_H], fill=RULE, width=1)

    # Left panel: each region's fastest datacenter, fastest region first, spread over the table's height.
    d.rectangle([0, HEADER_H+1, LEFT_W-1, footer_top-12], fill=PANEL)
    d.line([LEFT_W, HEADER_H, LEFT_W, footer_top-12], fill=RULE)
    d.text((24, table_top+12), '各地区最快', font=font('sc', 18, 700), fill=DIM)
    y = table_top+58
    pitch = max(66, (footer_top-36-y)//max(1, len(regions)))
    for code, center in regions:
        top = y+(pitch-60)//2
        d.text((24, top), safe(REGIONS.get(code, code)), font=font('sc', 22, 600), fill=TEXT)
        if center.wait is None:
            d.text((24, top+32), '暂无数据', font=font('sc', 16, 400), fill=FAINT)
        else:
            label = '最快：'
            f = font('sc', 16, 600)
            d.text((24, top+32), label, font=f, fill=tone(center.wait))
            x = 24+d.textlength(label, font=f)
            name = safe(display_name(center))
            d.text((x, top+32), name, font=font('sc', 16, 400), fill=DIM)
            x += d.textlength(name, font=font('sc', 16, 400))+10
            d.text((x, top+31), wait_text(center.wait), font=font('mono', 18), fill=tone(center.wait))
        y += pitch
        d.line([16, y-1, LEFT_W-16, y-1], fill=RULE)

    # Table header.
    y = table_top
    small = font('sc', 15, 600)
    d.text((COLS['region'], y+30), '地区', font=small, fill=DIM)
    d.text((COLS['name'], y+30), '数据中心', font=small, fill=DIM)
    right(d, COLS['current'], y+30, '当前', small, DIM)
    d.text((COLS['spark'], y+6), f'最近 {result.hours} 小时（UTC+8）', font=font('sc', 14, 400), fill=FAINT)
    for x, label in grid:
        d.text((x-d.textlength(label, font=font('mono', 14))/2, y+32), label, font=font('mono', 14), fill=FAINT)
    for key, label in (('min', '最短'), ('avg', '平均'), ('max', '最长')):
        right(d, COLS[key], y+30, label, small, DIM)
    y += 58
    d.line([LEFT_W, y, W, y], fill=RULE)

    y = section_title(d, y, f'最快的 {RANKED} 个数据中心')
    if not ranked:
        d.text((COLS['name'], y+14), '暂无数据', font=font('sc', 18, 400), fill=FAINT)
    for i, center in enumerate(ranked):
        row(d, y, display_name(center), center, result, span, grid, i % 2 == 1)
        y += ROW_H
    if not ranked:
        y += ROW_H
    y += 12
    y = section_title(d, y, '常用服务器')
    for i, (label, center) in enumerate(fixed):
        row(d, y, label, center, result, span, grid, i % 2 == 1)
        y += ROW_H

    # Footer: the plugin's own small print.
    d.line([0, footer_top-12, W, footer_top-12], fill=RULE)
    f = font('sc', 15, 400)
    lines = ('“—”表示暂无数据；查询失败不代表等待时间为 0 或服务器宕机。',
             f"{NAME} v{VERSION} · {REPO.removeprefix('https://')}", NOTICE)
    for i, text in enumerate(lines):
        d.text((W/2-d.textlength(text, font=f)/2, footer_top+8+i*26), text, font=f, fill=FAINT)
    return jpeg(img)
