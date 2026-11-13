"""What every feature of the plugin shares: settings, the data directory, HTTP, accounts, Atlas, picture drawing."""
import asyncio
import hashlib
import hmac
from contextlib import contextmanager

from astrbot.api import logger

from . import COMMAND_STYLES, transport
from .accounts import Accounts, load_secret, owner_key
from .atlas_client import AtlasClient
from .atlas_cooldown import AtlasBudget, AtlasCooldown
from .atlas_players import AtlasPlayers
from .font_download import FontDownloadError, FullFont
from .fonts import use_chinese_font
from .modes import GAME_MODES, parse_mode
from .public_api import ResourceKey, fetch as fetch_public, mode_path as public_mode_path
from .resource_cache import ResourceCache
from .transport import QueryError

PLAYER_SOURCES = ('atlas', 'tarkov-dev')
# Pictures drawn at once, for all features together (drawing is CPU work in threads).
DRAWINGS_AT_ONCE = 2
DENIED = '此会话或用户没有塔科夫查询权限。'


class Busy(QueryError):
    def __init__(self):
        super().__init__('你的上一次查询还没完成，请稍候。')


class App:
    def __init__(self, context, config, root):
        self.context, self.config, self.root = context, config, root
        root.mkdir(parents=True, exist_ok=True)
        self.terminated = False
        self.http = transport.HTTPClient()
        self.accounts = Accounts(root/'accounts.sqlite3')
        # Keys the binding keys and the log trace ids, so neither can be turned back into a user id.
        self.secret = load_secret(root/'binding.key')
        self.full_font = FullFont(root/'fonts', proxy=self.proxy, client=self.http,
                                  mirror=lambda: config.get('font_download_url', ''))
        self.drawings = asyncio.Semaphore(DRAWINGS_AT_ONCE)
        self.atlas = AtlasClient(self.http, proxy=self.proxy,
                                 cooldown=AtlasCooldown(config.int('atlas_query_cooldown_seconds')),
                                 budget=AtlasBudget(lambda: config.int('atlas_budget_per_minute'),
                                                    lambda: config.int('atlas_budget_per_hour')))
        self.atlas_players = AtlasPlayers(self.atlas, cache_seconds=lambda: config.int('profile_cache_minutes')*60)
        self.public_data = ResourceCache(root/'public-data', self.download_public)
        # Users with a command in progress (the keyed hash of chat and sender).
        self.active = set()

    # ---- settings every feature reads ----

    def style(self):
        """The wording of the "command_style" setting (unknown values: the default)."""
        return COMMAND_STYLES.get(str(self.config.get('command_style', 'mention')), COMMAND_STYLES['mention'])

    def cmd(self, *words):
        """A command as the user should type it: the style's command, then `words` (cmd('绑定') is the bind command)."""
        return ' '.join([self.style()['command'], *map(str, words)])

    def proxy(self):
        if not self.config.flag('proxy_enabled'):
            return None
        url = str(self.config.get('proxy_url', '')).strip()
        if not url:
            # An empty address must not turn into a direct connection.
            raise QueryError('已开启“访问 tarkov.dev 时使用代理”，但没有填写代理地址，请在插件配置中填写或关闭代理')
        return url

    def default_mode(self):
        mode = parse_mode(self.config.get('default_mode', 'pve'))
        return mode if mode in GAME_MODES else 'pve'

    def player_source(self):
        """'atlas' (live lookups, tarkov.dev snapshots as the fallback) or 'tarkov-dev' (snapshots only)."""
        value = str(self.config.get('player_source', 'atlas')).strip().lower()
        return value if value in PLAYER_SOURCES else 'atlas'

    def timeout(self):
        return self.config.int('download_timeout_seconds')

    # ---- who is asking ----

    def allowed(self, event):
        chats = self.config.strings('allowed_origins')
        if chats is None:
            logger.warning('Tarkov setting allowed_origins is not a list; access denied')
            return False
        if chats and event.unified_msg_origin not in chats:
            return False
        mode = self.config.get('access_mode', 'all')
        if mode == 'admin_only':
            return event.is_admin()
        if mode == 'admin_and_whitelist':
            return event.is_admin() or str(event.get_sender_id()) in (self.config.strings('user_whitelist') or [])
        return mode == 'all'

    def owner(self, event):
        """One sender in one chat, as a keyed hash: who a command in progress belongs to, and the quota's key."""
        return hmac.new(self.secret, (event.unified_msg_origin+'\0'+event.get_sender_id()).encode(), hashlib.sha256).hexdigest()

    def binding_key(self, event):
        # unified_msg_origin is "<platform id>:<message type>:<session>"; a binding holds in every chat of a platform.
        return owner_key(self.secret, event.unified_msg_origin.split(':', 1)[0], event.get_sender_id())

    @contextmanager
    def busy(self, event):
        """One command per sender at a time: raises Busy while an earlier one is still running."""
        owner = self.owner(event)
        if owner in self.active:
            raise Busy()
        self.active.add(owner)
        try:
            yield owner
        finally:
            self.active.discard(owner)

    # ---- pictures ----

    async def prepare_font(self):
        """Every picture, and every text measurement for one, waits for the complete Chinese font (downloaded once)."""
        use_chinese_font(await self.full_font.ensure())

    async def render(self, draw, *args):
        """JPEG bytes from `draw(*args)`, run in a thread once the font is ready."""
        await self.prepare_font()
        async with self.drawings:
            return await asyncio.to_thread(draw, *args)

    @staticmethod
    def render_failure(exc, what):
        """The one text line sent instead of a picture that could not be drawn."""
        logger.warning('Tarkov %s picture failed: %s', what, type(exc).__name__)
        return str(exc) if isinstance(exc, FontDownloadError) else f'{what}图片生成失败，请稍后再试。'

    # ---- tarkov.dev public game data ----

    async def download_public(self, key, *, etag=None, last_modified=None):
        return await fetch_public(key, etag=etag, last_modified=last_modified, timeout_seconds=min(60, self.timeout()),
                                  proxy=self.proxy(), client=self.http, validate=False)

    async def public_resource(self, resource, *, mode=None, language=None, fresh=False, allow_stale=False):
        key = ResourceKey(resource, public_mode_path(mode) if mode is not None else None, language)
        return await self.public_data.get(key, ttl_seconds=self.config.int('public_data_cache_minutes')*60,
                                          fresh=fresh, allow_stale=allow_stale)

    async def public_feed(self, resource, mode):
        """The parsed `data` of a public resource (an older copy when tarkov.dev cannot be reached)."""
        snapshot = await self.public_resource(resource, mode=mode, allow_stale=True)
        return (await snapshot.json())['data']

    async def close(self):
        self.terminated = True
        await self.atlas_players.close()
        await self.full_font.close()
        await self.atlas.close()
        await self.public_data.close()
        await self.http.close()
