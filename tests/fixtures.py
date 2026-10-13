"""Synthetic profiles shaped like players.tarkov.dev's (fields as of 2026-10-13); no real player's data."""
import copy

BASE = {
    'aid': 1234567,
    'info': {'nickname': 'Test_Player', 'side': 'Usec', 'experience': 29213830, 'memberCategory': 2,
             'selectedMemberCategory': 2, 'prestigeLevel': 0, 'prestigeGameMode': 'regular',
             'leagueRank': None, 'leagueMaxRank': None},
    'customization': {'head': 'h', 'body': 'b', 'feet': 'f', 'hands': 'x'},
    'skills': {'Common': [
        {'Id': 'BotReload', 'Progress': 0, 'PointsEarnedDuringSession': 0, 'LastAccess': -2147483648},
        # Not in the static skill table: skipped.
        {'Id': 'BotSound', 'Progress': 100, 'PointsEarnedDuringSession': 0, 'LastAccess': 1765731361},
        {'Id': 'Endurance', 'Progress': 5100, 'PointsEarnedDuringSession': 0, 'LastAccess': 1785639984},
        {'Id': 'Strength', 'Progress': 5100, 'PointsEarnedDuringSession': 0, 'LastAccess': 1780386615},
        {'Id': 'Vitality', 'Progress': 2992.30566, 'PointsEarnedDuringSession': 0, 'LastAccess': 1791112546},
        {'Id': 'Charisma', 'Progress': 4826.879, 'PointsEarnedDuringSession': 0, 'LastAccess': 1791108938},
        # Progress but never accessed: skipped, as on tarkov.dev.
        {'Id': 'Pistol', 'Progress': 300, 'PointsEarnedDuringSession': 0, 'LastAccess': 0},
    ], 'Mastering': [{'Id': 'AKM', 'Progress': 2126, 'Kills': 0}], 'Points': 0},
    'equipment': {'Id': 'e', 'Items': []},
    'achievements': {'a1': 1765742064, 'a2': 1791113100},
    'favoriteItems': [],
    'pmcStats': {'eft': {'totalInGameTime': 4698849, 'overAllCounters': {'Items': [
        {'Key': ['Sessions', 'Pmc'], 'Value': 1472},
        {'Key': ['ExitStatus', 'Survived', 'Pmc'], 'Value': 1234},
        {'Key': ['Kills'], 'Value': 12115},
        {'Key': ['Deaths'], 'Value': 164},
        {'Key': ['KilledPmc'], 'Value': 1894},
        {'Key': ['LongestWinStreak', 'Pmc'], 'Value': 60},
        {'Key': ['ExitStatus', 'Killed', 'Pmc'], 'Value': 155},
        {'Key': ['ExitStatus', 'Runner', 'Pmc'], 'Value': 27},
        {'Key': ['ExitStatus', 'Transit', 'Pmc'], 'Value': 55},
        {'Key': ['ExitStatus', 'MissingInAction', 'Pmc'], 'Value': 1},
    ]}}},
    'scavStats': {'eft': {'totalInGameTime': 4698849, 'overAllCounters': {'Items': [
        {'Key': ['Sessions', 'Scav'], 'Value': 58},
        {'Key': ['ExitStatus', 'Survived', 'Scav'], 'Value': 42},
        {'Key': ['Kills'], 'Value': 110},
        {'Key': ['Deaths'], 'Value': 15},
        {'Key': ['KilledPmc'], 'Value': 66},
        {'Key': ['LongestWinStreak', 'Scav'], 'Value': 8},
        {'Key': ['ExitStatus', 'Killed', 'Scav'], 'Value': 15},
        {'Key': ['ExitStatus', 'Runner', 'Scav'], 'Value': 1},
    ]}}},
    'ending': {'current': None, 'achieved': []},
    'hideout': None, 'hideoutAreaStashes': None, 'items': None, 'customizationStash': None,
    'updated': 1791903208537,
}


def profile(**changes):
    """A copy of BASE; keyword arguments replace top-level fields, info_<field> replaces an info field."""
    data = copy.deepcopy(BASE)
    for key, value in changes.items():
        if key.startswith('info_'):
            data['info'][key[5:]] = value
        else:
            data[key] = value
    return data
