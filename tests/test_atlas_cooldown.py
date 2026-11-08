import asyncio
import datetime
import unittest

from tarkov_core.atlas_client import retry_after
from tarkov_core.atlas_cooldown import AtlasCooldown, AtlasCooldownError


class Clock:
    def __init__(self):
        self.now = 10000.

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class CooldownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_entry_atomicity_and_start_interval(self):
        clock = Clock()
        gate = AtlasCooldown(clock=clock)
        async with gate.claim():
            clock.advance(10)
            with self.assertRaises(AtlasCooldownError) as caught:
                async with gate.claim():
                    self.fail('overlapping request')
            self.assertTrue(caught.exception.busy)
        # Counts from start, not completion.
        async with gate.claim():
            pass
        for _ in range(3):
            with self.assertRaises(AtlasCooldownError) as caught:
                async with gate.claim():
                    self.fail('cooldown bypass')
            self.assertEqual(caught.exception.retry_after_seconds, 5)
        clock.advance(5)
        async with gate.claim():
            pass

    async def test_failure_cancellation_and_longer_pause(self):
        clock = Clock()
        gate = AtlasCooldown(clock=clock)
        with self.assertRaises(asyncio.CancelledError):
            async with gate.claim():
                raise asyncio.CancelledError()
        self.assertFalse(gate.in_flight)
        gate.pause(90000)
        gate.pause(5)
        clock.advance(6)
        with self.assertRaises(AtlasCooldownError) as caught:
            async with gate.claim():
                pass
        self.assertEqual(caught.exception.retry_after_seconds, 89994)
        clock.advance(89994)
        async with gate.claim():
            pass

    async def test_invalid_settings_and_pause(self):
        for interval in (True, None, '5', 0, 301, float('nan'), float('inf')):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                AtlasCooldown(interval)
        gate = AtlasCooldown()
        gate.pause(float('inf'))
        with self.assertRaises(AtlasCooldownError) as caught:
            async with gate.claim():
                pass
        self.assertIsNone(caught.exception.retry_after_seconds)

    def test_retry_after_is_not_truncated_and_supports_dates(self):
        self.assertEqual(retry_after('90000'), 90000)
        self.assertEqual(retry_after('Sun, 08 Nov 2026 00:00:10 GMT', now=1794096000), 10)
        self.assertEqual(retry_after('bad'), 60)
        self.assertEqual(retry_after(None), 60)
        self.assertEqual(retry_after('0'), 0)
        self.assertEqual(retry_after('9'*1000), float('inf'))
