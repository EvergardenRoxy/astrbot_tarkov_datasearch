"""Replay QQ routing with original code from an AstrBot wheel, without installing all SDK providers.

Usage: python tools/verify_qq_routes.py /path/to/astrbot-4.16.0-py3-none-any.whl
Uses the wheel's registration, wake stage, filters and QQ message parser. External SDK types, plugin-management
services and network providers are boundary doubles. This is not a live AstrBot/QQ delivery test.
"""
import ast
import asyncio
import importlib.util
import json
import sys
import types
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
sys.path.insert(0, str(ROOT))

from fixtures import profile
from test_plugin import Event, ImageStub, PlainStub, PluginTests
from test_status import status_data
from test_boss import boss_data


class At:
    def __init__(self, qq): self.qq = qq


class AtAll: pass
class Reply: pass


class MessageChain:
    def message(self, text): return self


class RouteEvent(Event):
    def __init__(self, abm, platform, private=False):
        super().__init__(abm.message_str)
        self.message_obj = abm
        self.platform_name = platform
        self.components = abm.message
        self.private = private
        self._extras = {}
        self.role = 'member'
        self.is_at_or_wake_command = False
        self.is_wake = False
        self.session_id = 'test-session'

    def get_messages(self): return self.components
    def get_message_str(self): return self.message_str
    def get_self_id(self): return self.message_obj.self_id
    def get_group_id(self): return 'test-group'
    def get_session_id(self): return self.session_id
    def is_private_chat(self): return self.private
    def is_admin(self): return self.role == 'admin'
    def set_extra(self, key, value): self._extras[key] = value
    def get_extra(self, key=None, default=None): return self._extras if key is None else self._extras.get(key, default)
    async def send(self, message): raise AssertionError('Unexpected framework permission/filter error reply')


class SessionPluginManager:
    @staticmethod
    async def filter_handlers_by_session(event, handlers): return handlers


def execute_sdk_file(archive, path, namespace):
    """Execute upstream definitions unchanged, replacing only their non-stdlib import boundaries."""
    tree = ast.parse(archive.read(path), filename=path)
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom)) or
                 (isinstance(node, ast.ImportFrom) and node.level == 0 and
                  node.module.split('.')[0] in sys.stdlib_module_names) or
                 (isinstance(node, ast.Import) and all(alias.name.split('.')[0] in sys.stdlib_module_names
                                                       for alias in node.names))]
    # Type annotations are not the subject of the routing replay.
    tree.body.insert(0, ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0))
    exec(compile(ast.fix_missing_locations(tree), path, 'exec'), namespace)


async def verify(wheel):
    fixture = PluginTests()
    fixture.setUp()
    try:
        sdk = types.ModuleType('_tarkov_sdk_route_replay')
        sys.modules[sdk.__name__] = sdk
        ns = sdk.__dict__
        import time
        ns.update(AstrBotConfig=dict, AstrMessageEvent=RouteEvent, logger=fixture.module.logger,
                  star_map={}, Stage=object, PipelineContext=types.SimpleNamespace,
                  register_stage=lambda cls: cls, At=At, AtAll=AtAll, Reply=Reply,
                  Plain=PlainStub, MessageChain=MessageChain, MessageEventResult=MessageChain,
                  SessionPluginManager=SessionPluginManager,
                  Agent=object, HandoffTool=object, FunctionTool=object, BaseAgentRunHooks=object,
                  AstrAgentContext=object, PY_TO_JSON_TYPE={}, SUPPORTED_TYPES={}, llm_tools=None,
                  time=time, AstrBotMessage=types.SimpleNamespace,
                  MessageMember=lambda user_id, nickname: types.SimpleNamespace(user_id=user_id, nickname=nickname))
        with zipfile.ZipFile(wheel) as archive:
            metadata = next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))
            version = next(line.split(': ', 1)[1] for line in archive.read(metadata).decode().splitlines()
                           if line.startswith('Version: '))
            for path in ('astrbot/core/platform/message_type.py', 'astrbot/core/star/filter/__init__.py',
                         'astrbot/core/star/star_handler.py', 'astrbot/core/star/filter/custom_filter.py',
                         'astrbot/core/star/filter/command.py', 'astrbot/core/star/filter/command_group.py',
                         'astrbot/core/star/filter/permission.py', 'astrbot/core/star/filter/event_message_type.py',
                         'astrbot/core/star/filter/platform_adapter_type.py', 'astrbot/core/star/filter/regex.py',
                         'astrbot/core/star/register/star_handler.py', 'astrbot/core/pipeline/waking_check/stage.py'):
                execute_sdk_file(archive, path, ns)
            # The real adapter parser, with botpy message types and empty attachments replaced at its boundary.
            tree = ast.parse(archive.read('astrbot/core/platform/sources/qqofficial/qqofficial_platform_adapter.py'))
            adapter = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'QQOfficialPlatformAdapter')
            parser = next(node for node in adapter.body if isinstance(node, ast.FunctionDef) and node.name == '_parse_from_qqofficial')
            parser.decorator_list = []
            parser_tree = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), parser],
                                     type_ignores=[])
            for name in ('Message', 'GroupMessage', 'DirectMessage', 'C2CMessage'):
                ns[name] = type(name, (types.SimpleNamespace,), {})
            ns['botpy'] = types.SimpleNamespace(message=types.SimpleNamespace(**{name: ns[name] for name in
                                                     ('Message', 'GroupMessage', 'DirectMessage', 'C2CMessage')}))
            ns['QQOfficialPlatformAdapter'] = types.SimpleNamespace(_append_attachments=lambda msg, attachments: None)
            exec(compile(ast.fix_missing_locations(parser_tree), 'QQOfficialPlatformAdapter._parse_from_qqofficial', 'exec'), ns)

        # Register this plugin through the original command/custom-filter decorators.
        sys.modules['astrbot.api.event'].filter = types.SimpleNamespace(command=ns['register_command'],
            custom_filter=ns['register_custom_filter'], CustomFilter=ns['CustomFilter'])
        sys.modules['astrbot.core.star.star_handler'].star_handlers_registry = ns['star_handlers_registry']
        spec = importlib.util.spec_from_file_location('tarkov_test_plugin.main', ROOT/'main.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        fixture.module = module
        ns['star_map'][module.__name__] = types.SimpleNamespace(activated=True, name='tarkov', reserved=False)
        fixture.profiles[('pve', 7)] = profile(aid=7)

        cases = []
        for platform in ('qq_official', 'qq_official_webhook'):
            for prefix in (['/'], ['!']):
                for private in (False, True):
                    for command in ('/tarkov', '/塔科夫', 'tarkov'):
                        cases.append((platform, prefix, private, command+' pve 7', True, False, {}, True))
        cases += [
            ('qq_official', ['!'], False, '@机器人 /tarkov pve 7', False, False, {}, False),
            ('qq_official', ['/'], False, '/tarkov pve 7', False, True, {}, False),
            ('qq_official', ['!'], False, '/tarkovish pve 7', True, False, {}, False),
            ('qq_official', ['!'], False, 'https://example.com/tarkov', True, False, {}, False),
            ('telegram', ['!'], False, '/tarkov pve 7', True, False, {}, False),
            ('telegram', ['/'], False, '/tarkov pve 7', True, False, {}, True),
            ('qq_official', ['!'], False, '/tarkov pve 7', True, False, {'qq_official_compat_enabled': False}, False),
            ('qq_official', ['/'], False, '/tarkov pve 7', True, False, {'qq_official_compat_enabled': False}, True),
            ('qq_official', ['!'], False, '/tarkov pve 7', True, False, {'access_mode': 'admin_only'}, True),
        ]
        for platform in ('qq_official', 'qq_official_webhook'):
            for prefix in (['/'], ['!']):
                for private in (False, True):
                    for command in ('/status', '/状态', '/tarkov status'):
                        cases.append((platform, prefix, private, command, True, False, {'status_enabled': True}, True))
        cases += [
            ('qq_official', ['!'], False, '/status', True, False, {}, True),  # disabled-feature image
            ('qq_official', ['!'], False, '/status', True, False, {'short_commands_enabled': False}, False),
            ('qq_official', ['!'], False, '/tarkov status', True, False,
                {'short_commands_enabled': False, 'status_enabled': True}, True),
            ('qq_official', ['!'], False, '/status', True, False,
                {'status_enabled': True, 'qq_official_compat_enabled': False}, False),
            ('qq_official', ['/'], False, '/status', True, False,
                {'status_enabled': True, 'qq_official_compat_enabled': False}, True),
            ('qq_official', ['!'], False, '/status', True, False,
                {'status_enabled': True, 'access_mode': 'admin_only'}, True),
            ('qq_official', ['!'], False, '@机器人 /status', False, False, {'status_enabled': True}, False),
            ('telegram', ['!'], False, '/status', True, False, {'status_enabled': True}, False),
            ('telegram', ['/'], False, '/status', True, False, {'status_enabled': True}, True),
        ]
        checks = []
        for platform in ('qq_official', 'qq_official_webhook'):
            for prefix in (['/'], ['!']):
                for private in (False, True):
                    for command in ('/boss', '/刷率', '/tarkov boss pve'):
                        cases.append((platform, prefix, private, command, True, False,
                                      {'boss_enabled': True, 'boss_modes': ['pve']}, True))
        cases += [
            ('qq_official', ['!'], False, '/boss', True, False, {}, True),
            ('qq_official', ['!'], False, '/boss', True, False, {'short_commands_enabled': False}, False),
            ('qq_official', ['!'], False, '/tarkov boss pve', True, False,
                {'short_commands_enabled': False, 'boss_enabled': True}, True),
            ('qq_official', ['!'], False, '/boss', True, False,
                {'boss_enabled': True, 'qq_official_compat_enabled': False}, False),
            ('qq_official', ['/'], False, '/boss', True, False,
                {'boss_enabled': True, 'boss_modes': ['pve'], 'qq_official_compat_enabled': False}, True),
            ('qq_official', ['!'], False, '/boss', True, False,
                {'boss_enabled': True, 'access_mode': 'admin_only'}, True),
            ('qq_official', ['!'], False, '@机器人 /boss', False, False, {'boss_enabled': True}, False),
            ('telegram', ['!'], False, '/boss', True, False, {'boss_enabled': True}, False),
            ('telegram', ['/'], False, '/boss', True, False, {'boss_enabled': True, 'boss_modes': ['pve']}, True),
        ]
        for platform, prefix, private, text, mentioned, wrong_mention, config, should_reply in cases:
            message_type = ns['MessageType'].FRIEND_MESSAGE if private else ns['MessageType'].GROUP_MESSAGE
            message_class = ns['C2CMessage'] if private else ns['GroupMessage']
            raw = message_class(id='source-id', content=text, group_openid='group-openid', attachments=[],
                author=types.SimpleNamespace(user_openid='user-openid', member_openid='user-openid'))
            abm = ns['_parse_from_qqofficial'](raw, message_type)
            if not mentioned:
                abm.message = [At('someone-else'), PlainStub(text)] if wrong_mention else [PlainStub(text)]
            event = RouteEvent(abm, platform, private)
            plugin = fixture.plugin(config)
            plugin.boss_portraits.all = AsyncMock(return_value={})
            stage = ns['WakingCheckStage']()
            await stage.initialize(types.SimpleNamespace(astrbot_config={'platform_settings': {}, 'admins_id': [],
                                                                       'wake_prefix': prefix}))
            await stage.process(event)
            active = event.get_extra('activated_handlers', [])
            assert len(active) <= 1, 'duplicate handler activation'
            before = len(fixture.fetched)
            outputs = []
            try:
                async def public_response(key, **kwargs):
                    data = {} if key.language else boss_data(1) if key.resource == 'maps' else status_data()
                    return {'status': 200}, json.dumps({'data': data}).encode()
                fetch_status = AsyncMock(side_effect=public_response)
                with patch.object(fixture.transport, 'fetch_profile', fixture.fake_fetch), patch.object(module, 'fetch_public', fetch_status):
                    for handler in active:
                        outputs += [result async for result in handler.handler(plugin, event)]
                assert bool(outputs) == should_reply, (platform, prefix, text, config, active, outputs)
                is_status = text.lstrip('/').split()[0] in ('status', '状态') or text.endswith(' status')
                is_boss = text.lstrip('/').split()[0] in ('boss', '刷率') or ' boss' in text
                if config.get('access_mode') == 'admin_only':
                    if is_status or is_boss:
                        assert isinstance(outputs[0][0], ImageStub)
                        fetch_status.assert_not_awaited()
                    else:
                        assert '没有塔科夫查询权限' in outputs[0]
                    assert len(fixture.fetched) == before
                elif outputs:
                    if is_status or is_boss:
                        assert isinstance(outputs[0][0], ImageStub)
                    else:
                        assert '【塔科夫 PVE 档案】' in outputs[0]
                checks.append({'platform': platform, 'wake_prefix': prefix, 'private': private, 'text': text,
                               'config': config, 'handler': [h.handler_name for h in active], 'replied': bool(outputs)})
            finally:
                await plugin.terminate()
        # An opted-out short command must leave another plugin's standard route selectable.
        async def other_status(self, event):
            yield 'other-plugin'
        other_status.__module__ = 'other_status_plugin'
        ns['star_map'][other_status.__module__] = types.SimpleNamespace(activated=True, name='other', reserved=False)
        ns['register_command']('status')(other_status)
        plugin = fixture.plugin({'short_commands_enabled': False})
        try:
            raw = ns['GroupMessage'](id='source-id', content='/status', group_openid='group-openid', attachments=[],
                                    author=types.SimpleNamespace(member_openid='user-openid'))
            abm = ns['_parse_from_qqofficial'](raw, ns['MessageType'].GROUP_MESSAGE)
            event = RouteEvent(abm, 'qq_official')
            stage = ns['WakingCheckStage']()
            await stage.initialize(types.SimpleNamespace(astrbot_config={'platform_settings': {}, 'admins_id': [], 'wake_prefix': ['/']}))
            await stage.process(event)
            active = event.get_extra('activated_handlers', [])
            assert len(active) == 1 and active[0].handler is other_status
            checks.append({'short_command_opt_out_preserves_other_plugin': True})
        finally:
            await plugin.terminate()
        async def other_boss(self, event):
            yield event.plain_result('other Boss handler')
        other_boss.__module__ = 'other_boss_plugin'
        ns['star_map'][other_boss.__module__] = types.SimpleNamespace(activated=True, name='other_boss', reserved=False)
        ns['register_command']('boss')(other_boss)
        plugin = fixture.plugin({'short_commands_enabled': False})
        try:
            raw = ns['GroupMessage'](id='source-id', content='/boss', group_openid='group-openid', attachments=[],
                                    author=types.SimpleNamespace(member_openid='user-openid'))
            event = RouteEvent(ns['_parse_from_qqofficial'](raw, ns['MessageType'].GROUP_MESSAGE), 'qq_official')
            stage = ns['WakingCheckStage']()
            await stage.initialize(types.SimpleNamespace(astrbot_config={'platform_settings': {}, 'admins_id': [], 'wake_prefix': ['/']}))
            await stage.process(event)
            active = event.get_extra('activated_handlers', [])
            assert len(active) == 1 and active[0].handler is other_boss
            checks.append({'short_boss_opt_out_preserves_other_plugin': True})
        finally:
            await plugin.terminate()
        print(json.dumps({'astrbot_version': version, 'passed': len(checks), 'boundary_doubles': True,
                          'live_qq_delivery': False, 'checks': checks}, ensure_ascii=False, indent=2))
    finally:
        fixture.tearDown()
        sys.modules.pop('_tarkov_sdk_route_replay', None)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    asyncio.run(verify(Path(sys.argv[1])))
