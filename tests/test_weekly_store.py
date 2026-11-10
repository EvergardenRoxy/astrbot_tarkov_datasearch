"""Weekly report storage: resolved roster names, the night's progress, samples and broadcast records."""
import pathlib
import tempfile
import unittest

from tarkov_core.weekly_store import WeeklyStore
from test_weekly_report import DAY, LAST, sample


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = LAST+3600.
        self.days = 30
        self.store = WeeklyStore(pathlib.Path(self.temp.name)/'weekly.sqlite3', keep_days=lambda: self.days,
                                 clock=lambda: self.now)

    def test_reading_never_creates_the_file(self):
        store = self.store
        self.assertIsNone(store.resolved('pve', 'Someone'))
        self.assertEqual(store.roster('pve', ['Someone', '7']), [('Someone', None, None), ('7', None, None)])
        self.assertEqual(store.pending([('pve', 'Someone')], '2026-11-09'), [('pve', 'Someone')])
        self.assertEqual((store.samples(1, 'pve', 0), store.counts()), ([], (0, 0)))
        self.assertIsNone(store.push_state('2026-11-09 10:00', 'bot:GroupMessage:g1', 'pve'))
        store.prune()
        self.assertFalse(store.path.exists())

    def test_roster_names_are_resolved_per_mode_ignoring_case(self):
        store = self.store
        store.resolve('pve', 'Player_One', 11, 'Player_One')
        store.resolve('pve', '22', 22, 'Second')
        self.assertEqual(store.resolved('pve', 'player_one'), (11, 'Player_One'))
        self.assertIsNone(store.resolved('pvp', 'Player_One'))
        self.assertEqual(store.roster('pve', ['22', 'PLAYER_ONE', 'Unknown']),
                         [('22', 22, 'Second'), ('PLAYER_ONE', 11, 'Player_One'), ('Unknown', None, None)])
        # A renamed account keeps its entry; the newest nickname is shown.
        store.resolve('pve', 'Player_One', 11, 'New_Name')
        self.assertEqual(store.resolved('pve', 'Player_One'), (11, 'New_Name'))
        for mode, account in (('arena', 1), ('pve', '1'), ('pve', True)):
            with self.subTest(mode=mode, account=account), self.assertRaises(ValueError):
                store.resolve(mode, 'x', account, 'x')

    def test_one_attempt_and_one_retry_per_night(self):
        store, day = self.store, '2026-11-09'
        entries = [('pvp', 'A'), ('pve', 'A'), ('pve', 'B')]
        self.assertEqual(store.pending(entries, day), entries)
        store.attempted('pve', 'a', day, True)
        self.assertEqual(store.pending(entries, day), [('pvp', 'A'), ('pve', 'B')])
        # A failure is tried once more, after everything that was not tried yet.
        store.attempted('pvp', 'A', day, False, 'TimeoutError')
        self.assertEqual(store.pending(entries, day), [('pve', 'B'), ('pvp', 'A')])
        store.attempted('pvp', 'A', day, False, 'TimeoutError')
        self.assertEqual(store.pending(entries, day), [('pve', 'B')])
        store.attempted('pve', 'B', day, True, 'no_profile')
        self.assertEqual(store.pending(entries, day), [])
        # The next night starts over, and its first failure is attempt one again.
        self.assertEqual(store.pending(entries, '2026-11-10'), entries)
        store.attempted('pvp', 'A', '2026-11-10', False, 'x')
        self.assertEqual(store.pending(entries, '2026-11-10'), [('pve', 'A'), ('pve', 'B'), ('pvp', 'A')])

    def test_samples_are_stored_once_and_expire(self):
        store = self.store
        newest, older = sample(LAST, 1037, 825, 42, 129, 31, 5180, 950), sample(LAST-DAY, 1030, 820, 42, 127, 31, 5150, 940)
        self.assertTrue(store.add_sample(1, 'pve', newest))
        self.assertFalse(store.add_sample(1, 'pve', newest))
        self.assertTrue(store.add_sample(1, 'pve', older))
        self.assertTrue(store.add_sample(1, 'pvp', newest))
        self.assertEqual([entry['looked_up'] for entry in store.samples(1, 'pve', 0)], [LAST-DAY, LAST])
        self.assertEqual([entry['looked_up'] for entry in store.samples(1, 'pve', LAST)], [LAST])
        self.assertEqual(store.samples(1, 'pve', 0)[1], newest)
        self.assertEqual(store.counts(), (2, 3))
        for bad in (None, {}, {'looked_up': 'x'}, {'looked_up': True}, dict(newest, big='x'*70000)):
            self.assertFalse(store.add_sample(1, 'pve', bad))
        for mode, account in (('arena', 1), ('pve', '1'), ('pve', 1.0)):
            self.assertFalse(store.add_sample(account, mode, newest))
        # Nothing is deleted when a player leaves the roster; samples go when they are older than the setting.
        self.now = LAST-DAY+30*DAY+1
        store.prune()
        self.assertEqual([entry['looked_up'] for entry in store.samples(1, 'pve', 0)], [LAST])
        self.days = 14
        self.assertTrue(store.add_sample(2, 'pve', dict(newest, looked_up=self.now)))
        self.assertEqual(store.samples(1, 'pve', 0), [])

    def test_broadcast_records(self):
        store, slot, target = self.store, '2026-11-09 10:00', 'bot:GroupMessage:g1'
        store.push_record(slot, target, 'pve', 'retry')
        self.assertEqual(store.push_state(slot, target, 'pve'), ('retry', 1))
        store.push_record(slot, target, 'pve', 'submitted')
        self.assertEqual(store.push_state(slot, target, 'pve'), ('submitted', 2))
        self.assertIsNone(store.push_state(slot, target, 'pvp'))
        self.assertIsNone(store.push_state('2026-11-16 10:00', target, 'pve'))
        self.now += 61*DAY
        store.prune()
        self.assertIsNone(store.push_state(slot, target, 'pve'))


if __name__ == '__main__':
    unittest.main()
