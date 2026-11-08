"""The full Noto Sans SC font, downloaded once on first use and kept in the plugin's data directory.

Every image uses this complete font. First use waits for the shared download; failures are reported to the caller.
No trimmed font, character collection or missing-character substitution is maintained.

- Font: Noto Sans SC (SIL Open Font License 1.1), pinned to one commit
  of github.com/google/fonts and checked by size and SHA-256, so every mirror must serve exactly that file.
- Mirrors, tried in order: an optional address from the settings (e.g. a mirror reachable in the user's region),
  three jsDelivr CDN hosts, then raw.githubusercontent.com. A mirror that fails is skipped; when all fail the next
  try waits RETRY_SECONDS.
- The file is reused on disk across restarts; simultaneous first renders share one shielded download.
"""
import asyncio
import hashlib
import io
import os
import tempfile
import time

from yarl import URL

COMMIT = '86aced91de05cd4f96a06838124cca45be337b2b'
FILE = 'NotoSansSC[wght].ttf'
SHA256 = 'a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da'
SIZE = 17772300
REMOTE = 'ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf'
MIRRORS = (
    ('https://cdn.jsdelivr.net', f'/gh/google/fonts@{COMMIT}/{REMOTE}'),
    ('https://fastly.jsdelivr.net', f'/gh/google/fonts@{COMMIT}/{REMOTE}'),
    ('https://gcore.jsdelivr.net', f'/gh/google/fonts@{COMMIT}/{REMOTE}'),
    ('https://raw.githubusercontent.com', f'/google/fonts/{COMMIT}/{REMOTE}'),
)
CONTENT_TYPES = ('font/ttf', 'font/sfnt', 'application/x-font-ttf', 'application/font-sfnt', 'application/octet-stream',
                 'binary/octet-stream')
RETRY_SECONDS = 30*60
TIMEOUT_SECONDS = 300


class FontDownloadError(RuntimeError):
    """The complete font is unavailable; callers should report this instead of drawing an incomplete image."""


def custom_mirror(value):
    """(host, path) of an https address from the settings, or None when it is empty or not a plain https URL."""
    text = str(value or '').strip()
    if not text:
        return None
    try:
        url = URL(text)
    except (ValueError, TypeError):
        return None
    if url.scheme != 'https' or not url.host or url.user or url.password or url.fragment:
        return None
    host = f'https://{url.host}' + (f':{url.port}' if url.explicit_port else '')
    return host, url.raw_path_qs


def verify(body):
    return len(body) == SIZE and hashlib.sha256(body).hexdigest() == SHA256


class FullFont:
    def __init__(self, root, *, proxy=lambda: None, client=None, mirror=lambda: '', download=None, clock=time.monotonic):
        self.root, self.proxy, self.client, self.mirror = root, proxy, client, mirror
        self.download = download or self.fetch
        self.clock = clock
        self.path = root/FILE
        self.checked = None      # path once verified in this process
        self.task = None
        self.retry_at = 0
        self.closed = False

    async def fetch(self, host, path):
        from . import transport
        buffer = io.BytesIO()
        await transport.http_get(host, path, buffer, limits=(SIZE+1024, SIZE+1024), timeout_seconds=TIMEOUT_SECONDS,
                                 proxy=self.proxy(), client=self.client, content_types=CONTENT_TYPES)
        return buffer.getvalue()

    def check_disk(self):
        if not self.path.exists() or self.path.stat().st_size != SIZE:
            return False
        return verify(self.path.read_bytes())

    def write(self, body):
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.font-', dir=self.root)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(body)
            if not self.closed:
                os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def available(self):
        """The verified font file, or None. Checked on disk once per process."""
        if self.checked is not None:
            return self.checked
        try:
            ok = await asyncio.to_thread(self.check_disk)
        except OSError:
            ok = False
        if ok:
            self.checked = self.path
        return self.checked

    async def ensure(self):
        """Return the complete font, waiting for one shared download when needed. No incomplete fallback."""
        found = await self.available()
        if self.closed:
            raise RuntimeError('font downloader closed')
        if found:
            return found
        if self.clock() < self.retry_at:
            raise FontDownloadError('完整中文字体下载失败，请稍后再试，或在设置中填写字体镜像地址')
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self.download_now())
            self.task.add_done_callback(lambda done: done.cancelled() or done.exception())
        try:
            await asyncio.shield(self.task)
        except OSError:
            self.retry_at = self.clock()+RETRY_SECONDS
            raise FontDownloadError('完整中文字体无法保存，请检查插件数据目录的写入权限') from None
        if self.checked is None:
            raise FontDownloadError('完整中文字体下载失败，请稍后再试，或在设置中填写字体镜像地址')
        return self.checked

    def sources(self):
        custom = custom_mirror(self.mirror())
        return ([custom] if custom else [])+list(MIRRORS)

    async def download_now(self):
        """Try each mirror until one serves the pinned file; True when the font is on disk."""
        for host, path in self.sources():
            if self.closed:
                return False
            try:
                body = await self.download(host, path)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
            if not await asyncio.to_thread(verify, body):
                continue
            await asyncio.to_thread(self.write, body)
            self.checked = self.path if not self.closed else None
            return self.checked is not None
        self.retry_at = self.clock()+RETRY_SECONDS
        return False

    async def close(self):
        self.closed = True
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
