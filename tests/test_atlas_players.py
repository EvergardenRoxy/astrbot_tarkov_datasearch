"""Atlas player source: document mapping, budget, gate waiting, client endpoints, player service, quotas, and the
plugin's query/bind flow. Every document is invented (fixtures.atlas_profile); no test reaches the real service."""
import asyncio
import copy
import unittest
from unittest.mock import patch

from aiohttp import web

from fixtures import ATLAS_ARENA, arena_profile, atlas_profile, profile
from tarkov_core import atlas_client
from tarkov_core.atlas_client import AtlasClient, AtlasHTTPError, allowed
from tarkov_core.atlas_cooldown import AtlasBudget, AtlasBudgetError, AtlasCooldown, AtlasCooldownError
from tarkov_core.atlas_players import (AtlasNotFound, AtlasPlayers, QuotaError, UserQuota, can_fall_back,
                                       failure_text)
from tarkov_core.atlas_profile import AtlasProfileError, search_results, summarize
from tarkov_core.transport import ConnectionFailure, QueryError
from test_atlas_cooldown import Clock
import test_plugin
from test_plugin import Event, FakeNames, ImageStub, PlainStub


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
    def test_default_budget_is_ten_per_minute(self):
        clock = Clock()
        budget = AtlasBudget(clock=clock)
        for _ in range(10):
            budget.reserve()
        with self.assertRaises(AtlasBudgetError):
            budget.reserve()
        clock.advance(60)
        budget.reserve()
        self.assertEqual(budget.remaining_hour(), 139)

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
        self.assertEqual(budget.remaining_hour(), 1)
        clock.advance(3600)
        self.assertEqual(budget.remaining_hour(), 3)

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
        self.client = AtlasClient(cooldown=AtlasCooldown(10, clock=self.clock), budget=self.budget)
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
        self.host.stop()
        await self.runner.cleanup()

    async def test_lookup_and_search_requests(self):
        await self.client.lookup(1234567, 'pve')
        self.clock.advance(10)
        self.answer = lambda request: web.json_response({'nickname': 'Test_Player', 'players': [], 'cached': False})
        await self.client.search('Test_Player', 'season')
        self.assertEqual(self.seen, [('/atlas/api/lookup', {'accountId': '1234567', 'mode': 'pve'}),
                                     ('/atlas/api/lookup/search', {'nickname': 'Test_Player', 'mode': 'season'})])
        self.assertEqual(self.budget.remaining_hour(), 148)

    def test_only_fixed_requests_are_allowed(self):
        self.assertTrue(allowed('/atlas/api/lookup', {'accountId': '1', 'mode': 'arena'}))
        for path, params in (('/atlas/api/lookup', {'accountId': '0', 'mode': 'pve'}),
                             ('/atlas/api/lookup', {'accountId': '1234567890123', 'mode': 'pve'}),
                             ('/atlas/api/lookup', {'accountId': '１２', 'mode': 'pve'}),
                             ('/atlas/api/lookup', {'accountId': '1', 'mode': 'regular'}),
                             ('/atlas/api/lookup', {'accountId': '1', 'mode': 'pve', 'refresh': '1'}),
                             ('/atlas/api/lookup/search', {'nickname': 'TarkovCitizen1234567', 'mode': 'pve'}),
                             ('/atlas/api/lookup/search', {'nickname': 'a b', 'mode': 'pve'}),
                             ('/atlas/api/other', {'mode': 'pve'})):
            with self.subTest(path=path, params=params):
                self.assertFalse(allowed(path, params))

    async def test_refused_requests_are_refunded_and_an_empty_budget_sends_nothing(self):
        self.answer = lambda request: web.json_response({'detail': {'error': 'busy'}}, status=429, headers={'Retry-After': '5'})
        with self.assertRaises(AtlasHTTPError) as caught:
            await self.client.lookup(1234567, 'pve')
        self.assertEqual(caught.exception.code, 'busy')
        self.assertEqual(self.budget.remaining_hour(), 150)
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
        async def gate(max_wait):
            waits.append(max_wait)
            yield
        for interval, expected in ((10, 20), (20, 30), (30, 40), (300, 310)):
            self.client.cooldown.interval = interval
            with patch.object(self.client.cooldown, 'claim_within', gate):
                await self.client.search('Test_Player', 'pve')
                await self.client.lookup(1, 'pve')
            self.assertEqual(waits[-2:], [expected, expected])
        with patch.object(self.client.cooldown, 'claim_within', gate):
            await self.client.lookup(1, 'pve', max_wait=1)
        self.assertEqual(waits[-1], 1)

    async def test_a_refused_gate_gives_the_budget_back(self):
        self.client.cooldown.pause(600)
        with self.assertRaises(AtlasCooldownError):
            await self.client.lookup(1234567, 'pve', max_wait=1)
        self.assertEqual(self.budget.remaining_hour(), 150)
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
        async def delayed_gate(max_wait):
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
        async def competing_gate(max_wait):
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
                    await self.players.profile('pve', 1, charge=quota.ticket('a', False))
                self.assertEqual(quota.history['a'], [])
                quota.check('a', False)  # No 30-second wait either.
                self.atlas.searches['Name'] = refusal
                with self.assertRaises(type(refusal)):
                    await self.players.search('Name', 'pve', charge=quota.ticket('a', False))
                self.assertEqual(quota.history['a'], [])
        # A request Atlas answered (even with an error) still counts.
        self.atlas.docs[(1, 'pve')] = AtlasHTTPError(502)
        with self.assertRaises(AtlasHTTPError):
            await self.players.profile('pve', 1, charge=quota.ticket('a', False))
        self.assertEqual(len(quota.history['a']), 1)

    async def test_a_sent_search_still_counts_when_the_lookup_is_refused(self):
        """Only a query with nothing sent is given back: after the nickname search reached Atlas, a refused lookup
        must not make the whole query free (it could then be repeated at the cost of the shared budget)."""
        quota = UserQuota(lambda: (10, 3, 30, 300), clock=self.clock)
        self.atlas.searches['Name'] = [(1, 'Name')]
        self.atlas.docs[(1, 'pve')] = AtlasCooldownError(5)
        charge = quota.ticket('a', False)
        self.assertEqual(await self.players.search('Name', 'pve', charge=charge), [(1, 'Name')])
        with self.assertRaises(AtlasCooldownError):
            await self.players.profile('pve', 1, charge=charge)
        self.assertEqual(len(quota.history['a']), 1)
        # A search Atlas answered with an error was sent as well.
        self.atlas.searches['Other'] = AtlasHTTPError(502)
        self.atlas.docs[(2, 'pve')] = AtlasBudgetError(60)
        self.clock.advance(30)
        charge = quota.ticket('a', False)
        with self.assertRaises(AtlasHTTPError):
            await self.players.search('Other', 'pve', charge=charge)
        with self.assertRaises(AtlasBudgetError):
            await self.players.profile('pve', 2, charge=charge)
        self.assertEqual(len(quota.history['a']), 2)
        # The other order (the rename check after a lookup): the lookup was sent, the refused search changes nothing.
        self.atlas.put(3, 'pve')
        self.atlas.searches['Third'] = AtlasCooldownError(5)
        self.clock.advance(30)
        charge = quota.ticket('a', False)
        await self.players.profile('pve', 3, charge=charge)
        with self.assertRaises(AtlasCooldownError):
            await self.players.search('Third', 'pve', charge=charge)
        self.assertEqual(len(quota.history['a']), 3)
        # A lookup Atlas answered with an error was sent too.
        self.atlas.docs[(4, 'pve')] = AtlasHTTPError(502)
        self.clock.advance(30)
        charge = quota.ticket('a', False)
        with self.assertRaises(AtlasHTTPError):
            await self.players.profile('pve', 4, charge=charge)
        with self.assertRaises(AtlasCooldownError):
            await self.players.search('Third', 'pve', charge=charge)
        self.assertEqual(len(quota.history['a']), 4)

    async def test_cache_at_least_a_minute_and_refresh_skips_it(self):
        self.atlas.put(1, 'pve')
        charges = []
        await self.players.profile('pve', 1, charge=lambda: charges.append(1))
        self.clock.advance(59)
        cached = await self.players.profile('pve', 1, charge=lambda: charges.append(1))
        self.assertTrue(cached['from_cache'])
        await self.players.profile('pve', 1, refresh=True, charge=lambda: charges.append(1))
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
        with self.assertRaises(QueryError):
            await self.players.search('TarkovCitizen1234567', 'pve')
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
        quota.ticket('a', False)()
        with self.assertRaises(QuotaError) as caught:
            quota.check('a', False)
        self.assertIn('秒后再试', str(caught.exception))
        quota.check('b', False)
        clock.advance(10)
        quota.ticket('a', True)()
        clock.advance(10)
        with self.assertRaises(QuotaError) as caught:
            quota.check('a', True)
        self.assertIn('刷新次数（1 次）已用完', str(caught.exception))
        quota.ticket('a', False)()
        clock.advance(10)
        with self.assertRaises(QuotaError) as caught:
            quota.check('a', False)
        self.assertIn('查询次数（3 次）已用完', str(caught.exception))
        clock.advance(3600)
        quota.check('a', True)

    def test_a_ticket_charges_once_and_refresh_can_be_off(self):
        quota = UserQuota(lambda: (5, 0, 0, 0), clock=Clock())
        charge = quota.ticket('a', False)
        charge(); charge()
        self.assertEqual(len(quota.history['a']), 1)
        with self.assertRaises(QuotaError):
            quota.ticket('a', True)()


class FakeAssets:
    """Stands in for AtlasAssets: a fixed catalog, pictures from a table, no network."""
    def __init__(self):
        self.catalog_data = {'achievements': {'a1': 'https://assets.tarkov.dev/achievement-a1-icon.webp'},
                             'levels': [[1, 'https://assets.tarkov.dev/player-level-group-1.png']], 'prestige': {}}
        self.asked = []

    async def catalog(self):
        return self.catalog_data

    async def catalog_for(self, achievement_ids):
        return self.catalog_data

    async def images(self, urls):
        self.asked.append([url for url in urls if url])
        return {}

    async def close(self):
        pass


class FakeFont:
    """Stands in for FullFont: never downloads; counts the first-use calls."""
    def __init__(self):
        self.calls = 0

    async def ensure(self):
        self.calls += 1
        from font_support import TEST_FONT
        return TEST_FONT

    async def close(self):
        pass


class AtlasPluginTests(unittest.IsolatedAsyncioTestCase):
    """The plugin with the Atlas source (the default), a fake Atlas client and fake picture cache."""
    setUp = test_plugin.PluginTests.setUp
    tearDown = test_plugin.PluginTests.tearDown
    fake_fetch = test_plugin.PluginTests.fake_fetch
    run_command = test_plugin.PluginTests.run_command

    def plugin(self, config=None, names=None, draw=False):
        config = dict(config or {})
        config.setdefault('name_index_enabled', names is not None)
        plugin = self.module.TarkovPlugin(test_plugin.types.SimpleNamespace(), config)
        plugin.names = names or FakeNames()
        self.atlas = FakeAtlas()
        self.atlas.errors = test_plugin.sys.modules['tarkov_test_plugin.tarkov_core.atlas_client']
        plugin.atlas_players.client = self.atlas
        plugin.atlas_assets = self.assets = FakeAssets()
        plugin.full_font = self.font = FakeFont()
        self.quota_clock = Clock()
        plugin.quota.clock = self.quota_clock
        self.cards = []
        if not draw:
            def record(card, icons=None):
                self.cards.append(card)
                return b'\xff\xd8picture'
            patcher = patch.object(self.module, 'render_atlas_image', record)
            patcher.start()
            self.addCleanup(patcher.stop)
        return plugin

    def error(self, *args):
        """An AtlasHTTPError of the plugin's own copy of the package."""
        return self.atlas.errors.AtlasHTTPError(*args)

    async def ask(self, plugin, text, sender='alice', *, advance=30):
        self.quota_clock.advance(advance)
        return await self.run_command(plugin, Event(text, sender=sender))

    @staticmethod
    def text(reply):
        """The text of a reply: a plain string, or the Plain parts after the picture."""
        return '\n'.join(part.text for part in reply if isinstance(part, PlainStub)) if isinstance(reply, list) else reply

    async def test_defaults_atlas_pve_and_a_picture_whatever_the_output(self):
        plugin = self.plugin({'output_mode': 'text'})
        self.assertEqual((plugin.player_source(), plugin.default_mode()), ('atlas', 'pve'))
        self.atlas.put(1234567, 'pve')
        [reply] = await self.ask(plugin, '/tarkov 1234567')
        self.assertEqual(self.atlas.calls, [('lookup', 1234567, 'pve')])
        self.assertEqual(self.fetched, [])
        self.assertIsInstance(reply[0], ImageStub)
        text = self.text(reply)
        self.assertIn('数据来源：Atlas https://eftforge.com/atlas　tarkov.dev 玩家页：https://tarkov.dev/players/pve/1234567', text)
        self.assertIn('@机器人 tarkov pve 1234567 刷新', text)
        [card] = self.cards
        self.assertEqual((card['nickname'], card['mode_label'], card['aid']), ('Test_Player', 'PVE', '1234567'))
        # The first picture asks for the full font (the verified full font is prepared before rendering).
        self.assertEqual(self.font.calls, 1)
        # The icons asked for: item pictures, the known achievement icon and the level badge.
        self.assertIn('https://assets.tarkov.dev/player-level-group-1.png', self.assets.asked[0])

    async def test_one_mode_per_query_including_arena_uses_atlas(self):
        plugin = self.plugin({'arena_by_default': True})
        self.atlas.put(1, 'season')
        self.atlas.put(1, 'arena')
        await self.ask(plugin, '/tarkov 赛季 1')
        self.assertEqual(self.cards[0]['mode_label'], 'PVP 赛季')
        reply = (await self.ask(plugin, '/tarkov 竞技场 1'))[0]
        self.assertIn('https://tarkov.dev/players/arena/1', self.text(reply))
        self.assertEqual(self.atlas.calls, [('lookup', 1, 'season'), ('lookup', 1, 'arena')])
        self.assertEqual(self.fetched, [])
        self.assertIsInstance(reply[0], ImageStub)
        self.assertTrue(self.cards[-1]['overall_table'])
        self.assertEqual(self.cards[-1]['achievement_total'], ATLAS_ARENA['achievements']['total'])
        self.assertEqual(len(self.assets.asked), 1)  # Only season needs the main game's icons.
        entry = await asyncio.to_thread(plugin.atlas_history.latest, 1, 'arena')
        self.assertEqual(entry['document']['mode'], 'arena')

    async def test_arena_cached_refresh_url_name_and_real_picture(self):
        plugin = self.plugin(draw=True)
        self.atlas.put(1, 'arena')
        url = 'https://tarkov.dev/players/arena/1'
        [reply] = await self.ask(plugin, f'/tarkov {url}')
        self.assertTrue(reply[0].data.startswith(b'\xff\xd8'))
        self.assertIn('@机器人 tarkov 竞技场 1 刷新', self.text(reply))
        await self.ask(plugin, '/tarkov arena 1')
        self.assertEqual(self.atlas.calls, [('lookup', 1, 'arena')])
        await self.ask(plugin, '/tarkov arena Test_Player')
        self.assertEqual(self.atlas.calls, [('lookup', 1, 'arena')])
        await self.ask(plugin, '/tarkov arena 1 刷新')
        self.assertEqual(self.atlas.calls, [('lookup', 1, 'arena')]*2)
        self.assertEqual(self.assets.asked, [])

    async def test_arena_failure_falls_back_but_missing_profile_does_not(self):
        plugin = self.plugin({'output_mode': 'text'})
        self.profiles[('arena', 1)] = arena_profile(aid=1)
        self.atlas.docs[(1, 'arena')] = [self.error(429, 'busy', 5)]*2
        [reply] = await self.ask(plugin, '/tarkov arena 1')
        self.assertIn('tarkov.dev 的快照', reply)
        self.assertIn('【塔科夫竞技场档案】', reply)
        self.assertEqual(self.fetched, [('arena', 1)])
        self.assertIsNone(await asyncio.to_thread(plugin.atlas_history.latest, 1, 'arena'))
        self.atlas.docs[(2, 'arena')] = self.error(404, 'no_profile')
        [reply] = await self.ask(plugin, '/tarkov arena 2')
        self.assertIn('竞技场 模式下没有档案（Atlas）', reply)
        self.assertEqual(self.fetched, [('arena', 1)])

    async def test_names_use_the_record_then_the_list_then_atlas_search(self):
        names = FakeNames({'pve': [(2, 'Listed_One')]})
        plugin = self.plugin(names=names)
        await asyncio.to_thread(plugin.accounts.remember, 1, 'Known_One')
        for aid in (1, 2, 3):
            self.atlas.put(aid, 'pve', nickname={1: 'Known_One', 2: 'Listed_One', 3: 'Searched_One'}[aid])
        self.atlas.searches['Searched_One'] = [(3, 'Searched_One')]
        for name in ('Known_One', 'Listed_One', 'searched_one'):
            reply = (await self.ask(plugin, f'/tarkov pve {name}'))[-1]
            self.assertIsInstance(reply[0], ImageStub)
            self.assertNotIn('注意', self.text(reply))
        self.assertEqual(self.atlas.calls, [('lookup', 1, 'pve'), ('lookup', 2, 'pve'), ('search', 'searched_one', 'pve'),
                                            ('lookup', 3, 'pve')])
        self.assertEqual(names.lookups, [('pve', 'Listed_One'), ('pve', 'searched_one')])
        self.atlas.calls.clear()
        await self.ask(plugin, '/tarkov pve Searched_One')
        self.assertEqual(self.atlas.calls, [])

    async def test_a_stale_record_is_checked_with_atlas_search(self):
        plugin = self.plugin()
        await asyncio.to_thread(plugin.accounts.remember, 5, 'Old_Name')
        self.atlas.put(5, 'pve', nickname='New_Name')
        self.atlas.put(6, 'pve', nickname='Old_Name')
        self.atlas.searches['Old_Name'] = [(6, 'Old_Name')]
        [reply] = await self.ask(plugin, '/tarkov pve Old_Name')
        self.assertIn('Atlas 搜索到的 Old_Name 是账号 ID 6', self.text(reply))
        self.assertEqual(self.cards[-1]['aid'], '6')
        await asyncio.to_thread(plugin.accounts.remember, 7, 'Gone_Name')
        self.atlas.put(7, 'pve', nickname='Now_Other')
        [reply] = await self.ask(plugin, '/tarkov pve Gone_Name')
        self.assertIn('档案里现在的昵称是 Now_Other', self.text(reply))

    async def test_unknown_and_ambiguous_names(self):
        plugin = self.plugin()
        self.atlas.searches['Two_Of_Us'] = [(1, 'two_of_us'), (2, 'Two_of_Us')]
        self.assertIn('有多个名为 Two_Of_Us 的玩家', (await self.ask(plugin, '/tarkov pve Two_Of_Us'))[0])
        self.assertIn('Atlas 在 PVE 模式下没有找到名为 Nobody_Here 的玩家', (await self.ask(plugin, '/tarkov Nobody_Here'))[0])
        self.assertIn('3～15 个字符', (await self.ask(plugin, '/tarkov TarkovCitizen1234567'))[0])
        self.assertEqual([call[0] for call in self.atlas.calls], ['search', 'search'])

    async def test_refresh_word_quota_and_cache(self):
        plugin = self.plugin({'user_refresh_per_hour': 1})  # Retired setting has no effect.
        self.atlas.put(1, 'pve')
        await self.ask(plugin, '/tarkov pve 1')
        await self.ask(plugin, '/tarkov pve 1')
        self.assertEqual(len(self.atlas.calls), 1)
        self.atlas.put(1, 'pve', cached=True)
        await self.ask(plugin, '/tarkov pve 1 刷新')
        self.assertEqual(len(self.atlas.calls), 2)
        self.assertTrue(self.cards[-1]['cached'])
        await self.ask(plugin, '/tarkov pve 1 refresh', advance=300)
        await self.ask(plugin, '/tarkov pve 1 refresh', advance=300)
        self.assertIn('刷新次数（3 次）已用完', (await self.ask(plugin, '/tarkov pve 1 refresh', advance=300))[0])
        self.assertEqual(len(self.atlas.calls), 4)
        await self.ask(plugin, '/tarkov pve 1 刷新', sender='bob')
        self.assertEqual(len(self.atlas.calls), 5)

    async def test_user_interval_counts_only_atlas_queries(self):
        plugin = self.plugin({'user_query_cooldown_seconds': 30})
        self.atlas.put(1, 'pve')
        self.atlas.put(2, 'pve')
        await self.ask(plugin, '/tarkov 1')
        self.assertIsInstance((await self.ask(plugin, '/tarkov 1', advance=0))[0][0], ImageStub)
        self.assertIn('查询太快了', (await self.ask(plugin, '/tarkov 2', advance=0))[0])
        self.assertEqual(len(self.atlas.calls), 1)

    async def test_fixed_hourly_quota_ignores_removed_settings_and_recovers(self):
        plugin = self.plugin({'atlas': {'user_query_per_hour': 200, 'user_refresh_per_hour': 60}})
        for aid in range(1, 12):
            self.atlas.put(aid, 'pve')
        for aid in range(1, 11):
            reply = (await self.ask(plugin, f'/tarkov {aid}'))[0]
            self.assertIsInstance(reply[0], ImageStub)
        self.assertIn('查询次数（10 次）已用完', (await self.ask(plugin, '/tarkov 11'))[0])
        self.assertEqual(len(self.atlas.calls), 10)
        self.assertIsInstance((await self.ask(plugin, '/tarkov 1'))[0][0], ImageStub)
        self.assertEqual(len(self.atlas.calls), 10)
        self.assertIsInstance((await self.ask(plugin, '/tarkov 11', advance=3600))[0][0], ImageStub)
        self.assertEqual(len(self.atlas.calls), 11)

    async def test_minimum_query_and_refresh_intervals_apply_to_old_zero_settings(self):
        plugin = self.plugin({'user_query_cooldown_seconds': 0, 'user_refresh_cooldown_seconds': 0})
        self.atlas.put(1, 'pve')
        await self.ask(plugin, '/tarkov 1')
        self.assertIn('查询太快了', (await self.ask(plugin, '/tarkov 1 刷新', advance=29))[0])
        self.assertEqual(len(self.atlas.calls), 1)
        self.assertIsInstance((await self.ask(plugin, '/tarkov 1 刷新', advance=1))[0][0], ImageStub)
        self.assertIn('刷新太频繁了', (await self.ask(plugin, '/tarkov 1 刷新', advance=299))[0])
        self.assertEqual(len(self.atlas.calls), 2)
        self.assertIsInstance((await self.ask(plugin, '/tarkov 1 刷新', advance=1))[0][0], ImageStub)
        self.assertEqual(len(self.atlas.calls), 3)

    async def test_global_pause_keeps_cached_profiles_and_never_uses_a_snapshot(self):
        plugin = self.plugin()
        self.atlas.put(1, 'arena')
        await self.ask(plugin, '/tarkov arena 1')
        self.profiles[('arena', 2)] = arena_profile(aid=2)
        self.atlas.docs[(2, 'arena')] = test_plugin.sys.modules['tarkov_test_plugin.tarkov_core.atlas_cooldown'].AtlasBudgetError(60)
        [reply] = await self.ask(plugin, '/tarkov arena 2')
        self.assertIn('60 秒后自动恢复', reply)
        self.assertEqual(self.fetched, [])
        self.assertIsInstance((await self.ask(plugin, '/tarkov arena 1'))[0][0], ImageStub)
        self.atlas.put(2, 'arena')
        self.assertIsInstance((await self.ask(plugin, '/tarkov arena 2', advance=60))[0][0], ImageStub)
        self.assertEqual(self.fetched, [])

    async def test_atlas_failure_falls_back_to_the_old_snapshot_picture(self):
        plugin = self.plugin({'output_mode': 'text'})
        self.profiles[('pve', 1)] = profile(aid=1)
        self.atlas.docs[(1, 'pve')] = [self.error(429, 'busy', 5), self.error(429, 'busy', 5)]
        [reply] = await self.ask(plugin, '/tarkov 1')
        self.assertIn('Atlas 正忙（网站同时查询的人太多），下面是 tarkov.dev 的快照（不是实时数据）', reply)
        self.assertIn('数据来自 tarkov.dev，档案更新于', reply)
        self.assertEqual(self.cards, [])
        self.assertEqual(self.fetched, [('pve', 1)])
        self.atlas.docs[(2, 'pve')] = test_plugin.sys.modules['tarkov_test_plugin.tarkov_core.atlas_cooldown'].AtlasBudgetError(120)
        reply = (await self.ask(plugin, '/tarkov 2'))[0]
        self.assertIn('总额度已用完', reply)
        self.assertIn('自动恢复', reply)
        self.assertNotIn('tarkov.dev', reply)
        self.assertEqual(self.fetched, [('pve', 1)])

    async def test_not_found_is_not_a_failure(self):
        plugin = self.plugin()
        self.atlas.docs[(1, 'pvp')] = self.error(404, 'no_profile')
        reply = (await self.ask(plugin, '/tarkov pvp 1'))[0]
        self.assertIn('账号 ID 1 在 PVP 模式下没有档案（Atlas）', reply)
        self.assertIn('Atlas 找不到账号 ID 9', (await self.ask(plugin, '/tarkov 9'))[0])
        self.assertEqual(self.fetched, [])

    async def test_bind_by_name_and_query_yourself(self):
        plugin = self.plugin()
        self.atlas.put(1, 'pve')
        self.atlas.searches['Test_Player'] = [(1, 'Test_Player')]
        self.assertIn('已绑定：Test_Player（账号 ID 1）', (await self.ask(plugin, '/tarkov 绑定 Test_Player'))[0])
        self.assertIsInstance((await self.ask(plugin, '/tarkov'))[0][0], ImageStub)
        self.atlas.put(1, 'pvp')
        await self.ask(plugin, '/tarkov pvp 刷新')
        self.assertEqual(self.atlas.calls[-1], ('lookup', 1, 'pvp'))
        self.assertIn('还没有绑定账号', (await self.ask(plugin, '/tarkov', sender='bob'))[0])

    async def test_a_failed_atlas_bind_keeps_the_account_the_search_found(self):
        """The name was settled by Atlas' search (or the name list); when the lookup then fails, the tarkov.dev
        fallback binds that account instead of asking the local record again and answering "cannot tell"."""
        plugin = self.plugin()
        self.profiles[('pve', 1)] = profile(aid=1)
        self.atlas.searches['Test_Player'] = [(1, 'Test_Player')]
        self.atlas.docs[(1, 'pve')] = self.error(502)
        reply = (await self.ask(plugin, '/tarkov 绑定 Test_Player'))[0]
        self.assertNotIn('不能确定', reply)
        self.assertIn('（账号 ID 1）', reply)
        self.assertIn('已绑定：', reply)
        # The reply says why the snapshot was used.
        self.assertIn('Atlas 查询失败（HTTP 502），已改用 tarkov.dev 快照确认账号。', reply)
        self.assertEqual(self.fetched[0], ('pve', 1))
        self.assertIsNotNone(await asyncio.to_thread(plugin.accounts.bound, plugin.binding_key(Event('x', sender='alice'))))
        # By account id the fallback worked before and still does, with the same note.
        self.atlas.docs[(2, 'pve')] = self.error(502)
        self.profiles[('pve', 2)] = profile(aid=2)
        reply = (await self.ask(plugin, '/tarkov 绑定 2', sender='bob'))[0]
        self.assertIn('（账号 ID 2）', reply)
        self.assertIn('已改用 tarkov.dev 快照确认账号', reply)

    async def test_usage_and_nickname_texts_match_what_atlas_queries_accept(self):
        plugin = self.plugin()
        usage = (await self.ask(plugin, '/tarkov 绑定'))[0]
        self.assertIn('绑定 <昵称、账号ID 或玩家页网址>', usage)
        # The input rule has no dot, so the texts must not offer one.
        self.assertIn('请发送昵称', (await self.ask(plugin, '/tarkov Dotted.Name'))[0])
        long_name = (await self.ask(plugin, '/tarkov TarkovCitizen1234567'))[0]
        self.assertIn('Atlas 只能搜索 3～15 个字符的昵称（字母、数字、_ -）', long_name)
        with self.assertRaises(QueryError) as caught:
            await AtlasPlayers(FakeAtlas()).search('TarkovCitizen1234567', 'pve')
        self.assertIn('（字母、数字、_ -）', str(caught.exception))

    async def test_a_picture_failure_is_reported_with_the_links(self):
        plugin = self.plugin(draw=True)
        self.atlas.put(1, 'pve')
        with patch.object(self.module, 'render_atlas_image', side_effect=OSError('font')):
            reply = (await self.ask(plugin, '/tarkov 1'))[0]
        self.assertIn('档案图片生成失败（OSError）', reply)
        self.assertIn('tarkov.dev 玩家页：https://tarkov.dev/players/pve/1', reply)

    async def test_missing_complete_font_reports_a_clear_error_instead_of_rendering(self):
        from unittest.mock import AsyncMock
        plugin = self.plugin()
        self.atlas.put(1, 'arena')
        plugin.full_font.ensure = AsyncMock(side_effect=self.module.FontDownloadError('完整中文字体下载失败'))
        [reply] = await self.ask(plugin, '/tarkov arena 1')
        self.assertIn('完整中文字体下载失败', reply)
        self.assertIn('数据来源：Atlas', reply)
        self.assertEqual(self.cards, [])

    async def test_snapshot_and_help_keep_text_replies_when_font_download_fails(self):
        from unittest.mock import AsyncMock
        plugin = self.plugin({'player_source': 'tarkov-dev', 'output_mode': 'image'})
        self.profiles[('pve', 1)] = profile(aid=1)
        plugin.full_font.ensure = AsyncMock(side_effect=self.module.FontDownloadError('完整中文字体下载失败'))
        [reply] = await self.ask(plugin, '/tarkov pve 1')
        self.assertIn('完整中文字体下载失败', reply)
        self.assertIn('【塔科夫 PVE 档案】', reply)
        [help_reply] = await self.ask(plugin, '/tarkov 帮助')
        self.assertIn('完整中文字体下载失败', help_reply)
        self.assertIn('tarkov', help_reply)

    async def test_the_real_picture_is_drawn(self):
        plugin = self.plugin(draw=True)
        self.atlas.put(1, 'pve')
        reply = (await self.ask(plugin, '/tarkov 1'))[0]
        self.assertTrue(reply[0].data.startswith(b'\xff\xd8'))
        self.assertGreater(len(reply[0].data), 50000)

    async def test_history_records_atlas_documents_only_when_switched_on(self):
        plugin = self.plugin()
        self.atlas.put(1, 'pve')
        await self.ask(plugin, '/tarkov 1')
        await self.ask(plugin, '/tarkov 1')
        entry = await asyncio.to_thread(plugin.atlas_history.latest, 1, 'pve')
        self.assertEqual(entry['document']['nickname'], 'Test_Player')
        self.assertEqual(len(await asyncio.to_thread(plugin.atlas_history.history, 1, 'pve')), 1)
        # A tarkov.dev fallback is never stored.
        self.profiles[('pve', 2)] = profile(aid=2)
        self.atlas.docs[(2, 'pve')] = test_plugin.sys.modules['tarkov_test_plugin.tarkov_core.atlas_cooldown'].AtlasBudgetError(60)
        await self.ask(plugin, '/tarkov 2')
        self.assertIsNone(await asyncio.to_thread(plugin.atlas_history.latest, 2, 'pve'))
        off = self.plugin({'atlas_history_enabled': False})
        self.assertTrue(off.atlas_history.path.exists())
        await off.initialize()
        self.assertFalse(off.atlas_history.path.exists())
        self.atlas.put(3, 'pve')
        await self.ask(off, '/tarkov 3')
        self.assertFalse(off.atlas_history.path.exists())

    async def test_one_shot_picture_settings_are_consumed(self):
        config = {'atlas_assets_update': True, 'atlas_assets_clear': True}
        plugin = self.plugin(config)
        calls = []
        async def prefetch():
            calls.append('prefetch'); return 3, 3
        async def clear():
            calls.append('clear'); return 0
        self.assets.prefetch, self.assets.clear = prefetch, clear
        await plugin.initialize()
        self.assertEqual(calls, ['clear', 'prefetch'])
        self.assertIs(plugin.config.get('atlas_assets_update'), False)
        self.assertIs(plugin.config.get('atlas_assets_clear'), False)
        await plugin.initialize()
        self.assertEqual(calls, ['clear', 'prefetch'])

    async def test_the_snapshot_source_never_calls_atlas(self):
        plugin = self.plugin({'player_source': 'tarkov-dev', 'output_mode': 'text'})
        self.profiles[('pve', 1)] = profile(aid=1)
        reply = (await self.ask(plugin, '/tarkov pve 1 刷新'))[0]
        self.assertIn('数据来自 tarkov.dev', reply)
        self.assertEqual(self.atlas.calls, [])
        self.assertEqual(self.cards, [])
