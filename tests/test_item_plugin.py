import asyncio
import copy
import io
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

import test_plugin as boundary
from test_items import EN, ITEM_A, ITEM_B, ZH, bundle, item_data


class ItemPluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.boundary = boundary.PluginTests(); self.boundary.setUp()
        self.module,self.plugins = self.boundary.module,[]

    async def asyncTearDown(self):
        for plugin in self.plugins: await plugin.terminate()
        self.boundary.tearDown()

    def plugin(self,config=None):
        plugin = self.boundary.plugin(config); self.plugins.append(plugin)
        plugin.item_icons.get = AsyncMock(return_value=None)
        plugin.load_item_sources = AsyncMock(side_effect=lambda mode:bundle(mode,price={'pvp':100,'pve':500,'season':900}[mode]))
        plugin.item_context = AsyncMock(return_value={})
        return plugin

    async def capture(self,plugin,arguments):
        captured = []
        def render(page,*args): captured.append(page); return b'image'
        with patch.object(self.module,'render_item_page',render):
            result = await plugin.item_query(boundary.Event('/item'),arguments)
        self.assertIsInstance(result[0],boundary.ImageStub)
        return captured[0]

    async def test_disabled_denied_and_subfeatures_off_are_images_without_downloads(self):
        for config,arguments,expected in [({},'Med Kit','尚未开启'),
            ({'items_enabled':True,'access_mode':'admin_only'},'Med Kit','没有塔科夫查询权限'),
            ({'items_enabled':True,'item_details_enabled':False},'Med Kit','通用属性查询已关闭'),
            ({'items_enabled':True,'item_details_enabled':False,'item_flea_enabled':False,'item_trader_enabled':False},'pve Med Kit','均已关闭')]:
            plugin = self.plugin(config)
            card = await self.capture(plugin,arguments)
            self.assertIn(expected,' '.join(line for row in card['rows'] for line in row['lines']))
            plugin.load_item_sources.assert_not_awaited()
            self.assertFalse(plugin.item_catalog.path.exists())

    async def test_common_and_explicit_mode_have_separate_data_and_ignore_player_default(self):
        plugin = self.plugin({'items_enabled':True,'default_mode':'pve'})
        common = await self.capture(plugin,'Med Kit')
        text = ' '.join(line for row in common['rows'] for line in row['lines'])
        self.assertIn('治疗总量：400',text)
        self.assertFalse(any(word in text for word in ('RUB','跳蚤','卖给商人')))
        self.assertEqual({call.args[0] for call in plugin.load_item_sources.await_args_list},{'pvp','pve','season'})
        mode = await self.capture(plugin,'pve Med Kit')
        text = ' '.join(line for row in mode['rows'] for line in row['lines'])
        self.assertIn('500 RUB',text); self.assertNotIn('900 RUB',text)
        self.assertNotIn('治疗总量',text)
        plugin.item_context.assert_awaited_once()

    async def test_prices_only_loads_one_mode_and_trader_disabled_never_loads_references(self):
        plugin = self.plugin({'items_enabled':True,'item_details_enabled':False,'item_trader_enabled':False})
        card = await self.capture(plugin,'pve Med Kit')
        self.assertEqual([call.args[0] for call in plugin.load_item_sources.await_args_list],['pve'])
        plugin.item_context.assert_not_awaited()
        self.assertFalse(any(row['section']=='从商人购买' for row in card['rows']))

    async def test_partial_mode_failure_preserves_selected_price_without_false_common_claim(self):
        plugin = self.plugin({'items_enabled':True})
        async def load(mode):
            if mode == 'season': raise TimeoutError()
            return bundle(mode,price=500 if mode=='pve' else 100)
        plugin.load_item_sources = AsyncMock(side_effect=load)
        common = await self.capture(plugin,'Med Kit')
        self.assertFalse(any('治疗总量' in line for row in common['rows'] for line in row['lines']))
        mode = await self.capture(plugin,'pve Med Kit')
        self.assertTrue(any('500 RUB' in line for row in mode['rows'] for line in row['lines']))
        self.assertTrue(any('获取失败的模式' in note for note in mode['notes']))
        unavailable = await self.capture(plugin,'season Med Kit')
        self.assertIn('PVP 赛季 物品数据获取失败',' '.join(line for row in unavailable['rows'] for line in row['lines']))

    async def test_ambiguous_names_candidates_and_id_selection_are_images(self):
        plugin = self.plugin({'items_enabled':True})
        plugin.load_item_sources = AsyncMock(side_effect=lambda mode:bundle(mode,data=item_data(second=True)))
        candidates = await self.capture(plugin,'Med Kit')
        text = ' '.join(line for row in candidates['rows'] for line in row['lines'])
        self.assertIn('匹配 2 件',text); self.assertIn(ITEM_A,text); self.assertIn(ITEM_B,text)
        plugin.item_icons.get.assert_not_awaited()
        selected = await self.capture(plugin,'pve id:'+ITEM_B)
        self.assertEqual(selected['item_id'],ITEM_B)

    async def test_continuation_is_frozen_no_refetch_and_respects_current_feature_switches(self):
        plugin = self.plugin({'items_enabled':True})
        card = {'name':'Frozen','features':plugin.item_features()}
        pages = self.module.paginate_item(card,[('候选物品','long row '+str(i)) for i in range(100)])
        token = plugin.item_catalog.save_pages(pages)
        result = await self.capture(plugin,f'页 {token} 2')
        self.assertEqual(result,pages[1]); plugin.load_item_sources.assert_not_awaited()
        plugin.config['item_flea_enabled'] = False
        result = await self.capture(plugin,f'页 {token} 2')
        self.assertIn('展示开关已变更',' '.join(line for row in result['rows'] for line in row['lines']))

    async def test_namespace_short_qq_and_quoted_mode_word_name_routes(self):
        plugin = self.plugin({'items_enabled':True,'item_images_enabled':False})
        for text in ('/tarkov item Medical Aid Kit','/塔科夫 物品 pve Med Kit'):
            outputs = [r async for r in plugin.tarkov_command(boundary.Event(text))]
            self.assertEqual(Image.open(io.BytesIO(outputs[0][0].data)).format,'JPEG')
        event = boundary.Event('/item pve Med Kit'); event.platform_name='qq_official'
        self.assertIsInstance([r async for r in plugin.qq_item_command(event)][0][0],boundary.ImageStub)
        plugin.item_icons.get.assert_not_awaited()
        data = item_data(); data['items'][ITEM_A]['name'] = 'pve special item'
        plugin.load_item_sources = AsyncMock(side_effect=lambda mode:bundle(mode,data=data))
        result = [r async for r in plugin.tarkov_command(boundary.Event('/tarkov item "pve special item"'))]
        self.assertIsInstance(result[0][0],boundary.ImageStub)
        self.assertEqual({call.args[0] for call in plugin.load_item_sources.await_args_list},{'pvp','pve','season'})

    async def test_lazy_initialize_and_actual_short_handler_opt_out(self):
        handlers = [types.SimpleNamespace(handler_name=name,enabled=True) for name in ('item_command','qq_item_command','tarkov_command')]
        registry = sys.modules['astrbot.core.star.star_handler'].star_handlers_registry
        with patch.object(registry,'get_handlers_by_module_name',return_value=handlers):
            plugin = self.plugin({'short_commands_enabled':False})
        await plugin.initialize()
        self.assertEqual([h.enabled for h in handlers],[False,False,True])
        self.assertFalse(plugin.item_catalog.path.exists()); self.assertFalse(plugin.item_icons.root.exists())
        self.assertEqual([r async for r in plugin.item_command(boundary.Event('/item Med Kit'))],[])

    async def test_owner_is_held_through_image_drawing_and_image_error_has_only_brief_text(self):
        plugin = self.plugin({'items_enabled':True})
        started,gate = asyncio.Event(),asyncio.Event()
        real = plugin.item_picture
        async def picture(event,page,*args):
            if page.get('item_id'): started.set(); await gate.wait()
            return await real(event,page,*args)
        plugin.item_picture = picture
        first = asyncio.create_task(plugin.item_query(boundary.Event('/item'),'Med Kit'))
        await started.wait(); second = await plugin.item_query(boundary.Event('/item'),'Med Kit')
        self.assertIsInstance(second[0],boundary.ImageStub)
        gate.set(); await first; self.assertFalse(plugin.active)
        with patch.object(self.module,'render_item_page',side_effect=RuntimeError()):
            result = await plugin.item_query(boundary.Event('/item'),'Med Kit')
        self.assertEqual(result,'物品图片生成失败，请稍后再试。')
        with patch.object(self.module,'paginate_item',side_effect=RuntimeError()):
            result = await plugin.item_query(boundary.Event('/item'),'Med Kit')
        self.assertEqual(result,'物品图片生成失败，请稍后再试。')

    async def test_real_public_cache_is_shared_for_items_and_dictionary_and_bad_200_keeps_good_data(self):
        plugin = self.plugin({'items_enabled':True,'item_details_enabled':False,'item_trader_enabled':False})
        plugin.load_item_sources = types.MethodType(self.module.TarkovPlugin.load_item_sources,plugin)
        calls,bad = [],[False]
        async def fetch(key,**kwargs):
            calls.append(key)
            data = ZH if key.language=='zh' else EN if key.language else item_data(500)
            if not key.language and bad[0]: data['items'][ITEM_A]['weight'] = 'bad'
            return {'status':200},json.dumps({'data':data}).encode()
        with patch.object(self.module,'fetch_public',fetch):
            first = await self.capture(plugin,'pve Med Kit')
            await self.capture(plugin,'pve Med Kit')
            self.assertEqual(len(calls),3)
            plugin.config['public_data_cache_minutes'] = 0; bad[0] = True
            last = await self.capture(plugin,'pve Med Kit')
            self.assertTrue(any('旧索引/缓存' in note for note in last['notes']))
            self.assertTrue(any('500 RUB' in line for row in last['rows'] for line in row['lines']))
