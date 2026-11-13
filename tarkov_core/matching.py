"""One demand-driven matching snapshot for every user and chat in a plugin instance."""
import asyncio
import datetime
import math
import time
from dataclasses import dataclass, replace

from .atlas_client import AtlasDataError, AtlasHTTPError
from .atlas_cooldown import AtlasAccessPaused, AtlasCooldownError
from .transport import ConnectionFailure, QueryError

CACHE_SECONDS = 15*60
# A failed refresh may still show a result this old, labelled as such.
MAX_AGE_SECONDS = 60*60
# A request Atlas refused outright (404, ...) is not repeated for this long; old data never masks that error.
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
    """Atlas' matching document as a MatchingResult; AtlasDataError when it is not shaped as the picture needs."""
    def wait(value):
        if value is not None and (type(value) not in (int, float) or value < 0 or not math.isfinite(value)):
            raise ValueError()
        return value

    def timestamp(value):
        date = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError()
        return date.timestamp()

    try:
        history = document['history']
        series = history['series']
        centers = []
        for center in document['datacenters']:
            name, region = center['name'], center['region']
            if not isinstance(name, str) or not isinstance(region, str) or not name.strip():
                raise ValueError()
            points = series.get(name)
            centers.append(Datacenter(region, name, ' '.join(name.split()), wait(center['wait']),
                                      tuple(wait(point) for point in points) if points is not None else None))
        if not centers or len({center.display_name for center in centers}) != len(centers):
            raise ValueError()
        return MatchingResult(HOURS, timestamp(document['server_time']), timestamp(document['updated']), fetched_at,
                              int(history['start']), int(history['step']), tuple(centers))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise AtlasDataError() from None


class MatchingService:
    def __init__(self, client, *, clock=time.monotonic, wall_clock=time.time):
        self.client = client
        self.clock, self.wall_clock = clock, wall_clock
        self.cached = self.cached_at = None
        # No new request before this time: a success or a failed attempt opens a 15-minute window.
        self.next_attempt = 0
        self.pending = None
        self.last_error = None
        self.http_error = None
        self.http_error_until = 0

    def _cached_result(self):
        if self.cached is None:
            return None
        age = self.clock()-self.cached_at
        if not 0 <= age <= MAX_AGE_SECONDS:
            return None
        stale = age >= CACHE_SECONDS
        return replace(self.cached, from_cache=True, stale=stale, error=self.last_error if stale else None)

    async def get(self):
        """The shared result: fresh within 15 minutes, else refreshed; an older one (at most an hour) stands in,
        marked stale, while a refresh is impossible or failing."""
        # Check configuration even when returning cached data.
        self.client.validated_proxy()
        if self.client.refused('matching'):
            raise AtlasAccessPaused(self.client.refused('matching'))
        cached = self._cached_result()
        if cached is not None and not cached.stale:
            return cached
        if self.pending is None:
            if self.http_error is not None and self.clock() < self.http_error_until:
                raise AtlasHTTPError(*self.http_error)
            if self.clock() < self.next_attempt:
                if cached is not None:
                    return cached
                raise QueryError('匹配时间查询失败，请稍后再试')
            self.pending = asyncio.create_task(self._refresh())
            self.pending.add_done_callback(self._finished)
        return await asyncio.shield(self.pending)

    def _finished(self, task):
        if self.pending is task:
            self.pending = None
        if not task.cancelled():
            task.exception()

    async def _refresh(self):
        try:
            document = await self.client.matching()
            result = await asyncio.to_thread(normalize, document, self.wall_clock())
        except (ConnectionFailure, AtlasDataError, AtlasCooldownError, AtlasHTTPError) as exc:
            if isinstance(exc, AtlasHTTPError) and exc.status in (401, 403):
                raise
            if isinstance(exc, AtlasHTTPError) and exc.status != 429 and exc.status < 500:
                self.http_error = (exc.status, exc.code)
                self.http_error_until = self.clock()+HTTP_ERROR_SECONDS
                raise
            self.last_error = str(exc)
            # A local admission refusal sent nothing: it does not open a failure window.
            if not isinstance(exc, AtlasCooldownError):
                self.next_attempt = self.clock()+CACHE_SECONDS
            cached = self._cached_result()
            if cached is not None:
                return replace(cached, stale=True, error=self.last_error)
            raise QueryError('匹配时间查询失败，请稍后再试') from None
        self.cached, self.cached_at = result, self.clock()
        self.next_attempt = self.cached_at+CACHE_SECONDS
        self.last_error = self.http_error = None
        return result

    async def close(self):
        if self.pending is not None:
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)
        self.cached = None
