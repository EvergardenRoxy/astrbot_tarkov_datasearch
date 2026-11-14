[← 返回 README](../README.md)

# 服务器状态与 Boss 刷率

两项独立的功能，读取 [tarkov.dev](https://tarkov.dev) 的公开游戏数据，需要管理员在配置页分别开启。查询结果是图片；数据取不到时回复一行文字。

## 命令

```text
@机器人 tarkov status                    服务器状态（也可写 状态）
@机器人 tarkov boss                      Boss 刷率，使用个人默认模式
@机器人 tarkov boss pve                  只看 PVE（也可写 pvp、赛季、season、PVP S）
@机器人 tarkov boss 默认 pve             设置个人默认模式（也可写 default）
@机器人 tarkov boss 变化                 最近一次刷率变化
@机器人 tarkov boss 页 <编号> <页码>     长表续页，编号写在图下方
```

- QQ 官方机器人单词命令：`tstatus`、`tboss`（后面可以带模式和“变化”，如 `@机器人 tboss pve 变化`）。
- 没有设置个人默认模式时，`boss` 查询管理员配置的所有监控模式，每个模式一页。
- 个人默认模式按用户保存，跨群有效，不需要先绑定游戏账号，也不影响玩家档案的默认模式。

![服务器状态示例](status_preview.jpg)

## Boss 图怎么看

![Boss 刷率示例](boss_preview.jpg)

- 行是 Boss，列是地图，格子里是刷率，左上角写模式。同一格有多个概率时分别显示，不相加。空白表示上游没有列出。
- 100% 的刷率用红色粗体，该 Boss 的名字也标红。
- 固定的行：Tagilla、Reshala、Shturman、Sanitar、Killa、Glukhar、Zryachiy、Kaban、Kollontay、Partisan、邪教徒祭司和三狗。Partisan 没有被上游列出时隐藏该行。
- **活动 Boss**：固定行之外的 Boss 记录显示为“活动 <名字>”，最多 3 行。Raider、黑色军团、游荡者、守军等普通怪物和普通感染者不算活动 Boss。
- 没有包含在表里的：破冰船、原中心区、实验室 Dark、迷宫、中心区教程、码头，以及普通 AI。

![Boss 变化推送示例](boss_changes_preview.jpg)

**变化图**与全表相同，变化的格子标红，图下方用文字列出，例如“三狗 GOONS（海岸线）：60%→80%”；活动 Boss 出现或结束也会列出。

## 变化推送

状态和 Boss 刷率都可以在变化时把图片推送到聊天里，各有独立开关。

- **推送目标**填 AstrBot 会话标识（聊天中发送 `/sid` 查看），最多 50 个。**留空则推送给所有用过本插件命令的群**（只记群，不含私聊），仍受“限定聊天”限制。
- 第一次成功取得数据只记录当前状态，不推送；之后有变化才推送。
- QQ 官方机器人的会话需要另外打开“允许向 QQ 官方机器人推送”。
- 插件只能“提交”消息，不能确认对方收到。不同平台和权限对主动推送的支持不同，开启前请先在测试群里试一次。

## 设置

- **服务器状态**：查询开关、变化推送开关、检查间隔（10～90 分钟）、推送目标、QQ 官方推送开关。
- **Boss 刷率**：功能总开关、变化推送开关、检查间隔（10～90 分钟）、监控模式、推送目标、QQ 官方推送开关。
- **拉取 Boss 头像**：一次性动作，执行后自动关闭。头像从 assets.tarkov.dev 取得并缓存 365 天；平时不会自动下载，没有头像时用占位图。
- **不当作活动 Boss 的怪物**：上游新增了不想显示的普通怪物时，把它的 `normalizedName`（如 `raider`）填进来。
- **公共游戏数据缓存时间**（“通用”组）：状态和 Boss 共用。

## 数据存储（状态与 Boss）

位于 `data/plugin_data/astrbot_tarkov_datasearch/`：

- `public-data/resources.sqlite3`：公共游戏数据缓存，可以随时删除。
- `accounts.sqlite3` 的 `boss_preferences` 表：个人 Boss 默认模式，不保存原始用户 ID。
- `notifications.sqlite3`、`boss-notifications.sqlite3`：开启推送后生成，保存变化基线、待发事件和长表快照（保留 7 天）；删除后重新记录当前状态，不会补发。
- `boss-portraits/`：手动拉取的 Boss 头像缓存，可以随时删除。

示例图里的状态和刷率是 2026-11-04 取得的真实公开数据；变化图的差异是手工构造的。图中的 Boss 头像是 Battlestate Games 的游戏美术，不包含在插件包里，见 [THIRD_PARTY.md](../THIRD_PARTY.md)。
