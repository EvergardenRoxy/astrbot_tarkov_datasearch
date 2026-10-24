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
| License / AI statement | GPL-3.0-or-later. README and CHANGELOG state the plugin was written by Claude Opus 5.5 (via Claude Code) and reviewed by the maintainer. |
| Released | 0.4.4 (`tarkov_core/__init__.py` `VERSION`, `metadata.yaml`, `@register(...)`, CHANGELOG; `test_version_is_consistent`). Release branches `v0.3.0` … `v0.4.4` are stacked one commit each (§10). |
| Work branch | `1024` (head `5630c68` + later). Version number still 0.4.4: status, Boss and item features are **unreleased**. No PR. |
| Tests | 307 on `1024` (`python -m unittest discover -s tests`, needs `pip install -r requirements.txt`; local HTTP servers need sockets). CI runs Python 3.12 and 3.13. 0.4.4 had 193. |
| AstrBot evidence | 4.16.0 wheel read and its registration/wake/filter/parser bodies replayed by `tools/verify_qq_routes.py` (135 cases, SDK and network boundaries doubled). Plugin patterns copied from the spark plugin, which was live-tested on 4.28.2. The maintainer ran a small closed test of 0.3.0. **No live AstrBot run of the 1024 features; QQ image delivery and proactive push are unverified.** |

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

### 2.3 Public game data: json.tarkov.dev (status, Boss, items)

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
| Card / outputs | `card.py`, `output.py`, `image.py`, `help_image.py`, `fonts/` | `build_card()` is the single content model; `render_text`, `render_markdown`, `render_image` (JPEG q85, 820 px, seeded grain so output is byte-stable). Image style: dossier paper + dog tag + exit-status bars; footer with data time (GMT+8), plugin name/version/repo and the open-source notice. Exit wording follows the game: 撤离 / 匆匆逃离 / 转移 / KIA / 行动中失踪 (`card.EXITS`). Bundled OFL fonts (Noto Sans SC subset, Oswald, Share Tech Mono); other characters draw as `?`; `tools/build_fonts.py` rebuilds the subset from the characters in code, skill names and `data/item-label-chars.txt`. |
| Plugin | `main.py` | Commands `tarkov` (alias `塔科夫`), `status`/`状态`, `boss`/`刷率`, `item`/`物品` plus namespace forms (`tarkov status` …). Optional leading `/` accepted. Flow for players: access check → parse `[mode] target` → own binding / link / id / name → profiles → `show()` → `reply()` (output per `output_mode`). One in-flight query per sender (keyed hash). |
| Accounts | `accounts.py` | `bindings(owner, aid, nickname, bound_at)`; `owner` = HMAC-SHA256 with a random 32-byte `binding.key` (mode 0600). `names(key, aid, name, seen_at)` shared by everyone, fed by every fetched profile, exact-case first then most recent, capped at 200,000. |
| Name index (optional) | `names.py` | Off by default (`name_index_enabled`). Per mode a SQLite `names-<path>.sqlite3` built from the downloaded index with a bytes regex (not `json.loads`), built into a unique temp file then `os.replace`d; one lock per mode; failed refresh keeps the old db and backs off 10 min; first-download failure also backs off; limits 64 MiB / 192 MiB. PvP: 23-26 s, 97 MB db, ~106 MB RSS. Used only for names, never for ids. |
| Public data cache | `public_api.py`, `resource_cache.py` | Fixed host `json.tarkov.dev`; validated `ResourceKey` (mode translated pvp→regular, season→pvp-season; language; item id). Limits 32 MiB received / 64 MiB decoded. Lazy SQLite `public-data/resources.sqlite3`: body + validators + cache-control + times; LRU 32 entries / 128 MiB payload; TTL `public_data_cache_minutes` (default 5, 0 = always check); conditional 304; stale fallback only when explicitly allowed (never for 404 or config errors); Retry-After suppression; shared in-flight fetches (cancelled readers don't cancel others); ≤ 2 downloads, ≤ 32 pending; malformed 200 cannot overwrite a good row. Not yet used by player snapshots. |
| QQ / routing | `platform_compat.py`, `tools/verify_qq_routes.py` | `qq_official_compat_enabled` (default true) adds a second standard handler for slash-prefixed commands on `qq_official`/`qq_official_webhook` only, disjoint from the framework path, delegating to the original handlers. `command_style` (§6). `short_commands_enabled` edits real handler metadata for the six status/Boss/item short handlers (namespace routes stay). |
| Polling / notifications | `polling.py`, `notifications.py`, `boss_notifications.py` | One cancellable, non-overlapping task per feature (status, Boss); first check immediate, delay starts after a cycle, no catch-up, failure backoff ≤ 30 min, no worker when disabled or without targets. Separate lazy SQLite files; baseline + event written atomically; first success is a silent baseline; per-target submission state, ≤ 3 attempts, permission errors pause the target durably (reset: disable+save, enable+save); `send_message()` true = submitted, `None` = terminal unknown; no exactly-once. Retention 7 days / 200 events / 64 MiB. Targets are explicit UMOs (≤ 50), intersected with allowed origins and known proactive capability. |
| Server status | `server_status.py`, `status_image.py` | Normalised feed (component identity by id, then normalised name; compare status codes, add/remove, global message, notice id+content+time; ignore order and display names). Unknown codes are never shown as healthy. Failed requests never mean downtime. Image shows overall/component state, ≤ 3 notice excerpts (400 chars), checked/fetched/version times, stale flags. |
| Boss | `boss_monitor.py`, `boss_table_image.py`, `boss_portraits.py` | Scope: eleven daily bosses + GOONS (`bossKnight`, shown `三狗（GOONS）`) by stable mob-id allowlist; no BEAR/USEC/followers. Excluded maps: Icebreaker, original Ground Zero, Lab Dark, Labyrinth, tutorial; retained `ground-zero-21` is labelled `中心区`. Twelve columns: 中心区、白天工厂、夜间工厂、海关、森林、海岸线、立交桥、储备站、灯塔、塔科夫街区、实验室、码头. Conditional records are never summed; compare map+mob+rule identity, then probability; probabilities must be finite 0–1 numbers. Dark matrix (Boss rows, map columns, mode top-left, 1760 px); changes red; exact 100% and its Boss name bold red; explicit 0% printed; unlisted cells blank. Query snapshots/continuation `boss 页 <12-hex> <n>` kept ≤ 24 h, 16 groups, 32 MiB. Scope policy `daily-goons-selected-maps-matrix-v4`: older baselines/events/snapshots are retired on open. Portraits: fixed assets.tarkov.dev paths from the feed's known portrait links, **manual-only** (`boss_portraits_update` one-shot setting), 128 px sanitized thumbnails, 365-day expiry, placeholder when missing; no game art is bundled (THIRD_PARTY). |
| Items | `items.py`, `item_catalog.py`, `item_image.py`, `item_icons.py` | `item <name>` = attributes common to all three modes (no prices, no default mode); `item <pvp|pve|season> <name>` = that mode's prices, limits and verified differences. Names: full/short, zh/en, normalised, `id:<24-hex>`; quote a name starting with a mode word. Common = same value in all three modes with compatible `properties.propertiesType`; missing/null/zero stay distinct; partial modes give an identity-only card with "核对未完成". Whitelisted attributes only (not exhaustive). Flea: last low, 24 h stats, counts, scan time, flea restrictions; trader buy and sell separate, original currency + RUB, LL, task unlock, limits; zero/negative/missing prices are not free offers. Lazy `item-catalog.sqlite3` with versioned content signatures, off-loop builds, atomic replacement, last-good fallback. Search tiers exact → NFKC/case → substring, ≤ 25 candidates, never silently picks. Pages `item 页 <12-hex> <n>` from immutable 1-hour snapshots (≤ 16 groups / 16 MiB) that re-check current feature switches. Optional thumbnails: selected item only, fixed assets path, 7 days, ≤ 128 PNGs / 32 MiB. The real feed has one virtual dogtag id (`customdogtags12345678910`). |
| Settings | `_conf_schema.json` | New data features default **off** (`status_enabled`, `boss_enabled`, `items_enabled` and their broadcast / QQ-push switches; item sub-switches default true under the parent). Poll intervals `status_poll_minutes` (5) and `boss_poll_minutes` (10), 2–30 minutes each. `test_release` keeps schema defaults equal to code fallbacks. |

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
- **Command style**: `COMMAND_STYLES` in `tarkov_core/__init__.py` (`mention` = `@机器人 tarkov` default, `slash`,
  and `mention_slash` for QQ official) is the only place those strings are written; everything else goes through
  `TarkovPlugin.cmd()`. Matching is unchanged (`tarkov`/`塔科夫`, optional `/`). Tencent allows unverified QQ bots only
  "@bot + command" without a slash.
- **Access**: `access_mode` defaults to `all`; `allowed_origins` / `user_whitelist` accept lists (a string counts as one
  item, other types deny and log, empty items ignored). Query access and broadcast targets are separate things.

## 5. Tests and validation

Suites (`tests/`): `test_profile`, `test_output`, `test_image`, `test_help_image`, `test_arena`, `test_names`,
`test_transport`, `test_accounts`, `test_plugin` (player flow, bind, access, output modes, command style, QQ routes,
proxy/timeouts, concurrency), `test_release` (version, schema = code defaults, declared imports, package contents),
`test_public_api`, `test_resource_cache`, `test_status`, `test_status_plugin`, `test_boss`, `test_boss_plugin`,
`test_boss_portraits`, `test_items`, `test_item_plugin`, `test_item_icons`; helpers `fixtures.py` and `astrbot/`
(SDK stubs). Fixtures are invented.

Method: tests first and shown failing on the old code, then the fix, then the full suite. Each fix round was
mutation-checked in a scratch copy (each guard reverted alone, full suite, time-limited); survivors led to new tests
(e.g. unique download names, empty-bar text, the default-mode rule). Typical guarded behaviour: no-redirect,
`trust_env`, both size limits, ETag/304, content type, locks, retry delay, exact-case rule, one-per-sender, cache bound,
counter clamp, bar width clamp, skill de-duplication, keyed owner hash, failed-vs-missing, single flight, semaphore,
link line after images, command-style literals, scope/aliases, 100% styling, migration of old baselines.

Live read-only smokes (real network, SDK stubbed, nothing kept in the tree): player flows in all four modes; public
cache 200 → 304; status (nine components); Boss tables for three modes (sixteen then twelve maps; 29/29/27 selected
records, two exact-100% records per mode); item queries (nine public requests on first load, zero on repeat, one
thumbnail download). Previews were inspected visually.

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
- Item price semantics beyond the whitelist; mixed-currency edge cases rely on the feed's own fields.

## 7. Decisions made without the maintainer (revisit)

1. `access_mode` defaults to `all` (the spark plugin defaults to admin-only because it costs model tokens).
2. Times in UTC+8 with a label (images "GMT+8 {time}", text/Markdown "（北京时间）"), not configurable.
3. Stale threshold 30 days (`card.STALE_DAYS`), not configurable.
4. K/D = all kills ÷ deaths; PMC kills and PMC K/D shown separately (tarkov.dev shows raw counters only).
5. "预留默认查询竞技场的设置" was read as the `arena_by_default` switch, not an Arena option for `default_mode`.
6. Arena rank = highest rank whose lower bound BestArp has passed (overlapping table cut at each upper limit).
7. The open-source sentence is spaced like the other chat texts (a space between CJK and Latin).
8. Nickname rule 3–24 characters (longest generated name seen: 21).
9. Item/Boss/status sub-switches default true under off-by-default parents; proactive broadcast targets are separate
   from access lists.

## 8. Invariants for future changes

- Never call `player.tarkov.dev` or try to obtain or replay Turnstile tokens; do not bypass rate limits.
- Keep `trust_env=False`, `allow_redirects=False`, the fixed hosts (`players.tarkov.dev`, `json.tarkov.dev`, and the
  fixed `assets.tarkov.dev` paths for chosen item ids / known Boss portrait links), and all size limits. Never fetch a
  feed- or user-chosen host. Keep identifying the client honestly in `User-Agent`.
- `summarize()` and the status/Boss/item normalisers must never raise on odd third-party field types; malformed 200
  responses must never overwrite a last-good cache row, baseline or index.
- Chat messages come from `QueryError` texts written here or exception type names, never raw exception strings.
- Upstream failure is never "game down", "0% spawn" or "item does not exist". Failed requests do not advance baselines.
- Never render through AstrBot's `text_to_image`/`html_render` (they post content to a remote renderer by default); draw
  locally with the bundled fonts. After adding Chinese text run `python tools/build_fonts.py`
  (`test_font_covers_every_character_of_the_code` fails until then). Never subset or modify `ShareTechMono-Regular.ttf`
  (Reserved Font Name 'Share').
- Commands shown to users are built only through `TarkovPlugin.cmd()` from `COMMAND_STYLES`
  (`test_command_style_setting` checks it).
- Every help and profile reply carries its link as text, whatever the output (`test_links_are_always_given_as_text`).
- Boss portraits download only on the explicit one-shot setting; no data feature may start a hidden poller (each
  polling feature has its own 2–30 minute setting).
- Disabled features must not download data, start tasks, or leave stale price sections reachable through old pages.
- When upstream game data changes (levels, skills) re-run `tools/build_static.py` and commit the diff.

## 9. Maintainer requirements and open work

- **Output formats (maintainer correction, 2026-10-24)**: *image is the default output; text and Markdown must stay
  for compatibility with platforms that cannot send images.* Players, Arena and help already follow `output_mode`.
  Status, Boss and item queries and broadcasts are currently **image-only** (their schema hints say so), and
  `docs/development-plan.md` carries a correction banner for its old "image-only" wording. **Maintainer decision:
  postpone text/Markdown for the new features for now and keep their requirement on record.** When resumed: make
  `output_mode` govern status, Boss tables, item cards/candidates/pages and error replies (unknown → image); add text
  and Markdown renderers beside each image renderer from the same card/page model (same pagination and `页` tokens;
  Boss changes marked in text); image failure falls back to text with a note; broadcasts follow `output_mode`;
  tests per feature × three outputs; then rewrite the plan text in `docs/development-plan.md` §1/§2/§9. Binding
  confirmations are control replies and stay short text.
- **QQ acceptance and release**: verify passive images, proactive push, then decide whether the minimum AstrBot version
  changes; version bump, README and CHANGELOG at the maintainer's release request.
- **Old history outside `1024`** (maintainer decided on 2026-10-24 to clean only `1024`, no merge for now): `main`
  (through the merged PR #2), the tag `v0.4.4-release`, and GitHub's `refs/pull/*` still hold the two commits that
  carried one reference player's nickname (see §10). Cleaning them needs a force-push of `main`/tag changes and, for
  the pull refs, GitHub support; the maintainer decides. Merging `1024` into `main` will bring the cleaned history
  as unrelated commits, so decide this before that merge.
- Ideas not requested: an `llm_tool` so the chat model can look players up (see the spark plugin's stop-event and
  no-return-value pitfalls), weapon mastering / equipment / achievements by name, historical price charts, barter and
  crafting profit, quiet hours and coalescing for broadcasts, ETag checks for player snapshots, using the persistent
  public cache for player data. (The old plan to migrate player output to image-only is cancelled, see above.)

## 10. History worth keeping

**Releases** (branches `v0.3.0`–`v0.4.4`, each one commit on the previous one): 0.1.0 text output; 0.2.0 image and
Markdown output (maintainer chose the dossier style + dog tag + exit-status bars); `v0.3.0` bind-and-query (tested by the maintainer); `v0.4.0` image footer
+ Arena; `v0.4.1` `_`/`-` name suggestions; `v0.4.2` PMC K/D, source lines, Arena ranks, image wording; `v0.4.3` help as
an image, links always as text, `@机器人 tarkov` wording, logo; `v0.4.4` `command_style` setting. `1024` carries the
unreleased status/Boss/item/QQ work (published 2026-10-24, normal push, no PR).

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
byte size of the character list (the subset holds ~480+ glyphs plus the item corpus, package < 1 MiB).
