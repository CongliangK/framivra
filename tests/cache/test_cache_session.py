"""Cache working-set lifecycle; synthetic storage, not HTML render acceptance."""
from contextlib import ExitStack
import hashlib
import json
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vtx.cache.policy import CachePolicy
from vtx.cache.session import CacheSession, compact_graphics, hydrate_graphics, pack_path
from vtx.runtime import fstxn
from tests.cache.test_gfx_pack import png


class CacheSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.temporary.name)
        self.timeline = self.root / 'timeline.json'
        self.tl = {'project': {}, 'sources': [], 'video_tracks': []}
        self.timeline.write_text(json.dumps(self.tl))
        # This lifecycle fixture has no genuine Ctx/input pin. Authentication is
        # independently covered by reader/backend tests, not counterfeited here.
        self.identity = self.stack.enter_context(patch('vtx.cache.session.verify_input_identity'))
        self.manifest = self.stack.enter_context(patch('vtx.cache.hygiene.manifest_record'))

    def context(self, mode):
        return SimpleNamespace(root=self.root, cache=self.root / 'cache', out=self.root / 'out',
                               tl_path=self.timeline, tl=self.tl, dry=False, cache_policy=CachePolicy(mode),
                               verify_inputs=lambda stage: None, timeline_md5='fixture', w=32, h=32, fps=30)

    def test_off_and_core_private_media_cleanup_on_success_and_failure(self):
        for mode in ('off', 'core'):
            for fail in (False, True):
                with self.subTest(mode=mode, fail=fail):
                    c = self.context(mode)
                    old = c.cache / 'audio' / 'existing.wav'
                    old.parent.mkdir(parents=True, exist_ok=True)
                    old.write_bytes(b'old-cache-not-an-input')
                    original = c.cache
                    try:
                        with CacheSession(c):
                            self.assertNotEqual(c.cache, original)
                            self.assertEqual(c.gfx_cache, original if mode == 'core' else c.cache)
                            self.assertEqual(c.pass_cache, 'none')
                            artifact = c.cache / 'fresh.wav'
                            artifact.write_bytes(b'new-scratch')
                            self.assertFalse((c.cache / 'audio' / 'existing.wav').exists())
                            if fail:
                                raise RuntimeError('producer failed')
                    except RuntimeError as error:
                        self.assertEqual(str(error), 'producer failed')
                    self.assertEqual(c.cache, original)
                    self.assertFalse(hasattr(c, 'cache_session'))
                    self.assertFalse(artifact.exists())
                    self.assertEqual(old.read_bytes(), b'old-cache-not-an-input')

    def test_aggressive_reuses_original_media_and_retains_all_passes(self):
        c = self.context('aggressive')
        original = c.cache
        with CacheSession(c):
            self.assertEqual(c.cache, original)
            self.assertEqual(c.gfx_cache, original)
            self.assertEqual(c.pass_cache, 'all')
            artifact = c.cache / 'fresh.wav'
            artifact.write_bytes(b'persist')
        self.assertEqual(artifact.read_bytes(), b'persist')

    def test_dry_session_does_not_create_layout(self):
        c = self.context('core')
        c.dry = True
        before = sorted(self.root.rglob('*'))
        with CacheSession(c):
            self.assertFalse(hasattr(c, 'cache_session'))
        self.assertEqual(sorted(self.root.rglob('*')), before)

    def test_off_never_hydrates_existing_graphics(self):
        c = self.context('off')
        with patch('vtx.render.gfx_pack.inspect_pack', side_effect=AssertionError('old cache read')):
            hydrate_graphics(c)

    @unittest.skipUnless(fstxn.write_capability() == 'fd-anchored', 'compact backend requires fd-anchored I/O')
    def test_compact_restore_and_interrupted_restore_keep_original_bytes_and_locks(self):
        c = self.context('core')
        clip = {'src': 'fixture.html', 'dur': 3}
        (self.root / clip['src']).write_text('fixture, not rendered')
        c.tl['video_tracks'] = [{'kind': 'gfx', 'clips': [clip]}]
        self.timeline.write_text(json.dumps(c.tl))
        directory = c.cache / 'gfx' / 'fixture-key'
        directory.mkdir(parents=True)
        data = [png(i) for i in range(3)]
        paths = [directory / f'f{i:04d}.png' for i in range(3)]
        for path, raw in zip(paths, data):
            path.write_bytes(raw)
        record = {'schema': 1, 'key': directory.name, 'frames': 3, 'width': 32, 'height': 32,
                  'sha256': [hashlib.sha256(raw).hexdigest() for raw in data],
                  'environment': {'status': 'input-bound', 'fixture': True}}
        marker = json.dumps(record, indent=2).encode()
        (directory / 'complete.json').write_bytes(marker)
        lock = directory / 'f0000.png.lock'
        lock.touch()
        lock_inode = lock.stat().st_ino
        with patch('vtx.cache.hygiene.gfx_key', return_value=directory.name), \
             patch('vtx.cache.hygiene.gfx_cache_ns', return_value=''), \
             patch('vtx.render.gfx_cache_reader.resolve_frames', return_value=paths):
            compact_graphics(c)
            self.assertTrue(pack_path(c, clip).is_file())
            self.assertFalse((directory / 'complete.json').exists())
            self.assertTrue(all(not path.exists() for path in paths))
            # Simulate interruption after restoring a frame but before committing
            # the producer marker. Its next use must recover from the valid pack.
            paths[0].write_bytes(data[0])
            hydrate_graphics(c)
            self.assertEqual((directory / 'complete.json').read_bytes(), marker)
            self.assertEqual([p.read_bytes() for p in paths], data)
            self.assertEqual(lock.stat().st_ino, lock_inode)

    def page(self, *, key='fixture-key', electron=False):
        c = self.context('core')
        clip = {'src': 'fixture.html', 'dur': 3}
        (self.root / clip['src']).write_text('synthetic fixture; not rendered')
        c.tl['video_tracks'] = [{'kind': 'gfx', 'clips': [clip]}]
        self.timeline.write_text(json.dumps(c.tl))
        directory = c.cache / 'gfx' / key
        directory.mkdir(parents=True, exist_ok=True)
        data = [png(i) for i in range(3)]
        paths = [directory / f'f{i:04d}.png' for i in range(3)]
        for path, raw in zip(paths, data):
            path.write_bytes(raw)
        record = {'schema': 1, 'key': key, 'frames': 3, 'width': 32, 'height': 32,
                  'sha256': [hashlib.sha256(raw).hexdigest() for raw in data],
                  'environment': {'status': 'input-bound', 'fixture': True}}
        if electron:
            record['backend_identity'] = 'opaque-fixture-only'
            record['render_runs'] = [{'run_id': 'a' * 32, 'frames': [0, 1, 2]}]
            evidence = directory / '.electron' / ('a' * 32)
            evidence.mkdir(parents=True)
            for name in ('request.json', 'adapter-result.json', 'result.json'):
                (evidence / name).write_bytes(b'{"synthetic_opaque_receipt": true}\n')
        marker = json.dumps(record, indent=2).encode() + b'\n'
        (directory / 'complete.json').write_bytes(marker)
        self.stack.enter_context(patch('vtx.cache.hygiene.gfx_key', return_value=key))
        self.stack.enter_context(patch('vtx.cache.hygiene.gfx_cache_ns', return_value=''))
        def ready(*args, **kwargs):
            try:
                if json.loads((directory / 'complete.json').read_bytes()) != record:
                    raise ValueError('synthetic loose marker mismatch')
                if [path.read_bytes() for path in paths] != data:
                    raise ValueError('synthetic loose PNG mismatch')
            except (OSError, ValueError) as error:
                raise ValueError('synthetic loose page is incomplete') from error
            return paths
        self.stack.enter_context(patch('vtx.render.gfx_cache_reader.resolve_frames', side_effect=ready))
        return c, clip, directory, paths, data, marker

    @unittest.skipUnless(fstxn.write_capability() == 'fd-anchored', 'requires safe packed storage')
    def test_same_record_warm_core_verifies_without_recompressing(self):
        c, clip, directory, paths, data, marker = self.page()
        compact_graphics(c)
        archive = pack_path(c, clip)
        original = (archive.read_bytes(), archive.stat().st_mtime_ns, archive.stat().st_ino)
        hydrate_graphics(c)
        from vtx.render.gfx_pack import PagePack
        with patch('vtx.render.gfx_pack.pack_page', side_effect=AssertionError('warm recompression')), \
             patch.object(PagePack, 'verify', autospec=True, side_effect=PagePack.verify) as verify:
            compact_graphics(c)
        self.assertGreaterEqual(verify.call_count, 1)
        self.assertEqual((archive.read_bytes(), archive.stat().st_mtime_ns, archive.stat().st_ino), original)
        self.assertFalse((directory / 'complete.json').exists())
        # Range previews may hydrate/retire working PNGs but must leave the
        # formal manifest untouched, including on a warm package cycle.
        c.range_window = (0, 1)
        hydrate_graphics(c)
        self.manifest.reset_mock()
        compact_graphics(c)
        self.manifest.assert_not_called()

    @unittest.skipUnless(fstxn.write_capability() == 'fd-anchored', 'requires safe packed storage')
    def test_bad_loose_marker_or_partial_frame_restores_exact_original(self):
        c, clip, directory, paths, data, marker = self.page()
        compact_graphics(c)
        paths[0].write_bytes(b'incomplete frame')
        (directory / 'complete.json').write_bytes(b'{broken marker')
        hydrate_graphics(c)
        self.assertEqual((directory / 'complete.json').read_bytes(), marker)
        self.assertEqual([p.read_bytes() for p in paths], data)

    @unittest.skipUnless(fstxn.write_capability() == 'fd-anchored', 'requires safe packed storage')
    def test_bad_pack_refused_even_with_valid_bare_page_without_payload_loss(self):
        c, clip, directory, paths, data, marker = self.page()
        compact_graphics(c)
        hydrate_graphics(c)
        archive = pack_path(c, clip)
        raw = archive.read_bytes()
        archive.write_bytes(raw[:12] + bytes([raw[12] ^ 1]) + raw[13:])
        from vtx.render.gfx_pack import GfxPackError
        for operation in (hydrate_graphics, compact_graphics):
            with self.subTest(operation=operation.__name__), self.assertRaises(GfxPackError):
                operation(c)
            self.assertEqual((directory / 'complete.json').read_bytes(), marker)
            self.assertEqual([p.read_bytes() for p in paths], data)

    @unittest.skipUnless(fstxn.write_capability() == 'fd-anchored', 'requires safe packed storage')
    def test_unknown_author_and_explicit_source_block_whole_retirement(self):
        for kind in ('author', 'source'):
            with self.subTest(kind=kind):
                c, clip, directory, paths, data, marker = self.page(key=kind)
                if kind == 'author':
                    author = directory / 'notes.md'
                    author.write_bytes(b'author must survive')
                else:
                    c.tl['sources'] = [{'path': str(paths[0].relative_to(self.root))}]
                    self.timeline.write_text(json.dumps(c.tl))
                with patch('sys.stderr', new=io.StringIO()) as warning:
                    compact_graphics(c)
                self.assertIn('retained', warning.getvalue())
                self.assertEqual((directory / 'complete.json').read_bytes(), marker)
                self.assertEqual([p.read_bytes() for p in paths], data)
                if kind == 'author':
                    self.assertEqual(author.read_bytes(), b'author must survive')

    @unittest.skipUnless(fstxn.write_capability() == 'fd-anchored', 'requires safe packed storage')
    def test_interrupted_retirement_revokes_marker_first_then_hydrates(self):
        c, clip, directory, paths, data, marker = self.page()
        remove = fstxn.remove_pinned
        def interrupt(path, *args, **kwargs):
            if Path(path) == paths[0]:
                raise OSError('injected retirement interruption')
            return remove(path, *args, **kwargs)
        with patch.object(fstxn, 'remove_pinned', side_effect=interrupt), self.assertRaises(OSError):
            compact_graphics(c)
        self.assertFalse((directory / 'complete.json').exists())
        self.assertTrue(pack_path(c, clip).exists())
        hydrate_graphics(c)
        self.assertEqual((directory / 'complete.json').read_bytes(), marker)
        self.assertEqual([p.read_bytes() for p in paths], data)

    @unittest.skipUnless(fstxn.write_capability() == 'fd-anchored', 'requires safe packed storage')
    def test_electron_empty_evidence_dirs_and_nested_lock_survive_hydration(self):
        c, clip, directory, paths, data, marker = self.page(electron=True)
        evidence = directory / '.electron' / ('a' * 32)
        lock = evidence / 'result.json.lock'
        lock.touch()
        inode = lock.stat().st_ino
        compact_graphics(c)
        self.assertEqual(list(evidence.iterdir()), [lock])
        hydrate_graphics(c)
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual((directory / 'complete.json').read_bytes(), marker)
        self.assertEqual([p.read_bytes() for p in paths], data)
        self.assertTrue((evidence / 'result.json').exists())

    def test_context_restores_overrides_and_cleans_failed_enter(self):
        c = self.context('core')
        original = c.cache
        c.pass_cache, c.preview_cache = 'custom', original / 'custom-preview'
        c.gfx_cache = original / 'custom-gfx'
        mkdir = fstxn.mkdir
        def fail_work(path, **kwargs):
            if Path(path).name == 'work':
                raise RuntimeError('cannot create work')
            return mkdir(path, **kwargs)
        with patch.object(fstxn, 'mkdir', side_effect=fail_work), self.assertRaises(RuntimeError):
            with CacheSession(c):
                self.fail('enter should fail')
        self.assertEqual(c.cache, original)
        self.assertEqual(c.pass_cache, 'custom')
        self.assertEqual(c.preview_cache, original / 'custom-preview')
        self.assertEqual(c.gfx_cache, original / 'custom-gfx')
        self.assertFalse(hasattr(c, 'cache_session'))
        self.assertEqual(list((original / 'tmp').glob('*cache-core*')), [])

    def test_standalone_prerequisites_once_and_shared_qc_reference_plan(self):
        c = self.context('core')
        with CacheSession(c) as session, \
             patch('vtx.pipeline.stages.prepare') as prepare, \
             patch('vtx.cli.commands.gfx') as gfx, \
             patch('vtx.pipeline.composite.needs_qc_clean', return_value=True), \
             patch('vtx.pipeline.composite.prepare_layers', return_value=[]), \
             patch('vtx.pipeline.composite.RenderPlan') as plan, \
             patch('vtx.pipeline.composite.render_qc_clean') as qc, \
             patch.dict(os.environ, VTX_VIDEO_SEGMENTS='7'):
            session.ensure_stage_inputs('audio')
            session.ensure_stage_inputs('audio')
            session.ensure_stage_inputs('qc')
            session.ensure_stage_inputs('qc')
            prepare.assert_called_once_with(c)
            gfx.assert_called_once_with(c)
            self.assertEqual(plan.call_args.kwargs['nseg'], 7)
            qc.assert_called_once_with(c, plan.return_value, [])

    def test_stage_handoffs_retire_only_successful_full_build_not_aggressive(self):
        from vtx.cache.recovery import completion_marker
        for mode, fail, enabled in [('off', False, True), ('core', False, True),
                                     ('aggressive', False, True), ('core', True, True),
                                     ('core', False, False)]:
            with self.subTest(mode=mode, fail=fail, enabled=enabled):
                c = self.context(mode)
                c.out.mkdir(exist_ok=True)
                handoffs = [c.out / 'audio.wav', c.out / 'video_noaudio.mp4']
                payloads = handoffs + [completion_marker(p) for p in handoffs]
                final, receipt = c.out / 'final.mp4', c.out / 'receipt.json'
                for path in payloads + [final, receipt]:
                    path.write_bytes(b'keep exact original bytes unless retired')
                try:
                    with CacheSession(c, retire_stage_outputs=enabled) as session:
                        session.completed.update(('audio', 'video'))
                        if fail:
                            raise RuntimeError('producer failed')
                except RuntimeError:
                    pass
                retired = enabled and not fail and mode != 'aggressive'
                self.assertTrue(all(p.exists() != retired for p in payloads))
                self.assertTrue(final.exists())
                self.assertTrue(receipt.exists())

    def test_retirement_protects_explicit_handoff_source_and_untouched_old_outputs(self):
        from vtx.cache.recovery import completion_marker
        c = self.context('core')
        c.out.mkdir()
        audio, video = c.out / 'audio.wav', c.out / 'video_noaudio.mp4'
        for path in (audio, video, completion_marker(audio), completion_marker(video)):
            path.write_bytes(b'protected/previous handoff')
        c.tl['sources'] = [{'path': str(audio.relative_to(self.root))}]
        self.timeline.write_text(json.dumps(c.tl))
        with CacheSession(c, retire_stage_outputs=True) as session:
            session.completed.add('audio')
        self.assertTrue(all(p.exists() for p in (audio, video, completion_marker(audio), completion_marker(video))))

    def test_core_native_fallback_keeps_certified_loose_page_with_warning(self):
        c, clip, directory, paths, data, marker = self.page()
        for platform, capability in [('linux', 'legacy'), ('darwin', 'fd-anchored')]:
            with self.subTest(platform=platform, capability=capability):
                session = CacheSession(c)
                session.__enter__()
                session.graphics_touched = True
                with patch('vtx.cache.session.sys.platform', platform), \
                     patch.object(fstxn, 'write_capability', return_value=capability), \
                     patch('vtx.cache.session.compact_graphics', side_effect=AssertionError('native compression')), \
                     patch('sys.stderr', new=io.StringIO()) as warning:
                    session.__exit__(None, None, None)
                self.assertIn('retained', warning.getvalue())
        self.assertEqual(c.cache_policy.mode, 'core')
        self.assertEqual((directory / 'complete.json').read_bytes(), marker)
        self.assertEqual([p.read_bytes() for p in paths], data)


if __name__ == '__main__':
    unittest.main()
