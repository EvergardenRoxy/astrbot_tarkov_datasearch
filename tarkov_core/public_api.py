"""Read-only JSON game resources; separate paths/limits from player profile snapshots."""
import asyncio
import io
import json
import re
from dataclasses import dataclass

from . import transport
from .transport import QueryError

HOST = 'https://json.tarkov.dev'
RESOURCE_LIMITS = (32*1024*1024, 64*1024*1024)
MODES = frozenset(('regular', 'pve', 'pvp-season'))
MODE_NAMES = {'pvp': 'regular', 'pve': 'pve', 'season': 'pvp-season'}
LOCALIZED = frozenset(('items', 'maps', 'tasks', 'hideout', 'traders', 'season'))
RESOURCES = LOCALIZED | {'endpoints', 'status', 'barters', 'crafts', 'prices'}
LANGUAGES = frozenset('cs de en es fr hu id it ja ko pl pt ro ru sk th tr vn zh'.split())
ITEM_ID = re.compile(r'[0-9a-f]{24}')


class DataError(QueryError):
    """An unusable upstream document; an older valid resource may still be shown explicitly."""


def mode_path(mode):
    """Explicitly translate plugin mode keys; the player's 'profile' path is never accepted here."""
    try:
        return MODE_NAMES[mode]
    except (KeyError, TypeError):
        raise ValueError('公共数据仅支持 pvp、pve、season 模式') from None


@dataclass(frozen=True)
class ResourceKey:
    resource: str
    mode: str | None = None
    language: str | None = None
    item_id: str | None = None

    @property
    def path(self):
        if self.resource not in RESOURCES:
            raise ValueError('不支持的公共数据资源')
        if self.language is not None and (self.resource not in LOCALIZED or self.language not in LANGUAGES):
            raise ValueError('该资源或语言不支持翻译字典')
        if self.resource in ('endpoints', 'status'):
            if self.mode is not None:
                raise ValueError('全局资源不接受模式')
            path = '/'+self.resource
        elif self.resource == 'season':
            if self.mode not in (None, 'pvp-season'):
                raise ValueError('赛季信息仅支持 pvp-season')
            path = '/pvp-season/season'
        else:
            if self.mode not in MODES:
                raise ValueError('请使用 regular、pve 或 pvp-season 公共数据路径')
            path = f'/{self.mode}/{self.resource}'
        if self.resource == 'prices':
            if not isinstance(self.item_id, str) or not ITEM_ID.fullmatch(self.item_id):
                raise ValueError('历史价格需要有效的物品 ID')
            path += '/'+self.item_id
        elif self.item_id is not None:
            raise ValueError('只有历史价格资源接受物品 ID')
        return path+('_'+self.language if self.language else '')


def _invalid_constant(value):
    raise ValueError('non-finite JSON constant')


def decode(key, body):
    """Validate the JSON envelope and primary resource shape off the event loop."""
    if len(body) > RESOURCE_LIMITS[1]:
        raise DataError('tarkov.dev 公共数据超过大小上限')
    try:
        document = json.loads(body, parse_constant=_invalid_constant)
        if not isinstance(document, dict) or 'data' not in document:
            raise ValueError()
        data = document['data']
        paths = document.get('translations', [])
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise ValueError()
        if key.language:
            valid = isinstance(data, dict) and all(isinstance(value, str) for value in data.values())
        elif key.resource in ('barters', 'crafts', 'prices'):
            valid = isinstance(data, list) and all(isinstance(row, dict) for row in data)
        else:
            required = {'items': {'items': dict}, 'maps': {'maps': dict, 'mobs': dict},
                        'tasks': {'tasks': dict}, 'status': {'generalStatus': dict, 'currentStatuses': list, 'messages': list},
                        'endpoints': {'endpoints': list, 'gameModes': list, 'languages': list},
                        'season': {'id': str, 'perks': dict}}.get(key.resource, {})
            valid = isinstance(data, dict) and all(isinstance(data.get(name), kind) for name, kind in required.items())
        if not valid:
            raise ValueError()
        if key.resource == 'status':
            from .server_status import normalize_status
            normalize_status(data)
        if key.resource == 'maps' and not key.language:
            from .boss_monitor import normalize_bosses
            normalize_bosses(data)
    except (ValueError, UnicodeError, RecursionError, QueryError):
        raise DataError('tarkov.dev 返回的公共数据格式无法识别') from None
    return document


async def fetch(key, *, etag=None, last_modified=None, timeout_seconds=60, proxy=None):
    path = key.path  # Validate before opening a connection, including any proxy connection.
    buffer = io.BytesIO()
    metadata = await transport.http_get(HOST, path, buffer, limits=RESOURCE_LIMITS,
                                        timeout_seconds=timeout_seconds, proxy=proxy,
                                        etag=etag, last_modified=last_modified)
    body = buffer.getvalue() if metadata['status'] == 200 else None
    if body is not None:
        await asyncio.to_thread(decode, key, body)
    return metadata, body
