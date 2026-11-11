"""One demand-driven matching snapshot for every user and chat in a plugin instance."""
import asyncio
import datetime
import math
import time
from dataclasses import dataclass, replace

from .atlas_client import AtlasDataError, AtlasHTTPError
from .atlas_cooldown import AtlasCooldownError
from .transport import ConnectionFailure, QueryError

CACHE_SECONDS = 15*60
MAX_AGE_SECONDS = 60*60
# Other non-transient HTTP errors (404, ...) have their own short window: users cannot re-hit Atlas on
# every query, and stale data never masks the error.
HTTP_ERROR_SECONDS = 2*60
HOURS = 24


@dataclass(frozen=True)
class Datacenter:
    region: str
    name: str
    display_name: str
    wait: int | float | None
    history: tuple[int | float | None, ...] | None


@dataclass(frozen=True)
class MatchingResult:
    hours: int
    server_time: float
    updated: float
    fetched_at: float
    history_start: int
    history_step: int
    datacenters: tuple[Datacenter, ...]
    from_cache: bool = False
    stale: bool = False
    error: str | None = None


def normalize(document, fetched_at):
    def integer(value, low, high):
        if type(value) is not int or not low <= value <= high:
            raise ValueError()
        return value

    def wait(value):
        if value is not None and (type(value) not in (int, float) or value < 0 or not math.isfinite(value)):
            raise ValueError()
        return value

    def text(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(not c.isprintable() and not c.isspace() for c in value):
            raise ValueError()
        return value

    def timestamp(value):
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError()
        date = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError()
        return date.timestamp()

    try:
        if not isinstance(document, dict) or integer(document['hours'], 1, 168) != HOURS:
            raise ValueError()
        server_time, updated = timestamp(document['server_time']), timestamp(document['updated'])
        history = document['history']
        start = integer(history['start'], 0, 253402300799)
        step = integer(history['step'], 1, 604800)
        series = history['series']
        centers = document['datacenters']
        if not isinstance(series, dict) or len(series) > 128 or not isinstance(centers, list) or not 1 <= len(centers) <= 128:
            raise ValueError()
        for name, points in series.items():
            text(name)
            if not isinstance(points, list) or len(points) > 1024:
                raise ValueError()
            if points and start+(len(points)-1)*step > 253402300799:
                raise ValueError()
            for point in points:
                wait(point)
        result, seen, displays = [], set(), set()
        for center in centers:
            name, region = text(center['name']), text(center['region'])
            display = ' '.join(name.split())
            if name in seen or display in displays:
                raise ValueError()
            seen.add(name)
            displays.add(display)
            points = series.get(name)
            result.append(Datacenter(region, name, display, wait(center['wait']),
                                     tuple(points) if points is not None else None))
        return MatchingResult(HOURS, server_time, updated, fetched_at, start, step, tuple(result))
    except (ValueError, TypeError, KeyError, OverflowError, OSError, AttributeError):
        raise AtlasDataError() from None


class MatchingService:
    def __init__(self, client, *, enabled=lambda: False, clock=time.monotonic, wall_clock=time.time):
        self.client = client
        self.enabled = enabled
        self.clock, self.wall_clock = clock, wall_clock
        self.cached = None
        self.cached_at = None
        self.next_attempt = 0
        self.pending = None
        self.last_error = None
        self.http_error = None
        self.http_error_until = 0
        self.closed = False

    def _cached_result(self):
        if self.cached is None:
            return None
        age = self.clock()-self.cached_at
        if not 0 <= age <= MAX_AGE_SECONDS:
            return None
        return replace(self.cached, from_cache=True, stale=age >= CACHE_SECONDS,
                       error=self.last_error if age >= CACHE_SECONDS else None)

    async def get(self):
        if self.closed:
            raise RuntimeError('Matching service closed')
        if self.enabled() is not True:
            raise QueryError('匹配时间查询尚未开启')
        # Check configuration even when returning cached data.
        self.client.validated_proxy()
        await self.client.access.check('matching')
        cached = self._cached_result()
        if cached is not None and not cached.stale:
            return cached
        if self.pending is None and self.http_error is not None and self.clock() < self.http_error_until:
            status, code = self.http_error
            raise AtlasHTTPError(status, code)
        if cached is not None and (self.pending is not None or self.clock() < self.next_attempt):
            return cached
        if self.pending is None and self.clock() < self.next_attempt:
            raise QueryError('匹配时间查询失败，请稍后再试')
        if self.pending is None:
            self.pending = asyncio.create_task(self._refresh())
            def done(task):
                if self.pending is task:
                    self.pending = None
                if not task.cancelled():
                    task.exception()
            self.pending.add_done_callback(done)
        return await asyncio.shield(self.pending)

    async def _refresh(self):
        try:
            document = await self.client.matching()
            result = await asyncio.to_thread(normalize, document, self.wall_clock())
            if self.closed or self.enabled() is not True:
                raise asyncio.CancelledError()
            self.cached, self.cached_at = result, self.clock()
            self.next_attempt = self.cached_at+CACHE_SECONDS
            self.last_error = None
            self.http_error = None
            return result
        except (ConnectionFailure, AtlasDataError, AtlasCooldownError, AtlasHTTPError) as exc:
            # Configuration/programming errors and non-transient HTTP errors cannot masquerade as success.
            if isinstance(exc, AtlasHTTPError) and exc.status in (401, 403):
                if not self.client.access.is_blocked('matching'):
                    await self.client.access.block('matching', exc.status)
                self.http_error = None
                raise
            if isinstance(exc, AtlasHTTPError) and exc.status != 429 and not 500 <= exc.status <= 599:
                # Independent of the 15-minute window: neither shortens nor extends it.
                self.http_error = (exc.status, exc.code)
                self.http_error_until = self.clock()+HTTP_ERROR_SECONDS
                raise
            self.last_error = str(exc)
            # Admission refusal sends no HTTP request: it must not start a new 15-minute failure window.
            if not isinstance(exc, AtlasCooldownError):
                self.next_attempt = self.clock()+CACHE_SECONDS
            cached = self._cached_result()
            if cached is not None:
                return replace(cached, stale=True, error=self.last_error)
            raise QueryError('匹配时间查询失败，请稍后再试') from None

    async def close(self):
        self.closed = True
        if self.pending is not None:
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)
        self.pending = None
        self.cached = None
        self.cached_at = None
        self.http_error = None
