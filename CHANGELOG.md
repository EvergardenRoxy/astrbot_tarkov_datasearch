[![AI Co-created](https://img.shields.io/badge/AI-Claude%20%2B%20Claude%20Code-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](README.md#ai-制作声明)

> [!IMPORTANT]
> **AI 共创声明：本更新日志所记录的修改与日志本身，由项目维护者与生成式 AI 共同完成。** **Claude**（通过 **Claude Code**）参与 0.1.0 版本的数据源调研、方案制定、编码、测试，以及本日志与交接文档 `handoff.md` 的编写。AI 生成或修改的内容由项目维护者审阅、验证并承担最终维护责任。完整声明见 [README](README.md#ai-制作声明)。

# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## 0.1.0 - 2026-10-13

> 首个版本，通过 **Claude Code** 依据维护者确认的方案实现（开发分支 `claude/amazing-clarke-eu3xue`），尚未在聊天平台实测。数据源调研、设计、假设与验证见 [handoff.md](handoff.md)。

### 新增

- **查询塔科夫玩家档案**：`/tarkov [模式] <昵称或账号ID>`（别名 `/塔科夫`）。模式可选 PVP、PVE 和 PVP 赛季，不写时使用配置的默认模式。
- **显示内容**：
  - 阵营、等级、声望、游戏时长、成就数、最近活跃时间；
  - PMC 与 Scav 的战局、存活、存活率、击杀、死亡、K/D、击杀 PMC、撤离状态和最长连续存活；
  - 精英技能和其余等级最高的技能。
- **数据来源**：tarkov.dev 公开的玩家档案快照（`players.tarkov.dev`）。
  - 按昵称查询使用该模式的每日昵称名单，插件在本地保存一份 SQLite 数据库，按间隔检查更新；名单没变时只做一次条件请求。
  - 按账号 ID 查询直接读取档案。
- **提示**：
  - 档案超过 30 天未更新时提醒数据可能过时；
  - 名单中的昵称与档案不一致时提示可能改过名；
  - 同名（不区分大小写）的玩家有多个时列出账号 ID，请用户改用 ID 查询；
  - 某个 ID 没有档案而这串数字也符合昵称规则时，再按昵称查一次。
- **配置项**：
  - 默认模式；
  - 谁可以使用（默认所有人）、指定用户、限定聊天；
  - 显示的技能数、档案缓存时间（内存，默认 10 分钟）；
  - 昵称名单更新间隔（默认 24 小时）、下载最长等待；
  - 访问 tarkov.dev 时使用的 HTTP/HTTPS 代理。
- **访问边界**：
  - 只访问 `https://players.tarkov.dev`，不跟随跳转，只接受 JSON；
  - 只请求 gzip 压缩，限制下载和解压后的大小；
  - 请求带上插件自己的 User-Agent；
  - 不使用系统代理环境变量。

### 测试

- 单元测试 71 项，GitHub Actions 在 Python 3.12 和 3.13 上运行。

### 未做

- 不使用 tarkov.dev 的实时查询接口（`player.tarkov.dev`）：它需要浏览器完成 Cloudflare 人机验证。
- 不显示装备、武器熟练度、藏身处、封禁状态和账号类型。
- 不提供给聊天模型调用的工具（llm_tool），也不生成图片。
