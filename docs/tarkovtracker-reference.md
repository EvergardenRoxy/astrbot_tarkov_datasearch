# TarkovTracker 数据来源与本插件参考点

核对日期：2026-10-24。目标仓库：[tarkovtracker-org/TarkovTracker](https://github.com/tarkovtracker-org/TarkovTracker)，核对 main 快照 `d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de`。本次为只读源码/文档调研，没有运行目标项目、访问玩家数据或取得私有修正数据。

## 1. 数据来源总览

| 数据类别 | 实际来源 | 项目处理方式 |
|---|---|---|
| 物品、分类、任务、藏身处、商人、地图、等级表、prestige | `https://json.tarkov.dev` | 服务端获取，翻译、转换字段/关联 ID，部分资源再应用 overlay |
| 游戏数据翻译 | 同一 JSON API 的 `_<语言码>` 字典 | 读取基础响应的 `translations` 路径，目标语言缺失时回退英文 |
| 游戏数据修正、补充任务、版本/剧情等元数据 | 自有 `tarkov-data-overlay` 修正层 | 对公共数据按 ID、模式、语言应用补丁，也能补充上游缺少的记录 |
| 玩家档案导入 | `https://players.tarkov.dev` | 导入网站生成的快照，包含等级/经验、技能、阵营、prestige 等；不是实时读取游戏账号 |
| 个人任务/藏身处/收集等进度 | 用户操作、日志/备份导入、授权第三方写入 Tracker | 浏览器持久保存；登录后与 Supabase 进度表同步 |
| 队伍、共享档案、跨设备进度 | Tracker 自己的 Supabase 数据 | 认证、可见性、队伍权限及实时同步约束下读取 |
| 物品图标、部分地图瓦片 | `https://assets.tarkov.dev`，以及仓库内地图配置/静态资源 | 图片 URL 与坐标、缩放、楼层等配置共同用于展示 |

公共游戏数据和玩家进度是两条数据链，不能把网站显示的任务完成状态当成 tarkov.dev 公共 API 提供的数据。

来源：[游戏数据架构](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/docs/systems/game-data.md)、[进度导入](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/docs/systems/imports.md)。

## 2. 公共游戏数据：JSON API、转换和修正

### JSON API

`app/server/utils/tarkov-json.ts` 的默认上游是 `https://json.tarkov.dev`，支持通过运行配置覆盖。路径按模式构造：`regular`、`pve`、`pvp-season`。内部模式名称为 `pvp/pve/seasonal`，与本插件的 `pvp/pve/season` 需要显式区分。

主要读取 `/items`、`/tasks`、`/hideout`、`/maps`、`/traders`，赛季元数据读取 `/pvp-season/season`。玩家等级表从 items 资源提取，不是另有一个实时等级服务。

浏览器通过项目的 `/api/tarkov/*` 服务端接口取得处理后的数据；例如 tasks-core 组合任务、地图和商人，items/items-lite 返回不同粒度的物品，map-spawns 从地图数据提取出生点。当前文档明确使用 JSON API，旧 `api.tarkov.dev` GraphQL 已退出项目的游戏数据路径。

服务端接口依赖 Cloudflare edge cache，部分数据预计算写入 KV；浏览器另有 IndexedDB 缓存。预计算是部署/缓存环节，不是另一个原始数据源。文档中的不可变版本发布架构还包含未完成目标，不应将整份架构规划都当作当前已部署行为。

来源：[上游读取与 adapter](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/server/utils/tarkov-json.ts)、[物品路由](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/server/api/tarkov/items.get.ts)、[预计算工作流](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/.github/workflows/precompute-tarkov-data.yml)。

### 翻译与字段转换

先获取基础资源，再并发获取目标语言和英文回退词典。根据 `translations` 路径替换文本；赛季翻译有额外完整性校验。adapter 再将上游 ID 索引对象变为应用所需数组、解析物品/任务/商人引用，保留业务需要的字段。

重要区别：`adaptItem()` 的 full 分支会保留原始物品字段；lite 分支偏向名称、链接、图片等引用信息。因此 full 响应可能携带价格字段，不代表项目已经实现了完整的跳蚤/商人价格查询交互。不能拿 lite 数据直接替代我们的模式价格对象。

源码位置：同一 [tarkov-json.ts](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/server/utils/tarkov-json.ts) 中 `fetchTarkovJsonEndpoint()`、`applyTranslations()`、`adaptItemFields()`、`adaptItem()`、`adaptItemsResponse()`。

### Overlay 修正层

默认地址在 `overlay.ts` 中为：

```text
https://raw.githubusercontent.com/tarkovtracker-org/tarkov-data-overlay/main/dist/overlay.json
```

它可修正任务等级、前置/目标条件、物品/设施资料，也可补充任务、游戏版本及剧情章节等数据；支持全局、模式和语言级补丁。赛季 perk 的基础定义仍来自 JSON season 数据，修正与效果投影再结合 overlay。

此处有权限边界：`overlayAuth.ts` 直接将其描述为私有仓库，工作流也使用只读 `OVERLAY_TOKEN`。本次没有取得 overlay JSON 内容，不将该默认 URL 当成无需授权即可稳定使用的公共 API。可参考补丁架构；本插件若将来引入修正数据，应另定可公开取得且可核对来源的规则，并保留版本、来源和修正前值。

来源：[overlay.ts](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/server/utils/overlay.ts)、[overlayAuth.ts](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/server/utils/overlayAuth.ts)、[版本/剧情/赛季投影](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/server/api/tarkov/editions.get.ts)。

## 3. 玩家档案和进度的来源

### tarkov.dev 档案导入

`tarkovDevProfileSource.ts` 将玩家页链接解析到以下快照：

```text
PVP:    https://players.tarkov.dev/profile/{aid}.json
PVE:    https://players.tarkov.dev/pve/{aid}.json
Season: https://players.tarkov.dev/pvp-season/{aid}.json
```

与我们插件现有数据源相同。Tracker 通过自身 `/api/tarkov-dev/profile` 代理导入，加入缓存、限频、结构检查和更新时间检查；用户需先在 tarkov.dev 打开对应玩家页刷新快照。重新请求 Tracker 的缓存绕过参数也不能保证快照已经在上游刷新。

来源：[链接到快照的映射](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/utils/tarkovDevProfileSource.ts)、[导入代理](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/server/api/tarkov-dev/profile.get.ts)。

### Tracker 自己记录的进度

用户可手动记录完成状态，也可导入本地 EFT 日志、备份，或使用授权第三方工具经 Tracker API 更新。EFT 日志导入只将明确的任务通知视为证据；不从一般日志或队友快照推断额外进度。

本地有 IndexedDB 进度仓库及 localStorage 兼容/恢复数据。云端主要使用 Supabase：`user_game_mode_progress` 按用户、模式和赛季保存进度；`user_progress` 保留账号元数据。写入通过 `sync_user_game_mode_progress` 等 RPC，队伍/共享资料使用对应权限读取。

对外的 `https://api.tarkovtracker.org` 是令牌授权的 Tracker 进度 API，不是按任意 EFT 玩家 ID 查询官方完整进度的接口。TarkovMonitor、RatScanner 等可作为授权客户端；未来若做 Tracker 进度查询，需要用户主动提供其授权方式，而不能沿用本插件目前绑定一个公开账号 ID 的语义。

来源：[日志导入流程](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/composables/useEftLogsImport.ts)、[本地进度仓库](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/stores/tarkov/progressRepository.ts)、[进度持久化](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/stores/tarkov/progressPersistence.ts)、[进度 API](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/docs/systems/progress-api.md)。

## 4. 图片与地图资源

`GameItem.vue` 优先使用物品记录中的图片链接，必要时用 `https://assets.tarkov.dev/{itemId}-icon.webp` 回退。仓库的 `app/data/maps.json` 存放地图楼层、坐标变换、边界、缩放等配置，部分实验室/迷宫瓦片直接指向 `assets.tarkov.dev/maps/...`。

这可以帮助我们的物品图片卡片确定图标选择、占位及图片失败处理。但 Vue 组件、交互地图和网页本身不等同于机器人生成的静态图片表格；本插件仍需 Pillow renderer。

来源：[GameItem.vue](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/components/ui/GameItem.vue)、[地图配置](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/data/maps.json)。

## 5. 与我们计划功能的对应关系

| 我们的功能 | 仓库中已核对的可参考实现 | 使用方式与边界 |
|---|---|---|
| 公共缓存 | `tarkovCache.ts`、`edgeCache.ts`、`tarkov-json.ts` | 模式/语言/资源/版本分离、并发合并、保留最后可用数据；沿用我们自己的 SQLite，不照搬浏览器 IndexedDB 或 Cloudflare 部署 |
| 物品通用属性与模式数据 | `adaptItem()`、`adaptItemsResponse()`、`itemPicker.ts` | 按 ID 关联、lite/full 数据分层、稀疏引用补全；我们要求的跨模式共同字段核对和两类 `/item` 命令仍需自行实现 |
| 中文物品/地图/Boss 名称 | `fetchTarkovJsonEndpoint()`、`applyTranslations()` | 基础数据与词典分别加载、目标语言到英文回退、按路径翻译；可借鉴解析与缺译处理测试 |
| 所有查询生成图片 | `GameItem.vue` 的物品图标选择和错误占位 | 可参考内容与图片资源，查询卡片/分页/字体/表格需用我们自己的图片渲染流程 |
| 服务器状态检查与播报 | 已核查的游戏数据路由未见对应实现 | 仍按我们直接读取 `/status`、快照比较和图片播报的设计实现；项目中进度“状态”不是 EFT 服务器状态 |
| Boss 全地图刷率表及变化轮询 | 地图基础读取、模式化缓存和翻译可以参考 | `adaptMapSpawnsResponse()` 只投影 `id/name/spawns`，不含 Boss 概率；不能把 map-spawns 当刷率接口，完整 `maps.*.bosses` 比较与标红需自行实现 |
| QQ 官方机器人路由/播报 | 项目是 Nuxt 网页应用，已核查代码未见 QQ 适配 | 使用我们自己的 AstrBot/QQ 路由与发送验收方案 |
| 现有玩家档案查询 | 快照地址校验、缓存与新旧数据提示 | 同一上游，重点参考快照时间、重新检查和 404/失败区分，不把快照导入解释成实时 API |

缓存参考：[浏览器缓存](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/utils/tarkovCache.ts)、[边缘缓存](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/server/utils/edgeCache.ts)、[稀疏物品引用补全](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/stores/tarkov/itemPicker.ts)。

后续优先参考顺序：**JSON 翻译处理 → 物品字段/引用转换 → 图片资源回退 → 可追溯修正层**。新功能继续基于我们已实现的公共缓存，不切换到 Tracker 的网站内部代理。

原因是项目自己的 [API 文档](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/docs/api.md) 明确说明 `/api/tarkov/*` 为内部浏览器接口，不是对外支持的游戏数据 API，建议外部集成直接使用 `json.tarkov.dev`。它们可用来阅读实现和数据组织方法，不能当成本插件新上游的稳定性承诺。

本次核查范围包括上述文档、实际服务端 adapter/路由、缓存、物品展示、导入及进度存储代码，并检索仓库文件名及相关关键词；“未发现现成实现”限定于这次核查，不等于对全部历史分支/关联仓库作穷尽证明。没有为本插件新增该项目的数据依赖，也没有实际复制其运行代码。

## 6. 玩家档案导入与本插件当前实现的比较

### 相同的上游，不同的用途

两者获取的都是 `players.tarkov.dev` 已有快照；Tracker 没有在此导入流程中调用一个额外的实时官方玩家接口。打开对应 tarkov.dev 玩家页刷新快照的前提相同。

Tracker 将导入结果写入自己的用户进度：包含 UID、昵称、阵营、prestige、经验推导等级、技能和版本推测，先预览，再由用户确认；它还支持本地 JSON 文件导入。文件导入也不能证明数据来自最新服务器状态。

我们的当前实现用于聊天查询：根据 ID、网址或已知昵称取得档案并展示 PMC/Scav 等统计，成功查询还学习昵称映射；绑定只保存平台用户到游戏账号的关系，不把档案当作个人任务进度库的更新来源。因此，防止错误覆盖进度的严格要求在两个产品中权重不同。

### 具体差异

| 方面 | Tracker 该导入流程 | 我们当前玩家档案查询 |
|---|---|---|
| URL 模式 | URL 决定档案模式，确认导入写对应模式 | 显式模式控制显示；主游戏三个模式一起查看以支持回退/提示，竞技场另有规则 |
| 缓存 | 服务端共享缓存默认 15 分钟；成功普通响应允许浏览器私有缓存；404 缓存 60 秒 | 内存摘要缓存默认 10 分钟、最多 128 条；404 默认最多 60 秒，受总 TTL 约束；重载清空 |
| 强制复查 | `fresh=1` 跳过旧响应直出，但可带缓存 ETag，304 复用 body | 发玩家页链接/绑定跳过 TTL；当前 `fetch_profile()` 未传 ETag，因此重新读取完整 body；仍可共享进行中的请求 |
| 数据年龄 | 当 `updated` 可用时，默认超过 7 天拒绝导入，可配置关闭门槛；缓存和 304 也要过年龄检查 | 显示快照更新时间；超过 30 天提醒，仍允许查看；另外标明插件缓存年龄 |
| 结构校验 | 写入前要求正数安全整数 aid、有效 info、非负安全整数经验、skills.Common 数组 | 容错展示：缺少姓名/info 时拒绝，很多其他缺失/无效字段按未知、缺省或跳过处理；已有有限数值和字符串清洗 |
| 防误导入 | 预览后确认、链接模式约束、账号 UID 冲突提示、异步请求/账号状态保护、保存失败反馈 | 查询直接回复；绑定前验证有档案并保存绑定，不承担同步个人进度库职责 |
| 请求频控 | 默认每 IP 每分钟 5 次、每小时 20 次；URL 导入成功后客户端默认冷却 60 分钟 | 同会话同用户防重入、同档案请求合并、全局最多 6 个档案网络请求；未加按分钟/小时配额 |
| 验证码 | 配置后对 Tracker 自己的导入代理校验 Turnstile | 没有网页验证码流程；机器人直接读公开静态快照 |
| 传输保护 | 该路由使用 10 秒上游超时和错误分类 | 档案请求最多 60 秒（还受设置约束），固定主机、禁重定向、gzip 双重大小限额、明确代理控制 |

7 天年龄规则对缺少有效 `updated` 的 payload 不会触发；不能把它描述成所有导入都获得了“7 天内更新”的保证。客户端 60 分钟冷却只是用户体验约束，服务端限频才是实际请求控制。Tracker 验证码只保护自己的代理，不刷新上游快照、不带来额外的官方查询权限。

我们刚完成的 `public-data/resources.sqlite3` 是公共 JSON 游戏资源缓存，尚未接到玩家 `fetch_profile()` 路径。它已支持持久化和条件请求，不等于玩家档案当前也已经具有这些能力。

### 优势与适合借鉴的改进

Tracker 的优势集中在“可安全写入个人进度”：预览/确认、关键字段严格校验、过期拒绝、结构化错误、保存状态和模式/账号切换保护。ETag 条件复查和共享缓存还能减少重复传输。其数据源本身不比本插件更实时。

对聊天查询，优先借鉴：

1. **给玩家档案增加 ETag 条件复查。** 玩家页 URL 仍触发复查，304 保留原始快照 `updated` 并更新检查时间；不能把检查时间写成档案刷新时间。缓存键保持模式和账号 ID，不把玩家 JSON 硬塞进公共资源白名单。
2. **增加关键身份字段一致性检查。** 确认请求 ID 与响应 aid，至少为绑定/身份映射使用更严格规则；统计字段继续按当前策略容错，避免一个可选字段缺失就整份档案不可查看。当前展示时覆盖 header ID 的措施不等于已经核对了上游身份一致性。
3. **更明确的图片年龄提示。** 区分档案更新时间、插件取得时间、最近检查时间；保留查看历史模式的能力。7 天强制拒绝不直接照搬，可把新鲜度限制作为可选设置，默认提醒而不是拒绝。
4. **按实际负载增加可配置查询/刷新频控。** 机器人按用户或账号而非网站 IP 分配额度，并与已有缓存、请求合并配合，不机械套用“每小时 20 次”或“导入后 60 分钟禁用”。

预览确认适用于将来可能新增的“写入/覆盖个人进度”操作；现阶段普通查询无需增加一轮确认。本轮只记录研究建议，未修改档案缓存、校验或交互；后续实施应保持所有查询图片输出及设置开关要求。

源码依据：[导入代理及缓存/年龄/限频](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/server/api/tarkov-dev/profile.get.ts)、[预览/确认/写入流程](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/composables/useTarkovDevImport.ts)、[档案字段校验](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/utils/tarkovDevProfileParser.ts)、[客户端导入冷却](https://github.com/tarkovtracker-org/TarkovTracker/blob/d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de/app/utils/tarkovDevImportCooldown.ts)。
