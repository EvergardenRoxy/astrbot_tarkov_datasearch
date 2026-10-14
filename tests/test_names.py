import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tarkov_core.names import MAX_MATCHES, MAX_SEPARATORS, NAME, RETRY_SECONDS, NameIndex, build, parse_entries, spelling_variants
from tarkov_core import names as names_module
from tarkov_core.transport import QueryError

INDEX = {'15': 'Buhaus', '16': 'buhaus', '18': 'Vladimir-Karpov', '20': 'Sniper\n', '21': 'quote"name', '22': '',
         '23': 'x'*80, '30': 'Stea1'}


def document(entries=INDEX):
    return json.dumps(entries).encode()


class FakeDownload:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    async def __call__(self, mode_path, target, etag):
        self.calls.append((mode_path, etag))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            return {'status': 304, 'etag': etag}
        Path(target).write_bytes(payload)
        return {'status': 200, 'etag': f'"v{len(self.calls)}"'}


class ParseTests(unittest.TestCase):
    def test_entries(self):
        entries = list(parse_entries(document()))
        self.assertIn(('buhaus', 15, 'Buhaus'), entries)
        self.assertIn(('buhaus', 16, 'buhaus'), entries)
        # Escapes are decoded, whitespace trimmed; empty and over-long names are skipped.
        self.assertIn(('sniper', 20, 'Sniper'), entries)
        self.assertIn(('quote"name', 21, 'quote"name'), entries)
        self.assertNotIn(22, [aid for _, aid, _ in entries])
        self.assertNotIn(23, [aid for _, aid, _ in entries])

    def test_control_characters_in_names_are_removed(self):
        entries = list(parse_entries(json.dumps({'1': 'a\nb', '2': 'c\u202ed', '3': '\u0000'}).encode()))
        self.assertEqual(entries, [('a b', 1, 'a b'), ('cd', 2, 'cd')])

    def test_not_an_index(self):
        with self.assertRaises(QueryError):
            list(parse_entries(b'<!DOCTYPE html><html>blocked</html>'))

    def test_build_refuses_an_empty_index_and_keeps_the_old_one(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'names-x.sqlite3'
            build(path, document(), '"a"')
            with self.assertRaises(QueryError):
                build(path, b'{}', '"b"')
            db = sqlite3.connect(path)
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='etag'").fetchone()[0], '"a"')
            db.close()

    def test_name_rule(self):
        # Review F-15: auto-generated names such as TarkovCitizen1019349 are longer than 15 (up to 21 seen).
        for name, ok in (('Some_Player', True), ('a-b', True), ('ab', False), ('TarkovCitizen1019349', True),
                         ('x'*24, True), ('x'*25, False), ('名字', False), ('a b', False)):
            with self.subTest(name=name):
                self.assertEqual(bool(NAME.fullmatch(name)), ok)


class VariantTests(unittest.TestCase):
    def test_spelling_variants(self):
        self.assertEqual(spelling_variants('demo_player'), ['demo-player'])
        self.assertEqual(sorted(spelling_variants(' A-b_C ')), ['a-b-c', 'a_b-c', 'a_b_c'])
        self.assertEqual(spelling_variants('NoSeparators'), [])
        self.assertEqual(len(spelling_variants('a'+'_b'*MAX_SEPARATORS)), 2**MAX_SEPARATORS-1)
        self.assertEqual(spelling_variants('a'+'_b'*(MAX_SEPARATORS+1)), [])


class IndexTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    async def test_first_lookup_builds_then_reuses(self):
        download = FakeDownload([document()])
        index = NameIndex(self.root, download)
        self.assertTrue(index.needs_refresh('profile'))
        self.assertEqual((await index.lookup('profile', 'BUHAUS'))[0], [(15, 'Buhaus'), (16, 'buhaus')])
        self.assertEqual((await index.lookup('profile', 'stea1'))[0], [(30, 'Stea1')])
        self.assertEqual((await index.lookup('profile', 'nobody'))[0], [])
        self.assertEqual(download.calls, [('profile', None)])
        self.assertFalse(index.needs_refresh('profile'))
        self.assertFalse(list(self.root.glob('*.download')))

    async def test_refresh_sends_etag_and_handles_304(self):
        download = FakeDownload([document(), None])
        index = NameIndex(self.root, download, refresh_hours=1)
        await index.lookup('pve', 'Buhaus')
        index.refresh_seconds = 0
        await index.lookup('pve', 'Buhaus')
        self.assertEqual(download.calls, [('pve', None), ('pve', '"v1"')])

    async def test_changed_index_replaces_the_old_one(self):
        download = FakeDownload([document(), document({'99': 'NewName'})])
        index = NameIndex(self.root, download)
        await index.lookup('pve', 'Buhaus')
        index.refresh_seconds = 0
        self.assertEqual((await index.lookup('pve', 'newname'))[0], [(99, 'NewName')])
        self.assertEqual((await index.lookup('pve', 'buhaus'))[0], [])

    async def test_failed_refresh_keeps_serving_and_waits_before_retrying(self):
        download = FakeDownload([document(), QueryError('blocked')])
        index = NameIndex(self.root, download)
        await index.lookup('pve', 'Buhaus')
        index.refresh_seconds = 0
        self.assertEqual((await index.lookup('pve', 'Stea1'))[0], [(30, 'Stea1')])
        # The failure is remembered: no second download within the retry delay.
        self.assertFalse(index.needs_refresh('pve'))
        await index.lookup('pve', 'Buhaus')
        self.assertEqual(len(download.calls), 2)

    async def test_failure_without_any_index_is_reported_then_backed_off(self):
        # Review F-01: without a database, every lookup used to download the whole index again.
        download = FakeDownload([QueryError('blocked'), document()])
        index = NameIndex(self.root, download)
        with self.assertRaises(QueryError) as first:
            await index.lookup('pve', 'Buhaus')
        self.assertEqual(str(first.exception), 'blocked')
        for _ in range(3):
            with self.assertRaises(QueryError) as later:
                await index.lookup('pve', 'Buhaus')
            self.assertIn('分钟后再试', str(later.exception))
            self.assertIn('blocked', str(later.exception))
        self.assertEqual(len(download.calls), 1)
        self.assertFalse(index.needs_refresh('pve'))
        # After the delay it tries again.
        index.failed_at['pve'] = (time.time()-RETRY_SECONDS-1, 'blocked')
        self.assertTrue(index.needs_refresh('pve'))
        self.assertEqual((await index.lookup('pve', 'Stea1'))[0], [(30, 'Stea1')])
        self.assertEqual(len(download.calls), 2)

    async def test_concurrent_lookups_download_once(self):
        download = FakeDownload([document()])
        index = NameIndex(self.root, download)
        results = await asyncio.gather(*(index.lookup('profile', 'Buhaus') for _ in range(5)))
        self.assertEqual(len(download.calls), 1)
        self.assertTrue(all(result == results[0] for result in results))

    async def test_matches_are_capped(self):
        many = {str(i): 'Same' for i in range(1, 20)}
        index = NameIndex(self.root, FakeDownload([document(many)]))
        self.assertEqual(len((await index.lookup('pve', 'same'))[0]), MAX_MATCHES)

    async def test_corrupt_database_is_rebuilt(self):
        (self.root/'names-pve.sqlite3').write_bytes(b'not a database')
        download = FakeDownload([document()])
        index = NameIndex(self.root, download)
        self.assertTrue(index.needs_refresh('pve'))
        self.assertEqual((await index.lookup('pve', 'Stea1'))[0], [(30, 'Stea1')])

    async def test_data_directory_with_uri_characters(self):
        # Audit verification X-1: an unescaped path in the SQLite URI was cut at '#'/'?' or decoded at '%'.
        for name in ('data#1', 'data?mode=rw', 'data%41'):
            with self.subTest(name=name):
                root = self.root/name
                root.mkdir()
                download = FakeDownload([document()])
                index = NameIndex(root, download)
                self.assertEqual((await index.lookup('pve', 'Stea1'))[0], [(30, 'Stea1')])
                self.assertFalse(index.needs_refresh('pve'))
                await index.lookup('pve', 'Stea1')
                self.assertEqual(len(download.calls), 1)
        # Nothing was created outside the data directories (the '#' case used to create root/'data').
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['data#1', 'data%41', 'data?mode=rw'])

    def test_old_leftovers_are_removed_at_start(self):
        # Review F-02: only leftovers older than an hour; a fresh one may belong to an instance still running.
        old, fresh = self.root/'names-pve.sqlite3.1-a.tmp', self.root/'names-profile.2-b.download'
        for path in (old, fresh):
            path.write_bytes(b'x')
        os.utime(old, (time.time()-7200, time.time()-7200))
        NameIndex(self.root, FakeDownload([]))
        self.assertEqual([p.name for p in self.root.iterdir()], [fresh.name])

    async def test_reload_during_a_download_does_not_break_it(self):
        # Review F-02: a config save reloads the plugin while the old instance is still downloading.
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(mode_path, target, etag):
            Path(target).write_bytes(document())
            started.set()
            await release.wait()
            return {'status': 200, 'etag': '"a"'}
        old = NameIndex(self.root, slow)
        task = asyncio.create_task(old.lookup('pve', 'Stea1'))
        await started.wait()
        NameIndex(self.root, FakeDownload([]))
        release.set()
        self.assertEqual((await asyncio.wait_for(task, 10))[0], [(30, 'Stea1')])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['names-pve.sqlite3'])

    async def test_two_instances_never_share_a_download_file(self):
        # Review F-02: an old and a reloaded instance may refresh the same mode at the same time.
        targets, gate = [], asyncio.Event()
        async def slow(mode_path, target, etag):
            targets.append(target)
            await gate.wait()
            Path(target).write_bytes(document())
            return {'status': 200, 'etag': '"a"'}
        a, b = NameIndex(self.root, slow), NameIndex(self.root, slow)
        tasks = [asyncio.create_task(index.lookup('pve', 'Stea1')) for index in (a, b)]
        while len(targets) < 2:
            await asyncio.sleep(0.01)
        gate.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 10)
        self.assertNotEqual(targets[0], targets[1])
        self.assertTrue(all(rows == [(30, 'Stea1')] for rows, _ in results))

    def test_failed_build_removes_its_temp_file(self):
        # Review F-06.
        path = self.root/'names-pve.sqlite3'
        with self.assertRaises(QueryError):
            build(path, b'{"1": ""}', None)
        self.assertFalse(list(self.root.iterdir()))

    async def test_damaged_names_table_is_rebuilt(self):
        # Review F-07: meta readable, names table damaged (simulated by dropping it).
        download = FakeDownload([document(), document()])
        index = NameIndex(self.root, download)
        await index.lookup('pve', 'Stea1')
        db = sqlite3.connect(index.db_path('pve'))
        db.execute('DROP TABLE names')
        db.commit()
        db.close()
        self.assertEqual((await index.lookup('pve', 'Stea1'))[0], [(30, 'Stea1')])
        self.assertEqual(len(download.calls), 2)

    async def test_exact_case_first_and_total(self):
        # Review F-13: the exact-case entry is returned even when it would fall past the cap.
        many = {str(i): 'same' for i in range(1, 10)}
        many['99'] = 'Same'
        index = NameIndex(self.root, FakeDownload([document(many)]))
        rows, total = await index.lookup('pve', 'Same')
        self.assertEqual(rows[0], (99, 'Same'))
        self.assertEqual((len(rows), total), (MAX_MATCHES, 10))

    async def test_similar_names_from_the_index_on_disk_only(self):
        download = FakeDownload([document({'5': 'demo-player', '6': 'Demo_Player', '7': 'other_name', '8': 'demo-play-er'})])
        index = NameIndex(self.root, download)
        # No index yet: nothing is downloaded or opened for a suggestion.
        with patch.object(names_module.logger, 'warning') as warning:
            self.assertEqual(await index.similar('arena', 'demo_player'), [])
        warning.assert_not_called()
        self.assertEqual(download.calls, [])
        await index.lookup('arena', 'nobody')
        self.assertEqual(await index.similar('arena', 'demo_player'), [(5, 'demo-player')])
        self.assertEqual(await index.similar('arena', 'DEMO-PLAYER'), [(6, 'Demo_Player')])
        self.assertEqual(await index.similar('arena', 'nounderscore'), [])
        self.assertEqual(len(download.calls), 1)
        # The meta table still reads (so the index counts as present) but the names table is gone: no suggestion, no error.
        db = sqlite3.connect(index.db_path('arena'))
        db.execute('DROP TABLE names')
        db.commit()
        db.close()
        self.assertTrue(index.has_index('arena'))
        self.assertEqual(await index.similar('arena', 'demo_player'), [])

    def test_default_check_is_weekly(self):
        self.assertEqual(NameIndex(self.root, FakeDownload([])).refresh_seconds, 7*24*3600)

    async def test_refresh_interval(self):
        index = NameIndex(self.root, FakeDownload([document()]), refresh_hours=24)
        await index.lookup('pve', 'Buhaus')
        db = sqlite3.connect(index.db_path('pve'))
        with db:
            db.execute("UPDATE meta SET value=? WHERE key='checked_at'", (str(time.time()-25*3600),))
        db.close()
        self.assertTrue(index.needs_refresh('pve'))
