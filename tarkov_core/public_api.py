"""tarkov.dev's public game data (json.tarkov.dev): server status, maps with their bosses, tasks and items."""
import asyncio
import io
import json
from dataclasses import dataclass

from . import transport
from .transport import QueryError

HOST = 'https://json.tarkov.dev'
RESOURCE_LIMITS = (32*1024*1024, 64*1024*1024)
MODE_NAMES = {'pvp': 'regular', 'pve': 'pve', 'season': 'pvp-season'}
# What the plugin reads, and the top-level fields each document must have.
RESOURCES = {'status': {'generalStatus': dict, 'currentStatuses': list, 'messages': list},
             'maps': {'maps': dict, 'mobs': dict}, 'tasks': {'tasks': dict}, 'items': {'items': dict}}
# The translation dictionaries of `maps` the Boss table reads.
LANGUAGES = ('zh', 'en')


class DataError(QueryError):
    """An unusable upstream document; an older valid resource may still be shown explicitly."""


def mode_path(mode):
    """The public data path of a plugin mode key (the players' 'profile' path is another host's)."""
    try:
        return MODE_NAMES[mode]
    except (KeyError, TypeError):
        raise ValueError('公共数据仅支持 pvp、pve、season 模式') from None


@dataclass(frozen=True)
class ResourceKey:
    resource: str
    mode: str | None = None
    language: str | None = None

    @property
    def path(self):
        if self.resource not in RESOURCES:
            raise ValueError('不支持的公共数据资源')
        if self.resource == 'status':
            if self.mode is not None or self.language is not None:
                raise ValueError('服务器状态不分模式和语言')
            return '/status'
        if self.mode not in MODE_NAMES.values():
            raise ValueError('请使用 regular、pve 或 pvp-season 公共数据路径')
        if self.language is not None and self.language not in LANGUAGES:
            raise ValueError('该资源或语言不支持翻译字典')
        return f'/{self.mode}/{self.resource}'+('_'+self.language if self.language else '')


def decode(key, body):
    """The parsed document, checked for the fields its readers rely on (run off the event loop)."""
    try:
        document = json.loads(body)
        data = document['data']
        if key.language:
            valid = isinstance(data, dict) and all(isinstance(value, str) for value in data.values())
        else:
            valid = isinstance(data, dict) and all(isinstance(data.get(name), kind)
                                                   for name, kind in RESOURCES[key.resource].items())
        if not valid:
            raise ValueError()
        # A document the features cannot normalize must not replace the last good one in the cache.
        if key.resource == 'status':
            from .server_status import normalize_status
            normalize_status(data)
        if key.resource == 'maps' and not key.language:
            from .boss_monitor import normalize_bosses
            normalize_bosses(data)
    except (ValueError, TypeError, KeyError, RecursionError, QueryError):
        raise DataError('tarkov.dev 返回的公共数据格式无法识别') from None
    return document


async def fetch(key, *, client, etag=None, last_modified=None, timeout_seconds=60, proxy=None, validate=True):
    """(metadata, body) of one resource; body is None for a 304."""
    buffer = io.BytesIO()
    metadata = await transport.http_get(HOST, key.path, buffer, limits=RESOURCE_LIMITS, timeout_seconds=timeout_seconds,
                                        proxy=proxy, etag=etag, last_modified=last_modified, client=client)
    body = buffer.getvalue() if metadata['status'] == 200 else None
    if body is not None and validate:
        await asyncio.to_thread(decode, key, body)
    return metadata, body
