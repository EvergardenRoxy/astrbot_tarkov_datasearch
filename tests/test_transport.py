import gzip
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

from fixtures import profile
from tarkov_core import transport
from tarkov_core.transport import NotFound, QueryError


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.routes = {}
        app = web.Application()
        app.router.add_get('/{tail:.*}', self.serve)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.host = patch.object(transport, 'HOST', f'http://127.0.0.1:{port}')
        self.host.start()

    async def asyncTearDown(self):
        self.host.stop()
        await self.runner.cleanup()

    async def serve(self, request):
        self.requests.append(request)
        handler = self.routes.get(request.path)
        if handler is None:
            return web.Response(status=404, text='<html>404</html>', content_type='text/html')
        return handler(request)

    @staticmethod
    def json_response(data, gzipped=True, **headers):
        body = json.dumps(data).encode()
        if gzipped:
            body = gzip.compress(body)
            headers['Content-Encoding'] = 'gzip'
        return web.Response(body=body, headers={'Content-Type': 'application/json', **headers})

    async def test_profile_is_fetched_gzipped_with_plugin_agent(self):
        self.routes['/pve/1234567.json'] = lambda r: self.json_response(profile())
        data = await transport.fetch_profile('pve', 1234567)
        self.assertEqual(data['info']['nickname'], 'Test_Player')
        request = self.requests[0]
        self.assertTrue(request.headers['User-Agent'].startswith('astrbot_tarkov_datasearch/'))
        self.assertEqual(request.headers['Accept-Encoding'], 'gzip')

    async def test_plain_body_is_accepted(self):
        self.routes['/profile/1.json'] = lambda r: self.json_response(profile(), gzipped=False)
        self.assertEqual((await transport.fetch_profile('profile', 1))['aid'], 1234567)

    async def test_missing_profile(self):
        with self.assertRaises(NotFound):
            await transport.fetch_profile('pvp-season', 1)

    async def test_refusals_have_chat_messages(self):
        for status, words in ((403, 'Cloudflare'), (429, '429'), (500, 'HTTP 500')):
            with self.subTest(status=status):
                self.routes['/pve/1.json'] = lambda r, s=status: web.Response(status=s, text='<html></html>', content_type='text/html')
                with self.assertRaises(QueryError) as caught:
                    await transport.fetch_profile('pve', 1)
                self.assertNotIsInstance(caught.exception, NotFound)
                self.assertIn(words, str(caught.exception))

    async def test_redirects_are_not_followed(self):
        self.routes['/pve/1.json'] = lambda r: web.Response(status=302, headers={'Location': '/pve/2.json'})
        self.routes['/pve/2.json'] = lambda r: self.json_response(profile())
        with self.assertRaises(QueryError):
            await transport.fetch_profile('pve', 1)
        self.assertEqual([r.path for r in self.requests], ['/pve/1.json'])

    async def test_html_and_non_object_bodies_are_rejected(self):
        self.routes['/pve/1.json'] = lambda r: web.Response(text='<html>blocked</html>', content_type='text/html')
        self.routes['/pve/2.json'] = lambda r: self.json_response([1, 2])
        self.routes['/pve/3.json'] = lambda r: web.Response(body=b'{not json', content_type='application/json')
        for aid in (1, 2, 3):
            with self.subTest(aid=aid):
                with self.assertRaises(QueryError):
                    await transport.fetch_profile('pve', aid)

    async def test_size_limits(self):
        self.routes['/pve/1.json'] = lambda r: self.json_response({'pad': 'x'*5000})
        with patch.object(transport, 'PROFILE_LIMITS', (10**6, 1000)):
            with self.assertRaises(QueryError) as caught:
                await transport.fetch_profile('pve', 1)
        self.assertIn('大小上限', str(caught.exception))
        with patch.object(transport, 'PROFILE_LIMITS', (10, 10**6)):
            with self.assertRaises(QueryError):
                await transport.fetch_profile('pve', 1)

    async def test_plain_body_over_the_decoded_limit(self):
        self.routes['/pve/1.json'] = lambda r: self.json_response({'pad': 'x'*5000}, gzipped=False)
        with patch.object(transport, 'PROFILE_LIMITS', (10**6, 1000)):
            with self.assertRaises(QueryError) as caught:
                await transport.fetch_profile('pve', 1)
        self.assertIn('大小上限', str(caught.exception))

    async def test_json_under_another_content_type_is_rejected(self):
        body = json.dumps(profile()).encode()
        self.routes['/pve/1.json'] = lambda r: web.Response(body=body, content_type='text/plain')
        with self.assertRaises(QueryError):
            await transport.fetch_profile('pve', 1)

    async def test_truncated_gzip(self):
        body = gzip.compress(json.dumps(profile()).encode())[:-10]
        self.routes['/pve/1.json'] = lambda r: web.Response(body=body, headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})
        with self.assertRaises(QueryError):
            await transport.fetch_profile('pve', 1)

    async def test_other_encodings_are_refused(self):
        self.routes['/pve/1.json'] = lambda r: web.Response(body=b'x', headers={'Content-Type': 'application/json', 'Content-Encoding': 'br'})
        with self.assertRaises(QueryError):
            await transport.fetch_profile('pve', 1)

    async def test_index_download_and_conditional_request(self):
        def index(request):
            if request.headers.get('If-None-Match') == '"v1"':
                return web.Response(status=304)
            return self.json_response({'15': 'Buhaus'}, ETag='"v1"')
        self.routes['/pve/index.json'] = index
        with tempfile.TemporaryDirectory() as root:
            target = Path(root)/'index'
            self.assertEqual(await transport.fetch_index('pve', target), {'status': 200, 'etag': '"v1"'})
            self.assertEqual(json.loads(target.read_bytes()), {'15': 'Buhaus'})
            self.assertEqual((await transport.fetch_index('pve', target, etag='"v1"'))['status'], 304)

    async def test_connection_errors_hide_details(self):
        self.host.stop()
        with patch.object(transport, 'HOST', 'http://127.0.0.1:9'):
            with self.assertRaises(QueryError) as caught:
                await transport.fetch_profile('pve', 1)
        self.host.start()
        self.assertNotIn('127.0.0.1', str(caught.exception))

    async def test_environment_proxy_is_ignored(self):
        self.routes['/pve/1.json'] = lambda r: self.json_response(profile())
        dead = 'http://127.0.0.1:9'
        with patch.dict(os.environ, {'HTTP_PROXY': dead, 'http_proxy': dead, 'ALL_PROXY': dead, 'NO_PROXY': '', 'no_proxy': ''}):
            self.assertEqual((await transport.fetch_profile('pve', 1))['aid'], 1234567)

    async def test_proxy_must_be_valid(self):
        for value in ('socks5://h:1', 'http://h', 'http://h:1/path', 'not a url'):
            with self.subTest(value=value):
                with self.assertRaises(QueryError):
                    await transport.get('/x', io.BytesIO(), limits=(1, 1), timeout_seconds=5, proxy=value)
        self.assertFalse(self.requests)
