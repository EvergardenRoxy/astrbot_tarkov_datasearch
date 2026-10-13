# Engineering handoff: `astrbot_tarkov_datasearch`

Audience: engineers or LLM agents debugging or reviewing this repository. This is a record of what was built,
why, what evidence the decisions rest on, and where the risk is. It is not user documentation (see README /
CHANGELOG for that). `checklist.md` is the plain-language Chinese companion; keep the two consistent when either
changes. The layout and conventions follow the maintainer's earlier plugin `astrbot_plugin_spark_analyse`.

## 1. Context

| Item | Value |
|---|---|
| Task | New AstrBot plugin to look up Escape from Tarkov player profiles in chat (maintainer request, 2026-10-13). |
| Inputs | Maintainer's analysis `report.md` of the-hideout/tarkov-dev (not in the tree; key facts restated in §2). Maintainer's own profile JSON downloaded from tarkov.dev, used only for a local comparison (§2.4), not committed. |
| Maintainer decisions | Personal data only (no item/price/task queries). Any nickname may be queried, as "mode + name". Data source: players.tarkov.dev static snapshots. Plain-text output first; image rendering later. v0.1 fields: level, side, K/D, survival rate, skills; lookup by nickname or account id. License GPL-3.0-or-later. Name index checked weekly (§9). AI statement names Claude Opus 5.5 (§9). |
| Branch | `claude/amazing-clarke-eu3xue` (repo was empty). No PR. |
| Version | 0.1.0 (`tarkov_core/__init__.py` `VERSION`, `metadata.yaml`, `@register(...)`, CHANGELOG; `test_version_is_consistent`). |
| Tests | 72, `python -m unittest discover -s tests`, Python 3.13 locally; CI on 3.12 and 3.13. |
| AstrBot evidence | 4.16.0 wheel read (not executed): `register_command(command_name, sub_command, alias: set, **kwargs)` in `astrbot/core/star/register/star_handler.py`. Plugin patterns copied from the spark plugin, which was live-tested on 4.28.2. **No live AstrBot run.** |

Maintainer's working rule (carried over from the spark plugin): before changing behaviour, propose a plan with its
pros and cons and wait for a decision. The v0.1 plan was confirmed in chat; the smaller choices in §6 were not.

## 2. Data source: evidence

### 2.1 Why not the live API

tarkov.dev's player page calls `https://player.tarkov.dev/name/<str>` and `/account/<aid>` with a Cloudflare
Turnstile token (`src/modules/player-stats.mjs`). Without a token the API answers 401 `{}` (probed). A bot cannot
obtain a Turnstile token legitimately, and the report's guidance is not to bypass Turnstile or rate limits. Not used.

### 2.2 players.tarkov.dev (what the plugin uses)

URL layout from TarkovMonitor (`TarkovDev.cs` `ITarkovDevPlayerJsonAPI`, `GameWatcher.cs` `ToPlayersApiString`),
revision `3cf361e`; all items below probed on 2026-10-13:

| Path | Content |
|---|---|
| `/{mode}/index.json` | JSON object `{"<aid>": "<nickname>"}`. `profile`: 2,972,076 entries, 30.2 MB gzip / 67.3 MB decoded. `pve`: 131,399. `pvp-season`: 41,818. Rebuilt daily around 00:02 UTC (`Last-Modified`). |
| `/{mode}/{aid}.json` | One profile, 3-35 KB. Unknown id: 404 `text/html`. |

- `mode` is `profile` for regular PvP, `pve`, `pvp-season`. `/regular/<aid>.json` is 404.
- **User-Agent matters.** curl's default agent and a Chrome agent string got Cloudflare's 403 "you have been blocked" page. `TarkovMonitor/1.0` and `astrbot_tarkov_datasearch/0.1.0 (+repo url)` got 200. The plugin sends its own name (`transport.USER_AGENT`). If Cloudflare's rules change, every request fails with the `BLOCKED` message; the proxy setting is the user-side remedy.
- `Accept-Encoding: gzip` → `Content-Encoding: gzip` with a strong `ETag`. `If-None-Match` with that ETag → 304 (probed). Without gzip the ETag is weak (`W/`), so always asking for gzip keeps conditional requests working.
- `cache-control: max-age=86400`, `cf-cache-status: HIT`: Cloudflare may serve a copy up to a day old.

### 2.3 Profile schema (as served)

Top-level keys: `aid, info{nickname, side, experience, memberCategory, selectedMemberCategory, prestigeLevel,
[prestigeGameMode, leagueRank, leagueMaxRank]}, customization, skills{Common[], Mastering[], Points}, equipment,
achievements (object id->epoch s; [] when empty), favoriteItems, pmcStats/scavStats.eft{totalInGameTime,
overAllCounters.Items[{Key[], Value}]}, [ending, battlePassProgress, seasonalRewards, selectedSeasonalPerkIds],
hideout, hideoutAreaStashes, items, customizationStash, updated (epoch ms)`. `bannedUntil` from the report's §4 was
not present in any profile seen; it is not used.

### 2.4 Freshness and nickname reliability (maintainer's own account, compared field by field)

- The maintainer's downloaded JSON equals `pve/<aid>.json` exactly (`dict ==`), and that object's `Last-Modified`
  matched `updated` to the second and the time the maintainer viewed the page. **Inference:** a
  tarkov.dev page view writes the snapshot. Snapshots of players nobody views stay old.
- The same account's `profile/<aid>.json` has an `updated` in 2024. **This is not a data-source problem**: the
  maintainer does not play PvP (maintainer's correction). An old `updated` therefore means "the player has not
  played this mode recently" or "nobody has viewed the player on tarkov.dev recently"; the reply says both.
- The current nickname is in none of the three indexes; the `pve` index does not contain the account id at all,
  although `pve/<aid>.json` exists.
- Consequences in code: render shows `updated` and notes > 30 days (`output.STALE_DAYS`); name-not-found text
  recommends querying by id; a name hit whose profile nickname differs gets a "may have been renamed" note.
- Case-insensitive duplicates in the `profile` index: 8,344 lower-cased names with more than one id (31 of them
  the empty string). 35 names carry whitespace such as a trailing `\n`; they are stripped. Two entries have names
  longer than 64 characters (one 16,000) and are skipped by the parser.

## 3. Design

| Piece | Where | Notes |
|---|---|---|
| Modes | `tarkov_core/modes.py` | key -> (path, label); aliases incl. Chinese. |
| Transport | `tarkov_core/transport.py` | One host constant, `allow_redirects=False`, `trust_env=False`, `auto_decompress=False` + own gzip decoder with compressed and decoded limits (pattern from the spark plugin), content type must be `application/json`, 304/403/404/429 mapped to `NotFound`/`QueryError` messages, aiohttp errors reduced to their type name (the proxy URL may hold credentials). |
| Name index | `tarkov_core/names.py` | Per mode, one SQLite file `names-<path>.sqlite3`, table `names(key, aid, name)` `WITHOUT ROWID`, PK `(key, aid)`, `meta(etag, checked_at, entries)`. Built from the downloaded document with a bytes regex (`ENTRY`), not `json.loads`, to avoid ~3M Python objects. Built into `.tmp`, then `os.replace`. One `asyncio.Lock` per mode covers both refresh and query, so the file is never replaced under this plugin's own reader. Failed refresh with an existing db: keep serving it, retry after `RETRY_SECONDS` (600). Without a db: the error reaches the user. |
| Profile | `tarkov_core/profile.py` | `summarize()` validates loosely: anything not of the expected type becomes 0/None. Counters use the shortest key containing all parts (tarkov.dev takes the first match). MIA: `MissingInAction`, falling back to `Left` (the name tarkov.dev's page uses). Skills filtered like tarkov.dev (progress > 0, LastAccess > 0, id known). |
| Static tables | `tarkov_core/data/static.json`, `tools/build_static.py` | Level table (79 rows) and 49 skill names from `json.tarkov.dev/regular/items` + `items_zh`. The PvE level table was checked identical. Not fetched at run time. |
| Output | `tarkov_core/output.py` | Plain text, Beijing time (UTC+8, labelled). Elite skills listed in full; `skill_count` limits the rest. |
| Plugin | `main.py` | `/tarkov` (alias `/塔科夫`). Flow: access check → parse `[mode] target` → digits: fetch by id, on 404 fall back to the name index if the digits are a valid name → name: refresh notice if needed, lookup, disambiguate (one exact-case hit wins; otherwise >1 hit lists ids) → fetch → render. One in-flight query per sender (`self.active`, sha256 of origin + sender like spark). Profile summaries cached in memory (TTL `profile_cache_minutes`, at most `CACHE_ENTRIES` = 128, oldest-inserted evicted). |

Measured (this container, 2026-10-13): PVP index download + build 23-26 s, db 97 MB, process max RSS ~106 MB;
pve ~1 s / 4.3 MB; pvp-season ~0.6 s / 1.4 MB; an unchanged index refresh (304) 0.2 s; a profile fetch 0.2 s.

## 4. Test map

| File | Covers |
|---|---|
| `test_profile.py` (13) | level boundaries against the bundled table, hand-checked level 53, table shape, counter selection, side stats, `Left` fallback, malformed counters, summary fields, skill filter/sort/elite, non-profiles, missing fields, mode aliases, `profile` path. |
| `test_output.py` (5) | full rendering, no deaths / no raids, skill limit, stale flag, prestige and notes. |
| `test_names.py` (15) | entry parsing (escapes, whitespace, empty, over-long), HTML rejected, empty index keeps old db, nickname rule, build-then-reuse, ETag + 304, replacement, failed refresh keeps serving + retry delay, failure without db, one download under concurrency, match cap, corrupt db rebuilt, leftovers removed, refresh interval, weekly default. |
| `test_transport.py` (15) | local aiohttp server with `HOST` patched: gzip + agent header, plain body, 404, 403/429/500 messages, no redirects, HTML/non-object/invalid JSON, compressed/decoded limits (gzip and plain), wrong content type, truncated gzip, other encodings, index download + 304, connection error text, environment proxy ignored, proxy validation. |
| `test_plugin.py` (21) | SDK stubbed like the spark tests: help, id, name, default mode, refresh notice (size only for PvP), not found, digits missing as id and name, ambiguity (incl. two exact-case hits), exact case wins, rename note, missing id, digit-name fallback, bad input, error messages, cache on/off and bound, one query per sender (bounded wait), access modes incl. unknown value, proxy/timeout reach transport. |
| `test_release.py` (3) | version consistency, schema defaults equal code fallbacks, package contents. |

Mutation check (scratch copy, one change at a time, full suite, 120 s limit each): 24 mutations of the guards
above (no-redirect, trust_env, both size limits, gzip-only header, ETag, content type, lock, retry delay and check,
failure without db, empty index, exact-case rule, unknown access mode, one-per-sender, cache bound, rename note,
stale note, shortest key, digit message). All killed, none hangs. The first run showed 4 survivors and a hang;
tests were added or bounded for each, and one survivor was a no-op mutation (it edited a comment).

## 5. Not verified

- No live AstrBot run; no chat platform. The command registration with `alias={'塔科夫'}` is read from the 4.16.0
  source, not exercised.
- Windows: `os.replace` of the index db fails if another process holds the file open. Within the plugin, reads
  and replacement share a lock; `needs_refresh()` opens the file outside the lock (read-only, brief).
- Whether a tarkov.dev page view always refreshes the snapshot (§2.4) is inferred from one observation.
- Cloudflare's agent-based rule (§2.2) is observed behaviour, not documented anywhere.

## 6. Decisions made without the maintainer (revisit)

1. `access_mode` defaults to `all` (spark defaults to `admin_only` because each use costs model tokens; this
   plugin costs one small request). Schema default and code fallback are kept equal by `test_release`.
2. Times in UTC+8 with a label, not configurable.
3. Stale threshold 30 days (`output.STALE_DAYS`), not configurable.
4. K/D = all kills / deaths (the counters `Kills`/`Deaths`, as in the game's overall stats); PMC kills shown separately.
   tarkov.dev's page shows the raw counters only.
5. Nickname input limited to `[A-Za-z0-9_-]{3,15}` (tarkov.dev's search asks for 3-15; the game's character set).
6. The maintainer's profile JSON is not committed (it also holds other players' nicknames from dog tags); fixtures
   are synthetic but shaped like it.
7. `report.md` is not committed; §2 restates what this code depends on.

## 7. Invariants for future changes

- Never call `player.tarkov.dev` or try to obtain or replay Turnstile tokens.
- Keep `trust_env=False`, `allow_redirects=False`, the fixed host, and both size limits; the tests above pin them.
- Keep identifying the client honestly in `User-Agent`.
- `summarize()` must never raise on odd field types except for a missing `info.nickname`; profile content is
  third-party data.
- Messages shown in chat come from `QueryError` texts written here or from exception type names, never from raw
  exception strings.
- When upstream game data changes (new levels or skills), re-run `python tools/build_static.py` and commit the diff.

## 8. Ideas recorded for later (not requested for v0.1)

- Image output (maintainer's plan: render Markdown to an image).
- An `llm_tool` so the main chat model can look players up (see the spark plugin's §2.1-2.2 for the stop-event and
  no-return-value pitfalls).
- Weapon mastering, equipment (needs item names: `json.tarkov.dev/<mode>/items`), achievements by name, prestige
  icons, account type flags (`memberCategory`; tarkov.dev's flag table is in its player page).

## 9. Maintainer follow-up (same day, before release)

| Request | Change | Test |
|---|---|---|
| Check the name index weekly instead of daily. | Default `name_index_refresh_hours` 24 -> 168 in `_conf_schema.json`, the `main.py` fallback and `NameIndex(refresh_hours=168)`. Range stays 1-720. Chat texts ("每天" -> "每周") and the config hint updated; the hint says tarkov.dev itself rebuilds the index daily, so a shorter interval finds new or renamed players sooner. | `test_default_check_is_weekly`; `test_schema_defaults_match_code_fallbacks` keeps schema and code equal. |
| The 2024 PvP snapshot is because the maintainer does not play PvP, not a retrieval problem. | §2.4 corrected. The > 30 days line no longer says the data "may be outdated"; it names both causes (player inactive in that mode, or not viewed on tarkov.dev recently). README/checklist/CHANGELOG wording follows. | `test_stale_profile_is_flagged` (unchanged assertion on the day count). |
| AI statement: name the model. | README and CHANGELOG badges and statements, CHANGELOG 0.1.0 note and checklist name Claude Opus 5.5 (via Claude Code); the open decision about it was removed from §6. | - |
