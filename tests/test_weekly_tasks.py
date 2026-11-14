"""The nightly sampler's pacing rules and the weekly broadcast, with a controlled clock (no real waiting)."""
import asyncio
import pathlib
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from tarkov_core.atlas_cooldown import AtlasBudgetError, AtlasCooldownError
from tarkov_core.atlas_players import AtlasNotFound
from tarkov_core.notifications import PermanentDeliveryError
from tarkov_core.transport import ConnectionFailure
from tarkov_core.weekly_store import SampleResult, WeeklyStore
from tarkov_core.weekly_tasks import (IDLE_SECONDS, WeeklyBroadcast, WeeklySampler, day_id, in_window, latest_slot,
                                      parse_schedule, until_window)
from test_weekly_report import DAY, LAST

# LAST is Monday 2026-11-09 03:00 UTC+8.
HOUR = 3600


class Stop(Exception):
    pass


class ClockTests(unittest.TestCase):
    def test_the_sampling_window_is_three_to_nine_beijing_time(self):
        self.assertEqual(day_id(LAST), '2026-11-09')
        self.assertEqual(day_id(LAST-3*HOUR-1), '2026-11-08')
        for offset, inside in ((-1, False), (0, True), (6*HOUR-1, True), (6*HOUR, False), (12*HOUR, False)):
            with self.subTest(offset=offset):
                self.assertEqual(in_window(LAST+offset), inside)
        self.assertEqual(until_window(LAST-600), 600)
        self.assertEqual(until_window(LAST), DAY)
        self.assertEqual(until_window(LAST+7*HOUR), 17*HOUR)

    def test_schedule(self):
        self.assertEqual(parse_schedule('周一', '10:00'), (0, 10, 0))
        self.assertEqual(parse_schedule('周日', ' 21：30 '), (6, 21, 30))
        self.assertEqual(parse_schedule('周三', '9:05'), (2, 9, 5))
        for weekday, clock in (('周八', '10:00'), ('周一', '24:00'), ('周一', '10:60'), ('周一', '10'), ('周一', ''),
                               (1, '10:00'), ('周一', None)):
            self.assertIsNone(parse_schedule(weekday, clock))
        monday_ten = LAST+7*HOUR
        self.assertEqual(latest_slot(monday_ten, (0, 10, 0)), monday_ten)
        self.assertEqual(latest_slot(monday_ten-1, (0, 10, 0)), monday_ten-7*DAY)
        self.assertEqual(latest_slot(monday_ten+3*DAY, (0, 10, 0)), monday_ten)
        # Sunday 21:30 seen from Monday: yesterday evening.
        self.assertEqual(latest_slot(LAST, (6, 21, 30)), LAST-5.5*HOUR)


class SamplerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = LAST+60.
        self.store = WeeklyStore(pathlib.Path(self.temp.name)/'weekly.sqlite3', clock=lambda: self.now)
        self.entries = [('pve', '11'), ('pve', 'Named')]
        self.calls, self.saved, self.errors, self.slept = [], [], [], []
        self.names = {'Named': (22, 'Named', True)}
        self.answers = {}
        self.on = True
        self.sampler = WeeklySampler(self.store, lambda: list(self.entries), self.resolve, self.fetch, self.save,
                                     lambda: self.on, self.errors.append, clock=lambda: self.now, sleep=self.sleep,
                                     rand=lambda low, high: (self.calls.append(('rand', low, high)), 37)[1])

    async def resolve(self, mode, entry, *, deadline=None):
        self.calls.append(('resolve', mode, entry))
        answer = self.names.get(entry, (None, None, True))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def fetch(self, mode, account_id, *, deadline=None):
        self.calls.append(('fetch', mode, account_id))
        answer = self.answers.get(account_id)
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return {'nickname': f'Name{account_id}', 'doc': {}}

    async def save(self, account_id, mode, summary, *, day=None):
        self.saved.append((account_id, mode))
        self.store.attempted(mode, str(account_id), day, True)
        return SampleResult.ADDED

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds
        if len(self.slept) >= self.stop_after:
            raise Stop()

    async def test_one_request_per_step_and_a_search_takes_its_own_step(self):
        steps = [await self.sampler.step() for _ in range(4)]
        self.assertEqual(steps, ['pulled', 'resolved', 'pulled', None])
        self.assertEqual([call for call in self.calls if call[0] != 'rand'],
                         [('fetch', 'pve', 11), ('resolve', 'pve', 'Named'), ('fetch', 'pve', 22)])
        self.assertEqual(self.saved, [(11, 'pve'), (22, 'pve')])
        # An id entry learns its nickname from the sample; a resolved name is not searched again.
        self.assertEqual((self.store.resolved('pve', '11'), self.store.resolved('pve', 'named')), ((11, 'Name11'), (22, 'Name22')))
        self.now += DAY
        self.calls.clear()
        self.assertEqual([await self.sampler.step() for _ in range(3)], ['pulled', 'pulled', None])
        self.assertEqual(self.calls, [('fetch', 'pve', 11), ('fetch', 'pve', 22)])

    async def test_a_name_from_the_local_record_is_pulled_in_the_same_step(self):
        self.entries = [('pve', 'Named')]
        self.names['Named'] = (22, 'Named', False)
        self.assertEqual(await self.sampler.step(), 'pulled')
        self.assertEqual(self.calls, [('resolve', 'pve', 'Named'), ('fetch', 'pve', 22)])

    async def test_save_failure_or_invalid_sample_is_retried_but_bounded(self):
        self.entries = [('pve', '11')]
        original_save = self.sampler.save
        self.sampler.save = AsyncMock(side_effect=OSError('one-off write failure'))
        self.assertEqual(await self.sampler.step(), 'failed')
        self.assertEqual(self.store.pending(self.entries, day_id(self.now)), self.entries)
        self.sampler.save = original_save
        self.assertEqual(await self.sampler.step(), 'pulled')
        self.assertIsNone(await self.sampler.step())
        for failure in (OSError('persistent write failure'), SampleResult.INVALID):
            self.now += DAY
            self.sampler.save = AsyncMock(**({'side_effect': failure} if isinstance(failure, Exception)
                                            else {'return_value': failure}))
            self.assertEqual(await self.sampler.step(), 'failed')
            self.assertEqual(await self.sampler.step(), 'failed')
            self.assertIsNone(await self.sampler.step())
            self.assertEqual(self.sampler.save.await_count, 2)

    async def test_new_alias_of_completed_account_never_pulls_it_again(self):
        self.entries = [('pve', '11'), ('pve', 'Named')]
        self.names['Named'] = (11, 'Named', True)
        self.assertEqual(await self.sampler.step(), 'pulled')
        self.assertEqual(await self.sampler.step(), 'resolved')
        self.assertIsNone(await self.sampler.step())
        self.assertEqual([call for call in self.calls if call[0] == 'fetch'], [('fetch', 'pve', 11)])

    async def test_unwritable_progress_blocks_more_http_until_failure_can_be_recorded(self):
        self.entries = [('pve', '11')]
        self.sampler.save = AsyncMock(side_effect=OSError('disk full'))
        with patch.object(self.store, 'attempted', side_effect=OSError('disk full')):
            for _ in range(3):
                with self.assertRaises(OSError):
                    await self.sampler.step()
        self.assertEqual([call for call in self.calls if call[0] == 'fetch'], [('fetch', 'pve', 11)])
        # Once progress can be written again, the original failure consumes one attempt, not zero or three.
        self.assertEqual(await self.sampler.step(), 'failed')
        self.assertIsNone(await self.sampler.step())
        self.assertEqual(self.sampler.save.await_count, 2)

    async def test_local_alias_of_completed_account_is_skipped(self):
        self.entries = [('pve', '11'), ('pve', 'Named')]
        self.names['Named'] = (11, 'Named', False)
        self.assertEqual(await self.sampler.step(), 'pulled')
        self.assertEqual(await self.sampler.step(), 'skipped')
        self.assertIsNone(await self.sampler.step())
        self.assertEqual(self.saved, [(11, 'pve')])

    async def test_nothing_outside_the_window_or_when_off(self):
        for moment in (LAST-1, LAST+6*HOUR, LAST+12*HOUR):
            self.now = moment
            self.assertIsNone(await self.sampler.step())
        self.now, self.on = LAST+60, False
        self.assertIsNone(await self.sampler.step())
        self.assertEqual(self.calls, [])
        self.assertFalse(self.store.path.exists())

    async def test_failures_get_one_retry_and_refusals_cost_nothing(self):
        self.entries = [('pve', '11'), ('pve', '12'), ('pve', '13'), ('pve', 'Ghost'), ('pve', 'Broken')]
        self.answers = {11: [ConnectionFailure('x'), ConnectionFailure('x')], 12: AtlasNotFound('none'),
                        13: [AtlasBudgetError(30), AtlasCooldownError(5), None]}
        self.names['Broken'] = TimeoutError()
        steps = [await self.sampler.step() for _ in range(9)]
        # 11 fails, 12 has no profile, 13 is refused twice by our own limits (nothing sent) and then pulled, Ghost is
        # unknown to Atlas, Broken's search fails; then the two failures are tried once more, and that is all.
        self.assertEqual(steps, ['failed', 'missing', 'deferred', 'deferred', 'pulled', 'unresolved', 'failed', 'failed', 'failed'])
        self.assertIsNone(await self.sampler.step())
        self.assertEqual(self.saved, [(13, 'pve')])
        self.assertEqual([type(error).__name__ for error in self.errors], ['ConnectionFailure', 'TimeoutError']*2)
        self.assertEqual(sum(1 for call in self.calls if call == ('fetch', 'pve', 11)), 2)
        self.assertEqual(sum(1 for call in self.calls if call == ('fetch', 'pve', 12)), 1)

    async def test_the_pause_is_120_seconds_plus_a_random_20_to_60(self):
        self.entries = [('pve', '11'), ('pve', '12')]
        self.stop_after = 4
        with self.assertRaises(Stop):
            await self.sampler.run()
        # Two pulls, each followed by 120 + random(20, 60); then idle looks every ten minutes.
        self.assertEqual(self.slept, [157, 157, IDLE_SECONDS, IDLE_SECONDS])
        self.assertEqual([call for call in self.calls if call[0] == 'rand'], [('rand', 20, 60)]*2)
        self.assertEqual(self.saved, [(11, 'pve'), (12, 'pve')])

    async def test_it_sleeps_until_three_and_survives_errors(self):
        self.now = LAST-200
        self.stop_after = 3
        async def broken(mode, account_id, **kwargs):
            raise RuntimeError('boom')
        self.sampler.fetch = broken
        self.entries = [('pve', '11')]
        with self.assertRaises(Stop):
            await self.sampler.run()
        self.assertEqual(self.slept, [200, 157, 157])
        self.assertEqual([type(error).__name__ for error in self.errors], ['RuntimeError']*2)

    async def test_start_and_close(self):
        self.stop_after = 99
        gate = asyncio.Event()
        async def waiting(seconds):
            await gate.wait()
        self.sampler.sleep = waiting
        self.on = False
        self.sampler.start()
        self.assertIsNone(self.sampler.task)
        self.on = True
        self.sampler.start()
        task = self.sampler.task
        self.sampler.start()
        self.assertIs(self.sampler.task, task)
        await asyncio.sleep(0.05)
        await self.sampler.close()
        self.assertTrue(task.cancelled())
        self.assertIsNone(self.sampler.task)


class BroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = LAST+7*HOUR   # Monday 10:00
        self.store = WeeklyStore(pathlib.Path(self.temp.name)/'weekly.sqlite3', clock=lambda: self.now)
        self.targets, self.modes = ['bot:GroupMessage:g1', 'bot:GroupMessage:g2'], ['pvp', 'pve']
        self.pictures = {'pvp': None, 'pve': b'pve-picture'}
        self.results, self.sent, self.prepared, self.errors = {}, [], [], []
        self.broadcast = WeeklyBroadcast(self.store, lambda: (0, 10, 0), self.chats, self.prepare, self.send,
                                         lambda: True, self.errors.append, clock=lambda: self.now)

    async def chats(self):
        return {target: list(self.modes) for target in self.targets}

    async def prepare(self, chat, mode):
        self.prepared.append((chat, mode))
        if isinstance(self.pictures[mode], BaseException):
            raise self.pictures[mode]
        return self.pictures[mode]

    async def send(self, target, picture):
        self.sent.append((target, picture))
        answer = self.results.get(target, True)
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def test_one_ranking_per_mode_and_target_once(self):
        wait = await self.broadcast.tick()
        self.assertEqual(self.sent, [('bot:GroupMessage:g1', b'pve-picture'), ('bot:GroupMessage:g2', b'pve-picture')])
        # Every chat gets the ranking of its own roster; a mode nobody has a report for is skipped.
        drawn = [(chat, mode) for chat in self.targets for mode in self.modes]
        self.assertEqual(self.prepared, drawn)
        self.assertEqual(self.store.push_state('2026-11-09 10:00', 'bot:GroupMessage:g1', 'pvp'), ('empty', 1))
        self.assertEqual(wait, IDLE_SECONDS)
        # Looking again, or restarting within the hours after the slot, sends nothing twice.
        self.now += 1800
        await self.broadcast.tick()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.prepared, drawn)

    async def test_before_the_slot_and_long_after_it(self):
        self.now -= 1
        self.assertEqual(await self.broadcast.tick(), 1)
        self.now += 1+6*HOUR
        await self.broadcast.tick()
        self.assertEqual((self.sent, self.prepared), ([], []))
        self.now -= 1
        await self.broadcast.tick()
        self.assertEqual(len(self.sent), 2)

    async def test_retries_unknown_results_and_refused_targets(self):
        self.modes = ['pve']
        self.results = {'bot:GroupMessage:g1': [TimeoutError(), False, True], 'bot:GroupMessage:g2': None}
        self.assertEqual(await self.broadcast.tick(), 120)
        # None: the platform gave no answer, it may have arrived, never repeated.
        self.assertEqual(self.store.push_state('2026-11-09 10:00', 'bot:GroupMessage:g2', 'pve'), ('unknown', 1))
        self.assertEqual(await self.broadcast.tick(), 120)
        self.assertEqual(await self.broadcast.tick(), IDLE_SECONDS)
        self.assertEqual(self.store.push_state('2026-11-09 10:00', 'bot:GroupMessage:g1', 'pve'), ('submitted', 3))
        self.assertEqual([target for target, _ in self.sent].count('bot:GroupMessage:g2'), 1)
        # Three failures end it; a refused target is not tried again.
        self.now += 7*DAY
        self.results = {'bot:GroupMessage:g1': TimeoutError(), 'bot:GroupMessage:g2': PermanentDeliveryError('no')}
        self.sent.clear()
        waits = [await self.broadcast.tick() for _ in range(4)]
        self.assertEqual(waits, [120, 120, IDLE_SECONDS, IDLE_SECONDS])
        self.assertEqual([target for target, _ in self.sent], ['bot:GroupMessage:g1', 'bot:GroupMessage:g2',
                                                               'bot:GroupMessage:g1', 'bot:GroupMessage:g1'])
        self.assertEqual(self.store.push_state('2026-11-16 10:00', 'bot:GroupMessage:g2', 'pve'), ('blocked', 1))

    async def test_a_picture_that_cannot_be_drawn_is_retried(self):
        self.modes = ['pve']
        self.pictures['pve'] = RuntimeError('font')
        self.assertEqual(await self.broadcast.tick(), 120)
        self.assertEqual(self.sent, [])
        self.pictures['pve'] = b'pve-picture'
        await self.broadcast.tick()
        self.assertEqual(len(self.sent), 2)


if __name__ == '__main__':
    unittest.main()
