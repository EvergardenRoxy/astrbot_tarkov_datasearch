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

**Fonts** (in `tarkov_core/fonts/`, each with its licence text next to it; all SIL Open Font License 1.1, from
https://github.com/google/fonts, rebuilt by `tools/build_fonts.py`):
- `NotoSansSC-Subset.ttf`: a subset of Noto Sans SC (variable), Copyright 2014-2021 Adobe, with Reserved Font Name
  'Source'. Cut down to the characters listed in `NotoSansSC-Subset.chars.txt`; hinting removed. Source file sha256
  `a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`.
- `Oswald-Variable.ttf`: Oswald (variable), Copyright 2016 The Oswald Project Authors, unmodified (file renamed).
- `ShareTechMono-Regular.ttf`: Share Tech Mono, Copyright (c) 2012 Carrois Type Design, Ralph du Carrois, with
  Reserved Font Name 'Share', unmodified (a modified copy could not keep the reserved name).

**Boss table background.** `tarkov_core/assets/boss-background.jpg` is an AI-generated texture (no game art, text or logos)
supplied by the project maintainer, who states it was generated with GPT-Image-2.5-sunburst. According to the maintainer, the
provider's terms do not restrict use of the generated content in open-source software, and the image carries a hidden,
traceable provider watermark. The bundled file is a JPEG re-encoded from the generator's original (WebP) output, so the
watermark may not survive; nothing in the plugin reads or removes it. The terms themselves are not reproduced here; check
the provider's current terms if the use changes.

**Drawings and Boss portraits.** Player/status cards draw their game references themselves (a military dog tag,
exit-status bars, a dossier page). The Boss matrix can display game character portraits identified by
`data.mobs.*.imagePortraitLink` in `https://json.tarkov.dev/{mode}/maps`, served by `https://assets.tarkov.dev`.
Portraits are game artwork of Battlestate Games; they are not bundled in this GPL source/package. An explicit
administrator settings action downloads fixed known portrait paths into the plugin's local runtime data directory,
sanitizes them to small thumbnails and caches them for 365 days. Ordinary queries never download portraits. This
attribution does not relicense the artwork. Missing/expired portraits use a locally drawn placeholder.

**Documentation pictures with game artwork.** `docs/boss_preview.jpg` and `docs/boss_changes_preview.jpg` show the Boss
portraits (Battlestate Games artwork served by assets.tarkov.dev) exactly as the plugin draws them after an administrator
has run the manual portrait update. They are in the repository only to illustrate the plugin's output; the artwork remains
the property of Battlestate Games, is not covered by the GPL that applies to the plugin's source, is not part of the
plugin package (`docs/` is not packaged) and must not be reused separately. The maintainer accepted this when asking for
real-data examples. Regenerate with `tools/build_public_preview.py`.

This plugin is not affiliated with Battlestate Games or tarkov.dev. Plugin source is distributed under
GPL-3.0-or-later (see LICENSE). aiohttp is a runtime dependency, not vendored; its own license applies.
