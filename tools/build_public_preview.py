"""Rebuild docs/status_preview.jpg, docs/boss_preview.jpg and docs/boss_changes_preview.jpg from LIVE public data.

    python tools/build_public_preview.py [http://proxy:port]

Needs network access to json.tarkov.dev and assets.tarkov.dev (an explicit HTTP proxy may be given as the argument or in
HTTPS_PROXY). The Boss portraits are downloaded into a temporary directory exactly the way the plugin's manual portrait
update does it, and are drawn into the pictures: they are Battlestate Games artwork (see THIRD_PARTY.md). The status and
Boss tables are the real current data; the "changes" picture compares it with a hand-made previous table (three
differences), because a real change cannot be produced on demand.
"""
import asyncio
import copy
import os
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
api = types.ModuleType('astrbot.api')
api.logger = types.SimpleNamespace(warning=print, info=print, debug=print)
sys.modules.setdefault('astrbot', types.ModuleType('astrbot'))
sys.modules.setdefault('astrbot.api', api)

from tarkov_core.boss_monitor import build_table, normalize_bosses  # noqa: E402
from tarkov_core.boss_portraits import BossPortraits  # noqa: E402
from tarkov_core.boss_table_image import change_lines, paginate_table, render_boss_page  # noqa: E402
from tarkov_core.public_api import ResourceKey, fetch, mode_path  # noqa: E402
from tarkov_core.server_status import normalize_status  # noqa: E402
from tarkov_core.status_image import render_status_image  # noqa: E402

import json  # noqa: E402

MODE = 'pve'


async def document(key, proxy):
    metadata, body = await fetch(key, proxy=proxy)
    if metadata['status'] != 200:
        raise SystemExit(f'{key.path}: HTTP {metadata["status"]}')
    return json.loads(body), metadata


def previous_table(state):
    """A hand-made earlier table: one rate that was different, one record that did not exist, one that has since ended."""
    before = copy.deepcopy(state)
    rows = [(m, r) for m in before for r in m['rows'] if not r.get('event')]
    for m, r in rows:
        if r['chance'] not in ('1', '1.0') and float(r['chance']) < 0.6:
            r['chance'] = str(round(float(r['chance'])+0.2, 2)); break
    for m, r in rows:
        if r['chance'] in ('1', '1.0'):
            m['rows'].remove(r); break
    template = rows[0][1]
    for m in before:
        if m['rows'] and m['rows'][0] is not template and not any(r['mob_id'] == 'bossTagilla' for r in m['rows']):
            m['rows'].append({**template, 'mob_id': 'bossTagilla', 'boss_name': 'Tagilla', 'chance': '0.3', 'event': False,
                              'rule': template['rule']}); break
    return before


async def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('HTTPS_PROXY')
    now = time.time()
    path = mode_path(MODE)
    maps, metadata = await document(ResourceKey('maps', path), proxy)
    dictionaries = [(await document(ResourceKey('maps', path, language), proxy))[0]['data'] for language in ('zh', 'en')]
    state = normalize_bosses(maps['data'], dictionaries)
    status, status_meta = await document(ResourceKey('status'), proxy)
    with tempfile.TemporaryDirectory() as directory:
        portraits = BossPortraits(Path(directory))
        count = await portraits.refresh(proxy=proxy)
        images = await portraits.all()
        await portraits.close()
    print('portraits', count, 'map columns', len(state), 'event rows', sorted({r['mob_id'] for m in state for r in m['rows'] if r['event']}))
    meta = {'mode': MODE, 'checked_at': now, 'fetched_at': now, 'last_modified': metadata.get('last_modified'),
            'stale': False, 'from_cache': False}
    rows, _ = build_table(None, state)
    (ROOT/'docs'/'boss_preview.jpg').write_bytes(render_boss_page(paginate_table(meta, rows)[0], portraits=images))
    rows, _ = build_table(previous_table(state), state)
    page = paginate_table({**meta, 'compared_at': now-600}, rows)[0]
    page['change_lines'] = change_lines(rows, {column['map_id'] for column in page['columns']})
    (ROOT/'docs'/'boss_changes_preview.jpg').write_bytes(render_boss_page(page, portraits=images))
    card = {'state': normalize_status(status['data']), 'checked_at': now, 'fetched_at': now, 'from_cache': False,
            'stale': False, 'last_modified': status_meta.get('last_modified')}
    (ROOT/'docs'/'status_preview.jpg').write_bytes(render_status_image(card))


if __name__ == '__main__':
    asyncio.run(main())
