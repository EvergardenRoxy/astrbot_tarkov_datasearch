"""Atlas Arena: the reviewed Arena Masters register, totals tables and nine rare achievement cards.

The old tarkov.dev renderer remains available. Atlas uses a local font provider, accurate source and data time,
plus the same achievement text selection and rarity ordering as the PVP/PVE cards. No network calls here.
"""
from PIL import ImageDraw

from . import NAME, NOTICE, REPO, VERSION, arena_image
from .arena import build_arena_card
from .atlas_card import achievement_data, when
from .atlas_profile import summarize_atlas_arena
from .matching_image import ATLAS_URL, logo


def build_arena(doc, *, now=None):
    summary = summarize_atlas_arena(doc)
    card = build_arena_card(summary, now=now)
    achievements = achievement_data(doc)
    card.update(source='atlas', mode='arena', mode_label='竞技场', overall_table=True,
                achievement_total=achievements['total'], achievement_details=achievements['rare'],
                # Arena has no last-match timestamp. Its activity proxy is the newest achievement time.
                last_active_label='最近成就 LAST HONOUR', cached=summary['cached'],
                looked_up=when(summary['updated'], '%Y-%m-%d %H:%M:%S') if summary['updated'] else None,
                # Atlas freshness is explained in the reply's cache/refresh notes, not the snapshot's stale warning.
                stale_days=None)
    return card


def render_atlas_arena_image(card):
    from . import atlas_image as ui

    def footer(d, top, updated, color, dim):
        stamp = f"数据时间 {card['looked_up'] or '—'}（UTC+8）"+(' · Atlas 缓存' if card['cached'] else '')
        lines = (stamp, '特别致谢：Atlas（EFTForge 项目的一部分，作者 Morph1ne / SouthHorizons76）',
                 f'{NAME} v{VERSION} · {REPO.removeprefix("https://")}', NOTICE)
        for i, line in enumerate(lines):
            line = str(line)
            f = ui.fitted(d, line, 'sc', 12, arena_image.W-64, 400)
            d.text(((arena_image.W-d.textlength(line, font=f))/2, top+i*19), line, font=f,
                   fill=color if i < 2 else dim)

    register = arena_image.render_arena_image(card, font_provider=ui.font, fit_provider=ui.fitted,
                                              footer_provider=footer, as_image=True)
    header_h = 100
    img = arena_image.texture(arena_image.LEATHER, 21, 14, arena_image.W, register.height+header_h)
    img.paste(register, (0, header_h))
    d = ImageDraw.Draw(img)
    d.rectangle([14, 14, arena_image.W-14, header_h-6], outline=arena_image.GOLD, width=2)
    mark = logo(38)
    img.paste(mark, (38, 32), mark)
    x = 38+mark.width+24
    d.text((x, 25), '数据来源：Atlas', font=ui.font('sc', 19, 600), fill=(226, 214, 186))
    d.text((x, 55), ATLAS_URL, font=ui.font('mono', 18), fill=(214, 196, 160))
    if card.get('reference_label'):
        label = str(card['reference_label'])
        f = ui.fitted(d, label, 'sc', 13, 180, 400)
        d.text((arena_image.W-38-d.textlength(label, font=f), 32), label, font=f, fill=(214, 196, 160))
    return ui.jpeg(img)
