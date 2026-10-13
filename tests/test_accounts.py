import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tarkov_core import accounts
from tarkov_core.accounts import Accounts, load_secret, owner_key


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Accounts(Path(self.temp.name)/'accounts.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_owner_key_is_per_platform_and_keyed(self):
        # The same user in two groups of one platform is one owner (maintainer's choice); another platform is not.
        key = b'k'*32
        self.assertEqual(owner_key(key, 'aiocqhttp', '10001'), owner_key(key, 'aiocqhttp', '10001'))
        self.assertNotEqual(owner_key(key, 'aiocqhttp', '10001'), owner_key(key, 'telegram', '10001'))
        self.assertNotIn('10001', owner_key(key, 'aiocqhttp', '10001'))
        # Review R-11: an unkeyed sha256 of a QQ number is reversed by trying every number.
        self.assertNotEqual(owner_key(key, 'aiocqhttp', '10001'), hashlib.sha256(b'aiocqhttp\x0010001').hexdigest())
        self.assertNotEqual(owner_key(key, 'aiocqhttp', '10001'), owner_key(b'j'*32, 'aiocqhttp', '10001'))

    def test_secret_is_created_once_and_private(self):
        path = Path(self.temp.name)/'binding.key'
        first = load_secret(path)
        self.assertEqual(len(first), 32)
        self.assertEqual(load_secret(path), first)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(load_secret(Path(self.temp.name)/'other.key'), first)
        path.write_bytes(b'short')
        self.assertEqual(len(load_secret(path)), 32)

    def test_bind_rebind_unbind(self):
        self.assertIsNone(self.store.bound('a'))
        self.store.bind('a', 2345678, 'Player_Two')
        self.assertEqual(self.store.bound('a'), (2345678, 'Player_Two'))
        self.store.bind('a', 7654321, 'Player_One')
        self.assertEqual(self.store.bound('a'), (7654321, 'Player_One'))
        self.assertEqual(self.store.unbind('a'), (7654321, 'Player_One'))
        self.assertIsNone(self.store.unbind('a'))
        self.assertIsNone(self.store.bound('a'))

    def test_names_are_recorded_and_found_ignoring_case(self):
        self.store.remember(2345678, 'Player_Two')
        self.assertEqual(self.store.lookup('PLAYER_TWO'), ([(2345678, 'Player_Two')], 1))
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
