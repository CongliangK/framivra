"""Fail-closed release freeze policy; no network or renderer fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from scripts.release import release_github as release


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.asset = self.root / 'asset'; self.asset.write_bytes(b'asset')
        self.commit = 'a' * 40
        self.data = {'schema': 'framivra.freeze/1', 'commit': self.commit, 'tag': 'v2.1.0',
          'assets': {'asset': release.digest(self.asset)}, 'gates': {},
          'acceptance': {'path': 'package', 'manifest_sha256': 'f' * 64}}
        worker = '\n'.join('✓ ' + name + '[' + ('1' * 12 if name == 'canvas' else '2' * 12) + ']: 渲染 60 帧（含 alpha，4 tabs CDP override×60 failed×0, 1ms）' for name in ('canvas', 'dom'))
        for name, roles in release.REQUIRED_FREEZE_STEPS.items():
            steps = []
            for role, code in roles:
                log = self.root / (role + '.log')
                payload = {'integrity_checks_passed': True, 'release_blockers': {}}
                if role == 'package-tamper-rejected': payload = {'error': 'file digest mismatch: artifacts/final.mp4'}
                if role == 'input-tamper-rejected': payload = {'error': 'live input changed: a.mp4'}
                content = worker if role == 'demo-all' else json.dumps(payload)
                if role == 'edit-gfx':
                    content = '✓ dom[' + '2' * 12 + ']: 60 帧已缓存\n' + worker.splitlines()[0].replace('1' * 12, '3' * 12)
                log.write_text(content)
                steps.append({'role': role, 'source_commit': self.commit, 'argv': ['real-command'],
                    'exit_code': code, 'expected_exit': code, 'log': log.name, 'sha256': release.digest(log)})
            observations = {}
            if name == 'local_edit':
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
        self.package_result = {'gates': {name: {'status': 'passed'} for name in
           ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc', 'process', 'input_stability', 'tool_versions', 'media')}}
        verifier = mock.patch('vtx.acceptance.verify', return_value=self.package_result)
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
        self.report('independent_package', lambda report: report['steps'].pop())
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
        with mock.patch('vtx.acceptance.verify', return_value=result), mock.patch.object(release, 'observed_cdp', return_value='cdp'):
            self.verify()
            result['gates']['qc']['status'] = 'unknown'
            with self.assertRaisesRegex(release.ReleaseError, 'qc'): self.verify()

    def test_public_summary_never_copies_environment_paths_or_logs(self):
        package = self.root / 'package'; package.mkdir(exist_ok=True)
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

    def test_rc_accepted_but_unsupported_tag_rejected(self):
        self.assertEqual(release.tag_argument('v2.1.0-rc.1'), 'v2.1.0-rc.1')
        for tag in ('2.1.0', 'v2.1.0-rc.0', 'main'):
            with self.assertRaises(Exception): release.tag_argument(tag)


if __name__ == '__main__': unittest.main()
