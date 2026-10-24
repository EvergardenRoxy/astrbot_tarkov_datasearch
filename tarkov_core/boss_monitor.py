"""Conditional Boss spawn records, independent of display language and transport."""
import hashlib
import json
import math
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .text import clean
from .transport import QueryError

# Font-subset corpus for known upstream labels (translations still come from the API):
# 工厂、夜间工厂、迷宫、实验室、破冰船、森林、灯塔、海岸线、储备站、中心区、塔科夫街区、教程、桥。
# 邪教徒祭司、邪教徒战士、掠夺者、游荡者、黑色军团、保镖、守卫、俄军。

BOSS_SCOPE = 'daily-goons-selected-maps-matrix-v5'
# Explicit maintainer scope: Knight represents the GOONS squad. Ordinary AI and event variants stay excluded.
DAILY_BOSSES = frozenset(('bossTagilla', 'bossBully', 'bossKojaniy', 'bossSanitar', 'bossKilla', 'bossGluhar',
                         'bossZryachiy', 'bossBoar', 'bossKolontay', 'bossPartisan', 'sectantPriest', 'bossKnight'))
EXCLUDED_MAP_IDS = frozenset(('69af492a4819ea4ba10a69c5', '653e6760052c01c1c805532f',
                              '6a294a5b5eb5f9a1700417b7', '6733700029c367a3d40b02af', '68236e8153654e8c1200798a',
                              '65cc8f81a9aac3e77d0cfd3e'))
EXCLUDED_MAP_NAMES = frozenset(('icebreaker', 'ground-zero', 'the-lab-dark', 'the-labyrinth', 'ground-zero-tutorial', 'terminal'))


def canonical(value):
    if isinstance(value, float):
        if not math.isfinite(value):
            raise QueryError('Boss 刷新条件含无效数值')
        if value.is_integer(): return int(value)
        return float(probability(value)) if 0 <= value <= 1 else round(value, 12)
    if isinstance(value, dict):
        return {key: canonical(v) for key, v in sorted(value.items())
                if key not in ('positions', 'position', 'coordinates', 'imageLink', 'imagePortraitLink', 'imagePosterLink')}
    if isinstance(value, list):
        return sorted((canonical(v) for v in value), key=packed)
    return value


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def probability(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise QueryError('Boss 刷新概率缺失或格式错误')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not 0 <= number <= 1:
            raise ValueError()
        rounded = number.quantize(Decimal('0.000000000001'))
        # Remove floating-point tails, while keeping a value short of 100% distinct from guaranteed spawns.
        return str((number if rounded in (0, 1) and rounded != number else rounded).normalize())
    except (InvalidOperation, ValueError):
        raise QueryError('Boss 刷新概率必须在 0～1 范围内') from None


def percent(value):
    return '—' if value is None else format((Decimal(value)*100).normalize(), 'f')+'%'


def translated(value, dictionaries, fallback):
    text = str(value or fallback)
    for dictionary in dictionaries:
        if dictionary.get(text):
            return clean(dictionary[text], 160)
    return clean(text, 160)


def condition_text(rule, dictionaries):
    parts = []
    for key, value in rule.items():
        if key == 'spawnLocations':
            locations = []
            for location in value:
                name = translated(location.get('name'), dictionaries, location.get('spawnKey', '未提供'))
                chance = location.get('chance')
                locations.append(name+(f'（区域权重 {percent(str(chance))}）' if chance is not None else ''))
            parts.append('区域：'+('、'.join(locations) or '未提供'))
        elif key == 'spawnTime':
            parts.append('时间：'+('未指定' if value == -1 else str(value)+' 秒'))
        elif key == 'spawnTimeRandom':
            parts.append('随机时间：'+('是' if value else '否'))
        elif key in ('spawnTrigger', 'switch', 'switch_id'):
            if value is not None:
                parts.append({'spawnTrigger': '触发', 'switch': '开关引用', 'switch_id': '开关标识'}[key]+'：'+str(value))
        else:
            parts.append(key+'：'+packed(value))
    return '；'.join(parts) or '上游未提供条件'


def normalize_bosses(data, dictionaries=()):
    if not isinstance(data, dict) or not isinstance(data.get('maps'), dict) or not isinstance(data.get('mobs'), dict):
        raise QueryError('地图或 Boss 数据格式错误')
    maps, mobs = data['maps'], data['mobs']
    if not maps or len(maps) > 128:
        raise QueryError('地图数量异常')
    result, total = [], 0
    for map_id, game_map in sorted(maps.items()):
        normalized_name = game_map.get('normalizedName') if isinstance(game_map, dict) else None
        if map_id in EXCLUDED_MAP_IDS or (isinstance(normalized_name, str) and normalized_name in EXCLUDED_MAP_NAMES):
            continue
        if not isinstance(game_map, dict) or not isinstance(game_map.get('bosses'), list):
            raise QueryError('地图缺少完整 Boss 刷新记录')
        bosses = game_map['bosses']
        total += len(bosses)
        if total > 4096:
            raise QueryError('Boss 刷新记录过多')
        rows = []
        for boss in bosses:
            if not isinstance(boss, dict) or not isinstance(boss.get('mob'), str) or not boss['mob']:
                raise QueryError('Boss 引用缺失')
            mob_id = boss['mob']
            if mob_id not in DAILY_BOSSES:
                continue
            if mob_id not in mobs or not isinstance(mobs[mob_id], dict):
                raise QueryError('Boss 引用无法关联')
            chance = probability(boss.get('spawnChance'))
            # Escorts/support equipment are not conditions of the named mob's spawn. Keep unknown future rule fields.
            rule = {key: value for key, value in boss.items()
                    if key not in ('mob', 'spawnChance', 'escorts', 'supports', 'id', '_id', 'name', 'imageLink')}
            locations = rule.get('spawnLocations', [])
            if not isinstance(locations, list) or any(not isinstance(loc, dict) for loc in locations):
                raise QueryError('Boss 刷新区域格式错误')
            for loc in locations:
                if 'chance' in loc:
                    probability(loc['chance'])
            if 'spawnTime' in rule and (isinstance(rule['spawnTime'], bool) or not isinstance(rule['spawnTime'], (int, float))):
                raise QueryError('Boss 刷新时间格式错误')
            if 'spawnTimeRandom' in rule and not isinstance(rule['spawnTimeRandom'], bool):
                raise QueryError('Boss 随机时间格式错误')
            rule = canonical(rule)
            encoded = packed(rule)
            if len(encoded) > 8192:
                raise QueryError('Boss 刷新条件过长')
            name = '三狗（GOONS）' if mob_id == 'bossKnight' else translated(mobs[mob_id].get('name'), dictionaries, mob_id)
            rows.append({'mob_id': mob_id, 'boss_name': name,
                         'rule': encoded, 'condition': condition_text(rule, dictionaries), 'chance': chance})
        rows.sort(key=lambda row: (row['mob_id'], row['rule'], Decimal(row['chance'])))
        map_name = '中心区' if map_id == '65b8d6f5cdde2479cb2a3125' or normalized_name == 'ground-zero-21' else translated(game_map.get('name'), dictionaries, map_id)
        result.append({'map_id': map_id, 'map_name': map_name, 'rows': rows})
    return result


def build_table(before, after):
    """Full table with record-level diffs, matching equal conditions before comparing probabilities."""
    old_maps = {row['map_id']: row for row in before or []}
    new_maps = {row['map_id']: row for row in after}
    output, changed = [], False
    for map_id in sorted(old_maps.keys() | new_maps.keys()):
        old_map, new_map = old_maps.get(map_id), new_maps.get(map_id)
        game_map = new_map or old_map
        previous, current = defaultdict(list), defaultdict(list)
        for row in (old_map or {}).get('rows', []): previous[(row['mob_id'], row['rule'])].append(row)
        for row in (new_map or {}).get('rows', []): current[(row['mob_id'], row['rule'])].append(row)
        keys = previous.keys() | current.keys()
        if not keys:
            changed_map = before is not None and (old_map is None or new_map is None)
            output.append({'map_id': map_id, 'map_name': game_map['map_name'], 'mob_id': None, 'boss_name': '—',
                           'condition': '本次数据未列出日常 Boss', 'previous': None, 'current': None,
                           'change': ('地图新增' if new_map else '地图已移除') if changed_map else '', 'changed': changed_map})
            changed |= changed_map
        for key in sorted(keys):
            old, new = list(previous[key]), list(current[key])
            matched = []
            # Cancel equal multiset entries first: duplicates must not overwrite one another.
            for row in old[:]:
                match = next((r for r in new if r['chance'] == row['chance']), None)
                if match is not None:
                    old.remove(row); new.remove(match); matched.append((row, match))
            old.sort(key=lambda row: Decimal(row['chance']))
            new.sort(key=lambda row: Decimal(row['chance']))
            matched.extend((old[i] if i < len(old) else None, new[i] if i < len(new) else None)
                           for i in range(max(len(old), len(new))))
            for old, new in matched:
                row = new or old
                note = ''
                if before is not None:
                    if old is None: note = '新增记录/规则'
                    elif new is None: note = '已移除记录/规则'
                    elif old['chance'] != new['chance']:
                        delta = (Decimal(new['chance'])-Decimal(old['chance']))*100
                        note = ('+' if delta > 0 else '')+format(delta.normalize(), 'f')+' 个百分点'
                output.append({'map_id': map_id, 'map_name': game_map['map_name'], 'mob_id': row['mob_id'], 'boss_name': row['boss_name'],
                               'condition': row['condition'], 'previous': old['chance'] if old else None,
                               'current': new['chance'] if new else None, 'change': note, 'changed': bool(note)})
                changed |= bool(note)
    return output, changed


def state_fingerprint(state):
    semantic = [(m['map_id'], [(r['mob_id'], r['rule'], r['chance']) for r in m['rows']]) for m in state]
    return hashlib.sha256(packed(semantic).encode()).hexdigest()
