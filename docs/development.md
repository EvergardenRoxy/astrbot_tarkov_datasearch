# 开发指南

> 2026-11-11 整理，代码基线 `de6e2a6`（`fix/atlas-rev7-api-rules`），版本 v0.6.1。当前任务和验证状态只在 [checklist](../checklist.md) 维护。

## 插件与功能范围

本仓库提供一个 AstrBot 插件 **astrbot_tarkov_datasearch（塔科夫数据查询）**，入口为 [main.py](../main.py)，声明见 [metadata.yaml](../metadata.yaml)。

- [玩家档案](player-query.md)：搜索、查询、绑定 PVP/PVE/赛季/竞技场账号。默认 Atlas，一次一个模式；可选择或回退到 tarkov.dev 快照。Atlas 输出图片，快照保留图片、文字和 Markdown。
- [服务器状态与 Boss 刷率](status-and-boss.md)：两项独立功能，查询和变化通知默认关闭。状态是服务状态，Boss 是公开刷率，均不代表当前对局位置。
- [匹配时间](matching.md)：Atlas 平均等待时间与历史曲线，默认开启，只输出图片。
- [玩家周报](weekly-report.md)：名单内 PVP/PVE/赛季玩家的数字样本、个人报告、排行及可选播报，默认关闭；Arena 周报尚未实现。

Atlas/EFTForge、tarkov.dev 是数据来源；TarkovTracker 是研究参考；`tests/astrbot/` 是测试替身，均不是本仓库的独立插件。[物品查询](archive/item-query.md)已归档。密钥、玩家索引、封禁推测、完整 3D 渲染和变更集 API 未因 rev7 更新而启用。

## 代码与工具入口

- `main.py`：命令、权限、账号绑定、数据来源编排和后台任务生命周期；`_conf_schema.json` 与 `tarkov_core/settings.py`：配置。
- `tarkov_core/atlas_*.py`：Atlas 请求、访问暂停、冷却、资料/图片/历史；`accounts.py`、`names.py`、`profile.py`、`arena.py`：账号、昵称和快照。
- `server_status.py`、`boss_*.py`、`notifications.py`、`polling.py`：状态/Boss 归一化、变化队列与推送；`matching*.py`：匹配缓存与图。
- `weekly_report.py`、`weekly_store.py`、`weekly_tasks.py`、`weekly_image.py`：数字提取与计算、数据库、采样/播报、出图。
- `transport.py`、`public_api.py`、`resource_cache.py`：网络和公开资源；`fonts.py`、`font_download.py` 及各图片模块：本地渲染。
- `tests/`：回归、虚构数据和 SDK 替身；`.github/workflows/tests.yml`：CI。核心目录有 43 个 Python 文件，测试目录有 36 个 Python 文件，文件数不等于用例数。
- `tools/`：7 个预览脚本；另有 `build_static.py`（静态数据）、`download_font.py`（字体）、`package_plugin.py`（打包）、`verify_qq_routes.py`（真实 SDK 路由离线回放）。

[打包脚本](../tools/package_plugin.py)仅收集运行所需根文件和 `tarkov_core/`，不包含开发文档、测试、工具和交接记录。缓存、虚拟环境、`dist/` 等本地产物不计入已提交文件。

## 文档分工

- [README](../README.md) 与四份功能说明：安装和使用；配置具体行为以当前功能说明和 schema 为准。
- [checklist](../checklist.md)：唯一的当前待办、已知问题和验证状态入口；完成项移入版本记录或决策摘要，不重复抄录测试流水账。
- [CHANGELOG](../CHANGELOG.md)：版本对用户的变化；[handoff](../handoff.md)：新工作的短交接记录，引用专题说明。
- [开发决策摘要](archive/development-history.md)：合并原来的六份计划/审查，保留决策、实施结果、原文固定提交入口。
- [历史工程交接](archive/engineering-history.md)：截至本次整理前的完整交接记录；顶部过时状态仅代表历史，不能覆盖当前任务页。
- [公开接口参考](tarkov-api-reference.md)、[Atlas 探测证据](atlas-probe-record.md)、[TarkovTracker 调研](tarkovtracker-reference.md)：按需查询，结论均受原核对日期限制。
- [第三方声明](../THIRD_PARTY.md)、[物品归档](archive/item-query.md)：分别保存许可和退出运行范围的功能信息。

旧 checklist 的逐版本说明已从活动任务页移除，原文见[整理前版本](https://github.com/EvergardenRoxy/astrbot_tarkov_datasearch/blob/de6e2a6920e504ac5d762c63543c77d5ad5ef7f4/checklist.md)。新登记的五项问题完整保留在当前 checklist。历史交接只归档，不改写其结论；相关本地链接随目录位置更新。

## 开发约束

- 先确认行为变更范围，再实施；已获授权的工作直接继续。日常只更新对应专题、任务状态和简短交接；发布时再统一版本与 CHANGELOG，避免复制多份进度。
- 仓库、测试和预览只使用虚构玩家数据，不提交真实昵称、账号 ID、统计或其派生数字。
- 不绕过 Turnstile 或上游限流。网络保持固定来源、禁止自动跳转、`trust_env=False`、大小/超时边界和诚实 User-Agent；接口错误不能解释为游戏停服、零刷率或玩家不存在。
- 图像在本地绘制，不调用远程 HTML/图片渲染；不裁剪或修改字体。帮助和资料输出保留可点击来源链接；命令提示通过 `TarkovPlugin.cmd()` 生成。
- 失败或损坏响应不能覆盖成功缓存/基线。缓存、退避和管理员恢复各司其职；不要因恢复访问清空额度或缩短仍有效的上游等待。已记录的重建冷却边界见 checklist K02。
- Atlas 后台请求仅限已授权的周报名单采样：UTC+8 03:00～09:00，步骤间隔 140～180 秒，一步最多一个 HTTP，错过不在白天补拉。其他 Atlas 功能不暗中轮询、预取或批量请求。
- 关闭功能应停止请求和任务，旧分页不可继续访问；状态/Boss 的轮询和 QQ 主动推送各有独立开关，Boss 头像只由显式一次性动作下载。
- 保留当前数据；缓存政策未明确前不新增清理。关闭 Atlas 历史记录的现有开关会删除旧记录，不能拿它实现单纯“停止写入”。

## 验证与维护

在仓库根目录准备依赖和字体后运行单元测试（命令见下节）。打包使用 `python tools/package_plugin.py`；QQ 路由核对使用 `python tools/verify_qq_routes.py <AstrBot-4.16.0.whl>`，其结果仅证明离线路由，不证明平台送达。游戏静态数据变化时运行 `python tools/build_static.py` 并审阅差异。

记录检查日期、Python/系统环境、通过/失败和实机验收边界。不要把旧环境的一次通过当作当前所有平台均通过，也不要为了文档改动重跑与之无关的线上查询。

## 完整字体与图片开发

所有图片通过 `tarkov_core/fonts.py` 使用同一份完整 Noto Sans SC 字体，不需要收集中文字符、制作裁剪字库或修改字体文件。Oswald 和 Share Tech Mono 仍随插件提供，字体许可见 [THIRD_PARTY.md](../THIRD_PARTY.md)。

插件首次生成图片前，会通过现有代理和镜像设置下载固定版本的完整字体（约 17 MB），校验大小与 SHA-256，存入插件数据目录的 `fonts/`。并发请求共用一次下载，字体下载失败时给出文字提示，30 分钟后可再次尝试。已经缓存并校验的字体会跨重启复用。

本地测试或生成示例图之前，在仓库根目录执行：

```sh
python -m pip install -r requirements.txt
python tools/download_font.py
python -m unittest discover -s tests -v
```

准备脚本只下载并校验原始字体文件，缓存到 `.cache/fonts/`，不生成字库。该目录不会提交到 Git，也不包含在插件安装包中。测试不会自行下载字体或查询真实玩家；CI 在测试前单独准备字体。

离线使用时，可指向已取得的同版本文件：

```sh
TARKOV_TEST_FONT='/absolute/path/NotoSansSC[wght].ttf' python -m unittest discover -s tests -v
```

示例图生成脚本使用相同缓存，也支持 `TARKOV_TEST_FONT`：

```sh
python tools/build_preview.py
python tools/build_arena_legacy_preview.py
python tools/build_atlas_player_preview.py pve
```

其中玩家与竞技场示例使用虚构数据；`build_public_preview.py` 和不传已保存响应的 `build_matching_preview.py` 会读取公开服务数据。新增中文文案或显示新的成就名称时，不需要额外更新字符清单。
