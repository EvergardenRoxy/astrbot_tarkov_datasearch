"""Which chats a push may go to, and the group chats that have used the plugin (the default targets)."""
import asyncio
import sqlite3
import time

from astrbot.api import logger

from .platform_compat import QQ_OFFICIAL

MESSAGE_TYPES = ('GroupMessage', 'FriendMessage', 'OtherMessage')
MAX_TARGETS = 50
# A group's "last used" time is written at most this often (seconds).
REMEMBER_SECONDS = 3600


class PushTargets:
    def __init__(self, app):
        self.app = app
        # Group sessions that used the plugin, newest first.
        try:
            self.sessions = app.accounts.sessions()
        except sqlite3.Error as exc:
            logger.warning('Tarkov remembered sessions unavailable: %s', type(exc).__name__)
            self.sessions = []
        self.written = {}

    async def remember(self, event):
        """Remember a group chat that was allowed to use the plugin: only its session id, never a user id.
        True when the chat is new (a poller waiting for its first target can start)."""
        origin = event.unified_msg_origin
        if origin.split(':', 2)[1:2] != ['GroupMessage']:
            return False
        now = time.monotonic()
        if origin in self.sessions and now-self.written.get(origin, -REMEMBER_SECONDS) < REMEMBER_SECONDS:
            return False
        self.written[origin] = now
        try:
            await asyncio.to_thread(self.app.accounts.touch_session, origin)
        except sqlite3.Error as exc:
            logger.warning('Tarkov session record failed: %s', type(exc).__name__)
            return False
        new = origin not in self.sessions
        self.sessions = [origin]+[other for other in self.sessions if other != origin][:MAX_TARGETS-1]
        return new

    def platform(self, target):
        """The platform's metadata of a session id, or None when that platform is not loaded."""
        for platform in getattr(getattr(self.app.context, 'platform_manager', None), 'platform_insts', []):
            metadata = platform.meta()
            if metadata.id == target.split(':', 1)[0]:
                return metadata
        return None

    def usable(self, targets, *, qq_enabled):
        """The session ids among `targets` a push may be sent to: well-formed, inside "allowed_origins", on a
        platform that can send unprompted, and (QQ official bots) switched on."""
        allowed = self.app.config.strings('allowed_origins')
        if allowed is None:
            return []
        valid = []
        for target in targets:
            target = str(target).strip()
            parts = target.split(':', 2)
            if len(parts) != 3 or not all(parts) or parts[1] not in MESSAGE_TYPES or any(c.isspace() for c in target):
                continue
            if allowed and target not in allowed:
                continue
            metadata = self.platform(target)
            if metadata is not None:
                if getattr(metadata, 'support_proactive_message', True) is False:
                    continue
                if metadata.name in QQ_OFFICIAL and not qq_enabled:
                    continue
            valid.append(target)
        return list(dict.fromkeys(valid))

    def configured(self, setting, qq_setting):
        """A feature's push targets: its target list, or every remembered group chat when the list is empty."""
        targets = self.app.config.get(setting, [])
        if not isinstance(targets, list) or len(targets) > MAX_TARGETS:
            return []
        return self.usable(targets or self.sessions, qq_enabled=self.app.config.flag(qq_setting))
