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
| Version | 0.4.2 since §21; 0.4.1 since §20; 0.4.0 since §19; 0.3.0 since §16; 0.2.0 since §15; 0.1.0 before (`tarkov_core/__init__.py` `VERSION`, `metadata.yaml`, `@register(...)`, CHANGELOG; `test_version_is_consistent`). |
| Tests | 184, `python -m unittest discover -s tests`, Python 3.13 locally; CI on 3.12 and 3.13. |
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
| Card | `tarkov_core/card.py` | `build_card(summary, mode_label, skill_limit, notes, now)`: the one content model for every output (formatted rates/K/D, Beijing-time strings, stale days, skills shown = all elite + `skill_limit` others, notes). |
| Output | `tarkov_core/output.py` | `render_text(card)` (unchanged text format; `render(summary, …)` wraps it) and `render_markdown(card)` (escapes Markdown in third-party text). |
| Image | `tarkov_core/image.py`, `tarkov_core/fonts/` | `render_image(card)` -> JPEG bytes (q85, 4:4:4), 820 px wide, height from content. Bundled OFL fonts; characters outside `NotoSansSC-Subset.chars.txt`/ASCII drawn as `?`. Seeded paper grain, so output is byte-stable. |
| Plugin | `main.py` | `/tarkov` (alias `/塔科夫`). Flow: access check → parse `[mode] target` → digits: fetch by id, on 404 fall back to the name index if the digits are a valid name → name: refresh notice if needed, lookup, disambiguate (one exact-case hit wins; otherwise >1 hit lists ids) → fetch → `reply()`: card, then image (`asyncio.to_thread`, `Image.fromBytes`, text on any failure), text or Markdown per `output_mode` (unknown value -> image). One in-flight query per sender (`self.active`, sha256 of origin + sender like spark). Profile summaries cached in memory (TTL `profile_cache_minutes`, at most `CACHE_ENTRIES` = 128, oldest-inserted evicted). |
| Accounts | `tarkov_core/accounts.py` | Since §16 the main way to resolve names. One SQLite file: `bindings(owner, aid, nickname, bound_at)` with `owner` = sha256(platform id NUL sender id); `names(key, aid, name, seen_at)` shared by everyone, fed by every fetched profile (`TarkovPlugin.profile()`), lookups exact-case first then most recently seen, capped at `MAX_NAMES` = 200,000 (least recently seen dropped). |

Measured (this container, 2026-10-13): PVP index download + build 23-26 s, db 97 MB, process max RSS ~106 MB;
pve ~1 s / 4.3 MB; pvp-season ~0.6 s / 1.4 MB; an unchanged index refresh (304) 0.2 s; a profile fetch 0.2 s.

## 4. Test map

| File | Covers |
|---|---|
| `test_profile.py` (20) | player-page link parsing (modes, old /player/ form, arena, foreign hosts), page and search links, level boundaries against the bundled table, hand-checked level 53, table shape, counter selection, side stats, `Left` fallback, malformed counters, summary fields, skill filter/sort/elite, control characters, odd shapes in every field (>1000 combinations), huge integers, non-profiles, missing fields, mode aliases, `profile` path. |
| `test_output.py` (11) | exit-status wording, full rendering (account-wide play time label), text unchanged through the card model (exact string), Markdown (escaping, empty side, skills hidden), no deaths / no raids, skill limit, stale flag, out-of-range timestamps, prestige and notes. |
| `test_image.py` (16) | exit legend wording, JPEG of the right width and size, byte-stable (also after clearing the grain cache), height follows skills and notes, odd cards (missing values, empty sides, long/foreign names, huge numbers, every mode), long text shrunk to fit, empty sides say so, `safe()`, every non-ASCII character of the code and skill table is in the subset font, listed characters have glyphs, licences shipped and Share Tech Mono unmodified. |
| `test_names.py` (24) | entry parsing (escapes, whitespace, empty, over-long, control characters), HTML rejected, empty index keeps old db, nickname rule (3-24), build-then-reuse, ETag + 304, replacement, failed refresh keeps serving + retry delay, first-download failure backed off, one download under concurrency, match cap, exact case first + total, corrupt db rebuilt, damaged names table rebuilt, URI characters in the data dir, old leftovers only removed at start, reload during a download, two instances never share a download file, failed build removes its temp file, refresh interval, weekly default. |
| `test_transport.py` (18) | local aiohttp server with `HOST` patched: gzip + agent header, plain body, 404 (Chinese default text), 403/429/500 messages, no redirects, HTML/non-object/invalid JSON, compressed/decoded limits (gzip and plain), wrong content type, truncated and garbage gzip, other encodings, profile parsed off the loop, index limits, index download + 304, connection error text, environment proxy ignored, proxy validation (explicit port). |
| `test_plugin.py` (66) | (§16) bind then query yourself (same platform, other group, other platform, default mode without a profile), bind by id/name and its failures, unbind, query by player-page link (mode conflict, arena, missing profile), names learned from queries with the index off, renamed player found by an old name, refresh link in text/Markdown/image replies, a failed name record does not fail the query; SDK stubbed like the spark tests: help, id, name, default mode, refresh notices (download vs check, size only for a first PvP download), texts follow the interval, not found, digits missing as id and name, ambiguity (incl. two exact-case hits, "等，共 N 个"), exact case wins, rename note, missing id, digit-name fallback, long generated names, bad input, full-width digits, error messages, index 404 and timeouts in chat, cache on/off and bound, one query per sender (bounded wait), access modes incl. unknown value and list types, empty list rows, infinite settings, proxy with empty address fails closed, proxy/timeout reach transport, default output is a JPEG image (also for an unknown setting), Markdown and text outputs, image failure falls back to text with a note, image drawn off the loop. |
| `test_accounts.py` (8) | owner key per platform and keyed (HMAC, §17), secret file created once with mode 0600, bind/rebind/unbind, names case-insensitive, old names kept after a rename, exact-case then most recent order, record bounded. |
| `test_arena.py` (16) | arena summary (ranked/unranked split, labels and order, counters), hostile counters and mode keys, odd shapes in `stat`/entries never raise, non-profiles, text (exact lines, no level), Markdown tables and escaping, no games, stale note. |
| `test_release.py` (5) | version consistency, schema defaults equal code fallbacks, direct imports declared (aiohttp, yarl, pillow), package contents (fonts and licences included, zip under 1 MiB). |

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
- Windows UNC data directory (`\\server\share\…`): `as_uri()` gives `file://server/…`, which SQLite rejects
  (`invalid uri authority`, checked on Linux with a constructed URI). Not handled; AstrBot's data dir is normally local.

## 6. Decisions made without the maintainer (revisit)

1. `access_mode` defaults to `all` (spark defaults to `admin_only` because each use costs model tokens; this
   plugin costs one small request). Schema default and code fallback are kept equal by `test_release`.
2. Times in UTC+8 with a label, not configurable.
3. Stale threshold 30 days (`output.STALE_DAYS`), not configurable.
4. K/D = all kills / deaths (the counters `Kills`/`Deaths`, as in the game's overall stats); PMC kills shown separately.
   tarkov.dev's page shows the raw counters only.
5. Nickname input limited to `[A-Za-z0-9_-]{3,24}`: the game's character set; 24 leaves room above the longest generated name seen (`TarkovCitizen…`, 21). Was 3-15 until §13.
6. The maintainer's profile JSON is not committed (it also holds other players' nicknames from dog tags). Fixtures,
   the README example and `docs/preview.jpg` use invented values only (§18; before §18 the fixture numbers had been
   copied from the maintainer's PVE profile).
7. `report.md` is not committed; §2 restates what this code depends on.
8. "预留默认查询竞技场的设置" read as the `arena_by_default` switch (Arena joins queries without a mode), not as an
   Arena option for `default_mode` (§19.3).
9. Arena ranks from BestArp with the maintainer's overlapping table cut at each rank's upper limit; the profile has
   no current ARP, so the rank shown is the highest reached (§21).
10. The maintainer's open-source sentence is spaced like the other chat texts (a space between CJK and Latin); the
    image footer says "GMT+8 {time}", the text and Markdown replies still say "（北京时间）" (§21).

## 7. Invariants for future changes

- Never call `player.tarkov.dev` or try to obtain or replay Turnstile tokens.
- Keep `trust_env=False`, `allow_redirects=False`, the fixed host, and both size limits; the tests above pin them.
- Keep identifying the client honestly in `User-Agent`.
- `summarize()` must never raise on odd field types except for a missing `info.nickname`; profile content is
  third-party data.
- Messages shown in chat come from `QueryError` texts written here or from exception type names, never from raw
  exception strings.
- When upstream game data changes (new levels or skills), re-run `python tools/build_static.py` and commit the diff.
- Never render through AstrBot's `text_to_image`/`html_render` (they post content to a remote renderer by default). After adding Chinese text to the code or new skill names, re-run `python tools/build_fonts.py`; `test_font_covers_every_character_of_the_code` fails until then.
- Never subset or otherwise modify `ShareTechMono-Regular.ttf` (Reserved Font Name 'Share').

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

## 10. Security audit of 0.1.0: verified, proposals only (not implemented)

Source: `security_audit.md` (Chinese, static read-only audit dated 2026-10-14, provided by the maintainer; not in the
tree). It reports 0 high, 2 medium, 6 low, 10 informational. Each item was re-checked against the code; the
behavioural ones were reproduced with a scratch script (real plugin module with the SDK stubbed, local aiohttp
server). No code was changed; the maintainer decides what to fix (working rule, §1).

| Audit id | Verdict | Evidence |
|---|---|---|
| M-1 `allowed_origins` as a string becomes a substring test | **Confirmed, needs a hand-edited config.** `{'allowed_origins': 'platform:group:12'}` lets origin `platform:group:1` through. AstrBot 4.16 `check_config_integrity` only replaces missing/None values and type-mismatched dicts, so a string survives a manual edit; the WebUI list editor writes a list. `user_whitelist` as a string fails closed (as the audit says). | reproduction |
| M-2 upstream strings reach chat unfiltered | **Confirmed as defence in depth.** A nickname `X\n【塔科夫 PVP 档案】Fake` renders as two message lines; `parse_entries` keeps an inner `\n`. All three real indexes (2026-10-13) hold no inner control/format characters and no non-ASCII after `strip()`, so it needs corrupted upstream data. Output is `plain_result` text. My rating: low. | reproduction, index scan |
| L-1 index rebuild reads the whole file | **Confirmed.** `rebuild()` does `read_bytes()`; decoded limit 384 MiB vs 67 MB real. | code |
| L-2 profile JSON parsed on the event loop | **Confirmed, small.** A 14 MiB JSON parses in 0.55 s here; real profiles are 3-35 KB. | measurement |
| L-3 `\d` accepts Unicode digits | **Confirmed, cosmetic.** `１２３` matches and `int()` gives 123; URL and cache key are normalised. | reproduction |
| L-4 `asyncio.TimeoutError` on Python <= 3.10 | **Not applicable.** AstrBot 4.16.0 declares `Requires-Python: >=3.12` (wheel METADATA); 3.11+ aliases it to `TimeoutError`. | wheel metadata |
| L-5 `yarl` imported but not declared | **Confirmed.** Always present through aiohttp today. | code |
| L-6 garbage gzip raises `zlib.error` | **Confirmed.** Body `\x1f\x8b` + junk with `Content-Encoding: gzip` escapes `transport.get`; chat shows `查询失败：error。`. | reproduction |
| Info-1 ETag echoed as `If-None-Match` | **Not exploitable.** aiohttp 3.14.5 refuses to send a header value with CR/LF (`ValueError: Forbidden control character…`); the refresh failure path would keep the old index. | reproduction |
| Info-4 `Infinity` in config | **Confirmed.** `int(inf)` raises `OverflowError`, not caught by `int_setting`; the plugin fails to load. Needs a hand-edited config. | reproduction |
| Info-5 logs | **Confirmed, and the audit understates it.** The log line has the queried target and an 8-hex-digit prefix of sha256(origin + sender). That prefix is not anonymous to anyone who can list candidate origins and senders (an admin knows the group members), so a log reader can link requester and target. AstrBot's own logs also record incoming commands with the sender. README's "不记录谁查询了谁" should say the plugin keeps no query records of its own and logs contain the command. | code |
| Info-2, 3, 6, 7, 8, 9, 10 | Agreed as described (9 is already in §5). Info-8 (HTTPS proxy support in aiohttp) not tested. | - |

**Found while verifying (not in the audit):**
- **X-1 SQLite URI built from an unescaped path (real bug).** `f'file:{path}?mode=ro'` breaks when the data
  directory path contains `#`, `?` or `%`. Reproduced: with `#` the URI is cut at the fragment, so `mode=ro` is lost
  and SQLite opens (and creates, empty) the truncated path, e.g. `/tmp/with` outside the data directory; the lookup
  then fails with `no such table`, `meta()` returns None, and every later query downloads the index again. `%41`
  fails with `unable to open database file`. Fix: `path.resolve().as_uri() + '?mode=ro'` (percent-encodes).
  Windows paths were not tested.
- **X-2 extreme `updated` timestamp.** `updated = 1e20` makes `datetime.fromtimestamp` raise `OSError`; the whole
  profile becomes `查询失败：OSError。` instead of rendering without the date. Needs bad upstream data.

**Proposed fixes, in my order of priority (each with a test that fails without it):**
1. X-1: build read-only URIs with `Path.as_uri()`.
2. M-1: accept `allowed_origins`/`user_whitelist` only as lists; a string counts as one item; any other type denies
   access and logs a warning.
3. M-2: drop Unicode `Cc`/`Cf` characters and collapse whitespace in nickname, side and index names.
4. L-6: catch `zlib.error` in `transport.get` and raise `QueryError('tarkov.dev 返回的数据无法解压…')`.
5. X-2 / Info-4: `when()` returns None for out-of-range timestamps (line omitted); `int_setting` also catches
   `OverflowError`.
6. L-3: `ACCOUNT_ID = [0-9]{1,12}`. L-5: add `yarl>=1.9,<2` to requirements. Info-5: README wording.
7. L-1: lower the index decoded limit to 192 MiB (about 3x the current size) rather than rewriting the parser to stream.
   L-2: parse the profile in `asyncio.to_thread`.
Not proposed: L-4 (not applicable), Info-1 (not exploitable), Info-2 (CI actions by tag, same as the spark plugin;
pin to SHAs if the maintainer wants), Info-3 (per-sender limit is enough for a group bot), Info-9 (documented).

## 11. Security audit fixes (maintainer: fix all, in the proposed order)

Method as before: tests first, each confirmed failing on the old code (19 failing assertions/sub-tests before any fix),
then the fix, then the full suite. 72 -> 83 tests.

| # | Item | Change | Tests |
|---|---|---|---|
| 1 | X-1 | `names.read_only(path)`: `sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)` for `meta()` and `query()`. | `test_data_directory_with_uri_characters` (`#`, `?mode=rw`, `%41`; also asserts no file appears outside the data dirs) |
| 2 | M-1 | `TarkovPlugin.config_list(key)`: str -> `[str]`; list/tuple/set -> list of `str(item)`; anything else -> None + warning, and `allowed()` denies. Used for `allowed_origins` and `user_whitelist`. | `test_access_list_types` (8 cases incl. the substring case and the warning) |
| 3 | M-2 | New `tarkov_core/text.py` `clean(value, limit)`: Cc/Zl/Zp -> space, Cf dropped, whitespace collapsed, stripped, then cut. Applied to profile `nickname` (32, empty -> `未知`), `side` (16) and index names (64; empty entries skipped as before). | `test_control_characters_are_removed`, `test_control_characters_in_names_are_removed` |
| 4 | L-6 | `transport.get`: `except zlib.error` -> `QueryError('tarkov.dev 返回的数据无法解压…')`. | `test_garbage_gzip_body` |
| 5 | X-2, Info-4 | `output.when()` returns None on `OverflowError/OSError/ValueError`; `render` omits "最近活跃" or falls back to the dateless source line. `int_setting` also catches `OverflowError` (`int(inf)`); NaN already gave `ValueError`. | `test_out_of_range_timestamps_are_left_out`, `test_infinite_setting_falls_back` |
| 6 | L-3, L-5, Info-5 | `ACCOUNT_ID = [0-9]{1,12}`; `requirements.txt` adds `yarl>=1.9,<2`; README/checklist say the plugin keeps no query records of its own, but AstrBot logs every message with sender id and outline (`astrbot/core/event_bus.py` `_print_event`, 4.16.0) and the plugin log has the queried target. | `test_fullwidth_digits_are_not_an_account_id`, `test_direct_imports_are_declared` |
| 7 | L-1, L-2 | `INDEX_LIMITS = (64 MiB, 192 MiB)` (real: 30 MB / 67 MB); the parser was not changed to stream. `fetch_profile` parses with `asyncio.to_thread(json.loads, …)`. | `test_index_decoded_limit`, `test_profile_is_parsed_off_the_event_loop` |

Mutation check (scratch copy, each fix reverted alone, full suite, 120 s limit): 13 mutations (URI escaping, string
origin, other-type origin, Cf removal, Cc replacement, index cleaning, profile cleaning, zlib catch, timestamp range,
OverflowError, ASCII digits, to_thread, index limit), all killed.

Not changed: L-4 (not applicable), Info-1, Info-2, Info-3, Info-8, Info-9 (§10). Still not tested on Windows paths.

## 12. Independent review after §11 (subagent, no shared context): verified, proposals only

The maintainer asked for an independent review once the audit fixes were in. A fresh subagent got only a clone of
`fc26b0a`, the maintainer's requirements and the documentation convention, and was told to reproduce every claim. Its
report (Chinese, 20 findings: 0 high, 1 medium, 8 low, 11 info) and its 14 reproduction tests are kept outside the
tree. I re-ran all 14 reproductions against `fc26b0a` (all reproduce) and checked the inferred premises myself. It
confirmed the §11 fixes as correct, with two gaps (F-03, F-09). No code changed; the maintainer decides.

| Id | Sev. | Finding | My verification |
|---|---|---|---|
| F-01 | medium | No back-off when the **first** index download fails (no db yet): `failed_at` is only set when an old db exists, so every name query (and the digits-to-name fallback) downloads the whole index again; after a 403/429 it hits the server on every query. | Reproduced (5 lookups, 5 downloads). |
| F-02 | low | Config save hot-reloads the plugin; the new `NameIndex.__init__` deletes the `.download` the old instance is still writing (old query fails with `FileNotFoundError`); both instances may download/build to the same paths. Windows: `unlink` of an open file may fail plugin load (not tested). | Reproduced. Premise confirmed in the 4.16.0 wheel: `post_plugin_configs` calls `plugin_manager.reload`; `_terminate_plugin` only awaits `terminate()`. |
| F-03 | low | `summarize()` still raises: a JSON integer beyond float range makes `math.isfinite` raise `OverflowError`; a list as skill `Id` raises `TypeError` (unhashable). Breaks §7; chat shows `查询失败：OverflowError。`. | Reproduced. |
| F-04 | low | Proxy enabled with an empty address connects directly, silently, against the docs' "never falls back to direct". | Reproduced. |
| F-05 | low | Help, refresh notice and not-found text say "每周" whatever `name_index_refresh_hours` is. | Reproduced. |
| F-06 | low | A failed `build()` leaves `names-*.sqlite3.tmp` (up to ~100 MB) until the next start. | Reproduced. |
| F-07 | low | A db whose `meta` reads but whose `names` table is damaged is never rebuilt before the next refresh; lookups fail with `OperationalError`. | Consequence reproduced (damage simulated with `DROP TABLE`). |
| F-08 | low | §4 test counts are the pre-§11 ones (72). | Confirmed. |
| F-09 | low | README says the only dependency is aiohttp; `requirements.txt` also has yarl. | Confirmed. |
| F-10 | info | `URL('http://h').port` is 80, never None; the `'http://h'` test case passes only because DNS for `h` fails. | Reproduced. |
| F-11 | info | `''`, `['']` or a trailing space in `allowed_origins` denies everyone. | Reproduced. |
| F-12 | info | Index 404 reaches chat as `查询失败：not found`. | Reproduced. |
| F-13 | info | Over 6 case-insensitive matches, the exact-case player can be cut by `LIMIT`; 6 found, 5 shown, no "more". | Reproduced (constructed lookup result). |
| F-14 | info | The index is rebuilt daily, so with a weekly check the 304 path rarely helps; docs overstate the saving. | Plausible, not observed: this container's clock is still 2026-10-13, ETags equal the ones recorded today. |
| F-15 | info | `[A-Za-z0-9_-]{3,15}` excludes auto-generated `TarkovCitizen<digits>` names. | Confirmed and larger than reported: the PvP index has 778 names over 15 characters (775 `TarkovCitizen…`), all `[A-Za-z0-9_-]`, at most 21 characters (2 junk entries over 64 are skipped). |
| F-16 | info | "游戏时长" is account-wide, not per mode. | Confirmed: `pvp-season/712.json` has 7,141 h in both pmcStats and scavStats with 58 + 32 raids. |
| F-17 | info | The profile-timeout hint says to raise the download timeout, but profile fetches are capped at 60 s. | Confirmed by code. |
| F-18 | info | Windows UNC data dir: `as_uri()` gives `file://server/...`, which SQLite rejects. | Not tested on Windows; noted. |
| F-19 | info | Cleaning stops line injection, not same-line text or `@everyone` in a tampered nickname. | Agreed; needs tampered upstream data. |
| F-20 | info | Refresh notice always says "约 30 MB / 几十秒"; players named `help`/`pvp`/`season`… need an explicit mode; a mistyped PvP id can trigger the first 30 MB download. | Confirmed by code. |

**Proposed fixes, in my order of priority:**
1. F-01: record `failed_at` also without a db; inside `RETRY_SECONDS` raise a clear `QueryError` ("名单暂时无法下载… N 分钟后再试，可以先用账号 ID 查询") instead of downloading again.
2. F-03: `number()` catches `OverflowError`; `skills()` requires a `str` id; one test feeding odd shapes into every field.
3. F-04: proxy enabled with an empty address -> `QueryError` (fail closed).
4. F-02 + F-06: per-refresh unique temp names (pid + random); `__init__` removes only leftovers older than an hour and ignores `OSError`; `build()` deletes its temp file on failure.
5. F-07: on `sqlite3.DatabaseError` in a lookup, delete the db and rebuild once.
6. F-15: nickname length 3-24. F-16: label "账号总游戏时长".
7. Chat texts: F-05 (interval from the setting), F-17 (timeout hint only for the index), F-12 (Chinese text for an index 404), F-11 (strip and drop empty list items), F-13 ("等，共 N 个以上" when cut), F-20 (size note only when no db exists yet).
8. Docs: F-08, F-09, F-14 wording, F-18 in §5, F-20 note on names equal to mode words; F-10: validate with `explicit_port` (the hint already asks for `主机:端口`) and fix the test.
Not proposed: F-19 (cleaning is enough while the game restricts the character set; a display whitelist would hide legitimate names if that changes).

## 13. Independent review fixes (maintainer: fix all, in the proposed order)

Method as before: tests first; every new test failed on the code at `672594b` (the odd-shape test alone failed in
106 combinations), then the fix, then the full suite. 83 -> 96 tests.

| # | Review id | Change | Tests |
|---|---|---|---|
| 1 | F-01 | `NameIndex.failed_at[mode] = (time, reason)` on any failed refresh, with or without an older db. `needs_refresh()` is False while `waiting()` > 0. Without a db, `lookup()` then raises `QueryError('玩家名单暂时无法下载（<reason>），请约 N 分钟后再试；…')` instead of downloading again. The first failure still shows its own error. | `test_failure_without_any_index_is_reported_then_backed_off` |
| 2 | F-03 | `number()` returns None when `math.isfinite` raises `OverflowError`; `skills()` only looks up `str` ids. | `test_odd_shapes_anywhere_never_raise` (every node of the fixture x 12 odd values, rendered), `test_huge_integers_count_as_missing` |
| 3 | F-04 | `TarkovPlugin.proxy()` raises `QueryError` when the proxy is enabled and the address is empty or blank (fail closed). | `test_enabled_proxy_without_address_fails_closed` |
| 4 | F-02, F-06 | Temporary files carry `pid-random` (`unique_suffix()`): `names-<mode>.<sfx>.download`, `names-<mode>.sqlite3.<sfx>.tmp`. `__init__` removes leftovers only when older than `LEFTOVER_SECONDS` (1 h) and ignores `OSError`. `build()` deletes its temp file on any exception. Two instances may still both download (per-instance locks); each builds its own file and `os.replace` is atomic. | `test_reload_during_a_download_does_not_break_it`, `test_two_instances_never_share_a_download_file`, `test_old_leftovers_are_removed_at_start`, `test_failed_build_removes_its_temp_file` |
| 5 | F-07 | `lookup()` catches `sqlite3.DatabaseError` from the query, deletes the db, refreshes once and queries again. | `test_damaged_names_table_is_rebuilt` |
| 6 | F-15, F-16 | `NAME` 3-24; the input message says 3～24. Output label "账号总游戏时长". | `test_name_rule`, `test_long_generated_names_are_accepted`, `test_full_profile` |
| 7 | F-05, F-17, F-12, F-11, F-13, F-20 | `every(seconds)` renders the interval (每周 / 每 2 周 / 每天 / 每 3 天 / 每 N 小时) for help, notice and not-found text. Index downloads raise `IndexTimeout`; only that timeout suggests the timeout setting. An index 404 becomes `tarkov.dev 上没有 <模式> 模式的玩家名单`; `NotFound`'s default text is Chinese. `config_list` strips items and drops empty ones (`''`/`['']` mean no restriction). `lookup()` returns `(rows, total)` with exact-case rows first (separate query, so the cap cannot cut them); the candidate list adds `等，共 N 个` above 5. The notice says "正在下载 … 第一次…" (size only for PvP) without a db and "正在检查 … 的更新" with one. | `test_texts_follow_the_refresh_interval`, `test_index_404_and_timeouts_in_chat`, `test_missing_profile`, `test_empty_list_items_are_ignored`, `test_exact_case_first_and_total`, `test_long_candidate_lists_say_there_are_more`, `test_refresh_notice_mentions_size_only_for_a_first_pvp_download` |
| 8 | F-08, F-09, F-14, F-18, F-20, F-10 | §4 counts and coverage; README dependency line (aiohttp and yarl); README/schema/CHANGELOG/checklist no longer promise a cheap unchanged check (the index is rebuilt daily, so a weekly check usually downloads it again); §5 notes UNC paths; README notes that players named like a mode word need the mode written; `validate_proxy` requires `explicit_port` (the hint already asks for `主机:端口`), and the test asserts the validation message instead of relying on a DNS failure. | `test_proxy_must_be_valid` |

Mutation check (scratch copy, each change reverted alone, full suite, 180 s limit): 21 mutations (back-off record
and check, overflow, skill id type, empty proxy, leftover age, unique download name, temp cleanup, damaged-db rebuild,
exact-first order, name length, play-time label, interval text, empty rows, index 404 text, NotFound default text,
"等" line, index timeout class and hint, notice wording, explicit port). The unique-download-name mutation survived
the first run (the reload test passed through the leftover-age rule alone); `test_two_instances_never_share_a_download_file`
was added and kills it. All 21 killed.

Not changed: F-19 (cleaning is enough while the game restricts nicknames to `[A-Za-z0-9_-]`).

## 14. Image output: design proposal (not implemented)

The maintainer asked for a design for rendering the reply as an image (planned since v0.1: "先做纯文字，再考虑图片").
Evidence read from the AstrBot 4.16.0 wheel; a throw-away Pillow mock-up was rendered from a real PvE profile with
the name replaced (760x1038 PNG, 106 KB, not in the tree).

**What AstrBot offers (4.16.0):**
- `Star.text_to_image(text)` -> `html_renderer.render_t2i(...)`, `use_network=True` always: POSTs the text to the
  configured `t2i_endpoint`, by default the public `https://t2i.soulter.top/text2img` (endpoint list fetched from
  `api.soulter.top`), and falls back to `LocalRenderStrategy` (Pillow Markdown renderer; CJK font from
  `data/font.ttf` or a short list of system fonts, else Pillow's default font without CJK glyphs).
- `Star.html_render(tmpl, data)`: Jinja2 HTML rendered by the same remote service, **no local fallback**.
- `pillow>=11.2.1` is an AstrBot dependency, so the plugin can draw locally without a new requirement.

**Options:**

| | A. `text_to_image` (Markdown) | B. `html_render` (HTML template) | C. Pillow in the plugin | D. Local headless browser |
|---|---|---|---|---|
| Code | ~10 lines | template + ~30 lines | ~300 lines layout | heavy |
| Look | generic Markdown page | best, easy to restyle | good, fully controlled | best |
| Network | profile text sent to a third-party renderer by default | same, no fallback | none beyond players.tarkov.dev | none |
| Breaks the "only players.tarkov.dev" promise | yes (unless self-hosted endpoint) | yes | no | no |
| Testable offline | no | no | yes (deterministic) | partly |
| Fonts | renderer's / system | renderer's | must ship a CJK font | system |

**Recommended: C**, as a second renderer next to the text one:
1. `output.py` builds a small card model from the summary (header, 4 key tiles, PMC/Scav rows, skills with bars,
   footer, notes); `render()` (text) and a new `tarkov_core/image.py` `render_png()` both consume it, so the two
   outputs cannot drift.
2. Font: ship a subset of an OFL CJK font (Noto Sans SC / Source Han Sans SC) holding only the glyphs the card can
   need: fixed labels, the 49 skill names in `static.json`, ASCII. Nicknames are `[A-Za-z0-9_-]` in practice; any
   other character falls back to `?`. Subset built by a `tools/` script; a test checks every label and skill
   character is in the font's cmap. Size to be measured (expected ~100 KB). OFL notice in THIRD_PARTY.md.
3. Config `output_mode`: `image` / `text` (default to be decided). Rendering runs in `asyncio.to_thread`; any
   failure logs the type and sends the text reply instead. Candidate lists, notices and errors stay text.
4. Sending: PNG written under the data dir and sent as an `Image` component (`Image.fromFileSystem`). When AstrBot
   has finished with the file must be checked in its send path before choosing the clean-up rule (open question).
5. Tests: renders a PNG of sane size for the fixture and for the odd-shape summaries of §13; skill-count setting;
   fallback to text when rendering raises; font coverage.

**Open questions for the maintainer:** option (C recommended); default output (image with text fallback, or text);
skills shown in the image (all trained, or capped); whether to add tarkov.dev images (level badge, skill icons),
which would add `assets.tarkov.dev` as a second host (not recommended for the first version).

### 14.1 Maintainer decisions and style previews

Decided: option C (Pillow in the plugin); default output is the image; the config offers three outputs: plain
text, image, Markdown text. The style is still open.

Three previews were rendered from the same summary (throw-away script, not in the tree; fonts used, all SIL OFL from
github.com/google/fonts: Noto Sans SC, Oswald, Black Ops One, Share Tech Mono). Game references are drawn, not copied:
no game assets, logos or the game's own font are used.

| # | Style | Elements | PNG |
|---|---|---|---|
| 1 | 角色面板 (character screen) | stash-grid background, top tab bar, drawn dog tag with ball chain, two-column stat list, square skill tiles with level numbers (gold for elite) | 820x1108, 108 KB |
| 2 | 撤离结算 (raid summary) | hazard stripes, stencil headings (SURVIVED / KILLED IN ACTION), survival ring, exit-status bar, dotted leader rows, 10-pip skill bars | 820x1240, 660 KB |
| 3 | 情报档案 (intel dossier) | paper texture and vignette, file tab, photo slot with clip, typewriter digits, rotated "已核实" stamp ("存档快照 / SNAPSHOT" since §17), form fields, pip checklist, decorative redaction bar | 820x1226, 866 KB |

Sizes 2 and 3 come from the per-pixel noise texture, which PNG compresses badly; the implementation would use a
lighter texture or JPEG. Implementation (text / image / Markdown output setting, card model, font subset) waits for
the style choice.

### 14.2 Combined preview (maintainer: dog tag of 1, overall design of 3, exit-status bar of 2)

Rendered with the same throw-away script (not in the tree): the dossier of style 3; the photo slot replaced by the
dog tag of style 1, tilted 4 degrees, with a paper-brown shadow, the hole showing the paper and the ball chain running
up to a paper clip; the identity fields on the right reduced to what the tag does not show (mode, account play time,
achievements, last active, profile updated); the PMC/Scav exit-status bars of style 2 under the combat table, in
muted paper inks with hatching, a 0-100 % scale and a legend (PMC/Scav counts). 820x1364 PNG, 945 KB because of the
noise texture (to be reduced in the implementation: lighter texture and/or JPEG). Awaiting the maintainer's go-ahead
to implement it together with the three output settings.

## 15. Image and Markdown output (0.2.0)

Maintainer decisions (§14.1-§14.2): option C, image by default, three outputs (text / image / Markdown), the
combined style (dossier of 3 + dog tag of 1 + exit-status bars of 2). Version 0.2.0. 96 -> 113 tests.

**Code**
- `card.py` (new): the content model; `when`, `ratio`, `STALE_DAYS`, `stale_note` moved here from `output.py`.
- `output.py`: `render_text(card)` reproduces the 0.1.0 text exactly (40 old/new comparisons over 5 summaries x
  4 skill limits x 2 times, plus `test_text_is_unchanged_by_the_card_model`); `render_markdown(card)` added.
- `image.py` (new): ported from the preview script. Section numbers adapt when skills are hidden; notes (stale, rename,
  image-fallback) are wrapped into the 备注 section; table cells shrink their font to fit (`fit()`); bars for an
  empty side say 暂无记录; `MODE_EN` maps labels for the Latin-only fonts. Measured here: 0.2-0.3 s per image, about
  300-400 KB.
- `main.py`: `output_mode` setting (schema default `image` = code fallback), `reply()`; the image goes out as
  `Image.fromBytes(data)` (AstrBot 4.16 `components.Image.fromBytes` -> base64), so no file is written and there is
  nothing to clean up. Non-profile replies stay text.
- `tools/build_fonts.py` (new, dev only, needs fonttools): downloads the three fonts and OFL texts from google/fonts,
  subsets Noto Sans SC to printable ASCII + every non-ASCII character in `main.py`, `tarkov_core/*.py` and the skill
  names (character list 1158 bytes, 183 KB, variable `wght` axis kept, hinting removed), writes the character list, copies
  Oswald and Share Tech Mono unmodified. Source sha256 values are printed and recorded in THIRD_PARTY.md.
- `requirements.txt`: `pillow>=11.2.1` (AstrBot 4.16 requires the same).

**Mutation check** (11, full suite): unknown mode -> image, image fallback, image off the loop, Markdown mode,
Markdown escaping, `safe()`, skills hidden at 0, skills capped, `fit()`, seeded grain, empty-bar text. The last three
survived the first run; `test_long_text_is_shrunk_to_fit`, `test_same_picture_in_another_process` and
`test_empty_sides_say_so` were added and kill them.

**Not verified:** how chat platforms display a ~400 KB JPEG (QQ/NapCat, Telegram, Discord); Markdown rendering per
platform; Pillow versions other than 12.3.0 (AstrBot allows >= 11.2.1; `set_variation_by_axes` and variable fonts
need FreeType with variation support, present in Pillow wheels).

### 15.1 Exit-status wording (maintainer)

The game's Chinese wording and order: 撤离 (survived) / 匆匆逃离 (runner) / 转移 (transit) / KIA (killed) /
行动中失踪 (MIA). `card.SURVIVED` and `card.EXITS` hold them; text, Markdown (column and status line) and image (table
header, bar order, legend) all read from there. 存活率 (survived ÷ raids) keeps its name. The font subset was rebuilt
for the new characters (character list 1167 bytes, 185 KB). Tests: `test_exit_statuses_use_the_game_wording`,
`test_exit_legend_uses_the_game_wording`; 113 -> 115.

## 16. Bind-and-query redesign (0.3.0)

### 16.1 Evidence: why veteran players had no profile

The maintainer asked for PVE profiles of two long-time players (real players; names and account ids are deliberately not
recorded in this repository, called A and B here). Both names were absent from all three 2026-10-13 indexes; A's account
id (taken from a dog tag in the maintainer's profile) returned 404 in all three modes. The maintainer then searched A on
tarkov.dev in PVE (no visible captcha): `pve/<A's id>.json` appeared with
`Last-Modified` 17:02:58 UTC and `updated` 17:02:56.894 UTC, a minute after the search; `profile/` and `pvp-season/` stayed
404. B's PVE profile likewise appeared after the maintainer's search (updated 01:05 Beijing time).
Conclusions: players.tarkov.dev holds only profiles fetched by tarkov.dev's live search (per mode, at search time), not
every player; play time is irrelevant. The live search needs a Turnstile token; tarkov.dev uses
`appearance: "interaction-only"`, so browsers pass it silently, which is why no captcha was seen, while a request
without a token gets 401 (probed). The website's search page takes only `?gameMode=`; `/players/<mode>/<name>` was
reported by the maintainer not to work, so a link cannot pre-fill a name. Cloudflare also caches 404 responses for
minutes (`age: 550` seen on a 404).

Not verified: that opening `https://tarkov.dev/players/<mode>/<id>` (without searching) rewrites the snapshot. The page
code calls the same live `/account/<aid>` lookup, so it is likely; the reminder text names searching first and the page
second for that reason.

### 16.2 Maintainer decisions

Implement B (query by player-page link) and D (remember name -> id); remind users that data must be refreshed by hand;
the plugin returns to "bind identity + query", with names supplied by the users. Then (AskUserQuestion): the downloaded
index stays as an option, **off by default**; a binding holds across all chats of one platform; the name record is
shared by everyone; `/tarkov` without arguments queries the caller's own binding (help moves to `/tarkov 帮助`).

### 16.3 Changes

| Piece | Change |
|---|---|
| `modes.py` | `MODES` gains the website mode segment (`regular`/`pve`/`pvp-season`); `parse_player_url()` accepts `tarkov.dev/players/<mode>/<id>` and `/player/<id>` (scheme and `www.` optional, case-insensitive, full match only, so no other host or query string); arena -> `(None, id)`; `page_url()`, `search_url()`. |
| `accounts.py` (new) | See §3. |
| `main.py` | `handle()` parses `帮助/help`, `绑定/bind`, `解绑/unbind`, an optional mode, then nothing (own binding), a link, an id or a name. A link's mode wins; a different explicit mode is refused; arena is refused. `bind()` resolves link/id/recorded name, fetches the profile to confirm it exists, stores the binding. `query()`: own binding, link/id, then `by_name()`: the shared record first, the downloaded index only when `name_index_enabled`; not found -> step-by-step guidance (search page link for that mode, example link command). `profile()` records every fetched nickname (`sqlite3.Error` is logged, never fails the query). `reply()` passes the player's page to the card and, for images, appends `Plain('刷新数据：…')` after the image. |
| Card / outputs | `build_card(..., refresh_url=)`; text, Markdown (unescaped link built by the plugin) and image notes end with the "数据不会自动更新" reminder. |
| Config | `name_index_enabled` (bool, default false, schema = code fallback); `name_index_refresh_hours` and the timeout hint now say they apply only with the index. |
| Fonts | Subset rebuilt for the new text (character list 1242 bytes, 196 KB). |

Live run (real network, SDK stubbed, data dir fresh, not uploaded): unbound `/tarkov` -> guidance; bind by B's
link; `/tarkov pve` -> image + link line; `/tarkov` (PVP, no profile) -> guidance with the PVP search link; A by
name -> unknown + steps; by link -> image; then A's name in lower case -> image (learned); unbind. The data dir held only
`accounts.sqlite3` (no index download).

Mutation check (12, full suite): binding key per platform, name learning, index off by default, link-mode conflict,
arena refusal, refresh note in the card, link line after the image, exact-case-first order, record bound, unbind reply,
record failure tolerated, link host check. All killed. 115 -> 131 tests.

### 16.4 Multi-mode (maintainer: "不要仅局限于 PVE 模式，本插件需要支持多模式数据")

- `TarkovPlugin.profiles(aid, first)` fetches all three modes together (`asyncio.gather`); "no profile" is skipped; any
  other error fails only for `first`, the mode the reply is about (logged and skipped for the others).
- `show()`: a mode chosen by the user (`explicit`: a mode word or a link) is kept; without one, the default mode is shown
  when it has a profile, else the first other mode that has one, with a note. The reply names the other modes with a
  profile (with a ready command). Nothing shown -> a message naming the modes that do have a profile, or "none of the
  three", with the matching search hint. The rename note moved into `show()` (it needs the shown profile).
- `show()` returns `(reply, missing_text)`; a first draft kept the text on `self`, which concurrent queries of
  different users would have overwritten. Caught in self-review before any commit.
- Binding: a profile in any mode confirms the account; the reply lists the modes with a profile.
- Cost: a query now makes up to three profile requests. "No profile" is cached in memory for `NOT_FOUND_SECONDS` = 60
  (a found profile for `profile_cache_minutes`), so repeated queries do not ask again, and a player searched on the
  website a moment ago appears within a minute (Cloudflare's own caching aside).
- The optional downloaded index stays per mode: a name lookup uses the chosen (or default) mode's index.
- Tests: `test_without_a_mode_every_mode_is_looked_at`, `test_a_chosen_mode_is_kept`, `test_names_work_in_every_mode`,
  `test_other_modes_failing_do_not_fail_the_query`, `test_bind_accepts_a_profile_in_any_mode`,
  `test_missing_profiles_are_cached_briefly`; existing expectations updated (fetch lists now hold all modes).
  131 -> 137. Mutation check (9): explicit mode kept, fallback, explicit flag, main-mode error, "also has" note,
  bind in any mode, not-found TTL, not-found cached, rename note. All killed.
- Fonts: subset rebuilt for the new messages (character list 1248 bytes, 196 KB).
- Live run after the push: `/tarkov pvp <A's id>` (PVE profile only) answered "no profile, unknown name": an id of 5+
  digits is also a valid nickname, so the name lookup ran and its "unknown" text replaced the id's. Now, when no name
  matches, the id's own answer is given ("PVP 模式下还没有…但 PVE 模式（/tarkov pve <id>）下有档案").
  `test_digits_with_a_profile_in_another_mode`; 137 -> 138; reverting the fix fails 2 tests.

## 17. Second independent review (after §16.4) and fixes

A general-purpose subagent with no shared context reviewed a detached copy at `84e2eaf` (hash after the §18 history rewrite) (report in Chinese, repro tests
for 12 of 13 findings, no network, no change to the real repository). Every finding was checked against the code and
held; the maintainer had authorised fixing review findings without asking again. All 13 fixed:

| ID | Sev. | Finding | Fix | Test |
|---|---|---|---|---|
| R-01 | med | `profiles()` logged and dropped a failed non-first mode, so a 429 on the only mode with a profile read as "PVP、PVE、赛季都没有", and binding was refused. | `profiles()` returns `(found, failed)` and never raises. `show()`: a failed mode the reply is about, or nothing found with any failure, raises the error (chat shows "查询失败：…"); a failed default mode without a chosen mode falls back with "PVP 模式这次没有查到（…）"; other failures are named in the notes / missing text. `bind()` raises when nothing was found and something failed. `failure_text()`. | `test_a_failed_mode_is_never_reported_as_no_profile`, `test_a_failed_default_mode_falls_back_with_a_note`, updated `test_other_modes_failing_do_not_fail_the_query` |
| R-02 | med | Found profiles stayed cached for `profile_cache_minutes`, so "refresh on the website, ask again in a few minutes" showed the old snapshot. | Summaries carry `fetched` (epoch). A reply from a cached summary at least `CACHE_NOTE_SECONDS` (60) old says how old and that a player-page link skips the cache. Link queries and binding pass `fresh=True` (skip the cache read). | `test_cached_profiles_say_how_old_they_are_and_links_skip_the_cache` |
| R-09 | med | Negative counters accepted; `exit_bars` hatch loop ran `w/9` times with `w` ~ 10^14 for offsetting counters (10^6 took 63 s; the to_thread worker cannot be cancelled, and the sender stays in `active`). | `counter()` clamps to `max(0, …)`; `exit_bars` clamps `w` to `[0, bw]`. | `test_negative_counters_read_as_zero`, `test_offsetting_counters_draw_quickly` |
| R-10 | med | Skills not de-duplicated by `Id`; elites uncapped; 3,700 repeats gave an 18 MB JPEG / 770 MB RSS, 4,000 broke JPEG save. | `skills()` keeps one entry per id (highest progress), so at most the static table size (49). | `test_repeated_skills_count_once` |
| R-03 | low | With the index on, an account id tried as a name could start a 30 MB index download, and an index error replaced the id's answer. | The digit fallback (`by_name(..., id_missing=…)`) uses only the shared record. | `test_an_account_id_never_downloads_the_index`; `test_all_digit_name_falls_back_to_the_record` (was `…_to_the_index`) |
| R-04 | low | With the index on and no mode, only the default mode's index is searched but the reply said "the index". | Reply names the mode's index; without an explicit mode it adds "其他模式的名单要写上模式查询". | `test_index_name_miss_says_which_mode_was_searched` |
| R-05 | low | Bind reply had no manual-refresh reminder; fallback note stated "no profile" as a fact about the account. | Bind reply adds the reminder and which modes appear only after a search in that mode; the fallback note says tarkov.dev has no profile in that mode yet, with the mode's search link. | `test_bind_reply_reminds_to_refresh`, wording in existing tests |
| R-06 | low | Displayed id came from the document's `aid`. | `reply()` sets `card['aid']` to the requested id, logs when they differ. | `test_the_shown_account_id_is_the_one_asked_for` |
| R-07 | low | README/checklist said a profile waits at most 60 s; it is `min(60, download_timeout_seconds)`. | Docs corrected (schema hint was already right). | docs |
| R-08 | low | Identical concurrent queries not merged; no global cap (10 users -> 30 requests). | `self.pending` `{(mode, aid): Task}` shared through `asyncio.shield` (one waiter cancelling does not cancel the others; the done callback pops the entry and retrieves the exception); `FETCHES_AT_ONCE = 6` semaphore around `transport.fetch_profile`; `terminate()` cancels pending tasks. | `test_one_request_per_profile_for_queries_at_the_same_time`, `test_requests_at_once_are_capped`, `test_one_user_giving_up_does_not_cancel_the_shared_request` |
| R-11 | low | Unsalted `sha256(platform\0sender)`: a QQ number came back in 0.6 s. | `load_secret(root/'binding.key')` (32 random bytes, written via a temp file with mode 0600, regenerated if shorter than 32 bytes); `owner_key(secret, platform, sender)` = HMAC-SHA256; the per-session lock key / log trace uses the same secret. 0.3.0 is untagged and unreleased, so no migration of old keys: bindings made while testing 0.3.0 must be redone (CHANGELOG says so). Docs state the limit: whoever has the key file as well can still enumerate. | `test_owner_key_is_per_platform_and_keyed`, `test_secret_is_created_once_and_private` |
| R-12 | low | checklist "how to verify" and "data kept" described 0.2. | Rewritten for 0.3.0 (unbound reply, bind, own query, unknown name steps; `accounts.sqlite3` and `binding.key` always exist, index files only with the option). | docs |
| R-13 | low | "已核实 / VERIFIED" stamp contradicts "non-live snapshot". | "存档快照 / SNAPSHOT". Fonts rebuilt. | `test_stamp_says_snapshot` |

Mutation check (`scratchpad/mut3.py`, scratch copy, one change at a time, full suite, 120 s limit): 23 mutations, one
or more per fix (counter clamp, bar clamp, dedupe, stamp, keyed hash, key file mode, failures kept apart, raise on
failure, fallback failure note, shown-reply failure note, missing-text failure note, bind raise, cache-age note, link
skips cache, single flight, semaphore, shield, requested id, no index for ids, index label, other-mode hint, bind
reminder, bind "other modes" line). All killed; removing the bar clamp makes the suite run until the time limit (the
original hang).

Found while fixing: the "N characters" figures for the font subset in §15, §16.3 and §16.4 were the byte size of
`NotoSansSC-Subset.chars.txt` (UTF-8; `wc -m` under the C locale counts bytes). Reworded as "character list N bytes".
The subset now holds 484 characters (character list 1263 bytes, font 197,760 bytes). Tests 138 -> 153.

## 18. Real player data removed from the tree (maintainer: "你怎么在插件说明文档里写了真实用户名")

- README command examples, CHANGELOG, handoff §16 and tests used the real nicknames and account ids of the two players
  probed in §16.1, and the maintainer's own account id. Replaced: nicknames -> `Player_One` / `Player_Two`, ids ->
  `7654321` / `2345678` / `3456789`; handoff narrative -> "player A / B" with no name or id.
- `tests/fixtures.py` was labelled "no real player's data", but every number in it (experience, all 18 counters, play
  time, `updated`, skill progress and access times, achievement times) equalled the maintainer's PVE profile
  (checked against the local copy: 18/18 counters equal). The README text example and `docs/preview.jpg` were drawn
  from the same numbers. Replaced with round invented values (1000 raids, 5000 kills, experience 12,000,000 -> level
  53, `updated` 1791000000000); expectations in test_profile/test_output/test_image/test_plugin updated.
- `tools/build_preview.py` (new) builds the README text example and `docs/preview.jpg` from the fixture plus invented
  skills and achievements, so the documentation never needs real data again.
- §2.4: exact times of the maintainer's page view and PvP snapshot date generalised.
- History rewritten at the maintainer's request ("改写历史并强制推送这两个分支"): `git filter-repo --file-info-callback`
  on a fresh clone applied the same replacements to every `.md`/`.py` blob of all 17 commits (names, ids, the copied
  numbers and the text derived from them, the two exact times in §2.4) and replaced every `docs/preview.jpg` with the
  invented one. Checks before the force push: the final tree equals the pre-rewrite tree; the full test suite passes
  at every one of the 17 rewritten commits; a scan of every blob and commit message in the history finds none of the
  real names, ids or numbers. Both branches were force-pushed; every commit hash changed (hashes quoted in this file
  are the new ones). A bundle of the old history was kept only in the session scratchpad, outside the repository.
  Copies cloned or forked before the rewrite, and GitHub's cache of the old commits, are outside this repository's
  control.

## 19. After the maintainer's closed test (0.4.0)

Feedback: the maintainer wants the plugin name, the GitHub repository and the open-source licence in small print at
the foot of the generated content; testers liked the image but asked for Tarkov Arena.

### 19.1 Image footer

- `tarkov_core/__init__.py`: `NAME`, `REPO`, `LICENSE` beside `VERSION`. `image.render_image` draws two 13 px lines
  under the data-source line: `astrbot_tarkov_datasearch v<VERSION> · github.com/EvergardenRoxy/astrbot_tarkov_datasearch`
  and `开源插件，GPL-3.0-or-later 许可；与 tarkov.dev 和 Battlestate Games 无关` (README already states the
  non-affiliation). Canvas budget +40 px, crop `y+84`. Text and Markdown replies unchanged (the request named the
  image; a footer in every chat text reply would be noise) -- maintainer may extend it.
- Tests: `test_footer_names_the_plugin_source_and_licence` (exact lines, drawn last, inside the picture, also with long
  notes), `test_footer_facts_match_the_repository` (`metadata.yaml` `repo` == `REPO`, LICENSE is the GPL, README names
  the licence). 153 -> 155. Font subset rebuilt (character list 1272 bytes, font 199,048 bytes). `docs/preview.jpg`
  regenerated with `tools/build_preview.py`. Version left at 0.3.0 with an "未发布" CHANGELOG section at first, so the
  `v0.3.0` branch the maintainer tested stayed as tested; released as 0.4.0 with the Arena work (§19.7).

### 19.2 Arena: feasibility (research only, not implemented)

Sources: tarkov-dev `src/pages/player/index.jsx` and `src/modules/player-stats.mjs` (main, read 2026-10-13); probes of
players.tarkov.dev with the plugin's own User-Agent (static files only; no player.tarkov.dev, no Turnstile).

- **Same mechanism as the other modes.** The website loads every mode, Arena included, from the live API
  `player.tarkov.dev/account/<aid>?gameMode=arena` behind Turnstile, which also writes a static snapshot. Probed:
  `players.tarkov.dev/arena/index.json` 200 (53,944 names, ~606 KB gzip); `arena/<aid>.json` 200 for 9 of 9 ids taken
  from that index; `arena/1234567.json` 404. So an Arena profile exists only after someone opened the player in Arena
  mode on tarkov.dev, exactly like the other modes; website link `/players/arena/<aid>` (currently refused by
  `parse_player_url` callers).
- **Same account id and nickname.** Of the arena index ids also present in the PvP (50,355), PVE (29,413) and season
  (16,724) indexes, 100% carry the same name in both. Bindings and the learned name record need no change.
- **Different document.** Top keys `aid, info, presetCustomization, profileCustomization, achievements, stat,
  equipment, updated`; no `pmcStats`/`scavStats`, `skills` absent. `info` has `nickname, side, experience,
  battlePassesLevels, memberCategory, selectedMemberCategory, registrationDate`. `stat` = `totalInGameTime` +
  `arenaOverAllCounters`: a dict of modes, each `{"Counters": {name: int}}` (plain dict, not the Key/Value list).
  Mode keys seen: `UnrankedOverall`, `UnrankedTeamFight`, `UnrankedLastHero`, `UnrankedCheckPoint`,
  `UnrankedBlastGang`, `UnrankedShootOutDuo`, and on one profile ranked-looking `Overall`, `TeamFight`, `ShootOutDuo`.
  Overall counters: `GamesCount, Kills, Deaths, KillsWithoutDeaths, MaxKillsWithoutDeaths, LongestWinStreak,
  LoseStreak, LongestLoseStreak, BestArp`. Per-mode counters add e.g. `ArenaWins` (`ArenaLoses` in ranked),
  `Assists, Headshots, DoubleKills, KnifeKills, DamageDealt, RoundMvpCount, MatchMvpCount, MoneyCollected`.
- **What the website shows**: an overall table (killstreak, max killstreak, max win streak, best ARP, loss streak,
  max loss streak) and a per-mode table (kills, deaths, K/D, max killstreak, round MVP, match MVP, max win streak) for
  Team Fight, Last Hero, Checkpoint, Blast Gang. Level is computed from `info.experience` with the main game's
  `playerLevels` table, which may not be the level the Arena client shows.
- **Work if approved**: `MODES['arena'] = ('arena', '竞技场', 'arena')` but kept out of the automatic three-mode lookup;
  `profile.summarize_arena()` with the same hostile-input rules (non-negative ints, unknown modes ignored, bounded);
  an arena card (text, Markdown, image without exit bars and skills); link parsing accepts arena; tests and fixture
  with invented values; optional index unchanged (arena index exists, 606 KB). Open decisions are listed in
  checklist.md (scope, official Chinese mode names, ranked vs unranked, image design, level).

### 19.3 Arena implemented (maintainer's decisions)

Decisions: query with `/tarkov 竞技场 <...>` and reserve a setting to query Arena by default; mode names stay English
("官方和游戏里也是英文"); ranked and unranked kept apart; three new picture proposals; no level.

- `modes.py`: `MODES['arena'] = ('arena', '竞技场', 'arena')`, aliases `arena`/`竞技场`, `GAME_MODES = ('pvp', 'pve',
  'season')`. Arena links now parse to `'arena'`; an unknown link mode is still `None` and refused.
- `main.py`: `looked_at(mode)`: `['arena']` for Arena (fetched alone); otherwise the asked/default mode first, then the
  other game modes, plus Arena last when `arena_by_default` (new bool setting, default false, schema = fallback).
  `profiles()` uses it; `none_found()` names exactly the modes looked at ("竞技场档案" when only Arena). `profile()`
  calls `summarize_arena` for Arena. `reply()` hands Arena to `arena_reply()`: text, Markdown, and for the image setting
  text with "（竞技场的图片样式还在设计中，暂以文字回复）" until a style is chosen. The displayed id is the requested one.
  `default_mode()` accepts only the game modes (Arena is not a default-mode option; the schema test checks
  `GAME_MODES`). Help text names Arena and how it is queried.
- `arena.py` (new): `summarize_arena()` keeps 12 counters per mode as non-negative ints (`number()` rules: bools,
  strings, non-finite and huge values dropped), mode keys `[A-Za-z]{1,40}`, at most `MAX_MODES` = 12 per section,
  `Unranked*` -> 非排位, others -> 排位, `Overall` is the section total; labels from tarkov.dev's page (Team Fight,
  Last Hero, Checkpoint, Blast Gang), other keys as they are (ShootOutDuo); fixed order. Card, text and Markdown
  renderers; `output.markdown_refresh()` now shared by both Markdown outputs.
- Live check (real network, three public Arena profiles taken from `arena/index.json`, output redacted, nothing kept):
  all parsed. Observation: a profile's `Overall.GamesCount` (22) can differ from the sum of its modes (31); shown as
  stored.
- Tests: `tests/test_arena.py` (8) with the invented `fixtures.ARENA`; `test_plugin.py` `test_arena_is_asked_for_by_name`,
  `test_arena_by_default_setting`, `test_arena_links_binding_and_names`, `test_arena_missing_and_outputs`;
  `test_player_page_links` updated. 155 -> 168. Fonts rebuilt (character list 1299 bytes, font 202,636 bytes).
- Interpretation to confirm: "预留默认查询竞技场的设置" was read as the `arena_by_default` switch, not as an Arena
  option for `default_mode`.

### 19.4 Arena picture proposals (not implemented; maintainer to choose)

Rendered by a throw-away script (`scratchpad/arena_previews.py`, not in the tree) from the invented fixture, using only
the bundled font families (Oswald, Share Tech Mono, Noto Sans SC), so no new dependency whichever is chosen. All three
end with the data line and the §19.1 footer, and show no level.

| | Style | Elements |
|---|---|---|
| A | 情报档案附页 (same family as the main card) | paper, FILE NO., "竞技场 / ARENA · SNAPSHOT" stamp, dog tag without level, per-section summary line, per-mode table with a hatched win/loss bar (exit-bar colours) |
| B | 赛后计分板 (dark, Arena orange) | dark panel with diagonal texture, orange accent bar, large nickname, four stat tiles per section (K/D, games, best ARP, max killstreak), per-mode rows with W/L bar and win rate, K/D, kills, MVP; ranked in orange, unranked in grey |
| C | 选手卡 (steel, hazard stripes) | hazard-stripe bands, hexagonal BEST ARP badge, ID plate, section chips, per-mode cards (two columns) with a win-rate ring, K/D and MVP |
- Mutation check (13, scratch copy): counter clamp, mode-key filter, mode cap, ranked split, labels, order, Arena
  fetched alone, `arena_by_default`, game-only default mode, Arena summariser, requested id, image-output note, Arena
  "no profile" text. The default-mode one survived at first; `test_arena_is_not_a_default_mode` added (168 tests),
  then killed.

### 19.5 Arena picture proposals, round 2 (built on the game's setting)

Maintainer: all three round-1 looks are good, but the design must draw on Arena's setting. Research (web, 2026-10-14):
- The official site and its "What is Arena?" page: gladiator fights in arenas across the city of Tarkov, organised by
  a mysterious group of Arena Masters led by the Host ([support page](https://arena.tarkov.com/support/knowledge/457));
  the Fandom wiki describes Block as a courtyard "equipped by the Host for Arena fights"
  ([wiki](https://escapefromtarkov.fandom.com/wiki/Arena)); a hands-on review describes a cheering crowd
  ([MMORPG.com](https://www.mmorpg.com/player-interviews/hands-on-impressions-escape-from-tarkov-arena-is-a-great-entry-point-to-the-extraction-franchies-2000129435)).
- The Host appears in the main game as the trader Ref, who moves roubles, GP coins and medals between the games; his
  unlock quest has players paste fight posters around the maps ([Insider Gaming](https://insider-gaming.com/how-to-unlock-ref-in-escape-from-tarkov/)).
- ARP = Arena Reputation Points, the ranked rating ([Liquipedia patch notes](https://liquipedia.net/tarkovarena/Patch_0.1.5.0.28989));
  maps include Air Pit, Bay 5, Block, Bowl, Chop Shop, Equator, Fort, Iceberg, Sawmill, Skybridge
  ([Liquipedia maps](https://liquipedia.net/tarkovarena/Category:Maps)).

Proposals (throw-away `scratchpad/arena_previews2.py`, invented fixture, bundled font families only, no game assets;
every element drawn; footer as §19.1; no level):

| | Concept | Setting elements |
|---|---|---|
| A | The Host's fight poster | a poster taped to a concrete-block wall (the posters of Ref's quest): "THE HOST PRESENTS", huge nickname with an offset print, red/black bands per section with the season record (wins – losses summed over modes), starburst "BEST ARP", each mode as a numbered BOUT, torn bottom edge, "每晚开赛 · 观众席已满" |
| B | The Arena Masters' fighter registry | a ledger page on dark leather: "ARENA MASTERS · 选手名册", entry rows, a GP-coin-like milled medallion for BEST ARP ("REPUTATION"), sections marked "计入 ARP / 不计 ARP", ruled event table, pen ticks for winning records, "登记人：THE HOST" and a wax seal |
| C | Live scoreboard of a night-time pit | floodlights and light cones, "LIVE · FROM THE PIT", amber LED dot-matrix numbers on a stadium board per section, per-mode rows, chain-link fence along the bottom, "主持：THE HOST" |

Not done: no map-level statistics exist in the profile, so maps are not shown; the round-1 proposals (§19.4) are
superseded unless the maintainer prefers one of them.

### 19.6 Arena picture: proposal B implemented (maintainer: "采用方案2")

- `tarkov_core/arena_image.py` (new): `render_arena_image(card)` -> JPEG. Ledger page on a leather surface (seeded
  grain tiles, byte-stable), "ARENA MASTERS · 选手名册", entry rows (nickname through `safe()`, fitted), a milled
  medallion with `best_arp(card)` (highest ARP of any section or mode, "—" when none), per section a dark band
  "非排位 UNRANKED · 不计 ARP" / "排位 RANKED · 计入 ARP" with `record(section)` (wins – losses summed over modes; hidden
  when no mode counts wins), the section total line, a ruled table (EVENT, games, W – L, win rate, kills, K/D, MVP;
  every cell fitted to its column), a pen tick where wins > losses, notes (stale, rename/also-has, refresh), "登记人：
  THE HOST" and a wax seal; then `image.footer()`. Height is bounded: at most `MAX_MODES` rows per section.
- `arena.py`: `losses()` (the document's `ArenaLoses` when present — unranked modes carry it too — else games -
  wins, never negative), `record()`, `best_arp()`.
- `image.py`: the footer and the JPEG save are now `footer()` and `jpeg()`, shared by both pictures.
- `main.py`: `arena_reply()` is async; image output renders off the loop and sends the picture plus the refresh link
  line, falling back to text with "（图片生成失败，已改为文字）" (same as the main card).
- `tools/build_preview.py` also writes `docs/arena_preview.jpg` from the invented fixture (README shows it).
- Reference run the maintainer asked for, on a real public profile named by the maintainer (name and id deliberately
  not recorded, §18; the account is in the PvP, PVE, season and Arena indexes): by Arena link, then by the learned name, then without a mode; real network, images kept in
  `/home/user/test_output` only (not committed). It showed three-digit W – L and MVP values crowding their columns;
  columns re-spaced (games 240, W – L 300, win rate 425, kills 500, K/D 570, MVP 630, tick at the right edge).
- Tests: `ArenaImageTests` (stable JPEG of the right width, registry content incl. coin value, both section bands,
  record, W – L cell, notes, no level, footer drawn last; 20 modes with huge values and a hostile nickname stay bounded;
  missing `stat`/`aid`/`updated`/`achievements`; helpers), the plugin test now expects the picture and the link line,
  and the image-failure fallback. 168 -> 172. Mutation check (8): best-ARP max, `ArenaLoses` first, losses floor,
  record without wins, image fallback note, link line, footer, row cap — all killed. Fonts rebuilt (character list
  1317 bytes, font 204,020 bytes).

### 19.7 Release 0.4.0 (maintainer: "提交到新仓库v0.4.0")

Version 0.4.0 (`VERSION`, `metadata.yaml`, `@register`, CHANGELOG `## 0.4.0 - 2026-10-14`, README/CHANGELOG AI
statement "0.1.0 至 0.4.0"); branch `v0.4.0` created next to `v0.3.0`, the same way. Its history folds the proposal-B
commit, the follow-up that removed the reference player's name from the docs, and this version bump into one commit,
so the name never appears in `v0.4.0`'s history (it still appears in the work branch's history until that branch is
rewritten, which waits for the maintainer).

## 20. Suggestions for names that differ by '_' and '-' (0.4.1)

Maintainer's live test asked for an Arena player by a name with '_'; the public indexes hold two accounts, one with
'_' (PvP index only, no Arena profile) and one with '-' (Arena, PVE, season). The maintainer approved suggesting such
names. (The names and ids are not recorded here, §18.)

- `names.spelling_variants(name)`: every other lower-case spelling with each '_'/'-' swapped; [] with no separator
  or more than `MAX_SEPARATORS` = 6 (at most 63 variants). `SUGGESTIONS` = 3.
- `NameIndex.similar(mode_path, name)`: `key IN (variants)` on the index already on disk (indexed column), under the
  mode's lock; never refreshes or downloads; [] without an index; a damaged names table is logged and gives [].
- `Accounts.similar(name)`: same on the shared record, most recently seen first.
- `main.py`: `similar_accounts(mode, target, exclude)` merges the record and (only with `name_index_enabled`) the
  index, dedupes by account, drops `exclude`, keeps 3. Used (1) when a name is unknown: "你是不是要找：X（账号 ID n）？
  可以发送 /tarkov [mode] X。如果都不是，" before the usual steps; (2) when a name resolves to an account without a
  profile in the mode: "名字相近的还有：X（账号 ID n），可以发送 … 试试。" (the live case: the '_' name had been learned
  from a PvP query); (3) in `bind()` when the record has no such name.
- Tests: `VariantTests.test_spelling_variants`, `test_similar_names_from_the_index_on_disk_only` (no download, no
  warning without an index, damaged table), `test_similar_names` (record), plugin tests for the unknown-name, matched-
  but-missing, bind and index cases; `FakeNames.similar`. 172 -> 177. Mutation check (10): own key excluded from
  variants, separator cap, index-on-disk guard, damaged-table guard, index only when enabled, suggestion in
  `by_name`, in `bind`, mode word in the command, record query, excluding the matched account. Three survived the
  first run (the tests did not reach those branches); tests strengthened, all killed.
- Live re-run (real network, data in scratchpad only): with the index on, the '_' name gets "你是不是要找" with the
  '-' account; with the '_' account already learned, the "no Arena profile" reply adds the '-' account.
- Release 0.4.1 (maintainer: "推送新分支v0.4.1"): version bumped in the usual places; branch `v0.4.1` is one commit
  on top of `v0.4.0` (whose history never held the reference player's name, §19.7), with the same tree as the work
  branch.

## 21. PMC K/D, source lines, image wording, Arena ranks (0.4.2; maintainer's list)

- `card.side()`: `pmc_kd` = PMC kills / deaths (None without deaths). Text "，PMC K/D x" after "击杀 PMC n"; Markdown
  column "PMC K/D" after "击杀 PMC"; image combat table gets a ninth column (xs re-spaced: 40, 135, 210, 285, 385,
  470, 540, 615, 705; headers fitted to their column). Not in Arena (no PMC there).
- `tarkov_core.NOTICE` = "本插件在 GitHub 开源，遵循 GPL-3.0-or-later 许可；与 Battlestate Games 无直接关系" (the
  maintainer's sentence with the plugin's usual CJK/Latin spacing). `output.source_lines()` /
  `markdown_source_lines()` end every text and Markdown profile reply, Arena included: name + version + repository
  URL, then NOTICE.
- `image.py`: title "塔科夫市 · 绝密情报档案"; dog tag "{mode} · {n} 成就数" (the word in the CJK font); footer
  "档案更新于 GMT+8 {time}"; the two small-print lines are 15 px (was 13) and centred; footer height 96. Both
  pictures share `footer()`.
- Arena ranks (`arena.RANKS`, `rank()`, `arp_text()`): the maintainer's table has 100-ARP overlaps between neighbours
  (promotion above the upper limit, demotion below the next lower limit) and the profile only stores BestArp (the
  highest ever reached), so a rank counts once BestArp passed the previous rank's upper limit: <=1000 Marauder,
  <=2000 Jackal, ..., <=7000 Veteran, <=7900 Professional; > 7900 (`TOP_ARP`) the leaderboard ranks cannot be told
  apart, shown as "竞技场前100名玩家" without the ARP (maintainer). Text/Markdown: "最佳 ARP 1610（Jackal）"; the coin:
  value, then the rank name; above 7900 a black coin with gold "竞技场 / 前100名玩家".
- Tests: `test_pmc_kd_is_pmc_kills_per_death`, `test_markdown_ends_with_the_source`, `test_maintainer_wording`,
  footer test (exact lines, centred, GMT+8, no 北京时间 on the image), `ArenaRankTests` (boundaries, top label, no
  ARP on the top coin, source lines in Arena outputs); expectations updated. 177 -> 183. Mutation check (13): PMC K/D
  formula, rank boundary, top threshold, top hides ARP, text/Markdown/Arena source lines, centred footer, GMT+8,
  title, 成就数, PMC K/D column, rank on the coin — all killed. Fonts rebuilt (character list 1341 bytes).
- Reference images for the maintainer (season and Arena of the player they named; real network; kept in
  `/home/user/test_output`, not committed, player not named here).
- Follow-up (maintainer, after checking the reference images): the data-source line of `image.footer()` is centred too;
  Arena W – L cells are `arena_image.win_bar()`: one bar, wins `WIN` green and losses `LOSS` red (the main card's
  survived/KIA colours), counts "w / l" in 12 px under it, an empty frame for 0 games, "—" without wins; the
  "登记人：THE HOST" signature is replaced by a closing double rule broken around "ARENA MASTERS · 竞技场主办方存档"
  (the wax seal stays). Tests: `test_win_loss_bar`, registry content, centred data line. 183 -> 184. Mutation check
  (5): centred data line, bar colours, zero-games guard, counts, closing line — all killed.
- Release 0.4.2 (maintainer: "整理changelog handoff checklist文件后，推送分支v0.4.2"): version bumped in the usual
  places; CHANGELOG `## 0.4.2 - 2026-10-14`; checklist reorganised (versions newest first, 0.1.0's audits and 0.3.0's
  second review as subsections of their versions, the real-player cleanup between 0.3.0 and 0.4.0, open questions
  renumbered); §6 items 8-10 added. Branch `v0.4.2` is one commit on top of `v0.4.1` with the work branch's tree.
