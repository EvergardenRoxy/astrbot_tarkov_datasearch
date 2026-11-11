import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tarkov_core.atlas_access import AtlasAccess, AtlasAccessPaused
from tarkov_core.transport import QueryError


class AccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'atlas-access.json'
        self.access = AtlasAccess(self.path)

    async def test_first_use_does_not_create_a_file_or_request_recovery(self):
        await self.access.check('players')
        self.assertFalse(await self.access.before_send('players'))
        await self.access.arm_recovery()
        self.assertFalse(await self.access.before_send('matching'))
        self.assertEqual(json.loads(self.path.read_text())['scopes'], {})

    async def test_refusal_survives_restart_and_keeps_other_scope_available(self):
        await self.access.block('players', 403)
        restored = AtlasAccess(self.path)
        with self.assertRaises(AtlasAccessPaused):
            await restored.check('players')
        await restored.check('matching')
        data = json.loads(self.path.read_text())
        self.assertEqual(set(data), {'version', 'scopes'})
        self.assertEqual(set(data['scopes']['players']), {'state', 'status', 'at'})

    async def test_recovery_arms_one_probe_then_opens_on_success(self):
        await self.access.block('players', 403)
        await self.access.arm_recovery()
        await self.access.check('players')
        self.assertTrue(await self.access.before_send('players'))
        with self.assertRaises(AtlasAccessPaused):
            await self.access.before_send('players')
        await self.access.finish_probe('players', True)
        self.assertFalse(await self.access.before_send('players'))
        await AtlasAccess(self.path).check('players')

    async def test_failed_and_interrupted_probes_stay_blocked(self):
        for success in (False, None):
            await self.access.block('players', 401)
            await self.access.arm_recovery()
            await self.access.before_send('players')
            if success is False:
                await self.access.finish_probe('players', False)
            restored = AtlasAccess(self.path)
            with self.assertRaises(AtlasAccessPaused):
                await restored.check('players')

    async def test_corrupt_state_requires_explicit_recovery(self):
        for body in ('broken', '{"version":2,"scopes":{}}', '{"version":1,"scopes":{"evil":{}}}'):
            self.path.write_text(body)
            restored = AtlasAccess(self.path)
            for scope in ('players', 'matching'):
                with self.assertRaises(AtlasAccessPaused):
                    await restored.check(scope)
            await restored.arm_recovery()
            self.assertTrue(await restored.before_send('players'))

    async def test_write_failure_does_not_unlock(self):
        await self.access.block('players', 403)
        with patch.object(self.access, '_write', side_effect=OSError('private path')):
            with self.assertRaises(QueryError):
                await self.access.arm_recovery()
        with self.assertRaises(AtlasAccessPaused):
            await self.access.check('players')
        with self.assertRaises(AtlasAccessPaused):
            await AtlasAccess(self.path).check('players')

    async def test_failed_probe_state_save_prevents_sending(self):
        await self.access.block('players', 403)
        await self.access.arm_recovery()
        with patch.object(self.access, '_write', side_effect=OSError()):
            with self.assertRaises(AtlasAccessPaused):
                await self.access.before_send('players')
        self.assertTrue(self.access.is_blocked('players'))

    async def test_cancellation_waits_for_pending_disk_write(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def worker():
            started.set()
            await release.wait()
            return 42
        # The helper shields work, waits on cancellation, and never leaves a writer running.
        with patch('asyncio.to_thread', new=lambda *args: worker()):
            task = asyncio.create_task(AtlasAccess._worker(lambda: None))
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
