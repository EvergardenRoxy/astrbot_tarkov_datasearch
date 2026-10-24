import asyncio
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tarkov_core.public_api import DataError, ResourceKey
from tarkov_core.resource_cache import ResourceCache
from tarkov_core.transport import ConnectionFailure, HTTPError, NotFound, QueryError


class ResourceCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'cache'
        self.key = ResourceKey('items', 'regular')
        self.calls = []
        self.version = 1
        self.cache = ResourceCache(self.root, self.download)

    async def asyncTearDown(self):
        await self.cache.close()
        self.temp.cleanup()

    async def download(self, key, **validators):
        self.calls.append((key, validators))
        tag = f'"v{self.version}"'
        if validators.get('etag') == tag:
            return {'status': 304, 'etag': tag}, None
        body = {'data': {'key': key.path} if key.language else {'items': {}, 'version': self.version}}
        return {'status': 200, 'etag': tag, 'last_modified': 'Wed, 21 Oct 2026 00:00:00 GMT'}, json.dumps(body).encode()

    async def test_lazy_start_ttl_revalidation_and_restart(self):
        self.assertFalse(self.root.exists())
        first = await self.cache.get(self.key)
        self.assertFalse(first.from_cache)
        self.assertTrue(self.cache.path.exists())
        second = await self.cache.get(self.key)
        self.assertTrue(second.from_cache)
        self.assertEqual(len(self.calls), 1)
        renewed = await self.cache.get(self.key, fresh=True)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(renewed.body, first.body)
        self.assertEqual(renewed.fetched_at, first.fetched_at)
        self.assertGreaterEqual(renewed.checked_at, first.checked_at)
        self.version = 2
        changed = await self.cache.get(self.key, ttl_seconds=0)
        self.assertEqual((await changed.json())['data']['version'], 2)
        await self.cache.close()
        self.cache = ResourceCache(self.root, self.download)
        restored = await self.cache.get(self.key)
        self.assertEqual(restored.body, changed.body)
        self.assertEqual(len(self.calls), 3)

    async def test_mode_and_dictionary_caches_are_separate_and_readers_cannot_mutate_them(self):
        keys = [self.key, ResourceKey('items', 'pve'), ResourceKey('items', 'pvp-season'),
                ResourceKey('items', 'regular', 'zh'), ResourceKey('items', 'regular', 'en')]
        snapshots = await asyncio.gather(*(self.cache.get(key) for key in keys))
        self.assertEqual(len(self.calls), 5)
        first_doc = await snapshots[0].json()
        first_doc['data']['version'] = 100
        self.assertEqual((await snapshots[0].json())['data']['version'], 1)
        self.assertEqual((await snapshots[-2].json())['data']['key'], '/regular/items_zh')

    async def test_concurrent_checks_share_one_request_and_cancelled_reader_does_not_cancel_it(self):
        gate = asyncio.Event()
        async def slow(key, **kwargs):
            await gate.wait()
            return await self.download(key, **kwargs)
        self.cache.download = slow
        tasks = [asyncio.create_task(self.cache.get(self.key)) for _ in range(10)]
        await asyncio.sleep(.02)
        tasks[0].cancel()
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertTrue(all(result.body == results[1].body for result in results[1:]))
        self.assertEqual(len(self.calls), 1)

    async def test_stale_fallback_is_opt_in_and_preserves_success_times(self):
        old = await self.cache.get(self.key)
        for failure in (HTTPError('service unavailable', 503), TimeoutError('timeout'), ConnectionFailure('connection failed'),
                        DataError('invalid upstream JSON')):
            async def failed(*args, **kwargs): raise failure
            self.cache.download = failed
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(type(failure)):
                    await self.cache.get(self.key, fresh=True)
                fallback = await self.cache.get(self.key, fresh=True, allow_stale=True)
                self.assertTrue(fallback.stale)
                self.assertEqual(fallback.error, str(failure))
                self.assertEqual((fallback.body, fallback.fetched_at, fallback.checked_at),
                                 (old.body, old.fetched_at, old.checked_at))
        async def invalid_proxy(*args, **kwargs): raise QueryError('代理地址无效')
        self.cache.download = invalid_proxy
        with self.assertRaises(QueryError):
            await self.cache.get(self.key, fresh=True, allow_stale=True)

    async def test_404_and_failure_without_a_cached_body_do_not_manufacture_data(self):
        async def failed(*args, **kwargs): raise HTTPError('unavailable', 503)
        self.cache.download = failed
        with self.assertRaises(HTTPError):
            await self.cache.get(self.key, allow_stale=True)
        self.cache.download = self.download
        await self.cache.get(self.key)
        async def missing(*args, **kwargs): raise NotFound()
        self.cache.download = missing
        with self.assertRaises(NotFound):
            await self.cache.get(self.key, fresh=True, allow_stale=True)
        self.assertIsNone(await self.cache._thread(self.cache._read, self.key))

    async def test_invalid_download_cannot_replace_last_successful_document(self):
        old = await self.cache.get(self.key)
        async def invalid(*args, **kwargs): return {'status': 200, 'etag': 'bad'}, b'{"data":{}}'
        self.cache.download = invalid
        with self.assertRaises(DataError):
            await self.cache.get(self.key, fresh=True)
        self.assertEqual((await self.cache.get(self.key)).body, old.body)

    async def test_corrupt_entry_is_rebuilt_without_sending_its_etag(self):
        await self.cache.get(self.key)
        with sqlite3.connect(self.cache.path) as db:
            db.execute('UPDATE resources SET body=?', (b'not json',))
        restored = await self.cache.get(self.key)
        self.assertEqual(self.calls[-1][1], {'etag': None, 'last_modified': None})
        self.assertEqual((await restored.json())['data']['version'], 1)

    async def test_bad_304_without_a_body_and_non_finite_ttl(self):
        async def unexpected(*args, **kwargs): return {'status': 304}, None
        self.cache.download = unexpected
        with self.assertRaises(DataError):
            await self.cache.get(self.key)
        for ttl in (-1, float('nan'), float('inf'), '5'):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                await self.cache.get(self.key, ttl_seconds=ttl)

    async def test_retry_after_suppresses_repeat_downloads(self):
        old = await self.cache.get(self.key)
        attempts = []
        async def limited(*args, **kwargs):
            attempts.append(1)
            raise HTTPError('rate limited', 429, 20)
        self.cache.download = limited
        for _ in range(3):
            stale = await self.cache.get(self.key, fresh=True, allow_stale=True)
            self.assertTrue(stale.stale)
            self.assertEqual(stale.body, old.body)
        self.assertEqual(len(attempts), 1)

    async def test_lru_budget_and_no_store(self):
        await self.cache.close()
        self.cache = ResourceCache(self.root, self.download, max_entries=2, max_bytes=1000)
        for mode in ('regular', 'pve', 'pvp-season'):
            await self.cache.get(ResourceKey('items', mode))
        with sqlite3.connect(self.cache.path) as db:
            paths = [row[0] for row in db.execute('SELECT path FROM resources')]
        self.assertEqual(set(paths), {'/pve/items', '/pvp-season/items'})
        async def no_store(key, **kwargs):
            meta, body = await self.download(key, **kwargs)
            meta['cache_control'] = 'no-store'
            return meta, body
        self.cache.download = no_store
        await self.cache.get(self.key)
        count = len(self.calls)
        await self.cache.get(self.key)
        self.assertEqual(len(self.calls), count+1)

    async def test_close_waits_for_sqlite_worker_and_cancels_inflight_queries(self):
        started, release = threading.Event(), threading.Event()
        original = self.cache._write
        def slow_write(snapshot):
            started.set()
            release.wait(3)
            original(snapshot)
        self.cache._write = slow_write
        request = asyncio.create_task(self.cache.get(self.key))
        await asyncio.to_thread(started.wait, 3)
        closing = asyncio.create_task(self.cache.close())
        await asyncio.sleep(.02)
        self.assertFalse(closing.done())
        release.set()
        await closing
        with self.assertRaises(asyncio.CancelledError): await request
        with self.assertRaises(RuntimeError): await self.cache.get(self.key)
        self.assertFalse(self.cache.pending)

    async def test_download_concurrency_is_bounded_across_different_resources(self):
        running, peak = 0, 0
        gate = asyncio.Event()
        async def slow(key, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await gate.wait()
            running -= 1
            return await self.download(key, **kwargs)
        self.cache.download = slow
        keys = [ResourceKey('items', mode, language) for mode in ('regular', 'pve', 'pvp-season')
                for language in (None, 'zh')]
        tasks = [asyncio.create_task(self.cache.get(key)) for key in keys]
        await asyncio.sleep(.02)
        gate.set()
        await asyncio.gather(*tasks)
        self.assertEqual(peak, 2)

    async def test_byte_budget_evicts_and_http_no_cache_revalidates(self):
        await self.cache.close()
        self.cache = ResourceCache(self.root, self.download, max_bytes=100)
        for mode in ('regular', 'pve', 'pvp-season'):
            await self.cache.get(ResourceKey('items', mode))
        with sqlite3.connect(self.cache.path) as db:
            self.assertLessEqual(db.execute('SELECT SUM(LENGTH(body)) FROM resources').fetchone()[0], 100)
        async def no_cache(key, **kwargs):
            meta, body = await self.download(key, **kwargs)
            meta['cache_control'] = 'no-cache'
            return meta, body
        self.cache.download = no_cache
        await self.cache.get(self.key, fresh=True)
        count = len(self.calls)
        await self.cache.get(self.key)
        self.assertEqual(len(self.calls), count+1)

    async def test_fresh_reader_upgrades_an_inflight_ttl_lookup_to_a_conditional_check(self):
        await self.cache.get(self.key)
        original = self.cache._read
        started, release = threading.Event(), threading.Event()
        def slow_read(key):
            started.set()
            release.wait(3)
            return original(key)
        self.cache._read = slow_read
        ordinary = asyncio.create_task(self.cache.get(self.key))
        await asyncio.to_thread(started.wait, 3)
        forced = asyncio.create_task(self.cache.get(self.key, fresh=True))
        await asyncio.sleep(.01)
        release.set()
        first, second = await asyncio.gather(ordinary, forced)
        self.assertEqual(len(self.calls), 2)  # initial download, then a shared conditional check
        self.assertEqual(first.checked_at, second.checked_at)
