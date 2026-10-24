import asyncio
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from tarkov_core.item_catalog import ItemCatalog, SNAPSHOT_SECONDS
from tarkov_core.item_image import paginate_item, render_item_page
from tarkov_core.items import (attribute_blocks, flea_blocks, money, parse_item_arguments, project_item,
                               scan_time, trader_blocks, validate_items)
from tarkov_core.public_api import DataError, ResourceKey, decode, mode_path
from tarkov_core.resource_cache import CachedResource
from tarkov_core.transport import HTTPError, QueryError

ITEM_A, ITEM_B = 'a'*24, 'b'*24
ZH = {'item-name':'急救包','item-short':'药包'}
EN = {'item-name':'Medical Aid Kit','item-short':'Med Kit'}


def item_data(price=100,weight=0.6,second=False):
    item = {'id':ITEM_A,'name':'item-name','shortName':'item-short','normalizedName':'medical-aid-kit',
        'width':1,'height':2,'weight':weight,'stackMaxSize':1,'hasGrid':False,'categories':[],
        'properties':{'propertiesType':'ItemPropertiesMedKit','hitpoints':400,'useTime':3,'cures':['HeavyBleeding','LightBleeding']},
        'lastLowPrice':price,'avg24hPrice':price+10,'low24hPrice':price-10,'high24hPrice':price+20,
        'lastOfferCount':0,'lastScan':'2026-10-24T01:00:00Z','updated':'2026-10-24T02:00:00Z','types':[],
        'minLevelForFlea':0,'buyFromTrader':[{'trader':'t1','price':10.25,'currency':'USD','priceRUB':1000,
            'minTraderLevel':2,'taskUnlock':'quest1','buyLimit':3,'restockAmount':0}],
        'sellToTrader':[{'trader':'t2','price':40,'currency':'RUB','priceRUB':40}]}
    items = {ITEM_A:item}
    if second: items[ITEM_B] = {**copy.deepcopy(item),'id':ITEM_B,'normalizedName':'another-kit'}
    return {'items':items,'itemCategories':{},'fleaMarket':{'enabled':True,'minPlayerLevel':15,'foundInRaidRequired':False}}


def bundle(mode='pvp',price=100,weight=0.6,checked=100,data=None):
    key = ResourceKey('items',mode_path(mode))
    base = CachedResource(key,json.dumps({'data':data or item_data(price,weight)}).encode(),'base',None,None,90,checked)
    translations = [CachedResource(ResourceKey('items',mode_path(mode),lang),json.dumps({'data':dictionary}).encode(),lang,None,None,90,checked)
                    for lang,dictionary in [('zh',ZH),('en',EN)]]
    return {'base':base,'translations':translations}


def projected(mode='pvp',price=100,weight=0.6):
    data = item_data(price,weight)
    return project_item(ITEM_A,data['items'][ITEM_A],data,[ZH,EN])


class ItemModelTests(unittest.TestCase):
    def test_parser_preserves_multiword_names_and_quoted_mode_words_and_ids(self):
        self.assertEqual(parse_item_arguments('pve Medical Aid Kit'),('pve','Medical Aid Kit'))
        self.assertEqual(parse_item_arguments('"pve special item"'),(None,'pve special item'))
        self.assertEqual(parse_item_arguments('赛季 id:'+ITEM_A),('season',ITEM_A))
        self.assertEqual(parse_item_arguments('id:customdogtags12345678910'),(None,'customdogtags12345678910'))
        for query in ('','pve','arena Salewa','id:123','"unterminated'):
            with self.subTest(query=query), self.assertRaises(QueryError): parse_item_arguments(query)

    def test_common_properties_exclude_prices_and_different_or_missing_fields(self):
        items = {mode:projected(price=price) for mode,price in [('pvp',100),('pve',500),('season',900)]}
        blocks = attribute_blocks(items,True)
        self.assertIn(('共同属性','重量（kg）：0.6'),blocks)
        self.assertTrue(any('治疗总量：400' in text for _,text in blocks))
        self.assertFalse(any('价格' in text or '500' in text or '900' in text for _,text in blocks))
        del items['pve']['attributes']['weight']
        self.assertFalse(any('重量' in text for _,text in attribute_blocks(items,True)))
        differences = attribute_blocks(items,True,'pve')
        self.assertTrue(any('重量（kg）：未提供' in text for _,text in differences))
        self.assertTrue(any('核对未完成' in text for _,text in attribute_blocks(items,False)))

    def test_mode_difference_is_compared_to_other_modes_and_preserves_zero(self):
        items = {'pvp':projected(weight=0),'pve':projected(weight=0.7),'season':projected(weight=0)}
        blocks = attribute_blocks(items,True,'pve')
        self.assertTrue(any('重量（kg）：0.7' in text and 'PVP：0' in text for _,text in blocks))
        self.assertFalse(any('治疗总量' in text for _,text in blocks))

    def test_bad_catalog_values_are_rejected_before_public_cache_publication(self):
        for mutate in (lambda item:item.update(weight=True),lambda item:item['properties'].update(hitpoints='bad'),
                       lambda item:item['buyFromTrader'][0].update(price=float('inf')),
                       lambda item:item.update(id=ITEM_B)):
            data = item_data(); mutate(data['items'][ITEM_A])
            with self.assertRaises(DataError): decode(ResourceKey('items','pve'),json.dumps({'data':data}).encode())
        virtual = item_data(); virtual['items']['customdogtags12345678910'] = {'id':'customdogtags12345678910','name':'Dogtags'}
        validate_items(virtual)

    def test_flea_forbidden_closed_zero_offer_and_scan_time_are_explicit(self):
        item = projected(price=0); item['prices']['types'] = ['noFlea']
        text = '\n'.join(text for _,text in flea_blocks(item,{'enabled':False,'minPlayerLevel':15}))
        for word in ('未开放','禁止','无有效报价','报价数量：0','2026-10-24 09:00'):
            self.assertIn(word,text)
        self.assertTrue(any('开放状态未提供' in line for _,line in flea_blocks(projected(),None)))
        self.assertEqual(scan_time(None),'未提供')

    def test_trader_directions_currency_and_unlocks_are_not_inverted_or_rounded(self):
        blocks = trader_blocks(projected(),{'traders':{'t1':{'name':'买入商人'},'t2':{'name':'卖出商人'}},'tasks':{'quest1':'解锁任务'}})
        buy = next(text for section,text in blocks if section=='从商人购买')
        sell = next(text for section,text in blocks if section=='卖给商人')
        for word in ('买入商人','10.25 USD','1,000 RUB','等级 2','限购 3','补货量 0','解锁任务'): self.assertIn(word,buy)
        self.assertIn('卖出商人：40 RUB',sell)
        self.assertEqual(money(0),'无有效报价'); self.assertEqual(money(None),'无有效报价')

    def test_projection_has_chinese_english_short_name_aliases_and_nested_type(self):
        item = projected()
        self.assertTrue({'急救包','Medical Aid Kit','Med Kit','medical-aid-kit'} <= set(item['aliases']))
        self.assertEqual(item['attributes']['properties.propertiesType'],'ItemPropertiesMedKit')

    def test_armor_component_materials_are_compared_without_embedding_component_prices(self):
        data = item_data(); data['items'][ITEM_A]['properties'] = {'propertiesType':'ItemPropertiesArmor',
            'class':4,'durability':50,'armorSlots':[{'name':'Front','class':4,'durability':25,'armorMaterial':'Aramid','baseValue':99999}]}
        item = project_item(ITEM_A,data['items'][ITEM_A],data,[ZH,EN])
        attrs = attribute_blocks({mode:item for mode in ('pvp','pve','season')},True)
        self.assertTrue(any('材料：Aramid' in text for _,text in attrs))
        self.assertFalse(any('99999' in text for _,text in attrs))
        self.assertEqual(money(0.001,'USD'),'0.001 USD')

    def test_equal_numeric_fields_with_different_property_types_are_not_common_attributes(self):
        items = {mode:projected() for mode in ('pvp','pve','season')}
        items['pve']['attributes']['properties.propertiesType'] = 'ItemPropertiesResource'
        common = attribute_blocks(items,True)
        self.assertTrue(any('重量' in text for _,text in common))
        self.assertFalse(any('治疗总量' in text for _,text in common))
        self.assertTrue(any('属性类型不同' in text for _,text in attribute_blocks(items,True,'pve')))


class ItemCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.catalog = ItemCatalog(Path(self.temp.name)/'items.sqlite3')

    async def asyncTearDown(self):
        await self.catalog.close(); self.temp.cleanup()

    async def load(self,mode): return bundle(mode)

    async def test_lazy_atomic_index_restart_and_mode_prices_remain_separate(self):
        self.assertFalse(self.catalog.path.exists())
        async def load(mode): return bundle(mode,price=100 if mode=='pvp' else 500)
        await asyncio.gather(*(self.catalog.ensure(mode,load) for mode in ('pvp','pve')))
        self.assertEqual(self.catalog.search('药包',['pvp','pve']),( [ITEM_A],1))
        items = self.catalog.get(ITEM_A,['pvp','pve'])
        self.assertEqual(items['pve']['prices']['lastLowPrice'],500)
        self.assertEqual(items['pvp']['prices']['lastLowPrice'],100)
        await self.catalog.close(); self.catalog = ItemCatalog(self.catalog.path)
        self.assertEqual(self.catalog.search('MED KIT',['pve']),([ITEM_A],1))
        self.assertEqual(self.catalog.search('aid',['pvp']),([ITEM_A],1))

    async def test_same_version_rechecks_only_metadata_without_rebuilding_rows(self):
        await self.catalog.ensure('pvp',self.load)
        with sqlite3.connect(self.catalog.path) as db:
            db.execute("CREATE TRIGGER no_rebuild BEFORE INSERT ON items BEGIN SELECT RAISE(ABORT,'unexpected rebuild'); END")
        async def load(mode): return bundle(mode,checked=200)
        meta = await self.catalog.ensure('pvp',load)
        self.assertEqual(meta['checked_at'],200)

    async def test_changed_version_failure_cannot_erase_old_rows_and_empty_catalog_uses_explicit_old_index(self):
        await self.catalog.ensure('pvp',self.load)
        with sqlite3.connect(self.catalog.path) as db:
            db.execute("CREATE TRIGGER fail_index BEFORE INSERT ON items BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        async def changed(mode): return bundle(mode,price=500)
        with self.assertRaises(sqlite3.IntegrityError): await self.catalog.ensure('pvp',changed)
        self.assertEqual(self.catalog.get(ITEM_A,['pvp'])['pvp']['prices']['lastLowPrice'],100)
        async def empty(mode): return bundle(mode,data={'items':{}})
        self.assertTrue((await self.catalog.ensure('pvp',empty))['stale'])

    async def test_concurrent_queries_share_build_and_cancellation_does_not_cancel_other_readers(self):
        started,gate = asyncio.Event(),asyncio.Event(); calls = []
        async def load(mode): calls.append(mode); started.set(); await gate.wait(); return bundle(mode)
        tasks = [asyncio.create_task(self.catalog.ensure('pvp',load)) for _ in range(5)]
        await started.wait(); tasks[0].cancel(); gate.set()
        results = await asyncio.gather(*tasks,return_exceptions=True)
        self.assertIsInstance(results[0],asyncio.CancelledError)
        self.assertEqual(calls,['pvp']); self.assertTrue(all(r['mode']=='pvp' for r in results[1:]))

    async def test_ambiguous_names_return_distinct_ids_and_exact_match_precedes_substrings(self):
        async def load(mode): return bundle(mode,data=item_data(second=True))
        await self.catalog.ensure('pvp',load)
        self.assertEqual(self.catalog.search('Med Kit',['pvp']),([ITEM_A,ITEM_B],2))
        self.assertEqual(self.catalog.search('another-kit',['pvp']),([ITEM_B],1))
        self.assertEqual(self.catalog.search(ITEM_A,['pvp']),([ITEM_A],1))

    async def test_name_prefix_candidates_precede_incidental_substrings(self):
        data = item_data(second=True)
        data['items'][ITEM_A].update(name='Makarov',shortName='PM',normalizedName='makarov')
        data['items'][ITEM_B].update(name='AK-74',shortName='AK-74',normalizedName='ak-74')
        async def load(mode): return bundle(mode,data=data)
        await self.catalog.ensure('pvp',load)
        self.assertEqual(self.catalog.search('AK',['pvp']),([ITEM_B,ITEM_A],2))

    async def test_network_failure_marks_last_good_index_stale_and_retains_its_times(self):
        await self.catalog.ensure('pvp',self.load)
        async def load(mode): raise HTTPError('unavailable',503)
        result = await self.catalog.ensure('pvp',load)
        self.assertTrue(result['stale']); self.assertEqual(result['checked_at'],100)

    async def test_page_snapshot_immutability_expiry_and_capacity(self):
        original = [{'label':'PVE','rows':[{'lines':['100 RUB']}]}]
        token = self.catalog.save_pages(original,now=100); original[0]['rows'][0]['lines'].append('modified')
        self.assertEqual(self.catalog.pages(token,now=101)[0]['rows'][0]['lines'],['100 RUB'])
        self.assertIsNone(self.catalog.pages(token,now=101+SNAPSHOT_SECONDS))
        tokens = [self.catalog.save_pages([],now=200+i) for i in range(17)]
        self.assertIsNone(self.catalog.pages(tokens[0],now=220))

    async def test_close_cancels_pending_catalog_loads(self):
        started = asyncio.Event()
        async def load(mode): started.set(); await asyncio.Event().wait()
        task = asyncio.create_task(self.catalog.ensure('pvp',load)); await started.wait()
        await self.catalog.close()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(self.catalog.workers)


class ItemImageTests(unittest.TestCase):
    def test_common_mode_candidate_and_error_cards_are_jpegs_with_all_page_lines_retained(self):
        blocks = [('共同属性',f'治疗总量：{number}') for number in range(80)]
        pages = paginate_item({'name':'Salewa急救包','label':'通用属性','item_id':ITEM_A},blocks)
        self.assertGreater(len(pages),1)
        texts = [''.join(row['lines']) for page in pages for row in page['rows']]
        self.assertEqual(texts,[text for _,text in blocks])
        for page in (pages[0],pages[-1],paginate_item({'label':'查询提示'},[('提示','没有查询权限。')])[0]):
            output = render_item_page(page,total=len(pages),command='item 页 abcdef123456 2')
            image = Image.open(io.BytesIO(output)); self.assertEqual((image.width,image.format),(940,'JPEG'))
            self.assertLessEqual(image.height,1900)

    def test_candidate_name_id_and_command_keep_their_line_breaks(self):
        page = paginate_item({'label':'候选'},[('候选','AK-74\nID '+ITEM_A+'\nitem id:'+ITEM_A)])[0]
        self.assertEqual(page['rows'][0]['lines'],['AK-74','ID '+ITEM_A,'item id:'+ITEM_A])
