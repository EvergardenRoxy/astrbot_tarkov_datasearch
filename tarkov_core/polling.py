"""One cancellable, non-overlapping poller; normal interval comes from feature settings."""
import asyncio
import time


async def wait_for_change(event, seconds):
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except TimeoutError:
        pass
    event.clear()


class PollingTask:
    def __init__(self, cycle, enabled, interval, on_error, *, clock=time.monotonic, wait=wait_for_change):
        self.cycle, self.enabled, self.interval, self.on_error = cycle, enabled, interval, on_error
        self.clock, self.wait = clock, wait
        self.changed = asyncio.Event()
        self.task = None

    def start(self):
        if self.enabled() and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.run())

    def reconfigure(self):
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
                delay = min(1800, max(120, self.interval())*2**max(0, failures-1))
                remaining = delay-(self.clock()-finished)
                if remaining <= 0:
                    break
                await self.wait(self.changed, remaining)

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
