"""`tarkov status`: the game's server status as a picture, and pushes when it changes."""
import asyncio

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image

from .notifications import NotificationStore, PermanentDeliveryError, StatusNotifications
from .polling import PollingTask
from .server_status import normalize_status
from .status_image import render_status_image
from .transport import QueryError


def push_error(what):
    """The poller's error callback of a feature: a refused target is said once, anything else by its type."""
    def report(exc):
        if isinstance(exc, (PermanentDeliveryError, PermissionError)):
            logger.warning('Tarkov %s push target refused; check the platform permissions, then switch the pushes off and on', what)
        else:
            logger.warning('Tarkov %s monitor failed: %s', what, type(exc).__name__)
    return report


class StatusCommands:
    def __init__(self, app, push):
        self.app, self.push = app, push
        self.on_error = push_error('status')
        self.store = NotificationStore(app.root/'notifications.sqlite3')
        self.notifications = StatusNotifications(self.store, lambda: self.snapshot(fresh=True), self.draw, self.send,
                                                 self.targets, self.broadcasting, self.on_error)
        self.poller = PollingTask(self.notifications.cycle, self.broadcasting,
                                  lambda: app.config.int('status_poll_minutes')*60, self.on_error)

    def enabled(self):
        return self.app.config.flag('status_enabled')

    def targets(self):
        return self.push.configured('status_notify_targets', 'status_qq_push_enabled')

    def broadcasting(self):
        return (not self.app.terminated and self.enabled() and self.app.config.flag('status_broadcast_enabled')
                and bool(self.targets()))

    async def start(self):
        if self.broadcasting():
            self.poller.reconfigure()
            return
        await self.poller.close()
        if self.enabled() and self.app.config.flag('status_broadcast_enabled'):
            logger.info('Tarkov status pushes idle: no usable target; check the chats, the platform and the QQ push setting')
        # Monitoring that was off starts from a new silent baseline when it is switched on again.
        await self.store.thread(self.store.retire)

    async def snapshot(self, *, fresh=False, allow_stale=False):
        """The status card: the normalized state with when it was checked and whether it is an older copy."""
        snapshot = await self.app.public_resource('status', fresh=fresh, allow_stale=allow_stale)
        state = normalize_status((await snapshot.json())['data'])
        return {'state': state, 'checked_at': snapshot.checked_at, 'fetched_at': snapshot.fetched_at,
                'last_modified': snapshot.last_modified, 'from_cache': snapshot.from_cache, 'stale': snapshot.stale,
                'error': ('连接失败，展示上次成功数据：'+snapshot.error) if snapshot.stale else None}

    async def draw(self, card):
        return await self.app.render(render_status_image, card)

    async def query(self, event, arguments):
        if not self.enabled():
            return event.plain_result('服务器状态查询尚未开启，请联系管理员在设置中启用。')
        if arguments:
            return event.plain_result('状态查询不需要参数。用法：'+self.app.cmd('status'))
        with self.app.busy(event):
            try:
                card = await self.snapshot(allow_stale=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.on_error(exc)
                why = str(exc) if isinstance(exc, QueryError) else '连接超时，请稍后再试。' if isinstance(exc, TimeoutError) else '本次获取失败，请稍后再试。'
                return event.plain_result(f'无法取得服务器状态：{why} 此结果不表示游戏停服。')
            try:
                return event.chain_result([Image.fromBytes(await self.draw(card))])
            except Exception as exc:
                return event.plain_result(self.app.render_failure(exc, '服务器状态'))

    async def send(self, target, picture):
        if not self.broadcasting() or target not in self.targets():
            raise PermanentDeliveryError('目标不再允许播报')
        return await self.app.context.send_message(target, MessageChain(chain=[Image.fromBytes(picture)]))

    async def close(self):
        await self.poller.close()
        await self.store.close()
