"""Instance-wide admission for Atlas only; cache reads never enter this gate."""
import asyncio
import collections
import math
import time
from contextlib import asynccontextmanager

from .transport import QueryError

# How often a waiting player request looks again while another Atlas request is in flight (seconds).
POLL_SECONDS = 0.25


class AtlasCooldownError(QueryError):
    def __init__(self, seconds, *, busy=False):
        super().__init__('Atlas 查询暂时无法完成，请稍后再试')
        self.retry_after_seconds = math.ceil(seconds) if math.isfinite(seconds) else None
        self.busy = busy


class AtlasBudgetError(QueryError):
    """The plugin's own minute/hour budget for Atlas player requests is used up (no request was sent)."""
    def __init__(self, seconds):
        super().__init__(f'Atlas 玩家查询总额度已用完，约 {math.ceil(seconds)} 秒后自动恢复；缓存仍可查看')
        self.retry_after_seconds = math.ceil(seconds)


class AtlasDeadlineError(AtlasCooldownError):
    """A scheduled request's window ended before it was sent; no budget was consumed."""
    def __init__(self):
        super().__init__(0)


class AtlasBudget:
    """Sliding minute and hour windows over the Atlas player requests actually sent, for every user together.

    Atlas allows 20 a minute and 200 an hour per source IP (author, 2026-11-08); the defaults keep a margin. A request
    refused with 429 is not counted by Atlas, so its slot is given back (`refund`)."""

    def __init__(self, per_minute=lambda: 10, per_hour=lambda: 150, *, clock=time.monotonic):
        self.per_minute, self.per_hour = per_minute, per_hour
        self.clock = clock
        self.sent = collections.deque()

    def _trim(self, now):
        while self.sent and now-self.sent[0] >= 3600:
            self.sent.popleft()

    def check(self):
        """Read the current window deadlines without taking a slot; expired limits recover on the next call."""
        now = self.clock()
        self._trim(now)
        waits = []
        recent = [moment for moment in self.sent if now-moment < 60]
        for limit, window, sent in ((self.per_minute(), 60, recent), (self.per_hour(), 3600, list(self.sent))):
            if len(sent) >= limit:
                # The slot frees when the request `limit` places back leaves the window (a zero limit: never sooner).
                waits.append(window-(now-sent[-limit]) if limit > 0 else window)
        if waits:
            raise AtlasBudgetError(max(1, max(waits)))

    def reserve(self):
        """Count one request now, or raise AtlasBudgetError. Returns a token for `refund`."""
        self.check()
        now = self.clock()
        self.sent.append(now)
        return now

    def refund(self, token):
        try:
            self.sent.remove(token)
        except ValueError:
            pass

    def remaining_hour(self):
        self._trim(self.clock())
        return max(0, self.per_hour()-len(self.sent))


class AtlasCooldown:
    def __init__(self, interval=10, *, clock=time.monotonic):
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or not 10 <= interval <= 300:
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
        async with self._reserved(now):
            yield

    @asynccontextmanager
    async def claim_within(self, max_wait, *, sleep=asyncio.sleep, deadline=None):
        """Like claim(), but waits up to `max_wait` seconds for the shared interval, an upstream pause or another
        request in flight (player queries: a name search is followed by a lookup that must respect the interval).
        A pause or a next start beyond the wait fails at once instead of sleeping until the limit; nothing is
        queued beyond `max_wait`."""
        wait_until = self.clock()+max_wait
        if deadline is not None:
            wait_until = min(wait_until, deadline)
        while True:
            now = self.clock()
            if deadline is not None and now >= deadline:
                raise AtlasDeadlineError()
            remaining = max(self.next_start, self.paused_until)-now
            if not self.in_flight and remaining <= 0:
                break
            if max(self.next_start, self.paused_until) > wait_until or now >= wait_until:
                raise AtlasCooldownError(max(0, remaining), busy=self.in_flight)
            step = max(remaining, POLL_SECONDS) if self.in_flight else remaining
            await sleep(min(step, wait_until-now))
        async with self._reserved(now):
            yield

    @asynccontextmanager
    async def _reserved(self, now):
        self.in_flight = True
        self.next_start = now+self.interval
        try:
            yield
        finally:
            self.in_flight = False
