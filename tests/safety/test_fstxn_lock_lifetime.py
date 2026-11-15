"""Per-path Python locks retire only after their final owner/waiter releases them."""
import gc
import threading
import unittest
import weakref

from tests._support import temporary_root
from vtx.runtime import fstxn


class LockLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)

    def test_unique_idle_paths_do_not_accumulate(self):
        refs = []
        keys = []
        for index in range(2000):
            path = self.root / f'frame-{index}.png'
            lock = fstxn._thread_lock_for(path)
            refs.append(weakref.ref(lock))
            keys.append(str(path.absolute()))
        del lock
        gc.collect()
        self.assertTrue(all(ref() is None for ref in refs))
        self.assertTrue(all(key not in fstxn._LOCKS for key in keys))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_staging_paths_retire_after_public_transaction(self):
        for index in range(20):
            path = self.root / f'frame-{index}.png'
            with fstxn.locked_staging(path) as staging:
                reference = weakref.ref(fstxn._thread_lock_for(path))
                self.assertTrue(reference().locked())
                staging.write_bytes(b'payload')
            gc.collect()
            self.assertIsNone(reference())
            self.assertEqual(path.read_bytes(), b'payload')
            self.assertTrue(path.with_name(path.name + '.lock').is_file())

    def test_waiter_keeps_same_domain_after_owner_drops_reference(self):
        path = self.root / 'shared.png'
        owner = fstxn._thread_lock_for(path)
        reference = weakref.ref(owner)
        owner.acquire()
        waiting, entered, finish = (threading.Event() for _ in range(3))
        errors = []

        def waiter():
            try:
                lock = fstxn._thread_lock_for(path)
                self.assertIs(lock, reference())
                waiting.set()
                with lock:
                    entered.set()
                    self.assertTrue(finish.wait(5))
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=waiter)
        thread.start()
        try:
            self.assertTrue(waiting.wait(5))
            gc.collect()
            self.assertIs(fstxn._thread_lock_for(path), owner)
            self.assertFalse(entered.is_set())
            owner.release()
            del owner
            self.assertTrue(entered.wait(5))
            gc.collect()
            # Only the waiter owns the original lock now; a third caller must
            # still see it and cannot enter the active critical section.
            third = fstxn._thread_lock_for(path)
            self.assertIs(third, reference())
            acquired = third.acquire(blocking=False)
            if acquired:
                third.release()
            self.assertFalse(acquired)
            del third
        finally:
            if 'owner' in locals():
                owner.release()
                del owner
            finish.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        gc.collect()
        self.assertIsNone(reference())
        self.assertNotIn(str(path.absolute()), fstxn._LOCKS)

    def test_exception_releases_context_and_preserves_sidecar(self):
        path = self.root / 'ledger.json'
        with self.assertRaisesRegex(RuntimeError, 'abort'):
            with fstxn.file_lock(path):
                reference = weakref.ref(fstxn._thread_lock_for(path))
                raise RuntimeError('abort')
        gc.collect()
        self.assertIsNone(reference())
        self.assertTrue(path.with_name(path.name + '.lock').is_file())
        with fstxn.file_lock(path):
            self.assertTrue(fstxn._thread_lock_for(path).locked())


if __name__ == '__main__':
    unittest.main()
