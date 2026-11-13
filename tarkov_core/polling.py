"""The plugin's background tasks: a cancellable loop, and a poller that repeats a cycle at a settings interval."""
import asyncio
import time


class Loop:
    """A background task around `run()`, started only while `enabled()`."""
    task = None

    def enabled(self):
        raise NotImplementedError

    async def run(self):
        raise NotImplementedError

    def start(self):
        if self.enabled() and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None


async def wait_for_change(event, seconds):
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except TimeoutError:
        pass
    event.clear()


class PollingTask(Loop):
    """Runs `cycle()` every `interval()` seconds (at least two minutes), never two at once. After failures the wait
    doubles, up to half an hour or the interval if that is longer."""

    def __init__(self, cycle, enabled, interval, on_error, *, clock=time.monotonic, wait=wait_for_change):
        self.cycle, self.enabled, self.interval, self.on_error = cycle, enabled, interval, on_error
        self.clock, self.wait = clock, wait
        self.changed = asyncio.Event()

    def reconfigure(self):
        """The settings changed: start if needed and measure the current wait again."""
        self.changed.set()
        self.start()

    async def run(self):
        failures = 0
        while self.enabled():
            try:
                await self.cycle()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures = min(5, failures+1)
                self.on_error(exc)
            finished = self.clock()
            while self.enabled():
                base = max(120, self.interval())
                delay = min(max(1800, base), base*2**max(0, failures-1))
                remaining = delay-(self.clock()-finished)
                if remaining <= 0:
                    break
                await self.wait(self.changed, remaining)
