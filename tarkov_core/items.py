"""Item identity, cross-mode attributes and mode-specific prices. No cross-mode price fallback."""
import json
import math
import shlex
import unicodedata
import datetime
from decimal import Decimal

from .modes import GAME_MODES, label_of, parse_mode
from .public_api import ITEM_ID, DataError
from .text import clean
from .transport import QueryError

BASE_FIELDS = {'width':'宽度（格）', 'height':'高度（格）', 'weight':'重量（kg）',
               'stackMaxSize':'最大堆叠', 'hasGrid':'内部容器', 'categories':'分类'}
PROPERTY_LABELS = {
    'propertiesType':'属性类型','caliber':'口径','damage':'伤害','penetrationPower':'穿深','armorDamage':'护甲伤害',
    'initialSpeed':'初速（m/s）','projectileCount':'弹丸数','tracer':'曳光','fragmentationChance':'碎弹概率',
    'ricochetChance':'跳弹概率','bulletMassGrams':'弹重（g）','heavyBleedModifier':'重出血修正',
    'lightBleedModifier':'轻出血修正','class':'护甲等级','durability':'耐久','maxDurability':'最大耐久',
    'material':'材料','armorType':'护甲类型','armorSlots':'护甲部位','zones':'覆盖区域','bluntThroughput':'钝伤传递',
    'speedPenalty':'移速修正','turnPenalty':'转向修正','ergoPenalty':'人机修正','ergonomics':'人机工效',
    'recoilVertical':'垂直后坐力','recoilHorizontal':'水平后坐力','fireRate':'射速（发/分）',
    'fireModes':'射击模式','effectiveDistance':'有效距离（m）','sightingRange':'瞄准距离（m）','moa':'精度（MOA）',
    'defaultErgonomics':'默认人机','defaultRecoilVertical':'默认垂直后坐','defaultRecoilHorizontal':'默认水平后坐',
    'capacity':'容量（格/发）','hitpoints':'治疗总量','useTime':'使用时间（秒）','maxHealPerUse':'单次最大治疗',
    'cures':'治疗状态','hpCostLightBleeding':'止轻出血消耗','hpCostHeavyBleeding':'止重出血消耗',
    'uses':'使用次数','painkillerDuration':'止痛时长（秒）','energyImpact':'能量变化','hydrationImpact':'水分变化',
    'minLimbHealth':'手术后最低血量比例','maxLimbHealth':'手术后最高血量比例','stimEffects':'药效',
    'energy':'能量','hydration':'水分','units':'资源量','fuse':'引信（秒）','fragments':'破片数',
    'minExplosionDistance':'最小爆炸距离（m）','maxExplosionDistance':'最大爆炸距离（m）',
    'slashDamage':'劈砍伤害','stabDamage':'刺击伤害','hitRadius':'攻击距离（m）','zoomLevels':'倍率',
    'recoilModifier':'后坐力修正','accuracyModifier':'精度修正','loadModifier':'装填修正',
    'ammoCheckModifier':'检查弹药修正','blindnessProtection':'闪光防护','deafening':'听觉影响',
    'blocksHeadset':'阻挡耳机','blocksEyewear':'阻挡眼镜','blocksHeadwear':'阻挡头饰',
    'ambientVolume':'环境音量','distanceModifier':'声音距离修正','intensity':'视觉强度','noiseIntensity':'噪点强度'}
TEXT_PROPERTIES = {'propertiesType','caliber','material','armorType','deafening'}
BOOL_PROPERTIES = {'tracer','blocksHeadset','blocksEyewear','blocksHeadwear'}
LIST_PROPERTIES = {'zones','fireModes','cures','stimEffects','zoomLevels','armorSlots'}
PERCENT_PROPERTIES = {'fragmentationChance','ricochetChance','bluntThroughput','minLimbHealth','maxLimbHealth','blindnessProtection'}
ENUMS = {'LightBleeding':'轻出血','HeavyBleeding':'重出血','Pain':'疼痛','Contusion':'挫伤','LostLimb':'肢体损毁',
         'Heavy':'重型','Light':'轻型','High':'高','Low':'低','None':'无', 'HealthRate':'生命恢复',
         'EnergyRate':'能量恢复','HydrationRate':'水分恢复','Skill':'技能','HandsTremor':'手部震颤'}
TYPE_NAMES = {'MedKit':'医疗包','MedicalItem':'医疗物品','SurgicalKit':'手术包','Painkiller':'止痛药',
              'Stim':'注射器','Ammo':'弹药','Armor':'护甲','Helmet':'头盔','ArmorAttachment':'护甲附件',
              'Weapon':'武器','Preset':'武器预设','WeaponMod':'武器配件','Magazine':'弹匣','Barrel':'枪管',
              'Scope':'瞄具','Backpack':'背包','ChestRig':'胸挂','Container':'容器','FoodDrink':'食物饮料',
              'Key':'钥匙','Resource':'资源','Headphone':'耳机','Melee':'近战武器','Grenade':'手雷',
              'NightVision':'夜视设备','Glasses':'眼镜','Headwear':'头饰','InfoContent':'信息物品'}
PRICE_FIELDS = ('lastLowPrice','avg24hPrice','low24hPrice','high24hPrice','lastOfferCount','lastScan','updated',
                'minLevelForFlea','discardLimit','types','buyFromTrader','sellToTrader')
VIRTUAL_ITEM_IDS = frozenset(('customdogtags12345678910',))


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC',str(text)).casefold().split())


def parse_item_arguments(arguments):
    raw = arguments.strip()
    # Quoting the whole name escapes a leading mode word, e.g. "pve special item".
    quoted = raw.startswith(('"', "'"))
    try: words = shlex.split(raw)
    except ValueError: raise QueryError('引号未闭合，请检查物品名称。') from None
    if not words: raise QueryError('请填写物品名称或 id:物品ID。')
    mode = None if quoted else parse_mode(words[0])
    if mode is not None:
        if mode not in GAME_MODES: raise QueryError('物品查询仅支持 pvp、pve、赛季。')
        words = words[1:]
    query = ' '.join(words).strip()
    if not query or len(query) > 240: raise QueryError('物品名称须为 1～240 个字符。')
    if query.lower().startswith('id:'):
        query = query[3:].lower()
        if not ITEM_ID.fullmatch(query) and query not in VIRTUAL_ITEM_IDS: raise QueryError('请填写 24 位物品 ID 或已支持的虚拟物品 ID。')
    return mode,query


def canonical(value):
    if isinstance(value,dict): return {key:canonical(v) for key,v in sorted(value.items())}
    if isinstance(value,list): return sorted((canonical(v) for v in value),key=lambda v:json.dumps(v,sort_keys=True,ensure_ascii=False))
    return value


def numeric(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def validate_items(data):
    if not isinstance(data,dict) or not isinstance(data.get('items'),dict): raise DataError('物品目录格式错误')
    if len(data['items']) > 30000: raise DataError('物品目录记录过多')
    for key in ('fleaMarket','itemCategories'):
        if data.get(key) is not None and not isinstance(data[key],dict): raise DataError('物品公共规则格式错误')
    for item_id,item in data['items'].items():
        if (not ITEM_ID.fullmatch(item_id) and item_id not in VIRTUAL_ITEM_IDS) or not isinstance(item,dict) or item.get('id',item_id) != item_id:
            raise DataError('物品身份格式错误')
        for key in ('name','shortName','normalizedName'):
            if key in item and not isinstance(item[key],str): raise DataError('物品名称格式错误')
        for key in ('width','height','weight','stackMaxSize',*PRICE_FIELDS[:5],'minLevelForFlea','discardLimit'):
            if key in item and item[key] is not None and not numeric(item[key]): raise DataError('物品数值格式错误')
        for key in ('categories','types'):
            if key in item and (not isinstance(item[key],list) or not all(isinstance(v,str) for v in item[key])):
                raise DataError('物品分类格式错误')
        if 'hasGrid' in item and item['hasGrid'] is not None and not isinstance(item['hasGrid'],bool):
            raise DataError('物品容器标记格式错误')
        props = item.get('properties')
        if props is not None:
            if not isinstance(props,dict): raise DataError('物品属性格式错误')
            for key in PROPERTY_LABELS:
                if key not in props or props[key] is None: continue
                value = props[key]
                valid = (isinstance(value,str) if key in TEXT_PROPERTIES else isinstance(value,bool) if key in BOOL_PROPERTIES
                         else isinstance(value,list) if key in LIST_PROPERTIES else numeric(value))
                if not valid: raise DataError('物品专属属性格式错误')
            slots = props.get('armorSlots')
            if slots is not None and (len(slots) > 128 or any(not isinstance(slot,dict) for slot in slots)):
                raise DataError('护甲部位格式错误')
        for key in ('buyFromTrader','sellToTrader'):
            offers = item.get(key,[])
            if not isinstance(offers,list) or len(offers) > 100: raise DataError('商人报价格式错误')
            for offer in offers:
                if not isinstance(offer,dict): raise DataError('商人报价格式错误')
                for field in ('trader','currency','taskUnlock'):
                    if field in offer and offer[field] is not None and not isinstance(offer[field],str): raise DataError('商人引用格式错误')
                for field in ('price','priceRUB','minTraderLevel','buyLimit','restockAmount'):
                    if field in offer and offer[field] is not None and not numeric(offer[field]): raise DataError('商人报价数值错误')


def translate(value,dictionaries,fallback='未提供'):
    if value is None: return fallback
    value = str(value)
    for dictionary in dictionaries:
        if dictionary.get(value): return clean(dictionary[value],300)
    return clean(value,300)


def project_item(item_id,item,data,dictionaries):
    fallback = item.get('normalizedName') or item_id
    raw_name = item.get('name',fallback)
    name = translate(raw_name,dictionaries,fallback)
    if name == f'{item_id} Name': name = fallback
    aliases = {item_id,fallback}
    for key in ('name','shortName','normalizedName'):
        if item.get(key):
            value = item[key]; aliases.add(value)
            aliases.update(dictionary[value] for dictionary in dictionaries if dictionary.get(value))
    attributes = {key:canonical(item[key]) for key in BASE_FIELDS if key in item}
    for key,value in (item.get('properties') or {}).items():
        if key not in PROPERTY_LABELS: continue
        if key == 'armorSlots' and value is not None:
            value = [{'name':slot.get('name',slot.get('nameId','未提供')),
                      **{k:slot[k] for k in ('class','durability','armorMaterial','armorType','zones') if k in slot}}
                     for slot in value]
        attributes['properties.'+key] = canonical(value)
    categories = data.get('itemCategories') or {}
    category_names = {key:translate((categories.get(key) or {}).get('name'),dictionaries,key) for key in item.get('categories',[])}
    used_strings = collect_strings(attributes)
    return {'id':item_id,'name':name,'short_name':translate(item.get('shortName'),dictionaries,name),
            'aliases':sorted(aliases),'attributes':attributes,'category_names':category_names,
            'display_dictionary':{key:dictionary[key] for dictionary in reversed(dictionaries) for key in used_strings if key in dictionary},
            'prices':{key:item[key] for key in PRICE_FIELDS if key in item}}


def collect_strings(value):
    if isinstance(value,str): return {value}
    if isinstance(value,list): return set().union(*(collect_strings(v) for v in value)) if value else set()
    if isinstance(value,dict): return set().union(*(collect_strings(v) for v in value.values())) if value else set()
    return set()


def display(value,item=None):
    if value is None: return '未提供'
    if isinstance(value,bool): return '是' if value else '否'
    if isinstance(value,(int,float)): return f'{value:g}'
    if isinstance(value,str):
        if item and value in item.get('display_dictionary',{}): return item['display_dictionary'][value]
        if value.startswith('ItemProperties'): return TYPE_NAMES.get(value[14:],value[14:])
        if item and value in item.get('category_names',{}): return item['category_names'][value]
        return ENUMS.get(value,value.removeprefix('Caliber'))
    if isinstance(value,list): return '、'.join(display(v,item) for v in value) or '无'
    labels = {'chance':'概率','delay':'延迟（秒）','duration':'持续（秒）','value':'数值','percent':'百分比',
              'type':'效果','skill':'技能','name':'部位','class':'护甲等级','durability':'耐久',
              'armorMaterial':'材料','armorType':'护甲类型','zones':'覆盖区域'}
    return '；'.join(labels.get(k,k)+'：'+display(v,item) for k,v in value.items())


def attribute_label(key):
    return BASE_FIELDS.get(key) or PROPERTY_LABELS.get(key.removeprefix('properties.'),key)


def display_attribute(key,value,item):
    if key.removeprefix('properties.') in PERCENT_PROPERTIES and numeric(value):
        return f'{value*100:g}%'
    return display(value,item)


def attribute_blocks(items,complete,mode=None,details=True):
    if not details: return []
    if not complete:
        message = '三个模式的物品属性核对未完成，暂不判断属性差异；价格仅来自指定模式。' if mode else '三个模式的物品数据核对未完成，仅展示身份信息；不把单模式属性当作共同或独有属性。'
        return [('属性核对',message)]
    selected = items[mode] if mode else next(iter(items.values()))
    keys = sorted(set().union(*(item['attributes'] for item in items.values())))
    common,differences = [],[]
    missing = object()
    types = [item['attributes'].get('properties.propertiesType',missing) for item in items.values()]
    compatible_type = all(value is not missing and value is not None and value == types[0] for value in types)
    for key in keys:
        values = {m:item['attributes'].get(key,missing) for m,item in items.items()}
        equal = all(v is not missing for v in values.values()) and all(v == next(iter(values.values())) for v in values.values())
        if key.startswith('properties.') and not compatible_type: equal = False
        if equal and values[next(iter(values))] is not None:
            common.append(attribute_label(key)+'：'+display_attribute(key,selected['attributes'][key],selected))
        elif mode:
            current = selected['attributes'].get(key,missing)
            value = '未提供' if current is missing else display_attribute(key,current,selected)
            others = '；'.join(label_of(m)+'：'+('未提供' if v is missing else display_attribute(key,v,items[m])) for m,v in values.items() if m != mode)
            differences.append(attribute_label(key)+'：'+value+'（'+others+'）')
    if mode and not compatible_type and any(key.startswith('properties.') for key in keys):
        differences.insert(0,'各模式属性类型不同或缺失，同名字段需按各模式类型解释。')
    return [('共同属性',line) for line in common] if mode is None else [('模式属性差异',line) for line in differences] or [('模式属性差异','已核对的属性与其他模式一致；共同属性请用不带模式的物品查询。')]


def money(value,currency='RUB'):
    if not numeric(value) or value <= 0: return '无有效报价'
    text = format(Decimal(str(value)),',f')
    return (text.rstrip('0').rstrip('.') if '.' in text else text)+' '+currency


def scan_time(value):
    from .card import when
    try:
        if numeric(value): stamp = value/1000 if abs(value) > 100000000000 else value
        else:
            date = datetime.datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if date.tzinfo is None: date = date.replace(tzinfo=datetime.timezone.utc)
            stamp = date.timestamp()
        return (when(stamp) or '未提供')+'（GMT+8）'
    except (ValueError,TypeError,OverflowError,OSError): return '未提供'


def flea_blocks(item,flea):
    flea = flea or {}
    prices = item['prices']; blocks = []
    if flea.get('enabled') is False: blocks.append(('跳蚤市场','此模式跳蚤市场未开放；以下价格为已有扫描记录。'))
    elif flea.get('enabled') is not True: blocks.append(('跳蚤市场','此模式跳蚤市场开放状态未提供，不能仅凭价格判断可购买。'))
    if 'noFlea' in prices.get('types',[]): blocks.append(('跳蚤市场','该物品禁止在跳蚤市场交易；扫描价格不代表可购买。'))
    for key,label in (('lastLowPrice','最新扫描最低价'),('avg24hPrice','24 小时均价'),('low24hPrice','24 小时最低'),('high24hPrice','24 小时最高')):
        blocks.append(('跳蚤市场',label+'：'+money(prices.get(key))))
    blocks.append(('跳蚤市场','报价数量：'+display(prices.get('lastOfferCount'))+'；扫描时间：'+scan_time(prices.get('lastScan'))))
    blocks.append(('交易限制','市场最低等级：'+display(flea.get('minPlayerLevel'))+'；物品最低等级：'+display(prices.get('minLevelForFlea'))+
                   '；要求战局发现：'+display(flea.get('foundInRaidRequired'))))
    return blocks


def trader_blocks(item,context):
    blocks = []
    for key,section in (('buyFromTrader','从商人购买'),('sellToTrader','卖给商人')):
        offers = item['prices'].get(key,[])
        if not offers: blocks.append((section,'未提供有效商人报价。'))
        for offer in offers:
            trader = str(offer.get('trader') or '未知商人')
            name = context.get('traders',{}).get(trader,{}).get('name',trader)
            text = name+'：'+money(offer.get('price'),str(offer.get('currency') or '币种未提供'))
            text += '；折合 '+money(offer.get('priceRUB'))
            if key == 'buyFromTrader':
                text += '；商人等级 '+display(offer.get('minTraderLevel'))+'；限购 '+display(offer.get('buyLimit'))+'；补货量 '+display(offer.get('restockAmount'))
                task = offer.get('taskUnlock')
                text += '；任务解锁：'+(context.get('tasks',{}).get(task,str(task)) if task else '无' if 'taskUnlock' in offer else '未提供')
            blocks.append((section,text))
    blocks.append(('报价说明','商人库存、购买资格及货币折算以扫描时数据为准，不承诺当前可成交。'))
    return blocks
