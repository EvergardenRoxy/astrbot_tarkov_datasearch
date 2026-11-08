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


# Atlas documents (shapes from the 2026-11-08 probes, docs/atlas-probe-record.md §7; every value invented and round).
ATLAS_PROFILE = {
    'nickname': 'Test_Player', 'side': 'Usec', 'experience': 12000000, 'prestigeLevel': 0,
    'slots': [{'slot': 'FaceCover', 'tpl': 't', 'en': {'name': 'n', 'short': 's'}, 'ch': {'name': 'n', 'short': 's'},
               'children': [], 'icon': 'https://assets.tarkov.dev/t-icon.webp', 'render': None, 'armor_class': None}],
    'favorites': [], 'level': 50, 'levelExp': {'current': 11000000, 'next': 13000000},
    'memberCategory': 2, 'selectedMemberCategory': 2, 'league': None, 'ending': None, 'endingsReached': [],
    'prestigeGameMode': 'pve',
    'stats': {
        'pmc': {'time': 3600000, 'counters': [
            [['Sessions', 'Pmc'], 1000], [['ExitStatus', 'Survived', 'Pmc'], 800], [['Kills'], 5000], [['Deaths'], 100],
            [['KilledPmc'], 900], [['LongestWinStreak', 'Pmc'], 40], [['ExitStatus', 'Killed', 'Pmc'], 120],
            [['ExitStatus', 'Runner', 'Pmc'], 30], [['ExitStatus', 'Transit', 'Pmc'], 40],
            [['ExitStatus', 'MissingInAction', 'Pmc'], 10]]},
        'scav': {'time': 3600000, 'counters': [
            [['Sessions', 'Scav'], 50], [['ExitStatus', 'Survived', 'Scav'], 30], [['Kills'], 100], [['Deaths'], 10],
            [['KilledPmc'], 20], [['LongestWinStreak', 'Scav'], 5], [['ExitStatus', 'Killed', 'Scav'], 15],
            [['ExitStatus', 'Runner', 'Scav'], 5]]},
    },
    'achievements': {'count': 2, 'total': 100, 'done': [
        {'id': 'a1', 'at': 1760000000, 'rarity': 'Common', 'percent': 10, 'en': {'name': 'A'}, 'ch': {'name': 'A'}},
        {'id': 'a2', 'at': 1790600000, 'rarity': 'Rare', 'percent': 1, 'en': {'name': 'B'}, 'ch': {'name': 'B'}}]},
    'skills': {'common': [
        {'id': 'Endurance', 'name': {'en': 'Endurance', 'ch': '耐力'}, 'progress': 5100, 'lastAccess': 1780000000},
        {'id': 'Strength', 'name': {'en': 'Strength', 'ch': '力量'}, 'progress': 2500, 'lastAccess': 1790500000},
        {'id': 'BotReload', 'name': {'en': 'x', 'ch': 'x'}, 'progress': 0, 'lastAccess': -2147483648}],
        'mastering': [{'id': 'AKM', 'progress': 2000, 'kills': 10, 'templates': ['t']}]},
    'perks': {'common': [], 'personal': []},
    'battlePass': {'completed': 0, 'total': 50}, 'seasonalRewards': {'completed': 0, 'total': 5},
    'accountId': '1234567', 'looked_up': '2026-11-01T04:00:00+00:00', 'client_version': '1.0.0.0.10000',
    'cached': False, 'mode': 'pve',
}

ATLAS_ARENA = {
    'nickname': 'Test_Player', 'side': 'Usec', 'experience': 500000, 'prestigeLevel': None, 'slots': [],
    'favorites': [], 'level': 20, 'levelExp': {'current': 400000, 'next': 600000},
    'memberCategory': 2, 'selectedMemberCategory': 2,
    'achievements': {'count': 1, 'total': 80, 'done': [{'id': 'b1', 'at': 1785000000}]},
    'accountId': '1234567', 'looked_up': '2026-11-01T04:00:00+00:00', 'client_version': '0.1.0.0.10000',
    'cached': False, 'mode': 'arena', 'registered': 1700000000,
    'arenaStats': {'time': 360000, 'modes': {
        'UnrankedOverall': {'GamesCount': 200, 'Kills': 1500, 'Deaths': 600, 'BestArp': 1200, 'MaxKillsWithoutDeaths': 12,
                            'LongestWinStreak': 7},
        'UnrankedTeamFight': {'GamesCount': 100, 'ArenaWins': 60, 'ArenaLoses': 40, 'Kills': 800, 'Deaths': 300,
                              'MatchMvpCount': 10},
        'UnrankedShootOutDuo': {'GamesCount': 10, 'ArenaWins': 5, 'Kills': 50, 'Deaths': 20},
    }},
    'battlePasses': [{'id': 'p1', 'level': 10}, {'id': 'p2', 'level': 20, 'max': 100, 'title': 'Season 1', 'current': True}],
}


def atlas_profile(mode='pve', **changes):
    """A copy of ATLAS_PROFILE (or ATLAS_ARENA for mode='arena') in `mode`; keyword arguments replace fields."""
    data = copy.deepcopy(ATLAS_ARENA if mode == 'arena' else ATLAS_PROFILE)
    data['mode'] = mode
    data.update(changes)
    return data
