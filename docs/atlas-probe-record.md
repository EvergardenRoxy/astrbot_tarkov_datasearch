# Atlas 接口只读实测：请求与返回记录

> 历史证据，以下响应仅代表 2026-11-08 探测时点，不作为当前服务状态或开发待办；入口见 [开发指南](development.md)。

日期：2026-11-08（UTC）。来源：本开发环境（Claude Code 云端容器）通过代理访问。**全部是 GET 请求，没有写入、没有登录、没有使用任何密钥。**

统一请求头：`User-Agent: astrbot_tarkov_datasearch-planning-probe (+https://github.com/EvergardenRoxy/astrbot_tarkov_datasearch)`，大多数请求带 `Accept-Encoding: gzip`。没有 Cookie，没有其他自定义请求头。

**隐私处理**：昵称和账号 ID 来自 tarkov.dev 公开昵称名单里随机抽取的 3 个真实玩家，这里用 `<昵称N>`、`<账号IDN>` 代替；原始值只在开发环境的临时目录里，不进仓库。

## 0. 抽样方法

从 `https://players.tarkov.dev/pve/index.json`（PVE 昵称名单，138,465 个）里，用固定随机种子 7，在符合 Atlas 昵称规则（`[A-Za-z0-9_.-]{3,15}`）的 138,428 个里抽 3 个，分别记为 1、2、3。之后只用到玩家 1 的账号。

## 请求汇总

| # | 时间(UTC) | 请求 | 结果 |
|---|---|---|---|
| 1 | 06:2x | `GET https://www.eftforge.com/atlas`（放行网络之前） | 代理拒绝建立隧道（CONNECT 403），请求没有到达 Atlas |
| 2 | 06:27:28 | `GET https://eftforge.com/atlas/api/matching?hours=1` | 301，跳转到 `https://www.eftforge.com/…` |
| 3 | 06:28:07 | `GET https://www.eftforge.com/atlas/api/matching?hours=1` | 200 |
| 4 | 06:28:16 | `GET https://players.tarkov.dev/pve/index.json` | 200，1,501,134 字节（gzip） |
| 5 | 06:28 | `GET https://www.eftforge.com/atlas/api/lookup/search?nickname=<昵称1>&mode=pve` | 隧道中途断开（`ws_closed_mid_exchange`），没有收到响应 |
| 6 | 06:28:40 | 同上，玩家 1 | 429 busy |
| 7 | 06:28:45 | `…/lookup/search?nickname=<昵称2>&mode=pve` | 429 busy |
| 8 | 06:28 | `…/lookup/search?nickname=<昵称3>&mode=pve` | 连接被对端重置（`Recv failure: Connection reset by peer`） |
| 9 | 06:28:57 | 同 8（立即重试一次） | 429 busy |
| 10 | 06:29:23 | `…/lookup/search?nickname=<昵称1>&mode=pve`（间隔约 15 秒） | 429 busy |
| 11 | 06:38:47 | `GET https://www.eftforge.com/atlas/api/lookup?accountId=<账号ID1>&mode=pve` | 429 busy |
| 12–16 | 06:39–06:41 | 同 11，按 `Retry-After` 等待后重试（等待 5、10、20、40 秒），共 5 次 | 全部 429 busy，见第 5 节 |

合计发往 `eftforge.com` / `www.eftforge.com` 的 API 请求共 16 次左右（含 301、断连和被重置的）；发往 `players.tarkov.dev` 的 1 次。

## 1. 匹配时间（`eftforge.com` → 301）

请求：`GET https://eftforge.com/atlas/api/matching?hours=1`

返回头（响应体为 178 字节 HTML 的跳转页）：

```
HTTP/2 301
server: nginx/1.18.0 (Ubuntu)
content-type: text/html
content-length: 178
location: https://www.eftforge.com/atlas/api/matching?hours=1
```

## 2. 匹配时间（`www.eftforge.com` → 200）

请求：`GET https://www.eftforge.com/atlas/api/matching?hours=1`

返回头：
```
HTTP/2 200 
server: nginx/1.18.0 (Ubuntu)
content-type: application/json
vary: Accept-Encoding
content-encoding: gzip
cache-control: public, max-age=60
content-length: 1220
accept-ranges: bytes
date: Sun, 08 Nov 2026 06:28:07 GMT
eo-log-uuid: 5594325011438224572
eo-cache-status: MISS
```

返回体（gzip 解压后，完整；这是公开的游戏服务数据，不含玩家信息）：
```json
{
 "server_time": "2026-11-08T06:28:07.739461+00:00",
 "updated": "2026-11-08T06:27:19+00:00",
 "hours": 1,
 "datacenters": [
  {
   "region": "RUS",
   "name": "Russia Central",
   "wait": 95
  },
  {
   "region": "EUR",
   "name": "Europe Central",
   "wait": 93
  },
  {
   "region": "USA",
   "name": "North America Central",
   "wait": 44
  },
  {
   "region": "EUR",
   "name": "Europe North",
   "wait": 73
  },
  {
   "region": "OCE",
   "name": "Australia",
   "wait": 218
  },
  {
   "region": "EUR",
   "name": "Europe West",
   "wait": 89
  },
  {
   "region": "USA",
   "name": "North America South East",
   "wait": 118
  },
  {
   "region": "USA",
   "name": "North America West",
   "wait": 52
  },
  {
   "region": "RUS",
   "name": "Russia East",
   "wait": 75
  },
  {
   "region": "ASI",
   "name": "Asia Central",
   "wait": 343
  },
  {
   "region": "EUR",
   "name": "Europe East",
   "wait": 104
  },
  {
   "region": "USA",
   "name": "North America North East",
   "wait": 57
  },
  {
   "region": "USA",
   "name": "North America South",
   "wait": 69
  },
  {
   "region": "ASI",
   "name": "Japan",
   "wait": 53
  },
  {
   "region": "EUR",
   "name": "Europe South",
   "wait": null
  },
  {
   "region": "SAM",
   "name": "South America",
   "wait": 174
  },
  {
   "region": "SAM",
   "name": "Mexico",
   "wait": 173
  },
  {
   "region": "ASI",
   "name": "Singapore",
   "wait": 52
  },
  {
   "region": "RUS",
   "name": "Russia West",
   "wait": 69
  },
  {
   "region": "ASI",
   "name": "China",
   "wait": 66
  },
  {
   "region": "ME",
   "name": "Middle East",
   "wait": 507
  },
  {
   "region": "AF",
   "name": "Africa",
   "wait": 355
  },
  {
   "region": "ASI",
   "name": "Korea",
   "wait": 114
  },
  {
   "region": "SAM",
   "name": "Sa   Colombia",
   "wait": 220
  },
  {
   "region": "OCE",
   "name": "New Zealand",
   "wait": 493
  },
  {
   "region": "AF",
   "name": "Af Nigeria",
   "wait": 269
  },
  {
   "region": "ASI",
   "name": "Malaysia",
   "wait": 100
  },
  {
   "region": "ME",
   "name": "Me   Dubai",
   "wait": 360
  }
 ],
 "history": {
  "start": 1794115687,
  "step": 300,
  "series": {
   "Russia Central": [
    119,
    115,
    130,
    146,
    147,
    134,
    129,
    127,
    116,
    93,
    91,
    97
   ],
   "Europe Central": [
    100,
    100,
    106,
    104,
    100,
    95,
    113,
    104,
    113,
    106,
    102,
    98
   ],
   "North America Central": [
    46,
    42,
    42,
    39,
    50,
    54,
    45,
    40,
    36,
    33,
    38,
    44
   ],
   "Europe North": [
    270,
    274,
    236,
    187,
    130,
    134,
    135,
    109,
    106,
    99,
    100,
    92
   ],
   "Australia": [
    221,
    153,
    140,
    149,
    185,
    222,
    244,
    249,
    229,
    230,
    191,
    205
   ],
   "Europe West": [
    96,
    108,
    116,
    118,
    110,
    104,
    111,
    112,
    108,
    100,
    101,
    94
   ],
   "North America South East": [
    57,
    49,
    47,
    60,
    66,
    53,
    52,
    61,
    61,
    59,
    61,
    96
   ],
   "North America West": [
    54,
    56,
    56,
    59,
    63,
    58,
    49,
    50,
    50,
    51,
    53,
    58
   ],
   "Russia East": [
    100,
    99,
    98,
    94,
    104,
    95,
    93,
    82,
    65,
    70,
    73,
    76
   ],
   "Asia Central": [
    320,
    312,
    304,
    244,
    215,
    261,
    301,
    334,
    368,
    389,
    398,
    377
   ],
   "Europe East": [
    132,
    176,
    185,
    148,
    146,
    167,
    175,
    100,
    50,
    76,
    77,
    80
   ],
   "North America North East": [
    50,
    53,
    46,
    46,
    50,
    50,
    48,
    47,
    45,
    47,
    44,
    50
   ],
   "North America South": [
    98,
    87,
    90,
    90,
    83,
    74,
    60,
    82,
    90,
    100,
    88,
    80
   ],
   "Japan": [
    59,
    64,
    64,
    64,
    57,
    55,
    54,
    53,
    51,
    51,
    56,
    55
   ],
   "Europe South": [
    721,
    430,
    540,
    277,
    495,
    495,
    495,
    383,
    511,
    766,
    493,
    578
   ],
   "South America": [
    164,
    186,
    202,
    184,
    159,
    171,
    186,
    176,
    153,
    152,
    169,
    172
   ],
   "Mexico": [
    252,
    256,
    246,
    221,
    289,
    319,
    266,
    267,
    289,
    301,
    257,
    213
   ],
   "Singapore": [
    73,
    78,
    88,
    97,
    87,
    78,
    73,
    73,
    72,
    63,
    57,
    54
   ],
   "Russia West": [
    65,
    72,
    76,
    88,
    89,
    73,
    72,
    72,
    74,
    67,
    70,
    68
   ],
   "China": [
    52,
    54,
    56,
    65,
    69,
    65,
    64,
    64,
    62,
    61,
    66,
    64
   ],
   "Middle East": [
    588,
    631,
    608,
    503,
    470,
    451,
    368,
    395,
    408,
    527,
    568,
    496
   ],
   "Africa": [
    345,
    294,
    290,
    340,
    336,
    346,
    364,
    355,
    371,
    322,
    336,
    346
   ],
   "Korea": [
    135,
    124,
    112,
    120,
    124,
    114,
    112,
    118,
    108,
    116,
    119,
    120
   ],
   "Sa   Colombia": [
    309,
    339,
    409,
    450,
    318,
    351,
    402,
    323,
    302,
    283,
    279,
    236
   ],
   "New Zealand": [
    288,
    361,
    341,
    310,
    292,
    347,
    338,
    416,
    435,
    439,
    480,
    508
   ],
   "Af Nigeria": [
    296,
    285,
    294,
    303,
    314,
    282,
    317,
    327,
    340,
    304,
    292,
    288
   ],
   "Malaysia": [
    147,
    129,
    130,
    144,
    133,
    116,
    135,
    119,
    97,
    91,
    109,
    107
   ],
   "Me   Dubai": [
    407,
    432,
    408,
    352,
    350,
    361,
    385,
    406,
    356,
    382,
    364,
    375
   ]
  }
 }
}
```

## 3. tarkov.dev 的 PVE 昵称名单

请求：`GET https://players.tarkov.dev/pve/index.json`

返回头：
```
HTTP/2 200 
date: Sun, 08 Nov 2026 06:28:16 GMT
content-type: application/json
content-length: 1501134
x-amz-id-2: 1PHNrk4uqcoMx440rdtcZ8ZXHWREFz73UtZ7HRd/buUig2ZqqDSYl91o7vSXJ8RjnzYU6JaDq0I=
x-amz-request-id: 6VRPF15CS5WMHRYF
server: cloudflare
content-encoding: gzip
last-modified: Sun, 08 Nov 2026 00:02:15 GMT
etag: "cea443909fb1a9af78e1b9870fd70d10"
age: 22568
accept-ranges: bytes
cache-control: max-age=86400
cf-cache-status: HIT
vary: accept-encoding
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
strict-transport-security: max-age=15552000; includeSubDomains; preload
x-content-type-options: nosniff
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=jVJoalZAoBvwMy2LJtgG7BV0J7a%2FFZs1uEkl0g6IeXtYl%2BzSLjSVWrSgjrf8vQfLr43kPwLD8AAbkyAUr7xoODpzQZ4oF94JhorIBLkfVidz6yTuhC5MhQxRY3i9%2Fzv8064Mcw%3D%3D"}]}
cf-ray: a57284404b529d25-IAD
alt-svc: h3=":443"; ma=86400
```

返回体：1,501,134 字节 gzip，解压后是 `{"<账号ID>":"<昵称>"}`，138,465 项；其中 31 个昵称超过 15 个字符。内容含真实玩家昵称，不在此记录。

## 4. 玩家搜索（`lookup/search`）

### 搜索玩家 1（`mode=pve`，请求 6）

返回头：
```
HTTP/2 429 
server: nginx/1.18.0 (Ubuntu)
content-type: application/json
retry-after: 5
content-length: 27
date: Sun, 08 Nov 2026 06:28:38 GMT
eo-log-uuid: 17097455337070180078
eo-cache-status: MISS
```

返回体：
```json
{
 "detail": {
  "error": "busy"
 }
}
```

### 搜索玩家 2（`mode=pve`，请求 7）

返回头：
```
HTTP/2 429 
server: nginx/1.18.0 (Ubuntu)
content-type: application/json
retry-after: 5
content-length: 27
date: Sun, 08 Nov 2026 06:28:44 GMT
eo-log-uuid: 9406806035767154032
eo-cache-status: MISS
```

返回体：
```json
{
 "detail": {
  "error": "busy"
 }
}
```

### 搜索玩家 3（`mode=pve`，请求 9）

返回头：
```
HTTP/2 429 
server: nginx/1.18.0 (Ubuntu)
content-type: application/json
retry-after: 5
content-length: 27
date: Sun, 08 Nov 2026 06:28:56 GMT
eo-log-uuid: 13448743357326424289
eo-cache-status: MISS
```

返回体：
```json
{
 "detail": {
  "error": "busy"
 }
}
```

请求 5（隧道断开）和请求 8（连接被重置）没有收到任何 HTTP 响应，所以没有返回头和返回体。

请求 10（15 秒后重试玩家 1）的返回与上面一致：429，`retry-after: 5`，`{"detail":{"error":"busy"}}`。

## 5. 玩家查询（`lookup`）

请求：`GET https://www.eftforge.com/atlas/api/lookup?accountId=<账号ID1>&mode=pve`

### 请求 11（06:38:47）

返回头：
```
HTTP/2 429 
server: nginx/1.18.0 (Ubuntu)
content-type: application/json
retry-after: 5
content-length: 27
date: Sun, 08 Nov 2026 06:38:47 GMT
eo-log-uuid: 14010914297196677530
eo-cache-status: MISS
```

返回体：
```json
{
 "detail": {
  "error": "busy"
 }
}
```

### 后续重试（请求 12+）

请求 11 之后，同一个 URL 按 `Retry-After` 等待后再试，每次返回都是 `429`，响应体都是 `{"detail":{"error":"busy"}}`（`retry-after: 5`）。脚本输出如下（第 1 列为第几次，第 3 列为这次请求耗时，秒）：

| 次数 | HTTP 状态 | 耗时（秒） | 响应体 | 下一次前等待（秒） |
|---|---|---|---|---|
| 12 | 429 | 1.68 | `{"detail":{"error":"busy"}}` | 5 |
| 13 | 429 | 6.01 | `{"detail":{"error":"busy"}}` | 10 |
| 14 | 429 | 7.77 | `{"detail":{"error":"busy"}}` | 20 |
| 15 | 429 | 3.12 | `{"detail":{"error":"busy"}}` | 40 |
| 16 | 429 | 2.52 | `{"detail":{"error":"busy"}}` | （停止） |

5 次都没有拿到成功响应，所以**没有任何一份 `lookup` 的成功返回**可供分析。已停止，不再继续请求。


最后一次重试保存下来的返回头：
```
HTTP/2 429 
server: nginx/1.18.0 (Ubuntu)
content-type: application/json
retry-after: 5
content-length: 27
date: Sun, 08 Nov 2026 06:40:35 GMT
eo-log-uuid: 3133122812019639975
eo-cache-status: MISS
```

最后一次重试的返回体：
```json
{
 "detail": {
  "error": "busy"
 }
}
```

## 6. 观察与结论

- 匹配时间接口可直接使用，返回结构与 EFTForge 公开代码一致；个别数据中心名称有多余空格或前缀，个别 `wait` 为 `null`。
- `eftforge.com` 会 301 到 `www.eftforge.com`；插件不跟随跳转，必须直接请求 `www`。
- 玩家搜索和查询在实测的时段里全部（约 10 次）返回 429 `busy`（`Retry-After: 5`）。作者回复：**网站服务器端的 8 个同步查询位置都满了**。`busy` 不是我们超限，也不计入每 IP 每分钟 20 次、每小时 200 次的额度。
- 更新时间字段、刷新的含义、四个模式是否都有效：取决于能否拿到成功的 `lookup` 响应，见第 5 节和规划稿 2.5。
## 7. 第二轮实测：玩家查询成功（2026-11-08 07:56–08:01 UTC）

维护者要求用两个指定账号实测：**玩家 A** 查 PVE；**玩家 B**（参考玩家）查 PVP、赛季和竞技场。两个账号的昵称、账号 ID、战绩、装备、成就、技能等**全部打码**，下文只保留接口结构、字段名和游戏本身的常量（成就总数、版本号等）；原始响应只存在开发环境的临时目录，不进仓库。请求头与第 0 节相同（只读 GET，没有 Cookie、没有密钥）。

### 7.1 请求汇总

| # | 时间(UTC) | 请求 | 结果 |
|---|---|---|---|
| 17 | 07:56:41 | `GET https://players.tarkov.dev/pve/index.json` | 200；玩家 A 在名单里，得到账号 ID |
| 18 | 07:56:41 | `GET /atlas/api/lookup?accountId=<玩家A>&mode=pve` | **200**，3.8 秒，gzip 6.8 KB（解压 23.7 KB），`cached:false` |
| 19 | 08:00 | `GET https://players.tarkov.dev/pvp-season/index.json`、`/arena/index.json` | 200；玩家 B 在两份名单里，账号 ID 相同 |
| 20 | 08:00:09 | `GET /atlas/api/lookup/search?nickname=<玩家B>&mode=season` | 连接被对端重置（`Recv failure: Connection reset by peer`），没有收到响应 |
| 21 | 08:00:16 | `GET /atlas/api/lookup?accountId=<玩家B>&mode=pvp` | **200**，8.9 秒，12.9 KB，`cached:false` |
| 22 | 08:00:29 | `GET /atlas/api/lookup?accountId=<玩家B>&mode=season` | **200**，3.2 秒，11.3 KB，`cached:false` |
| 23 | 08:00:36 | `GET /atlas/api/lookup?accountId=<玩家B>&mode=arena` | **200**，14.6 秒，8.9 KB，`cached:false` |
| 24 | 08:00:54 | 同 23（18 秒后重查） | **200**，2.6 秒，`cached:true`，`looked_up` 与 23 相同 |
| 25 | 08:01:08 | 同 20（约 1 分钟后重试一次） | **200**，2.7 秒，132 字节 |

这一轮发往 Atlas 7 次（6 次成功，1 次断连），**没有遇到 `busy`**。主机都是 `https://www.eftforge.com`；下文的路径省略主机。

### 7.2 返回头（玩家查询和搜索都一样）

```
HTTP/2 200
server: nginx/1.18.0 (Ubuntu)
content-type: application/json
vary: Accept-Encoding
content-encoding: gzip
cache-control: no-store
eo-cache-status: MISS
```

没有 `Retry-After`，没有 Cookie，没有限额剩余次数之类的头。

### 7.3 搜索（`lookup/search`）

```json
{"nickname": "<昵称>",
 "players": [{"accountId": "<账号ID>", "nickname": "<昵称>", "side": "<Bear|Usec>", "level": "<数>"}],
 "cached": false,
 "mode": "season"}
```

- `accountId` 是**字符串**。`players` 里除了账号 ID 和昵称，还有阵营和等级（规划稿 2.2 里只写了前两个）。
- 只搜到一个人时 `players` 只有一项；有多项时的排序方式和大小写规则还没有观察到。

### 7.4 查询（`lookup`）：PVE、PVP、赛季

三个模式的结构相同（下面是骨架，数组只列第一项，`…` 表示还有更多项；`<数>`、`<文本>` 是打码的值）：

```json
{
 "nickname": "<昵称>", "side": "<Bear|Usec>", "accountId": "<账号ID>",
 "experience": "<数>", "level": "<数>", "levelExp": {"current": "<数>", "next": "<数>"},
 "prestigeLevel": "<数>", "memberCategory": "<数>", "selectedMemberCategory": "<数>",
 "league": null, "ending": null, "endingsReached": [], "prestigeGameMode": "pve",
 "slots": [{"slot": "FaceCover", "tpl": "<物品ID>", "en": {"name": "<文本>", "short": "<文本>"},
            "ch": {"name": "<文本>", "short": "<文本>"}, "children": [], "icon": "<图片网址>",
            "image": "<图片网址>", "bg": "<图片网址>", "render": null, "armor_class": null}, "…"],
 "favorites": [],
 "stats": {
  "pmc":  {"time": "<数>", "counters": [[["Sessions", "Pmc"], "<数>"], [["ExitStatus", "Survived", "Pmc"], "<数>"],
                                       [["Kills"], "<数>"], [["Deaths"], "<数>"], [["KilledPmc"], "<数>"], "…"]},
  "scav": {"time": "<数>", "counters": [[["Sessions", "Scav"], "<数>"], "…"]}
 },
 "achievements": {"count": "<数>", "total": 106,
                  "done": [{"id": "<成就ID>", "at": "<秒>", "rarity": "<文本>", "percent": "<数>",
                            "en": {"name": "<文本>", "description": "<文本>"}, "ch": {"name": "<文本>", "description": "<文本>"}}, "…"]},
 "skills": {"common": [{"id": "Endurance", "name": {"en": "Endurance", "ch": "耐力"}, "progress": "<数>", "lastAccess": "<秒>"}, "…"],
            "mastering": [{"id": "<武器>", "progress": "<数>", "kills": "<数>", "templates": ["<物品ID>", "…"],
                           "weapon": {"tpl": "<物品ID>", "icon": "<图片网址>", "en": {"…": "…"}, "ch": {"…": "…"}}}, "…"]},
 "perks": {"common": [], "personal": []},
 "battlePass": {"completed": "<数>", "total": 53},
 "seasonalRewards": {"completed": "<数>", "total": 5},
 "looked_up": "<ISO 8601 时间，UTC>", "cached": false,
 "client_version": "1.3.0.1.48558", "mode": "pve"
}
```

各模式的差别：

| 字段 | PVE | PVP | 赛季 |
|---|---|---|---|
| `prestigeGameMode` | `pve` | `regular` | `regular` |
| `league` | `null` | `null` | `{"rank", "name":{en,ch}, "max", "maxName":{en,ch}}`（联赛段位） |
| `stats.scav.counters` | **空数组** | 有 | 有 |
| `stats.pmc.time` 与 `stats.scav.time` | 相同 | 相同 | 相同，且与 PVP 相同 |
| `slots` | 当前装备 | 当前装备，武器多一个 `build` | 只有刀鞘和安全箱（赛季开始时没带装备） |
| `client_version` | `1.3.0.1.48558` | 同左 | 同左 |

- 计数器是 `[[键…], 值]` 的数组，键名与 tarkov.dev 快照里 `overAllCounters.Items` 的 `Key` 完全一致（`Sessions`、`ExitStatus/Survived|Killed|Runner|Transit|MissingInAction`、`Kills`、`Deaths`、`KilledPmc`、`LongestWinStreak`）；没发生过的项不出现（例如没有 `Runner` 就没有这一项）。
- `time` 是**账号总游戏时长（秒）**，PMC 和 Scav 相同，PVP 与赛季也相同，与 tarkov.dev 快照的 `totalInGameTime` 含义一致，不能当成单个模式的时长。
- PVE 下 `scav.counters` 是空的：这一份样本里没有任何 PVE Scav 计数，不能确定是“没玩过 PVE Scav”还是 PVE 模式本来就不返回 Scav 统计。
- `level` 由 Atlas 直接给出（与按经验表算出的一致），`levelExp` 是本级起点和下一级所需的总经验。
- 技能 `progress` 与 tarkov.dev 一样是“等级 × 100”，满级 5100；技能自带中英文名。
- 成就只给出已完成的（`done`）和总数（`total`），完成时间是 `at`（秒）。

### 7.5 查询（`lookup`）：竞技场

与上面共有的字段：`nickname`、`side`、`experience`、`level`、`levelExp`、`memberCategory`、`selectedMemberCategory`、`slots`、`favorites`、`achievements`（`total` 为 86）、`accountId`、`looked_up`、`client_version`（`0.6.2.0.48598`，竞技场自己的版本）、`cached`、`mode`。`prestigeLevel` 为 `null`。

没有 `league`、`ending`、`stats`、`skills`、`perks`、`battlePass`、`seasonalRewards`；多出：

```json
{
 "registered": "<秒>",
 "arenaStats": {
  "time": "<数>",
  "modes": {
   "UnrankedOverall":     {"GamesCount": "<数>", "Kills": "<数>", "Deaths": "<数>", "BestArp": "<数>", "MaxKillsWithoutDeaths": "<数>",
                           "KillsWithoutDeaths": "<数>", "LongestWinStreak": "<数>", "LoseStreak": "<数>", "LongestLoseStreak": "<数>"},
   "UnrankedTeamFight":   {"GamesCount": "<数>", "ArenaWins": "<数>", "ArenaLoses": "<数>", "Kills": "<数>", "Deaths": "<数>",
                           "Assists": "<数>", "Headshots": "<数>", "MatchMvpCount": "<数>", "RoundMvpCount": "<数>", "BestArp": "<数>", "…": "共约 40 项"},
   "UnrankedCheckPoint":  {"…": "同上"}, "UnrankedLastHero": {"…": "同上"},
   "UnrankedBlastGang":   {"…": "同上"}, "UnrankedShootOutDuo": {"…": "同上"}
  }
 },
 "battlePasses": [{"id": "<通行证ID>", "level": "<数>"}, "…",
                  {"id": "<通行证ID>", "level": "<数>", "max": 100, "color": "#006C87", "title": "Season 3", "current": true}]
}
```

- `arenaStats.modes` 的模式名和计数器名与 tarkov.dev 竞技场快照的 `stat.arenaOverAllCounters` **相同**，只是少了一层 `{"Counters": …}`。这份样本里只有 `Unranked…` 模式，排位模式的键名沿用 tarkov.dev 的写法（`Overall`、`TeamFight` 等），**未在 Atlas 上见到**。
- 计数器名里有游戏原有的拼写错误（`GreanadeKills`、`ActivatedObjectCound` 等），原样保留。

### 7.6 结论（回答规划稿 2.5 的问题）

1. **数据时间**：`looked_up`（ISO 8601，UTC）是 Atlas 向游戏后端取数的时间；`cached` 表示这次是否命中 Atlas 的服务端缓存。没有“档案时间”之类的其他字段。
2. **刷新**：不需要额外参数。`cached:false` 时就是当场从游戏后端取的新数据（耗时 2.6～14.6 秒）；18 秒后重查同一模式返回 `cached:true`、`looked_up` 不变，与公开代码里 60 秒的结果缓存一致。所以“刷新”就是绕过插件本地缓存、再发一次 `lookup`；60 秒内重复刷新只会拿到 Atlas 的缓存。
3. **四个模式都有效**：同一账号 ID 用 `mode` 切换即可，每个模式的 `looked_up` 各自独立；不需要先在 tarkov.dev 搜索过这名玩家。
4. 搜索也返回 `cached`，同样是 `no-store`。
5. 断连（请求 20）没有返回任何状态码，与第 4 节的连接重置一样，按连接失败处理，不能当成“没有这名玩家”。

### 7.7 开发期间的其他请求（如实记录）

| # | 时间(UTC) | 请求 | 结果 |
|---|---|---|---|
| 26–28 | 08:1x | 开发中运行旧的插件测试时，测试替身没有拦住新加的 Atlas 路径，测试里的虚构昵称被真的发到了 `lookup/search` 和 `lookup`（约 3～6 次，确切次数没有记录） | 有一次搜到了同名的真实玩家并返回了档案；结果只出现在测试输出里，没有保存。之后在 `tests/test_plugin.py` 的 `setUp` 加了拦截：测试里任何 Atlas 玩家请求都会直接失败 |
| 29 | 08:19 | 冒烟测试：插件自己的 `AtlasClient` 查玩家 A 的 PVE | **502** `{"detail":{"error":"backend"}}`（Atlas 的上游当时不可用） |
| 30 | 08:20 | 用 curl 重复一次，确认 502 来自 Atlas | 同上，502 `backend`，耗时 3.7 秒 |

- `backend` 错误码是 502、`application/json`，没有 `Retry-After`。插件把它归为“Atlas 暂时无法查询”，回退到 tarkov.dev 快照，不说成“没有这名玩家”。
- 之后停止请求；作图检查改用 7.1 已保存的响应，不再访问 Atlas。
