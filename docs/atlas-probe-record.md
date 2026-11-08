# Atlas 接口只读实测：请求与返回记录

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