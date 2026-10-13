"""Issue #32: pinned inputs, completion gates, lock waits and recovery.

All products are synthetic and isolated in TemporaryDirectory; no media tools.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vtx import cache_state, fstxn, hygiene, stages
from vtx.infra import Ctx


class InputIdentityGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'timeline.json'
        (self.root / 'media').write_bytes(b'old source')
        (self.root / 'page.html').write_text('old html')
        (self.root / 'style.css').write_text('old style')
        self.tl = dict(project=dict(fps=30, width=640, height=360,
                                   duration_frames=30,
                                   cache_dependencies=['style.css']),
                       sources=[dict(id='s', path='media')],
                       video_tracks=[dict(kind='gfx', clips=[
                           dict(src='page.html', dst_in=0, dur=30)])],
                       audio_tracks=[], subtitles=[])
        self.path.write_text(json.dumps(self.tl))
        self.c = Ctx(self.path)
        self.product = self.c.out / 'final.mp4'
        self.product.parent.mkdir()
        self.product.write_bytes(b'old product')

    def mutate(self, name):
        p = self.root / name
        p.write_bytes(p.read_bytes() + b' ')

    def rejects(self, fn):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as raised:
                fn()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn('issue #32', err.getvalue())

    def test_unchanged_records_use_pinned_identity(self):
        self.c.receipt('validate', True)
        hygiene.manifest_record(self.c, self.product, 'video')
        hygiene.versions_record(self.c, self.product, 'mux')
        receipt = json.loads((self.c.out / 'receipt.json').read_text())
        self.assertEqual(receipt['timeline'], self.c.timeline_sha256)
        self.assertEqual(receipt['input_fingerprint'], self.c.input_fingerprint)
        manifest = json.loads((self.c.cache / 'manifest.json').read_text())
        versions = json.loads((self.c.out / 'VERSIONS.json').read_text())
        self.assertEqual(manifest['entries'][0]['tl'], self.c.timeline_md5)
        self.assertEqual(versions['versions'][0]['timeline_md5'], self.c.timeline_md5)

    def test_every_tracked_input_blocks_all_success_gates(self):
        for name in ('timeline.json', 'media', 'page.html', 'style.css'):
            with self.subTest(name=name):
                p = self.root / name
                original = p.read_bytes()
                self.mutate(name)
                self.rejects(lambda: self.c.receipt('video', True))
                self.rejects(lambda: hygiene.manifest_record(
                    self.c, self.product, 'video'))
                self.rejects(lambda: hygiene.versions_record(
                    self.c, self.product, 'mux'))
                self.assertFalse((self.c.cache / 'manifest.json').exists())
                self.assertFalse((self.c.out / 'VERSIONS.json').exists())
                rec = json.loads((self.c.out / 'receipt.json').read_text())
                self.assertFalse(rec['stages']['video']['ok'])
                self.assertEqual(rec['timeline'], self.c.timeline_sha256)
                p.write_bytes(original)

    def test_change_while_waiting_for_ledger_lock_is_detected(self):
        for gate, loader, target in (
            (lambda: self.c.receipt('video', True), 'receipt', self.c.out / 'receipt.json'),
            (lambda: hygiene.manifest_record(self.c, self.product, 'video'),
             '_load_manifest', self.c.cache / 'manifest.json'),
            (lambda: hygiene.versions_record(self.c, self.product, 'mux'),
             '_load_versions', self.c.out / 'VERSIONS.json'),
        ):
            with self.subTest(loader=loader):
                original = self.path.read_bytes()
                if loader == 'receipt':
                    original_load = cache_state.load_receipt
                    calls = 0
                    def load(p, fp):
                        nonlocal calls
                        value = original_load(p, fp)
                        calls += 1
                        if calls == 1:
                            self.mutate('timeline.json')
                        return value
                    # Only change once: failure receipt must be free to commit.
                    with mock.patch('vtx.infra.load_receipt', side_effect=load):
                        self.rejects(gate)
                else:
                    original_load = getattr(hygiene, loader)
                    def load(p):
                        value = original_load(p)
                        self.mutate('timeline.json')
                        return value
                    with mock.patch.object(hygiene, loader, load):
                        self.rejects(gate)
                    self.assertFalse(target.exists())
                self.path.write_bytes(original)

    def test_missing_dependency_fails_closed(self):
        (self.root / 'style.css').unlink()
        self.rejects(lambda: self.c.receipt('audio', True))
        self.c.receipt('validate', False, 'ordinary failure')

    def test_stage_start_refuses_stale_context_without_writes(self):
        self.mutate('timeline.json')
        with self.assertRaises(cache_state.InputIdentityError):
            self.c.begin_stage('prepare')
        self.assertFalse((self.c.out / 'receipt.json').exists())

    def test_prepare_source_swap_and_fresh_context_recovery(self):
        def extract(c, tasks, what):
            for _, cmd in tasks:
                Path(cmd[-1]).write_bytes(b'extracted')
        def swap(c, tasks, what):
            extract(c, tasks, what)
            self.mutate('media')
        with mock.patch.object(stages, 'run_parallel', swap):
            self.rejects(lambda: stages.prepare(self.c))
        fresh = Ctx(self.path)
        self.assertNotEqual(fresh.input_fingerprint, self.c.input_fingerprint)
        self.assertEqual(cache_state.load_receipt(
            self.c.out / 'receipt.json', fresh.input_fingerprint)['stages'], {})
        with mock.patch.object(stages, 'run_parallel', extract):
            fresh.begin_stage('prepare')
            stages.prepare(fresh)
        rec = json.loads((fresh.out / 'receipt.json').read_text())
        self.assertTrue(rec['stages']['prepare']['ok'])
        self.assertEqual(rec['input_fingerprint'], fresh.input_fingerprint)

    def test_constructor_does_not_hash_a_different_timeline_read(self):
        real_read = Path.read_bytes
        first = True
        def read(p):
            nonlocal first
            data = real_read(p)
            if p == self.path and first:
                first = False
                self.path.write_bytes(data + b' ')
            return data
        with mock.patch.object(Path, 'read_bytes', read):
            with self.assertRaises(cache_state.InputIdentityError):
                Ctx(self.path)

    def test_preview_failure_does_not_write_formal_receipt(self):
        self.mutate('timeline.json')
        self.rejects(lambda: self.c.verify_inputs(None))
        self.assertFalse((self.c.out / 'receipt.json').exists())

    def test_subtitles_changed_during_resolution_are_rejected(self):
        from vtx import subtitles_src
        self.tl['subtitles_src'] = 'subtitles.json'
        self.tl.pop('subtitles')
        self.path.write_text(json.dumps(self.tl))
        (self.root / 'subtitles.json').write_text('[]')
        real_resolve = subtitles_src.resolve_subtitles_src
        def resolve(c):
            real_resolve(c)
            self.mutate('subtitles.json')
        with mock.patch.object(subtitles_src, 'resolve_subtitles_src', resolve):
            with self.assertRaises(cache_state.InputIdentityError):
                Ctx(self.path)
        self.assertFalse((self.c.out / 'receipt.json').exists())

    def test_dry_gates_do_not_create_ledgers_or_locks(self):
        self.c.dry = True
        self.mutate('timeline.json')
        with mock.patch.object(fstxn, 'locked_update', side_effect=AssertionError):
            self.c.begin_stage('prepare')
            self.c.receipt('video', True)
            hygiene.manifest_record(self.c, self.product, 'video')
            hygiene.versions_record(self.c, self.product, 'mux')
        self.assertFalse(self.c.cache.exists())
        self.assertEqual(list(self.c.out.iterdir()), [self.product])


if __name__ == '__main__':
    unittest.main()
