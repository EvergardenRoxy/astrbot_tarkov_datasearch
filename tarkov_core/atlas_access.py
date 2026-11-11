"""Scope-specific Atlas refusal state. Recovery is one demand-driven probe, never a startup request."""
import asyncio
import json
import math
import os
import tempfile
import time

from astrbot.api import logger

from .transport import QueryError

SCOPES = ('players', 'matching')
STATES = ('blocked', 'probe_ready', 'probing')


class AtlasAccessPaused(QueryError):
    """Admission refused locally; no HTTP request or budget charge was made."""
    def __init__(self, scope):
        self.scope = scope
        super().__init__('Atlas 访问已暂停，请管理员检查访问权限后使用设置中的“恢复 Atlas 访问”')


class AtlasAccess:
    def __init__(self, path=None, *, clock=time.time):
        self.path, self.clock = path, clock
        self.states = {}
        self.loaded = False
        self.lock = asyncio.Lock()

    def is_blocked(self, scope):
        return self.states.get(scope, {}).get('state') in ('blocked', 'probing')

    def _read(self):
        if self.path is None or not self.path.exists():
            return {}
        if self.path.stat().st_size > 4096:
            raise ValueError('access state too large')
        document = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(document, dict) or document.get('version') != 1 or not isinstance(document.get('scopes'), dict):
            raise ValueError('invalid access state')
        states = document['scopes']
        for scope, entry in states.items():
            if scope not in SCOPES or not isinstance(entry, dict) or entry.get('state') not in STATES:
                raise ValueError('invalid access scope')
            if type(entry.get('status')) is not int or entry['status'] not in (0, 401, 403):
                raise ValueError('invalid access status')
            if (set(entry) != {'state', 'status', 'at'} or type(entry.get('at')) not in (int, float)
                    or not math.isfinite(entry['at'])):
                raise ValueError('invalid access time')
        return states

    def _write(self, states):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.atlas-access-', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump({'version': 1, 'scopes': states}, output, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    async def _worker(function, *args):
        # Do not leave an old writer running after cancellation/reload.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                pass
            raise

    async def _save(self):
        snapshot = {scope: dict(entry) for scope, entry in self.states.items()}
        try:
            await self._worker(self._write, snapshot)
            return True
        except (OSError, ValueError, TypeError):
            logger.warning('Tarkov Atlas access state could not be saved; access remains paused')
            return False

    async def load(self):
        async with self.lock:
            if self.loaded:
                return
            try:
                self.states = await self._worker(self._read)
                for entry in self.states.values():
                    if entry['state'] == 'probing':
                        entry['state'] = 'blocked'
            except (OSError, ValueError, TypeError, UnicodeError):
                self.states = {scope: {'state': 'blocked', 'status': 0, 'at': self.clock()} for scope in SCOPES}
                logger.warning('Tarkov Atlas access state is unreadable; administrator recovery is required')
            self.loaded = True

    async def check(self, scope):
        if scope not in SCOPES:
            raise ValueError('invalid Atlas scope')
        await self.load()
        if self.is_blocked(scope):
            raise AtlasAccessPaused(scope)

    async def before_send(self, scope):
        """Recheck after acquiring the shared HTTP gate. True means this request is the recovery probe."""
        await self.check(scope)
        async with self.lock:
            if self.is_blocked(scope):
                raise AtlasAccessPaused(scope)
            entry = self.states.get(scope)
            if entry is None:
                return False
            entry['state'] = 'probing'
            if not await self._save():
                entry['state'] = 'blocked'
                raise AtlasAccessPaused(scope)
            return True

    async def block(self, scope, status):
        await self.load()
        async with self.lock:
            self.states[scope] = {'state': 'blocked', 'status': status, 'at': self.clock()}
            await self._save()

    async def finish_probe(self, scope, success):
        async with self.lock:
            entry = self.states.get(scope)
            if entry is None or entry['state'] != 'probing':
                return
            if success:
                previous = self.states.pop(scope)
                if not await self._save():
                    self.states[scope] = dict(previous, state='blocked')
            else:
                entry['state'] = 'blocked'
                await self._save()

    async def arm_recovery(self):
        """Administrator action, already consumed and saved by the caller. Does not send a request."""
        await self.load()
        async with self.lock:
            previous = {scope: dict(entry) for scope, entry in self.states.items()}
            for entry in self.states.values():
                entry['state'] = 'probe_ready'
            if not await self._save():
                self.states = {scope: dict(entry, state='blocked') for scope, entry in previous.items()}
                raise QueryError('Atlas 恢复状态保存失败，访问仍暂停')
