"""Game modes and how players.tarkov.dev names them."""

# key -> (path segment on players.tarkov.dev, label shown in chat). The regular PvP mode is stored under
# "profile", not "regular" (TarkovMonitor GameWatcher.cs ToPlayersApiString; probed: /regular/<aid>.json is 404).
MODES = {
    'pvp': ('profile', 'PVP'),
    'pve': ('pve', 'PVE'),
    'season': ('pvp-season', 'PVP 赛季'),
}

ALIASES = {
    'pvp': 'pvp', 'regular': 'pvp', '常规': 'pvp', '普通': 'pvp',
    'pve': 'pve',
    'season': 'season', 'pvp-season': 'season', '赛季': 'season',
}


def parse_mode(token):
    """The mode key for a word the user typed, or None when it is not a mode."""
    return ALIASES.get(str(token or '').strip().lower())


def path_of(mode):
    return MODES[mode][0]


def label_of(mode):
    return MODES[mode][1]
