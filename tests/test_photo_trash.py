"""Disposable fixtures only: no camera, photos, network, or GPIO required."""

import ast
import copy
import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import photo_trash
from photo_trash import PhotoTrash, TrashError, PendingPhotoSaves, validate_ids


class PhotoTrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.originals = self.base / 'photos'
        self.processed = self.base / 'dithered_photos'
        self.root = self.base / '.photo-trash'
        self.store = PhotoTrash(self.originals, self.processed, self.root)

    def tearDown(self):
        self.temp.cleanup()

    def pair(self, photo_id='00001', derivative=True, ext='.jpg'):
        (self.originals / (photo_id + ext)).write_bytes(b'original-fixture-' + photo_id.encode())
        if derivative:
            (self.processed / (photo_id + '_dithered.png')).write_bytes(b'dithered-fixture-' + photo_id.encode())

    def snapshot(self):
        return {str(p.relative_to(self.base)): hashlib.sha256(p.read_bytes()).hexdigest()
                for directory in (self.originals, self.processed) for p in directory.iterdir() if p.is_file()}

    def restart(self):
        self.store = PhotoTrash(self.originals, self.processed, self.root)

    def test_bulk_move_restore_preserves_bytes_and_unselected(self):
        for i in range(4): self.pair(str(i).zfill(5))
        before = self.snapshot()
        moved = self.store.move_to_trash(['00001', '00003'])
        self.assertTrue(moved['success'])
        self.assertEqual(len(self.snapshot()), 4)
        self.assertTrue((self.originals / '00000.jpg').exists())
        self.restart()
        self.assertEqual(len(self.store.list_entries()), 2)
        self.assertEqual(self.store.highest_reserved_index(), 3)
        restored = self.store.restore([e['id'] for e in moved['completed']])
        self.assertTrue(restored['success'])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.store.list_entries(), [])

    def test_missing_derivative_and_all_supported_variants(self):
        self.pair(derivative=False, ext='.JPEG')
        (self.processed / '00001.jpg').write_bytes(b'legacy')
        (self.processed / '00001_dithered.jpg').write_bytes(b'legacy dithered')
        before = self.snapshot()
        result = self.store.move_to_trash(['00001'])
        self.assertEqual(self.snapshot(), {})
        self.assertTrue(self.store.restore([result['completed'][0]['id']])['success'])
        self.assertEqual(before, self.snapshot())

    def test_invalid_ids_do_not_touch_files(self):
        self.pair()
        before = self.snapshot()
        for ids in ([], None, '00001', ['../00001'], ['/tmp/a'], ['..'], ['a/b'], ['a\\b'], [1], ['%2e%2e'], ['x'] * 101):
            with self.subTest(ids=ids), self.assertRaises(TrashError):
                self.store.move_to_trash(ids)
        self.assertEqual(before, self.snapshot())

    def test_duplicate_and_network_retry_are_idempotent(self):
        self.pair()
        first = self.store.move_to_trash(['00001', '00001'])
        second = self.store.move_to_trash(['00001'])
        self.assertEqual(first['completed'], second['completed'])
        ids = [e['id'] for e in first['completed']]
        self.assertTrue(self.store.restore(ids)['success'])
        self.assertTrue(self.store.restore(ids)['success'])
        self.assertEqual(len(self.snapshot()), 2)

    def test_partial_batch_is_reported(self):
        self.pair()
        result = self.store.move_to_trash(['00001', 'missing'])
        self.assertFalse(result['success'])
        self.assertEqual(len(result['completed']), 1)
        self.assertEqual(result['failed'][0]['id'], 'missing')

    def test_collision_does_not_move_any_of_pair(self):
        self.pair()
        entry_id = self.store.move_to_trash(['00001'])['completed'][0]['id']
        (self.processed / '00001_dithered.png').write_bytes(b'unrelated-new-image')
        result = self.store.restore([entry_id])
        self.assertFalse(result['success'])
        self.assertFalse((self.originals / '00001.jpg').exists())
        self.assertEqual((self.processed / '00001_dithered.png').read_bytes(), b'unrelated-new-image')
        self.assertTrue((self.root / entry_id / 'original' / '00001.jpg').exists())

    def test_symlink_source_and_destination_rejected(self):
        outside = self.base / 'unrelated'
        outside.write_bytes(b'do-not-touch')
        (self.originals / '00001.jpg').symlink_to(outside)
        self.assertFalse(self.store.move_to_trash(['00001'])['success'])
        (self.originals / '00001.jpg').unlink()
        self.pair()
        entry_id = self.store.move_to_trash(['00001'])['completed'][0]['id']
        (self.originals / '00001.jpg').symlink_to(outside)
        self.assertFalse(self.store.restore([entry_id])['success'])
        self.assertEqual(outside.read_bytes(), b'do-not-touch')

    def test_same_id_different_extension_blocks_restore(self):
        self.pair()
        entry_id = self.store.move_to_trash(['00001'])['completed'][0]['id']
        (self.originals / '00001.png').write_bytes(b'new-photo')
        self.assertFalse(self.store.restore([entry_id])['success'])
        self.assertFalse((self.originals / '00001.jpg').exists())

    def test_symlink_trash_directory_rejected(self):
        self.root.rmdir()
        self.root.symlink_to(self.originals, target_is_directory=True)
        with self.assertRaises(TrashError): self.restart()

    def test_partial_file_failure_compensates(self):
        self.pair()
        before = self.snapshot()
        real_move = photo_trash._move
        count = 0
        def fail_once(source, destination):
            nonlocal count
            count += 1
            if count == 2: raise OSError('fixture failure')
            return real_move(source, destination)
        with patch('photo_trash._move', side_effect=fail_once):
            result = self.store.move_to_trash(['00001'])
        self.assertFalse(result['success'])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.store.list_entries(), [])

    def test_crash_after_each_link_unlink_and_journal_recovers(self):
        class Crash(BaseException): pass
        # Interrupted before/after each of the two file moves, or final commit.
        for stop in range(1, 7):
            with self.subTest(stop=stop):
                folder = self.base / str(stop)
                store = PhotoTrash(folder / 'photos', folder / 'dithered_photos', folder / '.photo-trash')
                (store.originals / '00007.jpg').write_bytes(b'original')
                (store.processed / '00007_dithered.png').write_bytes(b'dithered')
                real_link, real_unlink, real_journal = os.link, Path.unlink, store._journal
                count = 0
                def checkpoint():
                    nonlocal count
                    count += 1
                    if count == stop: raise Crash()
                def link(*args, **kwargs):
                    real_link(*args, **kwargs); checkpoint()
                def unlink(path, *args, **kwargs):
                    real_unlink(path, *args, **kwargs); checkpoint()
                def journal(entry):
                    real_journal(entry); checkpoint()
                with patch('photo_trash.os.link', side_effect=link), patch.object(Path, 'unlink', unlink), patch.object(store, '_journal', journal):
                    with self.assertRaises(Crash): store.move_to_trash(['00007'])
                recovered = PhotoTrash(store.originals, store.processed, store.root)
                entries = recovered.list_entries()
                self.assertEqual(entries[0]['state'], 'trashed')
                self.assertTrue(recovered.restore([entries[0]['id']])['success'])
                self.assertEqual((store.originals / '00007.jpg').read_bytes(), b'original')
                self.assertEqual((store.processed / '00007_dithered.png').read_bytes(), b'dithered')

    def test_crash_during_restore_recovers(self):
        self.pair()
        before = self.snapshot()
        entry_id = self.store.move_to_trash(['00001'])['completed'][0]['id']
        real_move = photo_trash._move
        def crash(source, destination):
            real_move(source, destination)
            raise KeyboardInterrupt()
        with patch('photo_trash._move', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt): self.store.restore([entry_id])
        self.restart()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.store.list_entries(), [])

    def test_replaced_source_fails_closed_and_preview_skips_it(self):
        self.pair(derivative=False)
        entry_id = self.store.move_to_trash(['00001'])['completed'][0]['id']
        saved = self.root / entry_id / 'original' / '00001.jpg'
        saved.write_bytes(b'replaced')
        self.assertFalse(self.store.restore([entry_id])['success'])
        self.assertFalse((self.originals / '00001.jpg').exists())
        with self.assertRaises(TrashError): self.store.preview(entry_id)

    def test_manifest_validation_rejects_traversal(self):
        self.pair()
        entry_id = self.store.move_to_trash(['00001'])['completed'][0]['id']
        manifest = self.root / entry_id / 'manifest.json'
        data = json.loads(manifest.read_text())
        data['files'][0]['name'] = '../../unrelated.jpg'
        manifest.write_text(json.dumps(data))
        with self.assertRaises(TrashError): self.restart()

    def test_private_backup_copy_still_restores(self):
        self.pair()
        entry_id = self.store.move_to_trash(['00001'])['completed'][0]['id']
        backup = self.base / 'private-backup'
        shutil.copytree(self.root, backup)
        copied_store = PhotoTrash(self.base / 'recovered-photos', self.base / 'recovered-dithered', backup)
        self.assertTrue(copied_store.restore([entry_id])['success'])

    def test_file_manager_allocator_reserves_trashed_highest_id(self):
        # Load only this hardware-independent class, not picamera2 or GPIO.
        source = Path(__file__).resolve().parents[1] / 'reframe.py'
        tree = ast.parse(source.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'FileManager')
        namespace = dict(os=os, threading=threading, PhotoTrash=PhotoTrash, PendingPhotoSaves=PendingPhotoSaves)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        self.pair('00099')
        self.store.move_to_trash(['00099'])
        manager = namespace['FileManager'](str(self.originals), str(self.processed))
        self.assertEqual(Path(manager.get_new_file_path(str(self.originals), 'jpg')).name, '00100.jpg')


class PendingSavesTests(unittest.TestCase):
    def test_overlapping_saves_and_failure_release(self):
        saves = PendingPhotoSaves()
        first, second = threading.Event(), threading.Event()
        a = saves.start(lambda: first.wait(2))
        b = saves.start(lambda: second.wait(2))
        self.assertTrue(saves.busy())
        first.set(); a.join(2)
        self.assertTrue(saves.busy())
        second.set(); b.join(2)
        self.assertFalse(saves.busy())

    def test_thread_construction_and_start_failure_release(self):
        for target in ('photo_trash.threading.Thread', 'photo_trash.threading.Thread.start'):
            saves = PendingPhotoSaves()
            with patch(target, side_effect=RuntimeError('fixture')):
                with self.assertRaises(RuntimeError): saves.start(lambda: None)
            self.assertFalse(saves.busy())

    def test_worker_exception_releases(self):
        saves = PendingPhotoSaves()
        def fail(): raise RuntimeError('fixture')
        with patch('threading.excepthook'):
            worker = saves.start(fail)
            worker.join(2)
        self.assertFalse(saves.busy())


if __name__ == '__main__': unittest.main()
