import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from tarkov_core.names import MAX_MATCHES, NAME, NameIndex, build, parse_entries
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
        for name, ok in (('Some_Player', True), ('a-b', True), ('ab', False), ('x'*16, False), ('名字', False), ('a b', False)):
            with self.subTest(name=name):
                self.assertEqual(bool(NAME.fullmatch(name)), ok)


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
        self.assertEqual(await index.lookup('profile', 'BUHAUS'), [(15, 'Buhaus'), (16, 'buhaus')])
        self.assertEqual(await index.lookup('profile', 'stea1'), [(30, 'Stea1')])
        self.assertEqual(await index.lookup('profile', 'nobody'), [])
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
        self.assertEqual(await index.lookup('pve', 'newname'), [(99, 'NewName')])
        self.assertEqual(await index.lookup('pve', 'buhaus'), [])

    async def test_failed_refresh_keeps_serving_and_waits_before_retrying(self):
        download = FakeDownload([document(), QueryError('blocked')])
        index = NameIndex(self.root, download)
        await index.lookup('pve', 'Buhaus')
        index.refresh_seconds = 0
        self.assertEqual(await index.lookup('pve', 'Stea1'), [(30, 'Stea1')])
        # The failure is remembered: no second download within the retry delay.
        self.assertFalse(index.needs_refresh('pve'))
        await index.lookup('pve', 'Buhaus')
        self.assertEqual(len(download.calls), 2)

    async def test_failure_without_any_index_is_reported(self):
        index = NameIndex(self.root, FakeDownload([QueryError('blocked')]))
        with self.assertRaises(QueryError):
            await index.lookup('pve', 'Buhaus')
        self.assertTrue(index.needs_refresh('pve'))

    async def test_concurrent_lookups_download_once(self):
        download = FakeDownload([document()])
        index = NameIndex(self.root, download)
        results = await asyncio.gather(*(index.lookup('profile', 'Buhaus') for _ in range(5)))
        self.assertEqual(len(download.calls), 1)
        self.assertTrue(all(result == results[0] for result in results))

    async def test_matches_are_capped(self):
        many = {str(i): 'Same' for i in range(1, 20)}
        index = NameIndex(self.root, FakeDownload([document(many)]))
        self.assertEqual(len(await index.lookup('pve', 'same')), MAX_MATCHES)

    async def test_corrupt_database_is_rebuilt(self):
        (self.root/'names-pve.sqlite3').write_bytes(b'not a database')
        download = FakeDownload([document()])
        index = NameIndex(self.root, download)
        self.assertTrue(index.needs_refresh('pve'))
        self.assertEqual(await index.lookup('pve', 'Stea1'), [(30, 'Stea1')])

    async def test_data_directory_with_uri_characters(self):
        # Audit verification X-1: an unescaped path in the SQLite URI was cut at '#'/'?' or decoded at '%'.
        for name in ('data#1', 'data?mode=rw', 'data%41'):
            with self.subTest(name=name):
                root = self.root/name
                root.mkdir()
                download = FakeDownload([document()])
                index = NameIndex(root, download)
                self.assertEqual(await index.lookup('pve', 'Stea1'), [(30, 'Stea1')])
                self.assertFalse(index.needs_refresh('pve'))
                await index.lookup('pve', 'Stea1')
                self.assertEqual(len(download.calls), 1)
        # Nothing was created outside the data directories (the '#' case used to create root/'data').
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['data#1', 'data%41', 'data?mode=rw'])

    def test_leftovers_are_removed_at_start(self):
        for name in ('names-pve.sqlite3.tmp', 'names-profile.download'):
            (self.root/name).write_bytes(b'x')
        NameIndex(self.root, FakeDownload([]))
        self.assertFalse(list(self.root.iterdir()))

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
