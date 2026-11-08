import asyncio
import copy
import dataclasses
import unittest
from unittest.mock import AsyncMock, Mock

from tarkov_core.atlas_client import AtlasDataError, AtlasHTTPError
from tarkov_core.atlas_cooldown import AtlasCooldownError
from tarkov_core.matching import MatchingService, normalize
from tarkov_core.transport import ConnectionFailure, QueryError
from test_atlas_cooldown import Clock


def matching_data():
    return {'hours': 24, 'server_time': '2026-11-08T06:28:07+00:00',
            'updated': '2026-11-08T06:27:19Z',
            'datacenters': [{'region': 'ASI', 'name': ' Example   Center ', 'wait': 0},
                            {'region': 'EUR', 'name': 'Other', 'wait': None}],
            'history': {'start': 1794115687, 'step': 300,
                        'series': {' Example   Center ': [12, None, 0]}}}


class ModelTests(unittest.TestCase):
    def test_normalized_immutable_result_preserves_names_zero_and_gaps(self):
        document = matching_data()
        result = normalize(document, 123)
        self.assertEqual(result.datacenters[0].display_name, 'Example Center')
        self.assertEqual(result.datacenters[0].history, (12, None, 0))
        self.assertEqual(result.datacenters[0].wait, 0)
        self.assertIsNone(result.datacenters[1].wait)
        self.assertIsNone(result.datacenters[1].history)
        document['history']['series'][' Example   Center '][0] = 99
        self.assertEqual(result.datacenters[0].history[0], 12)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.hours = 1

    def test_malformed_inputs_are_rejected(self):
        invalid = [None, [], {}, {'hours': True}]
        for key, value in [('hours', 1), ('server_time', '2026-11-08'), ('updated', None),
                           ('datacenters', []), ('history', [])]:
            data = matching_data()
            data[key] = value
            invalid.append(data)
        for value in (True, -1, '1', float('inf'), float('nan'), 10**1000):
            data = matching_data()
            data['datacenters'][0]['wait'] = value
            invalid.append(data)
        for value in (True, 0, -1, '300'):
            data = matching_data()
            data['history']['step'] = value
            invalid.append(data)
        data = matching_data()
        data['datacenters'].append(copy.deepcopy(data['datacenters'][0]))
        invalid.append(data)
        data = matching_data()
        data['history']['series']['Other'] = [0]*1025
        invalid.append(data)
        data = matching_data()
        data['history']['series']['Other'] = [False]
        invalid.append(data)
        for data in invalid:
            with self.subTest(data=str(data)[:80]), self.assertRaises(AtlasDataError):
                normalize(data, 123)


class MatchingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.client = Mock()
        self.client.validated_proxy.return_value = None
        self.client.matching = AsyncMock(return_value=matching_data())
        self.service = MatchingService(self.client, enabled=lambda: True, clock=self.clock,
                                       wall_clock=lambda: 123)

    async def asyncTearDown(self):
        await self.service.close()

    async def test_all_readers_share_fifteen_minute_cache(self):
        first = await self.service.get()
        self.assertFalse(first.from_cache)
        for _ in range(20):
            cached = await self.service.get()
            self.assertTrue(cached.from_cache)
        self.clock.advance(899)
        self.assertFalse((await self.service.get()).stale)
        self.client.matching.assert_awaited_once()
        self.clock.advance(1)
        await self.service.get()
        self.assertEqual(self.client.matching.await_count, 2)

    async def test_failed_refresh_keeps_original_age_and_backs_off(self):
        first = await self.service.get()
        self.clock.advance(900)
        self.client.matching.side_effect = ConnectionFailure('failure')
        result = await self.service.get()
        self.assertTrue(result.stale)
        self.assertEqual(result.fetched_at, first.fetched_at)
        for _ in range(20):
            self.assertTrue((await self.service.get()).from_cache)
        self.assertEqual(self.client.matching.await_count, 2)
        self.clock.advance(2640)  # Successful result is now 59 minutes old.
        self.assertTrue((await self.service.get()).stale)
        self.clock.advance(60)
        self.assertTrue((await self.service.get()).stale)  # Inclusive 60-minute boundary.
        self.clock.advance(.01)
        with self.assertRaises(QueryError):
            await self.service.get()
        self.assertEqual(self.service.cached, first)

    async def test_first_failure_is_not_retried_by_each_user(self):
        self.client.matching.side_effect = AtlasHTTPError(429, 'rate', 60)
        for _ in range(20):
            with self.assertRaises(QueryError):
                await self.service.get()
        self.client.matching.assert_awaited_once()
        self.clock.advance(900)
        self.client.matching.side_effect = None
        self.assertFalse((await self.service.get()).from_cache)

    async def test_transient_errors_and_admission_refusal_fall_back(self):
        await self.service.get()
        for error in (AtlasDataError(), AtlasHTTPError(503), AtlasCooldownError(5)):
            self.clock.advance(900)
            self.client.matching.side_effect = error
            self.assertTrue((await self.service.get()).stale)
        self.assertEqual(self.service.next_attempt, self.clock())

    async def test_programming_configuration_and_disabled_paths_do_not_fall_back(self):
        await self.service.get()
        self.client.validated_proxy.side_effect = QueryError('invalid proxy')
        with self.assertRaisesRegex(QueryError, 'invalid proxy'):
            await self.service.get()
        self.client.validated_proxy.side_effect = None
        self.service.enabled = lambda: False
        with self.assertRaises(QueryError):
            await self.service.get()
        self.service.enabled = lambda: True
        self.clock.advance(900)
        for error in (RuntimeError('bug'), AtlasHTTPError(403)):
            self.client.matching.side_effect = error
            with self.assertRaises(type(error)):
                await self.service.get()

    async def test_concurrency_cancellation_and_close(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def download():
            started.set()
            await release.wait()
            return matching_data()
        self.client.matching.side_effect = download
        callers = [asyncio.create_task(self.service.get()) for _ in range(20)]
        await started.wait()
        callers[0].cancel()
        await asyncio.gather(callers[0], return_exceptions=True)
        release.set()
        results = await asyncio.gather(*callers[1:])
        self.assertEqual(len(results), 19)
        self.client.matching.assert_awaited_once()
        self.clock.advance(900)
        release.clear()
        started.clear()
        refresher = asyncio.create_task(self.service.get())
        await started.wait()
        # Another caller can read the old result while the refresh is pending.
        self.assertTrue((await self.service.get()).stale)
        await self.service.close()
        with self.assertRaises(asyncio.CancelledError):
            await refresher
        self.assertIsNone(self.service.pending)
        with self.assertRaises(RuntimeError):
            await self.service.get()
