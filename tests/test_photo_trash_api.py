import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
import httpx

import dashboard
from photo_trash import PhotoTrash, PendingPhotoSaves
from photo_trash_api import register_photo_trash_routes


class TrashApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.store = PhotoTrash(base / 'photos', base / 'dithered_photos', base / '.photo-trash')
        (self.store.originals / '00001.jpg').write_bytes(b'original')
        (self.store.processed / '00001_dithered.png').write_bytes(b'processed')
        self.lock = threading.Lock()
        self.saves = PendingPhotoSaves()
        self.display_busy = False
        app = FastAPI()
        register_photo_trash_routes(app, lambda: self.store, self.lock,
                                   lambda: self.display_busy or self.saves.busy(), lambda: None)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_end_to_end_bulk_preview_restore(self):
        response = self.client.post('/api/photos/trash', json={'ids': ['00001']})
        self.assertEqual(response.status_code, 200)
        entry_id = response.json()['completed'][0]['id']
        self.assertEqual(self.client.get('/api/trash').json()['total'], 1)
        preview = self.client.get(f'/api/trash/{entry_id}/preview')
        self.assertEqual(preview.content, b'processed')
        self.assertEqual(preview.headers['cache-control'], 'no-store')
        self.assertTrue(self.client.post('/api/trash/restore', json={'ids': [entry_id]}).json()['success'])
        self.assertEqual(self.client.get('/api/trash').json()['total'], 0)

    def test_busy_guards_never_move_files(self):
        for kind in ('display', 'operation', 'save'):
            event = threading.Event()
            worker = None
            try:
                if kind == 'display': self.display_busy = True
                if kind == 'operation': self.lock.acquire()
                if kind == 'save': worker = self.saves.start(lambda: event.wait(5))
                response = self.client.post('/api/photos/trash', json={'ids': ['00001']})
                self.assertEqual(response.status_code, 409, kind)
                self.assertTrue((self.store.originals / '00001.jpg').exists())
                self.assertTrue((self.store.processed / '00001_dithered.png').exists())
            finally:
                self.display_busy = False
                if kind == 'operation': self.lock.release()
                event.set()
                if worker: worker.join(2)

    def test_invalid_json_and_ids(self):
        for body in (None, {}, {'ids': ['../bad']}, {'ids': ['a'] * 101}):
            self.assertEqual(self.client.post('/api/photos/trash', json=body).status_code, 400)


class DashboardTrashProxyTests(unittest.TestCase):
    def test_proxy_requires_custom_header_and_forwards_ids(self):
        with TestClient(dashboard.app) as client, patch.object(dashboard.reframe_client, 'post', new_callable=AsyncMock) as post:
            self.assertEqual(client.post('/api/photos/trash', json={'ids': ['00001']}).status_code, 403)
            post.assert_not_called()
            post.return_value = {'success': True, 'completed': [], 'failed': []}
            response = client.post('/api/photos/trash', json={'ids': ['00001', '00001']}, headers={'X-Reframe-Action': 'photo-library'})
            self.assertEqual(response.status_code, 200)
            post.assert_awaited_once_with('/photos/trash', json={'ids': ['00001']})

    def test_proxy_preserves_busy_and_releases_gate(self):
        request = httpx.Request('POST', 'http://fixture/photos/trash')
        response = httpx.Response(409, json={'detail': 'Camera is busy'}, request=request)
        error = httpx.HTTPStatusError('busy', request=request, response=response)
        with TestClient(dashboard.app) as client, patch.object(dashboard.reframe_client, 'post', new_callable=AsyncMock, side_effect=error):
            result = client.post('/api/photos/trash', json={'ids': ['00001']}, headers={'X-Reframe-Action': 'photo-library'})
            self.assertEqual(result.status_code, 409)
            self.assertFalse(dashboard.library_mutation_active)

    def test_zip_and_mutations_exclude_each_other_before_await(self):
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            async def get(path):
                entered.set()
                await release.wait()
                return []
            with patch.object(dashboard.reframe_client, 'get', side_effect=get):
                download = asyncio.create_task(dashboard.start_download_all(BackgroundTasks()))
                await entered.wait()
                self.assertTrue(dashboard.download_job_active)
                release.set()
                with self.assertRaises(HTTPException): await download
                self.assertFalse(dashboard.download_job_active)
            with patch.object(dashboard, 'library_mutation_active', True):
                with self.assertRaises(HTTPException) as error:
                    await dashboard.start_download_all(BackgroundTasks())
                self.assertEqual(error.exception.status_code, 409)
        asyncio.run(scenario())

    def test_assets_and_backup_privacy(self):
        with TestClient(dashboard.app) as client:
            self.assertEqual(client.get('/assets/photo-library.js').status_code, 200)
            self.assertEqual(client.get('/assets/photo-library.css').status_code, 200)
            self.assertEqual(client.get('/assets/settings.json').status_code, 404)
        self.assertIn('.photo-trash', dashboard.USER_DATA_PATHS)

    def test_delete_all_cannot_start_during_trash_or_export(self):
        async def scenario():
            for flag in ('library_mutation_active', 'download_job_active', 'delete_job_active'):
                with patch.object(dashboard, flag, True):
                    for function, args in ((dashboard.start_delete_all, (BackgroundTasks(),)),
                                           (dashboard.delete_all_photos, ()),
                                           (dashboard.start_download_all, (BackgroundTasks(),))):
                        with self.assertRaises(HTTPException) as error:
                            await function(*args)
                        self.assertEqual(error.exception.status_code, 409)
        asyncio.run(scenario())

    def test_delete_reservation_survives_abort_until_worker_finishes(self):
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            async def delete(path):
                entered.set()
                await release.wait()
                return {'success': True}
            with patch.object(dashboard.reframe_client, 'delete', side_effect=delete), \
                 patch.object(dashboard, 'delete_job_active', True), \
                 patch.object(dashboard, 'delete_abort', False), \
                 patch.object(dashboard, 'delete_progress', {'status': 'preparing', 'processed': 0, 'total': 1}):
                task = asyncio.create_task(dashboard.delete_photos_background([{'id': 'fixture'}]))
                await entered.wait()
                await dashboard.abort_delete()
                self.assertTrue(dashboard.delete_job_active)
                with self.assertRaises(HTTPException): await dashboard.start_download_all(BackgroundTasks())
                release.set()
                await task
                self.assertFalse(dashboard.delete_job_active)
        asyncio.run(scenario())

    def test_zip_does_not_silently_succeed_with_a_missing_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(dashboard, 'download_abort', False):
            with self.assertRaises(RuntimeError):
                dashboard.create_zip_file([{'id': 'fixture', 'original_path': str(Path(folder) / 'missing.jpg')}], str(Path(folder) / 'fixture.zip'))


if __name__ == '__main__': unittest.main()
