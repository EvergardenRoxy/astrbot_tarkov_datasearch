"""Instance-wide admission for Atlas only; cache reads never enter this gate."""
import math
import time
from contextlib import asynccontextmanager

from .transport import QueryError


class AtlasCooldownError(QueryError):
    def __init__(self, seconds, *, busy=False):
        super().__init__('Atlas 查询暂时无法完成，请稍后再试')
        self.retry_after_seconds = math.ceil(seconds) if math.isfinite(seconds) else None
        self.busy = busy


class AtlasCooldown:
    def __init__(self, interval=5, *, clock=time.monotonic):
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or not 1 <= interval <= 300:
            raise ValueError('invalid Atlas cooldown')
        self.interval = interval
        self.clock = clock
        self.next_start = 0
        self.paused_until = 0
        self.in_flight = False

    def pause(self, seconds):
        # Infinite pause is fail-closed for an unrepresentably large Retry-After.
        if seconds < 0 or math.isnan(seconds):
            raise ValueError('invalid Atlas pause')
        self.paused_until = max(self.paused_until, self.clock()+seconds)

    @asynccontextmanager
    async def claim(self):
        now = self.clock()
        remaining = max(self.next_start, self.paused_until)-now
        # No await between checking and reserving: atomic within the owning event loop.
        if self.in_flight or remaining > 0:
            raise AtlasCooldownError(max(0, remaining), busy=self.in_flight)
        self.in_flight = True
        self.next_start = now+self.interval
        try:
            yield
        finally:
            self.in_flight = False
