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

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def fixture(self):
        self.write(self.parent / 'source.json', {'head_sha': 'a' * 40, 'expected_head_sha': 'a' * 40, 'clean': True})
        self.write(self.parent / 'runner-result.json', {'return_code': 0})
        (self.parent / 'runner.log').write_text('fixture only\n')
        rounds = []
        for i in range(3):
            for scope in ('gfx', 'chain'):
                rounds.append({'round': i, 'scope': scope})
                for prefix in ('plan', 'golden'):
                    self.write(self.experiment / f'{prefix}-{i}-{scope}.json', {})
                self.write(self.experiment / f'comparison-{i}-{scope}.json',
                           {'ok': True, 'compared_frames': 120, 'hashed_frames': 240})
                for side in ('baseline', 'candidate'):
                    arm = self.experiment / f'round-{i}-{scope}-{side}'
                    for name in packer.ARM:
                        self.write(arm / name, {})
                    (arm / 'media.mp4').write_bytes(b'do not upload')
                    cache = arm / 'cache'; cache.mkdir()
                    (cache / 'f0000.png').write_bytes(b'do not upload')
        self.write(self.experiment / 'summary.json', {'paired_runs_complete': True,
                    'full_issue63_complete': False, 'rounds': rounds})

    def test_complete_finite_bundle_retains_all_receipts_without_media(self):
        self.fixture()
        result = packer.pack(self.parent, self.output)
        self.assertTrue(result['partial_acceptance'])
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
