"""Preview the old Arena register with Atlas attribution, using invented data only.

    python tools/build_arena_legacy_preview.py

Uses the production Atlas Arena renderer. No network requests or real player data.
"""
from build_atlas_arena_preview import ROOT, demo
from tarkov_core import atlas_image
from tarkov_core.atlas_card import build as build_atlas_card


def reference_document():
    doc = demo()
    # Invented achievement labels and values for layout review, not a real player's response.
    rarities = ['Seasonal']*2+['Legendary']*3+['Rare']*6+['Common']*13
    names = ['赛季挑战示例', '赛季荣誉示例', '连胜挑战示例', '精确射击示例', '团队荣誉示例',
             '竞技场老兵示例', '战术协作示例', '胜利之路示例', '关键回合示例']
    done = [{'id': f'example-{i}', 'rarity': rarity, 'at': 1794000000-i*86400,
             'percent': round(0.5+i*1.7, 1), 'ch': {'name': names[i] if i < len(names) else f'普通成就示例 {i}'}}
            for i, rarity in enumerate(rarities)]
    doc['achievements'] = {'count': len(done), 'total': 86, 'done': list(reversed(done))}
    return doc


def render(doc=None):
    doc = reference_document() if doc is None else doc
    card = build_atlas_card(doc, 'arena')
    card['reference_label'] = '虚构数据预览'
    return atlas_image.render_atlas_image(card)


if __name__ == '__main__':
    out = ROOT/'docs'/'arena_legacy_atlas_reference_v2.jpg'
    out.write_bytes(render())
    print(out)
