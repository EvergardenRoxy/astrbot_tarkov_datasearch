"""Grouped settings (AstrBot shows nested `object` groups as sections) read through the flat key names the code uses."""
from collections.abc import MutableMapping

GROUPS = {
    'general': ('command_style', 'qq_single_word_commands', 'access_mode', 'user_whitelist', 'allowed_origins',
                'proxy_enabled', 'proxy_url', 'public_data_cache_minutes'),
    'player': ('default_mode', 'arena_by_default', 'output_mode', 'skill_count', 'profile_cache_minutes',
               'name_index_enabled', 'name_index_refresh_hours', 'download_timeout_seconds'),
    'status': ('status_enabled', 'status_broadcast_enabled', 'status_poll_minutes', 'status_notify_targets',
               'status_qq_push_enabled'),
    'boss': ('boss_enabled', 'boss_broadcast_enabled', 'boss_poll_minutes', 'boss_modes', 'boss_notify_targets',
             'boss_qq_push_enabled', 'boss_event_ignore', 'boss_portraits_update'),
}
GROUP_OF = {key: group for group, keys in GROUPS.items() for key in keys}


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

    def save_config(self):
        save = getattr(self.raw, 'save_config', None)
        if callable(save):
            save()
