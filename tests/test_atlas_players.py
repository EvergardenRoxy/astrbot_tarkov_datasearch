"""Atlas player source: document mapping, budget, gate waiting, client endpoints, player service, quotas, and the
plugin's query/bind flow. Every document is invented (fixtures.atlas_profile); no test reaches the real service."""
import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web

from fixtures import ATLAS_ARENA, arena_profile, atlas_profile, profile
from tarkov_core import atlas_client
from tarkov_core.atlas_client import AtlasClient, AtlasHTTPError
from tarkov_core.atlas_cooldown import AtlasAccessPaused, AtlasBudget, AtlasBudgetError, AtlasCooldown, AtlasCooldownError
from tarkov_core.atlas_players import (AtlasNotFound, AtlasPlayers, QuotaError, UserQuota, can_fall_back,
                                       failure_text)
from tarkov_core.atlas_profile import AtlasProfileError, search_results, summarize
from tarkov_core.transport import ConnectionFailure, HTTPClient, QueryError
from test_atlas_cooldown import Clock


def use(quota, owner, refresh):
    """One query of `owner` that sent a request."""
    ticket = quota.ticket(owner, refresh)
    ticket.check()
    ticket.sent()


def remaining(budget):
    """Player requests the hour's budget still allows."""
    budget._trim(budget.clock())
    return budget.per_hour()-len(budget.sent)


class ProfileMappingTests(unittest.TestCase):
    def test_game_document(self):
        summary = summarize(atlas_profile('pve'), 'pve')
        self.assertEqual(summary['source'], 'atlas')
        self.assertEqual((summary['aid'], summary['nickname'], summary['side'], summary['level']), (1234567, 'Test_Player', 'USEC', 50))
        self.assertEqual(summary['play_seconds'], 3600000)
        self.assertEqual(summary['achievements'], 2)
        self.assertEqual(summary['pmc'], {'raids': 1000, 'survived': 800, 'killed': 120, 'mia': 10, 'runner': 30,
                                          'transit': 40, 'kills': 5000, 'deaths': 100, 'pmc_kills': 900, 'streak': 40})
        self.assertEqual(summary['scav']['raids'], 50)
        self.assertEqual(summary['skills'], [('耐力', 51, True), ('力量', 25, False)])
        self.assertEqual(summary['last_active'], 1790600000)
        self.assertEqual(summary['updated'], 1793505600.0)
        self.assertFalse(summary['cached'])

    def test_cached_flag_and_level_fallback(self):
        summary = summarize(atlas_profile('pvp', cached=True, level='x'), 'pvp')
        self.assertTrue(summary['cached'])
        # An unusable level falls back to the level table from the experience.
        self.assertIsInstance(summary['level'], int)

    def test_empty_scav_counters_are_an_empty_side(self):
        doc = atlas_profile('pve')
        doc['stats']['scav']['counters'] = []
        self.assertEqual(summarize(doc, 'pve')['scav']['raids'], 0)

    def test_odd_field_types_never_raise(self):
        odd = [None, 'x', 1, 1.5, True, [], {}, [[None]], [['Kills'], 'many'], float('inf')]
        for value in odd:
            for field in ('stats', 'skills', 'achievements', 'experience', 'level', 'side', 'looked_up', 'accountId',
                          'prestigeLevel', 'cached'):
                with self.subTest(field=field, value=value):
                    summary = summarize(atlas_profile('pve', **{field: value}), 'pve')
                    self.assertEqual(summary['nickname'], 'Test_Player')
            doc = atlas_profile('pve')
            doc['stats']['pmc']['counters'] = [value, [['Kills'], -5], [['Deaths'], value]]
            self.assertEqual(summarize(doc, 'pve')['pmc']['kills'], 0)
            summarize(atlas_profile('arena', arenaStats=value), 'arena')
            summarize(atlas_profile('arena', arenaStats={'modes': {'UnrankedOverall': value}}), 'arena')

    def test_not_a_profile(self):
        for doc in ({}, {'nickname': ''}, {'nickname': 5}, [], None):
            with self.subTest(doc=doc), self.assertRaises(AtlasProfileError):
                summarize(doc, 'pve')

    def test_arena_document(self):
        summary = summarize(atlas_profile('arena'), 'arena')
        self.assertTrue(summary['arena'])
        [section] = summary['sections']
        self.assertEqual(section['title'], '非排位')
        self.assertEqual(section['overall']['best_arp'], 1200)
        self.assertEqual([label for label, _ in section['modes']], ['Team Fight', 'ShootOutDuo'])
        self.assertEqual(summary['achievements'], 1)

    def test_search_results(self):
        doc = {'nickname': 'Test_Player', 'players': [
            {'accountId': '1234567', 'nickname': 'Test_Player', 'side': 'Usec', 'level': 50},
            {'accountId': 'x', 'nickname': 'Bad'}, {'accountId': '0', 'nickname': 'Zero'}, 'junk',
            {'accountId': 7654321, 'nickname': 'test_player'}, {'accountId': True, 'nickname': 'Bool'}]}
        self.assertEqual(search_results(doc), [(1234567, 'Test_Player'), (7654321, 'test_player')])
        with self.assertRaises(AtlasProfileError):
            search_results({'players': None})


class BudgetAndGateTests(unittest.IsolatedAsyncioTestCase):
    def test_default_budget_is_fifteen_per_minute_and_two_hundred_per_hour(self):
        clock = Clock()
        budget = AtlasBudget(clock=clock)
        for _ in range(15):
            budget.reserve()
        with self.assertRaises(AtlasBudgetError):
            budget.reserve()
        clock.advance(60)
        budget.reserve()
        self.assertEqual(remaining(budget), 184)

    def test_minute_and_hour_windows(self):
        clock = Clock()
        budget = AtlasBudget(lambda: 2, lambda: 3, clock=clock)
        budget.reserve(); budget.reserve()
        with self.assertRaises(AtlasBudgetError) as caught:
            budget.reserve()
        self.assertEqual(caught.exception.retry_after_seconds, 60)
        clock.advance(60)
        token = budget.reserve()
        with self.assertRaises(AtlasBudgetError) as caught:
            budget.reserve()
        self.assertGreater(caught.exception.retry_after_seconds, 3000)
        budget.refund(token)
        self.assertEqual(remaining(budget), 1)
        clock.advance(3600)
        self.assertEqual(remaining(budget), 3)

    async def test_claim_within_waits_for_the_interval_and_another_request(self):
        clock = Clock()
        gate = AtlasCooldown(10, clock=clock)
        slept = []
        async def sleep(seconds):
            slept.append(seconds)
            clock.advance(seconds)
        async with gate.claim():
            pass
        async with gate.claim_within(20, sleep=sleep):
            pass
        self.assertEqual(slept, [10])
        gate.in_flight = True
        async def release(seconds):
            clock.advance(seconds)
            gate.in_flight = False
        clock.advance(10)
        async with gate.claim_within(20, sleep=release):
            pass

    async def test_claim_within_fails_fast_on_a_long_pause(self):
        clock = Clock()
        gate = AtlasCooldown(10, clock=clock)
        gate.pause(60)
        async def sleep(seconds):
            self.fail('must not wait for a pause longer than the limit')
        with self.assertRaises(AtlasCooldownError) as caught:
            async with gate.claim_within(20, sleep=sleep):
                self.fail('admitted during a pause')
        self.assertEqual(caught.exception.retry_after_seconds, 60)


    async def test_claim_within_refuses_at_once_when_the_next_start_is_past_the_limit(self):
        clock = Clock()
        gate = AtlasCooldown(30, clock=clock)
        async def sleep(seconds):
            self.fail('must not wait for a start that comes after the limit')
        async with gate.claim():
            pass
        with self.assertRaises(AtlasCooldownError) as caught:
            async with gate.claim_within(20, sleep=sleep):
                self.fail('admitted before the shared interval passed')
        self.assertEqual(caught.exception.retry_after_seconds, 30)
        # The same start is reachable with a limit that covers it.
        slept = []
        async def wait(seconds):
            slept.append(seconds)
            clock.advance(seconds)
        async with gate.claim_within(40, sleep=wait):
            pass
        self.assertEqual(slept, [30])

class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.budget = AtlasBudget(lambda: 15, lambda: 150, clock=self.clock)
        self.client = AtlasClient(HTTPClient(), cooldown=AtlasCooldown(10, clock=self.clock), budget=self.budget)
        self.seen = []
        self.answer = lambda request: web.json_response(atlas_profile('pve'))
        async def serve(request):
            self.seen.append((request.path, dict(request.query)))
            return self.answer(request)
        app = web.Application()
        app.router.add_get('/{path:.*}', serve)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.host = patch.object(atlas_client, 'HOST', f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}')
        self.host.start()

    async def asyncTearDown(self):
        await self.client.close()
        await self.client.http.close()
        self.host.stop()
        await self.runner.cleanup()

    async def test_lookup_and_search_requests(self):
        await self.client.lookup(1234567, 'pve')
        self.clock.advance(10)
        self.answer = lambda request: web.json_response({'nickname': 'Test_Player', 'players': [], 'cached': False})
        await self.client.search('Test_Player', 'season')
        self.assertEqual(self.seen, [('/atlas/api/lookup', {'accountId': '1234567', 'mode': 'pve'}),
                                     ('/atlas/api/lookup/search', {'nickname': 'Test_Player', 'mode': 'season'})])
        self.assertEqual(remaining(self.budget), 148)

    async def test_refused_requests_are_refunded_and_an_empty_budget_sends_nothing(self):
        self.answer = lambda request: web.json_response({'detail': {'error': 'busy'}}, status=429, headers={'Retry-After': '5'})
        with self.assertRaises(AtlasHTTPError) as caught:
            await self.client.lookup(1234567, 'pve')
        self.assertEqual(caught.exception.code, 'busy')
        self.assertEqual(remaining(self.budget), 150)
        self.clock.advance(10)
        self.budget.per_minute = lambda: 0
        with self.assertRaises(AtlasBudgetError):
            await self.client.lookup(1234567, 'pve')
        self.assertEqual(len(self.seen), 1)

    async def test_player_wait_covers_a_longer_shared_interval(self):
        """A nickname query is a search followed by a lookup one interval later: the wait must reach that start."""
        self.assertEqual(self.client.player_wait(), 20)
        waits = []
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def gate(max_wait, deadline=None):
            waits.append(max_wait)
            yield
        for interval, expected in ((10, 20), (20, 30), (30, 40), (300, 310)):
            self.client.cooldown.interval = interval
            with patch.object(self.client.cooldown, 'claim_within', gate):
                await self.client.search('Test_Player', 'pve')
                await self.client.lookup(1, 'pve')
            self.assertEqual(waits[-2:], [expected, expected])

    async def test_a_refused_gate_gives_the_budget_back(self):
        self.client.cooldown.pause(600)
        with self.assertRaises(AtlasCooldownError):
            await self.client.lookup(1234567, 'pve')
        self.assertEqual(remaining(self.budget), 150)
        self.assertEqual(self.seen, [])

    async def test_exhausted_windows_block_http_then_recover_at_the_deadline(self):
        self.budget.per_minute, self.budget.per_hour = lambda: 1, lambda: 2
        await self.client.lookup(1, 'pve')
        self.clock.advance(59)
        with self.assertRaises(AtlasBudgetError) as caught:
            await self.client.lookup(2, 'pve')
        self.assertEqual(caught.exception.retry_after_seconds, 1)
        self.assertEqual(len(self.seen), 1)
        self.clock.advance(1)
        await self.client.lookup(2, 'pve')
        self.clock.advance(3539)
        with self.assertRaises(AtlasBudgetError) as caught:
            await self.client.lookup(3, 'pve')
        self.assertEqual(caught.exception.retry_after_seconds, 1)
        self.assertEqual(len(self.seen), 2)
        self.clock.advance(1)
        await self.client.lookup(3, 'pve')
        self.assertEqual(len(self.seen), 3)

    async def test_window_uses_http_start_after_gate_wait_and_rechecks_budget(self):
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def delayed_gate(max_wait, deadline=None):
            self.clock.advance(20)
            yield
        with patch.object(self.client.cooldown, 'claim_within', delayed_gate):
            await self.client.lookup(1, 'pve')
        self.assertEqual(list(self.budget.sent), [self.clock()])
        self.budget.per_minute = lambda: 1
        self.clock.advance(59)
        with self.assertRaises(AtlasBudgetError):
            self.budget.check()
        self.clock.advance(1)
        self.budget.check()
        @asynccontextmanager
        async def competing_gate(max_wait, deadline=None):
            self.budget.reserve()  # A different admitted query used the last slot while this one waited.
            yield
        with patch.object(self.client.cooldown, 'claim_within', competing_gate), self.assertRaises(AtlasBudgetError):
            await self.client.lookup(2, 'pve')
        self.assertEqual(len(self.seen), 1)


class FakeAtlas:
    """Stands in for AtlasClient: answers from tables, records the calls. `errors` is the atlas_client module whose
    AtlasHTTPError the code under test catches (the plugin tests load their own copy of the package)."""
    errors = atlas_client

    def __init__(self):
        self.docs, self.searches, self.calls = {}, {}, []
        self.gate = None

    def put(self, aid, mode='pve', **changes):
        self.docs[(aid, mode)] = atlas_profile(mode, accountId=str(aid), **changes)

    async def lookup(self, aid, mode, **kwargs):
        self.calls.append(('lookup', aid, mode))
        if self.gate:
            await self.gate.wait()
        answer = self.docs.get((aid, mode))
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            raise self.errors.AtlasHTTPError(404, 'not_found')
        return copy.deepcopy(answer)

    async def search(self, nickname, mode, **kwargs):
        self.calls.append(('search', nickname, mode))
        answer = next((found for key, found in self.searches.items() if key.lower() == nickname.lower()), [])
        if isinstance(answer, BaseException):
            raise answer
        return {'nickname': nickname, 'players': [{'accountId': str(aid), 'nickname': name} for aid, name in answer],
                'cached': False, 'mode': mode}


class PlayerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_statistical_and_full_cache_variants_do_not_overwrite_each_other(self):
        self.atlas.put(1, 'pve')
        calls = []
        async def lookup(aid, mode, **kwargs):
            calls.append(kwargs)
            return atlas_profile(mode, accountId=str(aid), character={'full':'synthetic'} if kwargs['include_art'] else None)
        self.atlas.lookup = lookup
        stats = await self.players.profile_once('pve', 1)
        self.assertIsNone(stats['doc']['character'])
        self.assertIsNone(self.players.cached('pve', 1))
        full = await self.players.profile('pve', 1)
        self.assertEqual(full['doc']['character'], {'full':'synthetic'})
        self.assertIsNone(self.players.cached('pve', 1, include_art=False)['doc']['character'])
        self.assertEqual(calls, [{'deadline': None, 'include_art': False}, {'deadline': None, 'include_art': True}])
        await self.players.profile('pve', 1)
        self.assertEqual(len(calls), 2)

    async def test_access_pause_refunds_only_a_query_that_sent_nothing(self):
        quota = UserQuota(lambda: (10, 3, 30, 300), clock=self.clock)
        self.atlas.docs[(1, 'pve')] = AtlasAccessPaused(60)
        with self.assertRaises(AtlasAccessPaused):
            await self.players.profile('pve', 1, ticket=quota.ticket('user', False))
        self.assertEqual(quota.recent('user'), [])

    async def test_busy_attempt_keeps_user_charge_if_recovery_blocks_the_retry(self):
        quota = UserQuota(lambda: (10, 3, 30, 300), clock=self.clock)
        for kind in ('lookup', 'search'):
            with self.subTest(kind=kind):
                answers = [AtlasHTTPError(429, 'busy', 5), AtlasAccessPaused(60)]
                async def request(*args, **kwargs):
                    raise answers.pop(0)
                setattr(self.atlas, kind, request)
                charge = quota.ticket(kind, False)
                with self.assertRaises(AtlasAccessPaused):
                    if kind == 'lookup':
                        await self.players.profile('pve', 1, ticket=charge)
                    else:
                        await self.players.search('Some_Name', 'pve', ticket=charge)
                self.assertEqual(len(quota.recent(kind)), 1)

    def setUp(self):
        self.clock = Clock()
        self.atlas = FakeAtlas()
        self.players = AtlasPlayers(self.atlas, cache_seconds=lambda: 0, clock=self.clock)

    async def test_refusals_before_sending_give_the_users_query_back(self):
        quota = UserQuota(lambda: (10, 3, 30, 300), clock=self.clock)
        for refusal in (AtlasBudgetError(60), AtlasCooldownError(5)):
            with self.subTest(refusal=type(refusal).__name__):
                self.atlas.docs[(1, 'pve')] = refusal
                with self.assertRaises(type(refusal)):
                    await self.players.profile('pve', 1, ticket=quota.ticket('a', False))
                self.assertEqual(quota.recent('a'), [])
                quota.check('a', False)  # No 30-second wait either.
                self.atlas.searches['Name'] = refusal
                with self.assertRaises(type(refusal)):
                    await self.players.search('Name', 'pve', ticket=quota.ticket('a', False))
                self.assertEqual(quota.recent('a'), [])
        # A request Atlas answered (even with an error) still counts.
        self.atlas.docs[(1, 'pve')] = AtlasHTTPError(502)
        with self.assertRaises(AtlasHTTPError):
            await self.players.profile('pve', 1, ticket=quota.ticket('a', False))
        self.assertEqual(len(quota.recent('a')), 1)

    async def test_a_sent_search_still_counts_when_the_lookup_is_refused(self):
        """Only a query with nothing sent is given back: after the nickname search reached Atlas, a refused lookup
        must not make the whole query free (it could then be repeated at the cost of the shared budget)."""
        quota = UserQuota(lambda: (10, 3, 30, 300), clock=self.clock)
        self.atlas.searches['Name'] = [(1, 'Name')]
        self.atlas.docs[(1, 'pve')] = AtlasCooldownError(5)
        charge = quota.ticket('a', False)
        self.assertEqual(await self.players.search('Name', 'pve', ticket=charge), [(1, 'Name')])
        with self.assertRaises(AtlasCooldownError):
            await self.players.profile('pve', 1, ticket=charge)
        self.assertEqual(len(quota.recent('a')), 1)
        # A search Atlas answered with an error was sent as well.
        self.atlas.searches['Other'] = AtlasHTTPError(502)
        self.atlas.docs[(2, 'pve')] = AtlasBudgetError(60)
        self.clock.advance(30)
        charge = quota.ticket('a', False)
        with self.assertRaises(AtlasHTTPError):
            await self.players.search('Other', 'pve', ticket=charge)
        with self.assertRaises(AtlasBudgetError):
            await self.players.profile('pve', 2, ticket=charge)
        self.assertEqual(len(quota.recent('a')), 2)
        # The other order (the rename check after a lookup): the lookup was sent, the refused search changes nothing.
        self.atlas.put(3, 'pve')
        self.atlas.searches['Third'] = AtlasCooldownError(5)
        self.clock.advance(30)
        charge = quota.ticket('a', False)
        await self.players.profile('pve', 3, ticket=charge)
        with self.assertRaises(AtlasCooldownError):
            await self.players.search('Third', 'pve', ticket=charge)
        self.assertEqual(len(quota.recent('a')), 3)
        # A lookup Atlas answered with an error was sent too.
        self.atlas.docs[(4, 'pve')] = AtlasHTTPError(502)
        self.clock.advance(30)
        charge = quota.ticket('a', False)
        with self.assertRaises(AtlasHTTPError):
            await self.players.profile('pve', 4, ticket=charge)
        with self.assertRaises(AtlasCooldownError):
            await self.players.search('Third', 'pve', ticket=charge)
        self.assertEqual(len(quota.recent('a')), 4)

    async def test_cache_at_least_a_minute_and_refresh_skips_it(self):
        self.atlas.put(1, 'pve')
        charges = []
        await self.players.profile('pve', 1, ticket=SimpleNamespace(check=lambda: charges.append(1), sent=lambda: None))
        self.clock.advance(59)
        cached = await self.players.profile('pve', 1, ticket=SimpleNamespace(check=lambda: charges.append(1), sent=lambda: None))
        self.assertTrue(cached['from_cache'])
        await self.players.profile('pve', 1, refresh=True, ticket=SimpleNamespace(check=lambda: charges.append(1), sent=lambda: None))
        self.assertEqual(len(self.atlas.calls), 2)
        self.assertEqual(charges, [1, 1])
        self.clock.advance(60)
        await self.players.profile('pve', 1)
        self.assertEqual(len(self.atlas.calls), 3)

    async def test_busy_is_retried_once(self):
        busy = lambda: AtlasHTTPError(429, 'busy', 5)
        self.atlas.docs[(1, 'pve')] = [busy(), atlas_profile('pve', accountId='1')]
        self.assertEqual((await self.players.profile('pve', 1))['nickname'], 'Test_Player')
        self.atlas.docs[(2, 'pvp')] = [busy(), busy(), atlas_profile('pvp')]
        with self.assertRaises(AtlasHTTPError):
            await self.players.profile('pvp', 2)
        self.assertEqual(len(self.atlas.calls), 4)

    async def test_background_cancellation_does_not_join_or_cancel_a_foreground_task(self):
        self.atlas.put(1, 'pve')
        entered, release = asyncio.Queue(), asyncio.Event()
        async def lookup(aid, mode, **kwargs):
            await entered.put(kwargs)
            await release.wait()
            return atlas_profile(mode, accountId=str(aid))
        self.atlas.lookup = lookup
        foreground = asyncio.create_task(self.players.profile('pve', 1))
        background = None
        try:
            self.assertEqual(await asyncio.wait_for(entered.get(), 2), {'deadline': None, 'include_art': True})
            background = asyncio.create_task(self.players.profile_once('pve', 1, deadline=12345))
            self.assertEqual(await asyncio.wait_for(entered.get(), 2), {'deadline': 12345, 'include_art': False})
            background.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await background
            self.assertFalse(foreground.done())
            release.set()
            self.assertEqual((await foreground)['nickname'], 'Test_Player')
            self.assertTrue(self.players.cached('pve', 1)['from_cache'])
        finally:
            release.set()
            for task in (foreground, background):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in (foreground, background) if task), return_exceptions=True)

    async def test_no_profile_is_not_found_and_remembered_briefly(self):
        self.atlas.docs[(1, 'season')] = AtlasHTTPError(404, 'no_profile')
        for _ in range(2):
            with self.assertRaises(AtlasNotFound) as caught:
                await self.players.profile('season', 1)
            self.assertIn('PVP 赛季', str(caught.exception))
        self.assertEqual(len(self.atlas.calls), 1)
        self.clock.advance(60)
        with self.assertRaises(AtlasNotFound):
            await self.players.profile('season', 1)
        self.assertEqual(len(self.atlas.calls), 2)

    async def test_equal_requests_share_one_lookup(self):
        self.atlas.put(1, 'pve')
        self.atlas.gate = asyncio.Event()
        tasks = [asyncio.ensure_future(self.players.profile('pve', 1)) for _ in range(5)]
        await asyncio.sleep(0)
        tasks[0].cancel()
        self.atlas.gate.set()
        results = await asyncio.gather(*tasks[1:])
        self.assertEqual({result['nickname'] for result in results}, {'Test_Player'})
        self.assertEqual(len(self.atlas.calls), 1)

    async def test_wrong_mode_document_is_rejected(self):
        self.atlas.docs[(1, 'pve')] = atlas_profile('pvp')
        with self.assertRaises(AtlasProfileError):
            await self.players.profile('pve', 1)

    async def test_search(self):
        self.atlas.searches['Test_Player'] = [(1, 'Test_Player')]
        self.assertEqual(await self.players.search('Test_Player', 'pve'), [(1, 'Test_Player')])
        self.assertEqual(len(self.atlas.calls), 1)

    def test_failure_classification(self):
        self.assertTrue(can_fall_back(AtlasHTTPError(429, 'busy', 5)))
        self.assertTrue(can_fall_back(AtlasHTTPError(502, 'http')))
        self.assertFalse(can_fall_back(AtlasBudgetError(60)))
        self.assertTrue(can_fall_back(AtlasCooldownError(30)))
        self.assertTrue(can_fall_back(ConnectionFailure('x')))
        for exc in (AtlasNotFound('x'), QuotaError('x'), AtlasHTTPError(400, 'bad_account'), ValueError()):
            self.assertFalse(can_fall_back(exc))
        self.assertIn('正忙', failure_text(AtlasHTTPError(429, 'busy', 5)))
        self.assertIn('60 秒', failure_text(AtlasHTTPError(429, 'rate', 60)))


class QuotaTests(unittest.TestCase):
    def test_hourly_counts_and_intervals(self):
        clock = Clock()
        quota = UserQuota(lambda: (3, 1, 10, 300), clock=clock)
        use(quota, 'a', False)
        with self.assertRaises(QuotaError) as caught:
            quota.check('a', False)
        self.assertIn('秒后再试', str(caught.exception))
        quota.check('b', False)
        clock.advance(10)
        use(quota, 'a', True)
        clock.advance(10)
        with self.assertRaises(QuotaError) as caught:
            quota.check('a', True)
        self.assertIn('刷新次数（1 次）已用完', str(caught.exception))
        use(quota, 'a', False)
        clock.advance(10)
        with self.assertRaises(QuotaError) as caught:
            quota.check('a', False)
        self.assertIn('查询次数（3 次）已用完', str(caught.exception))
        clock.advance(3600)
        quota.check('a', True)

    def test_a_ticket_counts_once_and_refreshes_have_their_own_limit(self):
        quota = UserQuota(lambda: (5, 1, 0, 0), clock=Clock())
        charge = quota.ticket('a', False)
        charge.sent(); charge.sent()
        self.assertEqual(len(quota.recent('a')), 1)
        use(quota, 'a', True)
        with self.assertRaises(QuotaError):
            use(quota, 'a', True)


