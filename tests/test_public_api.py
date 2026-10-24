import asyncio
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

from tarkov_core import public_api
from tarkov_core.public_api import DataError, ResourceKey, mode_path
from tarkov_core.transport import HTTPError, NotFound, QueryError


class PublicAPIKeysTests(unittest.TestCase):
    def test_modes_resources_and_languages_have_distinct_paths(self):
        self.assertEqual(mode_path('pvp'), 'regular')
        self.assertEqual(mode_path('season'), 'pvp-season')
        self.assertEqual(ResourceKey('items', 'regular', 'zh').path, '/regular/items_zh')
        self.assertEqual(ResourceKey('items', 'pve').path, '/pve/items')
        self.assertEqual(ResourceKey('season').path, '/pvp-season/season')
        self.assertEqual(ResourceKey('status').path, '/status')
        self.assertEqual(ResourceKey('prices', 'pve', item_id='544fb45d4bdc2dee738b4568').path,
                         '/pve/prices/544fb45d4bdc2dee738b4568')
        for mode in ('arena', 'profile', 'regular'):
            with self.assertRaises(ValueError):
                mode_path(mode)

    def test_no_arbitrary_hosts_paths_or_unsupported_translations(self):
        invalid = [ResourceKey('../status'), ResourceKey('https://example.com'), ResourceKey('items', 'profile'),
                   ResourceKey('items', 'arena'), ResourceKey('items', 'regular/../pve'),
                   ResourceKey('items', 'regular', '../../status'), ResourceKey('barters', 'pve', 'zh'),
                   ResourceKey('status', 'pve'), ResourceKey('season', 'pve'),
                   ResourceKey('prices', 'pve', item_id='../status'), ResourceKey('prices', 'pve'),
                   ResourceKey('items', 'regular', item_id='544fb45d4bdc2dee738b4568')]
        for key in invalid:
            with self.subTest(key=key), self.assertRaises(ValueError):
                key.path

    def test_envelope_shape_and_finite_numbers_are_checked(self):
        key = ResourceKey('items', 'regular')
        for body in (b'[]', b'{}', b'{"data": []}', b'{"data": {}}',
                     b'{"data":{"items":{}},"translations":"bad"}',
                     b'{"data":{"items":{},"x":NaN}}', b'{"data":{"items":{},"x":Infinity}}'):
            with self.subTest(body=body), self.assertRaises(DataError):
                public_api.decode(key, body)
        self.assertEqual(public_api.decode(key, b'{"data":{"items":{}},"translations":[]}')['data'], {'items': {}})
        with self.assertRaises(DataError):
            public_api.decode(ResourceKey('items', 'regular', 'zh'), b'{"data":{"key":123}}')


class PublicAPIHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen = []
        self.response = lambda request: web.json_response({'data': {'items': {}}})
        app = web.Application()
        async def serve(request):
            self.seen.append(request)
            return self.response(request)
        app.router.add_get('/{path:.*}', serve)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.host = patch.object(public_api, 'HOST', f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}')
        self.host.start()
        self.key = ResourceKey('items', 'regular')

    async def asyncTearDown(self):
        self.host.stop()
        await self.runner.cleanup()

    async def test_gzip_and_cache_validators(self):
        body = b'{"data":{"items":{}}}'
        self.response = lambda r: web.Response(body=gzip.compress(body), content_type='application/json',
                                                headers={'Content-Encoding': 'gzip', 'ETag': '"v1"',
                                                         'Last-Modified': 'Wed, 21 Oct 2026 00:00:00 GMT',
                                                         'Cache-Control': 'max-age=300'})
        meta, got = await public_api.fetch(self.key)
        self.assertEqual(got, body)
        self.assertEqual(meta['etag'], '"v1"')
        self.assertIn('Last-Modified'.lower().replace('-', '_'), meta)
        self.assertEqual(self.seen[-1].path, '/regular/items')
        self.assertEqual(self.seen[-1].headers['Accept-Encoding'], 'gzip')
        self.response = lambda r: web.Response(status=304)
        meta, body = await public_api.fetch(self.key, etag='"v1"', last_modified='old')
        self.assertEqual(meta['status'], 304)
        self.assertIsNone(body)
        self.assertEqual(self.seen[-1].headers['If-None-Match'], '"v1"')
        self.assertNotIn('If-Modified-Since', self.seen[-1].headers)
        await public_api.fetch(self.key, last_modified='Wed, 21 Oct 2026 00:00:00 GMT')
        self.assertIn('If-Modified-Since', self.seen[-1].headers)

    async def test_refusals_and_rate_limit_are_not_missing_resources(self):
        for status in (403, 429, 503, 302):
            self.response = lambda r, s=status: web.Response(status=s, headers={'Retry-After': '17', 'Location': '/elsewhere'})
            with self.subTest(status=status), self.assertRaises(HTTPError) as caught:
                await public_api.fetch(self.key)
            self.assertEqual(caught.exception.status, status)
            if status == 429:
                self.assertEqual(caught.exception.retry_after, 17)
            self.assertNotIsInstance(caught.exception, NotFound)
        self.assertEqual(len(self.seen), 4)  # no redirect followed

    async def test_large_catalog_has_separate_limits_from_profiles(self):
        # The observed regular item resource already exceeds the player's 16 MiB limit.
        pad = 'x'*(17*1024*1024)
        self.response = lambda r: web.Response(body=gzip.compress(json.dumps({'data': {'items': {}, 'pad': pad}}).encode()),
                                                content_type='application/json', headers={'Content-Encoding': 'gzip'})
        _, body = await public_api.fetch(self.key)
        self.assertGreater(len(body), 16*1024*1024)
        with patch.object(public_api, 'RESOURCE_LIMITS', (1024*1024, 1024)):
            with self.assertRaises(QueryError):
                await public_api.fetch(self.key)

    async def test_invalid_path_is_rejected_without_network_and_bad_json_is_rejected(self):
        with self.assertRaises(ValueError):
            await public_api.fetch(ResourceKey('items', '../pve'))
        self.assertFalse(self.seen)
        self.response = lambda r: web.Response(body=b'{"data":{}}', content_type='application/json')
        with self.assertRaises(DataError):
            await public_api.fetch(self.key)
        self.response = lambda r: web.Response(body=b'<html>blocked</html>', content_type='text/html')
        with self.assertRaises(QueryError):
            await public_api.fetch(self.key)
