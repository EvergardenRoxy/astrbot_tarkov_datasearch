"""The help as an image: a page of the intel dossier telling how to use the plugin (maintainer: the text help is hard
to read in phone chat apps).

Drawn like image.py, with larger type than the profile card because the page is mostly read on phones. The wording
follows the text help in main.py (help_text); both take the same settings.
"""
from PIL import Image, ImageDraw

from .image import FADED, INK, LINE, RED, W, fit, font, footer, jpeg, paper, safe, wrap

TAPE, TAPE_TEXT = (44, 40, 34), (226, 214, 186)
LABEL_X, TAPE_X = 40, 196


def help_rows(card):
    """(what, command, remark) of the command table."""
    arena = '不写名字时查自己' + ('；不写模式的查询也会看竞技场' if card['arena_by_default'] else '')
    return [
        ('查自己', '/tarkov [模式]', '需要先绑定'),
        ('查别人', '/tarkov [模式] <昵称、账号ID 或网址>', ''),
        ('竞技场', '/tarkov 竞技场 <昵称、账号ID 或网址>', arena),
        ('绑定', '/tarkov 绑定 <玩家页网址或账号ID>', ''),
        ('解绑', '/tarkov 解绑', ''),
        ('帮助', '/tarkov 帮助', ''),
    ]


MODE_CHIPS = (('pvp', 'PVP', 'pvp / 常规'), ('pve', 'PVE', 'pve'), ('season', 'PVP 赛季', '赛季 / season'),
              ('arena', '竞技场', '竞技场 / arena'))


def tape(img, x, y, text, size=24, width=None):
    """A strip of dark label tape with `text` on it, the command to type; the y below it."""
    d = ImageDraw.Draw(img)
    width = width or W-40-x
    f = fit(d, text, 'sc', size, width-28, 500, smallest=14)
    w = min(width, d.textlength(text, font=f)+28)
    d.rectangle([x, y, x+w, y+42], fill=TAPE)
    # Punched edge marks, like embossed label tape.
    for k in range(int(x)+6, int(x+w)-4, 14):
        d.point([(k, y+3), (k, y+38)], fill=(84, 78, 66))
    d.text((x+14, y+21), text, font=f, fill=TAPE_TEXT, anchor='lm')
    return y+42


def heading(d, y, text):
    d.text((40, y), text, font=font('sc', 26, 800), fill=INK)
    return y+48


def paragraph(d, y, text, x=40, size=20, fill=INK, width=None):
    f = font('sc', size, 400)
    for line in wrap(d, safe(text), f, width or W-40-x):
        d.text((x, y), line, font=f, fill=fill)
        y += size+12
    return y


def guide_stamp(img):
    mark = Image.new('RGBA', (230, 104), (0, 0, 0, 0))
    md = ImageDraw.Draw(mark)
    md.rectangle([4, 4, 225, 99], outline=RED+(210,), width=5)
    md.rectangle([12, 12, 217, 91], outline=RED+(170,), width=2)
    md.text((30, 20), '使用指南', font=font('sc', 34, 900), fill=RED+(210,))
    md.text((32, 64), 'FIELD MANUAL', font=font('mono', 18), fill=RED+(210,))
    mark = mark.rotate(-10, expand=True, resample=Image.BICUBIC)
    img.paste(mark, (W-290, 22), mark)


def render_help_image(card):
    """JPEG bytes of the help page. card: mode (key of the default mode), label (its name), search (the tarkov.dev
    search page in that mode), example (a player page in that mode), arena_by_default, index (how often the name
    lists are refreshed, or None without the name lists)."""
    img = paper(2400)
    d = ImageDraw.Draw(img)

    d.rectangle([40, 30, 330, 70], fill=(186, 172, 136))
    d.text((56, 38), 'FILE NO. HELP-01', font=font('mono', 22), fill=INK)
    d.text((40, 90), '塔科夫市 · 档案查询指南', font=font('sc', 36, 800), fill=INK)
    d.text((42, 140), 'TARKOV DOSSIER QUERY GUIDE  /  DEFAULT: '+safe(card['label'], latin=True), font=font('mono', 18),
           fill=FADED)
    d.line([40, 172, W-40, 172], fill=INK, width=2)
    d.line([40, 177, W-40, 177], fill=INK, width=1)
    guide_stamp(img)
    d = ImageDraw.Draw(img)

    y = heading(d, 206, '一、常用命令')
    for what, command, remark in help_rows(card):
        d.text((LABEL_X, y+21), what, font=font('sc', 24, 700), fill=INK, anchor='lm')
        y = tape(img, TAPE_X, y, command)
        if remark:
            y = paragraph(d, y+8, remark, x=TAPE_X, size=18, fill=FADED)-4
        y += 18
    d.text((40, y), '/tarkov 也可以写作 /塔科夫；[ ] 里的可以不写，< > 换成实际内容。', font=font('sc', 18, 400), fill=FADED)
    y += 46
    d.line([40, y, W-40, y], fill=LINE)

    y = heading(d, y+24, '二、模式写法')
    chip_w = (W-80-3*14)/4
    for i, (key, label, words) in enumerate(MODE_CHIPS):
        x = 40+i*(chip_w+14)
        default = key == card['mode']
        d.rectangle([x, y, x+chip_w, y+86], outline=RED if default else INK, width=3 if default else 2)
        d.text((x+chip_w/2, y+28), label, font=font('sc', 24, 800), fill=INK, anchor='mm')
        d.text((x+chip_w/2, y+64), words, font=fit(d, words, 'sc', 18, chip_w-16, 400), fill=FADED, anchor='mm')
        if default:
            d.rectangle([x+chip_w-48, y-12, x+chip_w+6, y+14], fill=RED)
            d.text((x+chip_w-21, y+1), '默认', font=font('sc', 16, 800), fill=TAPE_TEXT, anchor='mm')
    y += 104
    rule = (f"不写模式时先看 {card['label']}，没有档案就自动换成有档案的模式"
            + ('，也会看竞技场。' if card['arena_by_default'] else '；竞技场要写明。'))
    y = paragraph(d, y, rule)
    y += 14
    d.line([40, y, W-40, y], fill=LINE)

    y = heading(d, y+24, '三、第一次查询')
    remembered = '按 ID 或网址查到过的名字会被记住'
    if card['index']:
        remembered += f"，也会查昵称名单（{card['index']}更新一次）"
    steps = [
        ('在 tarkov.dev 选好模式，搜索这名玩家的游戏名：', card['search'].removeprefix('https://')),
        ('把跳转后的玩家页网址发给我，例如：', '/tarkov '+card['example']),
        ('之后就能直接用名字查（'+remembered+'）。', None),
    ]
    for i, (text, command) in enumerate(steps, 1):
        d.ellipse([40, y, 76, y+36], outline=INK, width=2)
        d.text((58, y+18), str(i), font=font('osw', 22, 600), fill=INK, anchor='mm')
        y = paragraph(d, y+3, text, x=92)
        if command:
            y = tape(img, 92, y+4, command, size=22)
        y += 20
    d.line([40, y, W-40, y], fill=LINE)

    y = heading(d, y+24, '四、注意')
    for text in ('档案不会自动更新：要看最新数据，请先在 tarkov.dev 上重新搜索或打开这名玩家的页面，几分钟后再查。',
                 '昵称只含字母、数字、下划线 _ 和连字符 -；_ 和 - 容易记混，查不到时插件会提示相近的名字。'):
        d.rectangle([44, y+10, 54, y+20], fill=INK)
        y = paragraph(d, y, text, x=70)+10
    y += 10
    d.line([40, y, W-40, y], fill=INK)
    return jpeg(img.crop((0, 0, W, footer(d, y, None, FADED, FADED, line='数据来自 tarkov.dev 公开快照（非实时）'))))
