"""Source/mock tests only; never launch Chrome/media or hosted workflows."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import yaml
from scripts import pack_lifecycle_evidence as packer


class HostedEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parent = self.root / 'input'; self.parent.mkdir()
        self.output = self.root / 'bundle'
        self.experiment = self.parent / 'issue63-fixture'; self.experiment.mkdir()
        # Corpus is synthetic; mock checkout cleanliness only, never media.
        import subprocess
        self.head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        identity = patch.object(packer, 'checkout_identity', return_value=(self.head, True))
        identity.start(); self.addCleanup(identity.stop)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def fixture(self):
        from scripts import bench_lifecycle as bench, gfx_evidence as evidence
        from tests.test_gfx_experiment import raw_observation, manifest
        from tests.test_execution_events import Buffer
        from vtx import events, gfx_runtime
        import hashlib, subprocess, uuid
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        self.write(self.parent / 'source.json', {'head_sha': head, 'expected_head_sha': head, 'clean': True})
        self.write(self.parent / 'runner-result.json', {'return_code': 0})
        (self.parent / 'runner.log').write_text('synthetic records; no pixel acceptance\n')
        template = self.experiment / 'fixture'
        timeline = bench.fixture(template, create_media=False)
        (template / 'media.mp4').write_bytes(b'mock media, not decoded')
        self.write(self.experiment / 'owner.json', {'profile': bench.PROFILE, 'id': str(uuid.uuid4())})
        raw = raw_observation(str(uuid.uuid4())); identity = gfx_runtime.identity(raw)
        env = manifest(identity, str(uuid.uuid4()), []); env['git_sha'] = head
        self.write(self.experiment / 'preflight.json', {'runtime': raw, 'environment': env})
        rounds = []
        for i in range(3):
            for scope in ('gfx', 'chain'):
                plan = bench.make_plan(bench.Ctx(timeline), identity, bench.stable_host(env), scope)
                self.write(self.experiment / f'plan-{i}-{scope}.json', plan)
                runs, summaries = {}, {}
                for side in ('baseline', 'candidate'):
                    arm = self.experiment / f'round-{i}-{scope}-{side}'
                    arm.mkdir(); (arm / 'timeline.json').write_bytes(timeline.read_bytes())
                    run_id = plan['arms'][side]['run_id']
                    raw = raw_observation(run_id); runtime_key = evidence.digest(raw)
                    witness = {'source': 'page-cdp-eval', 'viewport': {'width': 1920, 'height': 1080, 'device_scale': 1}, 'alpha_path': 'opaque'}
                    witness_key = evidence.digest(witness)
                    stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
                    instances = []
                    with execution.task('gfx').scope() as task:
                        for page in range(4):
                            frames = {}
                            for frame in range(30):
                                fields = {'gfx_key': f'{page:012x}', 'frame_index': frame, 'frame_origin': 'rendered',
                                    'png_sha256': 'b' * 64, 'runtime_sha256': runtime_key, 'page_witness_sha256': witness_key}
                                ref = execution.emit('frame_commit', task=task, extensions=fields)
                                frames[f'{frame:04d}'] = {'run_id': run_id, 'origin': 'rendered', 'sha256': 'd' * 64,
                                    'publication_seq': ref['event_id'][1], **{k: fields[k] for k in ('gfx_key', 'png_sha256', 'runtime_sha256', 'page_witness_sha256')}}
                            instances.append({'src': f'page{page}.html', 'dur': 30, 'frame_dir': str(arm / f'cache/{page}'), 'frames': frames})
                    execution.finish()
                    data = stream.getvalue(); (arm / 'events.jsonl').write_text(data)
                    actual_env = manifest(identity, run_id, []); actual_env['git_sha'] = head
                    run = {'schema': 'vtx.gfx_run/1', 'complete': True, 'run_id': run_id, 'plan_sha256': evidence.digest(plan),
                        'input_fingerprint': plan['input_fingerprint'], 'config': plan['arms'][side]['config'],
                        'env_manifest': actual_env, 'env_sha256': evidence.digest(actual_env), 'instances': instances,
                        'producer_binding': {'runtime': {runtime_key: raw}, 'page_witnesses': {witness_key: witness}},
                        'timeline_path': str(arm / 'timeline.json'), 'events_path': str(arm / 'events.jsonl'),
                        'events_sha256': hashlib.sha256(data.encode()).hexdigest(), 'execution_run_id': execution.run_id}
                    runs[side] = run
                    self.write(arm / 'gfx-run.json', run)
                    self.write(arm / 'runtime-raw.json', {str(uuid.uuid4()): raw})
                    self.write(arm / 'command-result.json', {'return_code': 0, 'execution_run_id': execution.run_id, 'wall_seconds': 1.0, 'load_before': [0.1, 0.2, 0.3], 'load_after': [0.2, 0.3, 0.4]})
                    summaries[side] = events.lifecycle_summary(data)
                    self.write(arm / 'lifecycle-summary.json', summaries[side])
                    (arm / 'media.mp4').write_bytes(b'do not upload')
                snapshot = {'meta': {'fps': 30, 'viewport': '1920x1080', 'run_evidence': runs['baseline'],
                    'env_manifest': runs['baseline']['env_manifest']}, 'pages': {}}
                for instance in runs['baseline']['instances']:
                    snapshot['pages'][instance['src']] = {'durs': {'30': {'mode': 'full', 'dur': 30,
                        'frames': {k: v['sha256'] for k, v in instance['frames'].items()}}}}
                self.write(self.experiment / f'golden-{i}-{scope}.json', snapshot)
                self.write(self.experiment / f'comparison-{i}-{scope}.json', {'schema': 'vtx.gfx_evidence_report/1',
                    'ok': True, 'same_version_causal': True, 'compared_frames': 120, 'hashed_frames': 240,
                    'plan_sha256': evidence.digest(plan), 'run_ids': {s: r['run_id'] for s, r in runs.items()},
                    'actual_environments': {s: r['env_manifest'] for s, r in runs.items()},
                    **{k: [] for k in ('different_frames', 'missing_frames', 'unknown_provenance', 'uncovered_instances', 'errors', 'confounders')}})
                rounds.append({'round': i, 'scope': scope, 'ok': True, 'compared_frames': 120,
                    'order': ['baseline', 'candidate'] if i % 2 == 0 else ['candidate', 'baseline'],
                    'lifecycle': summaries, 'wall_seconds': {'baseline': 1.0, 'candidate': 1.0},
                    'scope_gfx_frames_per_second': {'baseline': 120.0, 'candidate': 120.0}, 'paired_delta_seconds': 0.0})
        self.write(self.experiment / 'summary.json', {'profile': bench.PROFILE, 'paired_runs_complete': True,
            'full_issue63_complete': False, 'rounds': rounds, 'summary': {scope: {side: {'median_seconds': 1.0,
            'range_seconds': [1.0, 1.0]} for side in ('baseline', 'candidate')} for scope in ('gfx', 'chain')}})

    def test_complete_finite_bundle_retains_all_receipts_without_media(self):
        self.fixture()
        result = packer.pack(self.parent, self.output)
        self.assertTrue(result['partial_acceptance'], result['errors'])
        self.assertFalse(result['full_issue63_complete'])
        with zipfile.ZipFile(self.output / 'evidence.zip') as archive:
            names = archive.namelist()
            self.assertEqual(sum(n.endswith('/gfx-run.json') for n in names), 12)
            self.assertEqual(sum(n.endswith('/events.jsonl') for n in names), 12)
            self.assertFalse(any(n.endswith(('.mp4', '.png')) for n in names))
        self.assertLess(sum(p.stat().st_size for p in self.output.iterdir()), packer.LIMIT)
        with self.assertRaises(FileExistsError):
            packer.pack(self.parent, self.output)

    def test_missing_receipt_failed_run_and_nonfull_comparison_reject(self):
        self.fixture()
        path = self.experiment / 'round-0-gfx-baseline/gfx-run.json'
        path.unlink()
        self.assertFalse(packer.pack(self.parent, self.output)['partial_acceptance'])
        self.write(path, {})
        self.write(self.parent / 'runner-result.json', {'return_code': 124})
        self.assertFalse(packer.pack(self.parent, self.root / 'timeout')['partial_acceptance'])
        self.write(self.parent / 'runner-result.json', {'return_code': 0})
        self.write(self.experiment / 'comparison-2-chain.json', {'ok': True, 'compared_frames': 119, 'hashed_frames': 238})
        self.assertFalse(packer.pack(self.parent, self.root / 'incomplete')['partial_acceptance'])

    def test_budget_never_trims_and_claims_success(self):
        self.fixture()
        with patch.object(packer, 'LIMIT', 4096):
            result = packer.pack(self.parent, self.output)
        self.assertFalse(result['evidence_complete'])
        self.assertFalse(result['partial_acceptance'])
        self.assertTrue(result['errors'])

    def test_symlink_and_fifo_fail_without_reading_target(self):
        self.fixture()
        log = self.parent / 'runner.log'; log.unlink()
        log.symlink_to('/dev/zero')
        self.assertFalse(packer.pack(self.parent, self.output)['evidence_complete'])
        log.unlink()
        import os
        os.mkfifo(log)
        self.assertFalse(packer.pack(self.parent, self.root / 'fifo')['evidence_complete'])

    def reject_mutations(self, cases):
        self.fixture()
        for index, (relative, mutate) in enumerate(cases):
            path = self.parent / relative
            original = path.read_bytes()
            with self.subTest(record=relative, mutation=index):
                try:
                    changed = mutate(json.loads(original))
                    path.write_bytes(changed if isinstance(changed, bytes) else json.dumps(changed).encode())
                    status = packer.pack(self.parent, self.root / f'mutation-{index}')
                    self.assertFalse(status['partial_acceptance'], relative)
                    self.assertFalse(status['evidence_complete'], relative)
                    self.assertTrue(status['errors'])
                    self.assertTrue((self.root / f'mutation-{index}/evidence.zip').exists())
                finally:
                    path.write_bytes(original)

    def test_source_identity_shapes_and_actual_binding_reject(self):
        self.reject_mutations([
            ('source.json', lambda r: {'clean': True}),
            ('source.json', lambda r: {**r, 'head_sha': 'garbage', 'expected_head_sha': 'garbage'}),
            ('source.json', lambda r: {**r, 'head_sha': 'a' * 40, 'expected_head_sha': 'a' * 40}),
            ('runner-result.json', lambda r: {'return_code': False}),
            ('runner-result.json', lambda r: {'return_code': 0.0}),
            ('issue63-fixture/preflight.json', lambda r: {**r, 'environment': {**r['environment'], 'git_sha': 'a' * 40}}),
        ])

    def test_required_top_fixture_and_profile_bindings_reject(self):
        self.fixture()
        paths = ['owner.json', 'preflight.json', 'fixture/timeline.json'] + [f'fixture/page{i}.html' for i in range(4)]
        for index, relative in enumerate(paths):
            path = self.experiment / relative; data = path.read_bytes(); path.unlink()
            try:
                result = packer.pack(self.parent, self.root / f'missing-{index}')
                self.assertFalse(result['evidence_complete'], relative)
            finally:
                path.write_bytes(data)
        path = self.experiment / 'fixture/page0.html'; path.write_text('changed HTML')
        self.assertFalse(packer.pack(self.parent, self.root / 'changed-html')['partial_acceptance'])

    def test_arm_stream_receipt_and_summary_contradictions_reject(self):
        prefix = 'issue63-fixture/round-0-gfx-baseline/'
        self.reject_mutations([
            (prefix + 'gfx-run.json', lambda r: b''),
            (prefix + 'gfx-run.json', lambda r: {}),
            (prefix + 'command-result.json', lambda r: {**r, 'return_code': 1}),
            (prefix + 'command-result.json', lambda r: {**r, 'execution_run_id': 'bad'}),
            (prefix + 'gfx-run.json', lambda r: {**r, 'events_sha256': '0' * 64}),
            (prefix + 'runtime-raw.json', lambda r: {}),
            (prefix + 'lifecycle-summary.json', lambda r: {**r, 'complete': False}),
            ('issue63-fixture/plan-0-gfx.json', lambda r: {**r, 'input_fingerprint': '0' * 64}),
            ('issue63-fixture/comparison-0-gfx.json', lambda r: {**r, 'run_ids': {}}),
            ('issue63-fixture/golden-0-gfx.json', lambda r: {**r, 'meta': {}}),
            ('issue63-fixture/summary.json', lambda r: {**r, 'summary': {}}),
        ])

    def test_checkout_and_workflow_must_match_clean_source(self):
        self.fixture()
        with patch.object(packer, 'checkout_identity', return_value=(self.head, False)):
            self.assertFalse(packer.pack(self.parent, self.output)['partial_acceptance'])
        with patch.object(packer, 'checkout_identity', return_value=('f' * 40, True)):
            self.assertFalse(packer.pack(self.parent, self.root / 'wrong-checkout')['partial_acceptance'])
        with patch.dict('os.environ', {'EXPECTED_HEAD': 'e' * 40}):
            self.assertFalse(packer.pack(self.parent, self.root / 'wrong-workflow')['partial_acceptance'])

    def test_rehashed_unclosed_stream_still_rejects(self):
        import hashlib
        self.fixture()
        arm = self.experiment / 'round-0-gfx-baseline'
        data = (arm / 'events.jsonl').read_bytes().splitlines(keepends=True)
        truncated = b''.join(data[:-1]); (arm / 'events.jsonl').write_bytes(truncated)
        run = json.loads((arm / 'gfx-run.json').read_text())
        run['events_sha256'] = hashlib.sha256(truncated).hexdigest()
        self.write(arm / 'gfx-run.json', run)
        self.assertFalse(packer.pack(self.parent, self.output)['partial_acceptance'])

    def test_workflow_is_same_repo_head_bound_narrow_and_fail_closed(self):
        path = Path(__file__).resolve().parents[1] / '.github/workflows/lifecycle-63.yml'
        text = path.read_text()
        data = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(set(data['on']), {'pull_request'})
        self.assertEqual(data['permissions'], {'contents': 'read'})
        job = data['jobs']['paired']
        self.assertIn('head.repo.full_name == github.repository', job['if'])
        self.assertIn("startsWith(github.head_ref, 'acceptance/lifecycle-63/')", job['if'])
        self.assertEqual(job['timeout-minutes'], '35')
        self.assertEqual(job['steps'][0]['with']['ref'], '${{ github.event.pull_request.head.sha }}')
        self.assertEqual(job['steps'][0]['with']['persist-credentials'], 'false')
        commands = '\n'.join(step.get('run', '') for step in job['steps'])
        self.assertIn('timeout --signal=TERM --kill-after=10s 30m', commands)
        self.assertIn('--rounds 3 --scope both', commands)
        self.assertNotIn('continue-on-error', text)
        self.assertNotIn('${{ secrets.', text)
        self.assertNotIn('sysctl', commands)
        self.assertNotIn('chmod', commands)
        executable_lines = '\n'.join(line for line in commands.splitlines() if not line.lstrip().startswith('#'))
        self.assertNotIn('--no-sandbox', executable_lines)
        self.assertEqual(job['steps'][-1]['with']['retention-days'], '7')
