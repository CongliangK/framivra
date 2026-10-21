"""CPU-only generated PNG/run-receipt adversarial tests for #62."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from scripts.gfx import gfx_golden as gg
from scripts.gfx import gfx_evidence as ge
from PIL import Image


class Evidence(unittest.TestCase):
    def setUp(self):
        from tests.gfx._environment import bind_environment
        bind_environment(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'a.html').write_text('<html>fixture</html>')
        self.tp = self.root / 'timeline.json'
        self.tp.write_text(json.dumps({'project': {'fps': 30, 'width': 64, 'height': 48,
            'duration_frames': 4}, 'video_tracks': [{'kind': 'gfx', 'clips': [
                {'src': 'a.html', 'dur': 4, 'dst_in': 0}]}]}))
        self.c = gg.make_ctx(self.tp)
        self.clips = gg.gfx_clips(self.c)
        self.plan = {'schema': 'vtx.gfx_comparison_plan/1', 'required_instances': [
            {'src': 'a.html', 'dur': 4}], 'input_fingerprint': ge.input_fingerprint(self.c, self.clips),
            'variable_axes': [], 'arms': {s: {'run_id': s, 'backend_id': 'cdp',
            'effective_backend': 'software_skia', 'chromium_version': '152.0.1.2',
            'config': {'capture': 'png'}} for s in ('baseline', 'candidate')}}
        self.runs = {}
        for side in self.plan['arms']:
            d = self.root / side
            d.mkdir()
            frames = {}
            for i in range(4):
                p = d / f'f{i:04d}.png'
                Image.new('RGBA', (64, 48), (100, 20, 30, 128)).save(p)
                frames[f'{i:04d}'] = {'run_id': side, 'origin': 'rendered', 'sha256': gg.hash_frame(p)}
            rt = ge.env_manifest._runtime_backend_truth('cdp', {
                'effective_backend': 'software_skia', 'source': 'cdp',
                'chromium_version': '152.0.1.2', 'viewport': '64x48', 'color_space': 'srgb',
                'evidence': {'browser_version': {'product': 'Chrome/152.0.1.2'},
                             'system_info': {'gpu': {'featureStatus': {'gpu_compositing': 'disabled_software'}}}}})
            font = {'status': 'ok', 'files': {'fixture.ttf': 'a' * 64}}
            manifest = {'schema': 'vtx.env_manifest/1', 'run_id': side, 'backend_id': 'cdp',
                'runtime_backend_truth': rt, 'fonts_fingerprint': {'status': 'ok',
                'required': {f: copy.deepcopy(font) for f in ge.env_manifest.REQUIRED_FONT_FAMILIES},
                'sans-serif': copy.deepcopy(font)}, 'os_kernel': {'system': 'Linux', 'release': 'fixture', 'version': 'fixture', 'machine': 'x86_64'},
                'cpu_model': 'fixture-cpu', 'display_server': {'session_type': 'none',
                    'env': {'DISPLAY': None, 'WAYLAND_DISPLAY': None, 'XDG_SESSION_TYPE': None}},
                'gpu_driver_and_vram': {'gpus': [{'name': 'fixture GPU', 'driver_version': 'fixture',
                    'memory_total_mib': 1024}]}}
            self.runs[side] = {'schema': 'vtx.gfx_run/1', 'run_id': side, 'complete': True,
                'input_fingerprint': self.plan['input_fingerprint'], 'config': {'capture': 'png'},
                'env_manifest': manifest, 'instances': [{'src': 'a.html', 'dur': 4,
                    'frame_dir': str(d), 'frames': frames}]}
        self.g = {'meta': {'fps': 30, 'viewport': '64x48'}, 'pages': {'a.html': {'durs': {
            '4': {'dur': 4, 'mode': 'full', 'frames': {k: v['sha256'] for k, v in
                    self.runs['baseline']['instances'][0]['frames'].items()}}}}}}
        self.bind()

    def bind(self):
        for run in self.runs.values():
            run['plan_sha256'] = ge.digest(self.plan)
            run['env_sha256'] = ge.digest(run['env_manifest'])
        self.g['meta']['run_evidence'] = self.runs['baseline']
        self.g['meta']['env_manifest'] = self.runs['baseline']['env_manifest']

    def check(self):
        return ge.verify(self.c, self.clips, self.g, self.plan, self.runs['candidate'])

    def reject(self):
        r = self.check()
        self.assertFalse(r['ok'], r)
        self.assertNotEqual(r['quality']['display'], 'Verified Exact')
        self.assertFalse(r['quality']['reproducible'])
        return r

    def test_full_independent_run_passes(self):
        r = self.check()
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['compared_frames'], 4)
        self.assertEqual(r['hashed_frames'], 8)
        self.assertTrue(r['same_version_causal'])
        self.assertEqual(r['quality']['display'], 'Verified Exact')
        self.assertTrue(r['quality']['reproducible'])
        self.assertEqual(r['quality']['purpose'], 'unknown')
        self.assertGreaterEqual(r['quality_cost']['wall_seconds'], 0)
        self.assertIsNone(r['quality_cost']['render_seconds'])

    def test_production_input_environment_is_plan_bound_and_unknown_never_passes(self):
        from vtx import gfx_environment as environment
        axes = {'schema': environment.SCHEMA, 'status': 'input-bound', 'backend': 'cdp',
                'pixel_equivalence': 'unproven', 'version': 'fixture version',
                'viewport': {'width': 64, 'height': 48},
                **{k: {'fixture': 'a' * 64} for k in
                   ('browser', 'runtime', 'libraries', 'host_boot', 'implementation')},
                'fonts': {k: {'fixture': 'b' * 64} for k in ('files', 'configs', 'tools', 'libraries')}}
        cert = dict(schema=environment.SCHEMA, status='input-bound', axes=axes,
                    sha256=environment.digest(axes))
        for side in self.runs:
            self.runs[side]['input_environment'] = copy.deepcopy(cert)
            self.plan['arms'][side]['input_environment_sha256'] = cert['sha256']
        self.bind()
        self.assertTrue(self.check()['ok'])
        self.runs['candidate']['input_environment']['status'] = 'unknown'
        self.reject()
        self.runs['candidate']['input_environment'] = copy.deepcopy(cert)
        self.plan['arms']['candidate']['input_environment_sha256'] = '0' * 64
        self.bind()
        self.reject()

    def test_one_difference_counts_all_four(self):
        p = self.root / 'candidate/f0001.png'
        Image.new('RGBA', (64, 48), 'red').save(p)
        self.runs['candidate']['instances'][0]['frames']['0001']['sha256'] = gg.hash_frame(p)
        r = self.reject()
        self.assertEqual(r['compared_frames'], 4)
        self.assertEqual(len(r['different_frames']), 1)

    def test_legacy_golden_has_no_evidence(self):
        del self.g['meta']['run_evidence']
        self.assertIn('legacy golden', self.reject()['errors'][0])

    def test_touch_and_one_new_frame_cannot_supply_identity(self):
        frames = self.runs['candidate']['instances'][0]['frames']
        for k, v in frames.items():
            v['run_id'] = 'old-cache' if k != '0000' else 'candidate'
            os.utime(self.root / f'candidate/f{k}.png', (2000000000, 2000000000))
        self.assertEqual(len(self.reject()['unknown_provenance']), 3)

    def test_full_cache_hit_rejected(self):
        for v in self.runs['candidate']['instances'][0]['frames'].values():
            v['origin'] = 'cache_hit'
        self.assertEqual(len(self.reject()['unknown_provenance']), 4)

    def test_legal_verified_static_copy(self):
        rec = self.runs['candidate']['instances'][0]['frames']['0003']
        rec.update(origin='static_copy', head_frame='0000', static_status='verified_static')
        self.assertTrue(self.check()['ok'])
        rec['static_status'] = 'declared_static'
        self.assertEqual(len(self.reject()['unknown_provenance']), 1)

    def test_static_copy_of_copy_rejected(self):
        frames = self.runs['candidate']['instances'][0]['frames']
        for key, head in [('0002', '0000'), ('0003', '0002')]:
            frames[key].update(origin='static_copy', head_frame=head, static_status='verified_static')
        self.reject()

    def test_static_copy_cannot_hide_animation(self):
        self.g['pages']['a.html']['durs']['4']['frames']['0003'] = 'b' * 64
        self.runs['candidate']['instances'][0]['frames']['0003'].update(
            origin='static_copy', head_frame='0000', static_status='verified_static')
        self.assertEqual(len(self.reject()['unknown_provenance']), 1)

    def test_sample_empty_unknown_and_incomplete_golden(self):
        inst = self.g['pages']['a.html']['durs']['4']
        for mode in ('sample', 'unknown', None):
            inst['mode'] = mode
            self.reject()
        inst['mode'] = 'full'
        del inst['frames']['0001']
        self.reject()
        inst['frames'] = {}
        self.reject()

    def test_missing_and_corrupt_png(self):
        (self.root / 'candidate/f0000.png').unlink()
        (self.root / 'candidate/f0001.png').write_bytes(b'bad PNG')
        r = self.reject()
        self.assertEqual(len(r['missing_frames']), 2)
        self.assertEqual(r['compared_frames'], 2)

    def test_same_bytes_wrong_dimensions_rejected(self):
        p = self.root / 'candidate/f0000.png'
        Image.new('RGBA', (48, 64), (100, 20, 30, 128)).save(p)
        self.assertEqual(gg.hash_frame(p), self.g['pages']['a.html']['durs']['4']['frames']['0000'])
        self.reject()

    def test_missing_manifest_and_incomplete_receipt(self):
        self.runs['candidate']['complete'] = False
        self.reject()
        self.runs['candidate']['complete'] = True
        self.runs['candidate']['env_manifest'] = None
        self.reject()

    def test_new_page_or_duration_requires_explicit_coverage(self):
        for extra in [('b.html', 4), ('a.html', 5)]:
            self.clips.append(extra)
            self.assertEqual(len(self.reject()['uncovered_instances']), 1)
            self.clips.pop()

    def test_input_changed_and_plan_tampered(self):
        self.plan['extra'] = 'changed after run'
        self.reject()
        self.bind()
        (self.root / 'a.html').write_text('changed')
        self.reject()

    def test_run_identity_and_baseline_root_alias(self):
        self.runs['candidate']['run_id'] = 'baseline'
        self.reject()
        self.runs['candidate']['run_id'] = 'candidate'
        self.runs['candidate']['instances'][0]['frame_dir'] = str(self.root / 'baseline')
        self.reject()

    def test_hardlink_and_symlink_aliases(self):
        p = self.root / 'candidate/f0001.png'
        original = p.read_bytes()
        for link in (os.link, os.symlink):
            with self.subTest(link=link.__name__):
                try:
                    p.unlink()
                    link(self.root / 'baseline/f0001.png', p)
                    self.assertIn('aliases baseline frame', self.reject()['errors'][0])
                finally:
                    p.unlink(missing_ok=True)
                    p.write_bytes(original)

    def backend_change(self):
        self.plan['arms']['candidate']['effective_backend'] = 'swiftshader'
        self.runs['candidate']['env_manifest']['runtime_backend_truth']['effective_backend'] = 'swiftshader'
        self.runs['candidate']['env_manifest']['runtime_backend_truth']['evidence']['system_info']['gpu']['auxAttributes'] = {'glRenderer': 'ANGLE SwiftShader Device'}
        self.bind()

    def test_undeclared_backend_change_rejected(self):
        self.backend_change()
        self.assertIn('undeclared', self.reject()['errors'][0])

    def test_preregistered_backend_change_passes_without_mutating_strict_api(self):
        self.backend_change()
        self.plan['variable_axes'] = ['effective_backend']
        self.bind()
        result = self.check()
        self.assertTrue(result['ok'])
        self.assertEqual(result['quality']['display'], 'Verified Exact')
        self.assertFalse(result['quality']['reproducible'])
        self.assertTrue(ge.env_manifest.verify_manifest_compatibility(
            self.runs['baseline']['env_manifest'], self.runs['candidate']['env_manifest']))

    def test_declared_gpu_actual_software_fallback_rejected(self):
        self.plan['arms']['candidate']['effective_backend'] = 'nvidia_egl'
        self.plan['variable_axes'] = ['effective_backend']
        self.bind()
        self.assertIn('actual/declared', self.reject()['errors'][0])

    def test_version_drift_never_causal_acceptance(self):
        self.plan['arms']['candidate']['chromium_version'] = '153.0.1.2'
        t = self.runs['candidate']['env_manifest']['runtime_backend_truth']
        t.update(chromium_version='153.0.1.2', chromium_major=153)
        t['evidence']['browser_version']['product'] = 'Chrome/153.0.1.2'
        self.bind()
        self.reject()
        self.plan['variable_axes'] = ['chromium_version']
        self.bind()
        r = self.reject()
        self.assertFalse(r['same_version_causal'])
        self.assertEqual(r['compared_frames'], 4)
        self.assertEqual(len(r['confounders']), 1)

    def test_fonts_controls_and_unknown_axis_rejected(self):
        self.plan['variable_axes'] = ['fonts_fingerprint']
        self.bind()
        self.reject()
        self.plan['variable_axes'] = []
        self.runs['candidate']['env_manifest']['fonts_fingerprint']['sans-serif']['files']['fixture.ttf'] = 'c' * 64
        self.bind()
        self.assertIn('font drift', self.reject()['errors'][0])

    def test_completion_digest_and_manifest_digest_rejected(self):
        self.runs['candidate']['env_sha256'] = 'bad'
        self.reject()
        self.bind()
        self.runs['candidate']['instances'][0]['frames']['0000']['sha256'] = 'bad'
        self.assertEqual(len(self.reject()['missing_frames']), 1)

    def test_cli_writes_atomic_report_and_invalid_json_fails(self):
        paths = {}
        for name, obj in [('golden', self.g), ('plan', self.plan), ('candidate-run', self.runs['candidate'])]:
            paths[name] = self.root / (name + '.json')
            paths[name].write_text(json.dumps(obj))
        report = self.root / 'report.json'
        argv = ['verify-experiment', '--timeline', str(self.tp), '--report', str(report)]
        for name, path in paths.items():
            argv += ['--' + name, str(path)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gg.main(argv), 0)
        self.assertTrue(json.loads(report.read_text())['ok'])
        paths['plan'].write_text('{invalid')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gg.main(argv), 1)
        self.assertFalse(json.loads(report.read_text())['ok'])
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_baseline_copy_cannot_self_certify_static(self):
        self.runs['baseline']['instances'][0]['frames']['0001'].update(
            origin='static_copy', head_frame='0000', static_status='verified_static')
        self.reject()

    def test_empty_font_digests_cannot_prove_control(self):
        self.runs['candidate']['env_manifest']['fonts_fingerprint']['sans-serif']['files'] = {}
        self.bind()
        self.reject()

    def test_version_claim_must_match_cdp_evidence(self):
        self.runs['candidate']['env_manifest']['runtime_backend_truth']['evidence']['browser_version']['product'] = 'Chrome/152.0.1.3'
        self.bind()
        self.assertIn('version evidence mismatch', self.reject()['errors'][0])

    def test_cross_frame_baseline_aliases_rejected(self):
        originals = {self.root / f'candidate/f{i:04d}.png':
                     (self.root / f'candidate/f{i:04d}.png').read_bytes() for i in range(4)}
        for link in (os.link, os.symlink):
            with self.subTest(link=link.__name__):
                try:
                    for i, p in enumerate(originals):
                        p.unlink()
                        link(self.root / f'baseline/f{(i+1)%4:04d}.png', p)
                    self.assertIn('aliases baseline frame set', self.reject()['errors'][0])
                finally:
                    for p, data in originals.items():
                        p.unlink(missing_ok=True)
                        p.write_bytes(data)

    def test_claimed_gpu_with_software_observations_rejected(self):
        self.plan['variable_axes'] = ['effective_backend']
        self.plan['arms']['candidate']['effective_backend'] = 'nvidia_egl'
        t = self.runs['candidate']['env_manifest']['runtime_backend_truth']
        t['effective_backend'] = 'nvidia_egl'
        self.bind()
        self.reject()
        t['evidence']['system_info']['gpu']['auxAttributes'] = {'glRenderer': 'NVIDIA GeForce EGL'}
        self.bind()
        self.reject()  # contradictory software feature status still rejects
        t['evidence']['system_info']['gpu']['featureStatus']['gpu_compositing'] = 'enabled'
        self.bind()
        self.assertTrue(self.check()['ok'])

    def test_disk_timeline_mutation_before_and_during_verification(self):
        original = self.tp.read_bytes()
        doc = json.loads(original)
        doc['video_tracks'][0]['clips'][0]['dur'] = 3
        self.tp.write_text(json.dumps(doc))
        self.reject()
        self.tp.write_bytes(original)
        old_open = Image.open
        def mutate(*args, **kwargs):
            self.tp.write_text(json.dumps(doc))
            return old_open(*args, **kwargs)
        with mock.patch.object(Image, 'open', side_effect=mutate):
            self.reject()

    def test_host_evidence_missing_malformed_and_unknown_rejected(self):
        for field in ('os_kernel', 'cpu_model', 'display_server', 'gpu_driver_and_vram'):
            original = copy.deepcopy(self.runs['candidate']['env_manifest'][field])
            for value in (None, {}, [], '', 'unknown', 'unavailable'):
                with self.subTest(field=field, value=value):
                    self.runs['candidate']['env_manifest'][field] = value
                    self.bind()
                    self.reject()
            self.runs['candidate']['env_manifest'][field] = original
        self.bind()
        self.assertTrue(self.check()['ok'])

    def test_substantive_runtime_evidence_required(self):
        t = self.runs['candidate']['env_manifest']['runtime_backend_truth']
        original = copy.deepcopy(t['evidence']['system_info'])
        for value in (None, {}, {'gpu': None}, {'gpu': {}}, {'gpu': {'featureStatus': {}}}):
            t['evidence']['system_info'] = value
            self.bind()
            self.reject()
        t['evidence']['system_info'] = original
        self.bind()
        self.assertTrue(self.check()['ok'])

    def test_malformed_timeline_replaces_prior_success(self):
        argv = ['verify-experiment', '--timeline', str(self.tp), '--report', str(self.root / 'report.json')]
        for name, obj in [('golden', self.g), ('plan', self.plan), ('candidate-run', self.runs['candidate'])]:
            p = self.root / (name + '.json')
            p.write_text(json.dumps(obj))
            argv += ['--' + name, str(p)]
        original = self.tp.read_bytes()
        for invalid in ({'project': {}}, [], None, {'project': None}):
            self.tp.write_bytes(original)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(gg.main(argv), 0)
                self.tp.write_text(json.dumps(invalid))
                self.assertEqual(gg.main(argv), 1)
            self.assertFalse(json.loads((self.root / 'report.json').read_text())['ok'])

    def test_missing_completion_and_golden_coverage_counted(self):
        del self.runs['candidate']['instances'][0]['frames']['0001']
        self.assertEqual(self.reject()['missing_frames_count'], 1)
        self.runs['candidate']['instances'] = []
        self.assertEqual(self.reject()['uncovered_instances_count'], 1)
        self.runs['candidate']['instances'] = copy.deepcopy(self.runs['baseline']['instances'])
        del self.g['pages']['a.html']['durs']['4']
        self.assertEqual(self.reject()['uncovered_instances_count'], 1)

    def test_other_sources_validate_observation_shapes_and_versions(self):
        t = copy.deepcopy(self.runs['candidate']['env_manifest']['runtime_backend_truth'])
        for source, evidence in (
            ('cef', {'cef_version': {'chromium': '152.0.1.2'}, 'gl_probe': {
                'chrome_gpu_features': {'Compositing': 'Software only. Hardware acceleration disabled'},
                'webgl': {}}}),
            ('electron', {'chrome_version': '152.0.1.2', 'gpu_feature_status': {
                'gpu_compositing': 'disabled_software'}, 'gpu_info_complete': {
                'auxAttributes': {'glRenderer': 'llvmpipe'}}})):
            t.update(source=source, evidence=evidence)
            ge._observed_backend(t)
            required = ge.env_manifest.SOURCE_REQUIRED_EVIDENCE[source][-1]
            old = evidence[required]
            evidence[required] = None
            with self.assertRaises(ValueError):
                ge._observed_backend(t)
            evidence[required] = old
            t['effective_backend'] = 'nvidia_egl'
            with self.assertRaises(ValueError):
                ge._observed_backend(t)
            t['effective_backend'] = 'software_skia'

    def _strict_cli_files(self):
        argv = ['verify-experiment', '--timeline', str(self.tp), '--report', str(self.root / 'report.json')]
        for name, obj in [('golden', self.g), ('plan', self.plan), ('candidate-run', self.runs['candidate'])]:
            p = self.root / (name + '.json')
            p.write_text(json.dumps(obj))
            argv += ['--' + name, str(p)]
        return argv

    def test_looping_frame_roots_replace_prior_success(self):
        loop = self.root / 'loop'
        loop.symlink_to(loop)
        for side in ('baseline', 'candidate'):
            with self.subTest(side=side):
                argv = self._strict_cli_files()
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(gg.main(argv), 0)
                ri = self.runs[side]['instances'][0]
                with mock.patch.dict(ri, frame_dir=str(loop)):
                    argv = self._strict_cli_files()
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(gg.main(argv), 1)
                    report = json.loads((self.root / 'report.json').read_text())
                    self.assertFalse(report['ok'])
                    self.assertTrue(report['report_written'])
                    self.assertIn('cannot resolve evidence path', report['errors'][0])
        self.assertTrue(self.check()['ok'])

    def test_missing_frame_roots_fail_closed(self):
        missing = self.root / 'missing'
        broken = self.root / 'broken'
        broken.symlink_to(missing)
        for side in ('baseline', 'candidate'):
            for path in (missing, broken):
                with self.subTest(side=side, path=path.name):
                    with mock.patch.dict(self.runs[side]['instances'][0], frame_dir=str(path)):
                        report = self.reject()
                        self.assertIn('cannot resolve evidence path', report['errors'][0])
                        self.assertFalse(report['same_version_causal'])
        self.assertTrue(self.check()['ok'])

    def test_valid_symlink_frame_roots_pass(self):
        for side in ('baseline', 'candidate'):
            link = self.root / (side + '-link')
            link.symlink_to(self.root / side, target_is_directory=True)
            self.runs[side]['instances'][0]['frame_dir'] = str(link)
        self.assertTrue(self.check()['ok'])

    def test_new_report_parent_and_leaf_are_created(self):
        argv = self._strict_cli_files()
        report_path = self.root / 'new' / 'reports' / 'report.json'
        argv[argv.index('--report') + 1] = str(report_path)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gg.main(argv), 0)
        report = json.loads(report_path.read_text())
        self.assertTrue(report['ok'])
        self.assertTrue(report['report_written'])

    def test_looping_timeline_replaces_prior_success(self):
        argv = self._strict_cli_files()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gg.main(argv), 0)
        loop = self.root / 'loop'
        loop.symlink_to(loop)
        argv[argv.index('--timeline') + 1] = str(loop)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gg.main(argv), 1)
        self.assertFalse(json.loads((self.root / 'report.json').read_text())['ok'])

    def test_looping_report_parent_and_leaf_report_write_failure(self):
        argv = self._strict_cli_files()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gg.main(argv), 0)
        previous = (self.root / 'report.json').read_bytes()
        loop = self.root / 'loop'
        loop.symlink_to(loop)
        for path in (loop / 'report.json', loop):
            argv[argv.index('--report') + 1] = str(path)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(gg.main(argv), 1)
            result = json.loads(output.getvalue())
            self.assertFalse(result['ok'])
            self.assertFalse(result['report_written'])
            self.assertIn('cannot write report', result['errors'][-1])
            self.assertEqual((self.root / 'report.json').read_bytes(), previous)

    def test_unrelated_runtime_error_is_not_masked(self):
        with mock.patch.object(ge, '_manifest', side_effect=RuntimeError('programmer error')):
            with self.assertRaisesRegex(RuntimeError, 'programmer error'):
                self.check()

    def test_malformed_nested_shapes_fail_closed(self):
        for value in (None, [], 'bad', 1):
            with self.subTest(value=value):
                old = self.runs['candidate']['instances']
                self.runs['candidate']['instances'] = value
                self.reject()
                self.runs['candidate']['instances'] = old


if __name__ == '__main__':
    unittest.main()
