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


# Where render_arena_image() puts the page and its title (page offset 40, title at y 30 on the page).
PAGE_Y, TITLE_Y = 40, 30


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
    # No banner above the register (maintainer, 2026-11-08): the Atlas logo and link sit at the right end of the
    # "ARENA MASTERS" line, on a plate of the border's leather so the logo's white lettering stays legible.
    img = register
    d = ImageDraw.Draw(img)
    title = ui.font('osw', 40, 700)
    _, top, _, bottom = title.getbbox('ARENA MASTERS')
    cy = PAGE_Y+TITLE_Y+(top+bottom)/2
    mark, url_font = logo(28), ui.font('mono', 17)
    pad, gap = 14, 12
    right = arena_image.W-PAGE_Y-40
    plate_w = pad+mark.width+gap+d.textlength(ATLAS_URL, font=url_font)+pad
    # The plate is cut from the register's own leather edge (same colour and grain) with its gold frame line.
    box = (round(right-plate_w), round(cy-22), round(right), round(cy+22))
    img.paste(arena_image.texture(arena_image.LEATHER, 21, 14, box[2]-box[0], box[3]-box[1]), box[:2])
    d.rectangle(box, outline=arena_image.GOLD, width=2)
    img.paste(mark, (round(right-plate_w+pad), round(cy-mark.height/2)), mark)
    ui.mid(d, right-pad, cy, ATLAS_URL, url_font, (226, 214, 186), 'r')
    if card.get('reference_label'):
        label = str(card['reference_label'])
        ui.mid(d, right, PAGE_Y+92, label, ui.fitted(d, label, 'sc', 13, 180, 400), arena_image.BROWN, 'r')
    return ui.jpeg(img)
