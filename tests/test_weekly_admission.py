"""Sampler -> player service -> actual HTTP admission. Local server and invented documents only."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from aiohttp import web

from fixtures import atlas_profile
from tarkov_core.atlas_cooldown import AtlasBudget, AtlasDeadlineError
from tarkov_core.atlas_players import AtlasPlayers
from tarkov_core.weekly_report import extract_sample
from tarkov_core.weekly_store import WeeklyStore
from tarkov_core.weekly_tasks import WeeklySampler, day_id
import test_atlas_client
from test_weekly_report import LAST, iso


class WeeklyAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_atlas_client.AtlasHTTPTests.asyncSetUp(self)
        self.addAsyncCleanup(test_atlas_client.AtlasHTTPTests.asyncTearDown, self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = self.clock()
        self.wall = lambda: LAST+self.clock()-self.base
        self.store = WeeklyStore(Path(self.temp.name)/'weekly.sqlite3', clock=self.wall)
        self.budget = AtlasBudget(clock=self.clock)
        self.client.budget = self.budget
        self.players = AtlasPlayers(self.client)
        self.addAsyncCleanup(self.players.close)
        self.errors, self.starts = [], []
        self.entries = [('pve', '1234567')]
        self.sampler = WeeklySampler(self.store, lambda: self.entries, self.resolve, self.fetch, self.save,
            lambda: len(self.starts) < 2, self.errors.append, clock=lambda: self.wall(), monotonic=self.clock,
            sleep=self.advance, rand=lambda low, high: low)
        # Use the real admission algorithm with a controlled clock, avoiding real multi-minute sleeps.
        self.claim = self.gate.claim_within
        self.gate.claim_within = lambda limit, **kwargs: self.claim(limit, sleep=self.advance, **kwargs)
        self.response = self.answer

    async def advance(self, seconds):
        self.clock.advance(seconds)

    def answer(self, request):
        self.starts.append(self.clock())
        return web.json_response(atlas_profile('pve', looked_up=iso(self.wall())))

    async def resolve(self, mode, entry, *, deadline):
        rows = await self.players.search_once(entry, mode, deadline=deadline)
        return (*rows[0], True) if rows else (None, None, True)

    async def fetch(self, mode, aid, *, deadline):
        return await self.players.profile_once(mode, aid, deadline=deadline)

    async def save(self, aid, mode, summary, *, day):
        return self.store.add_sample(aid, mode, extract_sample(summary['doc'], mode), day=day)

    async def test_busy_lookup_retry_is_paced_by_the_sampler(self):
        def response(request):
            if not self.starts:
                self.starts.append(self.clock())
                return web.json_response({'detail': {'error': 'busy'}}, status=429, headers={'Retry-After': '1'})
            return self.answer(request)
        self.response = response
        await self.sampler.run()
        self.assertEqual(self.starts, [self.base, self.base+140])
        self.assertEqual(self.store.pending(self.entries, day_id(self.wall())), [])
        self.assertEqual(len(self.errors), 1)

    async def test_busy_search_and_lookup_use_separate_paced_steps(self):
        self.entries = [('pve', 'Named')]
        self.sampler.enabled = lambda: len(self.starts) < 3
        def response(request):
            self.starts.append(self.clock())
            if len(self.starts) == 1:
                return web.json_response({'detail': {'error': 'busy'}}, status=429, headers={'Retry-After': '1'})
            if request.path.endswith('/search'):
                return web.json_response({'players': [{'accountId': '1234567', 'nickname': 'Named'}]})
            return web.json_response(atlas_profile('pve', looked_up=iso(self.wall())))
        self.response = response
        await self.sampler.run()
        self.assertEqual(self.starts, [self.base, self.base+140, self.base+280])
        self.assertEqual([path for path, _, _ in self.seen],
                         ['/atlas/api/lookup/search', '/atlas/api/lookup/search', '/atlas/api/lookup'])

    async def test_long_upstream_pause_is_not_shortened_by_sampling_gap(self):
        def response(request):
            if not self.starts:
                self.starts.append(self.clock())
                return web.json_response({'detail': {'error': 'busy'}}, status=429, headers={'Retry-After': '600'})
            return self.answer(request)
        self.response = response
        await self.sampler.run()
        self.assertEqual(len(self.starts), 2)
        self.assertGreaterEqual(self.starts[1]-self.starts[0], 600)

    async def test_waiting_past_nine_never_sends_or_charges(self):
        self.wall = lambda: LAST+6*3600-5+self.clock()-self.base
        self.gate.next_start = self.clock()+10
        self.assertEqual(await self.sampler.step(), 'deferred')
        self.assertEqual(self.starts, [])
        self.assertEqual(list(self.budget.sent), [])
        self.assertEqual(self.store.pending(self.entries, day_id(self.wall())), self.entries)

    async def test_oversleep_cannot_admit_a_request_after_nine(self):
        self.wall = lambda: LAST+6*3600-5+self.clock()-self.base
        self.gate.next_start = self.clock()+1
        async def delayed(seconds):
            self.clock.advance(seconds+10)
        self.gate.claim_within = lambda limit, **kwargs: self.claim(limit, sleep=delayed, **kwargs)
        self.assertEqual(await self.sampler.step(), 'deferred')
        self.assertEqual(self.starts, [])
        self.assertEqual(list(self.budget.sent), [])
        self.assertFalse(self.gate.in_flight)

    async def test_exact_deadline_rejects_even_with_a_free_gate(self):
        with self.assertRaises(AtlasDeadlineError):
            await self.client.lookup(1234567, 'pve', deadline=self.clock())
        self.assertEqual((self.seen, list(self.budget.sent)), ([], []))

    async def test_in_flight_request_is_cancelled_at_deadline_and_gate_is_released(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def waiting(request):
            entered.set()
            await release.wait()
            return self.answer(request)
        self.response = waiting
        task = asyncio.create_task(self.players.profile_once('pve', 1234567, deadline=self.clock()+0.1))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            with self.assertRaises(TimeoutError):
                await task
            self.assertFalse(self.gate.in_flight)
            self.assertEqual(self.client.tasks, set())
            self.assertEqual(len(self.budget.sent), 1)  # The request was actually sent, not refundable.
            self.assertIsNone(self.players.cached('pve', 1234567))
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
