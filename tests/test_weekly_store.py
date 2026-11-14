"""Weekly report storage: resolved roster names, the night's progress, samples and broadcast records."""
import pathlib
import tempfile
import unittest

from tarkov_core import weekly_store
from tarkov_core.weekly_store import SampleResult, WeeklyStore
from test_weekly_report import DAY, LAST, sample
from unittest.mock import patch
import sqlite3


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
        self.assertEqual((store.roster('bot:GroupMessage:g1', 'pve'), store.entries(), store.chats()), ([], [], {}))
        self.assertFalse(store.remove('bot:GroupMessage:g1', 'pve', 'Someone'))
        self.assertFalse(store.listed('pve', 7, 'Someone'))
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
        chat = 'bot:GroupMessage:g1'
        self.assertEqual([store.add(chat, 'pve', entry) for entry in ('22', 'PLAYER_ONE', 'Unknown', 'player_one')],
                         ['added', 'added', 'added', 'listed'])
        self.assertEqual(store.roster(chat, 'pve'),
                         [('22', 22, 'Second'), ('PLAYER_ONE', 11, 'Player_One'), ('Unknown', None, None)])
        # A renamed account keeps its entry; the newest nickname is shown.
        store.resolve('pve', 'Player_One', 11, 'New_Name')
        self.assertEqual(store.resolved('pve', 'Player_One'), (11, 'New_Name'))

    def test_each_chat_keeps_its_own_roster_and_accounts_are_sampled_once(self):
        store, one, two = self.store, 'bot:GroupMessage:g1', 'bot:GroupMessage:g2'
        store.add(one, 'pve', 'Player_One')
        store.add(one, 'pvp', '7')
        store.add(two, 'pve', 'player_one')
        store.add(two, 'pve', 'Other')
        self.assertEqual(store.roster(one, 'pve'), [('Player_One', None, None)])
        self.assertEqual(sorted(entry for entry, _, _ in store.roster(two, 'pve')), ['Other', 'player_one'])
        self.assertEqual(store.roster(one, 'pvp'), [('7', 7, None)])
        # One nightly pull per listed player, however many chats list them.
        self.assertEqual(sorted(store.entries()), [('pve', 'Other'), ('pve', 'Player_One'), ('pvp', '7')])
        self.assertEqual(store.chats(), {one: ['pvp', 'pve'], two: ['pve']})
        # A user's own lookup settles a listed nickname before the nightly sampler does.
        self.assertTrue(store.listed('pve', 11, 'Player_One'))
        self.assertIn(('player_one', 11, 'Player_One'), store.roster(two, 'pve'))
        self.assertTrue(store.listed('pvp', 7, 'Anyone'))
        self.assertFalse(store.listed('pve', 99, 'Stranger'))
        # Removing works by what was listed, by account id or by nickname, in that chat only.
        self.assertTrue(store.remove(one, 'pve', '11'))
        self.assertFalse(store.remove(one, 'pve', 'Player_One'))
        self.assertIn(('player_one', 11, 'Player_One'), store.roster(two, 'pve'))
        self.assertEqual(store.chats(), {one: ['pvp'], two: ['pve']})

    def test_rosters_are_bounded_per_chat_and_per_night(self):
        store = self.store
        for number in range(1, weekly_store.CHAT_PLAYERS+1):
            self.assertEqual(store.add('bot:GroupMessage:g1', 'pve', str(number)), 'added')
        self.assertEqual(store.add('bot:GroupMessage:g1', 'pve', '999'), 'chat_full')
        # Another chat may list players already sampled, but no new ones in this mode.
        self.assertEqual(store.add('bot:GroupMessage:g2', 'pve', '1'), 'added')
        self.assertEqual(store.add('bot:GroupMessage:g2', 'pve', '999'), 'mode_full')
        self.assertEqual(store.add('bot:GroupMessage:g2', 'pvp', '999'), 'added')

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
        self.assertEqual(store.add_sample(1, 'pve', newest), SampleResult.ADDED)
        self.assertEqual(store.add_sample(1, 'pve', newest), SampleResult.DUPLICATE)
        self.assertEqual(store.add_sample(1, 'pve', older), SampleResult.ADDED)
        self.assertEqual(store.add_sample(1, 'pvp', newest), SampleResult.ADDED)
        self.assertEqual([entry['looked_up'] for entry in store.samples(1, 'pve', 0)], [LAST-DAY, LAST])
        self.assertEqual([entry['looked_up'] for entry in store.samples(1, 'pve', LAST)], [LAST])
        self.assertEqual(store.samples(1, 'pve', 0)[1], newest)
        self.assertEqual(store.counts(), (2, 3))
        for bad in (None, {}, {'looked_up': 'x'}, {'looked_up': True}, dict(newest, big='x'*70000)):
            self.assertEqual(store.add_sample(1, 'pve', bad), SampleResult.INVALID)
        for mode, account in (('arena', 1), ('pve', '1'), ('pve', 1.0)):
            self.assertEqual(store.add_sample(account, mode, newest), SampleResult.INVALID)
        # Nothing is deleted when a player leaves the roster; samples go when they are older than the setting.
        self.now = LAST-DAY+30*DAY+1
        store.prune()
        self.assertEqual([entry['looked_up'] for entry in store.samples(1, 'pve', 0)], [LAST])
        self.days = 14
        self.assertEqual(store.add_sample(2, 'pve', dict(newest, looked_up=self.now)), SampleResult.ADDED)
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

    def test_aliases_share_progress_and_retry_budget_across_restarts(self):
        store, day = self.store, '2026-11-09'
        entries = [('pve', '11'), ('pve', 'Named'), ('pvp', '11')]
        store.resolve('pve', 'Named', 11, 'Named')
        self.assertEqual(store.pending(entries, day), [('pve', '11'), ('pvp', '11')])
        store.attempted('pve', 'Named', day, False, 'timeout')
        self.assertEqual(store.pending(entries, day), [('pvp', '11'), ('pve', '11')])
        store.attempted('pve', '11', day, False, 'timeout')
        reopened = WeeklyStore(store.path)
        self.assertEqual(reopened.pending(entries, day), [('pvp', '11')])
        self.assertEqual(reopened.pending(entries, '2026-11-10'), [('pve', '11'), ('pvp', '11')])

    def test_resolving_an_alias_merges_earlier_failures(self):
        store, day = self.store, '2026-11-09'
        store.attempted('pve', 'Named', day, False)
        store.attempted('pve', '11', day, False)
        store.resolve('pve', 'Named', 11, 'Named')
        self.assertEqual(store.pending([('pve', 'Named'), ('pve', '11')], day), [])

    def test_legacy_progress_migration_preserves_done_and_is_idempotent(self):
        store, day = self.store, '2026-11-09'
        def legacy(connection):
            connection.execute('INSERT INTO players VALUES (?, ?, ?, ?, ?)', ('pve', 'named', 11, 'Named', LAST))
            connection.executemany('INSERT INTO pulls VALUES (?, ?, ?, ?, ?, ?, ?)',
                [('pve', '11', day, 1, 1, None, LAST), ('pve', 'named', day, 1, 0, 'timeout', LAST)])
        store._run(legacy)
        entries = [('pve', 'Named'), ('pve', '11')]
        for _ in range(2):
            reopened = WeeklyStore(store.path)
            self.assertEqual(reopened.pending(entries, day), [])
            self.assertEqual(reopened._run(lambda c: c.execute('SELECT entry, attempts, done FROM pulls').fetchall()),
                             [('id:11', 2, 1)])

    def test_sample_and_completion_commit_or_rollback_together(self):
        store, day = self.store, '2026-11-09'
        value = sample(LAST, 100, 50, 0, 20, 0, 200, 10)
        with patch.object(store, '_attempted', side_effect=sqlite3.OperationalError('write failed')):
            with self.assertRaises(sqlite3.OperationalError):
                store.add_sample(11, 'pve', value, day=day)
        self.assertEqual(store.samples(11, 'pve', 0), [])
        self.assertEqual(store.pending([('pve', '11')], day), [('pve', '11')])
        self.assertEqual(store.add_sample(11, 'pve', value, day=day), SampleResult.ADDED)
        self.assertEqual(store.pending([('pve', '11')], day), [])
        self.assertEqual(store.add_sample(11, 'pve', value, day=day), SampleResult.DUPLICATE)
        self.assertEqual(store._run(lambda c: c.execute('SELECT attempts FROM pulls').fetchone()), (1,))

    def test_an_existing_user_sample_can_complete_the_nightly_pull(self):
        store, day = self.store, '2026-11-09'
        value = sample(LAST, 100, 50, 0, 20, 0, 200, 10)
        store.add_sample(11, 'pve', value)
        self.assertEqual(store.pending([('pve', '11')], day), [('pve', '11')])
        self.assertEqual(store.add_sample(11, 'pve', value, day=day), SampleResult.DUPLICATE)
        self.assertEqual(store.pending([('pve', '11')], day), [])


if __name__ == '__main__':
    unittest.main()
