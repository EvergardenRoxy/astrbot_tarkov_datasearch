"""The two background tasks of the weekly report: the nightly sampler and the weekly broadcast.

Sampling rules given by the maintainer after Atlas relaxed its terms (2026-11-10), kept exactly:
- sampling starts at 03:00 Beijing time (UTC+8) and stops at 09:00 (off-peak only; a missed night is not caught up);
- one Atlas request at a time, for one listed player; the next one 120 seconds plus a random 20-60 seconds later;
- only the players the administrator listed in the settings; no hourly cap beyond the plugin's shared Atlas gate.
A nickname that needs Atlas' search uses one step for the search and the next step for the lookup.

The broadcast sends one ranking picture per mode to the listed targets at the configured weekly time; a slot missed
by more than LATE seconds (the plugin was down) is skipped. Neither task starts when its feature is off.
"""
import asyncio
import datetime
import random
import re
import time

from .atlas_players import NOT_SENT, AtlasNotFound
from .notifications import PermanentDeliveryError
from .weekly_report import TZ
from .weekly_store import SampleResult

START_HOUR, END_HOUR = 3, 9
GAP_SECONDS = 120
RANDOM_SECONDS = (20, 60)
# How often an idle task looks at the clock again (seconds).
IDLE_SECONDS = 600
LATE_SECONDS = 6*3600
RETRY_SECONDS = 120
PUSH_ATTEMPTS = 3
ACCOUNT = re.compile(r'[0-9]{1,12}')
WEEKDAYS = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')
CLOCK = re.compile(r'([01]?[0-9]|2[0-3])[:：]([0-5][0-9])')


def local(now):
    return datetime.datetime.fromtimestamp(now, TZ)


def day_id(now):
    return local(now).strftime('%Y-%m-%d')


def in_window(now):
    return START_HOUR <= local(now).hour < END_HOUR


def until_window(now):
    """Seconds until the next 03:00 (UTC+8)."""
    moment = local(now)
    start = moment.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
    if start <= moment:
        start += datetime.timedelta(days=1)
    return (start-moment).total_seconds()


def parse_schedule(weekday, clock):
    """(weekday 0-6, hour, minute) of the settings' "周一" and "10:00"; None when either is unusable."""
    found = CLOCK.fullmatch(str(clock).strip())
    if weekday not in WEEKDAYS or not found:
        return None
    return WEEKDAYS.index(weekday), int(found.group(1)), int(found.group(2))


def latest_slot(now, schedule):
    """Epoch seconds of the most recent scheduled moment that is not after `now`."""
    weekday, hour, minute = schedule
    moment = local(now)
    slot = (moment-datetime.timedelta(days=(moment.weekday()-weekday) % 7)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > moment:
        slot -= datetime.timedelta(days=7)
    return slot.timestamp()


class Loop:
    """A cancellable background task around `run()`."""
    task = None

    def start(self):
        if self.enabled() and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None


class WeeklySampler(Loop):
    def __init__(self, store, entries, resolve, fetch, save, enabled, on_error, *, clock=time.time,
                 sleep=asyncio.sleep, rand=random.uniform, monotonic=time.monotonic):
        # resolve/fetch accept a monotonic `deadline`; each may issue at most one HTTP request.
        # resolve -> (account id or None, nickname, whether Atlas was asked); fetch -> summary.
        # save(..., day=day) -> SampleResult, atomically committing the sample and nightly completion.
        self.store, self.entries, self.resolve, self.fetch, self.save = store, entries, resolve, fetch, save
        self.enabled, self.on_error = enabled, on_error
        self.clock, self.sleep, self.rand = clock, sleep, rand
        self.monotonic = monotonic
        self.unrecorded = None

    async def flush_progress(self):
        """If even the failure record could not be written, repair it before sending more HTTP requests."""
        if self.unrecorded is not None:
            await asyncio.to_thread(self.store.attempted, *self.unrecorded)
            self.unrecorded = None

    def gap(self):
        return GAP_SECONDS+self.rand(*RANDOM_SECONDS)

    async def step(self):
        """At most one Atlas request. None: nothing to do now; otherwise what happened (the pause follows)."""
        now = self.clock()
        if not self.enabled() or not in_window(now):
            return None
        # Convert this night's wall-clock cutoff to the HTTP gate's monotonic clock once, before any awaits.
        end = local(now).replace(hour=END_HOUR, minute=0, second=0, microsecond=0).timestamp()
        deadline = self.monotonic()+(end-now)
        day = day_id(now)
        await self.flush_progress()
        pending = await asyncio.to_thread(self.store.pending, self.entries(), day)
        if not pending:
            return None
        mode, entry = pending[0]
        async def mark(done, error=None):
            self.unrecorded = (mode, entry, day, done, error)
            await self.flush_progress()
        account_id = int(entry) if ACCOUNT.fullmatch(entry) else None
        if account_id is None:
            known = await asyncio.to_thread(self.store.resolved, mode, entry)
            if known:
                account_id = known[0]
            else:
                try:
                    account_id, name, asked = await self.resolve(mode, entry, deadline=deadline)
                except NOT_SENT:
                    # The plugin's own budget or gate refused: nothing was sent, the entry stays for the next step.
                    return 'deferred'
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await mark(False, type(exc).__name__)
                    self.on_error(exc)
                    return 'failed'
                if account_id is None:
                    await mark(True, 'unresolved')
                    return 'unresolved'
                try:
                    await asyncio.to_thread(self.store.resolve, mode, entry, account_id, name)
                except Exception as exc:
                    await mark(False, type(exc).__name__)
                    self.on_error(exc)
                    return 'failed'
                if asked:
                    return 'resolved'
        # Resolving a new alias may reveal an account already completed (or exhausted) under another entry.
        if not await asyncio.to_thread(self.store.pending, [(mode, entry)], day):
            return 'skipped'
        try:
            summary = await self.fetch(mode, account_id, deadline=deadline)
        except NOT_SENT:
            return 'deferred'
        except AtlasNotFound:
            await mark(True, 'no_profile')
            return 'missing'
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await mark(False, type(exc).__name__)
            self.on_error(exc)
            return 'failed'
        try:
            result = await self.save(account_id, mode, summary, day=day)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await mark(False, type(exc).__name__)
            self.on_error(exc)
            return 'failed'
        if result not in (SampleResult.ADDED, SampleResult.DUPLICATE):
            await mark(False, 'invalid_sample')
            return 'failed'
        # save() committed the sample and canonical account completion together. A nickname-write failure
        # after that commit cannot cause another HTTP lookup of the same account tonight.
        if isinstance(summary.get('nickname'), str):
            await asyncio.to_thread(self.store.resolve, mode, entry, account_id, summary['nickname'])
        return 'pulled'

    async def run(self):
        while self.enabled():
            try:
                outcome = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.on_error(exc)
                outcome = 'failed'
            if outcome is None:
                now = self.clock()
                await self.sleep(IDLE_SECONDS if in_window(now) else min(IDLE_SECONDS, until_window(now)))
            else:
                await self.sleep(self.gap())


class WeeklyBroadcast(Loop):
    def __init__(self, store, schedule, targets, modes, prepare, send, enabled, on_error, *, clock=time.time,
                 sleep=asyncio.sleep):
        # schedule() -> (weekday, hour, minute); targets() -> [session id]; modes() -> modes with listed players;
        # prepare(mode) -> picture bytes or None (nobody has a report); send(target, bytes) -> True / None / other.
        self.store, self.schedule, self.targets, self.modes = store, schedule, targets, modes
        self.prepare, self.send, self.enabled, self.on_error = prepare, send, enabled, on_error
        self.clock, self.sleep = clock, sleep

    async def deliver(self, slot):
        """Send what is still owed for `slot`; True when something should be tried again."""
        again = False
        for mode in self.modes():
            picture = self
            for target in self.targets():
                state = await asyncio.to_thread(self.store.push_state, slot, target, mode)
                if state and (state[0] != 'retry' or state[1] >= PUSH_ATTEMPTS):
                    continue
                try:
                    if picture is self:
                        picture = await self.prepare(mode)
                    if picture is None:
                        outcome = 'empty'
                    else:
                        result = await asyncio.wait_for(self.send(target, picture), timeout=20)
                        # True: submitted to the platform. None: unknown, never repeated (it may have arrived).
                        outcome = 'submitted' if result is True else 'unknown' if result is None else 'retry'
                except asyncio.CancelledError:
                    raise
                except (PermanentDeliveryError, PermissionError) as exc:
                    outcome = 'blocked'
                    self.on_error(exc)
                except Exception as exc:
                    outcome = 'retry'
                    self.on_error(exc)
                await asyncio.to_thread(self.store.push_record, slot, target, mode, outcome)
                if outcome == 'retry' and (state[1] if state else 0)+1 < PUSH_ATTEMPTS:
                    again = True
        return again

    async def tick(self):
        """One look at the clock; the seconds to sleep before the next one."""
        now = self.clock()
        schedule = self.schedule()
        moment = latest_slot(now, schedule)
        again = False
        if now-moment < LATE_SECONDS:
            again = await self.deliver(local(moment).strftime('%Y-%m-%d %H:%M'))
        wait = moment+7*86400-self.clock()
        return max(1, min(IDLE_SECONDS, RETRY_SECONDS if again else wait))

    async def run(self):
        while self.enabled():
            try:
                wait = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.on_error(exc)
                wait = IDLE_SECONDS
            await self.sleep(wait)
