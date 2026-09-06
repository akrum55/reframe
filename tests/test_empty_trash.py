import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import dashboard
from photo_trash import PhotoTrash, TrashError
from photo_trash_api import register_photo_trash_routes


class TrashFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.store = PhotoTrash(self.base / 'photos', self.base / 'processed', self.base / 'trash')
        self.entry = self.add('00009')
        (self.store.originals / '00010.jpg').write_bytes(b'keep original')
        (self.store.processed / '00010_dithered.png').write_bytes(b'keep processed')
        self.active = self.hashes(self.store.originals), self.hashes(self.store.processed)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def hashes(folder):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}

    def add(self, photo_id):
        (self.store.originals / (photo_id + '.jpg')).write_bytes(b'original-' + photo_id.encode())
        (self.store.processed / (photo_id + '_dithered.png')).write_bytes(b'processed-' + photo_id.encode())
        return self.store.move_to_trash([photo_id])['completed'][0]['id']

    def saved(self, entry=None):
        folder = self.store.root / (entry or self.entry)
        return list((folder / 'original').iterdir()) + list((folder / 'processed').iterdir())

    def empty(self):
        return self.store.empty(self.store.empty_preview()['snapshot'])

    def assert_active_untouched(self):
        self.assertEqual((self.hashes(self.store.originals), self.hashes(self.store.processed)), self.active)


class EmptyTrashTests(TrashFixture):
    def test_empty_deletes_only_trash_preserves_active_and_id_tombstone(self):
        plan = self.store.empty_preview()
        self.assertEqual(plan['total'], 1)
        result = self.store.empty(plan['snapshot'])
        self.assertTrue(result['success'])
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.store.list_entries(), [])
        self.assertEqual(self.store.highest_reserved_index(), 9)
        self.assert_active_untouched()
        restarted = PhotoTrash(self.store.originals, self.store.processed, self.store.root)
        self.assertEqual(restarted.highest_reserved_index(), 9)
        self.assertFalse(restarted.restore([self.entry])['success'])
        with self.assertRaises(TrashError): restarted.preview(self.entry)
        with self.assertRaises(TrashError): restarted.empty(plan['snapshot'])
        self.assertTrue(restarted.empty(restarted.empty_preview()['snapshot'])['success'])

    def test_new_entry_after_confirmation_rejects_entire_snapshot(self):
        plan = self.store.empty_preview()
        second = self.add('00011')
        with self.assertRaises(TrashError): self.store.empty(plan['snapshot'])
        self.assertEqual(len(self.saved()), 2)
        self.assertEqual(len(self.saved(second)), 2)
        self.assert_active_untouched()

    def test_highest_purged_numeric_id_stays_reserved_above_active_gallery(self):
        self.add('00099')
        self.assertTrue(self.empty()['success'])
        restarted = PhotoTrash(self.store.originals, self.store.processed, self.store.root)
        self.assertEqual(restarted.highest_reserved_index(), 99)
        self.assertEqual(restarted.list_entries(), [])
        self.assert_active_untouched()

    def test_restore_after_confirmation_rejects_snapshot(self):
        plan = self.store.empty_preview()
        self.store.restore([self.entry])
        with self.assertRaises(TrashError): self.store.empty(plan['snapshot'])
        self.assertTrue((self.store.originals / '00009.jpg').exists())

    def test_changed_file_blocks_entire_batch(self):
        second = self.add('00012')
        self.saved(second)[0].write_bytes(b'replaced')
        with self.assertRaises(TrashError): self.empty()
        self.assertEqual(len(self.saved()), 2)
        self.assertEqual(len(self.saved(second)), 2)

    def test_active_same_id_different_extension_blocks_purge(self):
        (self.store.originals / '00009.jpeg').write_bytes(b'other photo')
        with self.assertRaises(TrashError): self.empty()
        self.assertEqual(len(self.saved()), 2)

    def test_other_hardlink_blocks_purge(self):
        os.link(self.saved()[0], self.base / 'unexpected-link')
        with self.assertRaises(TrashError): self.empty()
        self.assertEqual(len(self.saved()), 2)

    def test_symlink_payload_blocks_purge(self):
        path = self.saved()[0]
        path.unlink()
        path.symlink_to(self.store.originals / '00010.jpg')
        with self.assertRaises(TrashError): self.empty()
        self.assert_active_untouched()

    def test_unknown_nested_file_or_entry_child_blocks_purge(self):
        for path in (self.store.root / self.entry / 'extra', self.store.root / self.entry / 'original' / 'extra'):
            path.write_bytes(b'unknown')
            with self.assertRaises(TrashError): self.empty()
            path.unlink()
        self.assertEqual(len(self.saved()), 2)

    def test_missing_file_requires_existing_durable_purge_intent(self):
        self.saved()[0].unlink()
        with self.assertRaises(TrashError): self.empty()
        self.assertEqual(len(self.saved()), 1)

    def test_uncertain_move_restore_and_corrupt_journal_fail_closed(self):
        manifest = self.store.root / self.entry / 'manifest.json'
        data = json.loads(manifest.read_text())
        for state in ('moving', 'restoring', 'invalid'):
            manifest.write_text(json.dumps(dict(data, state=state)))
            with self.assertRaises(TrashError): self.store.empty_preview()
            self.assertEqual(len(self.saved()), 2)

    def test_failure_before_intent_deletes_nothing(self):
        with patch.object(self.store, '_journal', side_effect=OSError('fixture')):
            result = self.empty()
        self.assertFalse(result['success'])
        self.assertEqual(len(self.saved()), 2)
        self.assertEqual(self.store.list_entries()[0]['state'], 'trashed')

    def test_failure_after_each_unlink_never_autopurges_on_restart(self):
        for failed_unlink in (1, 2):
            with self.subTest(failed_unlink=failed_unlink):
                entry_id = self.add('case-' + str(failed_unlink))
                # Test one newly created entry after finishing the previous group.
                original_unlink = Path.unlink
                calls = 0
                def unlink(path, *args, **kwargs):
                    nonlocal calls
                    result = original_unlink(path, *args, **kwargs)
                    if entry_id in path.parts:
                        calls += 1
                        if calls == failed_unlink: raise OSError('simulated interruption')
                    return result
                with patch.object(Path, 'unlink', unlink): result = self.empty()
                self.assertFalse(result['success'])
                remaining = len(self.saved(entry_id))
                self.store = PhotoTrash(self.store.originals, self.store.processed, self.store.root)
                self.assertEqual(len(self.saved(entry_id)), remaining)
                self.assertEqual(remaining, 2 - failed_unlink)
                self.assertFalse(self.store.restore([entry_id])['success'])
                with self.assertRaises(TrashError): self.store.preview(entry_id)
                self.assertTrue(self.empty()['success'])
                self.assertEqual(self.saved(entry_id), [])
                self.assert_active_untouched()

    def test_failure_final_journal_keeps_resumable_tombstone(self):
        original = self.store._journal
        def journal(entry):
            if entry['state'] == 'purged': raise OSError('simulated interruption')
            return original(entry)
        with patch.object(self.store, '_journal', journal): result = self.empty()
        self.assertFalse(result['success'])
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.store.list_entries()[0]['state'], 'purging')
        self.assertTrue(self.empty()['success'])
        self.assert_active_untouched()

    def test_more_than_100_entries_are_in_confirmed_empty_snapshot(self):
        for index in range(100): self.add('bulk-' + str(index))
        plan = self.store.empty_preview()
        self.assertEqual(plan['total'], 101)
        self.assertEqual(len(self.store.empty(plan['snapshot'])['completed']), 101)
        self.assert_active_untouched()

    def test_storage_uses_photo_filesystem(self):
        from collections import namedtuple
        usage = namedtuple('Usage', 'total used free')(1000, 600, 300)
        with patch('photo_trash.shutil.disk_usage', return_value=usage) as call:
            self.assertEqual(self.store.storage_status(), {'total_bytes': 1000, 'used_bytes': 600, 'available_bytes': 300})
            call.assert_called_once_with(self.store.originals)


class EmptyTrashApiTests(TrashFixture):
    def test_hardware_confirmation_snapshot_busy_and_success(self):
        lock = threading.Lock()
        busy = False
        app = FastAPI()
        register_photo_trash_routes(app, lambda: self.store, lock, lambda: busy, lambda: None)
        with TestClient(app) as client:
            plan = client.post('/api/trash/empty/preview').json()
            for body in ({}, [], None, {'snapshot': plan['snapshot']}):
                self.assertEqual(client.post('/api/trash/empty', json=body).status_code, 400)
            payload = {'snapshot': plan['snapshot'], 'confirmation': 'empty-trash-permanently'}
            busy = True
            self.assertEqual(client.post('/api/trash/empty', json=payload).status_code, 409)
            busy = False
            lock.acquire()
            try: self.assertEqual(client.post('/api/trash/empty', json=payload).status_code, 409)
            finally: lock.release()
            self.assertEqual(len(self.saved()), 2)
            self.assertTrue(client.post('/api/trash/empty', json=payload).json()['success'])
            self.assertEqual(client.get('/api/storage').status_code, 200)
            self.assert_active_untouched()

    def test_startup_uninitialized_reports_503_before_busy_callback(self):
        def get_store(): raise HTTPException(503, 'Not ready')
        def busy(): raise AssertionError('Must not dereference uninitialized camera')
        app = FastAPI()
        register_photo_trash_routes(app, get_store, threading.Lock(), busy, lambda: None)
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/trash/empty/preview').status_code, 503)

    def test_dashboard_proxies_confirmation_and_excludes_other_jobs(self):
        headers = {'X-Reframe-Action': 'photo-library'}
        payload = {'snapshot': 'a' * 64, 'confirmation': 'empty-trash-permanently'}
        with TestClient(dashboard.app) as client, patch.object(dashboard.reframe_client, 'post', new_callable=AsyncMock) as post:
            post.return_value = {'success': True, 'completed': [], 'failed': []}
            for path in ('/api/trash/empty', '/api/trash/empty/preview'):
                self.assertEqual(client.post(path, json=payload).status_code, 403)
            self.assertEqual(client.post('/api/trash/empty', headers=headers, json={}).status_code, 400)
            post.assert_not_called()
            for job in ('library_mutation_active', 'download_job_active', 'delete_job_active'):
                with patch.object(dashboard, job, True):
                    self.assertEqual(client.post('/api/trash/empty', headers=headers, json=payload).status_code, 409)
            self.assertEqual(client.post('/api/trash/empty', headers=headers, json=payload).status_code, 200)
            post.assert_awaited_once_with('/trash/empty', json=payload)
            self.assertFalse(dashboard.library_mutation_active)
            post.reset_mock()
            self.assertEqual(client.post('/api/trash/empty/preview', headers=headers, json={}).status_code, 200)
            post.assert_awaited_once_with('/trash/empty/preview', json={})

    def test_storage_proxy_errors_and_job_release(self):
        headers = {'X-Reframe-Action': 'photo-library'}
        with TestClient(dashboard.app) as client, patch.object(dashboard.reframe_client, 'get', new_callable=AsyncMock) as get:
            get.return_value = {'total_bytes': 1000, 'used_bytes': 600, 'available_bytes': 300}
            self.assertEqual(client.get('/api/storage').json()['available_bytes'], 300)
            get.assert_awaited_once_with('/storage')
        with TestClient(dashboard.app) as client, patch.object(dashboard.reframe_client, 'post', new_callable=AsyncMock, side_effect=HTTPException(503, 'fixture')):
            self.assertEqual(client.post('/api/trash/empty/preview', headers=headers, json={}).status_code, 503)
            self.assertFalse(dashboard.library_mutation_active)
