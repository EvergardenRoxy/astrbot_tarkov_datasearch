"""Grouped settings (AstrBot shows nested `object` groups as sections) read through the flat key names the code uses."""
from collections.abc import MutableMapping

GROUPS = {
    'general': ('command_style', 'qq_single_word_commands', 'access_mode', 'user_whitelist', 'allowed_origins',
                'proxy_enabled', 'proxy_url', 'public_data_cache_minutes'),
    'player': ('player_source', 'default_mode', 'arena_by_default', 'skill_count', 'profile_cache_minutes',
               'name_index_enabled', 'name_index_refresh_hours', 'download_timeout_seconds'),
    'atlas': ('matching_enabled', 'atlas_query_cooldown_seconds',
              'user_query_cooldown_seconds', 'user_refresh_cooldown_seconds', 'atlas_budget_per_minute',
              'atlas_budget_per_hour', 'atlas_assets_update', 'atlas_assets_clear', 'font_download_url'),
    'status': ('status_enabled', 'status_broadcast_enabled', 'status_poll_minutes', 'status_notify_targets',
               'status_qq_push_enabled'),
    'boss': ('boss_enabled', 'boss_broadcast_enabled', 'boss_poll_minutes', 'boss_modes', 'boss_notify_targets',
             'boss_qq_push_enabled', 'boss_event_ignore', 'boss_portraits_update'),
    'weekly': ('weekly_enabled', 'weekly_keep_days', 'weekly_push_enabled', 'weekly_push_weekday', 'weekly_push_time'),
}
GROUP_OF = {key: group for group, keys in GROUPS.items() for key in keys}
# Whole-number settings: key -> (default, lowest, highest). A value outside is moved to the nearest bound.
INTS = {
    'public_data_cache_minutes': (5, 0, 1440), 'skill_count': (10, 0, 49), 'profile_cache_minutes': (10, 0, 1440),
    'name_index_refresh_hours': (168, 1, 720), 'download_timeout_seconds': (120, 15, 900),
    'status_poll_minutes': (30, 10, 90), 'boss_poll_minutes': (30, 10, 90),
    'atlas_query_cooldown_seconds': (10, 10, 300), 'user_query_cooldown_seconds': (30, 30, 3600),
    'user_refresh_cooldown_seconds': (300, 300, 86400), 'atlas_budget_per_minute': (15, 1, 15),
    'atlas_budget_per_hour': (200, 1, 200), 'weekly_keep_days': (30, 14, 180),
}
# Switches that are on unless switched off; every other switch is off unless switched on.
ON_BY_DEFAULT = frozenset(('qq_single_word_commands', 'name_index_enabled', 'matching_enabled'))


class Settings(MutableMapping):
    """`settings.get('boss_enabled', False)` finds the value in the `boss` group of the real config. A flat key (a test
    config, or a config without the group) is used when the group does not hold it. Writes go where the value lives."""

    def __init__(self, raw):
        self.raw = raw

    def slot(self, key):
        group = self.raw.get(GROUP_OF.get(key))
        return group if isinstance(group, dict) else None

    def __getitem__(self, key):
        slot = self.slot(key)
        if slot is not None and key in slot:
            return slot[key]
        return self.raw[key]

    def __setitem__(self, key, value):
        slot = self.slot(key)
        (slot if slot is not None and (key in slot or key not in self.raw) else self.raw)[key] = value

    def __delitem__(self, key):
        slot = self.slot(key)
        if slot is not None and key in slot:
            del slot[key]
        else:
            del self.raw[key]

    def __iter__(self):
        seen = set()
        for key in self.raw:
            group = self.raw[key]
            if key in GROUPS and isinstance(group, dict):
                for inner in group:
                    if inner not in seen:
                        seen.add(inner); yield inner
            elif key not in seen:
                seen.add(key); yield key

    def __len__(self):
        return sum(1 for _ in self)

    def int(self, key):
        default, low, high = INTS[key]
        try:
            return max(low, min(high, int(self.get(key, default))))
        except (TypeError, ValueError, OverflowError):
            return default

    def flag(self, key):
        return self.get(key, key in ON_BY_DEFAULT) is True

    def strings(self, key):
        """A list setting as stripped, non-empty strings. A lone string counts as one item (`in` on a string would
        be a substring test); any other type returns None, which callers treat as "refuse"."""
        value = self.get(key, [])
        if isinstance(value, str):
            value = [value]
        if isinstance(value, (list, tuple, set)):
            return [text for text in (str(item).strip() for item in value) if text]
        return None

    def consume(self, key):
        """Whether a one-shot action switch is on. It is switched off and saved first, so a reload never repeats
        the action; when it cannot be saved the action does not run."""
        if not self.flag(key):
            return False
        self[key] = False
        self.save_config()
        return True

    def save_config(self):
        save = getattr(self.raw, 'save_config', None)
        if callable(save):
            save()
