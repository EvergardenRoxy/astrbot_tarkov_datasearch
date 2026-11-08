"""Rebuild docs/matching_preview.jpg from Atlas matching data (public game-service data, no player information).

    python tools/build_matching_preview.py saved-response.json   # render a response saved earlier (preferred)
    python tools/build_matching_preview.py                       # fetch https://www.eftforge.com/atlas/api/matching?hours=24 once

Atlas asks clients to cache and not to poll: reuse a saved response while iterating on the picture. The fetch uses the
environment's proxy settings (development only; the plugin itself uses only its own proxy setting).
"""
import json
import sys
import time
import types
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
# transport.py imports AstrBot's logger; nothing here logs.
api = types.ModuleType('astrbot.api')
api.logger = types.SimpleNamespace(warning=print, info=print, debug=print)
sys.modules.setdefault('astrbot', types.ModuleType('astrbot'))
sys.modules.setdefault('astrbot.api', api)

from tarkov_core.matching import normalize  # noqa: E402
from tarkov_core.matching_image import render_matching_image  # noqa: E402
from tarkov_core.transport import USER_AGENT  # noqa: E402

URL = 'https://www.eftforge.com/atlas/api/matching?hours=24'


def main():
    from download_font import prepare
    prepare()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        document, fetched_at = json.loads(path.read_text(encoding='utf-8')), path.stat().st_mtime
    else:
        request = urllib.request.Request(URL, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            document, fetched_at = json.load(response), time.time()
    out = ROOT/'docs'/'matching_preview.jpg'
    out.write_bytes(render_matching_image(normalize(document, fetched_at)))
    print(out, out.stat().st_size)


if __name__ == '__main__':
    main()
