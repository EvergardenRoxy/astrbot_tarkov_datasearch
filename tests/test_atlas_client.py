import asyncio
import gzip
import json
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from aiohttp import web

from tarkov_core import atlas_client
from tarkov_core.atlas_client import AtlasClient, AtlasDataError, AtlasHTTPError
from tarkov_core.atlas_cooldown import AtlasAccessPaused, AtlasBudget, AtlasCooldown, AtlasCooldownError, AtlasNotSent
from tarkov_core.matching import MatchingService
from tarkov_core.transport import HTTPClient, QueryError
from test_atlas_cooldown import Clock
from test_matching import matching_data


class AtlasHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.gate = AtlasCooldown(clock=self.clock)
        self.client = AtlasClient(HTTPClient(), cooldown=self.gate)
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
        await self.client.http.close()
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
        service = MatchingService(self.client, clock=self.clock)
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

    async def test_stats_and_live_parameters(self):
        await self.client.lookup(1234567, 'pve', include_art=False)
        self.clock.advance(10)
        await self.client.search('Old_Name', 'pve', live=True)
        self.assertEqual(self.seen[0][1], {'accountId': '1234567', 'mode': 'pve', 'art': '0'})
        self.assertEqual(self.seen[1][1], {'nickname': 'Old_Name', 'mode': 'pve', 'live': '1'})
        self.assertEqual(len(self.seen), 2)

    async def test_a_refusal_pauses_its_scope_for_half_an_hour_then_asks_again(self):
        self.client.budget = AtlasBudget(clock=self.clock)
        self.response = lambda request: web.json_response({'error': 'forbidden'}, status=403)
        with self.assertRaises(AtlasHTTPError):
            await self.client.lookup(1, 'pve')
        self.clock.advance(60)
        with self.assertRaises(AtlasAccessPaused) as caught:
            await self.client.lookup(1, 'pve')
        self.assertEqual(caught.exception.retry_after_seconds, 29*60)
        with self.assertRaises(AtlasAccessPaused):
            await self.client.search('Some_Name', 'pve')
        # A request that was waiting for its turn when the refusal came is not sent either.
        waited = self.client.cooldown.claim_within

        def turn(*args, **kwargs):
            self.client.refused_until.pop('players')
            gate = waited(*args, **kwargs)
            self.client.refused_until['players'] = self.clock()+60
            return gate
        self.client.cooldown.claim_within = turn
        with self.assertRaises(AtlasAccessPaused):
            await self.client.lookup(1, 'pve')
        self.client.cooldown.claim_within = waited
        # Nothing more was sent or counted, and matching (another scope) is still asked.
        self.assertEqual((len(self.seen), len(self.client.budget.sent)), (1, 1))
        self.response = lambda request: web.json_response(matching_data())
        await self.client.matching()
        self.clock.advance(29*60)
        self.response = lambda request: web.json_response({'nickname': 'Some_Name', 'players': []})
        await self.client.search('Some_Name', 'pve')
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
        service = MatchingService(self.client, clock=self.clock)
        try:
            await service.get()
            self.client.proxy = lambda: 'http://name:password@invalid/path'
            deadline = self.gate.next_start
            with self.assertRaises(QueryError):
                await service.get()
            with self.assertRaises(QueryError):
                await self.client.matching()
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
        await self.client.http.close()
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
        with self.assertRaises(AtlasNotSent):
            await client.matching()
