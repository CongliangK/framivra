"""Bounded PNG gate scheduling must not change validation or byte identity."""
import hashlib
import io
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import zlib

from tests.gfx._png import chunk, png
from vtx.render import gfx_cli


class PngValidationConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def write(self, i, data):
        path = self.out / f'f{i:04d}.png'
        path.write_bytes(data)
        return path

    def test_mixed_malformed_and_sizing_match_serial_gate_exactly(self):
        header = gfx_cli.PNG_SIGNATURE + chunk(
            b'IHDR', struct.pack('>IIBBBBB', 6, 4, 8, 6, 0, 0, 0))
        fixtures = [png(), b'', b'not png', png()[:-3],
                    png()[:-1] + b'x', png(filter_byte=5), png(w=7),
                    header + chunk(b'IDAT', b'bad zlib') + chunk(b'IEND', b''),
                    header + chunk(b'IDAT', zlib.compress(b'\0')) + chunk(b'IEND', b''),
                    header + chunk(b'IDAT', zlib.compress(b'\0' * 100) + b'x')
                    + chunk(b'IEND', b''), png() + b'extra']
        for i, data in enumerate(fixtures):
            self.write(i, data)
        count = len(fixtures) + 1  # also exercise missing files
        for width, height in ((None, None), (6, 4), (7, 4)):
            expected_bad, hashes = [], []
            for i in range(count):
                got = []
                reason = gfx_cli.png_invalid_reason(
                    self.out / f'f{i:04d}.png', width=width, height=height,
                    _fingerprint=got)
                if reason is not None:
                    expected_bad.append((i, reason))
                hashes.append(got[0] if got else None)
            with patch.object(gfx_cli.os, 'cpu_count', return_value=8):
                self.assertEqual(expected_bad, gfx_cli.verify_frames(
                    self.out, iter(range(count)), width=width, height=height))
                record, bad = gfx_cli._page_record(self.out, count, 'key', width, height)
            self.assertEqual(expected_bad, bad)
            self.assertEqual(hashes, record['sha256'])

    def test_order_duplicates_generator_and_empty(self):
        self.write(2, png())
        sequence = [4, 2, 1, 4, 3, 1]
        expected = [(i, '缺失') for i in sequence if i != 2]
        self.assertEqual(expected, gfx_cli.verify_frames(self.out, (i for i in sequence)))
        self.assertEqual([], gfx_cli.verify_frames(self.out, iter(())))
        record, bad = gfx_cli._page_record(self.out, 0, 'key', 6, 4)
        self.assertEqual([], record['sha256'])
        self.assertEqual([], bad)

    def test_each_call_revalidates_current_bytes_even_same_stat(self):
        first, second = png(value=1), png(value=2)
        self.assertEqual(len(first), len(second))
        paths = [self.write(i, first) for i in range(3)]
        stamp = paths[0].stat()
        old, bad = gfx_cli._page_record(self.out, 3, 'key', 6, 4)
        self.assertEqual([], bad)
        self.assertEqual([], gfx_cli.verify_frames(self.out, range(3)))
        paths[0].write_bytes(second)
        os.utime(paths[0], ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        current, bad = gfx_cli._page_record(self.out, 3, 'key', 6, 4)
        self.assertEqual([], bad)
        self.assertNotEqual(old['sha256'][0], current['sha256'][0])
        self.assertEqual(hashlib.sha256(second).hexdigest(), current['sha256'][0])
        paths[0].write_bytes(b'x' * len(second))
        os.utime(paths[0], ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        expected = gfx_cli.png_invalid_reason(paths[0], width=6, height=4)
        self.assertEqual([(0, expected)], gfx_cli.verify_frames(
            self.out, range(3), width=6, height=4))
        current, bad = gfx_cli._page_record(self.out, 3, 'key', 6, 4)
        self.assertEqual([(0, expected)], bad)
        self.assertIsNone(current['sha256'][0])

    def test_hash_comes_from_validated_stream_not_path_reread(self):
        on_disk, streamed = png(value=1), png(value=2)
        for i in range(3):
            self.write(i, on_disk)
        with patch('builtins.open', side_effect=lambda *a, **kw: io.BytesIO(streamed)):
            record, bad = gfx_cli._page_record(self.out, 3, 'key', 6, 4)
        self.assertEqual([], bad)
        self.assertEqual([hashlib.sha256(streamed).hexdigest()] * 3, record['sha256'])

    def test_cpu_and_tiny_paths_do_not_construct_executor(self):
        for cpus in (None, 1):
            with patch.object(gfx_cli.os, 'cpu_count', return_value=cpus), \
                    patch.object(gfx_cli, 'ThreadPoolExecutor') as executor:
                self.assertEqual(list(range(5)), list(gfx_cli._bounded_ordered_map(
                    lambda i: i, iter(range(5)))))
                executor.assert_not_called()
        with patch.object(gfx_cli.os, 'cpu_count', return_value=64), \
                patch.object(gfx_cli, 'ThreadPoolExecutor') as executor:
            self.assertEqual([], list(gfx_cli._bounded_ordered_map(lambda i: i, iter(()))))
            self.assertEqual([7], list(gfx_cli._bounded_ordered_map(lambda i: i, iter([7]))))
            executor.assert_not_called()

    def test_two_workers_order_finite_submission_and_input_laziness(self):
        pulled, completed, pools = [], [], []
        barrier = threading.Barrier(2)
        odd_finished = [threading.Event() for _ in range(3)]

        class ObservedExecutor(ThreadPoolExecutor):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                pools.append(self)
                self.submissions = 0

            def submit(self, *a, **kw):
                self.submissions += 1
                return super().submit(*a, **kw)

        def inputs():
            for i in range(6):
                pulled.append(i)
                yield i

        def one(i):
            barrier.wait(timeout=5)
            if i % 2 == 0:
                if not odd_finished[i // 2].wait(timeout=5):
                    raise AssertionError('second worker did not finish')
            completed.append(i)
            if i % 2:
                odd_finished[i // 2].set()
            return i

        with patch.object(gfx_cli.os, 'cpu_count', return_value=64), \
                patch.object(gfx_cli, 'ThreadPoolExecutor', ObservedExecutor):
            results = gfx_cli._bounded_ordered_map(one, inputs())
            self.assertEqual([], pulled)
            self.assertEqual(0, next(results))
            self.assertEqual([0, 1], pulled)
            self.assertEqual(2, pools[0].submissions)
            self.assertEqual(1, next(results))
            self.assertEqual([0, 1], pulled)
            self.assertEqual([2, 3, 4, 5], list(results))
        self.assertEqual(2, pools[0]._max_workers)
        self.assertEqual([1, 0, 3, 2, 5, 4], completed)
        self.assertTrue(all(not thread.is_alive() for thread in pools[0]._threads))

    def test_worker_and_input_exceptions_propagate_and_join_workers(self):
        for failure in ('worker', 'input'):
            pools = []

            class ObservedExecutor(ThreadPoolExecutor):
                def __init__(self, *a, **kw):
                    super().__init__(*a, **kw)
                    pools.append(self)

            def inputs():
                yield 0
                yield 1
                if failure == 'input':
                    raise RuntimeError('input exploded')
                yield 2

            def one(i):
                if failure == 'worker' and i == 0:
                    raise RuntimeError('worker exploded')
                return i

            with patch.object(gfx_cli.os, 'cpu_count', return_value=8), \
                    patch.object(gfx_cli, 'ThreadPoolExecutor', ObservedExecutor):
                with self.assertRaisesRegex(RuntimeError, failure + ' exploded'):
                    list(gfx_cli._bounded_ordered_map(one, inputs()))
            self.assertEqual(1, len(pools))
            self.assertTrue(all(not thread.is_alive() for thread in pools[0]._threads))

    def test_public_gates_do_not_swallow_unexpected_validator_errors(self):
        for call in (lambda: gfx_cli.verify_frames(self.out, range(4)),
                     lambda: gfx_cli._page_record(self.out, 4, 'key', 6, 4)):
            with patch.object(gfx_cli.os, 'cpu_count', return_value=8), \
                    patch.object(gfx_cli, 'png_invalid_reason',
                                 side_effect=RuntimeError('validator exploded')):
                with self.assertRaisesRegex(RuntimeError, 'validator exploded'):
                    call()

    def test_consumer_close_joins_workers(self):
        pools = []

        class ObservedExecutor(ThreadPoolExecutor):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                pools.append(self)

        with patch.object(gfx_cli.os, 'cpu_count', return_value=8), \
                patch.object(gfx_cli, 'ThreadPoolExecutor', ObservedExecutor):
            results = gfx_cli._bounded_ordered_map(lambda i: i, range(10))
            self.assertEqual(0, next(results))
            results.close()
        self.assertTrue(all(not thread.is_alive() for thread in pools[0]._threads))


if __name__ == '__main__':
    unittest.main()
