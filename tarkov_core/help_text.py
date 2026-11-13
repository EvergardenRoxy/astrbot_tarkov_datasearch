"""What the help says, for the help picture (help_image.py) and the text sent when it cannot be drawn."""
from .card import BOT_LIMIT
from .modes import label_of, page_url, search_url
from .snapshot_commands import every

NAME_RULE = '昵称只含字母、数字、下划线 _ 和连字符 -；_ 和 - 容易记混，查不到时插件会提示相近的名字。'


def search_line(app):
    """The tarkov.dev search page as a text line: every help reply ends with it, since a picture cannot carry a link."""
    return f'tarkov.dev 玩家搜索：{search_url(app.default_mode())}'


def feature_rows(command, features):
    """(what, command, remark) of the features switched on. features: a set of 'status', 'boss', 'matching',
    'weekly', 'qq_words'."""
    rows = []
    if 'status' in features:
        rows.append(('服务器状态', f'{command} status', '图片查询；播报需要单独开启'))
    if 'boss' in features:
        rows.append(('Boss 刷率', f'{command} boss [模式] [变化]', '全表分页；100% 红字加粗，播报单独开启'))
        rows.append(('Boss 默认', f'{command} boss 默认 <模式>', '个人默认模式；支持 pvp、pve、PVP S'))
    if 'matching' in features:
        rows.append(('匹配时间', f'{command} 匹配', '各服务器平均匹配时间图片；数据来自 Atlas'))
    if 'weekly' in features:
        rows.append(('玩家周报', f'{command} 周报 [模式] [昵称或排行]', '本聊天名单内玩家近 7 天的变化；数据来自 Atlas'))
        rows.append(('周报名单', f'{command} 周报 添加|移除|名单 [模式] [昵称或账号ID]', '每个聊天各自的名单；添加和移除仅 AstrBot 管理员'))
    if 'qq_words' in features:
        words = ('tpve [目标] · tbind <目标> · tstatus · tboss' + (' · tmatch' if 'matching' in features else '')
                 + (' · tweekly' if 'weekly' in features else ''))
        rows.append(('QQ 单词命令', words, '无空格，QQ 官方机器人用；参数可带空格'))
    return rows


def help_card(app, features, snapshot, players):
    """The help as data. rows: the command table; rule: what a query without a mode does; steps: the numbered
    section (`steps_title`), each (text, command or None); notes: the bullet list; foot: the source line."""
    command, mode = app.style()['command'], app.default_mode()
    label = label_of(mode)
    arena_by_default = app.config.flag('arena_by_default')
    index = every(snapshot.names.refresh_seconds) if snapshot.index_enabled() else None
    card = {'command': command, 'note': app.style()['note'], 'mode': mode, 'label': label}
    if app.player_source() == 'atlas':
        target = '<昵称、账号ID 或网址>'
        card.update(
            rows=[('查自己', f'{command} [模式]', '需要先绑定'),
                  ('查别人', f'{command} [模式] {target}', ''),
                  ('竞技场', f'{command} 竞技场 {target}', '不写名字时查自己'),
                  ('最新数据', f'{command} [模式] {target} 刷新', players.refresh_rule()),
                  ('绑定', f'{command} 绑定 {target}', ''),
                  ('解绑', f'{command} 解绑', ''),
                  ('帮助', f'{command} 帮助', '')],
            rule=f'不写模式时查 {label}，每次只查一个模式；竞技场要写明。',
            steps_title='三、按昵称查询',
            steps=[('先查插件记录' + ('和 tarkov.dev 昵称名单' if index else '') + '，找不到再用 Atlas 搜索。', None),
                   ('Atlas 只能搜索 3～15 个字符的昵称，更长的名字请用账号 ID 或玩家页网址，例如：',
                    f'{command} {page_url(mode, 1234567)}')],
            notes=[players.source_rule(), NAME_RULE],
            foot='数据来自 Atlas（实时查询）；故障时改用 tarkov.dev 快照')
    else:
        remembered = '按 ID 或网址查到过的名字会被记住' + (f'，也会查昵称名单（{index}更新一次）' if index else '')
        card.update(
            rows=[('查自己', f'{command} [模式]', '需要先绑定'),
                  ('查别人', f'{command} [模式] <昵称、账号ID 或网址>', ''),
                  ('竞技场', f'{command} 竞技场 <昵称、账号ID 或网址>',
                   '不写名字时查自己' + ('；不写模式的查询也会看竞技场' if arena_by_default else '')),
                  ('绑定', f'{command} 绑定 <玩家页网址或账号ID>', ''),
                  ('解绑', f'{command} 解绑', ''),
                  ('帮助', f'{command} 帮助', '')],
            rule=(f'不写模式时先看 {label}，没有档案就自动换成有档案的模式'
                  + ('，也会看竞技场。' if arena_by_default else '；竞技场要写明。')),
            steps_title='三、第一次查询',
            steps=[('在 tarkov.dev 选好模式，搜索这名玩家的游戏名：', search_url(mode).removeprefix('https://')),
                   ('把跳转后的玩家页网址发给我，例如：', f'{command} {page_url(mode, 1234567)}'),
                   (f'之后就能直接用名字查（{remembered}）。', None)],
            notes=[f'由于 {BOT_LIMIT}，机器人无法直接触发更新。要看最新数据，请先在 tarkov.dev 上重新搜索或打开这名玩家的页面，几分钟后再查。',
                   NAME_RULE],
            foot='数据来自 tarkov.dev 公开快照（非实时）')
    card['rows'] += feature_rows(command, features)
    return card


def help_text(card):
    """The same help as plain text, for when the picture cannot be drawn."""
    lines = ['塔科夫数据查询', card['note']]
    lines += [f'{what}：{command}' + (f'（{remark}）' if remark else '') for what, command, remark in card['rows']]
    lines += ['模式：pvp、pve、赛季（pvp-season）、竞技场（arena）。'+card['rule']]
    lines += [text + (command or '') for text, command in card['steps']]
    return '\n'.join([*lines, *card['notes'], card['foot']])
