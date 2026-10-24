"""Local status and status-error images; no full-text query fallback."""
import email.utils

from PIL import ImageDraw

from .card import when
from .image import FADED, INK, LINE, RED, W, fit, font, footer, jpeg, paper, safe, wrap

GREEN = (64, 108, 66)


def render_status_image(card):
    state = card.get('state')
    changes = card.get('changes', [])
    notices = card.get('notices', state['messages'] if state else [])
    paragraphs = [*changes]
    if card.get('error'):
        paragraphs.append(card['error'])
    if state and state['overall']['message']:
        paragraphs.append(state['overall']['message'])
    # The upstream announcement history is retained for deduplication; display a bounded recent excerpt.
    for notice in notices[-3:]:
        paragraphs.append(f"公告 {notice['time']}：{notice['text'][:400]}" + ('…' if len(notice['text']) > 400 else ''))
    rows = state['components'] if state else []
    height = 600+len(rows)*240+sum(28*(2+len(text)//30) for text in paragraphs)
    img = paper(height)
    d = ImageDraw.Draw(img)
    d.text((40, 32), 'TARKOV · SERVER STATUS', font=font('mono', 26), fill=INK)
    title = '服务器状态变化' if changes else '服务器状态检查'
    d.text((40, 82), title, font=font('sc', 34, 800), fill=INK)
    d.line([40, 134, W-40, 134], fill=INK, width=2)
    y = 154
    if state:
        overall = state['overall']
        text = f"总体状态：{overall['label']}"
        d.text((40, y), safe(text), font=fit(d, safe(text), 'sc', 28, W-80, 700),
               fill=GREEN if overall['code'] in ('ok', 'operational') else RED)
        y += 60
        for row in rows:
            name, label = safe(row['name']), safe(row['label'])
            names = wrap(d, name, font('sc', 20, 500), 450)
            labels = wrap(d, label, font('sc', 20, 600), W-560)
            for i, line in enumerate(names):
                d.text((48, y+i*26), line, font=font('sc', 20, 500), fill=INK)
            for i, line in enumerate(labels):
                d.text((520, y+i*26), line, font=font('sc', 20, 600),
                       fill=GREEN if row['code'] in ('ok', 'operational') else RED)
            row_height = max(48, max(len(names), len(labels))*26+12)
            d.line([40, y+row_height-10, W-40, y+row_height-10], fill=LINE)
            y += row_height
        if not rows:
            d.text((40, y), '上游未提供组件列表', font=font('sc', 20, 400), fill=FADED)
            y += 40
    for text in paragraphs:
        for line in wrap(d, safe(text), font('sc', 20, 400), W-80):
            d.text((40, y), line, font=font('sc', 20, 400), fill=RED if card.get('error') else INK)
            y += 28
        y += 14
    if len(notices) > 3:
        d.text((40, y), f'仅显示最近 3 条公告，共 {len(notices)} 条', font=font('sc', 17, 400), fill=FADED)
        y += 32
    checks = []
    if card.get('checked_at') is not None:
        checks.append('检查时间：'+(when(card['checked_at']) or '未知'))
    if card.get('compared_at') is not None:
        checks.append('对比上次检查：'+(when(card['compared_at']) or '未知'))
    if card.get('fetched_at') is not None:
        checks.append('取得时间：'+(when(card['fetched_at']) or '未知'))
    if card.get('last_modified'):
        try:
            stamp = when(email.utils.parsedate_to_datetime(card['last_modified']).timestamp())
        except (ValueError, TypeError, OverflowError):
            stamp = None
        if stamp:
            checks.append('上游资源版本时间：'+stamp)
    checks.append('时间为 GMT+8；公开状态数据，不代表实时连通性检测。')
    if card.get('stale'):
        checks.append('本次获取失败，图中为上次成功取得的旧数据。')
    elif card.get('from_cache'):
        checks.append('使用已缓存或条件复查后的资源。')
    for text in checks:
        d.text((40, y), text, font=font('sc', 16, 400), fill=RED if card.get('stale') else FADED)
        y += 26
    y += 12
    end = footer(d, y, None, FADED, FADED, line='数据来源：json.tarkov.dev/status · 社区整理的公开状态')
    return jpeg(img.crop((0, 0, W, end)))
