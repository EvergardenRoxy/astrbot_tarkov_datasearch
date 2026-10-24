# Release version; metadata.yaml, the @register literal in main.py and CHANGELOG.md must match (tests check it).
VERSION = '0.4.5hotfix'
# Shown at the foot of every image reply (maintainer). metadata.yaml `repo` and LICENSE must match (tests check it).
NAME = 'astrbot_tarkov_datasearch'
REPO = 'https://github.com/EvergardenRoxy/astrbot_tarkov_datasearch'
LICENSE = 'GPL-3.0-or-later'
# Open-source statement at the end of every profile reply (maintainer's wording, spaced like the other chat texts).
NOTICE = f'本插件在 GitHub 开源，遵循 {LICENSE} 许可；与 Battlestate Games 无直接关系'
# How commands are written in every reply and in the help, per the "command_style" setting (maintainer): "mention" for
# platforms where the bot is called by mentioning it without a slash and "slash" for platforms woken by "/". "mention" is
# the default. Only the wording changes: a slash after a QQ mention is always accepted. The only place these command
# strings may be written (tests check it).
COMMAND_STYLES = {
    'mention': {'command': '@机器人 tarkov',
                'help': '群里先 @机器人，再发送下面的命令（不加 /）；tarkov 也可以写作 塔科夫',
                'note': '群里先 @机器人 再发命令，不加 /；tarkov 也可写作 塔科夫；[ ] 可不写，< > 换成实际内容。'},
    'slash': {'command': '/tarkov',
              'help': '命令以 / 开头；/tarkov 也可以写作 /塔科夫',
              'note': '命令以 / 开头；/tarkov 也可写作 /塔科夫；[ ] 可不写，< > 换成实际内容。'},
}
