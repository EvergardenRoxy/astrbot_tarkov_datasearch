# Third-party attribution

**Player data.** Profiles and name indexes are read at run time from `https://players.tarkov.dev`, the public player
profile snapshots published by the tarkov.dev project (the-hideout, https://github.com/the-hideout). No player data is
bundled with the plugin. tarkov.dev states that player profile data comes from a source it does not disclose; the
plugin only reads what tarkov.dev already publishes and does not use its Turnstile-protected live API
(`player.tarkov.dev`).

**Static tables.** `tarkov_core/data/static.json` (experience per player level, skill ids with English and Chinese
names) was extracted from `https://json.tarkov.dev/regular/items` and its translation file `items_zh` by
`tools/build_static.py` on the date recorded in the file. One translation is overridden in the script (`Lockpicking`).
The data describes game content of Escape from Tarkov (Battlestate Games); it is used for display only.

**Behaviour references, no code copied.** Field meanings (level calculation, raid counters, skill level and filter,
"last active") follow tarkov.dev's player page `src/pages/player/index.jsx` in https://github.com/the-hideout/tarkov-dev
(revision `97ff6e7a43d03c10f3f7a3165c8dbcdf2193d6b9`). The players.tarkov.dev URL layout (`profile/index.json`,
`{mode}/{accountId}.json`, regular mode stored as `profile`) follows `TarkovDev.cs` and `GameWatcher.cs` in
https://github.com/the-hideout/TarkovMonitor (revision `3cf361efc49fecf6bffce3cae567f55a303757cd`).

This plugin is not affiliated with Battlestate Games or tarkov.dev. Plugin source is distributed under
GPL-3.0-or-later (see LICENSE). aiohttp is a runtime dependency, not vendored; its own license applies.
