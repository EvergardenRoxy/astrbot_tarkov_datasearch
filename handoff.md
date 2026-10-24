# Engineering handoff: `astrbot_tarkov_datasearch`

Audience: engineers or LLM agents debugging or reviewing this repository. This is a record of what was built,
why, what evidence the decisions rest on, and where the risk is. It is not user documentation (see README /
CHANGELOG for that). `checklist.md` is the plain-language Chinese companion. The layout and conventions follow the
maintainer's earlier plugin `astrbot_plugin_spark_analyse`.

Current documentation rule (maintainer, 2026-10-24): record work in `handoff.md`; do not modify `README.md` or
`checklist.md`, which will be organized separately later.

## 1. Context

| Item | Value |
|---|---|
| Task | New AstrBot plugin to look up Escape from Tarkov player profiles in chat (maintainer request, 2026-10-13). |
| Inputs | Maintainer's analysis `report.md` of the-hideout/tarkov-dev (not in the tree; key facts restated in §2). Maintainer's own profile JSON downloaded from tarkov.dev, used only for a local comparison (§2.4), not committed. |
| Maintainer decisions | Personal data only (no item/price/task queries). Any nickname may be queried, as "mode + name". Data source: players.tarkov.dev static snapshots. Plain-text output first; image rendering later. v0.1 fields: level, side, K/D, survival rate, skills; lookup by nickname or account id. License GPL-3.0-or-later. Name index checked weekly (§9). AI statement names Claude Opus 5.5 (§9). |
| Branch | `claude/amazing-clarke-eu3xue` (repo was empty). No PR. |
| Version | 0.4.4 since §24; 0.4.3 since §22-§23; 0.4.2 since §21; 0.4.1 since §20; 0.4.0 since §19; 0.3.0 since §16; 0.2.0 since §15; 0.1.0 before (`tarkov_core/__init__.py` `VERSION`, `metadata.yaml`, `@register(...)`, CHANGELOG; `test_version_is_consistent`). |
| Tests | 193, `python -m unittest discover -s tests`, Python 3.13 locally; CI on 3.12 and 3.13. |
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
| Plugin | `main.py` | command `tarkov` (alias `塔科夫`), shown to users as `@机器人 tarkov` (§23); `/tarkov` works too. Flow: access check → parse `[mode] target` → digits: fetch by id, on 404 fall back to the name index if the digits are a valid name → name: refresh notice if needed, lookup, disambiguate (one exact-case hit wins; otherwise >1 hit lists ids) → fetch → `reply()`: card, then image (`asyncio.to_thread`, `Image.fromBytes`, text on any failure), text or Markdown per `output_mode` (unknown value -> image). One in-flight query per sender (`self.active`, sha256 of origin + sender like spark). Profile summaries cached in memory (TTL `profile_cache_minutes`, at most `CACHE_ENTRIES` = 128, oldest-inserted evicted). |
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
- Commands shown to users are built only through `TarkovPlugin.cmd()` from `tarkov_core.COMMAND_STYLES` (§24); no
  other file may write `@机器人` or a `/tarkov` command (`test_command_style_setting` checks it).
- Every help and profile reply carries its link as text, whatever the output and whether drawing worked (maintainer,
  §22): images are followed by a `Plain` line; `test_links_are_always_given_as_text` pins it.

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
shared by everyone; `@机器人 tarkov` without arguments queries the caller's own binding (help moves to `@机器人 tarkov 帮助`).

### 16.3 Changes

| Piece | Change |
|---|---|
| `modes.py` | `MODES` gains the website mode segment (`regular`/`pve`/`pvp-season`); `parse_player_url()` accepts `tarkov.dev/players/<mode>/<id>` and `/player/<id>` (scheme and `www.` optional, case-insensitive, full match only, so no other host or query string); arena -> `(None, id)`; `page_url()`, `search_url()`. |
| `accounts.py` (new) | See §3. |
| `main.py` | `handle()` parses `帮助/help`, `绑定/bind`, `解绑/unbind`, an optional mode, then nothing (own binding), a link, an id or a name. A link's mode wins; a different explicit mode is refused; arena is refused. `bind()` resolves link/id/recorded name, fetches the profile to confirm it exists, stores the binding. `query()`: own binding, link/id, then `by_name()`: the shared record first, the downloaded index only when `name_index_enabled`; not found -> step-by-step guidance (search page link for that mode, example link command). `profile()` records every fetched nickname (`sqlite3.Error` is logged, never fails the query). `reply()` passes the player's page to the card and, for images, appends `Plain('刷新数据：…')` after the image. |
| Card / outputs | `build_card(..., refresh_url=)`; text, Markdown (unescaped link built by the plugin) and image notes end with the "数据不会自动更新" reminder. |
| Config | `name_index_enabled` (bool, default false, schema = code fallback); `name_index_refresh_hours` and the timeout hint now say they apply only with the index. |
| Fonts | Subset rebuilt for the new text (character list 1242 bytes, 196 KB). |

Live run (real network, SDK stubbed, data dir fresh, not uploaded): unbound `@机器人 tarkov` -> guidance; bind by B's
link; `@机器人 tarkov pve` -> image + link line; `@机器人 tarkov` (PVP, no profile) -> guidance with the PVP search link; A by
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
- Live run after the push: `@机器人 tarkov pvp <A's id>` (PVE profile only) answered "no profile, unknown name": an id of 5+
  digits is also a valid nickname, so the name lookup ran and its "unknown" text replaced the id's. Now, when no name
  matches, the id's own answer is given ("PVP 模式下还没有…但 PVE 模式（@机器人 tarkov pve <id>）下有档案").
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

Decisions: query with `@机器人 tarkov 竞技场 <...>` and reserve a setting to query Arena by default; mode names stay English
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
  可以发送 @机器人 tarkov [mode] X。如果都不是，" before the usual steps; (2) when a name resolves to an account without a
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

## 22. Help as an image (0.4.3; maintainer: the text help is hard to read in phone chat apps)

- `tarkov_core/help_image.py`, `render_help_image(card)`: the dossier paper of `image.py` (`paper`, `footer`,
  `jpeg`), W 820, larger type than the profile card (headings 26 px, commands 24 px on dark "label tape", body 20 px)
  because it is read on phones. Sections: commands (`help_rows`), modes (`MODE_CHIPS`, the default one framed red with
  a "默认" tab), first query in three numbered steps (search page, example `@机器人 tarkov <player page>`, names remembered
  [+ name lists]), notes (snapshots do not refresh; `_`/`-` suggestions). Stamp "使用指南 / FIELD MANUAL". The canvas
  is 2400 px and cropped at the footer.
- `image.footer()` takes an optional `line` replacing the data/update line (the help has no update time).
- `main.py`: `help_card()` (default mode key and label, `search_url`, `page_url(mode, 1234567)`, `arena_by_default`,
  index interval or None) and `help_reply()`: with output "image" the picture (drawn in a thread) plus
  `Plain('tarkov.dev 玩家搜索：<search url>')`; text for text/Markdown output or when drawing fails (logged
  "help image failed"). Both help paths (`help`/`帮助` and too many arguments) use it. `help_text()` is unchanged and
  stays the single text wording; the image wording mirrors it and must be kept in step.
- `_conf_schema.json` output-mode hint, README (reply section, example list, `docs/help_preview.jpg` from
  `tools/build_preview.py`), CHANGELOG "未发布", checklist. Fonts rebuilt.
- Tests: `tests/test_help_image.py` (JPEG, deterministic, every command drawn, default mode / arena rule / index
  interval follow the card, exactly one "默认" mark on the right chip, footer last), plugin tests
  `test_help_follows_the_output_mode`, `test_help_image_settings_and_failure`. 184 -> 190. Mutation check (6): always
  text, arena flag, index interval, default mark, arena rule, search link — all killed.
- Follow-up (maintainer: "无论如何情况下都要保留链接的文字输出，方便使用"): `search_line()` ("tarkov.dev 玩家搜索：<url>")
  is the last line of `help_text()` (the URL moved out of the first-query sentence, which now points to the last
  line) and the `Plain` after the help image, so every help reply ends with the same text link. Profile and Arena
  replies already carried theirs (image + `Plain` refresh line; text/Markdown refresh notes, also on the drawing
  fallback). Invariant added to §7. Test `test_links_are_always_given_as_text`: output image/text/markdown x drawing
  ok/failing x help/profile/Arena. 190 -> 191. Mutation check (2): link line after the help image, last line of the
  text help — both killed.

## 23. Commands shown as "@机器人 tarkov" (0.4.3)

Maintainer: Tencent only lets unverified QQ bots be invoked by mentioning them, "@机器人 + 指令" without a slash; every
document and image should show commands that way.

- Matching is unchanged: AstrBot routes `tarkov`/`塔科夫` (`filter.command`), and `COMMAND` strips an optional leading
  `/`, so `@机器人 tarkov ...` (the mention is not part of `message_str`), `tarkov ...` and `/tarkov ...` all work.
- Every command the plugin shows was rewritten from `/tarkov` to `@机器人 tarkov` (literal text in `main.py` and
  `help_image.py`: help text, bind usage, first-time steps, other-mode hints, suggestions, `command_for`). The text help
  gains a second line "群里先 @机器人，再发送下面的命令（不加 /）"; the help image's footnote under the command table says
  the same. "@机器人" is a placeholder for the bot's mention, not its name.
- Docs: README (usage note in "开始使用", aligned command block, all inline commands), CHANGELOG, checklist and this file
  use the same form (`/tarkov` kept only where the doc says it also works). Fonts rebuilt; help preview redrawn.
- Test `test_commands_are_shown_as_at_the_bot`: no `/tarkov` or `/塔科夫` command (URLs excluded) in `main.py` or
  `tarkov_core/*.py`; `tarkov ...`, `塔科夫 ...` and `/tarkov ...` all query; the unbound reply shows `@机器人 tarkov 绑定`.
  Expectations in other tests updated. 191 -> 192. Mutation check (2): one hint put back to `/tarkov` in `main.py` and
  in `help_image.py` — both killed.
- Release 0.4.3 (maintainer: "推送v0.4.3"): version bumped (`tarkov_core.VERSION`, `@register`, `metadata.yaml`, tests,
  README), the maintainer's `logo.png` from `main` added to the tree. Branch `v0.4.3` is one commit on top of `main`
  (= `v0.4.2` + logo) with the work branch's tree, so `main` can fast-forward to it.

## 24. "Command style" setting (0.4.4)

Maintainer: "需要实现从设置页切换两种不同的帮助和全部的操作提示 — 类型1：如QQ官方机器人等 @机器人 指令；类型2：支持 / 唤醒的平台上
/指令 直接调用".

- `tarkov_core.COMMAND_STYLES`: `mention` (`@机器人 tarkov`, the 0.4.3 wording, default) and `slash` (`/tarkov`), each
  with `command`, `help` (second line of the text help) and `note` (line under the help image's command table). The only
  place these strings are written.
- Setting `command_style` in `_conf_schema.json` (string, options mention/slash, default mention; labels name the
  platforms). `main.py`: `style()` (unknown value -> mention) and `cmd(*words)`; every former literal goes through
  `cmd()`: help text, bind usage, unbound reply, bind success, first-time steps (`first_time(command=None)` defaults
  to `cmd()`), other-mode hints, name-list hint, `command_for`, suggestions. `help_card()` passes `command` and `note`;
  `help_image.help_rows()` and the example step use `card['command']`, the footnote is `card['note']` fitted to the
  width. Matching is unchanged (`tarkov`/`塔科夫`, optional `/`).
- Docs: README (usage note and settings list), CHANGELOG, checklist; docs examples keep the default (mention) form.
- Tests: `test_command_style_setting` replaces `test_commands_are_shown_as_at_the_bot`: no `@机器人`/`/tarkov` outside
  `tarkov_core/__init__.py`; for style unset/mention/slash/unknown, 12 commands (help, unbound, bind usage, too many
  words, unknown names with and without a mode, other-mode hint, suggestions for query and bind, bind success, Arena
  miss) — at least 9 replies carry the style's command, the other style never appears outside URLs, and each hint kind
  is checked by its exact wording; typed `tarkov`/`塔科夫`/`/tarkov` all query under both styles.
  `test_help_image.test_command_style`: slash commands, note and example; no `@机器人`. 192 -> 193 (one test replaced, one
  added). Mutation check (9): setting ignored, a literal in `command_for`, a literal in
  the help text, help line dropped, slash help line swapped, `help_card` command, image note, image example, bind
  suggestion command — all killed.
- Release 0.4.4 (maintainer: "实现功能后检测无问题则自行推送分支v0.4.4"): version bumped in the usual places. Branch
  `v0.4.4` is one commit on top of `v0.4.3` with the work branch's tree.

## 25. Open review finding: binding treats failed mode requests as missing profiles (2026-10-24)

Status: recorded on branch `1024` at the maintainer's request; no code fix has been made.

- Scope: the second finding from the repository review only. The shared nickname-to-account-id mapping identifies
  the same account across modes; it does not establish whether that account has a profile in each mode. A timeout
  cannot establish that the player has never been searched in that mode on tarkov.dev.
- Reproduction: make the PVE profile request succeed, the PVP request raise `TimeoutError`, and the season request
  return HTTP 404, then bind the account with `tarkov 绑定 <account id>`. Binding succeeds using the PVE profile,
  but the reply both reports the PVP timeout and includes PVP in the modes whose profiles supposedly need a website
  search before they will appear.
- Cause: `main.py`, `TarkovPlugin.bind()`, computes `missing` as every mode absent from `found`, including those in
  `failed`. `profiles()` already distinguishes an HTTP 404 (`NotFound`) from a timeout or other request failure,
  but this binding-success hint loses that distinction.
- Suggested fix: exclude modes in `failed` from `missing`. Keep the failure/retry message for failed modes; use the
  website-search hint only for modes confirmed missing by HTTP 404. This does not require changing the shared name
  mapping or which modes are queried.
- Suggested regression check: PVE succeeds, PVP times out, and season returns 404. Assert binding succeeds, PVP is
  reported as temporarily unconfirmed with a retry hint, and only season appears in the website-search hint.
- Review evidence: reproduced with synthetic profiles and mocked transport responses; the existing 193 tests passed
  on Python 3.12.14 after allowing sockets for the local HTTP test server. Existing tests do not assert this mixed
  success/timeout/404 binding reply. No live AstrBot or external tarkov.dev request was used for this reproduction.

## 26. Public game-data API inventory and request conventions (2026-10-24)

Maintainer request: inspect `https://json.tarkov.dev/endpoints`, `https://tarkov.dev/api/`, and upstream manager
implementation where necessary; document available data and standardized requests. Documentation only, on branch
`1024`; no plugin behavior change or fix for §25. README and checklist were left unchanged per the current rule.

- Deliverable: `docs/tarkov-api-reference.md`, a Chinese reference covering all 10 advertised JSON endpoint families,
  response shapes and important fields, mode identifiers, ID joins, local filtering, translation dictionaries and
  JSONPath replacement, HTTP caching/error handling, examples, and a separate legacy GraphQL appendix.
- Sources: live endpoint manifest and responses; the official site's API page source and client implementation at
  `the-hideout/tarkov-dev` commit `ec44177bd3c21aa2b33938153acd0a96dbd939aa`; data generation at
  `the-hideout/tarkov-data-manager` commit `59bc50d9cbcb1a0abe80e02f7137ce98d8445dbf`. Exact links are in the reference.
- Main findings: `/api/` now documents JSON GET resources. The official GraphQL page source labels GraphQL as
  maintenance mode and recommends JSON for future use. JSON resources contain ID references and localization keys,
  rather than the expanded relationships / already-translated strings a GraphQL caller might expect. Base data and
  `_<language>` dictionaries must be fetched separately; use the response's `translations` JSONPaths to localize.
- Scope boundary: these endpoints describe shared game data, prices and rules, not an individual player's live
  profile, quest progress, stash or inventory. `regular` is the JSON API mode identifier for PVP; the existing
  player-snapshot service uses `profile` instead. Arena is not advertised in this JSON manifest.
- Validation: public, read-only GETs returned parsable JSON for all 10 families using regular-mode resources where
  applicable, global `/status`, and `/pvp-season/season`. Also checked items and a sample item's historical prices in
  all three modes; checked Chinese dictionaries for items, maps, hideout, tasks, traders and season, plus English
  items fallback. A conditional `/status` GET with its ETag returned 304. The reference's Salewa lookup was verified
  against the downloaded responses (`Salewa急救包`). No authenticated endpoints or player-profile requests were used.
- Live observations: regular items ~17.3 MB, maps ~8.9 MB, tasks ~2.1 MB decoded; `Cache-Control: max-age=691200`
  with ETag and Last-Modified was observed. Upstream generation purges CDN cache after changed data is uploaded;
  this is not a guaranteed update schedule. Historical prices combine archived and recent points, with epoch-ms
  timestamps and optional offer-count fields. The GraphQL GET probe returned an `errors` response saying the server
  was unavailable; its appendix examples are source-verified, not claimed as successful live queries.
- Verification for this documentation change: validate the examples against captured JSON, check source links and
  `git diff --check`; no application test rerun is needed because no runtime or dependency files changed. Source
  responses were kept under `/tmp/tarkov-api-research` for this session only, not added to the repository.

## 27. Proposed next-stage feature design (2026-10-24)

The initial proposal below is historical; §28 and the revised `docs/development-plan.md` supersede its item command
semantics, text/Markdown output options, monitoring filters, and interval bounds.

Maintainer goals: feature switches for server-status checks/broadcasts, item flea/trader prices and properties,
Boss spawn-rate change polling/broadcasts, and official QQ support using `@机器人 /命令`.

- Deliverable: `docs/development-plan.md`, the Chinese design proposal. No runtime implementation, schema change,
  actual message send, commit or push in this planning step; work remains on `1024`. README/checklist untouched.
- Config proposal: independent feature gates, nested behavior through dependent query/broadcast switches, individual
  flea/trader/details switches, explicit UMO recipient lists, mode/map/Boss filters, intervals and quiet/cooldown
  behavior. New game-data features default off; QQ compatibility defaults on without disabling old QQ behavior.
- Commands: `/status`, `/item`, `/boss`, plus stable `/tarkov ...` forms; short routes can be disabled to resolve
  conflicts. Item names accept spaces; explicit `id:` avoids name ambiguity. QQ adds a `mention_slash` display style
  plus actual framework-aware command normalization, rather than only changing the displayed command string.
- Shared implementation: dedicated public-resource client/cache, status/item/Boss modules, a common notification
  dispatcher with durable baselines/events/per-target attempts, and a platform compatibility boundary. Large data
  limits and thread-based parsing/indexing are separate from player-profile limits. Shutdown cancels and awaits
  pollers; config reload must not duplicate workers or let stale worker results overwrite current state.
- State handling: first successful poll establishes a silent baseline; failed requests preserve the last successful
  baseline and never mean game downtime / 0% Boss probability. Probability comparison is per mode/map/mob and
  preserves conditional spawn entries without summing them. Changes use percentage points, not ambiguous percent
  growth; invalid or absent data is distinguished from a confirmed complete-record change.
- Delivery: explicit targets only; no subscriptions inferred from access lists. Persist baseline and events together,
  track each recipient separately, merge during quiet/cooldown periods, and bound retries/history. Do not claim
  exactly-once delivery or equate `context.send_message()`'s matched-platform boolean with a delivered message.
- QQ evidence: inspected AstrBot commit `0cd42bd4dd216d8810da4e3f5fb2e381d3c6817e` docs, command/wake filters,
  official QQ adapter, and send context; Tencent bot-docs commit `645787a45937e5d9c4f0f61afefdffde0f38696e`.
  New AstrBot docs/adapter support configured proactive group messages, while older Tencent docs and a Context
  docstring retain contrary limitations. Treat proactive delivery as version/scene/permission-dependent and require
  actual target-platform verification; keep user-triggered `/status` and `/boss 变化` available where push cannot work.
  Do not claim support on the current minimum AstrBot 4.16.0 solely from current-master evidence.
- Order: shared infrastructure + QQ route proof; status query + notification backbone; item queries; Boss monitoring;
  QQ WebSocket/Webhook live acceptance and release review. §25's failure/missing distinction is a prerequisite cleanup.
- Validation at design time: source/response inspection and documentation consistency checks only. The proposal lists
  meaningful behavior tests and a live QQ acceptance matrix; no new application test results are claimed.

## 28. Design revision: image queries, item layers, configurable polling and Boss tables (2026-10-24)

Maintainer requirements incorporated into `docs/development-plan.md`; this is still documentation only on `1024`.
README/checklist, runtime code and configuration schema remain unchanged.

- All queries return images, including existing player profiles, status, items, Boss tables, change summaries and
  candidate/empty/error results. Help remains an image. Plain text can accompany permitted links or report a final
  image generation/delivery failure, but must not replace a successful query with a full text/Markdown report.
  The implementation plan migrates existing text/Markdown output preferences to images. Binding confirmations are
  control replies rather than query reports and may stay short text.
- Item command semantics: `/item <name>` means common attributes shared across modes, with no default-mode assumption or
  price; `/item <mode> <name>` means that mode's prices, trading/unlock conditions and actual attribute differences.
  Item IDs connect common identity and separate mode records; common-field whitelists are checked across snapshots,
  and unavailable/mismatched fields are not silently promoted to shared facts. Candidate results are images too.
- Polling cadence: Boss polling has a configurable 2–30 minute interval (integer minute steps). The general requirement
  applies to every polling feature, so status gets an independent 2–30 minute setting too; proposed defaults are
  status 5 minutes and Boss 10 minutes. On-demand cache freshness is named separately and is not a hidden poller.
  Rescheduling must not overlap runs or catch up missed ticks; rate-limit/failure backoff is separate from normal cadence.
- Boss image format: results and broadcasts are complete image tables for every map and Boss in each selected mode,
  keeping unchanged rows, preserving separate spawn conditions, and marking changed values/rules/new/removed records
  red with explicit text. Proposed monitoring defaults cover all three modes. Remove map/Boss filters that would
  truncate the requested full tables; modes remain separately labeled and paginated.
- Event images retain fixed old/new snapshot references and comparison timestamps; later manual queries or 304s
  must not erase or relabel the change. Readable pagination repeats headers and covers all rows. Page-level delivery
  tracking and snapshot-bound continuation queries handle QQ scene limits without silently losing rows.
- Add image renderers and a common configurable polling scheduler to the implementation plan. Image rendering is a
  first-stage acceptance requirement for each feature, not a later optional enhancement. The revised test plan covers
  output migration, common-vs-mode data, full-table coverage/red marks, pagination, independent 2/30 minute bounds,
  invalid settings, and reload/reschedule behavior with a controlled clock.
- Validation: documentation consistency and whitespace checks; no new runtime tests or actual message sends in this
  planning revision. Prior §27 is retained as history and explicitly marked superseded where these requirements differ.

## 29. Implemented foundation: public resource cache and QQ slash routing (2026-10-24)

Maintainer authorized the first implementation stage, "公共缓存与 QQ 路由验证". Work stays on `1024`; no commit,
push or release/version bump requested. README/checklist remain unchanged. The approved design is still in
`docs/development-plan.md`, now with a stage-progress section; remaining feature commands and image migration are
not claimed as implemented.

### Public resources and persistence

- `tarkov_core/public_api.py`: fixed `https://json.tarkov.dev` host; structured `ResourceKey` validates the resource,
  canonical mode, supported language and historical item ID before connecting. `mode_path()` explicitly translates
  plugin pvp/season to regular/pvp-season; no accidental use of the player's `profile` path. Global status/endpoints,
  season-only data, translations and price-history paths have distinct validation rules.
- Public-resource limits are 32 MiB received / 64 MiB decoded, separate from player-profile limits; a compressed
  synthetic 17 MiB item resource is accepted, while over-limit documents are rejected. Envelopes and primary resource
  shapes are validated and JSON is parsed off the event loop; translation dictionaries remain separate documents.
- `transport.http_get()` factors out the bounded reader without changing the existing `transport.get()` return shape.
  Supports ETag and Last-Modified (ETag preferred), 304, no redirects, honest plugin agent and the existing explicit
  proxy / `trust_env=False` policy. HTTP failures retain status and bounded parsed Retry-After; connection details and
  proxy credentials stay out of chat errors. Player and index transport regression tests continue to pass.
- `tarkov_core/resource_cache.py`: lazy SQLite file `public-data/resources.sqlite3`, under the existing plugin data
  directory. Initialization does not create a cache file, download anything or schedule a task. Rows contain body,
  validators, cache-control and fetched/checked/access times; transactional replacement keeps body/metadata together.
- Cache identity is the canonical resource path, including mode/language/item ID. TTL defaults to 5 minutes via
  `public_data_cache_minutes` (0 means check each time); stored Cache-Control can shorten TTL, no-cache revalidates,
  and no-store removes persistent entries. 304 preserves the body's original fetch time but updates check time.
- Defaults: at most 32 entries and 128 MiB of stored response bodies, with least-recently-used eviction; SQLite file
  overhead/free pages/journals are additional, so this is a payload budget, not a strict filesystem-size quota. Network
  downloads are capped at 2 and pending resource tasks at 32. One in-flight lookup/check per path is shared; cancelled
  readers do not cancel others, and a fresh reader upgrades an in-flight TTL lookup to a conditional check.
- Failures do not overwrite a valid document. Stale fallback is opt-in for HTTP/connection/timeout/invalid-JSON-shape
  failures and returns `stale=True` plus a safe error while retaining successful timestamps. Configuration errors such
  as an invalid/empty enabled proxy are not masked; confirmed 404 invalidates the row and is not served as fallback.
  Retry-After suppresses repeat network attempts for that path. Corrupt JSON rows rebuild without stale validators.
- Snapshots share immutable body bytes; `await snapshot.json()` returns a newly decoded document for each consumer,
  preventing localization/rendering code from mutating the shared cache. Complete localization and item search indexes
  remain for the item stage.
- `TarkovPlugin.public_resource()` translates plugin modes and applies the cache setting; downloads reuse the plugin's
  proxy and timeout settings. `terminate()` closes the public cache, cancels pending requests and awaits SQLite workers;
  it now also awaits cancellation of the existing profile tasks. No new status/item/Boss commands or pollers are registered.

### QQ route implementation and evidence

- Adds `qq_official_compat_enabled` (default true) and `command_style=mention_slash`, including schema labels/help-image
  wording. Existing mention/slash styles remain. No global wake-prefix configuration is modified.
- A second standard command handler matches a slash-prefixed tarkov/塔科夫 only on qq_official or
  qq_official_webhook, gated by the compatibility setting before it performs any query. It delegates to the existing
  handler, preserving plugin access rules and active-query guards. It is not a catch-all message/regex listener.
- With '/' in AstrBot wake_prefix, the framework strips it and selects the original handler. If the slash is retained
  after a genuine bot mention (or normal private-chat wake), the supplemental handler matches it. The patterns are
  disjoint, avoiding duplicate handling. Disabling compatibility skips only the supplemental handler; original framework
  routing still works. Other platforms do not acquire the supplemental slash route.
- `tools/verify_qq_routes.py` reads an official AstrBot wheel and executes the original registration, wake stage,
  command/custom filters and QQ parser bodies with external SDK/service boundaries replaced. It validates actual
  decorator registration and handler selection, rather than calling the plugin handler as if routing already worked.
- Verified against the PyPI `astrbot==4.16.0` wheel (SHA256
  `d2374bea6aaba1e27a764194d7163e97bdf65a972138584ebfae05a077dda174`): 33 cases pass, covering both official platform
  names, group/private input, stripped/retained slash, Chinese alias, original no-slash command, fake textual mention,
  a mention of another user, lookalike command/URL, other-platform isolation, compatibility disabled and access denied.
- Reproduce after downloading the wheel: `python tools/verify_qq_routes.py /path/to/astrbot-4.16.0-py3-none-any.whl`.
  Output includes `boundary_doubles: true` and `live_qq_delivery: false`. This is **not** a running AstrBot installation,
  QQ credentials test, WebSocket/Webhook connection, media upload or proactive message delivery verification.

### Related prerequisite and validation

- Fixed §25: `bind()`'s missing-mode hint now excludes modes in `failed`; regression test covers PVE success, PVP timeout
  and season 404. Binding still succeeds, PVP stays a failure message, and only season gets the website-search hint.
- Added public API/cache tests and plugin wiring/QQ/style tests. Rebuilt the bundled Noto Sans SC subset for new
  Chinese error strings after the existing font-coverage test detected missing glyphs. Used the upstream Google Fonts
  source SHA256 `a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`; other bundled fonts/licenses
  were preserved. `fonttools` was installed only as a development tool, not added to runtime requirements.
- Final full suite: **218 tests pass**, `python -m unittest discover -s tests`, Python 3.12.14. Local HTTP test servers
  need network-enabled execution in this sandbox. Tests cover persistence/restart, TTL/304, last-modified fallback,
  independent modes/dictionaries, immutable readers, cancellation, concurrency, byte/entry eviction, cache directives,
  Retry-After, invalid JSON, corruption, proxy plumbing, closing workers and the binding regression.
- Read-only live smoke using the new client/cache with the session proxy passed for `/status` and
  `/regular/items_zh`; both returned valid data, followed by **304** on conditional checks. Temporary responses and the
  wheel/replay report remain under `/tmp`, not packaged. No chat messages were sent.
- `git diff --check` passes. API inventory/design documents and the prior handoff edits remain in the working tree;
  README/checklist are unchanged. Next stage can build the server-status image query and poller on this foundation.

## 30. TarkovTracker data-source research and implementation references (2026-10-24)

Maintainer requested inspection of `https://github.com/tarkovtracker-org/TarkovTracker`, then clarified that existing
implementations relevant to our planned features should serve as references. Read-only GitHub research, pinned to
main commit `d07b3da5d4ebd5c83195fd3f1d6a0b2cd40f91de`; no runtime changes, remote writes or private-data access.

- Deliverable: `docs/tarkovtracker-reference.md`, covering source ownership, actual code paths and a feature-reference
  matrix. README/checklist remain unchanged; work remains on `1024`.
- Public game definitions come from `json.tarkov.dev`, with per-mode base JSON and separate language dictionaries.
  `tarkov-json.ts` fetches/translates/adapts; internal `/api/tarkov/*` routes combine/cache/project and may apply overlay.
  The project no longer uses the old GraphQL service for its game-data path. KV precompute and browser IndexedDB are
  caching/distribution layers, not independent raw game sources.
- Corrections/additions come from a separately hosted `tarkov-data-overlay` JSON. `overlay.ts` names the raw GitHub
  default URL; `overlayAuth.ts` and deployment docs explicitly identify a private read-token dependency. Its payload
  was not retrieved. Reference the merge/version/provenance design without treating it as an available public feed.
- Player import uses the same `players.tarkov.dev` snapshots as our plugin: profile/pve/pvp-season by numeric account
  ID. Tracker's proxy adds caching, conditional refetch, validation and a freshness gate; it does not make these live
  game-account queries. Their own Turnstile/cooldown controls are not a new upstream access grant.
- Actual Tracker progress originates from user edits, local logs/backups and authorized integration writes. Browser
  persistence includes IndexedDB with legacy localStorage recovery; cloud progress is mode/season-scoped Supabase
  `user_game_mode_progress`, with account metadata in `user_progress`, and RPC-based sync. The authenticated public
  `api.tarkovtracker.org` progress API exposes authorized Tracker state, not arbitrary official player progress by ID.
- Assets: `GameItem.vue` uses upstream item image URLs and assets.tarkov.dev icon fallback; local maps.json describes
  coordinate/tile configuration, including assets.tarkov.dev map tiles. These are useful renderer input references,
  not an existing Python/static-image rendering implementation.
- Highest-priority references for our next steps: base+dictionary translation/fallback, raw-to-domain adapters,
  ID-based sparse-reference hydration, lite/full item separation, mode/language/version-sensitive caching, stale
  indicators and snapshot provenance. Preserve our two distinct common-item / mode-item query semantics; no existing
  shared-field extraction was established in the inspected Tracker implementation.
- Full item adaptation retains raw fields (including potentially price data); this alone does not establish a finished
  price-query UI. `adaptMapSpawnsResponse()` only projects map id/name/spawns, not Boss probability. No ready-made
  server-status broadcasts, Boss probability-change broadcasts or QQ integration were found in the inspected paths.
  Continue implementing those from our design instead of equating data availability with a complete feature.
- Their API docs explicitly define `/api/tarkov/*` as internal browser routes and direct external game-data consumers
  to `json.tarkov.dev`. We will reference implementation, not switch to scraping their site proxy. A separate Worker
  API repository is linked by current progress docs; unavailable legacy worker files in this repo were not treated as
  inspected implementation.
- Validation: checked pinned source/docs and source-path links; `git diff --check`. No application test rerun is needed
  for this documentation-only addition. The prior 218-test result belongs to §29 and is not a new run for this research.

## 31. Tracker profile import versus current plugin profile queries (2026-10-24)

Maintainer asked what differs and where Tracker has advantages. Extended `docs/tarkovtracker-reference.md` §6 after
checking the pinned Tracker proxy, import composable, payload parser and cooldown code against current local
`main.py`, `transport.py`, `profile.py`, `accounts.py` and `card.py`. Documentation only; no behavior changes.

- Same snapshot origin and website-refresh dependency. Tracker imports account metadata/skills/derived level into
  its user-progress state after preview/confirmation; our plugin reads/displays statistics and stores only binding/name
  relations, with profile summaries in memory. Tracker's own progress store is not a newly available live game API.
- Tracker defaults: server cache 15 minutes; 404 cache 60 seconds; IP quotas 5/minute and 20/hour; client URL-import
  cooldown 60 minutes after confirmed import. Explicit fresh checks may send ETag and reuse a validated 304 body.
  Turnstile, when configured, protects Tracker's proxy, not the upstream snapshot generation.
- Tracker rejects imports older than 7 days when a valid updated timestamp exists; it checks cache/304 bodies too,
  but missing updated bypasses that age gate. It validates identity and XP more strictly for writes, previews first,
  guards stale asynchronous results and account/mode context, and surfaces save/UID-conflict errors.
- Current plugin profile path: 10-minute default memory summary TTL, 128-entry cap, max 60-second negative TTL,
  shared in-flight fetches and 6 network slots; links/binds skip serving TTL cache but fetch_profile does not supply
  conditional validators. The new §29 persistent public cache is not yet used by player snapshots.
- Our display-oriented path intentionally tolerates many missing statistics and continues showing >30-day snapshots
  with warnings. Fixed-host/no-redirect/gzip size bounds are already present. Do not treat strict whole-document
  rejection or website-specific quotas/confirmation as automatically beneficial to a chat query.
- Proposed borrowings: player-specific ETag checks, requested-ID/response-ID validation for binding/name identity,
  clear separation of snapshot-updated/fetched/checked times on images, and optional per-user/account rate controls.
  Keep stale-data rejection configurable and retain historical-mode viewing. Any future knobs/features must follow
  the agreed settings/image rules. No new implementation is claimed by this research response.
- `git diff --check` validation; README/checklist and runtime code unchanged by this follow-up. No tests rerun for docs.

## 32. Server-status image queries and broadcast foundation (2026-10-24)

Maintainer requested proceeding with status queries and broadcast infrastructure. Implemented on `1024`, building
on §29's public cache and QQ routes. This is working-tree implementation, not a release or deployed bot configuration.
README/checklist remain unchanged. Current usage and limits are also recorded in `docs/development-plan.md` §12.

- Commands: `status` / `状态`, plus stable `tarkov status` / `塔科夫 状态` namespace routes. Use the configured
  AstrBot wake prefix on ordinary platforms; QQ official mention + slash uses the supplemental route. No mode argument
  is needed because the public status feed is global. Existing access/origin restrictions apply to manual queries.
- All new status results are locally rendered JPEGs: success, disabled, denied, invalid arguments, busy and upstream
  errors. They show overall/component state, recent nonempty notices, checked/fetched/resource-version times (GMT+8),
  cache/stale flags and provenance. Unknown status codes remain explicit, never silently healthy. Only rendering failure
  falls back to a brief error sentence. Existing player output migration is outside this stage.
- New default-off settings: `status_enabled`, `status_broadcast_enabled`, `status_qq_push_enabled`. Targets default
  empty (`status_notify_targets`); `status_poll_minutes` defaults to 5, slider/clamp 2–30 minutes. Save/reload applies
  changes. Targets are explicit full UMO strings (`/sid`), deduplicated, max 50, intersected with allowed origins and
  known proactive platform capability. QQ requires the extra switch and suitable actual platform permission.
  Query access mode controls users; administrator-configured targets separately authorize automatic broadcasts.
- `short_commands_enabled` defaults true. `platform_compat.set_short_status_routes()` changes actual AstrBot handler
  metadata, not just early-return guards; disabling it leaves namespace commands and other plugins' `status` routes
  usable. It touches only the two status handlers. The internal registry boundary is verified against SDK 4.16.0.
- `server_status.py` validates/canonicalizes the public feed. Global duplication is removed; component identity uses
  ID with normalized-name fallback. Compare status codes, component addition/removal, nonempty global explanations
  and unseen notices; ignore array ordering/display-name changes. Notice identity includes ID, cleaned content and
  timestamp so revisions are visible. Empty notices are ignored. `public_api.decode()` rejects malformed status data
  before cache replacement, retaining a valid last-good snapshot for explicitly marked manual stale fallback.
- `status_image.py` builds state/change/error cards using bundled fonts and the existing local Pillow style. Status
  images show at most three notice excerpts (400 characters each) with a count/truncation indication. Full normalized
  notices remain available to bounded persistence/deduplication. The CJK subset was rebuilt from the same licensed
  pinned source files; no runtime font download or extra runtime dependency was introduced.
- `polling.py` owns one cancellable, nonoverlapping task for all status targets. No target/disabled means no dedicated
  download or worker. First enabled check is immediate; subsequent delay starts after a cycle finishes, with no catch-up
  calls. Settings may reconfigure the existing task; repeated initialization does not duplicate it. Fetch errors back
  off to at most 30 minutes. Termination cancels/awaits polling, then drains SQLite workers and closes the shared cache.
- `notifications.py` lazily creates separate `notifications.sqlite3`. Successful baseline advancement and immutable
  event creation are atomic. First success is silent; later state/announcement changes enqueue an image event. Failed
  requests and stale/manual queries do not advance the monitor baseline. Restart preserves baseline and per-target
  submission records. A failed later fetch can still drain a previously queued, unexpired event.
- Each target gets at most one event per polling cycle, preserving fault/recovery transitions without a burst.
  Explicit false results/transient exceptions are bounded to three attempts (120/240 second minimum eligibility,
  executed by the configured poll cycle). Already submitted targets are not retried. Explicit permission errors pause
  that target durably across subsequent events/restarts and log a settings/permissions reset hint. Fix permissions,
  disable broadcasts and save/reload, then enable/save/reload to reset. Removing a target retires its pending work;
  disabling broadcasts retires the old baseline/backlog and resets pauses before a new silent baseline.
- Submission semantics: `context.send_message()` true means submitted/matched platform, not a receipt. A `None` result
  is terminal unknown, not delivered or automatically retried. There remains a crash window between remote acceptance
  and local acknowledgement; no exactly-once guarantee. Exceptions are logged by type rather than remote payloads.
- Bounded storage: seven-day event retention (also enforced while draining after fetch failures), 200 event rows,
  64 MiB aggregate event payload, 2048 seen-notice keys. Dedicated quiet-hours/coalescing configuration remains future
  work; the foundation enforces one image per target per polling cycle. Items/Boss monitoring remain future work.

Validation:

- Full suite: **241 tests passed** with the project's Python 3.12 venv. Added normalized-state, unknown code, fault/
  recovery, ordering, notice revisions/reappearance, image/timestamp/stale, transaction rollback, restart persistence,
  target-specific retries/pause/reset, retention, controlled-clock scheduling, cancellation and plugin integration
  coverage. A malformed HTTP 200 cannot overwrite last-good status or swallow a transition. Real disk/HTTP cache
  and fake platform submission paths cover 200 → changed 200 → 304 without duplicate submission.
- `tools/verify_qq_routes.py` against the official AstrBot 4.16.0 wheel: **67 cases passed**, including short/namespaced
  state commands, both QQ official transports, group/private, differing wake prefixes, permission/feature gates and
  another plugin's short command when ours is disabled. SDK/network boundaries remain doubled; no QQ delivery claim.
- Live read-only smoke: two status requests via the configured session proxy, first uncached then conditional cache
  reuse, `stale=false`, nine distinct components. Rendered and visually inspected the status and error images; the
  status preview was 174,485 bytes. No broadcast task or notification DB was created for manual-only use. Temporary
  previews/report/scripts remain under `/tmp`; no chat messages were sent, and no external writes were performed.
- `git diff --check` passes. QQ passive image delivery, proactive permission and real platform rejection behavior
  still require bot-side acceptance testing. No commit/push or release metadata change was made.

## 33. Boss monitoring, reference matrix and manual portrait cache (2026-10-24)

Implemented the Boss module before item queries at the maintainer's request. Final scope, reference-image layout
and portrait-update behavior below incorporate all in-turn corrections. Work remains on `1024`, without commit,
push or release. README/checklist are unchanged. See `docs/development-plan.md` §§6/13 for current design/usage.
The later map-column selection in §34 supersedes this section's original sixteen-map scope.

- Scope: the eleven named daily bosses (Tagilla, Reshala, Shturman, Sanitar, Killa, Glukhar, Zryachiy, Kaban,
  Kollontay, Partisan and sectant priest), plus GOONS. Use a stable mob-ID allowlist; `bossKnight` represents squad
  probability and displays as `三狗（GOONS）`. Exclude ordinary BEAR/USEC, followers and unlisted event variants.
  Exclude Icebreaker by stable map ID or normalized name; keep other maps, including columns with no listed boss.
- Commands: `boss`/`刷率`, optional pvp/pve/season, optional `变化`/`changes`; namespace `tarkov boss ...`;
  immutable continuation `boss 页 <12-hex-token> <page>`. QQ official mention+slash routes reuse the proven SDK
  filter path. The short-command setting changes actual metadata for all four status/Boss short handlers; namespace
  routes and another plugin's standard short commands remain available when ours are disabled.
- Settings: `boss_enabled`, `boss_broadcast_enabled`, `boss_qq_push_enabled` default false; targets default empty,
  monitor/default-query modes pvp/pve/season. `boss_poll_minutes` defaults 10, independent 2–30 minute slider/clamp.
  Save/reload applies settings. Targets use explicit UMO validation (max 50), origin restrictions and known proactive
  platform capability. Boss and status QQ permissions are independent and do not grant platform permission.
- `boss_monitor.py`: preserve conditional records/multiplicity and compare stable map+mob+rule identities before
  probability pairs; never sum conditions. Rule changes remain distinct internally, but conditions are not printed.
  Ignore array order, positions, images, translations and escort equipment. Require finite numeric 0–1 probabilities,
  rejecting booleans/nulls. Normalize float tails to twelve decimal places while preserving values just below 100%.
  Base maps are validated before public-cache publication; malformed 200 cannot poison the last-good resource.
- `boss_table_image.py`: the maintainer's dark reference matrix, Boss rows, map columns, mode in the top-left cell,
  portraits/name in the left column. Fixed twelve Boss rows, at most sixteen map columns/page, repeated Boss rows
  when more map columns need pagination. No refresh-condition, previous-rate or change-explanation columns.
  Distinct same-cell rates stay separate, equal rates may display once, raw comparison retains all records.
  Unlisted cells stay blank, explicit 0% is printed; an observed removal can be a red dash. Changes are red; exact
  100% and the corresponding Boss name are always bold red, including unchanged/first snapshots. Other values in
  mixed cells keep their own style. Width is column-dependent (standard 16-map matrix: 2240px), normal cell font 22px.
- New errors are image replies; rendering failure alone returns a short error sentence. Metadata/footer carries
  mode/page, checked/fetched/compared/upstream version (GMT+8), stale/cache notes and provenance. Query replies show
  one page with immutable continuation when needed. All selected maps remain retrievable. Query snapshots are
  bounded to 24 hours, sixteen groups, 32 MiB payload; capacity pressure can evict earlier. Manual/304 queries do not
  rewrite the last monitor event's highlights or comparison time. All default/current maps fit one matrix per mode.
- `boss_portraits.py`: the API exposes `data.mobs[BossID].imagePortraitLink` and `imagePosterLink`. Portrait paths
  are fixed from those known upstream links under assets.tarkov.dev, never arbitrary feed/user URLs. GOONS uses the
  Knight portrait. Artwork is not bundled in the repository/package; THIRD_PARTY now attributes runtime game art.
- **Manual-only portraits**: `boss_portraits_update` defaults false. An administrator opens this one-shot setting
  and saves/reloads; initialize clears and persists the flag before network work, then manually updates all twelve.
  Retry/force update requires another explicit settings action. Ordinary query/poll/startup and cache expiry never
  download portraits. Local sanitized thumbnails expire after 365 days, including memory/disk checks; missing or
  expired entries use a drawn placeholder. Failed updates preserve still-valid old thumbnails. This cache is separate
  from public game-data TTL and does not create any background refresh interval.
- Portrait transport reuses proxy/no-redirect/timeout/decoded-size protections, three download slots, at most twelve
  fixed files, 128px RGBA sanitized PNG thumbnails. Accept PNG/WebP/JPEG and application/octet-stream (the live source
  labels seven PNGs with that generic MIME), then require actual Pillow image decoding and bounded dimensions.
  JSON callers retain their strict default MIME. No new runtime dependency; CJK subset rebuilt from pinned sources.
- `boss_notifications.py`: separate `boss-notifications.sqlite3`, reusing cancellation-safe worker draining. Atomic
  mode baseline/event writes, immutable matrix groups, per-target/page states, paused targets and query snapshots.
  First successful mode observation is silent; adding modes starts silently, removing modes retires their backlog.
  Scope/layout policy is `daily-goons-no-icebreaker-matrix-v3`: opening older Boss data retires old baselines, events
  and query snapshots before use, preserving permission pauses. Filtering/layout changes are not rate-change alerts.
  Status notifications, account bindings, raw public resources and valid portraits are unaffected by the migration.
- One cancellable/nonoverlapping Boss poller serves all targets, fresh conditional checks independent of manual TTL.
  Equivalent bodies reuse normalized state. Fetch/format/stale failure does not advance baselines. Successful modes
  can enqueue/submit while another fails, with whole-cycle backoff. Disabled/no effective modes or targets means no
  dedicated Boss poller. Shutdown awaits pollers, database workers, explicit portrait work and local I/O workers.
- At most five pages per target per cycle, sharing the budget across modes in event/page order; the current three
  one-page mode events can submit in one cycle. Temporary failure blocks later pages for that target and retries at
  most three times; submitted pages are not resent. Permission failures pause that target durably. True means
  submitted/matched platform, not receipt; None is terminal unknown for that page, never complete delivery. Disable/
  save/reload then enable/save/reload resets pause/backlog and starts a silent baseline. Events are bounded to seven
  days, 200 and 64 MiB payload. Quiet-hours/coalescing and exactly-once delivery are not implemented.

Validation:

- **275 tests passed** with the project Python 3.12 venv: scope/GOONS/Icebreaker, dark matrix axes/all fixed Boss rows,
  mixed rates and exact 100% styling, immutable column paging, policy migration, atomic rollback/restart, mode
  isolation, target/page retry and pause/unknown states, cross-mode page budgets, cache 200→changed 200→304 and
  malformed fallback, lifecycle, manual portrait trigger/persistence/reset, 365-day memory/disk expiry with no
  download, explicit update failure, image MIME/decode bounds and cancellation. Package remains under 1 MiB.
- AstrBot 4.16.0 original registration/wake/filter/parser replay: **101 cases passed** across both QQ transports,
  group/private, slash/namespace/localized commands, differing prefixes, access/settings gates and short-command
  opt-out coexistence. External boundaries are doubles; real QQ image/proactive delivery still needs bot acceptance.
- Live read-only smoke: base maps plus zh/en and conditional rechecks for all three modes, no stale/translation
  warnings, normalized-state reuse. Sixteen included maps per mode; pvp/pve/season yielded 29/29/27 original selected
  records, one matrix per mode, two exact-100% records per mode. Explicit portrait-update action was exercised in a
  temporary fixture configuration; ordinary image preparation only reads its local cache. Checked matrix previews
  against the reference, without BEAR/USEC, Icebreaker or removed detail columns. No chat messages were sent.
  Previews/scripts/replay reports remain under `/tmp`; no downloaded portraits are packaged.
- `git diff --check` passes. README/checklist unchanged. Item queries and legacy-player full image migration remain
  future work. No commit/push/release metadata change or deployed configuration update was performed.

## 34. Boss map selection and Ground Zero label (2026-10-24)

Maintainer requested removing the original Ground Zero, Lab Dark, Labyrinth and Ground Zero tutorial, and renaming
the retained Ground Zero 21+ column to `中心区`. Icebreaker remains excluded. Applied to shared normalization,
monitoring and matrix headers, with settings hints and `docs/development-plan.md` updated; README/checklist unchanged.

- Excluded map IDs: `653e6760052c01c1c805532f` (original Ground Zero), `6a294a5b5eb5f9a1700417b7` (Lab Dark),
  `6733700029c367a3d40b02af` (Labyrinth), `68236e8153654e8c1200798a` (tutorial), plus the existing Icebreaker ID.
  Equivalent normalized names are excluded too. Retain `65b8d6f5cdde2479cb2a3125` / `ground-zero-21`; both normalized
  display name and matrix header are `中心区`. Its own probabilities remain intact; do not merge the removed map.
- Current columns: 中心区、白天工厂、夜间工厂、海关、森林、海岸线、立交桥、储备站、灯塔、塔科夫街区、实验室、码头.
  The current twelve-map matrix is 1760px wide. Boss rows, red/bold rules and manual-only 365-day portraits are unchanged.
- Scope policy is now `daily-goons-selected-maps-matrix-v4`. Existing migration retires older Boss baselines, events
  and query snapshots, so removed columns cannot return through history/continuation and scope changes do not emit
  false removal alerts. Re-establish the normal silent baseline; account/status data and portraits remain untouched.
- Extended the existing scope regression to cover all excluded IDs and normalized-name aliases, retained 21+ identity,
  its 2% synthetic value, and resulting headers. Full suite: **275 passed**. Checked the existing cached real regular
  maps/zh response offline: twelve columns, one matrix, center data still from the retained 21+ ID. No portrait or
  upstream downloads, chat messages, commit or push were performed for this map-only change. `git diff --check` passes.

## 35. Publish accumulated work on branch 1024 (2026-10-24)

Maintainer explicitly requested pushing `1024` to GitHub after finding no remote branch. Checked the local branch
and GitHub: `1024` was local at `966d0fc` with the accumulated §§29–34 implementation/documentation uncommitted;
no matching remote branch existed. This commit packages the public cache, QQ routing, status queries/broadcasts,
Boss monitoring/matrix/manual portraits, latest twelve-map scope, research/design notes and regression coverage.

- Publication target: `https://github.com/EvergardenRoxy/astrbot_tarkov_datasearch/tree/1024`, through origin with
  upstream tracking. Use a normal push and verify the remote head against the local commit; no force push or merge.
- Validation reused from the completed implementation: 275 tests passed after the final map selection; the most
  recent SDK routing replay passed 101 cases. No runtime code changed during publication preparation, so these
  checks were not unnecessarily repeated. Staged whitespace validation is checked before committing.
- README/checklist remain untouched. Temporary research/downloads, portrait caches, SQLite files and generated
  distributions are not included. No pull request or release was requested as part of this branch publication.
