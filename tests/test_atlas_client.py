import asyncio
import gzip
import json
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from aiohttp import web

from tarkov_core import atlas_client
from tarkov_core.atlas_client import AtlasClient, AtlasDataError, AtlasHTTPError
from tarkov_core.atlas_cooldown import AtlasCooldown, AtlasCooldownError, AtlasBudget
from tarkov_core.atlas_access import AtlasAccess, AtlasAccessPaused
from tarkov_core.matching import MatchingService
from tarkov_core.transport import HTTPClient, QueryError
from test_atlas_cooldown import Clock
from test_matching import matching_data


class AtlasHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.gate = AtlasCooldown(clock=self.clock)
        self.client = AtlasClient(cooldown=self.gate)
        self.seen = []
        self.response = lambda request: web.json_response(matching_data())
        async def serve(request):
            self.seen.append((request.path, dict(request.query), dict(request.headers)))
            response = self.response(request)
            return await response if asyncio.iscoroutine(response) else response
        app = web.Application()
        app.router.add_get('/{path:.*}', serve)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.host_patch = patch.object(atlas_client, 'HOST', f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}')
        self.host_patch.start()

    async def asyncTearDown(self):
        await self.client.close()
        self.host_patch.stop()
        await self.runner.cleanup()

    async def test_fixed_request_gzip_no_cookies_and_ambient_proxy(self):
        self.response = lambda request: web.Response(body=gzip.compress(json.dumps(matching_data()).encode()),
            headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip', 'Set-Cookie': 'secret=abc'})
        with patch.dict('os.environ', {'HTTP_PROXY': 'http://invalid:1', 'HTTPS_PROXY': 'http://invalid:1'}):
            result = await self.client.matching()
            self.clock.advance(10)
            await self.client.matching()
        self.assertEqual(result['hours'], 24)
        self.assertEqual(self.seen[0][:2], ('/atlas/api/matching', {'hours': '24'}))
        self.assertIn('astrbot_tarkov_datasearch', self.seen[0][2]['User-Agent'])
        self.assertNotIn('Cookie', self.seen[1][2])
        self.assertFalse(self.client.http.session.trust_env)

    async def test_cache_window_and_shared_player_admission(self):
        service = MatchingService(self.client, enabled=lambda: True, clock=self.clock)
        try:
            await service.get()
            for _ in range(20):
                self.assertTrue((await service.get()).from_cache)
            self.assertEqual(len(self.seen), 1)
            # Future player methods will use the very same gate.
            with self.assertRaises(AtlasCooldownError):
                async with self.gate.claim():
                    pass
            self.clock.advance(10)
            async with self.gate.claim():
                pass  # Matching's independent 15-minute window does not block players.
            self.assertTrue((await service.get()).from_cache)
        finally:
            await service.close()

    async def test_429_updates_shared_pause_and_bounded_error_code(self):
        self.response = lambda request: web.json_response({'detail': {'error': 'busy', 'private': 'secret'}},
            status=429, headers={'Retry-After': '90000'})
        with self.assertRaises(AtlasHTTPError) as caught:
            await self.client.matching()
        self.assertEqual(caught.exception.code, 'busy')
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(caught.exception.retry_after, 90000)
        self.clock.advance(6)
        with self.assertRaises(AtlasCooldownError):
            async with self.gate.claim():
                pass
        self.assertEqual(len(self.seen), 1)

    async def test_malformed_error_body_still_preserves_429_pause(self):
        self.response = lambda request: web.Response(status=429, body=b'x'*70000)
        with self.assertRaises(AtlasHTTPError) as caught:
            await self.client.matching()
        self.assertEqual(caught.exception.retry_after, 60)
        self.assertEqual(caught.exception.code, 'http')
        self.assertEqual(self.gate.paused_until, self.clock()+60)

    async def test_no_redirects_and_service_failures(self):
        for status in (301, 404, 503, 403):
            self.response = lambda request, status=status: web.Response(status=status,
                headers={'Location': '/redirect-target'}, text='private upstream text')
            with self.assertRaises(AtlasHTTPError) as caught:
                await self.client.matching()
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn('private', str(caught.exception))
            self.clock.advance(10)
        self.assertEqual(len(self.seen), 4)

    async def test_stats_and_live_parameters_and_strict_validation(self):
        await self.client.lookup(1234567, 'pve', include_art=False)
        self.clock.advance(10)
        await self.client.search('Old_Name', 'pve', live=True)
        self.assertEqual(self.seen[0][1], {'accountId': '1234567', 'mode': 'pve', 'art': '0'})
        self.assertEqual(self.seen[1][1], {'nickname': 'Old_Name', 'mode': 'pve', 'live': '1'})
        for value in (0, 1, '0', None):
            with self.assertRaises(ValueError):
                await self.client.lookup(1, 'pve', include_art=value)
            with self.assertRaises(ValueError):
                await self.client.search('Old_Name', 'pve', live=value)
        for path, params in ((atlas_client.LOOKUP_PATH, {'accountId':'1', 'mode':'pve', 'art':'1'}),
                             (atlas_client.SEARCH_PATH, {'nickname':'Old_Name', 'mode':'pve', 'live':'0'}),
                             (atlas_client.LOOKUP_PATH, {'accountId':'1', 'mode':'pve', 'art':'0', 'url':'evil'})):
            with self.assertRaises(ValueError):
                await self.client._get(path, params)
        self.assertEqual(len(self.seen), 2)

    async def test_refusal_blocks_players_without_spending_more_budget_or_blocking_matching(self):
        self.client.budget = AtlasBudget(clock=self.clock)
        self.response = lambda request: web.json_response({'error':'forbidden'}, status=403)
        with self.assertRaises(AtlasHTTPError):
            await self.client.lookup(1, 'pve')
        self.assertEqual(self.client.budget.remaining_hour(), 199)
        self.clock.advance(3600)
        for mode in ('pve', 'pvp', 'season', 'arena'):
            with self.assertRaises(AtlasAccessPaused):
                await self.client.lookup(2, mode)
            with self.assertRaises(AtlasAccessPaused):
                await self.client.search('Other_Name', mode)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.client.budget.remaining_hour(), 200)
        self.response = lambda request: web.json_response(matching_data())
        await self.client.matching()
        self.assertEqual(len(self.seen), 2)

    async def test_request_already_waiting_rechecks_refusal_before_sending(self):
        entered, release = asyncio.Event(), asyncio.Event()
        self.client.budget = AtlasBudget(clock=self.clock)
        @asynccontextmanager
        async def waiting(*args, **kwargs):
            entered.set()
            await release.wait()
            yield
        with patch.object(self.gate, 'claim_within', waiting):
            task = asyncio.create_task(self.client.lookup(1, 'pve'))
            await entered.wait()
            await self.client.access.block('players', 403)
            release.set()
            with self.assertRaises(AtlasAccessPaused):
                await task
        self.assertEqual(self.seen, [])
        self.assertEqual(self.client.budget.remaining_hour(), 200)

    async def test_recovery_probe_respects_upstream_pause_and_does_not_retry_busy(self):
        await self.client.access.block('players', 403)
        await self.client.access.arm_recovery()
        self.response = lambda request: web.json_response({'error':'busy'}, status=429, headers={'Retry-After':'100'})
        with self.assertRaises(AtlasHTTPError):
            await self.client.lookup(1, 'pve')
        with self.assertRaises(AtlasAccessPaused):
            await self.client.lookup(1, 'pve')
        await self.client.access.arm_recovery()
        with self.assertRaises(AtlasCooldownError):
            await self.client.lookup(1, 'pve', max_wait=0)
        self.assertEqual(len(self.seen), 1)
        self.clock.advance(100)
        self.response = lambda request: web.json_response({'nickname':'Synthetic'})
        await self.client.lookup(1, 'pve')
        self.assertFalse(self.client.access.is_blocked('players'))
        self.clock.advance(10)
        await self.client.lookup(2, 'pve')
        self.assertEqual(len(self.seen), 3)

    async def test_matching_refusal_requires_recovery_even_after_two_minutes(self):
        service = MatchingService(self.client, enabled=lambda: True, clock=self.clock)
        self.addAsyncCleanup(service.close)
        await service.get()
        self.clock.advance(900)
        self.response = lambda request: web.Response(status=403)
        with self.assertRaises(AtlasHTTPError):
            await service.get()
        self.clock.advance(1200)
        with self.assertRaises(AtlasAccessPaused):
            await service.get()
        self.assertEqual(len(self.seen), 2)
        await self.client.access.arm_recovery()
        self.response = lambda request: web.json_response(matching_data())
        self.assertFalse((await service.get()).from_cache)
        self.assertEqual(len(self.seen), 3)

    async def test_wrong_media_json_and_gzip_are_rejected(self):
        responses = [web.Response(text='{}'), web.Response(body=b'{"a":NaN}', content_type='application/json'),
                     web.Response(body=b'bad', headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip'}),
                     web.Response(body=gzip.compress(b'{}')[:-2], headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip'}),
                     web.Response(body=b'', content_type='application/json')]
        for response in responses:
            self.response = lambda request: response
            with self.assertRaises(AtlasDataError):
                await self.client.matching()
            self.clock.advance(10)

    async def test_compressed_decoded_and_raw_limits(self):
        for body, encoding in [(b'x'*65, ''), (gzip.compress(b' '*200), 'gzip'),
                               (gzip.compress(b'{}')+b'trailing', 'gzip')]:
            self.response = lambda request: web.Response(body=body, content_type='application/json',
                headers={'Content-Encoding': encoding})
            with patch.object(atlas_client, 'LIMITS', (64, 128)), self.assertRaises(AtlasDataError):
                await self.client.matching()
            self.clock.advance(10)

    async def test_invalid_proxy_and_path_fail_before_admission_even_with_cache(self):
        service = MatchingService(self.client, enabled=lambda: True, clock=self.clock)
        try:
            await service.get()
            self.client.proxy = lambda: 'http://name:password@invalid/path'
            deadline = self.gate.next_start
            with self.assertRaises(QueryError):
                await service.get()
            with self.assertRaises(QueryError):
                await self.client.matching()
            with self.assertRaises(ValueError):
                await self.client._get('https://invalid/', {})
            self.assertEqual(deadline, self.gate.next_start)
            self.assertEqual(len(self.seen), 1)
        finally:
            await service.close()

    async def test_close_cancels_request_and_does_not_close_borrowed_pool(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def response(request):
            started.set()
            await release.wait()
            return web.json_response(matching_data())
        self.response = response
        task = asyncio.create_task(self.client.matching())
        await started.wait()
        await self.client.close()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.gate.in_flight)
        self.assertTrue(self.client.http.closed)
        pool = HTTPClient()
        client = AtlasClient(client=pool)
        await client.close()
        self.assertFalse(pool.closed)
        await pool.close()
        with self.assertRaises(RuntimeError):
            await client.matching()
