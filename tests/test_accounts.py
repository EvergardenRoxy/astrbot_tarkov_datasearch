import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tarkov_core import accounts
from tarkov_core.accounts import Accounts, owner_key


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Accounts(Path(self.temp.name)/'accounts.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_owner_key_is_per_platform_and_hashed(self):
        # The same user in two groups of one platform is one owner (maintainer's choice); another platform is not.
        self.assertEqual(owner_key('aiocqhttp', '10001'), owner_key('aiocqhttp', '10001'))
        self.assertNotEqual(owner_key('aiocqhttp', '10001'), owner_key('telegram', '10001'))
        self.assertNotIn('10001', owner_key('aiocqhttp', '10001'))

    def test_bind_rebind_unbind(self):
        self.assertIsNone(self.store.bound('a'))
        self.store.bind('a', 14858548, 'nowaYuMi')
        self.assertEqual(self.store.bound('a'), (14858548, 'nowaYuMi'))
        self.store.bind('a', 13871958, 'Hirururu')
        self.assertEqual(self.store.bound('a'), (13871958, 'Hirururu'))
        self.assertEqual(self.store.unbind('a'), (13871958, 'Hirururu'))
        self.assertIsNone(self.store.unbind('a'))
        self.assertIsNone(self.store.bound('a'))

    def test_names_are_recorded_and_found_ignoring_case(self):
        self.store.remember(14858548, 'nowaYuMi')
        self.assertEqual(self.store.lookup('NOWAYUMI'), ([(14858548, 'nowaYuMi')], 1))
        self.assertEqual(self.store.lookup('nobody'), ([], 0))
        self.store.remember(1, '  ')
        self.assertEqual(self.store.lookup(''), ([], 0))

    def test_old_names_survive_a_rename(self):
        self.store.remember(5, 'OldName')
        self.store.remember(5, 'NewName')
        self.assertEqual(self.store.lookup('oldname')[0], [(5, 'OldName')])
        self.assertEqual(self.store.lookup('newname')[0], [(5, 'NewName')])

    def test_exact_case_then_most_recent_first(self):
        self.store.remember(1, 'same')
        time.sleep(0.01)
        self.store.remember(2, 'SAME')
        time.sleep(0.01)
        self.store.remember(3, 'Same')
        self.assertEqual(self.store.lookup('same')[0], [(1, 'same'), (3, 'Same'), (2, 'SAME')])
        self.assertEqual(self.store.lookup('SAME'), ([(2, 'SAME'), (3, 'Same'), (1, 'same')], 3))

    def test_record_is_bounded(self):
        with patch.object(accounts, 'MAX_NAMES', 3):
            for aid in range(1, 6):
                self.store.remember(aid, f'name{aid}')
                time.sleep(0.005)
        self.assertEqual(self.store.lookup('name1'), ([], 0))
        self.assertEqual(self.store.lookup('name5')[0], [(5, 'name5')])
