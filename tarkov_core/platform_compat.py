"""QQ official scene detection. Command matching remains in AstrBot's standard command filter."""
QQ_OFFICIAL = frozenset(('qq_official', 'qq_official_webhook'))


def is_qq_official(event):
    return event.get_platform_name() in QQ_OFFICIAL
