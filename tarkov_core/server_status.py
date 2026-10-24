"""Normalize the upstream status feed without interpreting fetch failures as game downtime."""
import hashlib
import json

from .text import clean
from .transport import QueryError

LABELS = {'ok': '正常', 'operational': '正常', 'unstable': '不稳定', 'down': '不可用',
          'maintenance': '维护中', 'degraded': '服务降级', 'updating': '更新中'}
NAMES = {'website': '官网', 'forum': '论坛', 'authentication': '账号认证', 'launcher': '启动器',
         'group lobby': '组队大厅', 'trading': '交易', 'matchmaking': '匹配',
         'friends and msg': '好友与消息', 'inventory operations': '背包操作'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _component(row, overall=False):
    if not isinstance(row, dict):
        raise QueryError('服务器状态组件格式无法识别')
    name = row.get('name')
    code = row.get('statusCode')
    if not isinstance(name, str) or not name.strip() or not isinstance(code, str) or not code.strip():
        raise QueryError('服务器状态缺少名称或状态码')
    name, code = clean(name, 80), clean(code, 64)
    key = str(row.get('_id') or name.casefold())
    return {'key': 'global' if overall else key, 'name': '总体状态' if overall else NAMES.get(name.casefold(), name),
            'code': code.casefold(), 'label': LABELS.get(code.casefold(), f'未知（{code}）'),
            'message': clean(row.get('message') or '', 500)}


def normalize_status(data):
    if not isinstance(data, dict) or not isinstance(data.get('currentStatuses'), list) or not isinstance(data.get('messages'), list):
        raise QueryError('服务器状态格式无法识别')
    if len(data['currentStatuses']) > 64 or len(data['messages']) > 256:
        raise QueryError('服务器状态记录过多')
    overall = _component(data.get('generalStatus'), overall=True)
    components = {}
    for row in data['currentStatuses']:
        if isinstance(row, dict) and str(row.get('name', '')).strip().casefold() == 'global':
            continue
        component = _component(row)
        if component['key'] in components and components[component['key']] != component:
            raise QueryError('服务器状态组件标识重复')
        components[component['key']] = component
    messages = {}
    for row in data['messages']:
        if not isinstance(row, dict):
            raise QueryError('服务器公告格式无法识别')
        text = clean(row.get('content') or '', 2000)
        if not text:
            continue
        stamp = clean(row.get('time') or '', 80)
        key = digest([str(row.get('_id') or ''), text, stamp])
        messages[key] = {'key': key, 'text': text, 'time': stamp}
    return {'overall': overall, 'components': sorted(components.values(), key=lambda row: row['key']),
            'messages': sorted(messages.values(), key=lambda row: (row['time'], row['key']))}


def status_changes(before, after, seen_messages):
    changes = []
    previous = {row['key']: row for row in [before['overall'], *before['components']]}
    current = {row['key']: row for row in [after['overall'], *after['components']]}
    for key in sorted(previous.keys() | current.keys()):
        old, new = previous.get(key), current.get(key)
        if old is None:
            changes.append(f"{new['name']}：新增组件（{new['label']}）")
        elif new is None:
            changes.append(f"{old['name']}：上游已移除该组件")
        elif old['code'] != new['code']:
            changes.append(f"{new['name']}：{old['label']} → {new['label']}")
        elif key == 'global' and old['message'] != new['message'] and new['message']:
            changes.append(f"总体说明：{new['message']}")
    notices = [row for row in after['messages'] if row['key'] not in seen_messages]
    if notices:
        changes.append(f'新增有效公告 {len(notices)} 条')
    return changes, notices
