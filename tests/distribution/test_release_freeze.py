"""Fail-closed release freeze policy; no network or renderer fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import sys

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.release import release_github as release


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.asset = self.root / 'asset'; self.asset.write_bytes(b'asset')
        self.commit = 'a' * 40
        self.data = {'schema': 'framivra.freeze/2', 'commit': self.commit, 'tag': 'v2.1.0',
          'assets': {'asset': release.digest(self.asset)}, 'gates': {},
          'acceptance': {'capability_probe': {'path': 'package', 'manifest_sha256': 'f' * 64, 'source_root': 'package-input'},
                         'public_demo': {'path': 'main-package', 'manifest_sha256': 'e' * 64, 'source_root': 'main-package-input'}}}
        worker = '\n'.join('✓ ' + name + '[' + ('1' * 12 if name == 'canvas' else '2' * 12) + ']: 渲染 60 帧（含 alpha，4 tabs CDP override×60 failed×0, 1ms）' for name in ('canvas', 'dom'))
        for name, roles in release.REQUIRED_FREEZE_STEPS.items():
            steps = []
            for role, code in roles:
                log = self.root / (role + '.log')
                payload = {'integrity_checks_passed': True, 'release_blockers': {}}
                if role in ('package-tamper-rejected', 'main-package-tamper-rejected'): payload = {'error': 'file digest mismatch: artifacts/final.mp4'}
                if role == 'input-tamper-rejected': payload = {'error': 'live input changed: source-left.mp4'}
                if role == 'main-input-tamper-rejected': payload = {'error': 'live input changed: gfx/frames-svg/f0000.svg'}
                if role in ('demo-media-probe', 'main-package-media-probe'):
                    payload = {'streams': [{'nb_read_frames': '900', 'width': 1920, 'height': 1080, 'r_frame_rate': '30/1'}]}
                argv = ['real-command']
                if role in ('demo-media-probe', 'demo-decode', 'main-package-media-probe'):
                    video = self.root / ('main-package/artifacts/final.mp4' if role.startswith('main-') else 'promo-demo/out/final.mp4')
                    argv = release.media_command(video, decode=role == 'demo-decode')
                if role in ('package-build', 'main-package-build'):
                    payload = {'manifest_sha256': ('e' if role.startswith('main-') else 'f') * 64}
                if role in ('package-verify', 'main-package-verify'):
                    payload = {'integrity_verified': True, 'live_inputs_checked': True}
                    package = self.data['acceptance']['public_demo' if role.startswith('main-') else 'capability_probe']
                    argv += ['-m', 'vtx.inspection.acceptance', 'verify', str(self.root / package['path']),
                             '--manifest-sha256', package['manifest_sha256'], '--source-root', str(self.root / package['source_root'])]
                content = worker if role == 'capability-all' else json.dumps(payload)
                if role == 'edit-gfx':
                    content = '✓ dom[' + '2' * 12 + ']: 60 帧已缓存\n' + worker.splitlines()[0].replace('1' * 12, '3' * 12)
                log.write_text(content)
                steps.append({'role': role, 'source_commit': self.commit, 'argv': argv,
                    'exit_code': code, 'expected_exit': code, 'log': log.name, 'sha256': release.digest(log)})
            observations = {}
            if name == 'public_demo_qc':
                observation = {'stages': {stage: {'ok': True} for stage in
                    ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc')}}
                observation['stages']['gfx']['svg_producer'] = 'framivra-svg-inkscape/v1'
                key = 'demo_receipt'
            elif name == 'local_edit':
                old = {f'cache/gfx/{key}/f{i:04d}.png': {'sha256': 'a' * 64, 'mtime_ns': 10}
                       for key in ('1' * 12, '2' * 12) for i in range(60)}
                added = {f'cache/gfx/{"3" * 12}/f{i:04d}.png': {'sha256': 'b' * 64, 'mtime_ns': 20} for i in range(60)}
                observation = {'schema': 'framivra.cache_edit/1', 'commit': self.commit, 'tag': 'v2.1.0',
                    'reused': list(old), 'created': list(added), 'before': old, 'after': {**old, **added},
                    'html_before_sha256': 'c' * 64, 'html_after_sha256': 'd' * 64}
                key = 'cache_edit'
            elif name == 'failure_recovery':
                marker = 'INTERRUPTED RELEASE ACCEPTANCE CACHE'
                import hashlib
                observation = {'schema': 'framivra.cache_recovery/1', 'commit': self.commit, 'tag': 'v2.1.0',
                    'path': 'cache/audio/a.wav', 'before_sha256': 'e' * 64, 'after_sha256': 'e' * 64,
                    'corrupted_sha256': hashlib.sha256(marker.encode()).hexdigest(), 'injected_bytes': marker}
                key = 'cache_recovery'
            else: key = None
            if key:
                file = self.root / (key + '.json'); file.write_text(json.dumps(observation))
                observations[key] = {'path': file.name, 'sha256': release.digest(file)}
            report = self.root / (name + '.json')
            report.write_text(json.dumps({'schema': 'framivra.freeze-gate/2', 'commit': self.commit,
                    'tag': 'v2.1.0', 'gate': name, 'status': 'passed', 'steps': steps, 'observations': observations}))
            self.data['gates'][name] = {'status': 'passed', 'path': report.name, 'sha256': release.digest(report)}
        package = self.root / 'package'; package.mkdir(exist_ok=True)
        (package / 'build.log').write_text(worker)
        (package / 'environment.json').write_text(json.dumps({'git': {'status': 'passed', 'stdout': self.commit},
                    'git_status': {'status': 'passed', 'stdout': ''}}))
        main = self.root / 'main-package'
        (main / 'inputs/gfx').mkdir(parents=True)
        (main / 'artifacts').mkdir()
        (main / 'environment.json').write_bytes((package / 'environment.json').read_bytes())
        (main / 'artifacts/receipt.json').write_bytes((self.root / 'demo_receipt.json').read_bytes())
        frames = [f'gfx/frames-svg/f{frame:04d}.svg' for frame in range(900)]
        (main / 'inputs/gfx/demo.frames.json').write_text(json.dumps({'frames': frames}))
        (main / 'inputs/timeline.json').write_text(json.dumps({'project': {'duration_frames': 900},
            'video_tracks': [{'kind': 'gfx', 'clips': [{'format': 'svg-sequence', 'dur': 900, 'src': 'gfx/demo.frames.json'}]}]}))
        repo = Path(__file__).resolve().parents[2]
        for name in release.PROMO_NOTICES:
            source = repo / name if name == 'LICENSE' else repo / 'templates/demo-timeline-svg' / name
            target = main / 'inputs' / name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        (main / 'manifest.json').write_text(json.dumps({'inputs': [
            {'source_path': name} for name in frames + list(release.PROMO_NOTICES)]}))
        self.package_result = {'gates': {name: {'status': 'passed'} for name in
           ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc', 'process', 'input_stability', 'tool_versions', 'media')}}
        verifier = mock.patch('vtx.inspection.acceptance.verify', return_value=self.package_result)
        verifier.start(); self.addCleanup(verifier.stop)
        self.path = self.root / 'freeze.json'

    def verify(self):
        self.path.write_text(json.dumps(self.data))
        return release.verify_freeze_evidence(self.path, self.commit, 'v2.1.0', [self.asset])

    def report(self, gate, mutate):
        path = self.root / (gate + '.json'); data = json.loads(path.read_text())
        mutate(data); path.write_text(json.dumps(data)); self.data['gates'][gate]['sha256'] = release.digest(path)

    def observation(self, gate, key, mutate):
        path = self.root / (key + '.json'); data = json.loads(path.read_text())
        mutate(data); path.write_text(json.dumps(data))
        self.report(gate, lambda report: report['observations'][key].update(sha256=release.digest(path)))

    def test_complete_required_steps_and_observations_pass(self):
        self.verify()

    def test_missing_tamper_step_rejected_even_when_gate_rehashed(self):
        self.report('independent_package', lambda report: report.update(steps=[row for row in report['steps'] if row['role'] != 'input-tamper-rejected']))
        with self.assertRaisesRegex(release.ReleaseError, 'steps missing'): self.verify()

    def test_missing_tamper_log_rejected(self):
        (self.root / 'package-tamper-rejected.log').unlink()
        with self.assertRaises((OSError, ValueError)): self.verify()

    def test_wrong_tamper_exit_or_source_identity_rejected(self):
        self.report('independent_package', lambda report: report['steps'][2].update(exit_code=0, expected_exit=0))
        with self.assertRaisesRegex(release.ReleaseError, 'identity or exit'): self.verify()
        self.report('independent_package', lambda report: report['steps'][2].update(exit_code=1, expected_exit=1, source_commit='b' * 40))
        with self.assertRaisesRegex(release.ReleaseError, 'identity or exit'): self.verify()

    def test_missing_or_changed_cache_observation_rejected(self):
        (self.root / 'cache_edit.json').write_text('{}')
        with self.assertRaisesRegex(release.ReleaseError, 'observation bytes'): self.verify()
        (self.root / 'cache_edit.json').unlink()
        with self.assertRaises((OSError, ValueError)): self.verify()

    def test_deleting_observation_reference_rejected(self):
        self.report('local_edit', lambda report: report['observations'].clear())
        with self.assertRaisesRegex(release.ReleaseError, 'observations missing'): self.verify()

    def test_rehashed_cache_observation_must_prove_reuse_and_invalidation(self):
        self.observation('local_edit', 'cache_edit', lambda data: data.update(created=[]))
        with self.assertRaisesRegex(release.ReleaseError, 'claims disagree'): self.verify()

    def test_rehashed_recovery_observation_must_prove_actual_damage_and_restore(self):
        self.observation('failure_recovery', 'cache_recovery', lambda data: data.update(corrupted_sha256=data['before_sha256']))
        with self.assertRaisesRegex(release.ReleaseError, 'corrupted cache digest'): self.verify()

    def test_rehashed_observation_wrong_commit_rejected(self):
        self.observation('local_edit', 'cache_edit', lambda data: data.update(commit='b' * 40))
        with self.assertRaisesRegex(release.ReleaseError, 'identity mismatch'): self.verify()

    def test_each_gate_requires_exact_roles_and_exits(self):
        for gate in release.REQUIRED_FREEZE_STEPS:
            path = self.root / (gate + '.json'); original = path.read_bytes()
            for mutation in ('missing', 'extra', 'role', 'exit', 'log'):
                with self.subTest(gate=gate, mutation=mutation):
                    data = json.loads(original)
                    if mutation == 'missing': data['steps'].pop()
                    elif mutation == 'extra': data['steps'].append(dict(data['steps'][0]))
                    elif mutation == 'role': data['steps'][0]['role'] = 'unrelated-command'
                    elif mutation == 'exit':
                        data['steps'][0].update(exit_code=7, expected_exit=7)
                    else: data['steps'][0]['log'] = 'unrelated.log'
                    path.write_text(json.dumps(data))
                    self.data['gates'][gate]['sha256'] = release.digest(path)
                    with self.assertRaises(release.ReleaseError): self.verify()
            path.write_bytes(original)
            self.data['gates'][gate]['sha256'] = release.digest(path)

    def test_each_required_raw_log_is_sealed(self):
        for roles in release.REQUIRED_FREEZE_STEPS.values():
            for role, _ in roles:
                path = self.root / (role + '.log'); original = path.read_bytes()
                with self.subTest(role=role):
                    path.write_bytes(original + b'changed')
                    with self.assertRaises(release.ReleaseError): self.verify()
                path.write_bytes(original)

    def test_rehashed_tamper_log_requires_correct_rejection_reason(self):
        path = self.root / 'package-tamper-rejected.log'
        path.write_text(json.dumps({'error': 'unrelated failure'}))
        self.report('independent_package', lambda data: data['steps'][2].update(sha256=release.digest(path)))
        with self.assertRaisesRegex(release.ReleaseError, 'rejection reason'): self.verify()

    def test_rehashed_edit_observation_requires_both_gfx_reuse_and_invalidation(self):
        self.observation('local_edit', 'cache_edit', lambda data: data.update(
            before={'cache/audio/a.wav': {'sha256': 'a' * 64, 'mtime_ns': 10}},
            after={'cache/audio/a.wav': {'sha256': 'a' * 64, 'mtime_ns': 10},
                   'cache/audio/b.wav': {'sha256': 'b' * 64, 'mtime_ns': 20}},
            reused=['cache/audio/a.wav'], created=['cache/audio/b.wav']))
        with self.assertRaisesRegex(release.ReleaseError, 'gfx invalidation'): self.verify()

    def test_rehashed_recovery_observation_requires_original_hash_restored(self):
        self.observation('failure_recovery', 'cache_recovery', lambda data: data.update(after_sha256='f' * 64))
        with self.assertRaisesRegex(release.ReleaseError, 'restored'): self.verify()

    def test_rehashed_edit_log_cannot_claim_disk_residue_as_actual_reuse(self):
        path = self.root / 'edit-gfx.log'
        path.write_text(path.read_text().replace('60 帧已缓存', '60 帧仍在磁盘'))
        self.report('local_edit', lambda data: data['steps'][0].update(sha256=release.digest(path)))
        with self.assertRaisesRegex(release.ReleaseError, 'worker/hit observation'): self.verify()

    def test_rehashed_edit_log_keys_must_match_snapshot_frames(self):
        path = self.root / 'edit-gfx.log'
        path.write_text(path.read_text().replace('3' * 12, '4' * 12))
        self.report('local_edit', lambda data: data['steps'][0].update(sha256=release.digest(path)))
        with self.assertRaisesRegex(release.ReleaseError, 'bind actual worker'): self.verify()

    def test_missing_evidence_rejected(self):
        with self.assertRaisesRegex(release.ReleaseError, 'required'):
            release.verify_freeze_evidence(None, self.commit, 'v2.1.0', [self.asset])

    def test_missing_unknown_and_failed_required_gates_rejected(self):
        for status in ('unknown', 'failed', 'skipped'):
            self.data['gates']['local_edit']['status'] = status
            with self.assertRaises(release.ReleaseError): self.verify()
        self.data['gates'].pop('local_edit')
        with self.assertRaises(release.ReleaseError): self.verify()

    def test_wrong_commit_or_asset_rejected(self):
        self.data['commit'] = 'b' * 40
        with self.assertRaises(release.ReleaseError): self.verify()
        self.data['commit'] = self.commit; self.asset.write_bytes(b'tamper')
        with self.assertRaises(release.ReleaseError): self.verify()

    def test_changed_raw_log_rejected(self):
        (self.root / 'edit-all.log').write_bytes(b'changed')
        with self.assertRaises(release.ReleaseError): self.verify()

    def test_independent_required_unknown_rejected(self):
        result = {'gates': {name: {'status': 'passed'} for name in
           ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc', 'process', 'input_stability', 'tool_versions', 'media')}}
        (self.root / 'package').mkdir(exist_ok=True)
        (self.root / 'package/environment.json').write_text(json.dumps({'git': {'status': 'passed', 'stdout': self.commit}, 'git_status': {'status': 'passed', 'stdout': ''}}))
        with mock.patch('vtx.inspection.acceptance.verify', return_value=result), mock.patch.object(release, 'observed_cdp', return_value='cdp'):
            self.verify()
            result['gates']['qc']['status'] = 'unknown'
            with self.assertRaisesRegex(release.ReleaseError, 'qc'): self.verify()

    def test_public_summary_never_copies_environment_paths_or_logs(self):
        package = self.root / 'main-package'; package.mkdir(exist_ok=True)
        (package / 'environment.json').write_text(json.dumps({'python': '3.13.5',
           'tools': {name: {'stdout': name + ' version 8.1.3 Copyright build secret-private'} for name in ('ffmpeg', 'ffprobe')},
           'account': 'secret-private', 'HOME': '/private/user', 'logs': 'secret-private'}))
        self.path.write_text(json.dumps(self.data))
        with mock.patch.object(release, 'observed_cdp', return_value='cdp'):
            summary_path = release.build_public_summary(self.path, self.root)
        raw = summary_path.read_text()
        self.assertNotIn('secret-private', raw)
        self.assertNotIn('/private/user', raw)
        summary = json.loads(raw)
        self.assertEqual(summary['backend_observed'], {'public_demo': 'svg-sequence', 'capability_probe': 'cdp'})
        self.assertEqual(summary['tool_versions'], {'python': '3.13.5', 'ffmpeg': '8.1.3', 'ffprobe': '8.1.3'})
        self.assertEqual(set(summary), {'schema', 'version', 'tag', 'source_commit', 'core_asset_sha256', 'gates',
           'tool_versions', 'backend_observed', 'support_scope', 'limitations', 'local_freeze_sha256', 'reproduction_guide'})

    def test_requested_cdp_or_fallback_does_not_qualify(self):
        log = self.root / 'worker.log'
        for text in ('VTX_GFX=cdp', '✓ canvas[x]: 渲染 60 帧 CDP failed×0'):
            log.write_text(text)
            with self.assertRaises(release.ReleaseError): release.observed_cdp(log)
        good = '\n'.join('✓ ' + name + '[' + ('1' * 12 if name == 'canvas' else '2' * 12) + ']: 渲染 60 帧（含 alpha，4 tabs CDP override×60 failed×0, 1ms）' for name in ('canvas','dom'))
        log.write_text(good)
        self.assertEqual(release.observed_cdp(log), 'cdp')
        log.write_text(good + '\n回落 CLI')
        with self.assertRaises(release.ReleaseError): release.observed_cdp(log)

    def test_main_svg_cannot_replace_capability_cdp(self):
        path = self.root / 'capability-all.log'
        path.write_text('SVG rendering completed: 900 frames')
        self.report('public_demo_render', lambda data: data['steps'][-1].update(sha256=release.digest(path)))
        with self.assertRaisesRegex(release.ReleaseError, 'CDP worker'):
            self.verify()

    def test_main_requires_all_stages_and_actual_svg_producer(self):
        for stage in ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc'):
            with self.subTest(stage=stage):
                self.observation('public_demo_qc', 'demo_receipt', lambda data: data['stages'][stage].update(ok=False))
                with self.assertRaisesRegex(release.ReleaseError, 'pipeline stage'):
                    self.verify()
                self.observation('public_demo_qc', 'demo_receipt', lambda data: data['stages'][stage].update(ok=True))
        self.observation('public_demo_qc', 'demo_receipt', lambda data: data['stages']['gfx'].pop('svg_producer'))
        with self.assertRaisesRegex(release.ReleaseError, 'SVG producer'):
            self.verify()

    def test_main_decoded_frames_geometry_and_rate_cannot_be_self_selected(self):
        path = self.root / 'demo-media-probe.log'
        original = path.read_bytes()
        for field, value in (('nb_read_frames', '899'), ('nb_read_frames', '901'),
                             ('width', 1280), ('height', 720), ('r_frame_rate', '24/1')):
            with self.subTest(field=field, value=value):
                data = json.loads(original); data['streams'][0][field] = value
                path.write_text(json.dumps(data))
                self.report('public_demo_qc', lambda data: data['steps'][1].update(sha256=release.digest(path)))
                with self.assertRaisesRegex(release.ReleaseError, '900 frames'):
                    self.verify()
        path.write_bytes(original)
        self.report('public_demo_qc', lambda data: data['steps'][1].update(sha256=release.digest(path)))
        self.verify()

    def test_cdp_keeps_both_full_pages_and_zero_failures(self):
        path = self.root / 'capability-all.log'; good = path.read_text()
        for bad in (good.splitlines()[0], good.replace('60 帧', '59 帧'),
                    good.replace('failed×0', 'failed×1'), good.replace('failed×0', 'failed×0 cli_fallback×1'),
                    good + '\n' + good.splitlines()[0]):
            with self.subTest(log=bad):
                path.write_text(bad)
                with self.assertRaises(release.ReleaseError):
                    release.observed_cdp(path)

    def test_named_raw_log_rejects_missing_duplicate_source_log_and_hash(self):
        original = json.loads((self.root / 'public_demo_render.json').read_text())
        for field in ('missing', 'duplicate', 'source_commit', 'log', 'sha256', 'exit_code', 'expected_exit', 'argv', 'extra'):
            data = json.loads(json.dumps(original))
            row = next(row for row in data['steps'] if row['role'] == 'capability-all')
            if field == 'missing': data['steps'].remove(row)
            elif field == 'duplicate': data['steps'].append(dict(row))
            else: row[field] = 'wrong'
            with self.subTest(field=field), self.assertRaises(release.ReleaseError):
                release.step_log(self.root, data, 'capability-all')

    def test_capability_fixture_preserves_event_coverage_without_rendering(self):
        from tests.runners._capability_fixture import create, BOUNDARIES
        commands = []
        project = self.root / 'capabilities'
        timeline = json.loads(create(project, 'ffmpeg', commands.append).read_text())
        self.assertEqual(len(commands), 2)
        self.assertEqual(timeline['project']['duration_frames'], 180)
        self.assertEqual([(clip['dst_in'], clip['dur']) for clip in timeline['video_tracks'][1]['clips']], [(15, 60), (105, 60)])
        self.assertEqual([clip['dur'] for clip in timeline['video_tracks'][0]['clips']], [90, 90])
        self.assertTrue(timeline['audio_tracks'][0]['events'][0]['loop'])
        self.assertGreaterEqual(len(BOUNDARIES), 19)
        self.assertTrue({0, 14, 15, 74, 75, 89, 90, 104, 105, 164, 165, 179}.issubset(BOUNDARIES))
        self.assertTrue(any(row['in'] < 54 < 90 < row['out'] for row in timeline['subtitles']))
        with self.assertRaises(FileExistsError):
            create(project, 'ffmpeg', commands.append)

    def test_both_package_roles_are_required_and_cannot_alias(self):
        original = json.loads(json.dumps(self.data['acceptance']))
        for mutation in ('missing', 'extra', 'alias'):
            self.data['acceptance'] = json.loads(json.dumps(original))
            if mutation == 'missing': self.data['acceptance'].pop('public_demo')
            elif mutation == 'extra': self.data['acceptance']['other'] = dict(original['public_demo'])
            else: self.data['acceptance']['public_demo']['path'] = original['capability_probe']['path']
            with self.subTest(mutation=mutation), self.assertRaises(release.ReleaseError):
                self.verify()

    def test_main_package_requires_every_svg_input(self):
        manifest = self.root / 'main-package/manifest.json'
        data = json.loads(manifest.read_text()); data['inputs'].pop(0)
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(release.ReleaseError, 'frame closure'):
            self.verify()

    def test_main_package_frame_order_or_missing_frame_rejected(self):
        path = self.root / 'main-package/inputs/gfx/demo.frames.json'
        original = path.read_bytes()
        for mutation in ('missing', 'order', 'duplicate'):
            data = json.loads(original)
            if mutation == 'missing': data['frames'].pop()
            elif mutation == 'order': data['frames'][0], data['frames'][1] = data['frames'][1], data['frames'][0]
            else: data['frames'][-1] = data['frames'][0]
            path.write_text(json.dumps(data))
            with self.subTest(mutation=mutation), self.assertRaisesRegex(release.ReleaseError, '900 SVG frame'):
                self.verify()

    def test_package_manifest_and_live_source_are_bound_per_role(self):
        for role in ('public_demo', 'capability_probe'):
            for field in ('manifest_sha256', 'source_root'):
                original = self.data['acceptance'][role][field]
                self.data['acceptance'][role][field] = 'd' * 64 if field == 'manifest_sha256' else 'unrelated-input'
                with self.subTest(role=role, field=field), self.assertRaisesRegex(release.ReleaseError, 'binding disagrees'):
                    self.verify()
                self.data['acceptance'][role][field] = original

    def test_real_media_commands_bind_target_and_required_parameters(self):
        for role in ('demo-media-probe', 'demo-decode', 'main-package-media-probe'):
            gate = 'independent_package' if role.startswith('main-') else 'public_demo_qc'
            path = self.root / (gate + '.json'); original = path.read_bytes()
            for mutation in ('wrong-target', 'swapped-target', 'missing-option'):
                data = json.loads(original)
                row = next(row for row in data['steps'] if row['role'] == role)
                target_index = 4 if role == 'demo-decode' else -1
                if mutation == 'missing-option': row['argv'].pop(2)
                elif mutation == 'wrong-target': row['argv'][target_index] = str(self.root / 'other.mp4')
                else:
                    other = 'promo-demo/out/final.mp4' if role.startswith('main-') else 'main-package/artifacts/final.mp4'
                    row['argv'][target_index] = str(self.root / other)
                path.write_text(json.dumps(data)); self.data['gates'][gate]['sha256'] = release.digest(path)
                with self.subTest(role=role, mutation=mutation), self.assertRaisesRegex(release.ReleaseError, 'media command or target'):
                    self.verify()
            path.write_bytes(original); self.data['gates'][gate]['sha256'] = release.digest(path)

    def test_main_package_notices_require_manifest_and_original_bytes(self):
        manifest = self.root / 'main-package/manifest.json'; manifest_bytes = manifest.read_bytes()
        for name in release.PROMO_NOTICES:
            path = self.root / 'main-package/inputs' / name; original = path.read_bytes()
            for mutation in ('undeclared', 'missing', 'changed-with-rehashed-record'):
                with self.subTest(notice=name, mutation=mutation):
                    data = json.loads(manifest_bytes)
                    if mutation == 'undeclared':
                        data['inputs'] = [row for row in data['inputs'] if row['source_path'] != name]
                    elif mutation == 'missing': path.unlink()
                    else:
                        path.write_bytes(original + b'tamper')
                        next(row for row in data['inputs'] if row['source_path'] == name)['sha256'] = release.digest(path)
                    manifest.write_text(json.dumps(data))
                    with self.assertRaises((release.ReleaseError, ValueError)):
                        self.verify()
                    path.write_bytes(original); manifest.write_bytes(manifest_bytes)
        self.verify()

    def test_rc_accepted_but_unsupported_tag_rejected(self):
        self.assertEqual(release.tag_argument('v2.1.0-rc.1'), 'v2.1.0-rc.1')
        for tag in ('2.1.0', 'v2.1.0-rc.0', 'main'):
            with self.assertRaises(Exception): release.tag_argument(tag)


if __name__ == '__main__': unittest.main()
