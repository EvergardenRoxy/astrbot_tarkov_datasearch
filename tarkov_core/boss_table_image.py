"""Dark Boss-by-map probability matrix, with immutable column pagination."""
import email.utils
import io
import re
from collections import OrderedDict
from decimal import Decimal

from PIL import Image, ImageDraw, ImageOps

from .boss_monitor import percent
from .card import when
from .image import fit, font, jpeg, safe, wrap
from .modes import label_of

MARGIN, LABEL_WIDTH, MAP_WIDTH = 20, 280, 120
MAPS_PER_PAGE, FONT_SIZE, LINE_HEIGHT = 16, 22, 30
MAX_EVENT_ROWS = 3
BG, BODY, LABEL_BG, GRID = (12,12,12), (21,21,21), (31,31,31), (43,43,43)
TEXT, MUTED, RED = (218,220,222), (154,157,160), (255,65,65)
BOSS_ROWS = (('bossTagilla','Tagilla'), ('bossBully','Reshala'), ('bossKojaniy','Shturman'),
    ('bossSanitar','Sanitar'), ('bossKilla','Killa'), ('bossGluhar','Glukhar'), ('bossZryachiy','Zryachiy'),
    ('bossBoar','Kaban'), ('bossKolontay','Kollontay'), ('bossPartisan','Partisan'),
    ('sectantPriest','邪教徒祭司'), ('bossKnight','三狗（GOONS）'))
MAP_LABELS = (
    ('65b8d6f5cdde2479cb2a3125','中心区'),
    ('55f2d3fd4bdc2d5f408b4567','白天工厂'), ('59fc81d786f774390775787e','夜间工厂'),
    ('56f40101d2720b2a4d8b45d6','海关'), ('5704e3c2d2720bac5b8b4567','森林'),
    ('5704e554d2720bac5b8b456e','海岸线'), ('5714dbc024597771384a510d','立交桥'),
    ('5704e5fad2720bc05b8b4567','储备站'), ('5704e4dad2720bb55b8b4567','灯塔'),
    ('5714dc692459777137212e12','塔科夫街区'), ('5b0fc42d86f7744a585f9105','实验室'))


def compact_rows(rows):
    groups = OrderedDict()
    for row in rows:
        key = (row['map_id'],row['mob_id'])
        if key not in groups:
            groups[key] = {'map_id':row['map_id'],'map_name':row['map_name'],'mob_id':row['mob_id'],
                           'boss_name':row['boss_name'],'event':row.get('event',False),'rates':set(),'changed':False}
        group = groups[key]
        if row['current'] is not None: group['rates'].add(row['current'])
        group['changed'] |= row['changed']
    return [{**row,'rates':sorted(row['rates'],key=Decimal)} for row in groups.values()]


def rate_lines(draw, rates):
    lines, line = [], ''
    for rate in rates:
        text = percent(rate)
        candidate = line+' / '+text if line else text
        if line and draw.textlength(candidate,font=font('sc',FONT_SIZE,800)) > MAP_WIDTH-16:
            lines.append(line); line = ''
        line = line+' / '+text if line else text
    if line: lines.append(line)
    return [part for text in lines for part in wrap(draw,text,font('sc',FONT_SIZE,800),MAP_WIDTH-16)]


def paginate_table(card, rows):
    draw = ImageDraw.Draw(Image.new('RGB',(1,1)))
    compact = compact_rows(rows)
    maps = {r['map_id']:r['map_name'] for r in compact}
    order = {key:i for i,(key,_) in enumerate(MAP_LABELS)}
    names = dict(MAP_LABELS)
    map_ids = sorted(maps,key=lambda key:(order.get(key,len(order)),key))
    lookup = {(r['map_id'],r['mob_id']):r for r in compact}
    # Event bosses (not resident, not an ordinary soldier) get up to MAX_EVENT_ROWS extra rows below the fixed ones.
    events = {r['mob_id']:r['boss_name'] for r in compact if r.get('event') and r['mob_id']}
    event_ids = sorted(events)[:MAX_EVENT_ROWS]
    overflow = len(events)-len(event_ids)
    pages = []
    for start in range(0,max(1,len(map_ids)),MAPS_PER_PAGE):
        columns = [{'map_id':key,'name':names.get(key,maps[key])} for key in map_ids[start:start+MAPS_PER_PAGE]]
        for column in columns:
            column['lines'] = wrap(draw,safe(column['name']),font('sc',20,700),MAP_WIDTH-12)
        matrix = []
        for mob_id,name,event in [(m,n,False) for m,n in BOSS_ROWS]+[(m,events[m],True) for m in event_ids]:
            cells = []
            for column in columns:
                source = lookup.get((column['map_id'],mob_id),{})
                rates, changed = source.get('rates',[]), source.get('changed',False)
                cells.append({'rates':rates,'changed':changed,
                              'lines':rate_lines(draw,rates) if rates else ['—'] if changed else []})
            if event and not any(c['lines'] for c in cells):
                continue
            height = max(90,max((len(c['lines'])*LINE_HEIGHT+24 for c in cells),default=90))
            matrix.append({'mob_id':mob_id,'boss_name':name,'label':('活动 '+name) if event else name,
                           'cells':cells,'height':height})
        pages.append({**card,**({'event_overflow':overflow} if overflow else {}),'columns':columns,'rows':matrix,
                      'header_height':max(78,max((len(c['lines'])*25+20 for c in columns),default=78))})
    return pages


MAX_CHANGE_LINES = 20


def change_lines(rows, map_ids=None):
    """Short statements of what changed, for the change broadcast only (the table itself shows just the new values)."""
    lines = []
    for row in rows:
        if not row['changed'] or (map_ids is not None and row['map_id'] not in map_ids):
            continue
        name = ('活动 ' if row.get('event') else '')+str(row['boss_name'])
        if row['mob_id'] is None:
            lines.append(f"{row['change']}：{row['map_name']}")
        elif row['previous'] is None:
            lines.append(f"{name}（{row['map_name']}）：新增 {percent(row['current'])}")
        elif row['current'] is None:
            lines.append(f"{name}（{row['map_name']}）：移除（原 {percent(row['previous'])}）")
        else:
            lines.append(f"{name}（{row['map_name']}）：{percent(row['previous'])}→{percent(row['current'])}")
    if len(lines) > MAX_CHANGE_LINES:
        lines[MAX_CHANGE_LINES:] = [f'另有 {len(lines)-MAX_CHANGE_LINES} 项变化未列出']
    return lines


def notes_for(page,token,index,total,command):
    """Footer lines: only warnings and the continuation hint when they apply, then one source line."""
    notes = []
    if page.get('stale'): notes.append('获取失败，显示上次成功数据；本次不生成监控变化。')
    elif page.get('from_cache'): notes.append('使用已缓存或条件复查后的资源。')
    if page.get('translation_warning'): notes.append('部分翻译未取得，名称保留英文或上游标识。')
    if token and total > 1: notes.append(f'快照 {token} · 第 {index}/{total} 页 · 至多保留 24 小时；续查：'+command)
    if page.get('event_overflow'): notes.append(f"另有 {page['event_overflow']} 个活动 Boss 未显示")
    from . import NAME, NOTICE, VERSION
    notes.append(f'数据 json.tarkov.dev · 头像 assets.tarkov.dev · {NAME} v{VERSION} · '+NOTICE)
    return notes


def render_boss_page(page,token='',index=1,total=1,command='',portraits=None):
    portraits = portraits or {}
    columns, rows = page.get('columns',[]), page.get('rows',[])
    width = MARGIN*2+LABEL_WIDTH+MAP_WIDTH*len(columns) if columns else 1100
    header = page.get('header_height',78)
    grid_end = MARGIN+header+sum(row['height'] for row in rows)
    if page.get('error'): grid_end = 250
    probe = ImageDraw.Draw(Image.new('RGB',(1,1)))
    notes = [line for text in notes_for(page,token,index,total,command)
             for line in wrap(probe,safe(text),font('sc',18,400),width-2*MARGIN)]
    summary = [line for text in page.get('change_lines',[])
               for line in wrap(probe,safe(text),font('sc',20,600),width-2*MARGIN-20)]
    summary_height = len(summary)*30+44 if summary else 0
    img = Image.new('RGB',(width,grid_end+summary_height+len(notes)*26+48),BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([MARGIN,MARGIN,width-MARGIN,grid_end],radius=14,fill=BODY,outline=GRID)
    draw.rectangle([MARGIN+1,MARGIN+1,width-MARGIN-1,MARGIN+header],fill=LABEL_BG)
    mode = label_of(page['mode']) if page.get('mode') else 'BOSS'
    draw.text((MARGIN+LABEL_WIDTH/2,MARGIN+header/2),mode,font=font('sc',26,800),fill=TEXT,anchor='mm')
    if page.get('error'):
        y = MARGIN+header+26
        for line in wrap(draw,safe(page['error']),font('sc',23,500),width-2*MARGIN-40):
            draw.text((MARGIN+20,y),line,font=font('sc',23,500),fill=RED); y += 32
    else:
        for j,column in enumerate(columns):
            cx = MARGIN+LABEL_WIDTH+(j+.5)*MAP_WIDTH
            y = MARGIN+(header-len(column['lines'])*25)/2
            for line in column['lines']:
                draw.text((cx,y),line,font=font('sc',20,700),fill=TEXT,anchor='mt'); y += 25
        y = MARGIN+header
        for row in rows:
            height = row['height']
            draw.rectangle([MARGIN+1,y,MARGIN+LABEL_WIDTH,y+height],fill=LABEL_BG)
            guaranteed = any('1' in cell['rates'] for cell in row['cells'])
            label_color,label_weight = (RED,800) if guaranteed else (TEXT,600)
            portrait = portraits.get(row['mob_id'])
            if portrait:
                with Image.open(io.BytesIO(portrait)) as source:
                    icon = ImageOps.contain(source.convert('RGBA'),(72,72))
                    img.paste(icon,(MARGIN+10,int(y+(height-icon.height)/2)),icon)
            else:
                draw.ellipse([MARGIN+20,y+height/2-25,MARGIN+70,y+height/2+25],fill=GRID)
                draw.text((MARGIN+45,y+height/2),safe(row['boss_name'][:1] or '?'),font=font('sc',24,700),fill=MUTED,anchor='mm')
            name = safe(row.get('label',row['boss_name']))
            f = fit(draw,name,'sc',23,LABEL_WIDTH-98,label_weight,smallest=17)
            draw.text((MARGIN+90,y+height/2),name,font=f,fill=label_color,anchor='lm')
            for j,cell in enumerate(row['cells']):
                cx = MARGIN+LABEL_WIDTH+(j+.5)*MAP_WIDTH
                ty = y+(height-len(cell['lines'])*LINE_HEIGHT)/2
                for line in cell['lines']:
                    parts = re.split(r'(100%)',line)
                    runs = [(part,font('sc',FONT_SIZE,800 if part=='100%' else 600),
                             RED if part=='100%' or cell['changed'] else TEXT) for part in parts if part]
                    x = cx-sum(draw.textlength(part,font=f) for part,f,_ in runs)/2
                    for part,f,color in runs:
                        draw.text((x,ty),part,font=f,fill=color); x += draw.textlength(part,font=f)
                    ty += LINE_HEIGHT
            draw.line([MARGIN,y+height,width-MARGIN,y+height],fill=GRID); y += height
        for j in range(len(columns)+1):
            x = MARGIN+LABEL_WIDTH+j*MAP_WIDTH
            draw.line([x,MARGIN,x,grid_end],fill=GRID)
        draw.line([MARGIN,MARGIN+header,width-MARGIN,MARGIN+header],fill=GRID)
    y = grid_end+18
    if summary:
        draw.text((MARGIN,y),'本次变化',font=font('sc',22,800),fill=TEXT); y += 34
        for line in summary:
            draw.text((MARGIN+20,y),line,font=font('sc',20,600),fill=TEXT); y += 30
        y += 10
    for line in notes:
        draw.text((MARGIN,y),line,font=font('sc',18,400),fill=MUTED); y += 26
    return jpeg(img)
