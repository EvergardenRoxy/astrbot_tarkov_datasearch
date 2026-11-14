"""`tarkov 匹配`: Atlas' average matching time per datacenter as one picture shared by every chat."""
import asyncio

from astrbot.api import logger
from astrbot.api.message_components import Image, Plain

from .matching import MatchingService
from .matching_image import ATLAS_NAME, ATLAS_URL, render_matching_image
from .transport import QueryError

SOURCE_LINE = f'数据来源：{ATLAS_NAME} {ATLAS_URL}'


class MatchingCommands:
    def __init__(self, app):
        self.app = app
        self.service = MatchingService(app.atlas)

    def enabled(self):
        return self.app.config.flag('matching_enabled')

    async def query(self, event, arguments):
        if not self.enabled():
            return event.plain_result('匹配时间查询尚未开启，请联系管理员在设置中启用。')
        if arguments:
            return event.plain_result('匹配时间查询不需要参数。用法：'+self.app.cmd('匹配'))
        with self.app.busy(event):
            try:
                result = await self.service.get()
            except asyncio.CancelledError:
                raise
            except QueryError as exc:
                return event.plain_result(f'匹配时间查询失败：{exc}')
            except Exception as exc:
                logger.warning('Tarkov matching query failed: %s', type(exc).__name__)
                return event.plain_result('匹配时间查询失败，请稍后再试。')
            try:
                picture = await self.app.render(render_matching_image, result)
            except Exception as exc:
                return event.plain_result(self.app.render_failure(exc, '匹配时间'))
            # The picture cannot carry a clickable link; the text line does.
            return event.chain_result([Image.fromBytes(picture), Plain(SOURCE_LINE)])

    async def close(self):
        await self.service.close()
