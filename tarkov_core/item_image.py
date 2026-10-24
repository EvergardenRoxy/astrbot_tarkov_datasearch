"""Locally drawn common/mode/candidate/error item cards with frozen continuation pages."""
import io

from PIL import Image, ImageDraw, ImageOps

from . import NAME, NOTICE, VERSION
from .card import when
from .image import fit, font, jpeg, safe, wrap

WIDTH = 940
BG, PANEL, INK, MUTED, ACCENT = (17,20,24),(29,33,39),(230,232,235),(156,166,178),(216,177,106)


def paginate_item(card,blocks):
    draw = ImageDraw.Draw(Image.new('RGB',(1,1)))
    pages,rows,used = [],[],0
    previous = None
    for section,text in blocks:
        lines = [line for paragraph in str(text).splitlines()
                 for line in wrap(draw,safe(paragraph),font('sc',23,500),WIDTH-96)] or ['—']
        for start in range(0,len(lines),20):
            segment = lines[start:start+20]
            height = len(segment)*32+14+(38 if section != previous else 0)
            if rows and used+height > 1160:
                pages.append({**card,'rows':rows}); rows,used,previous = [],0,None
                height = len(segment)*32+52
            rows.append({'section':section,'lines':segment}); used += height; previous = section
    if rows or not pages: pages.append({**card,'rows':rows})
    return pages


def render_item_page(card,index=1,total=1,command='',icon=None):
    image = Image.new('RGB',(WIDTH,1900),BG); draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([24,24,WIDTH-24,206],radius=14,fill=PANEL)
    draw.text((44,40),'TARKOV · ITEM DATABASE',font=font('mono',24),fill=ACCENT)
    title = safe(card.get('name') or '物品查询')
    name_width = WIDTH-250 if icon else WIDTH-88
    f = fit(draw,title,'sc',32,name_width,800,smallest=20)
    lines = wrap(draw,title,f,name_width)
    draw.text((44,82),lines[0] if lines else '',font=f,fill=INK)
    if len(lines) > 1: draw.text((44,120),lines[1]+('…' if len(lines) > 2 else ''),font=f,fill=INK)
    subtitle = safe(card.get('label','查询提示'))
    draw.text((44,170),subtitle+f' · 第 {index}/{total} 页',font=font('sc',20,600),fill=MUTED)
    if icon:
        with Image.open(io.BytesIO(icon)) as source:
            thumb = ImageOps.contain(source.convert('RGBA'),(128,128))
            image.paste(thumb,(WIDTH-180,55),thumb)
    y,previous = 230,None
    if card.get('item_id'):
        draw.text((44,y),'ID '+card['item_id'],font=font('mono',22),fill=MUTED); y += 42
    for row in card.get('rows',[]):
        if previous != row['section']:
            draw.text((44,y),row['section'],font=font('sc',21,800),fill=ACCENT); y += 38
            previous = row['section']
        for line in row['lines']:
            draw.text((48,y),line,font=font('sc',23,500),fill=INK); y += 32
        y += 14
    y += 16; draw.line([44,y,WIDTH-44,y],fill=MUTED); y += 18
    notes = list(card.get('notes',[]))
    if total > 1: notes.append('冻结快照，至多保留 1 小时；续查：'+command)
    notes.extend(['数据来源：json.tarkov.dev · 图片：assets.tarkov.dev',f'{NAME} v{VERSION}',NOTICE])
    for text in notes:
        for line in wrap(draw,safe(text),font('sc',17,400),WIDTH-88):
            draw.text((44,y),line,font=font('sc',17,400),fill=MUTED); y += 24
    return jpeg(image.crop((0,0,WIDTH,y+24)))


def source_notes(metadata,failed=()):
    notes = []
    for mode,meta in metadata.items():
        from .modes import label_of
        checked = when(meta['checked_at']) if meta.get('checked_at') is not None else None
        fetched = when(meta['fetched_at']) if meta.get('fetched_at') is not None else None
        text = label_of(mode)+'：检查 '+(checked or '未知')+'；取得 '+(fetched or '未知')
        if meta.get('stale'): text += '（获取失败，旧索引/缓存）'
        elif meta.get('from_cache'): text += '（缓存/条件复查）'
        if meta.get('translation_warning'): text += '（翻译有缺失）'
        notes.append(text)
    if failed:
        from .modes import label_of
        notes.append('获取失败的模式：'+'、'.join(label_of(mode) for mode in failed))
    notes.append('时间为 GMT+8；扫描/获取时间不等于当前成交时间。')
    return notes
