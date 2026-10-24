import asyncio
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from tarkov_core.notifications import NotificationStore, PermanentDeliveryError, RETENTION_SECONDS, StatusNotifications
from tarkov_core.polling import PollingTask
from tarkov_core.server_status import normalize_status, status_changes
from tarkov_core.status_image import render_status_image
from tarkov_core.transport import QueryError


def status_data(code='OK', notice=None):
    return {'generalStatus': {'name': 'Global', 'statusCode': code},
            'currentStatuses': [{'_id': 'auth', 'name': 'Authentication', 'statusCode': code},
                                {'_id': 'match', 'name': 'Matchmaking', 'statusCode': 'OK'},
                                {'name': 'Global', 'statusCode': code}],
            'messages': [notice] if notice is not None else []}


def status_card(code='OK', notice=None, checked_at=100):
    return {'state': normalize_status(status_data(code, notice)), 'checked_at': checked_at,
            'fetched_at': 90, 'from_cache': False, 'stale': False, 'last_modified': None}


class StatusTests(unittest.TestCase):
    def test_normalization_ignores_global_duplicate_order_names_and_empty_notices(self):
        before = normalize_status(status_data(notice={'_id': 'empty', 'content': ''}))
        data = status_data()
        data['currentStatuses'].reverse()
        data['currentStatuses'][1]['name'] = 'new display name'
        after = normalize_status(data)
        self.assertEqual(len(before['components']), 2)
        self.assertEqual(before['messages'], [])
        self.assertEqual(status_changes(before, after, set()), ([], []))

    def test_failures_and_invalid_components_are_not_healthy_status(self):
        for data in ({}, {'generalStatus': {}, 'currentStatuses': [], 'messages': []},
                     {**status_data(), 'currentStatuses': [None]},
                     {**status_data(), 'messages': ['bad']},
                     {**status_data(), 'currentStatuses': status_data()['currentStatuses']*30}):
            with self.subTest(data=str(data)[:50]), self.assertRaises(QueryError):
                normalize_status(data)
        unknown = status_data('Unrecognized')
        self.assertIn('未知', normalize_status(unknown)['overall']['label'])

    def test_fault_recovery_and_component_removal_have_explicit_changes(self):
        healthy, failed = normalize_status(status_data()), normalize_status(status_data('Unstable'))
        changes, _ = status_changes(healthy, failed, set())
        self.assertIn('总体状态：正常 → 不稳定', changes)
        recovery, _ = status_changes(failed, healthy, set())
        self.assertIn('账号认证：不稳定 → 正常', recovery)
        healthy['components'].pop()
        removed, _ = status_changes(failed, healthy, set())
        self.assertTrue(any('已移除' in line for line in removed))

    def test_status_and_error_cards_are_images_with_times_and_stale_markers(self):
        texts = []
        real = ImageDraw.ImageDraw.text
        def capture(draw, xy, text, *args, **kwargs):
            texts.append(text)
            return real(draw, xy, text, *args, **kwargs)
        card = status_card('Unstable')
        card.update(stale=True, compared_at=80, changes=['账号认证：正常 → 不稳定'])
        with patch.object(ImageDraw.ImageDraw, 'text', capture):
            body = render_status_image(card)
        with Image.open(io.BytesIO(body)) as img:
            self.assertEqual(img.width, 820)
            self.assertEqual(img.format, 'JPEG')
        self.assertTrue(any('检查时间' in line for line in texts))
        self.assertTrue(any('对比上次检查' in line for line in texts))
        self.assertTrue(any('旧数据' in line for line in texts))
        self.assertEqual(Image.open(io.BytesIO(render_status_image({'error': '状态查询尚未开启'}))).format, 'JPEG')


class NotificationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = NotificationStore(Path(self.temp.name)/'notifications.sqlite3')
        self.targets = ['bot:GroupMessage:1', 'bot:GroupMessage:2']

    def tearDown(self): self.temp.cleanup()

    def test_first_poll_silent_then_one_event_per_change_and_restart_does_not_replay(self):
        self.assertIsNone(self.store.observe(status_card(), self.targets, now=100))
        event_id = self.store.observe(status_card('Unstable', checked_at=200), self.targets, now=200)
        rows = self.store.due(self.targets, now=200)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][2]['compared_at'], 100)
        self.store.record(event_id, self.targets[0], 'submitted', now=200)
        self.store = NotificationStore(self.store.path)
        self.assertIsNone(self.store.observe(status_card('Unstable', checked_at=300), self.targets, now=300))
        self.assertEqual([r[1] for r in self.store.due(self.targets, now=300)], [self.targets[1]])
        self.assertIsNotNone(self.store.observe(status_card('OK', checked_at=400), self.targets, now=400))

    def test_notices_first_seen_revised_and_removed_then_returned(self):
        initial = {'_id': 'n1', 'content': '维护中', 'time': '2026-10-24'}
        self.store.observe(status_card(notice=initial), self.targets, now=100)
        self.assertIsNone(self.store.observe(status_card(), self.targets, now=110))
        self.assertIsNone(self.store.observe(status_card(notice=initial), self.targets, now=120))
        changed = {**initial, 'content': '维护已结束'}
        self.assertIsNotNone(self.store.observe(status_card(notice=changed), self.targets, now=130))
        self.assertIsNone(self.store.observe(status_card(notice=changed), self.targets, now=140))

    def test_failed_target_retry_is_bounded_and_other_target_is_not_resent(self):
        self.store.observe(status_card(), self.targets, now=100)
        eid = self.store.observe(status_card('Down'), self.targets, now=200)
        self.store.record(eid, self.targets[0], 'submitted', now=200)
        self.store.record(eid, self.targets[1], 'TimeoutError', now=200)
        self.assertEqual(self.store.due(self.targets, now=201), [])
        self.assertEqual([r[1] for r in self.store.due(self.targets, now=321)], [self.targets[1]])
        self.store.record(eid, self.targets[1], 'TimeoutError', now=321)
        self.store.record(eid, self.targets[1], 'TimeoutError', now=600)
        self.assertEqual(self.store.due(self.targets, now=10000), [])

    def test_removed_targets_and_disabled_monitor_do_not_receive_old_backlogs(self):
        self.store.observe(status_card(), self.targets, now=100)
        self.store.observe(status_card('Down'), self.targets, now=200)
        self.store.due(self.targets[:1], now=200)
        self.assertEqual([row[1] for row in self.store.due(self.targets, now=201)], self.targets[:1])
        self.store.retire()
        self.assertIsNone(self.store.observe(status_card('Down'), self.targets, now=300))
        self.assertEqual(self.store.due(self.targets, now=300), [])

    def test_expired_events_are_retired_even_without_another_successful_fetch(self):
        self.store.observe(status_card(), self.targets, now=100)
        self.store.observe(status_card('Down'), self.targets, now=200)
        self.assertEqual(len(self.store.due(self.targets, now=200)), 2)
        self.assertEqual(self.store.due(self.targets, now=201+RETENTION_SECONDS), [])

    def test_failed_event_write_does_not_advance_baseline_and_lose_transition(self):
        self.store.observe(status_card(), self.targets, now=100)
        with sqlite3.connect(self.store.path) as db:
            db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.observe(status_card('Down'), self.targets, now=200)
        with sqlite3.connect(self.store.path) as db:
            db.execute('DROP TRIGGER fail_event')
        self.store.observe(status_card('Down'), self.targets, now=300)
        rows = self.store.due(self.targets, now=300)
        self.assertEqual(rows[0][2]['compared_at'], 100)
        self.assertIn('总体状态：正常 → 不可用', rows[0][2]['changes'])


class StatusNotificationsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = NotificationStore(Path(self.temp.name)/'notifications.sqlite3')
        self.targets = ['bot:GroupMessage:1', 'bot:GroupMessage:2']
        self.card = status_card()
        self.failure = None
        self.sent = []
        self.prepared = []
        async def fetch():
            if self.failure: raise self.failure
            return self.card
        async def prepare(card):
            self.prepared.append(card)
            return b'image'
        async def send(target, data):
            self.sent.append((target, data))
            return True
        self.monitor = StatusNotifications(self.store, fetch, prepare, send,
                                            lambda: self.targets, lambda: True, lambda e: None)

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def test_network_failure_and_stale_data_never_advance_or_broadcast(self):
        await self.monitor.cycle()
        self.failure = TimeoutError()
        with self.assertRaises(TimeoutError): await self.monitor.cycle()
        self.assertEqual(self.sent, [])
        self.failure = None
        self.card = status_card('Down')
        self.card['stale'] = True
        with self.assertRaises(ValueError): await self.monitor.cycle()
        self.assertEqual(self.sent, [])
        self.card['stale'] = False
        await self.monitor.cycle()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(len(self.prepared), 1)
        self.assertIn('总体状态：正常 → 不可用', self.prepared[0]['changes'])

    async def test_no_change_is_quiet_and_permissions_unknown_results_are_not_called_delivered(self):
        await self.monitor.cycle()
        await self.monitor.cycle()
        self.assertEqual(self.sent, [])
        self.card = status_card('Down')
        async def send(target, data):
            if target.endswith('1'): raise PermanentDeliveryError()
            return None
        self.monitor.send = send
        await self.monitor.cycle()
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual({row[0] for row in db.execute('SELECT state FROM deliveries')}, {'blocked', 'unknown'})

    async def test_permission_failure_pauses_target_across_events_and_restart_until_reset(self):
        await self.monitor.cycle()
        self.card = status_card('Down')
        async def send(target, data):
            raise PermissionError()
        self.monitor.send = send
        await self.monitor.cycle()
        self.assertEqual(self.store.due(self.targets), [])
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual({row[0] for row in db.execute('SELECT state FROM deliveries')}, {'blocked'})
        restarted = NotificationStore(self.store.path)
        restarted.observe(status_card(), self.targets)
        self.assertEqual(restarted.due(self.targets), [])
        restarted.retire()
        restarted.observe(status_card(), self.targets)
        restarted.observe(status_card('Down'), self.targets)
        self.assertEqual(len(restarted.due(self.targets)), 2)


class PollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_interval_change_uses_remaining_delay_and_does_not_overlap_or_catch_up(self):
        current = [0]
        interval = [300]
        active = [True]
        cycles, delays = [], []
        async def cycle():
            cycles.append(current[0])
            current[0] += 500  # A slow run does not trigger catch-up calls.
            if len(cycles) == 2: active[0] = False
        async def wait(event, seconds):
            delays.append(seconds)
            if len(delays) == 1:
                interval[0] = 120
                current[0] += 30
            else:
                current[0] += seconds
        poller = PollingTask(cycle, lambda: active[0], lambda: interval[0], lambda e: None,
                             clock=lambda: current[0], wait=wait)
        await poller.run()
        self.assertEqual(delays, [300, 90])
        self.assertEqual(cycles, [0, 620])

    async def test_long_intervals_are_not_cut_to_the_failure_backoff_cap(self):
        # Intervals go up to 90 minutes: the 30-minute cap of the failure back-off must not shorten a normal wait.
        clock, delays = [0], []
        async def cycle(): pass
        async def wait(event, seconds):
            delays.append(seconds)
            clock[0] += seconds
        stop = []
        async def wait_once(event, seconds):
            await wait(event, seconds)
            stop.append(1)
        poller = PollingTask(cycle, lambda: not stop, lambda: 5400, lambda e: None, clock=lambda: clock[0], wait=wait_once)
        await poller.run()
        self.assertEqual(delays, [5400])

    async def test_failures_back_off_and_close_cancels_sleep(self):
        clock, delays, errors = [0], [], []
        async def cycle(): raise TimeoutError()
        async def wait(event, seconds):
            delays.append(seconds)
            clock[0] += seconds
        poller = PollingTask(cycle, lambda: len(errors) < 3, lambda: 120, errors.append,
                             clock=lambda: clock[0], wait=wait)
        await poller.run()
        self.assertEqual(delays, [120, 240])
        gate = asyncio.Event()
        async def idle(): gate.set()
        poller = PollingTask(idle, lambda: True, lambda: 1800, errors.append)
        poller.start()
        task = poller.task
        poller.start()
        self.assertIs(poller.task, task)
        await gate.wait()
        await poller.close()
        self.assertIsNone(poller.task)
