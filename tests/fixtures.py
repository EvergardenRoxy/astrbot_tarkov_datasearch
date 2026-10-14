"""Synthetic profiles shaped like players.tarkov.dev's (fields as of 2026-10-13). Every value is invented: the numbers
are round on purpose, so they cannot be mistaken for (or come from) a real profile."""
import copy

BASE = {
    'aid': 1234567,
    'info': {'nickname': 'Test_Player', 'side': 'Usec', 'experience': 12000000, 'memberCategory': 2,
             'selectedMemberCategory': 2, 'prestigeLevel': 0, 'prestigeGameMode': 'regular',
             'leagueRank': None, 'leagueMaxRank': None},
    'customization': {'head': 'h', 'body': 'b', 'feet': 'f', 'hands': 'x'},
    'skills': {'Common': [
        {'Id': 'BotReload', 'Progress': 0, 'PointsEarnedDuringSession': 0, 'LastAccess': -2147483648},
        # Not in the static skill table: skipped.
        {'Id': 'BotSound', 'Progress': 100, 'PointsEarnedDuringSession': 0, 'LastAccess': 1760000000},
        {'Id': 'Endurance', 'Progress': 5100, 'PointsEarnedDuringSession': 0, 'LastAccess': 1780000000},
        {'Id': 'Strength', 'Progress': 5100, 'PointsEarnedDuringSession': 0, 'LastAccess': 1781000000},
        {'Id': 'Vitality', 'Progress': 2500.5, 'PointsEarnedDuringSession': 0, 'LastAccess': 1790500000},
        {'Id': 'Charisma', 'Progress': 4000.25, 'PointsEarnedDuringSession': 0, 'LastAccess': 1790400000},
        # Progress but never accessed: skipped, as on tarkov.dev.
        {'Id': 'Pistol', 'Progress': 300, 'PointsEarnedDuringSession': 0, 'LastAccess': 0},
    ], 'Mastering': [{'Id': 'AKM', 'Progress': 2126, 'Kills': 0}], 'Points': 0},
    'equipment': {'Id': 'e', 'Items': []},
    'achievements': {'a1': 1760000000, 'a2': 1790600000},
    'favoriteItems': [],
    'pmcStats': {'eft': {'totalInGameTime': 3600000, 'overAllCounters': {'Items': [
        {'Key': ['Sessions', 'Pmc'], 'Value': 1000},
        {'Key': ['ExitStatus', 'Survived', 'Pmc'], 'Value': 800},
        {'Key': ['Kills'], 'Value': 5000},
        {'Key': ['Deaths'], 'Value': 100},
        {'Key': ['KilledPmc'], 'Value': 900},
        {'Key': ['LongestWinStreak', 'Pmc'], 'Value': 40},
        {'Key': ['ExitStatus', 'Killed', 'Pmc'], 'Value': 120},
        {'Key': ['ExitStatus', 'Runner', 'Pmc'], 'Value': 30},
        {'Key': ['ExitStatus', 'Transit', 'Pmc'], 'Value': 40},
        {'Key': ['ExitStatus', 'MissingInAction', 'Pmc'], 'Value': 10},
    ]}}},
    'scavStats': {'eft': {'totalInGameTime': 3600000, 'overAllCounters': {'Items': [
        {'Key': ['Sessions', 'Scav'], 'Value': 50},
        {'Key': ['ExitStatus', 'Survived', 'Scav'], 'Value': 30},
        {'Key': ['Kills'], 'Value': 100},
        {'Key': ['Deaths'], 'Value': 10},
        {'Key': ['KilledPmc'], 'Value': 20},
        {'Key': ['LongestWinStreak', 'Scav'], 'Value': 5},
        {'Key': ['ExitStatus', 'Killed', 'Scav'], 'Value': 15},
        {'Key': ['ExitStatus', 'Runner', 'Scav'], 'Value': 5},
    ]}}},
    'ending': {'current': None, 'achieved': []},
    'hideout': None, 'hideoutAreaStashes': None, 'items': None, 'customizationStash': None,
    'updated': 1791000000000,
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


ARENA = {
    'aid': 1234567,
    'info': {'nickname': 'Test_Player', 'side': 'Usec', 'experience': 500000, 'battlePassesLevels': None,
             'memberCategory': 2, 'selectedMemberCategory': 2, 'registrationDate': 1700000000},
    'presetCustomization': {}, 'profileCustomization': {},
    'achievements': {'b1': 1785000000, 'b2': 1789000000},
    'stat': {'totalInGameTime': 360000, 'arenaOverAllCounters': {
        'UnrankedOverall': {'Counters': {'GamesCount': 200, 'Kills': 1500, 'Deaths': 600, 'BestArp': 1200,
                                         'MaxKillsWithoutDeaths': 12, 'LongestWinStreak': 7, 'KillsWithoutDeaths': 3,
                                         'LoseStreak': 1, 'LongestLoseStreak': 5}},
        'UnrankedShootOutDuo': {'Counters': {'GamesCount': 10, 'ArenaWins': 5, 'Kills': 50, 'Deaths': 20}},
        'UnrankedTeamFight': {'Counters': {'GamesCount': 100, 'ArenaWins': 60, 'Kills': 800, 'Deaths': 300, 'MatchMvpCount': 10}},
        'UnrankedLastHero': {'Counters': {'GamesCount': 50, 'ArenaWins': 10, 'Kills': 400, 'Deaths': 200, 'MatchMvpCount': 4}},
        'UnrankedCheckPoint': {'Counters': {'GamesCount': 40, 'ArenaWins': 20, 'Kills': 250, 'Deaths': 80, 'MatchMvpCount': 3}},
        'Overall': {'Counters': {'GamesCount': 30, 'Kills': 200, 'Deaths': 100, 'BestArp': 1500}},
        'TeamFight': {'Counters': {'GamesCount': 20, 'ArenaWins': 12, 'ArenaLoses': 8, 'Kills': 150, 'Deaths': 70, 'MatchMvpCount': 2}},
        'ShootOutDuo': {'Counters': {'GamesCount': 10, 'ArenaWins': 4, 'ArenaLoses': 6, 'Kills': 50, 'Deaths': 30}},
    }},
    'equipment': {'Id': 'e', 'Items': []},
    'updated': 1791000000000,
}


def arena_profile(**changes):
    """A copy of ARENA (invented values, shaped like players.tarkov.dev's arena/<aid>.json); same rules as profile()."""
    data = copy.deepcopy(ARENA)
    for key, value in changes.items():
        if key.startswith('info_'):
            data['info'][key[5:]] = value
        else:
            data[key] = value
    return data
