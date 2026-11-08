"""Cache the pinned complete font for development, tests and previews. No glyph collection or font editing.

    python tools/download_font.py
    TARKOV_TEST_FONT=/path/to/NotoSansSC.ttf python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tarkov_core.font_download import FILE, MIRRORS, SIZE, verify  # noqa: E402
from tarkov_core.fonts import use_chinese_font  # noqa: E402

DEFAULT_PATH = ROOT/'.cache'/'fonts'/FILE


def prepare(path=None):
    path = Path(path or os.environ.get('TARKOV_TEST_FONT') or DEFAULT_PATH).resolve()
    if not path.is_file() or not verify(path.read_bytes()):
        if path != DEFAULT_PATH.resolve():
            raise ValueError(f'Expected the pinned complete Noto Sans SC font: {path}')
        body = None
        for host, remote in (MIRRORS[-1], *MIRRORS[:-1]):
            try:
                request = urllib.request.Request(host+remote, headers={'User-Agent': 'astrbot_tarkov_datasearch-dev'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    # Bound development downloads just as runtime downloads are bounded.
                    candidate = response.read(SIZE+1)
                if verify(candidate):
                    body = candidate
                    break
            except OSError:
                continue
        if body is None:
            raise RuntimeError('Could not download the verified complete Noto Sans SC font')
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.font-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(body)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    use_chinese_font(path)
    return path


if __name__ == '__main__':
    print(prepare(sys.argv[1] if len(sys.argv) > 1 else None))
