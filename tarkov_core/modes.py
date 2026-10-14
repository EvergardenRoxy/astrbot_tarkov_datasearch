"""Game modes, how players.tarkov.dev and the tarkov.dev website name them, and player-page links."""
import re

# key -> (path segment on players.tarkov.dev, label shown in chat, mode segment of a tarkov.dev player page).
# The regular PvP mode is stored under "profile" on players.tarkov.dev (TarkovMonitor GameWatcher.cs
# ToPlayersApiString; probed: /regular/<aid>.json is 404) but is "regular" in website URLs (tarkov-dev
# src/pages/player/index.jsx mode toggle: regular / pve / pvp-season / arena).
MODES = {
    'pvp': ('profile', 'PVP', 'regular'),
    'pve': ('pve', 'PVE', 'pve'),
    'season': ('pvp-season', 'PVP 赛季', 'pvp-season'),
    # Tarkov Arena: a separate game with the same account ids (handoff §19.2); looked at only when asked for, or when
    # the "arena_by_default" setting adds it to queries without a mode.
    'arena': ('arena', '竞技场', 'arena'),
}
# The main game's modes, looked at together when a query names no mode.
GAME_MODES = ('pvp', 'pve', 'season')

ALIASES = {
    'pvp': 'pvp', 'regular': 'pvp', '常规': 'pvp', '普通': 'pvp',
    'pve': 'pve',
    'season': 'season', 'pvp-season': 'season', '赛季': 'season',
    'arena': 'arena', '竞技场': 'arena',
}

# A tarkov.dev player page: /players/<mode>/<account id>, or the older /player/<account id> (regular mode).
PLAYER_URL = re.compile(r'(?:https?://)?(?:www\.)?tarkov\.dev/(?:players/([a-z-]+)|player)/(\d{1,12})/?', re.I)
SITE_MODES = {site: key for key, (_, _, site) in MODES.items()}


def parse_mode(token):
    """The mode key for a word the user typed, or None when it is not a mode."""
    return ALIASES.get(str(token or '').strip().lower())


def path_of(mode):
    return MODES[mode][0]


def label_of(mode):
    return MODES[mode][1]


def page_url(mode, aid):
    """The tarkov.dev page of a player; opening it refreshes the snapshot the plugin reads."""
    return f'https://tarkov.dev/players/{MODES[mode][2]}/{int(aid)}'


def search_url(mode):
    """The tarkov.dev player search page in a mode (it takes no name parameter, the name is typed there)."""
    return f'https://tarkov.dev/players?gameMode={MODES[mode][2]}'


def parse_player_url(text):
    """(mode key or None, account id) for a tarkov.dev player page link, or None when `text` is not one.

    The mode is None for a page mode the plugin does not know."""
    match = PLAYER_URL.fullmatch(str(text).strip())
    if not match:
        return None
    site = (match.group(1) or 'regular').lower()
    return SITE_MODES.get(site), int(match.group(2))
