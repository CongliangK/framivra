"""Exact shared renderer identity, including order and full ASS bytes."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from vtx.composite import RenderPlan, _graph_code_digest, segment_content_key


class SharedIdentity(unittest.TestCase):
    def test_legacy_key_and_ass_bytes(self):
        c = SimpleNamespace(fps=30, gpu=False, tl={'subtitles': [
            {'in': 10, 'out': 80, 'text': 'hello'}]})
        plan = RenderPlan(c, False, 1, 64, 64)
        layers = [{'gfx': True, 'source': SimpleNamespace(identity='abc'),
                   'cl': {'src': 'page.html', 'dst_in': 0, 'dur': 90},
                   'x': 0, 'y': 0, 'w2': None, 'h2': None}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'subs.ass'
            plan.write_seg_ass(path, 30, 90)
            self.assertEqual(path.read_bytes(), plan.segment_ass_bytes(30, 90))
            old = hashlib.sha1()
            old.update(b'30|90|64x64|0|0\n')
            old.update(f'graph={_graph_code_digest()}\n'.encode())
            old.update(('abc|' + json.dumps(layers[0]['cl'], sort_keys=True,
                                            ensure_ascii=False) + '|0|0|None|None\n').encode())
            old.update(path.read_bytes())
            self.assertEqual(old.hexdigest()[:12], plan._seg_content_key(
                30, 90, layers, True, path))

    def test_layer_order_and_geometry_are_significant(self):
        a = ('abc', {'dur': 90}, 0, 0, None, None)
        b = ('def.mp4', {'dur': 90}, 2, 3, 64, 64)
        def key(layers):
            return segment_content_key(0, 90, 64, 64, False, False, layers, b'', 'graph')
        self.assertNotEqual(key([a, b]), key([b, a]))
        self.assertNotEqual(key([a]), key([a[:-1] + (32,)]))

    def test_identity_helper_input_mutations_are_visible(self):
        from vtx.composite import pass_content_key
        base = ('upstream', {'dur': 90}, 0, 0, None, None)
        original = pass_content_key('previous', 0, base, 'graph')
        variants = [('other', 0, base, 'graph'), ('previous', 1, base, 'graph'),
                    ('previous', 0, base, 'newgraph')]
        for index, value in enumerate(('new-upstream', {'dur': 91}, 1, 2, 64, 32)):
            layer = list(base); layer[index] = value
            variants.append(('previous', 0, layer, 'graph'))
        for args in variants:
            with self.subTest(args=args):
                self.assertNotEqual(original, pass_content_key(*args))
        args = [0, 90, 64, 64, False, False, [base], b'subtitle', 'graph']
        original = segment_content_key(*args)
        for index, value in enumerate((1, 91, 128, 128, True, True,
                                       [('new', *base[1:])], b'new subtitle', 'new graph')):
            changed = list(args); changed[index] = value
            with self.subTest(index=index):
                self.assertNotEqual(original, segment_content_key(*changed))


class SnapshotImpact(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'shared.bin').write_bytes(b'a' * 262144)
        self.tl = {'project': {'fps': 30, 'width': 64, 'height': 64,
                              'duration_frames': 2700,
                              'cache_dependencies': ['shared.bin']},
                   'sources': [], 'audio_tracks': [], 'subtitles': [],
                   'video_tracks': [{'kind': 'gfx', 'clips': []}]}
        for i in range(30):
            page = f'page{i}.html'
            (self.root / page).write_text(f'<p>Scene {i}</p>')
            self.tl['video_tracks'][0]['clips'].append(
                {'src': page, 'dst_in': i * 90, 'dur': 90})

    def snapshot(self):
        from vtx.impact import capture
        return capture(self.tl, self.root)

    def test_scene_eight_edit_has_no_unrelated_page_misses(self):
        from vtx.impact import compare
        before = self.snapshot()
        (self.root / 'page7.html').write_text('<p>New title</p>')
        after = self.snapshot()
        result = compare(before, after)
        pages = [n['node'] for n in result['affected_nodes'] if n['stage'] == 'gfx']
        self.assertEqual(pages, ['clip:0:7'])
        self.assertFalse(result['audio_mix_changed'])
        self.assertEqual(len([p for p in result['new_artifacts'] if '/gfx/' in p]), 1)
        self.assertIn('clip:0:7', result['conservative_nodes'])

    def test_declared_dependency_middle_bytes_restored_mtime(self):
        import os
        from vtx.impact import compare
        before = self.snapshot()
        path = self.root / 'shared.bin'
        st = path.stat()
        data = bytearray(path.read_bytes()); data[131072] = 98
        path.write_bytes(data)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        result = compare(before, self.snapshot())
        self.assertEqual(len([n for n in result['affected_nodes'] if n['stage'] == 'gfx']), 30)
        self.assertFalse(result['audio_mix_changed'])

    def test_noop_roundtrip(self):
        from vtx.impact import compare
        before = json.loads(json.dumps(self.snapshot()))
        result = compare(before, self.snapshot())
        self.assertEqual(result['affected_nodes'], [])
        self.assertEqual(result['new_artifacts'], [])

    def test_missing_dependency_never_certified_unchanged(self):
        from vtx.impact import compare
        (self.root / 'shared.bin').unlink()
        result = compare(self.snapshot(), self.snapshot())
        self.assertTrue(result['affected_nodes'])
        self.assertTrue(all(n['reasons'] == ['identity_unavailable'] for n in result['affected_nodes']))

    def test_cli_snapshot_and_no_overwrite(self):
        import os
        import subprocess
        import sys
        timeline = self.root / 'timeline.json'
        timeline.write_text(json.dumps(self.tl))
        snapshot = self.root / 'before.json'
        repo = Path(__file__).resolve().parents[2]
        command = [sys.executable, str(repo / 'vtx/vtx.py'), 'diff']
        env = dict(os.environ, VTX_SEG_NVENC='0')
        def run(args):
            return subprocess.run(command + args, env=env, capture_output=True, text=True)
        result = run(['--capture-impact', str(timeline), '--json', str(snapshot)])
        self.assertEqual(result.returncode, 0, result.stderr)
        original = snapshot.read_bytes()
        result = run(['--capture-impact', str(timeline), '--json', str(snapshot)])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(snapshot.read_bytes(), original)
        result = run([str(snapshot), str(timeline)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['new_artifacts'], [])
        (self.root / 'page7.html').write_text('changed')
        result = run([str(snapshot), str(timeline)])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(json.loads(result.stdout)['affected_nodes'])
        self.assertFalse((self.root / 'cache').exists())
        self.assertFalse((self.root / 'out').exists())

    def test_fast_media_blind_spot_is_explicit(self):
        import os
        from vtx.impact import capture, compare
        path = self.root / 'large.media'
        path.write_bytes(b'a' * 262144)
        tl = {'project': self.tl['project'],
              'sources': [{'id': 's', 'path': 'large.media'}],
              'video_tracks': [], 'subtitles': [], 'audio_tracks': []}
        before = capture(tl, self.root)
        st = path.stat(); data = bytearray(path.read_bytes()); data[131072] = 98
        path.write_bytes(data); os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        result = compare(before, capture(tl, self.root))
        self.assertEqual(result['new_artifacts'], [])
        self.assertIn('source_media_fast_sampled_identity_not_verified', result['limitations'])

    def test_windowed_media_without_probe_is_unknown(self):
        from vtx.impact import capture, compare
        (self.root / 'media').write_bytes(b'a')
        tl = {'project': self.tl['project'], 'sources': [{'id': 's', 'path': 'media'}],
              'video_tracks': [{'kind': 'aroll', 'clips': [
                  {'src': 's', 'src_in': 0, 'src_out': 90, 'dur': 90, 'dst_in': 0, 'scale': .5}]}],
              'subtitles': [], 'audio_tracks': []}
        snapshot = capture(tl, self.root)
        self.assertIsNone(snapshot['nodes']['clip:0:0']['key'])
        result = compare(snapshot, capture(tl, self.root))
        self.assertIn('clip:0:0', result['conservative_nodes'])
        self.assertTrue(result['uncertainties'])


class ClipDependencies(SnapshotImpact):
    def setUp(self):
        super().setUp()
        (self.root / 'local.bin').write_bytes(b'a' * 262144)
        self.clip = self.tl['video_tracks'][0]['clips'][7]
        self.clip['cache_dependencies'] = ['local.bin']

    def test_local_middle_bytes_restore_mtime_exact_page_and_receipt(self):
        import os
        from vtx.impact import compare
        from vtx.cache_state import receipt_fingerprint, verify_input_identity, InputIdentityError
        ctx = SimpleNamespace(root=self.root, tl=self.tl)
        timeline_bytes = json.dumps(self.tl).encode()
        old_receipt = receipt_fingerprint(ctx, timeline_bytes=timeline_bytes)
        ctx.tl_path = self.root / 'timeline.json'
        ctx.tl_path.write_bytes(timeline_bytes)
        ctx.input_fingerprint = old_receipt
        before = self.snapshot()
        path = self.root / 'local.bin'; st = path.stat()
        data = bytearray(path.read_bytes()); data[131072] = 98
        path.write_bytes(data); os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        result = compare(before, self.snapshot())
        self.assertEqual([n['node'] for n in result['affected_nodes'] if n['stage'] == 'gfx'], ['clip:0:7'])
        self.assertNotEqual(old_receipt, receipt_fingerprint(ctx, timeline_bytes=timeline_bytes))
        with self.assertRaises(InputIdentityError):
            verify_input_identity(ctx)
        self.assertFalse(result['audio_mix_changed'])
        self.assertIn('input_changed:dependencies', next(n['reasons'] for n in result['affected_nodes'] if n['node'] == 'clip:0:7'))

    def test_union_alias_order_and_cwd(self):
        import os
        from vtx.hygiene import gfx_key
        from vtx.timeline_diff import DiffCtx
        c = DiffCtx(self.tl, self.root)
        first = gfx_key(c, self.clip)
        self.clip['cache_dependencies'] = ['./shared.bin', './local.bin', 'local.bin']
        old = Path.cwd()
        try:
            os.chdir('/')
            self.assertEqual(first, gfx_key(c, self.clip))
        finally:
            os.chdir(old)
        self.clip['cache_dependencies'].reverse()
        self.assertEqual(first, gfx_key(c, self.clip))

    def test_environment_migration_preserves_empty_dependency_equivalence(self):
        from vtx.hygiene import gfx_key
        from vtx.gfx_environment import identity
        from vtx.timeline_diff import DiffCtx
        from vtx.cache_state import file_digest
        c = DiffCtx(self.tl, self.root)
        cl = self.tl['video_tracks'][0]['clips'][0]
        blob = (self.root / cl['src']).read_bytes()
        blob += b'|' + str((self.root / 'shared.bin').resolve()).encode() + b'\0' + file_digest(self.root / 'shared.bin').encode()
        old = hashlib.sha1(blob + b'|30|64x64|90').hexdigest()[:12]
        expected = hashlib.sha1(blob + b'|environment:' + identity(c).encode()
                                + b'|30|64x64|90').hexdigest()[:12]
        self.assertNotEqual(expected, old)
        self.assertEqual(gfx_key(c, cl), expected)
        cl['cache_dependencies'] = []
        self.assertEqual(gfx_key(c, cl), expected)

    def test_missing_local_only_invalidates_declaring_page(self):
        from vtx.impact import compare
        from vtx.cache_state import validate_cache_dependencies
        (self.root / 'local.bin').unlink()
        with self.assertRaisesRegex(ValueError, 'not a file'):
            validate_cache_dependencies(SimpleNamespace(tl=self.tl, root=self.root))
        result = compare(self.snapshot(), self.snapshot())
        self.assertEqual([n['node'] for n in result['affected_nodes'] if n['stage'] == 'gfx'], ['clip:0:7'])
        self.assertIn('clip:0:7', result['conservative_nodes'])

    def test_invalid_and_unresolved_declarations(self):
        from vtx.cache_state import validate_cache_dependencies
        from vtx.impact import compare
        for value in (None, 'local.bin', [1], [''], ['  '], ['a\0b'], ['https://example.invalid/a'], ['.']):
            with self.subTest(value=value):
                self.clip['cache_dependencies'] = value
                with self.assertRaises(ValueError):
                    validate_cache_dependencies(SimpleNamespace(tl=self.tl, root=self.root))
                result = compare(self.snapshot(), self.snapshot())
                self.assertIn('clip:0:7', result['conservative_nodes'])
                self.assertTrue(any(n['node'] == 'clip:0:7' for n in result['affected_nodes']))

    def test_non_gfx_declaration_rejected(self):
        from vtx.cache_state import all_declared_dependencies
        self.tl['video_tracks'][0]['kind'] = 'aroll'
        with self.assertRaisesRegex(ValueError, 'only valid on gfx'):
            all_declared_dependencies(SimpleNamespace(tl=self.tl))

    def test_acceptance_collects_clip_inputs(self):
        from vtx.acceptance import collect_inputs
        path = self.root / 'timeline.json'; path.write_text(json.dumps(self.tl))
        names = collect_inputs(path)
        self.assertEqual(names['local.bin'], 'declared_dependency')
        self.assertEqual(names['shared.bin'], 'declared_dependency')

    def test_schema_exposes_only_video_clip_dependency_field(self):
        schema = json.loads((Path(__file__).resolve().parents[2] / 'vtx/timeline.schema.json').read_text())
        tracks = schema['properties']['video_tracks']['items']
        self.assertIn('cache_dependencies', tracks['properties']['clips']['items']['properties'])
        self.assertIn('allOf', tracks)
        audio = schema['properties']['audio_tracks']['items']['properties']['events']['items']['properties']
        self.assertNotIn('cache_dependencies', audio)
