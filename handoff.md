# Engineering handoff: `astrbot_tarkov_datasearch`

Audience: engineers or LLM agents debugging or reviewing this repository. This is the current technical record: what
exists, why, what evidence the decisions rest on, and where the risk is. It is not user documentation (README /
CHANGELOG), and `checklist.md` is the plain-language Chinese companion. Layout and conventions follow the
maintainer's earlier plugin `astrbot_plugin_spark_analyse`.

This file was consolidated once on 2026-10-24 (one-time authorisation): superseded proposals, per-section mutation
lists and intermediate test counts were removed; the previous long form stays in git history (commit `5630c68` and
earlier). From now on new work is appended as new sections.

## 0. Rules

- **Documentation** (maintainer, 2026-10-24): work is recorded in `handoff.md`. `checklist.md` may be edited and is
  updated with development progress. `README.md` is edited together with the maintainer when they ask for a release.
- **Working rule** (carried over from the spark plugin): before changing behaviour, propose a plan with pros and cons and
  wait for a decision, unless the maintainer has authorised the specific work or review fixes.
- **Delegation**: the maintainer has a coding/light-task subagent (gpt6.1-sol) that is not connected to Claude Code.
  Task text is written out for the maintainer, who reviews it and passes it on.
- **Never put real player data in the tree** (names, account ids, real stats, even derived numbers): fixtures, README
  examples and previews use invented values (`tools/build_preview.py`, `tests/fixtures.py`). History was rewritten once
  for this reason (§10).

## 1. Context

| Item | Value |
|---|---|
| Task | AstrBot plugin to look up Escape from Tarkov player profiles in chat (maintainer request, 2026-10-13); since 2026-10-24 also public game data: server status, Boss spawn rates, items. |
| License / AI statement | GPL-3.0-or-later. README and the top of CHANGELOG carry the same AI statement: participating AI listed by name only (Claude Opus 5.5 via Claude Code; the Boss background by GPT-Image-2.5-sunburst), no per-task description. Per-version AI notes were removed from CHANGELOG (maintainer, 2026-11-08). |
| Release target | 0.5.0 (Atlas matching time, pending PR review; last release 0.4.8 on 2026-11-04; 0.4.7 on 2026-11-04; 0.4.6 on 2026-10-24; 0.4.5 and 0.4.4 before) (`tarkov_core/__init__.py` `VERSION`, `metadata.yaml`, `@register(...)`, CHANGELOG; `test_version_is_consistent`). Release branches `v0.3.0` … `v0.4.4` are stacked one commit each (§10). |
| Work branch | `main` was overwritten with the `1024` history on 2026-10-24 (§16); the item query was archived (§11). |
| Tests | 358 (`python -m unittest discover -s tests`, needs `pip install -r requirements.txt`; local HTTP servers need sockets). CI runs Python 3.12 and 3.13. |
| AstrBot evidence | 4.16.0 wheel read and its registration/wake/filter/parser bodies replayed by `tools/verify_qq_routes.py` (146 cases at 0.4.6; SDK and network boundaries doubled). Plugin patterns copied from the spark plugin, which was live-tested on 4.28.2. The maintainer ran a small closed test of 0.3.0. **No live AstrBot run of the 1024 features; QQ image delivery and proactive push are unverified.** |

## 2. Data sources and evidence

### 2.1 Player snapshots: players.tarkov.dev

tarkov.dev's player page calls `player.tarkov.dev/name/<str>` and `/account/<aid>` with a Cloudflare Turnstile token;
without it the API answers 401. A bot cannot legitimately obtain a token, so the live API is **never used** (§8).
The plugin reads the static snapshots TarkovMonitor uses (`TarkovDev.cs`, revision `3cf361e`; probed 2026-10-13):

| Path | Content |
|---|---|
| `/{mode}/{aid}.json` | One profile, 3-35 KB; unknown id: 404 `text/html`. |
| `/{mode}/index.json` | `{"<aid>": "<nickname>"}`. `profile` (PvP): 2.97 M entries, 30 MB gzip / 67 MB decoded; `pve` 131 k; `pvp-season` 42 k; `arena` 54 k (606 KB gzip). Rebuilt daily ~00:02 UTC. |

- `mode` is `profile` for regular PvP, `pve`, `pvp-season`, `arena`. `/regular/<aid>.json` is 404. The JSON API (§2.4)
  calls PvP `regular`; `modes.py` keeps both names apart.
- **User-Agent matters**: curl's and a Chrome agent got Cloudflare 403; `TarkovMonitor/1.0` and the plugin's honest
  `astrbot_tarkov_datasearch/<ver> (+repo)` got 200. Observed behaviour, not documented anywhere.
- `Accept-Encoding: gzip` gives a strong ETag and `If-None-Match` works (304); without gzip the ETag is weak.
  `cache-control: max-age=86400`, Cloudflare may serve a copy up to a day old and caches 404s for minutes.
- Profile keys: `aid, info{nickname, side, experience, memberCategory, prestigeLevel, …}, skills{Common[], Mastering[]},
  pmcStats/scavStats.eft{totalInGameTime, overAllCounters.Items[{Key[],Value}]}, achievements, updated (epoch ms), …`.
  `bannedUntil` was not present in any profile seen. Play time is **account-wide**, not per mode.
- Arena profile is a different document: `stat.arenaOverAllCounters` = dict of modes → `{"Counters": {name:int}}`,
  no `pmcStats`/skills; `info.battlePassesLevels`, `registrationDate`. Mode keys seen: `Unranked*` (Overall, TeamFight,
  LastHero, CheckPoint, BlastGang, ShootOutDuo) and ranked-looking `Overall`, `TeamFight`, `ShootOutDuo`. A profile's
  `Overall.GamesCount` can differ from the sum of its modes; shown as stored. Same account id and nickname as the other
  modes (100% match over the shared index ids).

### 2.2 Snapshots are not a directory of all players (decisive for the design)

- A snapshot appears or updates only when someone opens/searches the player **on tarkov.dev in that mode**
  (maintainer's own page view and two veteran players' searches each created or rewrote the snapshot within a minute;
  `Last-Modified` equalled `updated`). Play time is irrelevant. A snapshot of a player nobody views stays old; an old
  `updated` means "not played in that mode" **or** "nobody viewed them recently". The maintainer's old PvP snapshot is
  because they do not play PvP, not a retrieval problem.
- Nicknames in the daily indexes are incomplete and sometimes stale (the maintainer's current name was in none of the
  three; the PVE index lacked their id although `pve/<aid>.json` existed). Hence ids and player-page links are the
  reliable input, and names are learned (§3, `accounts.py`).
- Not verified: that opening `tarkov.dev/players/<mode>/<id>` (without searching) rewrites the snapshot; the plugin's
  refresh text names searching first, the page second. The website search page takes only `?gameMode=`; a link cannot
  prefill a name.
- Index data quality: 8,344 lower-cased names with more than one id in PvP (31 empty), 35 with whitespace (stripped),
  two over 64 characters (one 16,000; skipped), 778 names over 15 characters (775 `TarkovCitizen…`, max 21).

### 2.3 Public game data: json.tarkov.dev (status, Boss; the items feed is only documented, item queries are archived)

Full inventory and request conventions: `docs/tarkov-api-reference.md` (sources pinned to tarkov-dev
`ec44177…` and tarkov-data-manager `59bc50d…`). Key points:

- Ten JSON endpoint families (`/status`, per-mode `/regular|pve|pvp-season/{items,maps,tasks,traders,hideout,…}`,
  `/pvp-season/season`, item price history, `_<lang>` dictionaries). Base data holds ids and localization keys;
  dictionaries are separate documents (`translations` JSONPaths). GraphQL is in maintenance mode; not used.
- Observed sizes: regular items ~17.3 MB, maps ~8.9 MB, tasks ~2.1 MB decoded; `Cache-Control: max-age=691200`, ETag,
  Last-Modified, 304 works. The feeds describe shared game data, not any player's progress.
- `docs/tarkovtracker-reference.md` studies TarkovTracker (pinned `d07b3da…`) as an implementation reference only. It
  reads the same player snapshots (no live game-account API), uses `json.tarkov.dev` for game data, has no ready-made
  status/Boss broadcasts, and its `/api/tarkov/*` are internal browser routes (do not scrape them). Possible borrowings
  not implemented: ETag checks for player snapshots, response-id validation, separate updated/fetched/checked times on
  player images, optional per-user rate controls.

## 3. Design (current)

| Piece | Where | Notes |
|---|---|---|
| Modes | `modes.py` | `MODES` (key → path, label, website segment), aliases incl. Chinese, `GAME_MODES = (pvp, pve, season)` (Arena is separate), `parse_player_url()` (tarkov.dev `/players/<mode>/<id>`, `/player/<id>`; full match, no other host), `page_url()`, `search_url()`. |
| Player transport | `transport.py` | One host constant, `allow_redirects=False`, `trust_env=False`, `auto_decompress=False` + own gzip decoder with compressed/decoded limits, JSON content type required, 304/403/404/429 mapped to `NotFound`/`QueryError` Chinese texts, aiohttp errors reduced to the type name (proxy URL may hold credentials). `http_get()` is the shared bounded reader (ETag/Last-Modified, Retry-After). |
| Profile | `profile.py` | `summarize()` never raises on odd field types except a missing `info.nickname`; counters clamped ≥ 0; skills de-duplicated by id (≤ 49); level from the bundled table. Counter lookup by shortest key containing all parts (as tarkov.dev). MIA = `MissingInAction`, falling back to `Left`. |
| Arena | `arena.py`, `arena_image.py` | `summarize_arena()` (12 counters/mode, ≤ 12 modes/section, ranked/unranked split, no level); `RANKS` from BestArp with the maintainer's overlapping table cut at each upper limit; BestArp is the highest ever reached, so the rank shown is the highest reached; > 7900 shown as "竞技场前100名玩家" without ARP. Image = "ARENA MASTERS · 选手名册" ledger with GP-style ARP medallion, W – L bars. |
| Static tables | `data/static.json`, `tools/build_static.py` | 79 level rows and 49 skill names from `json.tarkov.dev` (+ zh); not fetched at run time. Re-run on game updates. |
| Card / outputs | `card.py`, `output.py`, `image.py`, `help_image.py`, `fonts/` | `build_card()` is the single content model; `render_text`, `render_markdown`, `render_image` (JPEG q85, 820 px, seeded grain so output is byte-stable). Image style: dossier paper + dog tag + exit-status bars; footer with data time (GMT+8), plugin name/version/repo and the open-source notice. Exit wording follows the game: 撤离 / 匆匆逃离 / 转移 / KIA / 行动中失踪 (`card.EXITS`). OFL fonts: complete Noto Sans SC downloaded and verified before drawing, bundled Oswald and Share Tech Mono. All renderers share `fonts.py`; no glyph collection, trimmed font or missing-character substitution. |
| Plugin | `main.py` | Commands `tarkov` (alias `塔科夫`) with sub-commands (`tarkov status`, `tarkov boss` …) and the QQ single-word commands (§18). Optional leading `/` accepted. Flow for players: access check → parse `[mode] target` → own binding / link / id / name → profiles → `show()` → `reply()` (output per `output_mode`). One in-flight query per sender (keyed hash). |
| Accounts | `accounts.py` | `bindings(owner, aid, nickname, bound_at)`; `owner` = HMAC-SHA256 with a random 32-byte `binding.key` (mode 0600). `names(key, aid, name, seen_at)` shared by everyone, fed by every fetched profile, exact-case first then most recent, capped at 200,000. |
| Name index (optional) | `names.py` | On by default (`name_index_enabled`, §18). Per mode a SQLite `names-<path>.sqlite3` built from the downloaded index with a bytes regex (not `json.loads`), built into a unique temp file then `os.replace`d; one lock per mode; failed refresh keeps the old db and backs off 10 min; first-download failure also backs off; limits 64 MiB / 192 MiB. PvP: 23-26 s, 97 MB db, ~106 MB RSS. Used only for names, never for ids. |
| Public data cache | `public_api.py`, `resource_cache.py` | Fixed host `json.tarkov.dev`; validated `ResourceKey` (mode translated pvp→regular, season→pvp-season; language; item id). Limits 32 MiB received / 64 MiB decoded. Lazy SQLite `public-data/resources.sqlite3`: body + validators + cache-control + times; LRU 32 entries / 128 MiB payload; TTL `public_data_cache_minutes` (default 5, 0 = always check); conditional 304; stale fallback only when explicitly allowed (never for 404 or config errors); Retry-After suppression; shared in-flight fetches (cancelled readers don't cancel others); ≤ 2 downloads, ≤ 32 pending; malformed 200 cannot overwrite a good row. Not yet used by player snapshots. |
| QQ / routing | `platform_compat.py`, `tools/verify_qq_routes.py` | QQ single-word commands behind `QQOfficialFilter`, gated by `qq_single_word_commands` (§18); `@机器人 /tarkov` is always accepted on QQ official. `command_style` (§4). |
| Polling / notifications | `polling.py`, `notifications.py`, `boss_notifications.py` | One cancellable, non-overlapping task per feature (status, Boss); first check immediate, delay starts after a cycle, no catch-up, failure backoff ≤ max(30 min, interval), no worker when disabled or without targets. Separate lazy SQLite files; baseline + event written atomically; first success is a silent baseline; per-target submission state, ≤ 3 attempts, permission errors pause the target durably (reset: disable+save, enable+save); `send_message()` true = submitted, `None` = terminal unknown; no exactly-once. Retention 7 days / 200 events / 64 MiB. Targets are explicit UMOs (≤ 50), intersected with allowed origins and known proactive capability. |
| Server status | `server_status.py`, `status_image.py` | Normalised feed (component identity by id, then normalised name; compare status codes, add/remove, global message, notice id+content+time; ignore order and display names). Unknown codes are never shown as healthy. Failed requests never mean downtime. Image shows overall/component state, ≤ 3 notice excerpts (400 chars), checked/fetched/version times, stale flags. |
| Boss | `boss_monitor.py`, `boss_table_image.py`, `boss_portraits.py` | Scope: eleven daily bosses + GOONS (`bossKnight`, shown `三狗 GOONS`) by stable mob-id allowlist; no BEAR/USEC/followers. Resident bosses are rows; other listed bosses are event rows unless generic (§14). Excluded maps: Icebreaker, original Ground Zero, Lab Dark, Labyrinth, tutorial, the pier (`terminal`, id `65cc8f81…`, removed 2026-10-24, §13); retained `ground-zero-21` is labelled `中心区`. Eleven columns: 中心区、白天工厂、夜间工厂、海关、森林、海岸线、立交桥、储备站、灯塔、塔科夫街区、实验室. Conditional records are never summed; compare map+mob+rule identity, then probability; probabilities must be finite 0–1 numbers. Dark matrix (Boss rows, map columns, mode top-left, 1640 px); changes red; exact 100% and its Boss name bold red; explicit 0% printed; unlisted cells blank. Query snapshots/continuation `boss 页 <12-hex> <n>` kept ≤ 24 h, 16 groups, 32 MiB. A change of the scope policy (current v7, §20) retires older baselines/events/snapshots on open. Portraits: fixed assets.tarkov.dev paths from the feed's known portrait links, **manual-only** (`boss_portraits_update` one-shot setting), 128 px sanitized thumbnails, 365-day expiry, placeholder when missing; no game art is bundled (THIRD_PARTY). |
| Items | – | **Archived and removed** (§11). Branch `archive/item-query` @ `9f29e6f`; description in `docs/archive/item-query.md`. |
| Settings | `_conf_schema.json` | New data features default **off** (`status_enabled`, `boss_enabled` and their broadcast / QQ-push switches). Poll intervals `status_poll_minutes` and `boss_poll_minutes`: default 30, 10–90 minutes (§18). `test_release` keeps schema defaults equal to code fallbacks. |

Measured: profile fetch 0.2 s; image 0.2–0.3 s, ~300–400 KB; package under 1 MiB (`tools/package_plugin.py`).

## 4. Player query behaviour (stable since 0.3.0–0.4.4)

- **Bind and query**: `tarkov 绑定 <link|id|name>` stores the account (any mode with a profile confirms it);
  `tarkov` alone queries the caller's own binding. A link's mode wins; a different explicit mode is refused.
  Names resolve through the shared record first, then (only if enabled) the downloaded index. A digit string is tried
  as id first; if there is no profile, as a name in the record only (never starts an index download).
- **Multi-mode**: all three modes are fetched together (`asyncio.gather`); Arena only when asked or `arena_by_default`.
  Without a chosen mode the default mode is shown when it has a profile, else the first one that has one, with a note;
  a chosen mode is kept. The reply names the other modes with a profile. **A failed request is never reported as "no
  profile"**: failures are named in the reply, and `bind()` excludes failed modes from the "needs a website search"
  hint (regression test `test_bind_failure_is_not_listed_as_a_missing_profile`).
- **Freshness**: every reply says the data is a snapshot and links the player's page. In-memory summaries (TTL
  `profile_cache_minutes`, ≤ 128; "no profile" ≤ 60 s) carry `fetched`; a reply from a cache ≥ 60 s old says how old and
  that a player-page link skips the cache. Links and binding skip the cache. Equal concurrent requests share one fetch
  (shielded); ≤ 6 profile requests at once; waits ≤ min(60, `download_timeout_seconds`).
- **Names**: similar spelling (`_` ↔ `-`, ≤ 6 separators, ≤ 3 suggestions) is suggested from the record and the on-disk
  index only (no download, no network); nickname input rule `[A-Za-z0-9_-]{3,24}`.
- **Output**: setting `output_mode` image (default) / text / Markdown for profiles, Arena and help; image failure falls
  back to text with a note; unknown value → image. Every help/profile reply also carries its link as plain text.
- **Command style**: `COMMAND_STYLES` in `tarkov_core/__init__.py` (`mention` = `@机器人 tarkov` default, and `slash`) is the only place those strings are written; everything else goes through
  `TarkovPlugin.cmd()`. Matching is unchanged (`tarkov`/`塔科夫`, optional `/`). Tencent allows unverified QQ bots only
  "@bot + command" without a slash.
- **Access**: `access_mode` defaults to `all`; `allowed_origins` / `user_whitelist` accept lists (a string counts as one
  item, other types deny and log, empty items ignored). Query access and broadcast targets are separate things.

## 5. Tests and validation

Suites (`tests/`): `test_profile`, `test_output`, `test_image`, `test_help_image`, `test_arena`, `test_names`,
`test_transport`, `test_accounts`, `test_plugin` (player flow, bind, access, output modes, command style, QQ routes,
proxy/timeouts, concurrency), `test_release` (version, schema = code defaults, declared imports, package contents),
`test_public_api`, `test_resource_cache`, `test_status`, `test_status_plugin`, `test_boss`, `test_boss_plugin`,
`test_boss_portraits`; helpers `fixtures.py` and `astrbot/`
(SDK stubs). Fixtures are invented.

Method: tests first and shown failing on the old code, then the fix, then the full suite. Each fix round was
mutation-checked in a scratch copy (each guard reverted alone, full suite, time-limited); survivors led to new tests
(e.g. unique download names, empty-bar text, the default-mode rule). Typical guarded behaviour: no-redirect,
`trust_env`, both size limits, ETag/304, content type, locks, retry delay, exact-case rule, one-per-sender, cache bound,
counter clamp, bar width clamp, skill de-duplication, keyed owner hash, failed-vs-missing, single flight, semaphore,
link line after images, command-style literals, scope/aliases, 100% styling, migration of old baselines.

Live read-only smokes (real network, SDK stubbed, nothing kept in the tree): player flows in all four modes; public
cache 200 → 304; status (nine components); Boss tables for three modes (sixteen then twelve maps; 29/29/27 selected
records, two exact-100% records per mode). Previews were inspected visually.

## 6. Not verified

- No live AstrBot run of the 1024 features and no chat platform. **QQ image delivery (passive reply), proactive
  group push and platform permission/rejection behaviour need bot-side acceptance.** Proactive support differs between
  AstrBot/adapter versions (newer docs support configured proactive group messages; older Tencent docs and a Context
  docstring say otherwise); do not claim support on the minimum AstrBot 4.16.0 from current-master evidence alone.
- How chat platforms show a ~300–400 KB JPEG (QQ/NapCat, Telegram, Discord); Markdown rendering per platform; Pillow
  versions other than 12.3.0 (needs FreeType variable-font support).
- Windows: `os.replace` of an index db held open by another process; UNC data directory (`as_uri()` gives
  `file://server/…`, SQLite rejects it); Windows paths in general.
- Cloudflare's User-Agent rule is observed, not documented. Whether a page view always refreshes a snapshot is inferred.
- The weekly name-index check usually re-downloads (the index is rebuilt daily); the 304 saving was never observed
  across days.

## 7. Decisions made without the maintainer (revisit)

1. `access_mode` defaults to `all` (the spark plugin defaults to admin-only because it costs model tokens).
2. Times in UTC+8 with a label (images "GMT+8 {time}", text/Markdown "（北京时间）"), not configurable.
3. Stale threshold 30 days (`card.STALE_DAYS`), not configurable.
4. K/D = all kills ÷ deaths; PMC kills and PMC K/D shown separately (tarkov.dev shows raw counters only).
5. "预留默认查询竞技场的设置" was read as the `arena_by_default` switch, not an Arena option for `default_mode`.
6. Arena rank = highest rank whose lower bound BestArp has passed (overlapping table cut at each upper limit).
7. The open-source sentence is spaced like the other chat texts (a space between CJK and Latin).
8. Nickname rule 3–24 characters (longest generated name seen: 21).
9. Proactive broadcast targets are separate
   from access lists.

## 8. Invariants for future changes

- Never call `player.tarkov.dev` or try to obtain or replay Turnstile tokens; do not bypass rate limits.
- Keep `trust_env=False`, `allow_redirects=False`, the fixed hosts (`players.tarkov.dev`, `json.tarkov.dev`, and the
  fixed `assets.tarkov.dev` paths for known Boss portrait links, plus `www.eftforge.com` for the Atlas matching endpoint), and all size limits. Never fetch a
  feed- or user-chosen host. Keep identifying the client honestly in `User-Agent`.
- `summarize()` and the status/Boss normalisers must never raise on odd third-party field types; malformed 200
  responses must never overwrite a last-good cache row, baseline or index.
- Chat messages come from `QueryError` texts written here or exception type names, never raw exception strings.
- Upstream failure is never "game down", "0% spawn" or "item does not exist". Failed requests do not advance baselines.
- Never render through AstrBot's `text_to_image`/`html_render` (they post content to a remote renderer by default); draw
  locally with the complete Chinese font prepared through `FullFont.ensure()` and the bundled Latin fonts.
  Do not collect glyphs or rebuild fonts after changing text. Never subset or modify `ShareTechMono-Regular.ttf`
  (Reserved Font Name 'Share').
- Commands shown to users are built only through `TarkovPlugin.cmd()` from `COMMAND_STYLES`
  (`test_command_style_setting` checks it).
- Every help and profile reply carries its link as text, whatever the output (`test_links_are_always_given_as_text`).
- Boss portraits download only on the explicit one-shot setting; no data feature may start a hidden poller (each
  polling feature has its own 10–90 minute setting).
- Disabled features must not download data, start tasks, or leave old continuation pages reachable.
- When upstream game data changes (levels, skills) re-run `tools/build_static.py` and commit the diff.

## 9. Maintainer requirements and open work

- **Output formats (maintainer correction, 2026-10-24)**: *image is the default output; text and Markdown must stay
  for compatibility with platforms that cannot send images.* Players, Arena and help already follow `output_mode`.
  Status and Boss queries and broadcasts are currently **image-only** (their schema hints say so), and
  `docs/development-plan.md` carries a correction banner for its old "image-only" wording. **Maintainer decision:
  postpone text/Markdown for the new features for now and keep their requirement on record.** When resumed: make
  `output_mode` govern status, Boss tables and error replies (unknown → image); add text
  and Markdown renderers beside each image renderer from the same card/page model (same pagination and `页` tokens;
  Boss changes marked in text); image failure falls back to text with a note; broadcasts follow `output_mode`;
  tests per feature × three outputs; then rewrite the plan text in `docs/development-plan.md` §1/§2/§9. Binding
  confirmations are control replies and stay short text.
- **QQ acceptance and release**: verify passive images, proactive push, then decide whether the minimum AstrBot version
  changes; version bump, README and CHANGELOG at the maintainer's release request.
- **Old history outside `main`/`1024`** (§16): the tag `v0.4.4-release` and GitHub's `refs/pull/*` still hold the two commits that
  carried one reference player's nickname (see §10). Retiring the tag needs a maintainer decision; the pull refs need GitHub
  support.
- Ideas not requested: an `llm_tool` so the chat model can look players up (see the spark plugin's stop-event and
  no-return-value pitfalls), weapon mastering / equipment / achievements by name, historical price charts, barter and
  crafting profit, quiet hours and coalescing for broadcasts, ETag checks for player snapshots, using the persistent
  public cache for player data. (The old plan to migrate player output to image-only is cancelled, see above.)

## 10. History worth keeping

**Releases** (the release branches were each one commit on the previous one; on the remote only `v0.3.0-不支持竞技场-留档` and `v0.4.4` remain as branches, tags `v0.3.0beta`, `v0.4.2-release` … `v0.4.4-release` exist, `v0.4.0`–`v0.4.3` branches are gone): 0.1.0 text output; 0.2.0 image and
Markdown output (maintainer chose the dossier style + dog tag + exit-status bars); `v0.3.0` bind-and-query (tested by the maintainer); `v0.4.0` image footer
+ Arena; `v0.4.1` `_`/`-` name suggestions; `v0.4.2` PMC K/D, source lines, Arena ranks, image wording; `v0.4.3` help as
an image, links always as text, `@机器人 tarkov` wording, logo; `v0.4.4` `command_style` setting. The status/Boss/QQ work was developed on `1024` (published 2026-10-24, normal push) and became `main` in §16.

**Real player data removed (2026-10-14, maintainer: "你怎么在插件说明文档里写了真实用户名")**: README examples, tests,
fixtures and previews had used real nicknames, account ids and numbers copied from the maintainer's PVE profile.
Replaced with invented values; the history was rewritten (`git filter-repo`) and both branches force-pushed; the
final tree was identical, the suite passed at every rewritten commit, and a scan of all blobs and messages found none of
the real data. Clones/forks made earlier and GitHub's cache are outside the repository's control.

**Reference player removed from `1024` history (2026-10-24, maintainer: delete it, only `1024`)**: the work branch
`claude/amazing-clarke-eu3xue` (no longer on the remote) had briefly carried a public player's nickname in two
documentation sentences (commit "Arena picture … proposal B", removed again one commit later). `1024` descends from it
through `main`. The sentences about the reference run and the reference images were deleted from every commit of `1024`
with `git filter-repo --replace-text` (fresh clone); the final tree differs from before only in two checklist
sentences, tests pass at the rewritten tip and at the affected commits, and no blob or message mentions the name. All
`1024` hashes from that point on changed; `1024` was force-pushed (with lease). No account id was ever involved.

**Audits and reviews (all findings fixed except those noted)**:

| Round | Result |
|---|---|
| Security audit of 0.1.0 (maintainer's `security_audit.md`, not in tree; 0 high, 2 medium, 6 low, 10 info) | All applicable items fixed: substring test on string-typed allowlists (now list-typed, fail closed), control/format characters in third-party text (`text.clean`), SQLite URI built from an unescaped path (`#`, `?`, `%`), garbage gzip, out-of-range timestamps, `Infinity` in config, ASCII-only account ids, `yarl` declared, index limits tightened (64/192 MiB), profile JSON parsed off the loop, README states logs hold the command and sender. Not applicable: Python ≤ 3.10 timeout alias (AstrBot needs ≥ 3.12); ETag header injection (aiohttp refuses CR/LF). |
| Independent review 1 (20 findings) | Fixed: first-download failure back-off, overflow/unhashable skill ids, empty proxy fails closed, reload/temp-file collisions (unique temp names, leftovers only after 1 h), damaged names table rebuild, 3–24 name length, account-wide play-time label, interval-aware texts, index 404/timeout texts, candidate-list totals, UNC note. Not changed: tampered same-line text in nicknames (the game restricts the character set). |
| Independent review 2 (13 findings) | Fixed: failed mode reported as "no profile" (now failures and absences are kept apart), stale cache hiding manual refreshes (age note, links skip cache), negative/offsetting counters hanging the bar drawing, repeated skills inflating images, id→name index downloads, mode-specific index wording, bind reminders, displayed id from the request not the document, single-flight + 6-slot cap, keyed hash for bindings (unsalted sha256 of a QQ number was reversible in 0.6 s), "SNAPSHOT" stamp instead of "VERIFIED". |
| Review after takeover (2026-10-24) | Binding hint listed failed modes as missing; fixed in the 1024 work with a regression test. |

**Other facts**: Cloudflare's rule on the default agent; the font-subset size figures once quoted as "characters" were the
byte size of the character list (after the item archive the subset holds the glyphs of the code and skill names only, font 278 KB).

## 11. Item query archived and removed (2026-10-24, maintainer decision)

Reason: item lookup cannot be searched by common nicknames (that would need a large hand-maintained alias table), and the
feature proved inefficient. It is sealed, not deleted from history.

- **Archive**: remote branch `archive/item-query` = `9f29e6f` (the `1024` tip before removal). Tag push failed (network
  disconnect), so no tag. `docs/archive/item-query.md` holds the design/implementation summary and the original
  `development-plan` §5 and §14 verbatim, plus restore instructions.
- **Removed from `1024`**: `tarkov_core/items.py`, `item_catalog.py`, `item_image.py`, `item_icons.py`,
  `data/item-label-chars.txt`; `tests/test_items.py`, `test_item_plugin.py`, `test_item_icons.py` (32 tests); in
  `main.py` the `item`/`物品` commands (plain, QQ slash, namespace), the catalog/icon objects, help lines and the
  permission-denied item branch; the five `item*` settings; `public_api.decode`'s item validator hook (the generic
  `items` resource and `item_id` history key stay in the public client, which is documented in
  `docs/tarkov-api-reference.md`); the help image's item row; the item cases of `tools/verify_qq_routes.py`; the item
  paragraph of `THIRD_PARTY.md`. `short_commands_enabled` now covers four handlers.
- **Fonts**: `tools/build_fonts.py` no longer reads the item corpus; the subset was rebuilt from the same pinned Noto
  source (sha256 `a3041811…`), 833,760 → 278,292 bytes; other fonts unchanged.
- **Behaviour now**: `item …` is no longer a command. Typed after the plugin command it falls into the player query
  path like any other word (a nickname lookup), so no special message is shown.
- **Docs**: `docs/development-plan.md` §5/§14 reduced to pointers, item settings/commands removed; checklist updated.
  README had no item section.

## 12. Boss switches and change summary (2026-10-24, maintainer decisions)

- **Two switches, as decided**: `boss_enabled` is the feature switch ("Boss 刷率功能总开关": off = no `boss` / `boss 变化` /
  continuation queries, no polling, no push, no Boss download); `boss_broadcast_enabled` ("Boss 刷率变化推送") only controls
  the background polling and push and never affects queries. Push still also needs the feature switch, a monitored mode
  and a target (`boss_broadcast_active()`; behaviour unchanged, only the settings text and one test were added).
- **Change summary** (change events only): `boss_table_image.change_lines(rows, map_ids)` builds short statements from the
  `build_table` diff: `Boss（地图）：60%→80%`, `…：新增 100%`, `…：移除（原 100%）`, `地图新增/地图已移除：地图`; at most 20 lines
  then `另有 N 项变化未列出`; filtered to the maps of each page. `BossStore.observe` stores them as `page['change_lines']`;
  `render_boss_page` draws a "本次变化" block under the table. A full-table query has no previous snapshot, so it never
  carries the block; the stored event (and therefore `boss 变化`, which replays the latest event) shows the same image as
  the broadcast. Cells still show only the new value and Boss names are not red (maintainer: intended; Boss names stay
  plain). Font subset rebuilt for the new characters.
- Tests added (`ChangeSummaryTests`, `test_only_change_events_carry_the_summary`, two-switch assertions in the lifecycle
  test); mutation check: dropping the attachment and dropping the changed-row filter both fail a test.

## 13. Boss footer trimmed, pier map removed, Lab event-boss findings (2026-10-24)

- **Footer** (maintainer): the Boss image no longer prints the "Boss 刷率 · 第 n/n 页…" legend, the check/compare/fetch time
  line or the "空白为上游未列出…" line. The data-source line and the plugin/licence line are one line now
  (`数据 json.tarkov.dev · 头像 assets.tarkov.dev · <name> v<ver> · <notice>`). Kept only when they apply: the stale,
  cache and translation warnings and, with more than one page, the snapshot/page/continuation line. Times are therefore
  no longer visible on the image (they remain in the stored card). Tests: `FooterTests`.
- **Pier removed** (maintainer): `terminal` (码头, `65cc8f81a9aac3e77d0cfd3e`) joins the excluded maps; scope policy
  `…-matrix-v5` retires older baselines/events/snapshots on open (no false removal alerts). Eleven columns, 1640 px.
- **Lab event boss, API check (json.tarkov.dev maps, three modes, zh/en dictionaries, 2026-10-24)**: on `the-lab`
  (`5b0fc42d86f7744a585f9105`) the feed lists, besides raiders, one non-daily boss in all of PvP, PvE and season:
  `bossScourgeEventLab` ("Scourge", escort `followerScourgeEventLab` "Scourge 保镖"), `spawnChance` 1, `spawnTime` -1,
  zones `BotZoneArmory`/`BotZoneGreenOffice`, generic `unknown-npc-portrait.webp`. Lab non-event mobs: `PmcBot` (raiders;
  PvP/PvE), `pmcBotBlackDiv` (season raiders), `pmcUSEC`/`pmcBEAR` (PvE). Lab Dark (excluded map) holds `bossWedgeLab`.
  Implemented below.
- **Rule extension check (maintainer: all selected maps; 2-3 event rows; resident whitelist + generic-mob blacklist)** — feasibility
  from the same three-mode feed, every non-daily boss record on the eleven selected maps. Mob `normalizedName` (language
  independent) separates the generic soldiers cleanly: `raider` (`PmcBot`), `black-div` (`pmcBotBlackDiv`,
  `pmcBotBlackDivSeason`), `usec` (`pmcUSEC`), `bear` (`pmcBEAR`), `rogue` (`exUsecFree`, Lighthouse), `af` (`Sentry`,
  Shoreline). After removing those six, the only record left on the selected maps in PvP, PvE and season is
  `bossScourgeEventLab` (Lab). Excluded maps hold more non-daily mobs (Wedge, Black Div., Tagilla's shadow, `vsRF`), which
  never reach the rule. Raider/PMC/USEC/BEAR alone are not enough: Rogue and Sentry would otherwise be listed as events.
  Blacklisting by `normalizedName` is feasible; residual risk is a future generic type that is not yet on the list
  (it would show as an event until added), hence the optional ignore setting (§14).

## 14. Event bosses on all selected maps (2026-10-24, maintainer decisions)

Rule (maintainer: extend beyond the Lab; 2-3 event rows; resident whitelist + generic-mob blacklist): on the eleven selected
maps a listed boss is **resident** if its id is in `DAILY_BOSSES` (fixed rows), an ordinary soldier if the mob's
`normalizedName` is in `boss_monitor.GENERIC_MOBS` (`raider`, `black-div`, `usec`, `bear`, `rogue`, `af`) or in the new
`boss_event_ignore` setting, otherwise an **event boss**. Without a `normalizedName` the id decides (`pmc*`, `exusec*`,
`follower*` are never events). Feasibility check and the data behind the blacklist are in §13.

- `normalize_bosses(data, dictionaries, ignore)` marks rows `event`; `build_table` passes the flag; event rows go through the
  same probability/rule validation as resident ones (a bad record still raises).
- Image: up to `MAX_EVENT_ROWS` = 3 rows labelled `活动 <name>` under the twelve fixed rows (sorted by mob id; the Boss name is
  red when the row has a 100% cell, like resident rows); rows with no content on a page are skipped; the placeholder circle
  uses the name's first character; more than three → footer line `另有 N 个活动 Boss 未显示`. No portrait (generic
  `unknown-npc` art is never downloaded).
- Changes: an event appearing is `活动 <name>（地图）：新增 100%`, ending is `…：移除（原 100%）`, shown in the change summary and
  pushed like any other change.
- Scope policy `daily-goons-events-selected-maps-matrix-v6`; `BossStore.scope` = policy + `:` + the sorted ignore list, so
  adding a mob to the ignore list (or the upgrade itself) retires old baselines and the next observation is a silent
  baseline. The normalised-state cache key includes the ignore list.
- New setting `boss_event_ignore` (list of `normalizedName`, lower-cased, ≤ 64 characters, default empty).
- Residual risk: a new ordinary soldier type that is not on the blacklist shows as an event until added to the list.
- Tests added (`EventBossTests`, scope-retirement store test, ignore-setting plugin test, footer/summary tests); mutation
  check: dropping `af`, raising the row cap, ignoring the scope and ignoring the ignore list each fail tests. Fonts rebuilt.

## 15. Boss background image (2026-10-24, maintainer-supplied)

- Two generated candidates were supplied; the darker one is bundled as `tarkov_core/assets/boss-background.jpg` (1254×1254,
  JPEG q80, 207 KB; mean luminance ≈ 35/255, centre very even, no bright spots). The lighter one (bullet holes, rivets,
  peaks up to 214/255) was not used: it competes with the table text. Package 636 KB (limit 1 MiB).
- `boss_table_image.backdrop(width, height)` scales it to cover the canvas, centred (`ImageOps.fit`); `background_source()`
  loads it once (`lru_cache`) and returns `None` when unreadable, in which case the table is drawn on plain black as before.
- The table body, header and label column are translucent dark panels over it (`BODY_ALPHA` 205, `LABEL_ALPHA` 220), plus a
  dark band under the grid (`FOOTER_ALPHA` 120) so the change summary and footer stay legible. The texture is faint inside the
  table and visible in the outer margin, header strip and footer area. Output stays byte-stable.
- Tests added (`BackgroundTests`: size and readability, texture visible and stable, fallback to black, panel darkness);
  mutation check: ignoring the file and making the panels see-through both fail.
- **Provenance**: generator GPT-Image-2.5-sunburst (maintainer's statement, recorded in `THIRD_PARTY.md`). Maintainer's statement: the provider's
  terms do not restrict use of generated content in open-source software, and images carry a hidden traceable watermark.
  Recorded in `THIRD_PARTY.md` as the maintainer's statement (terms not reproduced); the bundled JPEG is a re-encode of the
  original WebP, so the watermark may not survive. Closed 2026-10-24.

- **Label alignment** (maintainer): the GOONS row/label is now `三狗 GOONS` (no parentheses), matching the event rows' `活动 <name>` form;
  change-summary lines read `三狗 GOONS（海岸线）：60%→80%`.

## 16. `main` overwritten with `1024` (2026-10-24, maintainer decision)

PR #6 (`1024` → `main`) could not be merged: `1024`'s history had been rewritten (§10) and shares only an early ancestor with
`main`, so nine files conflicted. The maintainer chose to **overwrite `main` with `1024`** ("强制合并").

- Done with a lease-protected force push (`main` was `966d0fc…` and was expected to be exactly that). `main` now equals the 0.4.5
  release commit of `1024`; its history no longer contains the reference player's nickname.
- Consequences: every commit hash on `main` changed; PRs #1–#5's merge commits are gone from `main`; clones/forks made earlier
  keep the old history and must be reset (`git fetch && git reset --hard origin/main`); branch `v0.4.4`, branch
  `v0.3.0-不支持竞技场-留档` and the tags still point at old commits (the tag `v0.4.4-release` carries the nickname; branches do not).
- The old `main` was `966d0fcef8f5be479e7dbf1974c01009eb9b838f`; no backup branch was pushed (it would keep the nickname).
- PR #6 closes as merged once `main` contains its head.

## 17. README split, rename and live-data previews (2026-10-24, after release)

Maintainer decisions (asked first): previews use live data with portraits **and are committed**; display name becomes
"塔科夫数据查询" (README title and `metadata.yaml` `display_name`/`desc` only; plugin id, repo name and in-image texts stay);
details move to `docs/player-query.md` and `docs/status-and-boss.md`; README keeps the overview, getting started, reply modes
(player example images only), general settings and links; deliver by pushing `1024` and `main` and rebuilding the 0.4.5 zip.

- `tools/build_public_preview.py` (new, needs network): fetches live `status` and PVE `maps` (+ zh/en), downloads the twelve
  portraits through `BossPortraits.refresh` into a temp dir (the same path as the manual update), renders
  `docs/status_preview.jpg`, `boss_preview.jpg`, `boss_changes_preview.jpg`. The changes picture compares live data with a
  hand-made previous table (one rate different, one record missing, one ended); the docs say so. `tools/build_preview.py`
  now only builds the player pictures (invented data).
- **Licence note**: the Boss pictures in `docs/` contain Battlestate artwork. This reverses the earlier "no game art in the
  repository" convention for documentation images only: `THIRD_PARTY.md` has a new paragraph (not GPL, illustration only, not
  packaged because `docs/` is not in the zip, no separate reuse). The plugin package and runtime behaviour are unchanged:
  portraits are still never bundled and never downloaded without the manual setting. Take the pictures down if Battlestate or
  tarkov.dev objects.
- Live observation worth knowing: at generation time the real status feed reported several components as unstable; real
  PVE rates for the 11 maps were shown as fetched (75 % for several single-record resident bosses, 100 % for Glukhar and
  Zryachiy on Lighthouse, Scourge 100 % on Labs).

## 18. Settings regrouped, QQ single-word commands, push targets default to used groups (2026-10-24, maintainer decisions)

Asked first; decisions: single-word commands = the full set including bind/help; empty target list = every session that used
the plugin; short commands removed outright; nested (AstrBot `object`) settings groups; name index on by default; poll
interval default 30, range 10–90 minutes.

- **Why single words**: the QQ official bot console rejects a command containing a space ("指令中不要包含空格"), so
  `/tarkov boss` cannot be listed in its command menu. `main.py` registers `tpvp`, `tpve`, `tseason`, `tarena`, `tbind`,
  `tunbind`, `thelp`, `tstatus`, `tboss` (each also as `/word`) behind `QQOfficialFilter`; `qq_word()` rebuilds the spaced
  command (`tpve Player One` → `pve Player One`) and calls `dispatch()`, the one place doing the access check, session
  recording and `handle()` (shared with `tarkov_command`). Setting `qq_single_word_commands` (default true) gates them; the help
  text and image list them when on. Replay (`tools/verify_qq_routes.py`, AstrBot 4.16.0): 146 cases, including the negative
  ones for the removed `/status` and `/boss`.
- **Removed**: the `status`/`boss`/`状态`/`刷率` short commands and their QQ slash twins, `short_commands_enabled`,
  `qq_official_compat_enabled` (`@机器人 /tarkov` is always accepted on QQ official) and `platform_compat.set_short_status_routes`;
  command style `mention_slash` (an unknown value falls back to `mention`).
- **Settings page**: `_conf_schema.json` is four `object` groups (`general`, `player`, `status`, `boss`), every item with a short
  description (≤ 30 chars) and hint (≤ 170). `tarkov_core/settings.py` `Settings` wraps the config so the code still reads flat
  keys (`GROUPS`/`GROUP_OF`; a flat key is used when the group lacks it, which keeps the tests and a flat config working;
  writes go where the value lives; `save_config` passes through). AstrBot's integrity check deletes keys that are not in the
  schema, so **settings saved by the old flat schema are lost** and reset to the defaults (accepted by the maintainer; no
  migration is possible). `test_release` flattens the schema for the default/fallback check and requires every key in exactly
  one group.
- **Push targets**: when `status_notify_targets` / `boss_notify_targets` is empty, targets are the remembered group sessions
  (`accounts.sqlite3` table `sessions(umo, seen_at)`, newest first, ≤ 200 stored, ≤ 50 used). `dispatch()` records a session
  (conversation id only, groups only, never private chats or users, only after the access check passed, rewritten at most hourly);
  the first new session starts the status and Boss pollers when they were idle for lack of targets. The usual target checks
  still apply to remembered sessions (allowed origins, proactive capability, the QQ push switches). A listed target replaces the
  remembered ones.
- **Intervals**: `status_poll_minutes` and `boss_poll_minutes` default 30, slider 10–90 (step 1). `PollingTask`'s failure
  back-off cap is now `max(1800, interval)` seconds, because the old fixed 1800 s cap would have cut a 90-minute interval short.
- **Name index** (`name_index_enabled`) defaults to **true** in the schema and in `index_enabled()`: the first name lookup that
  misses the recorded names in a mode downloads that mode's index (PvP ~30 MB, ~25 s, ~100 MB on disk). The test helper switches it
  off unless a test passes fake names.
- Mutation checks: back-off cap, groups-only recording, recording at all, the empty-list fallback each fail a test.
  Docs updated (README general settings, both detail documents, CHANGELOG 0.4.5, checklist). Installs of the previously delivered 0.4.5 zip lose their saved settings.

## 19. Release 0.4.6, first built as `0.4.5hotfix` (2026-10-24, maintainer: "v0.4.5hotfix 推送到main 交付发行版")

- The §18 changes were delivered as a hotfix build named `0.4.5hotfix` and **released as 0.4.6** (CHANGELOG `## 0.4.6`; the
  changelog keeps `## 0.4.5` as it was delivered first). The `0.4.5hotfix` spelling did not match AstrBot's semver-like
  `VersionComparator`, which is why the release carries a comparable version number.
- `main` fast-forwarded to the release commit (no force). The example pictures were rebuilt for that release (player pictures
  from invented data; status and Boss pictures from live data with portraits, as in §17).

## 20. Boss Halloween robustness, mode queries and personal defaults — v0.4.7 (2026-11-04)

Maintainer-authorised scope: backend Boss handling, then only explicit-mode queries and personal default binding;
no Boss/map search or cross-mode comparison commands. Implemented with Claude Opus 5.5 (via Claude Code). Delivery is a review branch
`fix/boss-modes-v0.4.7` and PR targeting `main`; the maintainer will merge it. No deployment, tag or GitHub release.

- **Observed feed**: public `regular/maps`, `pve/maps`, `pvp-season/maps` were read on 2026-11-04 KST. PvP and season
  rates matched in the selected scope; PvE differed in 13 Boss/map cells. Partisan had no current map records in all
  three feeds; absence must not imply it cannot spawn in game. No validation was relaxed and no rates were invented.
- **Halloween** (`boss_monitor.py`): ordinary `normalizedName=infected` mobs and the four stable ordinary IDs
  `infectedAssault`, `infectedCivil`, `infectedPmc`, `infectedLaborant` no longer consume event slots. The ID fallback
  also covers missing normalized names; it is not an infected-prefix blacklist. `infectedScreamer` remains special.
  Per the maintainer's explicit choice, `infectedTagilla` maps to resident `bossTagilla` for table display. The original
  name and synthetic `sourceMob` rule field preserve the variant's source and prevent diff matching against ordinary
  Tagilla. Separate conditional rates are retained, never added together. The event ignore list still applies to the
  source variant. `BOSS_SCOPE` v7 silently retires older baselines, events and frozen tables through the existing store.
- **Partisan** (`boss_table_image.py`): omit its fixed row when no current Partisan rates exist anywhere in the result;
  removed-only historical records cannot keep it visible. Explicit zero counts as present. Reappearing feed records
  restore the row automatically. Historical removal records remain available to the existing change summary.
- **Font**: added 感染者 to the font builder's upstream-event translation corpus and rebuilt the bundled Noto Sans SC subset and charset
  from the upstream Google Fonts source via fontTools (same subset options as `tools/build_fonts.py`). This fixes 染
  being replaced by `?`; rendering/layout code otherwise stays intact except the authorised Partisan visibility rule.
- **Mode queries** (`main.py::boss_query`): existing `tarkov boss <mode>` and QQ `tboss <mode>` continue to work.
  Boss-only parsing additionally accepts case-insensitive `PVP S` and `pvps`; existing `pvp`, `pve`, `regular`,
  `season`, `pvp-season`, `赛季` remain. No standalone `/boss` route was reintroduced. Explicit mode queries read exactly
  that feed, independent of monitored modes, with existing per-mode caching, error pages and frozen pagination.
- **Default binding**: `tarkov boss 默认 <mode>` / `default <mode>` (also through `tboss`) persists to the new SQLite
  `accounts.sqlite3/boss_preferences(owner PRIMARY KEY, mode)` table. The owner is the existing keyed hash of platform
  instance ID + sender ID (`binding_key`), shared across chats in one instance; no raw IDs are stored. This additive
  schema needs no reset of existing player bindings. Valid modes are pvp/pve/season, not arena. The command passes the
  existing permission, feature-switch and active-query checks, performs no public-data fetch, and confirms in text.
  Explicit mode > personal Boss preference > existing configured multi-mode fallback. This also applies to `变化`.
  An explicit query never rebinds the preference. Restart preserves it; player bind/unbind and admin monitor settings
  do not affect it. No clear-default command or other new command family was added.
- **Documentation/release**: README, command help, status/Boss guide, this handoff and checklist updated. Runtime VERSION,
  AstrBot registration, metadata, README/footer assertions and changelog agree on 0.4.7. Historical version records are
  preserved. All six bundled example images rebuilt with v0.4.7 footers (player/arena/help use invented fixture data;
  Boss/status use current public game data). Packaging follows metadata and remains an ignored local ZIP.
- **Validation**: the suite passed with the workspace Python 3.12 environment; this includes feed routing, aliases, SQLite
  restart persistence, cross-user/platform isolation, cross-chat reuse, explicit override, permissions/disabled/invalid
  binding, QQ dispatch, default-mode change lookup, Halloween family separation and Partisan missing/zero/restoration.
  The release test checks version consistency and ZIP contents/size. `git diff --check` passes. CI retains Python 3.12
  and 3.13 coverage; Python 3.13 and live AstrBot/QQ delivery have not been run locally for this change.

### Review follow-up: event label font corpus (2026-11-04)

The maintainer requested removing 感染者 from the label comment in `boss_monitor.py`, since it belongs to an event.
Moved its font-subset input to `tools/build_fonts.py::characters`; the resulting requested character set is unchanged,
so the bundled font does not need another rebuild. This is a corpus/comment correction only; mob classification and
event visibility remain as implemented above. Existing image/glyph and event tests verify coverage and behaviour.


## Async test execution environment (2026-11-04)

The managed sandbox can reject asyncio's local socketpair wakeup writes with
`PermissionError: [Errno 1] Operation not permitted`. This also stalls a minimal
`asyncio.to_thread(lambda: 42)` program during completion or executor shutdown.
Run the tests with local socket access; do not change production poller logic to
work around this executor restriction. With network permission enabled, the full
suite finished in about 10 seconds.
CI now prints individual test names and limits the test step to five minutes,
so future hangs identify the active test and terminate rather than waiting indefinitely.


## Query stability and performance follow-up — v0.4.8 (2026-11-04)

- Nickname parsing validates the entire object grammar, string encoding and EOF
  before publishing the staged SQLite database. Malformed documents leave the old
  database intact. Refresh now maps the downloaded file read-only and decodes one
  key/value pair at a time instead of retaining a complete Python dictionary.
- Existing nickname indexes remain available during one shared background refresh
  per mode. First-time readers share a shielded download. Publication and queries
  share a lock; download and staging run outside it. Closing cancels downloads and
  waits for worker completion before deleting temporary files or closing HTTP.
- Profile requests retain deduplication, with at most 64 pending keys and a
  30-second semaphore wait. Queue timeout does not release an unacquired slot.
- The plugin owns a lazy HTTP pool for profiles, nickname downloads and public
  JSON. Per-request timeouts, explicit proxies, disabled redirects, size bounds
  and disabled ambient proxy settings remain enforced; cookies are disabled.
- Public-cache snapshots retain their validated document and return deep copies
  to readers. Plugin downloads delegate schema validation to the cache; standalone
  public API fetches still validate by default. Boss translation copies are deferred
  until the normalized state actually changes.

Validation: 326 tests passed in 9.175 seconds with local socket permission enabled.
Regression coverage includes malformed index preservation, queue overflow/timeout,
shared-request cancellation, HTTP connection reuse and close, isolated JSON reader
mutations, stale reads during refresh, and cleanup while a builder is running.
A synthetic 100,000-entry parser measurement with tracemalloc observed 9.5 KiB
Python allocation peak for mapped iteration versus 19.6 MiB for read_bytes plus
json.loads. This excludes mapped resident pages, SQLite native allocations and
other process memory; it is not a production RSS measurement.


## Atlas matching backend planning (2026-11-08)

The maintainer requested a development plan for matching-time backend support and one global cooldown shared only by Atlas matching and future Atlas player HTTP requests. See `docs/atlas-matching-backend-plan.md`; linked from the overall Atlas plan. This is documentation only: no runtime changes, commands, or player integration. Maintainer correction: all users/chats share one matching result, with an independent 15-minute cooldown/cache window and fallback to successful cached results no older than 60 minutes. During cooldown, return cached data normally; report failure only when no usable result can be obtained. This independent matching window must not block player requests for 15 minutes. Remaining proposals (not implemented): 5-second shared request-start interval, one in-flight Atlas HTTP request, fixed 24-hour matching history, 15-minute backoff after failed matching attempts, and shared Retry-After pause. Cache hits and joined requests consume no admission. Existing tarkov.dev/status/Boss requests are outside this gate. Matching backend work does not depend on successful player-response probes. Per-user quotas and global player minute/hour budgets remain future prerequisites for player integration.


## Atlas matching backend implementation (2026-11-08)

Authorised by the maintainer after the plan: implement backend only. No version bump, README/CHANGELOG release edits,
chat commands, rendering, Atlas player integration, or player-source changes.

- `self.atlas` owns one `AtlasCooldown` and borrows the plugin's existing HTTP pool. Fixed matching endpoint,
  `hours=24`, no redirects/cookies/environment proxies, explicit proxy validation, 30-second timeout, 2/8 MiB
  compressed/decoded body limits and 64 KiB error-body limit. Errors expose controlled messages, never raw bodies.
- `self.matching.get()` returns frozen result/datacenter models. `atlas.matching_enabled` defaults false;
  `atlas.atlas_query_cooldown_seconds` defaults 5, valid integers 1–300. Booleans/nonfinite/out-of-range settings
  fall back to 5 with a controlled warning. Startup does not connect to Atlas.
- One matching cache and task for all callers. Fixed 900-second cache window; demand-driven refresh, 900-second
  failure backoff, fallback only to successful results at most 3600 seconds old. Monotonic cache age is independent
  of the retry deadline and wall-clock metadata. Cache hits never extend age or consume global admission.
- The shared Atlas gate permits one in-flight HTTP request with a minimum start interval. It applies Retry-After
  from 429 and explicitly delayed 5xx globally, does not truncate long pauses or auto-retry. Future Atlas player
  HTTP methods must use this same gate; their minute/hour budgets remain unimplemented prerequisites. Other
  hosts and existing functions never enter this gate. Matching's 15-minute window does not block player requests.
- Admission refusal sends no HTTP and does not start a fresh matching failure backoff. Cached fallback is labelled
  structurally (`from_cache`, `stale`, `error`) with original `updated` and `fetched_at`. Configuration errors,
  disabled features, programming errors and non-transient HTTP failures are not hidden by a stale result.
- Identical calls share shielded tasks; caller cancellation cannot cancel peers. Termination cancels and awaits
  matching/Atlas work before releasing the shared pool. No background matching tasks, durable cache or cooldown.

Validation: 23 new tests; all 349 tests pass. Local HTTP-server tests cover redirects, gzip, malformed/oversized
bodies, cookies/ambient proxy exclusion, shared pauses, cache/admission interaction and close. Clock-driven tests
cover 900/3600-second boundaries, failure backoff, 20 concurrent callers, cancellation and malformed data.
Font rebuild produced no tracked changes. One live read-only matching request succeeded: 24 hours, 28 datacenters,
288 history points; a second service call read the cache. The development smoke test explicitly selected the managed
proxy; production still uses only plugin settings. No real player data or samples stored, no live chat-platform test.

Maintainer follow-up: non-transient Atlas HTTP errors (403, 404, ...) get an independent 2-minute window
(`HTTP_ERROR_SECONDS`). Callers receive the same error without contacting Atlas and are never served the stale
result; the window neither shortens nor extends the 15-minute cache/failure windows. 351 tests pass.


## Atlas matching command and picture — v0.5.0 (2026-11-08)

Maintainer decisions in this round: image only (text/Markdown for matching will not be built; their maintenance is
being dropped plugin-wide later, profile text/Markdown untouched for now); commands `tarkov 匹配` / `matching`, QQ word
`tmatch`; matching on by default; layout from the maintainer's reference picture; player queries via Atlas deferred.

- Routing: `MATCHING_WORDS` in `handle()`; `TarkovPlugin.matching_query()` checks access, `matching_enabled()` (schema
  default true, code fallback true), no arguments, one active query per owner; then `self.matching.get()` and
  `render_matching_image` in a thread under `matching_image_slots` (2). Reply: `[Image, Plain('数据来源：Atlas <url>')]`.
  `QueryError` → `匹配时间查询失败：<message>`; other exceptions → fixed text, logged by type only; render failure → text.
- `tarkov_core/matching_image.py`: dark 1600 px board. Header: title, `updated` (UTC+8), stale warning when
  `result.stale`; logo `assets/atlas-logo.png` (maintainer-supplied, Atlas developer's requirement, THIRD_PARTY.md) with
  `数据来源：Atlas https://eftforge.com/atlas` under it. Left: per-region fastest datacenter (`REGIONS` Chinese labels,
  regions with no data last). Right: `RANKED`=8 shortest current waits excluding `FIXED` and `None`; then `FIXED`
  rows China→香港, Japan→日本, Korea→韩国 (Chinese only; missing → "Atlas 暂未提供"). Columns: region code, name,
  current, 24 h sparkline (per-row scale, minimum span 60 s, gaps break the line), min/avg/max of the history.
  `display_name()` drops a two-letter prefix that repeats the region (Sa/Me/Af; maintainer confirmed). Wait tones:
  ≤60 s, ≤180 s, longer. Footer: dash note, plugin name/version/repo, and
  `特别致谢：Atlas（EFTForge 项目的一部分，作者 Morph1ne / SouthHorizons76）` instead of `NOTICE` (maintainer).
- Text uses its own `font()` with `ImageFont.Layout.BASIC`: with RAQM, Pillow rounded glyph positions unevenly at
  15–19 px (visible gap in "h ttps"). Other pictures keep `image.font()`.
- Help text/card/image, QQ word list and `_conf_schema.json` hints mention the command. Fonts rebuilt (fonttools 4.68;
  the TTF bytes differ from the previous build even for an unchanged character list).
- Previews: `tools/build_matching_preview.py <saved.json>` renders `docs/matching_preview.jpg` (public game-service
  data, no player data). Maintainer: iterate on the one saved response (2026-11-08 07:29:55 UTC sample) instead of
  new requests. Older previews were not re-rendered and still show v0.4.8.
- Tests: `test_matching_image.py` (sections, ordering, exclusions, missing fixed server, wait text, names, JPEG with
  logo pixels), `MatchingCommandTests` (image whatever the output setting, link line, QQ word, errors, disabled,
  access, one query per owner), help-card/QQ-word expectations updated. 358 tests pass. Not verified on a live
  chat platform.



## Atlas player picture, icon cache and query history (2026-11-08, in progress)

Maintainer decisions this round (redo after reviewing the first draft; draft commit `f9e1104` kept as reference):
a new picture after the Atlas website's player page (maintainer's reference screenshot), **old tarkov.dev cards
untouched** (image.py, arena_image.py, card.py, output.py, arena.py, help_image.py restored to `175f8bc`); Atlas data is
always a picture (text/Markdown are no longer maintained and will be removed later); tarkov.dev snapshot stays the
fallback. Search, budgets, per-user quotas and the refresh word stay as in `docs/atlas-integration-plan.md` §3.

- **Picture** (`atlas_card.build()` -> `atlas_image.render_atlas_image(card, icons)`, 1600 px dark board): Atlas logo and
  link at the very top (Atlas author's requirement), header (level badge, level, name, side as text, EXP bar, six
  small numbers), equipment grid (13 slots; tarkov.dev item picture from Atlas' `image` link = `base-image`, i.e. the
  weapon without attachments; never Atlas' `render`; English short name as on the Atlas page), faction (text) and
  prestige (icon), info row (account id, highest league, last active, lookup time), the mode's 12 numbers, rare
  achievements (9, by rarity seasonal > legendary > rare > common, then newest first; icon + name + share), statistics (overview/PMC/Scav, 15 rows), skills
  drawn like the old card (`skill_count`), the 10 latest achievements (icon, name, description <= 2 lines, rarity,
  share, time), footer with the Atlas thanks. Not shown (maintainer): favourite weapons/items, endings, edition and
  profile icon, armour class badges, stat icons, average survival time, account age.
- **Counter rules** (read off the reference picture, maintainer: the old K/D was wrong): K/D = player kills /
  (killed + left); K/D (all kills) = all kills / (killed + left); AI kills = all kills - player kills; overview =
  PMC + Scav, including the longest streaks (as Atlas shows); a counter Atlas omits (e.g. `Left`) is 0. The old cards
  keep their own (old) formula.
- **Pictures** (`atlas_assets.py`): only `https://assets.tarkov.dev/<name>.(png|webp|jpg)`; stored as PNG scaled into
  512 px under `<data>/atlas-assets/`, **no expiry**. One-shot setting `atlas_assets_update` downloads every common icon
  (all achievement icons of the regular and PVE `tasks` feeds, level badges from `items.playerLevels`, prestige icons;
  153 files, 12.7 s in the dev environment); anything else (item pictures) on first use (<= 4 at once, <= 48 per
  picture). Failures draw an empty placeholder box and are not retried for 10 minutes. `atlas_assets_clear` removes
  everything. The icon catalog (`catalog.json`) is built from the public cache (`public_resource`). Arena
  achievements are not in tarkov.dev's data (0/67 in the sample).
- **History** (`atlas_history.py`, `<data>/atlas-history.sqlite3`): raw Atlas document (gzip JSON) per successful
  lookup, one row per (account, mode, `looked_up`); never tarkov.dev snapshots. Settings: `atlas_history_enabled`
  (privacy switch; off = no writes and the file is deleted on load), `atlas_history_hours` (default 180, 1-8760),
  `atlas_history_max_mb` (default 64, 16-128; oldest rows go first). Read interface for later work: `latest`,
  `history(since, until)`, `compare` (oldest/newest in a range), `accounts(since)`. No scheduler.
- **Wiring**: `TarkovPlugin.atlas_picture()` / `atlas_icons()`; notes (rename, cache age) and the refresh hint go in the
  text line after the picture; a drawing failure answers with text and the links. **Arena under the Atlas source uses
  the tarkov.dev path for now** (its Atlas picture is the next step). `AtlasPlayers` keeps the raw document in
  `summary['doc']`.
- **Font**: `tools/build_atlas_chars.py` writes the characters of tarkov.dev's Chinese achievement names/descriptions to
  `data/atlas-text-chars.txt`, which `build_fonts.py` adds (subset 694 -> 1,071 glyphs, 296 -> 470 KB; package 848 KB of
  the 1 MiB cap). Including Chinese item names as well would have made the font 813 KB and the package too big.
- **Dev environment**: personal environment `astrbot_tarkov_datasearch` now also allows `json.tarkov.dev` and
  `assets.tarkov.dev`.

Invariant change (§8): besides the fixed Boss portrait paths, `assets.tarkov.dev` picture links from Atlas documents
and tarkov.dev's public data are fetched, validated by the URL pattern above; no other picture host.

Decisions taken without the maintainer (revisit): history switch on by default; size cap default 64 MB; stored
pictures scaled into 512 px; 10-minute retry wait and 48 downloads per picture;  arena temporarily on tarkov.dev; equipment names in English.

Observations for the maintainer: every PVE achievement in Atlas' PVE sample has `percent` 0, so "rare" cannot rank
there (tarkov.dev's PVE feed has its own completion shares); tarkov.dev's `base-image` of a weapon is the bare weapon
(small in the slot); the arm band picture is mostly black.

Tests: `test_atlas_picture.py` (card rules, odd fields, rendering, URL validation, sanitising, first-use download,
failure wait, prefetch/catalog/clear, shared downloads, history rows/pruning/size cap/delete) and the rewritten
`AtlasPluginTests` (picture whatever the output setting, links line, arena on tarkov.dev, history only for Atlas and only
when on, one-shot settings consumed once, drawing failure). 413 tests pass. Previews were rendered from the saved
probe responses in the scratch directory only (real player data, not in the tree). Not verified on a chat platform.

Follow-up (maintainer, 2026-11-08):
- **Rare achievements**: every mode now ranks by rarity, then completion time (newest first); Atlas' `percent` is only
  displayed. Reason: every PVE achievement has `percent` 0 in Atlas' data.
- **Known problem, recorded, not solved — the main weapon picture**: the slot shows tarkov.dev's picture of the weapon
  item alone (`base-image`), so a modded weapon appears bare: no scope, stock, magazine or other attachments, and it
  is drawn small in the slot. Showing the real build needs the attachments composed onto the weapon ("拼图"); Atlas'
  own composed render (`render`, `/atlas/api/lookup-art/...`) is excluded by the maintainer. Atlas' document has what a
  composition would need: each attachment in `slots[].children` (recursive) with its tarkov.dev picture link, slot
  name/id, and the weapon's `build` payload. Not started; to be designed with the maintainer.
- Font question (deleting the bundled font) answered in chat: without it Chinese cannot be drawn on servers without
  a Chinese system font; options A-D listed there, no change made.

Follow-up 2 (maintainer, 2026-11-08):
- **Full font on first use** (option C of the font question; `font_download.py`): the Atlas picture asks
  `FullFont.path_or_start()`; the first time it starts a background download of the full Noto Sans SC variable font
  (17,772,300 bytes, sha256 `a3041811…`, the same file the bundled subset is cut from, google/fonts commit `86aced9`)
  into `<data>/fonts/` and draws with the bundled subset meanwhile. Mirrors in order: optional setting
  `font_download_url` (https only), cdn/fastly/gcore.jsdelivr.net, raw.githubusercontent.com; a download is used only if
  size and sha256 match; when all fail the next try waits 30 minutes. With the full font `atlas_image.safe()` no longer
  replaces characters. The bundled subset stays as the fallback (package unchanged). Dev check: jsDelivr is blocked in
  this environment and was skipped; GitHub served the file in 1.9 s. jsDelivr's reachability from mainland China is
  not verified here; the setting lets an administrator point at a local mirror.
- Invariant change (§8): the font download may contact the four mirror hosts above and the administrator's https
  address; nothing else, and only the pinned file is accepted.
- **Weapon composition test** (experiment only, scratch directory, not in the plugin): Atlas gives attachments as a slot
  tree with tarkov.dev pictures but **no positions**, so attachments cannot be placed on the weapon picture the way the
  game draws it. Tested (a) matching the build to a tarkov.dev weapon preset (500 presets; the composed preset picture
  exists): no sample build matches exactly (AK-12 closest preset differs by 6 missing / 4 extra parts, M1A by 4 / 3), so
  this only helps unmodified presets; (b) a loadout board: weapon picture plus every attachment picture with name and
  slot (all 17/10 pictures found): complete and correct, but a parts list rather than an assembled gun, and three rows
  tall. Waiting for the maintainer's choice.

Follow-up 3 (maintainer, 2026-11-08): **option B adopted, guns by name only** — the equipment cells of the guns
(primary on sling, primary on back, holster) show the gun's English short name and its attachment count, no picture;
a new section "武器配件" lists each gun's attachments (depth first, loaded cartridges left out, at most 40) with the
tarkov.dev picture, name and slot name (top-level slots in gold). Rarity order for rare achievements: seasonal first,
then legendary, rare, common (maintainer). The "known problem" above is thereby handled by listing, not composing.

Follow-up 4 (maintainer, 2026-11-08): the equipment grid drops the three weapon slots (primary on sling, primary on
back, holster), the melee slot and the secure container: eight slots in two rows remain. The "武器配件" section is
removed (no weapon pictures or attachment lists at all now). Rare achievements are a full-width bar under the
equipment/headline panels: completion line and bar, then the nine achievements in one row (icon, name, share,
rarity). The equipment and headline panels share one height.

## Atlas Arena register and achievement text provenance (2026-11-08)

Implemented with OpenAI Codex on `feat/arena-query-display`, based on the maintainer's requested
`claude/vibrant-newton-lxci9c` head `13ae46c`. The maintainer reviewed a dark prototype, chose the original
Arena Masters register, then approved six-column totals tables, completed/total achievement counts and the
nine rarest achievements. This section supersedes the earlier temporary Arena-on-tarkov.dev routing decision.

- `main.py`: Atlas now handles explicit Arena queries/bindings through the same single-mode lookup, nickname
  resolution, quota, cache, refresh and history flow as the other modes. Only failed Atlas lookups fall back
  to labelled tarkov.dev snapshots; an explicit no-profile response does not. Snapshot-source behaviour remains.
- `atlas_card.build(..., 'arena')` uses `atlas_arena_image.build_arena()`, then `atlas_image.render_atlas_image()`
  dispatches to the Arena register. The shared old renderer accepts local typography/footer providers and can
  return its Pillow image, avoiding process-wide preview monkeypatches and repeated JPEG encoding. Default
  snapshot rendering is unchanged. Full-font loading still uses the existing first-use download mechanism.
- Register: original paper/leather/ARP medallion/W-L bars; per-section six-column totals tables, mode rows,
  completed/total achievements and up to nine achievement text cards in three columns, with rarity, share and date.
  No Arena icon fetch/catalog read: the known catalog has no Arena achievement artwork. Missing sections and
  totals are not invented. The former "last bout" label is now "latest achievement", since Arena does not return
  a last-match timestamp. The source header carries the unmodified Atlas logo and link; footer gives looked_up,
  server cache status, Atlas thanks and plugin attribution. Reply text retains clickable links and refresh hints.
- `atlas_card.achievement_data()` shares rarity order (seasonal, legendary, rare, common, unknown last), then
  newest achievement time, with PVP/PVE/season. Text provenance checked: `achievement_list()` reads each entry's
  `ch.name` / `ch.description` using `text_of()`, with independent `en` fallback for each field. The plugin does
  not translate these strings; the tarkov.dev catalog supplies icons only. No claim is made about Atlas' own
  upstream translation source. This behaviour now has regression coverage across all four modes.
- Preview: `tools/build_arena_legacy_preview.py [pinned-full-font.ttf]` invokes the production renderer with
  invented fixture data and marks the result as a preview. `docs/arena_legacy_atlas_reference_v2.jpg` includes
  invented ranked records for layout review; the live Atlas probe had only unranked records. No live player
  lookups were made for this work. Earlier visual experiments remain as comparison artifacts.

Validation: all 425 tests passed on Python 3.12 (12.748 s), including Arena Atlas routing, real JPEG rendering,
cache/refresh/name/url paths, history persistence, fallback vs missing profiles, rarity ordering, empty/odd fields,
text provenance and concurrent snapshot/Atlas rendering. Release ZIP contents/size and font coverage checks pass.
The updated preview was visually inspected. Live AstrBot/QQ delivery and Python 3.13 were not run here.

## Atlas settings floors and recoverable local budget pauses (2026-11-08)

Maintainer-specified policy from the annotated configuration screenshots, implemented with OpenAI Codex:
shared request interval minimum/default 10 seconds; per-user hourly query/refresh counts fixed at 10/3 with both
settings removed; per-user query interval minimum/default 30 seconds; refresh interval minimum/default 300
seconds; global player minute budget configurable from 1 to 10, default 10. The global hourly budget remains
1-200, default 150. Settings hints, help text and current user documentation now agree.

- `main.py` enforces these bounds when reading both grouped and legacy flat settings. Old per-user count keys
  have no effect. `AtlasCooldown` itself rejects intervals below 10 seconds. No persistent configuration rewrite
  or data deletion is required for existing installations.
- Local `AtlasBudgetError` no longer qualifies for tarkov.dev fallback, for both minute and hour exhaustion.
  The response gives seconds until the sliding window frees a slot; cached profiles remain readable and the
  next user query can resume after expiry. No timers, polling, scheduled retries or background requests were added.
  Actual Atlas/network failures retain the previous snapshot fallback rules.
- `AtlasBudget.check()` admits no request and takes no slot. The client checks it before waiting, then reserves
  and rechecks only after acquiring the shared gate, so global budget windows track actual HTTP start times.
  Rejected/cancelled gate waits consume no global slots; upstream 429 refund handling remains intact.
- Tests use virtual clocks for the new user intervals, never real sleeps or remote Atlas lookups. Added coverage
  checks removed keys/schema ranges, old-value bounds, fixed hourly count recovery, exact 30/300-second boundaries,
  ten-request default budget, minute/hour deadline recovery with no HTTP before the deadline, recheck after a
  competing waiter, actual start timestamps and cache access while a local budget pause is active.

Validation: all 432 tests passed on Python 3.12 in 12.567 seconds, including release ZIP and font checks;
`git diff --check` passed. No live QQ/AstrBot configuration panel or Python 3.13 validation in this environment.

## Remove the font-subsetting pipeline; complete typography everywhere (2026-11-08)

The maintainer explicitly chose to unify all pictures on the complete font and remove the trimmed font and
missing-character substitution, rather than retaining a frozen fallback. Implemented with OpenAI Codex.
This supersedes all earlier instructions in this file about rebuilding glyph subsets or updating character corpora.
Historical build records above are retained only as history.

- Removed `tools/build_fonts.py`, `tools/build_atlas_chars.py`, `data/atlas-text-chars.txt`, the bundled
  Noto Sans SC subset and its character list; removed the Boss-comment glyph corpus and source-character coverage
  tests. There is no fontTools dependency, font editing, code scan for glyphs or runtime `safe()` character
  substitution. Dynamic text is passed through unchanged; non-ASCII snapshot nicknames use the Chinese face.
- New `fonts.py` is shared by snapshot, Arena, Atlas, matching, help, status and Boss pictures. It retains the
  existing basic-layout choice for Atlas/matching and the original layout for the older cards. Font instances
  are bounded-cache entries keyed by font path, size, weight and layout. Bundled Latin fonts and OFL licences stay.
- `FullFont.ensure()` waits for a single shielded first-use download, reuses the verified file on disk and preserves
  existing pinned size/hash, mirrors, explicit proxy and retry wait. Failed downloads and unwritable caches have
  curated error messages. Cancelling a waiter does not cancel the shared download; plugin close does.
  No first-picture trimmed-font fallback remains.
- All chat and notification rendering paths prepare the font before drawing. Boss pagination also requires it
  before measuring and freezing pages, including monitor observations. Snapshot/help can return text with the
  font error; Atlas/status/Boss/matching report the font error when no picture can be drawn. Data queries, quotas,
  image styles and message permissions are otherwise unchanged.
- Development uses `tools/download_font.py` to cache the same unmodified pinned file under ignored `.cache/fonts/`.
  CI prepares this file before running tests. `tests/font_support.py` accepts this cache or `TARKOV_TEST_FONT` and
  verifies it without making network requests. Plugin test doubles return the prepared file instead of downloading.
  Preview tools use the same cached file; see `docs/development.md` for commands. Complete Chinese font data is not
  included in the plugin ZIP, and the removed subset/character artifacts are checked absent from it.

Validation: 435 tests passed on Python 3.12 in 14.639 seconds. Coverage includes complete-font glyphs outside the old
character list, unchanged non-ASCII names, all-renderer font source selection, no-font errors, shared first download,
waiter cancellation, close cleanup, mirror verification/retry, disk permission failures and image/text fallbacks.
Snapshot/help and the approved Atlas Arena preview were rendered from invented data; the snapshot was visually
inspected and the Arena output remained byte-identical. ZIP packaging passed (519,296 bytes before this documentation
update); all licences remain. Live AstrBot/QQ and Python 3.13 were not validated here.

## Mobile-readable PVP/PVE/season statistics and skills (2026-11-08)

The maintainer approved complete-profile mockups retaining the existing palette and all other sections, with a
horizontal combat table and exit-state bars replacing the three narrow statistics columns. Skills stay in two
columns with larger labels and squares. Arena was explicitly excluded. Implemented with OpenAI Codex.

The earlier `claude/vibrant-newton-lxci9c` branch was merged to main and deleted remotely before implementation.
Work therefore starts at main `9d19c62` on `feat/player-mobile-statistics`; no deleted branch is recreated.

- `atlas_card.py`: replaces the obsolete display columns with two `combat_rows` containing raw exit counters and
  table values. Survival = (survived + transit) / sessions, formatted to one decimal place, dash without sessions.
  The header and headline use the same function. Per-role all-kill and PMC K/D retain the prior killed+left
  denominator. The existing combined header's player-kill K/D is unchanged.
- `atlas_image.py`: approved 1600px board retained; larger horizontal PMC/SCAV rows (44px base numbers, 30px
  headers), exit bars and a six-outcome legend with PMC/SCAV counts. Oversized exit totals cannot draw outside the
  bar; original legend values remain visible. Skills retain two columns, with 34px base labels, 35px levels,
  28px squares and 78px row pitch; long text is fitted/clipped inside its cells. Elite stays gold.
- Snapshot `card.py` uses the same survival formula for all three main-game modes, including text/Markdown and
  Atlas failure fallback. Existing snapshot styles and K/D rules remain. Arena models, renderers and previews are
  untouched. The old PMC snapshot example changes from 80.0% to 84.0% because its 40 transits count as success.
- `tools/build_atlas_player_preview.py [pvp|pve|season]` renders the complete production card from invented counters,
  skills and achievements, without player or icon HTTP requests. The committed PVE example has icon placeholders;
  all three modes were also rendered to /tmp for inspection. README uses a link, not an inline test image.

Validation: all 438 tests pass on Python 3.12 (13.088 seconds), including formula regressions in all three modes,
combined header accuracy, zero games/deaths, exit counters/bar bounds, larger dual-column skills, full image
rendering and existing Arena tests. Production full previews were rendered for all three modes (1600x3184,
~482 KB JPEG) and the PVE image visually inspected. Package/whitespace checks pass. Live QQ/mobile delivery and
Python 3.13 were not run here; phone readability still depends on the chat client's image scaling.

## 0.5.1 review: labels, survival wording and alignment (2026-11-08)

Review of the mobile statistics commit (Claude Code), with the maintainer's decisions:

- Version 0.5.1 everywhere (`__init__`, `metadata.yaml`, `main.py`, CHANGELOG heading, footer test strings). Only
  the mobile-statistics entry moved from the untagged 0.5.0 section into 0.5.1; the other post-0.5.0 bullets
  (font pipeline, Atlas limits, Arena) were left where the maintainer wrote them.
- Equal labels now mean equal numbers: the table's all-kills ratio is "K/D（全部击杀）" again; the header/headline
  player-kill count is "击杀 PMC" (was "击杀数", which clashed with the table's all-kills "击杀"). Header/headline
  "K/D" still = player kills ÷ (killed + left). "擅离率" drops the space before "%".
- "幸存率" is the single wording (maintainer): Atlas picture, snapshot picture, text and Markdown, plus
  "最长连续幸存". Survival still counts transits (maintainer confirmed). Headline "幸存数" remains survived only,
  so 幸存数 ÷ 战局数 differs from 幸存率 whenever there are transits.
- `atlas_image.mid()` centres each text on its ink ('H0' for Latin/digits, one ideograph for Chinese) with a
  baseline anchor; used by the statistics table, exit bars, skills, both band styles and the header numbers, so
  Oswald, Share Tech Mono and Noto Sans SC sit on one visual line. The "100%" tick hangs inward inside the margin.
  Column widths rebalanced for the longer K/D header (still summing to 1536).
- README/CHANGELOG AI statement and badges add OpenAI Codex, matching the handoff notes.

Validation: 438 tests pass (Python 3.13), with new assertions for the labels, wording and in-margin text.
`docs/atlas_pve_preview.jpg` and `docs/preview.jpg` regenerated and inspected; PVP and season rendered for
inspection only. Arena and help previews untouched. Open item: the small-text sections (header, rare and latest
achievements, equipment) remain hard to read on phones — a full-board rescale is pending the maintainer's choice.

### Revert and real-data annotations (2026-11-08)

The full-board rescale (`b35b71a`) was reverted (`e78ee0f`) at the maintainer's request. The maintainer then
annotated a real PVE picture (one Atlas search + one lookup through the plugin's client; data and picture kept in
the session scratchpad only, never committed) and asked for:

- Atlas logo and source link moved into the player header's right side, replacing the small duplicate numbers; the
  top row now holds the mode title (left) and the query time (right). Badge position unchanged.
- Headline panel: Oswald 44 values and 22 px labels (fitted/clipped per cell).
- Rare achievements: the "% 玩家" line removed; names 24 px (min 18, then clipped) and rarity 20 px.
- Latest achievements: same table, larger type (header 22, name 26, description 22 with two wrapped lines, rarity
  24, time mono 24, 60 px icons); the "% 玩家" column removed in all modes. The Arena register's achievement cards
  also lose their share label (maintainer: all modes). Atlas reports 0 % for every PVE achievement.
- Maintainer decisions: transit counts as survival; "幸存数" stays survived-only; the empty body-armour slot with a
  plate-carrier rig is correct; a missing rarity ("—") is a game-side issue and is left alone.
- `fitted()`/`centered()` regain an optional `minimum` (default 10, so Arena output is unchanged).

Validation: 439 tests pass. The PVE example and the Arena Atlas example were regenerated from invented data.
Not yet decided: whether the rare-achievement title row, equipment labels and footer are enlarged too.
