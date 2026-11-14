"""The whole plugin behind doubles of the AstrBot SDK and of every network source: commands go in as chat text
through `tarkov_command`, replies come back as text or as lists holding pictures. Not a running AstrBot.

Pictures are not drawn unless a test asks (`self.draw_for_real()`): what would be drawn is recorded in `self.drawn`
as (drawing function name, its arguments), so tests can read the cards.
"""
import copy
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fixtures import atlas_profile
from tarkov_core.names import spelling_variants

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'tarkov_test_plugin'


class Event:
    def __init__(self, text, sender='alice', admin=False, origin='bot:GroupMessage:g1', platform='telegram'):
        self.message_str, self.sender, self.admin = text, sender, admin
        self.unified_msg_origin, self.platform = origin, platform
        self.stopped = False

    def get_sender_id(self): return self.sender
    def get_platform_name(self): return self.platform
    def is_admin(self): return self.admin
    def plain_result(self, text): return text
    def chain_result(self, chain): return chain
    def stop_event(self): self.stopped = True


class ImageStub:
    def __init__(self, data): self.data = data

    @staticmethod
    def fromBytes(data): return ImageStub(data)


class PlainStub:
    def __init__(self, text): self.text = text


class MessageChainStub:
    def __init__(self, chain=None): self.chain = chain or []


def text_of(reply):
    """A reply as one string: its text, with `[picture]` where a picture is."""
    if isinstance(reply, str):
        return reply
    return '\n'.join('[picture]' if isinstance(part, ImageStub) else part.text for part in reply)


class FakeNames:
    """Stands in for NameIndex: a fixed name table per mode path."""
    def __init__(self, table=None, downloaded=True):
        self.table, self.downloaded = table or {}, downloaded
        self.refresh_seconds = 168*3600
        self.lookups = []

    async def close(self): pass
    def needs_refresh(self, mode_path): return not self.downloaded
    def has_index(self, mode_path): return self.downloaded

    async def similar(self, mode_path, name):
        variants = spelling_variants(name)
        return [entry for entry in self.table.get(mode_path, []) if entry[1].lower() in variants][:3]

    async def lookup(self, mode_path, name):
        self.lookups.append((mode_path, name))
        found = [entry for entry in self.table.get(mode_path, []) if entry[1].lower() == name.lower()]
        found.sort(key=lambda entry: (entry[1] != name, entry[0]))
        return found[:6], len(found)


class FakeAtlas:
    """Stands in for Atlas: answers from tables and records the calls. A table value may be an exception to raise,
    or a list of answers used one after another."""
    def __init__(self, error):
        self.error = error
        self.docs, self.searches, self.calls = {}, {}, []
        self.matching_doc = None

    def put(self, aid, mode='pve', **changes):
        self.docs[(aid, mode)] = atlas_profile(mode, accountId=str(aid), **changes)

    @staticmethod
    def answer(value):
        if isinstance(value, list) and value and isinstance(value[0], (dict, BaseException)):
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, BaseException):
            raise value
        return value

    async def lookup(self, aid, mode, **kwargs):
        self.calls.append(('lookup', int(aid), mode))
        doc = self.answer(self.docs.get((int(aid), mode)))
        if doc is None:
            raise self.error(404, 'not_found')
        return copy.deepcopy(doc)

    async def search(self, nickname, mode, live=False, **kwargs):
        self.calls.append(('live search' if live else 'search', nickname, mode))
        found = self.answer(next((value for key, value in self.searches.items() if key.lower() == nickname.lower()), []))
        return {'nickname': nickname, 'players': [{'accountId': str(aid), 'nickname': name} for aid, name in found],
                'cached': False, 'mode': mode}

    async def matching(self):
        self.calls.append(('matching',))
        return copy.deepcopy(self.answer(self.matching_doc))


class PublicResource:
    """Stands in for a cached tarkov.dev resource (resource_cache.CachedResource)."""
    def __init__(self, data, stale=False, checked_at=100):
        self.data, self.stale = data, stale
        self.body = repr(data).encode()
        self.checked_at = self.fetched_at = checked_at
        self.last_modified, self.from_cache, self.error = None, False, '连接失败' if stale else None

    async def json(self):
        return {'data': copy.deepcopy(self.data)}


class PluginCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = []
        api = types.ModuleType('astrbot.api')
        api.AstrBotConfig = dict
        api.logger = types.SimpleNamespace(**{level: (lambda *args: self.logs.append(args)) for level in ('warning', 'info', 'debug')})
        event = types.ModuleType('astrbot.api.event')
        decorator = lambda *args, **kwargs: lambda function: function

        class CustomFilter:
            pass
        event.filter = types.SimpleNamespace(command=decorator, custom_filter=decorator, CustomFilter=CustomFilter)
        event.AstrMessageEvent, event.MessageChain = Event, MessageChainStub
        star = types.ModuleType('astrbot.api.star')

        class Star:
            def __init__(self, context): self.context = context
        star.Star, star.Context, star.register = Star, object, decorator
        star.StarTools = types.SimpleNamespace(get_data_dir=lambda name: self.root)
        components = types.ModuleType('astrbot.api.message_components')
        components.Image, components.Plain = ImageStub, PlainStub
        modules = patch.dict(sys.modules, {'astrbot': types.ModuleType('astrbot'), 'astrbot.api': api, 'astrbot.api.event': event,
                                           'astrbot.api.star': star, 'astrbot.api.message_components': components})
        modules.start()
        self.addCleanup(modules.stop)
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        sys.modules[PACKAGE] = package
        self.addCleanup(lambda: [sys.modules.pop(name) for name in list(sys.modules) if name.startswith(PACKAGE)])
        spec = importlib.util.spec_from_file_location(PACKAGE+'.main', ROOT/'main.py')
        self.main = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE+'.main'] = self.main
        spec.loader.exec_module(self.main)

        from font_support import TEST_FONT
        self.font = AsyncMock(return_value=TEST_FONT)
        self.start(patch.object(self.core('font_download').FullFont, 'ensure', self.font))
        self.drawn = []

        async def record(app, draw, *args):
            await app.prepare_font()
            self.drawn.append((draw.__name__, args))
            return b'picture'
        self.render = self.start(patch.object(self.core('app').App, 'render', record), keep=True)

        # tarkov.dev player snapshots: {(mode path, account id): document}
        self.snapshots, self.fetched = {}, []

        async def fetch_profile(mode_path, aid, **kwargs):
            self.fetched.append((mode_path, aid))
            if isinstance(self.snapshots.get((mode_path, aid)), BaseException):
                raise self.snapshots[(mode_path, aid)]
            if (mode_path, aid) not in self.snapshots:
                raise self.core('transport').NotFound()
            return copy.deepcopy(self.snapshots[(mode_path, aid)])
        self.start(patch.object(self.core('transport'), 'fetch_profile', fetch_profile))

        self.atlas = FakeAtlas(self.core('atlas_client').AtlasHTTPError)
        client = self.core('atlas_client').AtlasClient
        self.start(patch.multiple(client, lookup=lambda _, *args, **kwargs: self.atlas.lookup(*args, **kwargs),
                                  search=lambda _, *args, **kwargs: self.atlas.search(*args, **kwargs),
                                  matching=lambda _: self.atlas.matching()))

        # tarkov.dev public game data: {(resource, mode, language): PublicResource or an exception}
        self.public, self.public_calls = {}, []

        async def public_resource(app, resource, *, mode=None, language=None, fresh=False, allow_stale=False):
            self.public_calls.append((resource, mode, language, fresh))
            found = self.public.get((resource, mode, language))
            if found is None or isinstance(found, BaseException):
                raise found or self.core('transport').QueryError('没有这份数据')
            return found
        self.start(patch.object(self.core('app').App, 'public_resource', public_resource))

    def start(self, patcher, keep=False):
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher if keep else None

    def core(self, name):
        """The module tarkov_core.<name> as the plugin under test loaded it."""
        return sys.modules[f'{PACKAGE}.tarkov_core.{name}']

    def draw_for_real(self):
        self.render.stop()

    def plugin(self, config=None, names=None):
        """A plugin with these settings. The tarkov.dev name list is off unless `names` (a FakeNames) is given."""
        settings = {} if config is None else config
        settings.setdefault('name_index_enabled', names is not None)
        context = types.SimpleNamespace(send_message=AsyncMock(return_value=True), platform_manager=types.SimpleNamespace(
            platform_insts=[types.SimpleNamespace(meta=lambda: types.SimpleNamespace(
                id='bot', name='telegram', support_proactive_message=True))]))
        plugin = self.main.TarkovPlugin(context, settings)
        plugin.snapshot.names = names or FakeNames()
        self.addAsyncCleanup(plugin.terminate)
        return plugin

    async def ask(self, plugin, text, **who):
        """The replies to one chat message, each as text (see text_of)."""
        return [text_of(reply) for reply in await self.replies(plugin, text, **who)]

    async def replies(self, plugin, text, **who):
        event = Event(text, **who)
        found = [reply async for reply in plugin.tarkov_command(event)]
        self.assertTrue(event.stopped)
        return found

    def error(self, status, code='http', retry_after=None):
        return self.core('atlas_client').AtlasHTTPError(status, code, retry_after)
