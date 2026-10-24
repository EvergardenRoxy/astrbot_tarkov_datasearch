"""QQ official scene detection. Command matching remains in AstrBot's standard command filter."""
QQ_OFFICIAL = frozenset(('qq_official', 'qq_official_webhook'))


def is_qq_official(event):
    return event.get_platform_name() in QQ_OFFICIAL


def set_short_status_routes(module_name, enabled):
    """Disable actual query metadata so opted-out short commands cannot claim another plugin's routes."""
    from astrbot.core.star.star_handler import star_handlers_registry
    for handler in star_handlers_registry.get_handlers_by_module_name(module_name):
        if handler.handler_name in ('status_command', 'qq_status_command', 'boss_command', 'qq_boss_command',
                                    'item_command', 'qq_item_command'):
            handler.enabled = enabled
