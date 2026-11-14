# 开发指南

一个 AstrBot 插件 **astrbot_tarkov_datasearch（塔科夫数据查询）**：入口 [main.py](../main.py)，声明见 [metadata.yaml](../metadata.yaml)，功能说明见 [README](../README.md) 和 `docs/` 下四份功能文档。

## 模块地图

`main.py` 只做四件事：注册命令、检查权限、把第一个词分发给对应功能、管理启动和关闭。功能是普通的类，在 `TarkovPlugin.__init__` 里组装，共用一个 `App` 对象。

| 位置 | 内容 |
| --- | --- |
| `tarkov_core/app.py` | `App`：设置、HTTP 客户端、账号库、Atlas 客户端、公开数据缓存；`render`（出图）、`busy`（每人同时一个查询）、权限判断 |
| `settings.py`、`_conf_schema.json` | 配置项及默认值（`INTS`、`ON_BY_DEFAULT` 与 schema 一致，由 `test_release` 核对） |
| `player_commands.py` | Atlas 玩家查询、绑定、刷新、失败时回退快照 |
| `snapshot_commands.py` | tarkov.dev 快照查询、昵称名单 |
| `status_commands.py`、`boss_commands.py`、`matching_commands.py` | 服务器状态、Boss 刷率、匹配时间 |
| `weekly_commands.py` | 周报命令、各聊天名单、采样与播报的组装 |
| `push_targets.py` | 推送目标：记住用过命令的群、过滤不可推送的会话 |
| `help_text.py` | 帮助内容（图片和文字共用一份） |
| `atlas_client.py`、`atlas_cooldown.py`、`atlas_players.py` | Atlas 请求、共用间隔与额度、玩家缓存与每人额度 |
| `transport.py`、`public_api.py`、`resource_cache.py` | HTTP、tarkov.dev 公开数据及其磁盘缓存 |
| `accounts.py`、`names.py`、`profile.py`、`arena.py`、`atlas_profile.py` | 绑定、昵称名单、档案数据整理 |
| `server_status.py`、`boss_monitor.py`、`notifications.py`、`boss_notifications.py`、`polling.py` | 状态/Boss 归一化、变化检测、推送队列、后台循环 |
| `weekly_report.py`、`weekly_store.py`、`weekly_tasks.py` | 周报计算、数据库、夜间采样与每周播报 |
| `*_image.py`、`card.py`、`atlas_card.py`、`image.py`、`fonts.py`、`font_download.py` | 本地绘图和字体 |
| `tests/` | 单元测试；`plugin_harness.py` 让测试通过聊天命令入口驱动整个插件，`tests/astrbot/` 是 AstrBot 的测试替身 |
| `tools/` | 示例图脚本（`build_*_preview.py`）、`build_static.py`（静态数据）、`download_font.py`、`package_plugin.py`（打包） |

打包脚本只收集运行所需的根文件和 `tarkov_core/`，不含文档、测试和工具。

## 开发约束

- 仓库、测试和示例图只使用虚构玩家数据，不提交真实昵称、账号 ID 或统计。
- 不绕过 Turnstile 或上游限流。网络请求保持固定来源、禁止自动跳转、`trust_env=False`、大小与超时上限和诚实的 User-Agent；接口错误不能解释成游戏停服、零刷率或玩家不存在。
- Atlas 是个人维护的服务：玩家请求共用间隔和总额度；上游要求的等待时间不得缩短；401/403 后同类请求暂停 30 分钟。后台请求只有周报名单采样一种：UTC+8 03:00～09:00，一步一个请求，间隔 140～180 秒，错过不在白天补拉。其他功能不轮询、不预取、不批量请求 Atlas。
- 失败或损坏的响应不能覆盖成功的缓存或变化基线。
- 图片在本地绘制，不调用远程渲染，不裁剪或修改字体。回复里的命令写法通过 `App.cmd()` 生成，图片后保留可点击的来源链接。
- 关闭的功能不发请求、不启动后台任务。Boss 头像只由一次性设置下载。
- 绑定数据（`accounts.sqlite3`、`binding.key`）的格式不能破坏。

## 测试

```sh
python -m pip install -r requirements.txt
python tools/download_font.py
python -m unittest discover -s tests
```

`download_font.py` 把完整 Noto Sans SC 下载并校验到 `.cache/fonts/`（不提交、不打包）；离线时用环境变量 `TARKOV_TEST_FONT` 指向同版本字体文件。测试不联网、不查询真实玩家。

在 Windows 上有三项已知失败，与平台有关而不是代码问题：`test_accounts` 的密钥文件权限检查，以及 `test_names` 两项含特殊字符目录的检查。

单元测试不能证明的事：真实 AstrBot 的命令路由、QQ 官方平台的图片回复和主动推送、真实 Atlas 的限流行为。发布前请在测试群里各试一次。

## 示例图

```sh
python tools/build_preview.py
python tools/build_arena_legacy_preview.py
python tools/build_atlas_player_preview.py pve
python tools/build_weekly_preview.py
```

玩家、竞技场、周报和帮助示例使用虚构数据；`build_public_preview.py` 和不传已保存响应的 `build_matching_preview.py` 会读取公开服务数据。游戏静态数据变化时运行 `python tools/build_static.py` 并审阅差异。字体许可见 [THIRD_PARTY.md](../THIRD_PARTY.md)。
