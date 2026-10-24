# tarkov.dev 公共游戏数据 API 参考

核对日期：2026-10-24。本文供本仓库后续接入使用，区分官方约定、当日实测与本项目接入建议。

## 1. 接口选择与数据边界

当前官方推荐使用 **JSON API**：`https://json.tarkov.dev`。它以 HTTP GET 返回完整资源文件；客户端在本地按名称、ID、类型等筛选并关联数据。

官方的 GraphQL 页面源码将 `https://api.tarkov.dev/graphql` 标为维护模式：保持现有功能，但不再修改 schema，建议后续使用 JSON API。旧 GraphQL 的查询方式见第 9 节。

这些接口提供物品、价格、任务规则、地图、商人等公共游戏数据。它们不提供某个玩家的实时档案、任务完成进度、仓库、装备或个人藏身处等级；`playerLevels` 和 `skills` 是等级/技能定义，不是玩家当前等级/技能。不能据此替代本插件已有的玩家档案快照查询。

来源：[当前 API 文档](https://tarkov.dev/api/)、[端点目录](https://json.tarkov.dev/endpoints)、[官方 GraphQL 维护声明源码](https://github.com/the-hideout/tarkov-dev/blob/ec44177bd3c21aa2b33938153acd0a96dbd939aa/src/pages/api-graphql/index.jsx)。

## 2. 统一请求约定

| 项目 | 约定 |
|---|---|
| 根地址 | `https://json.tarkov.dev` |
| 方法 | `GET`，不发送请求体 |
| 响应类型 | `application/json` |
| 请求头 | 建议 `Accept: application/json`；请求示例无需登录或 API key |
| 路径 | 使用目录列出的路径；示例不加 `.json` |
| 发现能力 | `GET /endpoints`，读取 `data.endpoints`、`data.gameModes`、`data.languages` |
| 基础响应 | 通常为 `{"data": ..., "translations": [...]}` |
| 翻译响应 | `{"data": {"翻译键": "译文", ...}}`，是字典，不是完整资源 |
| 查询筛选 | JSON 目录未约定 `name`、`ids`、`limit`、`offset` 等服务端筛选参数，应在本地筛选 |
| 单条历史价格 | 使用目录明确提供的 `/{gameMode}/prices/{itemId}` |

模式必须显式转换，不能混用不同服务的命名：

| 用户/本插件模式 | JSON API 路径值 | 已有 `players.tarkov.dev` 快照路径值 |
|---|---|---|
| PVP / `pvp` | `regular` | `profile` |
| PVE / `pve` | `pve` | `pve` |
| 赛季 / `season` | `pvp-season` | `pvp-season` |
| 竞技场 / `arena` | 当前 JSON 目录未列出 | `arena` |

语言码以实时目录为准。本次列出 `cs de en es fr hu id it ja ko pl pt ro ru sk th tr vn zh`；中文使用 `zh`，不要自行改为 `zh-CN`。只有目录标注 `translations: true` 的资源才按本约定请求语言文件。

## 3. 全部 JSON 端点与可查数据

下表中的 `{mode}` 取 `regular`、`pve` 或 `pvp-season`；`season` 只有表中列出的赛季路径。表中结构来自当日实测，字段可能新增，调用方应容忍额外字段。

| 端点 | `data` 结构 | 可查询的数据 | 翻译字典 |
|---|---|---|---|
| `/{mode}/items` | 包含多组数据的对象 | 物品、分类、跳蚤市场规则、护甲材料、等级经验表、武器熟练度、技能、特殊物品、部分全局设置 | 有 |
| `/{mode}/barters` | 易物交易数组 | 商人易物配方、材料、产物、商人等级/任务解锁要求、限购及补货数量 | 无 |
| `/{mode}/crafts` | 制造配方数组 | 制造材料、工具、任务物品、产物、工作站与等级、耗时、任务解锁和版本要求 | 无 |
| `/{mode}/hideout` | 工作站 ID → 工作站对象 | 藏身处设施、各等级升级材料、建造耗时、商人/技能/前置设施要求、加成 | 有 |
| `/{mode}/maps` | 包含多组数据的对象 | 地图规则、出生点、撤离点、转移、钥匙/门锁、危险区、战利品点、开关、固定武器、BTR 停靠点、Boss 与随从、三男团报告 | 有 |
| `/{mode}/prices/{itemId}` | 历史价格点数组 | 单件物品的跳蚤市场历史最低价、统计价格、部分报价数量及时间 | 无 |
| `/status` | 服务器状态对象 | 总体状态、服务组件状态、状态消息 | 无 |
| `/{mode}/tasks` | 包含多组数据的对象 | 任务、任务专用物品、成就定义/整体完成率、prestige 条件及奖励 | 有 |
| `/{mode}/traders` | 商人 ID → 商人对象 | 商人资料、货币、重置时间、等级门槛、保险/维修系数、收购允许/禁止类别和物品 | 有 |
| `/pvp-season/season` | 当前赛季对象 | 赛季标识、名称、开始/结束时间、公共及个人 perk 定义和互斥关系 | 有 |

这不是“每种数据一个 URL”：例如技能与经验表在 `items` 中，成就与 prestige 在 `tasks` 中。不要自行拼接 `/regular/skills`、`/regular/achievements` 或 `/regular/items/{id}` 作为已支持端点。

来源：[实时目录](https://json.tarkov.dev/endpoints)、[目录生成配置](https://github.com/the-hideout/tarkov-data-manager/blob/59bc50d9cbcb1a0abe80e02f7137ce98d8445dbf/src/tarkov-data-manager/data/json_api_endpoints.json)。

### 3.1 物品、价格和公共规则：`/{mode}/items`

| `data` 字段 | 结构/内容 |
|---|---|
| `items` | 物品 ID → 物品对象 |
| `itemCategories` | 物品分类 ID → 分类，包含父子分类关联 |
| `handbookCategories` | 手册分类 ID → 分类 |
| `fleaMarket` | 是否开启、最低玩家等级、手续费系数、是否要求战局发现、信誉等级等 |
| `armorMaterials` | 护甲材料键 → 材料定义、破坏和维修损耗系数 |
| `playerLevels` | 等级数组，包含 `level`、`exp`、等级徽章图片；经验换算需遵循上游定义，本仓库按逐级累加使用 |
| `mastering` | 武器熟练度定义数组，包含武器 ID、二/三级阈值 |
| `skills` | 技能定义数组，包含 ID、名称、标准名称、Wiki 和图片链接 |
| `specialItems` | 特殊物品 ID 数组 |
| `settings` | 本次包含 `scavCooldownSeconds`、`globalMaxTraders`、`gpCoinValue` |

物品对象的重要字段：

- 身份与展示：`id`、`name`、`shortName`、`description`、`normalizedName`、`wikiLink`、`link`、多种尺寸的图片链接。
- 体积和分类：`width`、`height`、`weight`、`stackMaxSize`、`types`、`categories`、`handbookCategories`、`hasGrid`。
- 价格：`basePrice`、`lastLowPrice`、`avg24hPrice`、`low24hPrice`、`high24hPrice`、`changeLast48h`、`changeLast48hPercent`、`lastOfferCount`、`lastScan`、`updated`。基础价、跳蚤市场价与商人价应分别显示；价格不存在或为 0 时，不据此认定物品可免费获得。
- 商人报价：`buyFromTrader` 表示玩家向商人购买；`sellToTrader` 表示玩家卖给商人。报价含商人 ID、`price`、`currency`、`currencyItem`、统一折算的 `priceRUB`；购买报价还可含等级、任务解锁、限购与补货数量。比较不同币种使用 `priceRUB`，展示原币价保留货币。
- 装备关系：`containsItems`、`conflictingItems`、`conflictingSlotIds`、`conflictingCategories` 等；属性和冲突限制需结合对应类型读取。
- `properties` 根据其内部的 `properties.propertiesType` 分型，不能假设所有物品都有相同属性。

| 属性类型举例 | 可用数据举例 |
|---|---|
| 弹药 | 口径、伤害、穿深、护甲伤害、初速、曳光、弹丸数、出血/跳弹/碎弹参数 |
| 武器/预设/配件 | 人机工效、后坐力、射速、射击模式、弹药兼容、插槽兼容、默认弹药及预设、精度 |
| 护甲/头盔/插板 | 等级、耐久、材料、覆盖区域、护甲槽、移动/转向/人机惩罚 |
| 背包/容器/胸挂 | 格子结构、容量、内部容纳限制或槽位 |
| 医疗/手术/止痛/针剂 | 治疗量、使用时间、治疗状态、止血消耗、持续时间、药效 |
| 食物饮料/资源/钥匙 | 能量/水分、资源数量、使用次数等 |
| 手雷/近战/瞄具/耳机/夜视 | 爆炸参数、劈砍/刺击伤害、倍率、声音处理或视觉参数等 |

类型列表与属性字段应从响应发现；本文只列主要用途。制造利润、易物划算程度、按格价值等是基于这些数据计算的结果，不是保证成交的报价。

来源：[物品响应](https://json.tarkov.dev/regular/items)、[物品数据生成与字段映射](https://github.com/the-hideout/tarkov-data-manager/blob/59bc50d9cbcb1a0abe80e02f7137ce98d8445dbf/src/tarkov-data-manager/jobs/update-item-cache.mjs)。

### 3.2 易物、制造与藏身处

- **易物**：`id`、`trader`、`minTraderLevel`、`taskUnlock`、`requiredItems`、`offeredItem`、`buyLimit`、`restockAmount`。`requiredItems[].item` 与 `offeredItem.item` 是物品 ID；材料数量 `count` 可为小数，不应强制转成整数。
- **制造**：`id`、`station`、`level`、`duration`、`requiredItems`、`requiredQuestItems`、`productItem`、可选 `taskUnlock`、`gameEditions`。材料 `attributes.tool: true` 表示工具，利润计算应与消耗材料区分；不要套用旧 GraphQL 的 `rewardItems` 代替 JSON 的 `productItem`。
- **设施**：每个工作站包含 `levels[]`。各级包含 `constructionTime`、`itemRequirements`、`traderRequirements`、`stationLevelRequirements`、`skillRequirements`、`bonuses`、`description` 等。保留材料属性里的 `foundInRaid` 等要求。

`duration`、`constructionTime` 按上游游戏数据的秒值使用；任务延迟字段本身以 `Seconds` 命名。不要与后文 epoch 毫秒时间戳混用。

来源：[易物](https://json.tarkov.dev/regular/barters)、[制造](https://json.tarkov.dev/regular/crafts)、[设施](https://json.tarkov.dev/regular/hideout)、[制造字段生成](https://github.com/the-hideout/tarkov-data-manager/blob/59bc50d9cbcb1a0abe80e02f7137ce98d8445dbf/src/tarkov-data-manager/jobs/update-crafts.mjs)。

### 3.3 地图、Boss 与地图实体

`data` 含 `maps`、`goonReports`、`mobs`、`lootContainers`、`stationaryWeapons`。其中除报告数组外，本次均为 ID/键索引对象。

- `maps`：地图名称、说明、战局时长、人数、玩家等级限制与通行钥匙要求；`spawns`、`extracts`、`transits`、`locks`、`hazards`、`lootContainers`、`lootLoose`、`switches`、`stationaryWeapons`、`btrStops` 提供相关位置或区域数据。
- `bosses`：刷新概率、位置、护卫、支援、时间/触发条件及 `mob` 引用。概率是游戏数据定义，不代表当前这场战局一定刷新。
- `mobs`：Boss/AI 定义、名称、图片、装备、携带物品和身体部位血量。
- `goonReports`：三男团社区报告，本次为空数组；空数组只表示本响应没有报告，不能推断当前实时位置。
- 坐标包含三维位置及部分区域边界；绘图还需要结合地图坐标/方向定义，不能直接把 `x/y/z` 当图片像素。

来源：[地图响应](https://json.tarkov.dev/regular/maps)。

### 3.4 任务、任务物品、成就与 prestige

`data` 含 `tasks`、`questItems`、`achievements` 对象，以及 `prestige` 数组。

- 任务：商人、所属阵营、地图、最低等级、前置任务/商人要求、目标、失败条件、开始/完成/失败奖励、经验、是否可重启、Kappa/Lightkeeper 要求、钥匙、可接取延迟及其他要求。
- 目标：通过 `type` 区分访问、击杀、交付、搜集等；具体可包含数量、候选物品、战局发现、耐久/狗牌等级、目标区域、身体部位或健康状态等约束。不能仅凭目标描述文本还原全部条件。
- 奖励：可能包含物品、商人声望、购买解锁、制造解锁、技能等级、商人解锁、成就、外观等，用引用 ID 连接其他资源。
- `questItems`：任务专用物品的名称、说明、尺寸和图片；普通任务所需物品仍在 `items` 中。
- `achievements`：成就条件、隐藏性、阵营、稀有度、图片、整体完成率及调整后完成率，不是某个玩家的解锁记录。
- `prestige`：等级、条件、奖励、继承设置、图片，不是某个玩家当前 prestige。

来源：[任务响应](https://json.tarkov.dev/regular/tasks)。字段以实际模式响应为准，不从翻译路径中出现的可选名称推断一定存在对应数据。

### 3.5 商人、服务器状态、赛季

- 商人 `levels[]` 含等级、玩家等级/声望/交易额要求、收购倍率、保险与维修系数；`resetTime` 是 ISO 时间字符串。商人的具体现金报价在 `items.*.buyFromTrader/sellToTrader`，易物报价在 `barters`。
- 服务器 `/status` 含 `generalStatus`、`currentStatuses[]`、`messages[]`；结合 `statusCode`、消息和缓存时间展示，不应把旧缓存包装成实时在线检测结果。
- 赛季含 `id`、`name`、`start`、`end`、`perks.common`、`perks.personal`。perk 含名称、描述、点数、互斥 perk ID 和图片。这里的 personal 是可选个人 perk 定义，不是指定玩家已选择的 perk。

来源：[商人](https://json.tarkov.dev/regular/traders)、[状态](https://json.tarkov.dev/status)、[赛季](https://json.tarkov.dev/pvp-season/season)、[赛季生成代码](https://github.com/the-hideout/tarkov-data-manager/blob/59bc50d9cbcb1a0abe80e02f7137ce98d8445dbf/src/tarkov-data-manager/jobs/update-seasonal-data.mjs)。

## 4. 中文及其他语言的规范处理

以物品为例，分别 GET：

```text
https://json.tarkov.dev/regular/items
https://json.tarkov.dev/regular/items_zh
https://json.tarkov.dev/regular/items_en
```

处理顺序与官方客户端一致：

1. 保留基础响应完整结构，读取顶层 `translations` 数组。
2. 使用支持对应语法的 JSONPath 实现，对基础响应匹配每条路径，取得匹配节点、父节点及属性。
3. 节点当前字符串就是翻译键；依次使用中文字典、英文字典、原键回退。按“键是否存在/值是否为 null”判断，不用真值判断吞掉空字符串。
4. 将译文写回匹配节点后，再取 `response.data`。普通 ID 引用不翻译。
5. 对 `translations: false` 的易物/制造资源，需借助物品、商人、设施、任务等资源展示对应名称。

不要把 `_zh` 理解为“服务端已返回完整中文版”。不要遍历全部字符串并无条件替换，也不要把 JSONPath 简化成点号切分：当前路径包含 `[*]`、`.*`、递归 `..` 以及多字段选择。响应可能含没有匹配对象的路径，应容忍零匹配。部分上游路径/字典可能不覆盖所有字段，保留回退值，不承诺全字段都能汉化。

例如 Salewa 物品名称在基础响应里是 `544fb45d4bdc2dee738b4568 Name`；本次 `items_zh.data` 对应值为 `Salewa急救包`。名称索引应基于翻译后的名字及 `normalizedName` 建立。

来源：[官方本地化客户端](https://github.com/the-hideout/tarkov-dev/blob/ec44177bd3c21aa2b33938153acd0a96dbd939aa/src/modules/api-request.mjs)、[中文字典示例](https://json.tarkov.dev/regular/items_zh)。

## 5. 本地查询和跨资源关联

推荐将 `(服务, mode, 资源路径)` 作为缓存身份；翻译缓存另加 `language`。同一个物品/商人 ID 可跨模式关联，但价格、配方、任务和模式规则必须读取对应模式，不能因为 ID 相同而复用 regular 的整份响应。

| 业务查询 | 数据来源与本地操作 |
|---|---|
| 按物品 ID 查询 | `items` 响应的 `data.items[itemId]` |
| 按名字查询 | 本地匹配已翻译的 `name`、`shortName` 和 `normalizedName`，同名时返回候选及 ID |
| 查弹药/护甲/钥匙 | 在 `items` 中按 `types`、分类或 `properties.propertiesType` 筛选 |
| 查最优商人售价 | 比较该物品 `sellToTrader[].priceRUB`，关联 `traders` 响应的 `data[traderId]` 名称 |
| 查购买条件 | 读取 `buyFromTrader` 的商人等级/任务要求；易物另查 `barters` |
| 查某物品的制造/易物 | 在配方数组匹配 `productItem.item` / `offeredItem.item`；材料反查匹配 `requiredItems[].item` |
| 查设施升级成本 | `hideout` 响应的 `data[stationId].levels`，把材料 ID 连接 `items`，前置设施连接 `hideout` |
| 查任务需要/奖励某物品 | 遍历目标及奖励中的物品引用，保留目标条件；任务 ID 关联 `tasks` 响应的 `data.tasks` |
| 查 Boss 出现地图 | 在 `maps` 响应的 `data.maps.*.bosses` 中查对应 `mob`；名称/血量连接 `data.mobs` |
| 查历史趋势 | 请求 `/{mode}/prices/{itemId}`，按时间排序/过滤/降采样 |

JSON 的跨资源字段多是 ID。例如 `barter.trader` 是商人 ID，`craft.station` 是设施 ID；它们不是旧 GraphQL 中已经展开、可直接读取 `.name` 的嵌套对象。删除或缺失的引用应显示 ID/未知并继续返回其余可用数据。

## 6. 历史价格与时间单位

```text
GET https://json.tarkov.dev/regular/prices/544fb45d4bdc2dee738b4568
```

历史点通常含 `timestamp`、`priceMin`、`price`，部分还含 `offerCount`、`offerCountMin`。其中 `priceMin` 是最低价，`price` 来源于平均价统计；旧归档与近期数据粒度不同，数量字段可能缺失，不是逐笔成交记录。

生成代码将归档与近期价格合并，因此目录所说的全部历史是“服务保存并发布的历史”，不是保证所有时期都连续或按固定间隔采样。绘图前按 `timestamp` 排序，按业务需要处理重复点；缺口不补成 0。

| 字段类别 | 格式/单位 |
|---|---|
| 历史价格 `timestamp` | Unix epoch 毫秒 |
| 赛季 `start`、`end` | Unix epoch 毫秒 |
| 物品 `updated`、`lastScan`；商人 `resetTime` | ISO 8601 时间字符串，实测含 `Z`（UTC） |
| 制造/建造时长、明确带 `Seconds` 的字段 | 秒数，是时长，不是 epoch 时间 |

统一解析为带时区时间后再转换显示时区。不要对所有时间字段一律除以 1000。

来源：[历史价格生成](https://github.com/the-hideout/tarkov-data-manager/blob/59bc50d9cbcb1a0abe80e02f7137ce98d8445dbf/src/tarkov-data-manager/jobs/update-historical-prices.mjs)、[归档价格生成](https://github.com/the-hideout/tarkov-data-manager/blob/59bc50d9cbcb1a0abe80e02f7137ce98d8445dbf/src/tarkov-data-manager/jobs/update-archived-prices.mjs)。

## 7. 缓存、失败和客户端约束

官方 JSON 文档说明免费、没有固定速率限制，并称数据缓存到有新数据为止。这不等于网络永不失败，也不是固定刷新周期或实时成交保证。

本次观察到 `ETag`、`Last-Modified` 和 `Cache-Control: max-age=691200`（8 天）；`/status` 携带原 ETag 的条件请求返回 304。上游生成代码会在内容更新后上传并清除对应 CDN 缓存。不要把响应头的 8 天理解成“数据每 8 天更新一次”，也不要将旧 GraphQL 文档的 5 分钟缓存约定直接套到 JSON API。

本项目后续接入建议（不是官方强制值）：

- 资源下载后建立本地索引，不为每条聊天重复下载整份物品/地图。价格敏感数据可从约 5 分钟的条件复查起步，静态信息按更长周期复查，结合实际响应头和维护者需求调整。
- 保存成功数据、获取时间、ETag/Last-Modified；复查优先 `If-None-Match`，304 保留原 body。基础数据及每种语言文件各自保存校验值，不假设它们同时更新。
- HTTP 200 后验证内容类型、JSON 和预期 `data` 结构；304 是成功复用缓存。404 是该资源不可用，不能推广为玩家或整个模式不存在。
- 超时、连接失败、403、429、5xx 都不表示数据不存在。429 尊重有效的 `Retry-After`；其他临时失败采用有限重试与退避。失败时如展示旧缓存，应明确标注旧数据及时间。
- 固定允许的服务主机、模式和资源路径；历史物品 ID 从已发现的物品取值。设置超时、响应大小限制、下载并发上限；不要因错误无限重试或枚举大量物品历史。
- 注意已有 `transport.PROFILE_LIMITS` 的解码上限是 16 MiB，本次物品 JSON 为 17,303,942 字节，已经超过它。若未来复用传输层，需要为公共资源单独设定限额，而不是直接使用档案请求限额。

来源：[官方 JSON 文档源码](https://github.com/the-hideout/tarkov-dev/blob/ec44177bd3c21aa2b33938153acd0a96dbd939aa/src/pages/api-docs/index.jsx)、[上传与 CDN 清理代码](https://github.com/the-hideout/tarkov-data-manager/blob/59bc50d9cbcb1a0abe80e02f7137ce98d8445dbf/src/tarkov-data-manager/modules/data-job.mjs)。

## 8. 可复用请求示例

下载完整资源及中文词典：

```bash
curl --fail --silent --show-error --max-time 60 \
  -H 'Accept: application/json' \
  'https://json.tarkov.dev/regular/items' -o items.json

curl --fail --silent --show-error --max-time 60 \
  -H 'Accept: application/json' \
  'https://json.tarkov.dev/regular/items_zh' -o items_zh.json

curl --fail --silent --show-error --max-time 60 \
  -H 'Accept: application/json' \
  'https://json.tarkov.dev/regular/items_en' -o items_en.json
```

以下 Python 示例只展示单物品常用名称字段的字典查询；完整资源本地化应按第 4 节使用 JSONPath。

```python
import json
from pathlib import Path

def read(filename):
    return json.loads(Path(filename).read_text(encoding="utf-8"))["data"]

data = read("items.json")
zh = read("items_zh.json")
en = read("items_en.json")

def localize(key):
    value = zh.get(key)
    if value is None:
        value = en.get(key)
    return key if value is None else value

item = data["items"]["544fb45d4bdc2dee738b4568"]
print(item["id"], localize(item["name"]), item.get("lastLowPrice"))

# 按已翻译名称或标准名称在本地筛选；返回全部候选，不假设名称唯一。
term = "salewa".casefold()
matches = [
    entry for entry in data["items"].values()
    if any(term in localize(entry.get(field, "")).casefold()
           for field in ("name", "shortName", "normalizedName"))
]
print([entry["id"] for entry in matches])
```

查 PVE 历史价格时只替换模式路径，不能沿用 PVP 价格：

```bash
curl --fail --silent --show-error --max-time 60 \
  -H 'Accept: application/json' \
  'https://json.tarkov.dev/pve/prices/544fb45d4bdc2dee738b4568'
```

## 9. 旧 GraphQL API：兼容参考

JSON 是本次整理的首选入口。下面是基于官方 schema 的兼容说明；本次对 GraphQL 的 GET 探测返回 `{"errors":["GraphQL server unavailable. Try again later."]}`，因此不能声称这些查询当日已成功执行。

- 地址：`https://api.tarkov.dev/graphql`。
- 标准请求：POST，`Content-Type: application/json`，请求体包含 `query`、`variables`，可选 `operationName`。
- GraphQL 的语言/模式是查询参数，例如 `lang: zh`、`gameMode: regular`；不要把 JSON API 的 `_zh` 后缀拼到 GraphQL URL。
- 只请求所需字段。使用变量传 ID/名字，不把用户输入直接拼进查询字符串。HTTP 200 仍需检查 `errors`；可能同时存在部分 `data`。
- schema 提供服务端过滤/分页，但每个根字段的参数不同，不能统一假设所有字段都支持 `gameMode`、`name`、`limit`、`offset`。

| 查询类型 | 未弃用的根字段 |
|---|---|
| 物品及弹药 | `item`、`items`、`ammo` |
| 分类与规则 | `itemCategories`、`handbookCategories`、`fleaMarket`、`armorMaterials` |
| 易物、制造、设施 | `barters`、`crafts`、`hideoutStations` |
| 历史价格 | `historicalItemPrices`、`archivedItemPrices`、`itemPrices` |
| 地图及实体 | `maps`、`bosses`、`goonReports`、`lootContainers`、`stationaryWeapons` |
| 任务和成就 | `task`、`tasks`、`questItems`、`achievements`、`prestige` |
| 商人、等级、技能、状态 | `traders`、`playerLevels`、`skills`、`mastering`、`status` |

`items` 支持 ID、名称、类型、分类等筛选以及 `limit/offset`；`item` 支持 ID 或 `normalizedName`；`task` 使用 ID。避免使用已经弃用的 `itemsByIDs`、`itemsByName`、`itemsByType`、`quests`、`hideoutModules`、`traderResetTimes`。

示例（schema 已核对，非本次成功实测）：

```json
{
  "query": "query ItemLookup($id: ID!, $lang: LanguageCode!, $mode: GameMode!) { item(id: $id, lang: $lang, gameMode: $mode) { id name shortName lastLowPrice avg24hPrice updated } }",
  "variables": {
    "id": "544fb45d4bdc2dee738b4568",
    "lang": "zh",
    "mode": "regular"
  },
  "operationName": "ItemLookup"
}
```

来源：[官方维护声明](https://github.com/the-hideout/tarkov-dev/blob/ec44177bd3c21aa2b33938153acd0a96dbd939aa/src/pages/api-graphql/index.jsx)、[GraphQL schema](https://github.com/the-hideout/tarkov-api/blob/92f40c6ecc32dcfab67cb2ff9a00c864c54b79a3/schema-static.mjs)。JSON 的 `offeredItem`、`productItem`、`buyFromTrader`、`sellToTrader` 与旧 GraphQL 字段不同，迁移时需重新映射。

## 10. 本次验证范围

已读取官方文档对应源码和端点目录，并以只读 GET 核对全部 10 类资源：除全局状态和赛季专属路径外，基础样例使用 regular。额外核对三种模式的物品与 Salewa 历史价格；中文词典覆盖 items、maps、hideout、tasks、traders、season，另取 items 英文词典验证回退；`/status` 的 ETag 条件 GET 返回 304。

本次 regular 快照包含 5,480 个物品、855 条易物、214 条制造、26 个设施、17 张地图、503 个任务、138 个任务物品、128 个成就、6 个 prestige、16 个商人；这些只是核对样本的数量，不是客户端应硬编码的常量。原始 items/maps/tasks 大小约为 17.3/8.9/2.1 MB。

未穷举每个端点的全部模式/语言组合；未验证数据与实际游戏完全一致；没有调用管理端写接口、个人账号接口或绕过访问限制。运行时应继续以实时目录和实际响应为准。

物品查询实现补充（2026-10-24）：普通物品使用 24 位 ID，当前目录另含虚拟狗牌 `customdogtags12345678910`。不能因此拒绝整份目录；该虚拟项不适用仅接受 24 位 ID 的历史价格/固定缩略图路径。界面的共同属性需按三个模式的 ID 与属性类型核对，不能将单模式条目直接称作共同属性。当前实现范围见 [开发进度第 14 节](development-plan.md#14-物品查询阶段进度)。
