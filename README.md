[![AI Assisted](https://img.shields.io/badge/AI-Claude%20Opus%205.5%20%2B%20Claude%20Fable%205.2%20%2B%20Claude%20Code-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](#ai-制作声明) [![AI Assisted](https://img.shields.io/badge/AI-OpenAI%20Codex-412991?style=for-the-badge&logo=openai&logoColor=white)](#ai-制作声明)

<a id="ai-制作声明"></a>

> [!IMPORTANT]
> **AI 制作声明：塔科夫数据查询采用生成式 AI 参与架构设计、编码、文档与测试。** AI 生成或修改的内容由项目维护者审阅、验证并承担最终维护责任。
>
> 参与的 AI：**Claude Opus 5.5**、**Claude Fable 5.2**（均通过 **Claude Code**）、**OpenAI Codex**；Boss 图的背景纹理由维护者用 **GPT-Image-2.5-sunburst** 生成。

# 塔科夫数据查询

在 AstrBot 聊天中查询《逃离塔科夫》的数据：玩家档案、服务器状态、Boss 刷率、各服务器的平均匹配时间，以及指定玩家的“近 7 天”周报。玩家档案默认来自 [Atlas](https://eftforge.com/atlas)，支持 PVP、PVE、赛季和竞技场，每次只查指定的一个模式；可切换到 tarkov.dev 快照。服务器状态和 Boss 刷率来自 [tarkov.dev](https://tarkov.dev)，匹配时间和玩家周报来自 Atlas。

Atlas 竞技场采用选手名册图片：排位／非排位汇总表、各模式战绩、成就完成数／总数，以及按稀有度和获得时间排序的前 9 项成就。顶部带 Atlas 标志、来源和链接，底部注明数据时间和特别致谢。成就名称与描述直接使用 Atlas 返回的中文，缺少中文时回退英文；插件不翻译。见 [玩家查询说明](docs/player-query.md#atlas-玩家查询默认)。

## tarkov.dev 对机器人调用的限制与现状（玩家档案）

**只有在 tarkov.dev 上被搜索过的玩家才有档案，档案不会自动更新，第一次查某个玩家之前要先到网站搜索他一次。** 默认 Atlas 查询不需要这一步，可在命令末尾加“刷新”重新查询。详细说明见 [玩家数据查询](docs/player-query.md#tarkovdev-对机器人调用的限制与现状)。特别感谢作者 Morph1ne，其维护的项目 [**Atlas**](https://eftforge.com/atlas) 解决了长期以来的这部分难题。

## 功能

- **[塔科夫玩家数据查询](docs/player-query.md)**：绑定自己的账号，用昵称、账号 ID 或玩家页网址查询自己或他人的档案——等级、阵营、PMC 与 Scav 的战局、幸存率、K/D、技能；支持 PVP、PVE、PVP 赛季和塔科夫竞技场（段位、ARP、胜率）。
- **[服务器状态与 Boss 刷率](docs/status-and-boss.md)**（0.4.5 新增，默认关闭）：服务器各组件状态；各地图日常 Boss 与三狗的刷率全表，另显示活动 Boss；两者都可开启变化推送。
- **[服务器匹配时间](docs/matching.md)**（0.5.0 新增，默认开启）：各数据中心的平均匹配等待时间和最近 24 小时走势，列出各地区最快、最快的 8 个数据中心，以及香港、日本、韩国三个常用服务器；数据来自 Atlas，所有会话共用一份结果，15 分钟更新一次。
- **[玩家周报](docs/weekly-report.md)**（0.6.0 新增，默认关闭，娱乐用途）：每个聊天各自维护周报名单（AstrBot 管理员在聊天里用命令添加玩家），插件每天凌晨采样一次，生成“近 7 天”的个人周报和排行图；可每周定时把各聊天自己的排行发到该聊天。数据来自 Atlas；竞技场周报尚未实现。
- **通用**：所有回复都是本地绘制的图片；所有人、仅管理员、管理员和指定用户三种权限，也可限定聊天；支持 QQ 官方机器人：`@机器人 /命令` 写法，以及不含空格的单词命令（tpve、tstatus、tboss、tmatch、tweekly 等）。

## 安装与配置

需要 **AstrBot ≥ 4.16.0**。

1. 将插件安装到 AstrBot 的插件目录，或在插件管理页导入发行压缩包。
2. 在 AstrBot 的 Python 环境安装依赖：`pip install -r requirements.txt`（`aiohttp`、`yarl`、`pillow`，AstrBot 本身都已带）。
3. 按需修改默认模式、谁可以使用等设置，保存后生效。默认所有人都可以使用玩家查询和匹配时间；服务器状态、Boss 刷率和玩家周报默认关闭，需要管理员在配置页打开（周报名单在聊天里用命令维护）。注意：昵称名单默认开启，第一次按昵称查 PVP 会下载约 30 MB，需要等几十秒（可在“玩家数据查询”里关闭）。

## 开始使用

请到对应文档查看命令与用法：

- [玩家档案查询（绑定账号、PVP／PVE／赛季／竞技场）](docs/player-query.md#atlas-玩家查询默认)
- [服务器状态与 Boss 刷率](docs/status-and-boss.md)
- [服务器匹配时间](docs/matching.md)
- [玩家周报（名单、采样规则、每周播报）](docs/weekly-report.md)

## 回复方式

所有查询结果都是**图片**，用开源字体在本地绘制，不经过在线渲染服务。Atlas 玩家档案使用深色面板，竞技场使用选手名册风格，tarkov.dev 快照使用情报档案风格；图片后附一行可点击的来源链接。图片最下方写明插件名称、版本、仓库和开源许可。

- 找不到玩家、同名列表、绑定确认、用法提示和出错提示用一行或几行文字回复；图片生成失败时也回复文字说明。
- `@机器人 tarkov 帮助` 回复一张用法图片。
- 所有图片统一使用完整中文字体，首次生成前下载约 17 MB，之后复用本地文件；下载失败会提示，可在设置中填写镜像地址。

0.7.0 起不再提供“文字”和“Markdown 文字”两种输出方式。

图片示例（虚构数据，点击查看）：[Atlas 玩家档案](docs/atlas_pve_preview.jpg)、[玩家档案快照](docs/preview.jpg)、[竞技场快照](docs/arena_preview.jpg)、[Atlas 竞技场](docs/arena_legacy_atlas_reference_v2.jpg)、[帮助](docs/help_preview.jpg)、[个人周报](docs/weekly_report_preview.jpg)、[周报排行](docs/weekly_ranking_preview.jpg)。Atlas 玩家档案示例使用图标占位；竞技场示例含排位演示，实际只显示接口有记录的分区。服务器状态、Boss 刷率和匹配时间的示例图见 [status-and-boss.md](docs/status-and-boss.md) 和 [matching.md](docs/matching.md)。

## 通用设置

配置页分成六组：通用、玩家数据查询、Atlas 查询后端（匹配时间）、服务器状态、Boss 刷率、玩家周报。

- **命令写法**：`@机器人 tarkov`（默认）或 `/tarkov`，只影响帮助和回复里怎么显示命令。两种写法都能用；在 QQ 官方平台，`@机器人 /tarkov …` 也一直能用，不需要开关。
- **QQ 官方机器人单词命令**（默认开启）：QQ 官方机器人的命令里不能有空格，所以另有一组单词命令：`tpvp`、`tpve`、`tseason`、`tarena`（查玩家）、`tbind`、`tunbind`、`thelp`（绑定、解绑、帮助）、`tstatus`、`tboss`、`tmatch`（状态、Boss、匹配时间）、`tweekly`（玩家周报）。后面的参数可以带空格，例如 `@机器人 tpve 昵称`。把它们加进 QQ 机器人后台的命令列表即可；只对 QQ 官方平台生效。
- **谁可以使用**：所有人（默认）、仅管理员，或管理员和指定用户。“管理员”指 AstrBot 管理员，不是 QQ 群管理员。
- **指定用户 / 限定聊天**：填写平台用户 ID（例如 QQ 号）和 AstrBot 会话标识（聊天中发送 `/sid` 查看），留空不限制聊天。手工编辑配置文件时应为列表；写成其他类型时拒绝所有人。
- **访问数据接口时使用代理 / 代理地址**：默认关闭。开启后访问 tarkov.dev 和 Atlas 的请求都经过这个 HTTP/HTTPS 代理（不支持 SOCKS，需写明端口），不使用 AstrBot 或系统的代理设置；代理连不上时不会改为直连。部分服务器的 IP 会被 Cloudflare 拦截，查询提示“HTTP 403”时可以开启。

各功能自己的设置见对应的文档：[玩家数据查询的设置](docs/player-query.md#玩家功能的设置)、[状态与 Boss 的设置](docs/status-and-boss.md)、[匹配时间的设置](docs/matching.md#设置)、[玩家周报的设置](docs/weekly-report.md#设置)。

## 数据存储

数据位于 `data/plugin_data/astrbot_tarkov_datasearch/`，具体文件见功能文档的“数据存储”一节：[玩家功能](docs/player-query.md#数据存储玩家功能)、[状态与 Boss](docs/status-and-boss.md#数据存储状态与-boss)、[玩家周报](docs/weekly-report.md#数据存储)；匹配时间只保存在内存里，不写入磁盘。绑定只保存带密钥的哈希，不保存 QQ 号等原始 ID；用过本插件命令的群会话（只存会话标识，不存用户）会被记住，作为没填推送目标时状态和 Boss 变化的推送对象（周报播报只发给有周报名单的聊天）；插件不另存“谁查询了谁”，但 AstrBot 的日志会记下每条消息的发送者和内容，请像对待聊天记录一样保管日志和整个数据目录。

## 特别致谢

- **[Atlas](https://eftforge.com/atlas)**（[EFTForge](https://github.com/SouthHorizons76/EFTForge) 项目的一部分，作者 Morph1ne / SouthHorizons76）

## 开源许可

本项目采用 **GNU General Public License v3.0 or later（GPL-3.0-or-later）**。完整许可见 [LICENSE](LICENSE)，数据来源与第三方声明见 [THIRD_PARTY.md](THIRD_PARTY.md)。

Boss 图的背景纹理是 AI 生成的图片（见 [THIRD_PARTY.md](THIRD_PARTY.md)）。图片使用的三款字体（完整 Noto Sans SC、Oswald、Share Tech Mono）采用 SIL Open Font License 1.1，许可文本放在 `tarkov_core/fonts/`。完整中文字体首次使用时下载到插件数据目录，两个西文字体随插件提供。文档里的 Boss 示例图含有 Boss 头像，是 Battlestate Games 的游戏美术，不适用本项目的许可，也不包含在插件包里。

本插件与 Battlestate Games、tarkov.dev 和 Atlas 没有关联。

## 文档

- [塔科夫玩家数据查询](docs/player-query.md)：命令、模式、绑定、竞技场、数字含义、设置和数据存储。
- [服务器状态与 Boss 刷率](docs/status-and-boss.md)：命令、设置、读图方法、活动 Boss 规则、变化推送和数据存储。
- [服务器匹配时间](docs/matching.md)：命令、读图方法、缓存与请求频率、设置。
- [玩家周报](docs/weekly-report.md)：命令、名单维护、读图方法、采样规则、设置和数据存储。
- [开发指南](docs/development.md)：模块地图、开发约束、测试和示例图。
- [CHANGELOG.md](CHANGELOG.md)：每个版本给使用者带来了哪些变化。
