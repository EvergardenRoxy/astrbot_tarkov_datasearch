"""`tarkov boss`: the Boss spawn table of each map as pictures, a personal default mode, and pushes on changes."""
import asyncio
import re

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image

from .boss_monitor import BOSS_SCOPE, build_table, normalize_bosses
from .boss_notifications import BossNotifications, BossStore
from .boss_portraits import BossPortraits
from .boss_table_image import paginate_table, render_boss_page
from .modes import GAME_MODES, label_of, parse_mode
from .notifications import PermanentDeliveryError
from .polling import PollingTask
from .public_api import LANGUAGES
from .status_commands import push_error
from .transport import QueryError

PAGE_WORDS = ('页', 'page')
DEFAULT_WORDS = ('默认', 'default')
CHANGE_WORDS = ('变化', 'changes')
TOKEN = re.compile('[0-9a-f]{12}')
PAGE_NUMBER = re.compile('[0-9]{1,4}')


def boss_mode(words):
    """The mode of the words after `boss`: the usual mode words, and "pvp s" / "pvps" for the season."""
    text = ' '.join(words)
    return 'season' if text in ('pvp s', 'pvps') else parse_mode(text)


class BossCommands:
    def __init__(self, app, push):
        self.app, self.push = app, push
        self.on_error = push_error('Boss')
        self.store = BossStore(app.root/'boss-notifications.sqlite3')
        # Part of the stored scope: changing the ignore list starts new baselines instead of reporting "changes".
        self.store.scope = BOSS_SCOPE+':'+','.join(sorted(self.ignored()))
        self.portraits = BossPortraits(app.root/'boss-portraits', app.http, app.proxy)
        # mode -> (what the state was built from, the normalized state)
        self.normalized = {}
        self.notifications = BossNotifications(self.store, self.snapshot_for_monitor, self.draw, self.send,
                                               self.modes, self.targets, self.broadcasting, self.on_error)
        self.poller = PollingTask(self.notifications.cycle, self.broadcasting,
                                  lambda: app.config.int('boss_poll_minutes')*60, self.on_error)

    # ---- settings ----

    def enabled(self):
        return self.app.config.flag('boss_enabled')

    def ignored(self):
        """normalizedName values of mobs never shown as event bosses (lower-cased)."""
        return {text.lower()[:64] for text in self.app.config.strings('boss_event_ignore') or []}

    def modes(self):
        """The modes watched, and queried when a query names none."""
        values = self.app.config.get('boss_modes', list(GAME_MODES))
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(mode for mode in map(parse_mode, values) if mode in GAME_MODES))

    def targets(self):
        return self.push.configured('boss_notify_targets', 'boss_qq_push_enabled')

    def broadcasting(self):
        return (not self.app.terminated and self.enabled() and bool(self.modes())
                and self.app.config.flag('boss_broadcast_enabled') and bool(self.targets()))

    async def start(self):
        if self.app.config.consume('boss_portraits_update'):
            count = await self.portraits.refresh()
            logger.info('Tarkov Boss portraits updated: %s/12', count)
        if self.broadcasting():
            await self.store.thread(self.store.reconcile, self.modes(), self.targets())
            self.poller.reconfigure()
            return
        await self.poller.close()
        await self.store.thread(self.store.retire)
        if self.enabled() and self.app.config.flag('boss_broadcast_enabled'):
            logger.info('Tarkov Boss pushes idle: no usable mode or target; check the settings and the platform')

    # ---- data and pictures ----

    async def snapshot(self, mode, *, fresh=False, allow_stale=False):
        """The Boss card of a mode: the normalized spawn state with when it was checked. The translation
        dictionaries may be older copies or missing (`translation_warning`); the spawn data decides `stale`."""
        maps = await self.app.public_resource('maps', mode=mode, fresh=fresh, allow_stale=allow_stale)
        found = await asyncio.gather(*(self.app.public_resource('maps', mode=mode, language=language, allow_stale=True)
                                       for language in LANGUAGES), return_exceptions=True)
        for result in found:
            if isinstance(result, asyncio.CancelledError):
                raise result
        dictionaries = [result for result in found if not isinstance(result, BaseException)]
        warning = len(dictionaries) < len(found) or any(dictionary.stale for dictionary in dictionaries)
        # Normalizing the maps of a mode takes a while; it is done again only when one of its inputs changed.
        signature = (hash(maps.body), *(hash(dictionary.body) for dictionary in dictionaries), tuple(sorted(self.ignored())))
        cached = self.normalized.get(mode)
        if cached and cached[0] == signature:
            state = cached[1]
        else:
            words = [(await dictionary.json())['data'] for dictionary in dictionaries]
            state = await asyncio.to_thread(normalize_bosses, (await maps.json())['data'], words, self.ignored())
            self.normalized[mode] = (signature, state)
        return {'mode': mode, 'state': state, 'checked_at': maps.checked_at, 'fetched_at': maps.fetched_at,
                'last_modified': maps.last_modified, 'stale': maps.stale, 'from_cache': maps.from_cache,
                'translation_warning': warning}

    async def snapshot_for_monitor(self, mode):
        # The monitor splits the table into pages by measuring text, which needs the font the picture uses.
        await self.app.prepare_font()
        return await self.snapshot(mode, fresh=True)

    async def draw(self, page, index=1, total=1, token=None):
        """One page as a picture. `token` names the stored pages, so the picture can say how to ask for the next."""
        token = page.get('snapshot_token', '') if token is None else token
        following = self.app.cmd('boss', '页', token, index+1 if index < total else 1) if token else ''
        portraits = await self.portraits.all() if not page.get('error') else {}
        return await self.app.render(render_boss_page, page, token, index, total, following, portraits)

    async def picture(self, event, page, index=1, total=1, token=None):
        try:
            return event.chain_result([Image.fromBytes(await self.draw(page, index, total, token))])
        except Exception as exc:
            return event.plain_result(self.app.render_failure(exc, 'Boss 表格'))

    async def pages_of(self, mode):
        card = await self.snapshot(mode, allow_stale=True)
        rows, _ = await asyncio.to_thread(build_table, None, card['state'])
        await self.app.prepare_font()
        return await asyncio.to_thread(paginate_table, {key: value for key, value in card.items() if key != 'state'}, rows)

    # ---- the command ----

    async def query(self, event, arguments):
        """`boss [模式] [变化]`, `boss 默认 <模式>`, `boss 页 <快照编号> <页码>`."""
        if not self.enabled():
            return event.plain_result('Boss 查询尚未开启，请联系管理员在设置中启用。')
        words = arguments.lower().split()
        with self.app.busy(event):
            try:
                if words and words[0] in PAGE_WORDS:
                    return await self.stored_page(event, words[1:])
                if words and words[0] in DEFAULT_WORDS:
                    return await self.set_default(event, words[1:])
                return await self.table(event, words)
            except asyncio.CancelledError:
                raise
            except QueryError as exc:
                return event.plain_result(str(exc))
            except Exception as exc:
                self.on_error(exc)
                return event.plain_result('本次表格查询失败，请稍后再试。')

    async def stored_page(self, event, words):
        if len(words) != 2 or not TOKEN.fullmatch(words[0]) or not PAGE_NUMBER.fullmatch(words[1]):
            raise QueryError('续查用法：'+self.app.cmd('boss 页 <快照编号> <页码>'))
        pages = await self.store.thread(self.store.query, words[0])
        index = int(words[1])
        if not pages:
            raise QueryError('表格快照已过期或被清理，请重新查询。')
        if not 1 <= index <= len(pages):
            raise QueryError(f'页码超出范围，此快照共 {len(pages)} 页。')
        return await self.picture(event, pages[index-1], index, len(pages), words[0])

    async def set_default(self, event, words):
        mode = boss_mode(words)
        if mode not in GAME_MODES:
            raise QueryError('请选择 pvp、pve 或赛季模式。用法：'+self.app.cmd('boss 默认 <模式>'))
        await asyncio.to_thread(self.app.accounts.set_boss_mode, self.app.binding_key(event), mode)
        return event.plain_result(f'Boss 默认模式已设为 {label_of(mode)}。之后发送 '+self.app.cmd('boss')+' 即可查询。')

    async def table(self, event, words):
        changes = any(word in CHANGE_WORDS for word in words)
        words = [word for word in words if word not in CHANGE_WORDS]
        if words:
            modes = [mode for mode in [boss_mode(words)] if mode in GAME_MODES]
        else:
            preferred = await asyncio.to_thread(self.app.accounts.boss_mode, self.app.binding_key(event))
            modes = [preferred] if preferred else self.modes()
        if not modes:
            raise QueryError('请选择 pvp、pve 或赛季模式。用法：'+self.app.cmd('boss [模式] [变化]'))
        pages = []
        for mode in modes:
            # A mode that fails becomes an error page; the other modes are still shown.
            try:
                if changes:
                    latest = await self.store.thread(self.store.latest, mode)
                    if not latest:
                        raise QueryError('尚无已记录的刷率变化；开启监控后首次成功只建立基线。')
                    pages.extend(latest)
                else:
                    pages.extend(await self.pages_of(mode))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.on_error(exc)
                why = str(exc) if isinstance(exc, QueryError) else '本次数据获取失败，请稍后再试。'
                pages.append({'mode': mode, 'error': why+' 此结果不表示刷率为零。'})
        token = await self.store.thread(self.store.save_query, pages)
        return await self.picture(event, pages[0], 1, len(pages), token)

    async def send(self, target, picture):
        if not self.broadcasting() or target not in self.targets():
            raise PermanentDeliveryError('目标不再允许 Boss 播报')
        return await self.app.context.send_message(target, MessageChain(chain=[Image.fromBytes(picture)]))

    async def close(self):
        await self.poller.close()
        await self.store.close()
        self.normalized.clear()
