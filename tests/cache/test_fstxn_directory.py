"""Directory-anchored writes do not require a reopenable proc-fd Path."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vtx.runtime import fstxn


@unittest.skipUnless(fstxn.directory_write_capability(), 'no directory-fd writes')
class DirectoryWrites(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_byte_staging_lock_and_cleanup_without_proc_path(self):
        target = self.root / 'nested' / 'output'
        with patch.object(fstxn, '_FD_WRITE_SUPPORT', False), \
                patch.object(fstxn, 'fd_path', side_effect=AssertionError('proc Path')):
            self.assertEqual(fstxn.write_capability(), 'legacy')
            self.assertTrue(fstxn.directory_write_capability())
            fstxn.mkdir(target.parent, root=self.root)
            fstxn.atomic_write(target, b'old', root=self.root)
            with fstxn.file_lock(self.root / 'operation', root=self.root):
                with fstxn.locked_staging(target, root=self.root) as stage:
                    stage.write_bytes(b'new')
            self.assertEqual(target.read_bytes(), b'new')
            fstxn.replace(target, target.with_name('moved'), root=self.root)
            fstxn.unlink(target.with_name('moved'), root=self.root)
            self.assertFalse(target.exists())

    def test_stream_seek_abort_and_no_proc_reopen(self):
        target = self.root / 'stream'
        target.write_bytes(b'old')
        with patch.object(fstxn, 'fd_path', side_effect=AssertionError('proc Path')):
            with fstxn.locked_stream(target, root=self.root) as stream:
                stream.write(b'abc')
                stream.seek(1)
                stream.write(b'X')
            self.assertTrue(stream.closed)
            self.assertEqual(target.read_bytes(), b'aXc')
            with self.assertRaisesRegex(RuntimeError, 'abort'):
                with fstxn.locked_stream(target, root=self.root) as stream:
                    stream.write(b'incomplete')
                    raise RuntimeError('abort')
        self.assertTrue(stream.closed)
        self.assertEqual(target.read_bytes(), b'aXc')
        self.assertEqual(list(self.root.glob('.*.tmp')), [])

    def test_staging_exchange_refuses_publication(self):
        target = self.root / 'output'
        target.write_bytes(b'old')
        with self.assertRaises(ValueError):
            with fstxn.locked_staging(target, root=self.root) as stage:
                moved = stage.with_name(stage.name + '.moved')
                stage.rename(moved)
                stage.write_bytes(b'decoy')
        self.assertEqual(target.read_bytes(), b'old')

    def test_lock_symlink_and_fifo_refused_without_touching_victim(self):
        victim = self.root / 'victim'
        victim.write_bytes(b'unchanged')
        target = self.root / 'operation'
        lock = self.root / 'operation.lock'
        lock.symlink_to(victim)
        with self.assertRaises(ValueError):
            with fstxn.file_lock(target, root=self.root):
                self.fail('linked lock accepted')
        self.assertEqual(victim.read_bytes(), b'unchanged')
        lock.unlink()
        os.mkfifo(lock)
        with self.assertRaises(ValueError):
            with fstxn.file_lock(target, root=self.root):
                self.fail('FIFO lock accepted')

    def test_byte_write_rechecks_temp_inode(self):
        target = self.root / 'output'
        target.write_bytes(b'old')
        real = os.replace
        calls = []
        def record(*args, **kwargs):
            calls.append(kwargs)
            return real(*args, **kwargs)
        with patch.object(fstxn.os, 'replace', side_effect=record):
            fstxn.atomic_write(target, b'new', root=self.root)
        self.assertEqual(target.read_bytes(), b'new')
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0]['src_dir_fd'], int)
        self.assertEqual(calls[0]['src_dir_fd'], calls[0]['dst_dir_fd'])


if __name__ == '__main__':
    unittest.main()
