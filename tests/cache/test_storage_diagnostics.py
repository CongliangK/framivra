"""Storage diagnostics do no display-only work for pipeline audit totals."""
import contextlib
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._support import temporary_root
from vtx.cache import hygiene


class StorageDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)
        self.ctx = SimpleNamespace(root=self.root, cache=self.root / 'cache',
                                   out=self.root / 'out')

    def test_quiet_totals_skip_display_traversal_and_ledger(self):
        self.ctx.cache.mkdir()
        self.ctx.out.mkdir()
        (self.ctx.cache / 'payload.bin').write_bytes(b'cache')
        (self.ctx.out / 'final.mp4').write_bytes(b'output')
        manifest = self.ctx.cache / 'manifest.json'
        manifest.write_bytes(b'broken manifest')
        # Root-level files need no rglob for dir_size. Any rglob here is the
        # discarded top-20 diagnostic pass, not the required size measurement.
        with mock.patch.object(Path, 'rglob', side_effect=AssertionError('top-20 traversal')), \
                mock.patch.object(hygiene, 'tmp_orphans', side_effect=AssertionError('orphan scan')), \
                mock.patch.object(Path, 'read_text', side_effect=AssertionError('ledger read')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            totals = hygiene.df(self.ctx, quiet=True)
        self.assertEqual(totals, {'cache': 20, 'out': 6, 'total': 26})
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(manifest.read_bytes(), b'broken manifest')

    def test_verbose_top20_matches_stable_full_sort_including_ties(self):
        self.ctx.cache.mkdir()
        for i in range(65):
            (self.ctx.cache / f'item-{i:02d}.bin').write_bytes(b'x' * (i % 9))
        (self.ctx.cache / 'empty-directory').mkdir()
        candidates = list(self.ctx.cache.rglob('*'))
        expected = sorted(candidates,
                          key=lambda p: p.stat().st_size if p.is_file() else 0,
                          reverse=True)[:20]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            totals = hygiene.df(self.ctx)
        ranking = output.getvalue().split('== top-20 大文件（cache）==\n')[1].split('== tmp 孤儿 ==')[0]
        expected_text = ''.join(f'  {hygiene.gb(p.stat().st_size):>10s}  {p.relative_to(self.root)}\n'
                                for p in expected if p.is_file())
        self.assertEqual(ranking, expected_text)
        self.assertEqual(totals, hygiene.df(self.ctx, quiet=True))

    def test_zero_size_directory_ties_and_top20_boundaries(self):
        for count in (0, 4, 20, 25):
            with self.subTest(count=count):
                self.ctx.cache = self.root / f'cache-{count}'
                self.ctx.cache.mkdir()
                for index in range(count):
                    path = self.ctx.cache / f'item-{index}'
                    if index % 2:
                        path.mkdir()
                    else:
                        path.touch()
                expected = list(self.ctx.cache.rglob('*'))[:20]
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    hygiene.df(self.ctx)
                ranking = output.getvalue().split('== top-20 大文件（cache）==\n')[1].split('== tmp 孤儿 ==')[0]
                self.assertEqual(ranking, ''.join(
                    f'  {hygiene.gb(0):>10s}  {p.relative_to(self.root)}\n'
                    for p in expected if p.is_file()))

    def test_missing_layout_remains_zero_write(self):
        with contextlib.redirect_stdout(io.StringIO()):
            for quiet in (False, True):
                self.assertEqual(hygiene.df(self.ctx, quiet=quiet),
                                 {'cache': 0, 'out': 0, 'total': 0})
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
