"""Regression tests for the historical-fixture review findings.

The bytecode case uses pinned production sources copied to an owned temporary
directory and tiny synthetic PNGs. The decoder cases substitute a controlled
Python child at process launch, retaining the real Budget stdout/stderr checks.
These are author regression tests, not an independent review, a real-input Mac
qualification, or an observation of errors in the historical PNGs.
"""
import importlib.util
import json
import marshal
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from experiments.perf_frame_source import real_fixture as runner
from experiments.perf_frame_source.measure import png


class TemporaryReviewFiles(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='fixture-review-regression-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)


class TestPinnedRuntimeBytecode(TemporaryReviewFiles):
    def test_candidate_source_executes_despite_compatible_baseline_pyc(self):
        candidate_path = os.environ.get('VTX_FIXTURE_CANDIDATE')
        baseline_path = os.environ.get('VTX_FIXTURE_BASELINE')
        if not candidate_path or not baseline_path:
            self.fail('explicit bytecode regression requires VTX_FIXTURE_CANDIDATE and VTX_FIXTURE_BASELINE')
        candidate = runner.verify_repo(candidate_path, runner.CANDIDATE)
        baseline = runner.verify_repo(baseline_path, runner.BASE)
        repo = self.root / 'candidate-copy'
        for name, expected in candidate['modules'].items():
            data = (Path(candidate['path']) / name).read_bytes()
            self.assertEqual(runner.sha_bytes(data), expected)
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        module = repo / 'vtx/frame_source.py'
        candidate_source = module.read_bytes()
        baseline_source = (Path(baseline['path']) / 'vtx/frame_source.py').read_bytes()
        self.assertEqual(runner.sha_bytes(baseline_source), baseline['modules']['vtx/frame_source.py'])
        self.assertNotEqual(candidate_source, baseline_source)
        baseline_code = compile(baseline_source, str(module), 'exec', dont_inherit=True, optimize=0)
        info = module.stat()
        cached = Path(importlib.util.cache_from_source(str(module)))
        cached.parent.mkdir()
        # An ordinary timestamp cache is valid for the unchanged candidate file,
        # while its executable code is compiled from the pinned older source.
        cached.write_bytes(importlib.util.MAGIC_NUMBER
                           + struct.pack('<III', 0, int(info.st_mtime), info.st_size)
                           + marshal.dumps(baseline_code))
        before = {str(path): (runner.file_hash(path), runner.stat_token(path))
                  for path in repo.rglob('*') if path.is_file()}

        # Establish that this is a usable cache, not a malformed/ignored pyc.
        probe = ('import inspect, json, sys; sys.path.insert(0, sys.argv[1]); '
                 'from vtx.frame_source import PngSequence; '
                 'print(json.dumps(str(inspect.signature(PngSequence._validated))))')
        ordinary = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe, str(repo)],
                                  capture_output=True, text=True, timeout=30, cwd=self.root)
        self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
        self.assertEqual(ordinary.stderr, '')
        self.assertEqual(json.loads(ordinary.stdout), '(self, start, stop, *, through_eof=False)')

        case = self.root / 'case'
        (case / 'source').mkdir(parents=True)
        rows = []
        for index in range(3):
            data = png(8, 4, index, rgb=index == 1)
            filename = 'f%04d.png' % index
            (case / 'source' / filename).write_bytes(data)
            rows.append(dict(filename=filename, sha256=runner.sha_bytes(data),
                             rgb=index == 1, local=index, original=index, bytes=len(data)))
        (case / 'origin-complete.json').write_text('{"synthetic": true}\n', encoding='utf-8')
        fixture = dict(schema=runner.SCHEMA, identity='synthetic-bytecode-regression',
                       width=8, height=4, fps=30, frames=rows,
                       origin_marker_sha256=runner.file_hash(case / 'origin-complete.json'))
        runner.write_json(case / 'fixture.json', fixture)
        plan = case / 'worker-plan.json'
        runner.write_json(plan, dict(repo=str(repo), modules=candidate['modules'],
                                    fixture_sha256=runner.file_hash(case / 'fixture.json')))
        process = subprocess.run([sys.executable, '-I', '-S', '-B',
                                  str(Path(runner.__file__).resolve()), '--worker', str(plan)],
                                 capture_output=True, text=True, timeout=30, cwd=case)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, '')
        record = json.loads(process.stdout)
        self.assertEqual(record['module_sha256'], candidate['modules']['vtx/frame_source.py'])
        self.assertEqual(record['fixture_identity'], fixture['identity'])
        self.assertEqual(record['runtime']['loader'], 'pinned-source-only')
        self.assertEqual(record['runtime']['validated_signature'],
                         '(self, start, stop, *, through_eof=False, _previous=())')
        loaded = record['runtime']['loaded_sources']
        self.assertTrue({'vtx', 'vtx.frame_source', 'vtx.gfx_cli', 'vtx.fstxn', 'vtx.composite'} <= loaded.keys())
        for name, evidence in loaded.items():
            with self.subTest(module=name):
                path = Path(evidence['path'])
                relative = str(path.relative_to(repo))
                self.assertIn(relative, candidate['modules'])
                self.assertEqual(evidence['source_sha256'], candidate['modules'][relative])
                compiled = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True, optimize=0)
                self.assertEqual(evidence['code_sha256'], runner.sha_bytes(marshal.dumps(compiled)))
        self.assertNotEqual(loaded['vtx.frame_source']['code_sha256'],
                            runner.sha_bytes(marshal.dumps(baseline_code)))
        self.assertEqual(set(record['phases']), {'cold', 'warm'})
        cold, warm = record['phases']['cold'], record['phases']['warm']
        self.assertEqual(cold['canonical'], warm['canonical'])
        self.assertEqual(cold['marker'], warm['marker'])
        self.assertEqual(cold['fixture_check_calls'], 2)
        self.assertEqual(warm['fixture_check_calls'], 2)
        self.assertEqual(len(record['canonical']), len(rows))
        self.assertEqual(module.read_bytes(), candidate_source)
        self.assertEqual(before, {str(path): (runner.file_hash(path), runner.stat_token(path))
                                  for path in repo.rglob('*') if path.is_file()})
        self.assertEqual(list(repo.rglob('*.pyc')), [cached])


class TestDecoderDiagnostics(TemporaryReviewFiles):
    def controlled_decode(self, diagnostic):
        framehash = ('#format: frame checksums\n#tb 0: 1/30\n#dimensions 0: 2x2\n'
                     + '0, 0, 0, 1, 16, ' + 'a' * 64 + '\n'
                     + '0, 1, 1, 1, 16, ' + 'b' * 64 + '\n')
        script = 'import sys; sys.stdout.write(%r); sys.stderr.write(%r)' % (framehash, diagnostic)
        original_popen = subprocess.Popen
        launched = []

        def launch(command, *args, **kwargs):
            if command[0] == 'controlled-decoder-for-test':
                self.assertEqual(command[1:4], ['-nostdin', '-v', 'error'])
                launched.append(command)
                command = [sys.executable, '-I', '-S', '-B', '-c', script]
            return original_popen(command, *args, **kwargs)

        budget = runner.Budget(self.root)
        fixture = dict(width=2, height=2, frames=[{}, {}])
        return mock.patch.object(runner.subprocess, 'Popen', side_effect=launch), budget, fixture, launched

    def test_zero_exit_complete_framehash_with_error_stderr_fails_and_retains_evidence(self):
        diagnostic = '[png] controlled recoverable decode error\n'
        patch, budget, fixture, launched = self.controlled_decode(diagnostic)
        with patch, self.assertRaisesRegex(ValueError, 'FFmpeg -v error emitted diagnostics despite exit 0'):
            runner.ffmpeg_hash('controlled-decoder-for-test', self.root / 'f%04d.png',
                               self.root, 'decode', fixture, budget)
        self.assertEqual(len(launched), 1)
        self.assertEqual(runner.parse_framehash(self.root / 'decode.stdout', 2, 2, 2),
                         ['a' * 64, 'b' * 64])
        self.assertEqual((self.root / 'decode.stderr').read_text(), diagnostic)
        self.assertEqual(len(budget.processes), 1)
        receipt = budget.processes[0]
        self.assertEqual(receipt['returncode'], 0)
        self.assertTrue(receipt['require_empty_stderr'])
        self.assertIn('diagnostics', receipt['failure'])
        for channel in ('stdout', 'stderr'):
            self.assertEqual(receipt[channel + '_sha256'],
                             runner.file_hash(self.root / receipt[channel]))

    def test_zero_exit_complete_framehash_without_diagnostics_succeeds(self):
        patch, budget, fixture, launched = self.controlled_decode('')
        with patch:
            hashes = runner.ffmpeg_hash('controlled-decoder-for-test', self.root / 'f%04d.png',
                                        self.root, 'decode', fixture, budget)
        self.assertEqual(hashes, ['a' * 64, 'b' * 64])
        self.assertEqual(len(launched), 1)
        self.assertEqual((self.root / 'decode.stderr').read_bytes(), b'')
        self.assertEqual(budget.processes[0]['returncode'], 0)
        self.assertTrue(budget.processes[0]['require_empty_stderr'])
        self.assertIsNone(budget.processes[0]['failure'])


if __name__ == '__main__':
    unittest.main()
