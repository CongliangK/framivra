"""Opt-in tiny synthetic plumbing tests, never a real Mac/input qualification."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock

from experiments.perf_frame_source import real_fixture as runner
from experiments.perf_frame_source.measure import png


class TestReplayFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = self.root / 'original'
        self.source = self.original / 'cache' / 'gfx'
        self.source.mkdir(parents=True)
        self.specs = {
            'fixture-first': dict(frames=6, width=8, height=4, rgb=(2, 3), small=(1, 5)),
            'fixture-second': dict(frames=5, width=8, height=4, rgb=(1, 3), small=(0, 4)),
        }
        summary, inventory = [], []
        for page, spec in self.specs.items():
            directory = self.source / page
            directory.mkdir()
            hashes = []
            for frame in range(spec['frames']):
                data = png(spec['width'], spec['height'], frame,
                           rgb=spec['rgb'][0] <= frame < spec['rgb'][1])
                filename = 'f%04d.png' % frame
                (directory / filename).write_bytes(data)
                hashes.append(runner.sha_bytes(data))
                inventory.append(dict(page=page, filename=filename, bytes=len(data), sha256=hashes[-1]))
            runner.write_json(directory / 'complete.json', dict(schema=1, key=page,
                frames=spec['frames'], width=spec['width'], height=spec['height'], sha256=hashes,
                environment=dict(status='input-bound', policy='synthetic test only')))
            summary.append(dict(page=page, frame_source=None, frames=spec['frames'],
                width=spec['width'], height=spec['height'],
                complete_sha256=runner.file_hash(directory / 'complete.json')))
        documents = dict(inventory=inventory, summary=dict(pages=summary),
                         bound_inventory=[], historical_environment=dict(policy='synthetic test'))
        paths, hashes = {}, {}
        for name, value in documents.items():
            paths[name] = self.root / (name + '.json')
            runner.write_json(paths[name], value)
            hashes[name] = runner.file_hash(paths[name])
        binding = self.original / 'binding.json'
        binding.write_text('{"synthetic":true}\n')
        self.args = argparse.Namespace(**{k: str(v) for k, v in paths.items()},
            baseline_repo=os.environ.get('VTX_FIXTURE_BASELINE', ''),
            candidate_repo=os.environ.get('VTX_FIXTURE_CANDIDATE', ''),
            original_project_root=str(self.original), source_root=str(self.source),
            binding=str(binding), binding_sha256=runner.file_hash(binding), protected_file=[],
            ffmpeg=os.environ.get('VTX_TEST_FFMPEG', ''), output=str(self.root / 'run'))
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(runner.DOCUMENTS, hashes, clear=True).start()
        mock.patch.dict(runner.PAGES, self.specs, clear=True).start()
        mock.patch.object(runner, 'current_environment', return_value=dict(
            ffmpeg=self.args.ffmpeg, policy='TEST: synthetic Linux fixture, not Mac qualification')).start()

    def require_tools(self):
        if not all((self.args.baseline_repo, self.args.candidate_repo, self.args.ffmpeg)):
            self.fail('explicit fixture tests require VTX_FIXTURE_BASELINE, VTX_FIXTURE_CANDIDATE, VTX_TEST_FFMPEG')

    def test_pinned_arms_tiny_real_process_flow_and_hierarchical_receipts(self):
        self.require_tools()
        before = {str(p): (runner.file_hash(p), runner.stat_token(p))
                  for p in self.original.rglob('*') if p.is_file()}
        output = runner.replay(self.args)
        result = runner.read_json(output / 'result.json')
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(len(result['runs']), 16)
        self.assertEqual(len(result['quality']), 4)
        self.assertTrue((output / 'small-stage-passed.json').is_file())
        self.assertEqual([r['arm'] for r in result['runs']], list('ABABABABBABAABAB'))
        self.assertEqual(result['repos']['A']['commit'], runner.BASE[0])
        self.assertEqual(result['repos']['B']['commit'], runner.CANDIDATE[0])
        self.assertEqual(before, {str(p): (runner.file_hash(p), runner.stat_token(p))
                                  for p in self.original.rglob('*') if p.is_file()})
        manifest = runner.read_json(output / 'MANIFEST.json')
        self.assertTrue(manifest['complete'])
        covered = set()
        for receipt in manifest['files']:
            path = output / receipt['path']
            self.assertEqual(runner.file_hash(path), receipt['sha256'])
            self.assertEqual(path.stat().st_size, receipt['bytes'])
            covered.add(path)
            if path.name == 'files.json':
                for item in runner.read_json(path):
                    artifact = path.parent / item['path']
                    self.assertEqual(runner.file_hash(artifact), item['sha256'])
                    self.assertEqual(artifact.stat().st_size, item['bytes'])
                    covered.add(artifact)
        self.assertEqual(covered, {p for p in output.rglob('*') if p.is_file()} - {output / 'MANIFEST.json'})

    def test_small_quality_failure_does_not_start_full_and_keeps_receipt(self):
        self.require_tools()
        with mock.patch.object(runner, 'ffmpeg_hash', side_effect=ValueError('synthetic quality failure')):
            with self.assertRaisesRegex(ValueError, 'synthetic quality failure'):
                runner.replay(self.args)
        output = Path(self.args.output)
        result = runner.read_json(output / 'result.json')
        self.assertEqual(result['status'], 'FAILED_INCOMPLETE')
        self.assertFalse((output / 'full').exists())
        self.assertFalse((output / 'small-stage-passed.json').exists())
        self.assertFalse(runner.read_json(output / 'MANIFEST.json')['complete'])
        self.assertEqual(len(result['runs']), 2)

    def assert_preflight_failure(self, error_type, phase, action):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(error_type):
            action()
        output = Path(self.args.output)
        result = runner.read_json(output / 'result.json')
        self.assertEqual(result['status'], 'FAILED_INCOMPLETE')
        self.assertEqual(result['phase'], phase)
        self.assertFalse(result['preflight_completed'])
        self.assertEqual(result['runs'], [])
        self.assertEqual(result['processes'], [])
        self.assertFalse((output / 'small').exists())
        self.assertFalse((output / 'full').exists())
        self.assertFalse(runner.read_json(output / 'MANIFEST.json')['complete'])
        attempt = json.loads(stderr.getvalue().splitlines()[-1])
        self.assertTrue(attempt['result_receipt_written'])
        self.assertEqual(attempt['result_path'], str(output / 'result.json'))
        return result, attempt

    def test_preflight_input_pin_failure_has_owned_receipts(self):
        Path(self.args.inventory).write_text('[]')
        self.assert_preflight_failure(ValueError, 'preflight:documents', lambda: runner.replay(self.args))

    def test_preflight_binding_pin_failure_has_owned_receipts(self):
        self.args.binding_sha256 = '0' * 64
        self.assert_preflight_failure(ValueError, 'preflight:binding', lambda: runner.replay(self.args))

    def test_preflight_repository_drift_has_owned_receipts(self):
        with mock.patch.object(runner, 'verify_repo', side_effect=ValueError('synthetic repository drift')):
            self.assert_preflight_failure(ValueError, 'preflight:repositories', lambda: runner.replay(self.args))

    def test_preflight_tool_version_failure_has_owned_receipts(self):
        self.require_tools()
        with mock.patch.object(runner, 'current_environment', side_effect=ValueError('synthetic FFmpeg version mismatch')):
            result, _ = self.assert_preflight_failure(ValueError, 'preflight:environment', lambda: runner.replay(self.args))
        self.assertIsNone(result['current_environment'])

    def test_preflight_cancellation_has_receipt_without_continuing_guards(self):
        self.require_tools()
        with mock.patch.object(runner, 'current_environment', side_effect=KeyboardInterrupt), \
                mock.patch.object(runner, 'source_guards') as guards:
            result, attempt = self.assert_preflight_failure(KeyboardInterrupt, 'preflight:environment', lambda: runner.replay(self.args))
        guards.assert_not_called()
        self.assertTrue(result['cancelled'])
        self.assertEqual(attempt['status'], 'CANCELLED')
        self.assertEqual(result['original_guard_status'], 'UNRUN: cancellation')

    def test_argument_rejection_has_structured_stderr_without_writes(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            runner.main([])
        self.assertEqual(error.exception.code, 2)
        attempt = json.loads(stderr.getvalue().splitlines()[-1])
        self.assertEqual(attempt['phase'], 'arguments')
        self.assertFalse(attempt['result_receipt_written'])
        self.assertFalse(Path(self.args.output).exists())

    def test_existing_owned_output_is_unchanged_and_stderr_records_no_safe_root(self):
        output = Path(self.args.output)
        output.mkdir()
        sentinel = output / 'sentinel.txt'
        sentinel.write_bytes(b'ordinary existing output')
        before = runner.stat_token(sentinel), sentinel.read_bytes()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaisesRegex(ValueError, 'previously absent'):
            runner.replay(self.args)
        attempt = json.loads(stderr.getvalue())
        self.assertEqual(attempt['phase'], 'output-boundary')
        self.assertFalse(attempt['result_receipt_written'])
        self.assertIsNone(attempt['result_path'])
        self.assertEqual(before, (runner.stat_token(sentinel), sentinel.read_bytes()))
        self.assertEqual(list(output.iterdir()), [sentinel])

    def test_small_decode_error_stderr_stops_before_full_even_with_complete_hashes(self):
        self.require_tools()
        original_run = runner.Budget.run
        def run(budget, command, *args, **kwargs):
            if command[0] == self.args.ffmpeg:
                rows = '#tb 0: 1/30\n#dimensions 0: 8x4\n' + ''.join(
                    '0, %d, %d, 1, 128, %s\n' % (i, i, 'a' * 64) for i in range(4))
                code = 'import sys;sys.stdout.write(%r);sys.stderr.write("decode error\\n")' % rows
                command = [sys.executable, '-c', code]
            return original_run(budget, command, *args, **kwargs)
        with mock.patch.object(runner.Budget, 'run', new=run), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'emitted diagnostics'):
                runner.replay(self.args)
        output = Path(self.args.output)
        result = runner.read_json(output / 'result.json')
        self.assertEqual(result['status'], 'FAILED_INCOMPLETE')
        self.assertFalse((output / 'full').exists())
        self.assertFalse((output / 'small-stage-passed.json').exists())
        self.assertEqual(result['processes'][-1]['returncode'], 0)
        self.assertIn('emitted diagnostics', result['processes'][-1]['failure'])
        self.assertEqual(len(result['runs']), 2)

    def test_original_eof_change_is_rejected_by_post_guard(self):
        inputs = runner.load_inputs({name: getattr(self.args, name) for name in runner.DOCUMENTS}, self.source)
        (self.source / 'fixture-first' / 'f0006.png').write_bytes(b'extra ordinary file')
        with self.assertRaisesRegex(ValueError, 'EOF changed'):
            runner.source_guards(inputs, {})

    def test_document_digest_change_fails_before_copy(self):
        paths = {name: getattr(self.args, name) for name in runner.DOCUMENTS}
        Path(self.args.inventory).write_text('[]')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            runner.load_inputs(paths, self.source)


if __name__ == '__main__':
    unittest.main()
