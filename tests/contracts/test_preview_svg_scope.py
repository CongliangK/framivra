"""Bounded SVG preview consumption; synthetic engine, real input/PNG gates."""
import hashlib
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from vtx.cache.cache_state import InputIdentityError, verify_input_identity
from vtx.cache.hygiene import gfx_dir
from vtx.cache.policy import CachePolicy
from vtx.cache.session import CacheSession, compact_graphics, hydrate_graphics, pack_path
from vtx.pipeline.infra import Ctx
from vtx.render import gfx_pack, gfx_packed_reader, gfx_svg, png_artifact
from vtx.render.preview_inputs import PreviewInputs
from vtx.timeline_frame import frame_timeline
from vtx.runtime import fstxn
from tests.gfx._png import write_png


@unittest.skipUnless(sys.platform in ('linux', 'darwin') and fstxn.directory_write_capability(),
                     'requires directory-anchored preview artifacts')
class PreviewSvgScopeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name, value in [('engine_identity', {'fixture': 'engine'}),
                            ('fontconfig_identity', {'fixture': 'fontconfig', 'executable': '/fixture/fc-scan'}),
                            ('font_faces', [{'family': 'fixture', 'style': 'Regular', 'index': 0}])]:
            mock = patch.object(gfx_svg._PosixRuntimeIdentity, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.clip = dict(format='svg-sequence', src='manifest.json', dst_in=0, dur=2)
        self.manifest = dict(format=gfx_svg.FORMAT, fps=30, width=32, height=32,
                             frame_count=2, frames=['f0.svg', 'f1.svg'],
                             generator='generator.js', fonts=['font.ttf'])
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><rect width="32" height="32" fill="red"/></svg>'
        for name in self.manifest['frames']:
            (self.root / name).write_text(svg)
        (self.root / 'generator.js').write_text('// fixture')
        (self.root / 'font.ttf').write_bytes(b'fixture font bytes')
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest))
        self.path = self.root / 'timeline.json'
        self.path.write_text(json.dumps(dict(version=2,
            project=dict(fps=30, width=32, height=32, duration_frames=2), sources=[],
            video_tracks=[dict(name='graphics', kind='gfx', clips=[self.clip])], audio_tracks=[], subtitles=[])))
        self.c = Ctx(self.path)
        self.c.cache_policy = CachePolicy('core')
        self.clip = self.c.tl['video_tracks'][0]['clips'][0]
        self.directory = gfx_dir(self.c, self.clip)
        self.directory.mkdir(parents=True)
        hashes = []
        for index in range(2):
            path = self.directory / f'f{index:04d}.png'
            write_png(path, width=32, height=32, frame=index)
            hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        self.record = dict(format='framivra-svg-completion/v1', key=self.directory.name,
                           input=gfx_svg.resolve(self.c, self.clip), sha256=hashes)
        self.marker = self.directory / gfx_svg.COMPLETE_FILE
        self.marker.write_text(json.dumps(self.record))
        self.archive = pack_path(self.c, self.clip)

    def current(self, sources):
        verify_input_identity(self.c)
        result = sources.identity()
        verify_input_identity(self.c)
        return result

    def test_reuse_resolves_only_at_two_full_document_boundaries(self):
        with gfx_svg.consumer_scope(self.c):
            sources = PreviewInputs(self.c, 0)
            with patch.object(gfx_svg, 'resolve', wraps=gfx_svg.resolve) as resolve:
                for _ in range(3):
                    self.current(sources)
                self.assertEqual(resolve.call_count, 6)
            with patch.object(gfx_packed_reader, 'verify_input_identity', wraps=verify_input_identity) as verify:
                gfx_packed_reader.read_frame(self.c, self.clip, 0)
                self.assertEqual(verify.call_count, 2, 'standalone reader retains both full checks')

    def test_isolated_warm_pack_resolves_once_per_producer_check_and_rechecks_live_inputs(self):
        self.archive.parent.mkdir(parents=True)
        gfx_pack.pack_page(self.directory, self.archive, expected_record=self.record,
                           svg_marker=True, root=self.root)
        rechecks = []
        with patch.object(gfx_svg, 'resolve', wraps=gfx_svg.resolve) as resolve, \
                patch.object(gfx_packed_reader, 'verify_input_identity', wraps=verify_input_identity) as verify:
            frames = gfx_packed_reader.read_frames(self.c, self.clip, 0, 1,
                                                   keep_recheck=rechecks.append, expected_svg_pack=self.archive)
            self.assertEqual(frames, ((self.directory / 'f0000.png').read_bytes(),))
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(resolve.call_count, 4, 'two full pins and two fresh producer checks')
            recheck, = rechecks
            recheck()
            self.assertEqual(resolve.call_count, 5, 'consumption must resolve the producer anew')

        off_window = self.root / 'f1.svg'
        original = off_window.read_bytes()
        changed = original.replace(b'width="32" height="32" fill=', b'width="31" height="32" fill=')
        self.assertEqual(len(changed), len(original))
        self.assertNotEqual(changed, original)
        off_window.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'packed gfx inputs/backend changed'):
            recheck()
        off_window.write_bytes(original)

        replacement = self.archive.with_suffix('.replacement')
        replacement.write_bytes(self.archive.read_bytes())
        replacement.replace(self.archive)
        with self.assertRaisesRegex(ValueError, 'packed gfx changed during consumption'):
            recheck()

    def test_requested_and_invisible_svg_mutations_during_anchored_read_refused(self):
        for name in ('f0.svg', 'f1.svg', 'generator.js', 'font.ttf'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                with gfx_svg.consumer_scope(self.c):
                    sources = PreviewInputs(self.c, 0)
                    read = png_artifact.read_png
                    def mutate(*args, **kwargs):
                        result = read(*args, **kwargs)
                        path.write_bytes(original.replace(b'red', b'blue') if name.endswith('.svg') else original + b'x')
                        return result
                    with patch.object(png_artifact, 'read_png', side_effect=mutate):
                        with self.assertRaises((ValueError, InputIdentityError)):
                            self.current(sources)
                path.write_bytes(original)

    def test_runtime_identity_change_during_optimized_read_refused(self):
        with gfx_svg.consumer_scope(self.c):
            sources = PreviewInputs(self.c, 0)
            original = png_artifact.read_png
            def mutate(*args, **kwargs):
                result = original(*args, **kwargs)
                gfx_svg._PosixRuntimeIdentity.engine_identity.return_value = {'fixture': 'changed runtime'}
                return result
            with patch.object(png_artifact, 'read_png', side_effect=mutate):
                with self.assertRaises(InputIdentityError):
                    self.current(sources)

    def test_marker_and_png_changes_refused(self):
        for target in ('marker', 'png'):
            with self.subTest(target=target), gfx_svg.consumer_scope(self.c):
                sources = PreviewInputs(self.c, 0)
                self.current(sources)
                if target == 'marker':
                    original = self.marker.read_bytes()
                    self.marker.write_bytes(original + b' ')
                else:
                    path = self.directory / 'f0000.png'
                    original = path.read_bytes()
                    write_png(path, width=32, height=32, frame=9)
                with self.assertRaises(ValueError):
                    self.current(sources)
                (self.marker if target == 'marker' else path).write_bytes(original)

    def test_pack_appearance_uses_full_reader_and_corrupt_or_linked_pack_refused(self):
        with gfx_svg.consumer_scope(self.c):
            sources = PreviewInputs(self.c, 0)
            self.archive.parent.mkdir(parents=True)
            gfx_pack.pack_page(self.directory, self.archive, expected_record=self.record,
                               svg_marker=True, root=self.root)
            with patch.object(gfx_packed_reader, 'read_frame', wraps=gfx_packed_reader.read_frame) as read:
                self.current(sources)
                read.assert_called_once()
            self.archive.write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                self.current(sources)
            self.archive.unlink()
            self.archive.symlink_to(self.root / 'missing')
            with self.assertRaises(ValueError):
                self.current(sources)

    @unittest.skipUnless(sys.platform == 'linux' and fstxn.write_capability() == 'fd-anchored',
                         'requires Linux core persistent graphics storage')
    def test_core_archives_native_svg_without_retiring_and_restores_only_lock_only_page(self):
        original = json.dumps(self.record, indent=2).encode() + b'\n'
        self.marker.write_bytes(original)
        frames = [self.directory / f'f{i:04d}.png' for i in range(self.clip['dur'])]
        pngs = [path.read_bytes() for path in frames]
        with CacheSession(self.c) as session:
            session.ensure_graphics()
        with gfx_pack.PagePack(self.archive, expected_record=self.record, root=self.root) as page:
            self.assertEqual(page.metadata_names, ('complete.json',))
            self.assertEqual(page.read_metadata('complete.json'), original)
            self.assertEqual([page.read_frame(i) for i in range(len(frames))], pngs)
        inode = self.archive.stat().st_ino
        with CacheSession(self.c) as session:
            session.ensure_graphics()
        self.assertEqual(self.archive.stat().st_ino, inode)
        self.assertEqual(self.marker.read_bytes(), original)
        self.assertEqual([path.read_bytes() for path in frames], pngs)
        self.assertFalse((self.directory / 'complete.json').exists())

        lock = self.marker.with_suffix('.json.lock')
        lock.touch()
        self.marker.unlink()
        frames[0].unlink()
        with self.assertRaises(gfx_pack.GfxPackError):
            hydrate_graphics(self.c)
        self.assertFalse(self.marker.exists())
        self.assertEqual(frames[1].read_bytes(), pngs[1])
        frames[1].unlink()
        hydrate_graphics(self.c)
        self.assertTrue(lock.is_file())
        self.assertEqual(self.marker.read_bytes(), original)
        self.assertEqual([path.read_bytes() for path in frames], pngs)
        self.assertFalse((self.directory / 'complete.json').exists())

    def test_core_refuses_bad_native_marker_or_last_png_without_publishing_pack(self):
        marker = self.marker.read_bytes()
        wrong = {**self.record, 'input': {**self.record['input'], 'width': -1}}
        (self.directory / 'complete.json').write_bytes(marker)  # Not native SVG authority.
        self.marker.write_text(json.dumps(wrong))
        with self.assertRaisesRegex(ValueError, 'completion record does not match'):
            compact_graphics(self.c)
        self.assertFalse(self.archive.exists())
        self.marker.write_bytes(marker)
        last = self.directory / 'f0001.png'
        write_png(last, width=32, height=32, frame=9)
        with self.assertRaisesRegex(gfx_pack.GfxPackError, 'frame 1 differs from completion'):
            compact_graphics(self.c)
        self.assertFalse(self.archive.exists())
        self.assertEqual(self.marker.read_bytes(), marker)

    def test_core_warm_bad_last_png_fails_and_only_explicit_producer_can_proceed(self):
        compact_graphics(self.c)
        archive = self.archive.read_bytes()
        inode = self.archive.stat().st_ino
        marker = self.marker.read_bytes()
        last = self.directory / 'f0001.png'
        write_png(last, width=32, height=32, frame=9)
        damaged = last.read_bytes()
        manifest = self.c.cache / 'manifest.json'
        ledger = manifest.read_bytes()
        for operation in (compact_graphics, hydrate_graphics):
            with self.subTest(operation=operation.__name__), \
                    self.assertRaisesRegex(gfx_pack.GfxPackError, 'frame 1 differs from completion'):
                operation(self.c)
        hydrate_graphics(self.c, allow_svg_rebuild=True)
        self.assertEqual(last.read_bytes(), damaged)
        self.assertEqual(self.marker.read_bytes(), marker)
        # Independently valid loose output is still not this archive's output.
        self.marker.write_text(json.dumps({**self.record, 'sha256': [self.record['sha256'][0],
                                                                   hashlib.sha256(damaged).hexdigest()]}))
        with self.assertRaisesRegex(gfx_pack.GfxPackError, 'working frame hashes differ'):
            hydrate_graphics(self.c)
        self.marker.unlink()
        hydrate_graphics(self.c, allow_svg_rebuild=True)
        self.assertFalse(self.marker.exists())
        self.assertEqual(last.read_bytes(), damaged)
        self.assertEqual(manifest.read_bytes(), ledger)
        self.assertEqual((self.archive.stat().st_ino, self.archive.read_bytes()), (inode, archive))
        self.archive.write_bytes(b'bad archive')
        with self.assertRaises(gfx_pack.GfxPackError):
            hydrate_graphics(self.c, allow_svg_rebuild=True)

    def test_core_oversized_svg_index_leaves_original_page_and_existing_pack(self):
        record = {**self.record, 'raster_seconds': 1}
        marker = json.dumps(record).encode()
        self.marker.write_bytes(marker)
        # A cap of exactly the source marker fits that marker but not the index,
        # whose canonical encoding includes the record and the complete frame map.
        with patch.object(gfx_pack, 'PACK_INDEX_LIMIT', len(marker)):
            compact_graphics(self.c)
        self.assertFalse(self.archive.exists())
        self.assertEqual(self.marker.read_bytes(), marker)
        compact_graphics(self.c)
        original = (self.archive.stat().st_ino, self.archive.read_bytes())
        marker = json.dumps({**record, 'raster_seconds': 2}).encode()
        self.marker.write_bytes(marker)
        for cap in (len(marker), len(marker) - 1):
            with self.subTest(cap=cap), patch.object(gfx_pack, 'PACK_INDEX_LIMIT', cap):
                compact_graphics(self.c)
            self.assertEqual((self.archive.stat().st_ino, self.archive.read_bytes()), original)
            self.assertEqual(self.marker.read_bytes(), marker)
        self.archive.write_bytes(b'corrupt')
        with patch.object(gfx_pack, 'PACK_INDEX_LIMIT', len(marker) - 1), \
                self.assertRaises(gfx_pack.GfxPackError):
            compact_graphics(self.c)

    def test_corrupt_pack_appearing_during_read_is_not_ignored(self):
        with gfx_svg.consumer_scope(self.c):
            sources = PreviewInputs(self.c, 0)
            read = png_artifact.read_png
            def appear(*args, **kwargs):
                result = read(*args, **kwargs)
                self.archive.parent.mkdir(parents=True, exist_ok=True)
                self.archive.write_bytes(b'corrupt')
                return result
            with patch.object(png_artifact, 'read_png', side_effect=appear), self.assertRaises(ValueError):
                self.current(sources)

    def test_escaped_and_later_scope_sources_use_full_reader(self):
        with gfx_svg.consumer_scope(self.c):
            sources = PreviewInputs(self.c, 0)
            with self.assertRaisesRegex(ValueError, 'nested'):
                with gfx_svg.consumer_scope(self.c):
                    pass
            self.current(sources)
        with patch.object(gfx_packed_reader, 'read_frame', wraps=gfx_packed_reader.read_frame) as read:
            self.current(sources)
            with gfx_svg.consumer_scope(self.c):
                self.current(sources)
            self.assertEqual(read.call_count, 2)
        self.assertFalse(hasattr(self.c, '_svg_consumer_sources'))

    def render(self, composite):
        # The 32px fixture stubs formal structure/core validation as well as
        # compositor/environment. Full input boundaries, scratch lifecycle and
        # immutable artifact authentication remain real.
        with patch('vtx.timeline_frame._environment', return_value={'fixture': 'environment'}), \
                patch('vtx.pipeline.stages.validate_core', return_value=[]), \
                patch('vtx.timeline_structure.structure_errors', return_value=[]), \
                patch('vtx.pipeline.composite.RenderPlan.composite', composite):
            return frame_timeline(self.path, frame=0, render=True)

    def test_cancel_cleans_scratch_and_explicit_scope(self):
        seen = []
        def cancel(plan, layers, staging, **kwargs):
            seen.append(plan.c)
            self.assertIsNotNone(getattr(plan.c, '_svg_consumer_sources', None))
            raise KeyboardInterrupt
        result = self.render(cancel)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['diagnostics'][0]['code'], 'render.cancelled')
        self.assertEqual(len(seen), 1)
        self.assertFalse(hasattr(seen[0], '_svg_consumer_sources'))
        self.assertFalse(list((self.root / 'cache' / 'preview').glob('*/inputs_*')))
        self.assertFalse(list((self.root / 'cache' / 'preview').glob('*/complete.json')))

    def test_render_current_checkpoints_keep_two_full_resolves_each(self):
        import sys
        contexts = []
        counts = {'current': 0, 'identity': 0}
        original = gfx_svg.resolve
        def resolve(*args, **kwargs):
            frame = sys._getframe(1)
            names = []
            while frame is not None:
                names.append(frame.f_code.co_name)
                frame = frame.f_back
            for name in counts:
                counts[name] += name in names
            return original(*args, **kwargs)
        def composite(plan, layers, staging, **kwargs):
            contexts.append(plan.c)
            write_png(staging, width=32, height=32)
        with patch.object(gfx_svg, 'resolve', side_effect=resolve):
            result = self.render(composite)
        self.assertTrue(result['ok'], result)
        self.assertEqual(counts, {'current': 14, 'identity': 0})
        self.assertFalse(hasattr(contexts[0], '_svg_consumer_sources'))

    def test_render_full_after_checks_catch_invisible_mutation_at_initial_and_current_identity(self):
        for read_number in (2, 3):
            with self.subTest(read_number=read_number):
                path = self.root / 'f1.svg'
                svg = path.read_text()
                def composite(plan, layers, staging, **kwargs):
                    write_png(staging, width=32, height=32)
                original = png_artifact.read_png
                reads = []
                def mutate(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if Path(args[0]) == self.directory / 'f0000.png':
                        reads.append(True)
                        if len(reads) == read_number:
                            path.write_text(svg.replace('red', 'blue'))
                    return result
                with patch.object(png_artifact, 'read_png', side_effect=mutate):
                    result = self.render(composite)
                self.assertFalse(result['ok'], result)
                self.assertIsNone(result['image'])
                self.assertEqual(len(reads), read_number)
                path.write_text(svg)


if __name__ == '__main__':
    unittest.main()
