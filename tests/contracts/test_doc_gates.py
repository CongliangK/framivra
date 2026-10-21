"""Required document-gate aggregation, using isolated executable fixtures."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which('bash') or 'bash'
LEAVES = (
    ('docs_smoke', 'docs/docs_smoke.sh'),
    ('doc_budget', 'docs/verify_doc_budget.py'),
    ('repeat_anchors', 'docs/verify_repeat_anchors.py'),
    ('charter_budget', 'docs/verify_charter_budget.py'),
    ('catalog_check', 'docs/verify_manifest_catalog.py'),
    ('version_sync', 'docs/verify_version_sync.py'),
    ('contract_surface', 'docs/verify_contract_surface.py'),
)


class RequiredDocumentGates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='doc-gates-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scripts = self.root / 'scripts'
        self.scripts.mkdir()
        (self.scripts / 'docs').mkdir()
        self.gate = self.scripts / 'doc_gates.sh'
        self.gate.write_text((ROOT / 'scripts/doc_gates.sh').read_text(encoding='utf-8'), encoding='utf-8')
        self.populate()

    def populate(self):
        for index, (_, filename) in enumerate(LEAVES, 1):
            path = self.scripts / filename
            message = 'executed-%s' % index
            path.write_text(('echo %s\n' if filename.endswith('.sh') else
                             'print("%s")\n') % message)
            # Interpreter execution intentionally does not require an executable bit.
            path.chmod(0o600)

    def run_gate(self, encoding_env=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        if encoding_env is not None:
            env.pop('PYTHONUTF8', None)
            env.pop('PYTHONIOENCODING', None)
            env.update(encoding_env)
        result = subprocess.run([BASH, str(self.gate)], cwd=self.root,
                                env=env, text=True, encoding='utf-8', stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=15)
        lines = re.findall(r'^leaf_\d+:.*$', result.stdout, re.MULTILINE)
        self.assertEqual(len(lines), 7, result.stdout)
        return result

    def assert_summary(self, result, passed, failed):
        self.assertIn('registry=7 output=7 header=7 对账=OK; '
                      'PASS=%s FAIL=%s SKIP=0' % (passed, failed), result.stdout)

    def run_leaf(self, filename, snapshot):
        path = self.root / 'snapshot.json'
        path.write_text(json.dumps(snapshot), encoding='utf-8')
        leaf = self.scripts / 'docs' / filename
        leaf.write_bytes((ROOT / 'scripts/docs' / filename).read_bytes())
        return subprocess.run(
            [sys.executable, '-X', 'utf8', str(leaf),
             '--snapshot', str(path)], cwd=self.root,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True, text=True, encoding='utf-8', timeout=15)

    def test_repeat_anchor_rejects_invalid_expected_and_unavailable_count(self):
        source = self.root / 'anchors.txt'
        source.write_text('needle\nother\nneedle\n', encoding='utf-8')
        missing = self.root / 'missing.txt'
        cases = ((missing, 'needle', -1, 1), (missing, 'needle', 0, 1),
                 (source, 'needle', False, 1), (source, 'needle', '2', 1),
                 (source, 'needle', 2, 0), (source, 'absent', 0, 0),
                 (source, 'needle', 1, 1), (Path('-'), 'needle', 0, 1),
                 (source, '-needle', 0, 0))
        for path, word, expected, rc in cases:
            with self.subTest(path=path.name, word=word, expected=expected):
                result = self.run_leaf('verify_repeat_anchors.py', {
                    'version': 1, 'repeat_anchors': [
                        {'word': word, 'files': [str(path)], 'expected': [expected]}]})
                self.assertEqual(result.returncode, rc, result.stdout + result.stderr)
                if rc:
                    self.assertIn('L3 FAIL:', result.stdout)
                    self.assertNotIn('== expected OK', result.stdout)

    def test_required_leaf_work_cannot_be_empty(self):
        for filename, snapshot in (
                ('verify_repeat_anchors.py', {'version': 1, 'repeat_anchors': []}),
                ('verify_repeat_anchors.py', {'version': 1, 'repeat_anchors': [
                    {'word': 'needle', 'files': [], 'expected': []}]}),
                ('verify_charter_budget.py', {'version': 1, 'budget_items': []})):
            with self.subTest(filename=filename, snapshot=snapshot):
                result = self.run_leaf(filename, snapshot)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('nonempty', result.stdout + result.stderr)
                self.assertNotIn('== expected OK', result.stdout)

    def test_repeat_anchor_distinguishes_grep_result_from_execution_error(self):
        module = runpy.run_path(str(ROOT / 'scripts/docs/verify_repeat_anchors.py'))
        cases = ((0, b'2\n', 2), (1, b'0\n', 0), (0, b'', None),
                 (1, b'', None), (0, b'-1\n', None), (0, b'bad\n', None),
                 (0, b'0\n', None), (1, b'2\n', None), (2, b'0\n', None),
                 (0, b'\xff\n', None))
        for rc, stdout, expected in cases:
            with self.subTest(rc=rc, stdout=stdout), contextlib.redirect_stderr(io.StringIO()):
                with mock.patch('subprocess.run', return_value=subprocess.CompletedProcess(
                        ['grep'], rc, stdout=stdout, stderr=b'failure')):
                    self.assertEqual(module['live_count']('needle', 'file'), expected)
        with mock.patch('subprocess.run', side_effect=OSError('spawn failed')), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(module['live_count']('needle', 'file'))

    def test_charter_budget_rejects_nonfinite_numbers_without_changing_thresholds(self):
        base = {'id': 'demo', 'unit': 'word', 'warning': 8, 'ceiling': 10, 'measured': 7}
        for field in ('warning', 'ceiling', 'measured'):
            for value in (float('nan'), float('inf'), -float('inf'), True):
                with self.subTest(field=field, value=value):
                    result = self.run_leaf('verify_charter_budget.py', {
                        'version': 1, 'budget_items': [dict(base, **{field: value})]})
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn('must be a finite number', result.stderr)
        for measured, rc, marker in ((7, 0, 'OK'), (9.5, 0, 'changming=1'),
                                     (10, 0, 'changming=1'), (10.5, 1, 'over=1')):
            with self.subTest(measured=measured):
                result = self.run_leaf('verify_charter_budget.py', {
                    'version': 1, 'budget_items': [dict(base, measured=measured)]})
                self.assertEqual(result.returncode, rc, result.stdout + result.stderr)
                self.assertIn(marker, result.stdout)
        huge = 10 ** 400
        result = self.run_leaf('verify_charter_budget.py', {'version': 1, 'budget_items': [
            dict(base, warning=huge - 1, ceiling=huge + 1, measured=huge)]})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('changming=1', result.stdout)

    def test_all_seven_execute_without_executable_bits(self):
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_summary(result, 7, 0)
        for index in range(1, 8):
            self.assertIn('executed-%s' % index, result.stdout)

    def test_each_missing_required_leaf_fails_and_other_leaves_run(self):
        for index, (name, filename) in enumerate(LEAVES, 1):
            with self.subTest(leaf=name):
                self.populate()
                (self.scripts / filename).unlink()
                result = self.run_gate()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('leaf_%s: %s rc=127 FAIL reason=target-missing' %
                              (index, name), result.stdout)
                self.assert_summary(result, 6, 1)
                for other in set(range(1, 8)) - {index}:
                    self.assertIn('executed-%s' % other, result.stdout)

    def test_all_missing_cannot_pass_with_zero_execution(self):
        for _, filename in LEAVES:
            (self.scripts / filename).unlink()
        result = self.run_gate()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assert_summary(result, 0, 7)
        self.assertNotIn('executed-', result.stdout)

    def test_directory_is_not_a_required_script(self):
        path = self.scripts / LEAVES[0][1]
        path.unlink()
        path.mkdir()
        result = self.run_gate()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assert_summary(result, 6, 1)

    def test_each_empty_required_leaf_fails(self):
        for index, (name, filename) in enumerate(LEAVES, 1):
            with self.subTest(leaf=name):
                self.populate()
                (self.scripts / filename).write_text('')
                result = self.run_gate()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('leaf_%s: %s rc=126 FAIL reason=target-empty' %
                              (index, name), result.stdout)
                self.assert_summary(result, 6, 1)

    def test_leaf_failure_preserves_return_code_and_continues(self):
        (self.scripts / LEAVES[0][1]).write_text('exit 9\n')
        result = self.run_gate()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('leaf_1: docs_smoke rc=9 FAIL', result.stdout)
        self.assert_summary(result, 6, 1)
        self.assertIn('executed-7', result.stdout)

    def test_optional_warning_and_skip_text_remain_successful(self):
        (self.scripts / LEAVES[0][1]).write_text(
            'echo "WARNING optional smoke unavailable"\n'
            'echo "SKIP optional environment check"\nexit 0\n')
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_summary(result, 7, 0)

    def test_header_reconciliation_remains_fail_closed(self):
        self.gate.write_text(self.gate.read_text(encoding='utf-8').replace('# LEAVES=7', '# LEAVES=8'), encoding='utf-8')
        result = self.run_gate()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('对账=MISMATCH', result.stdout)

    def test_aggregate_and_standalone_smoke_own_utf8_boundaries(self):
        fixture = self.scripts / 'unicode_help.py'
        fixture.write_text('import sys\nassert sys.flags.utf8_mode == 1\nprint("中文 ⚠")\n', encoding='utf-8')
        for _, filename in LEAVES[1:]:
            (self.scripts / filename).write_text(fixture.read_text(encoding='utf-8'), encoding='utf-8')
        (self.scripts / 'docs/docs_smoke.sh').write_text(
            (ROOT / 'scripts/docs/docs_smoke.sh').read_text(encoding='utf-8'), encoding='utf-8')
        (self.root / 'README.md').write_text('```bash\npython3 scripts/unicode_help.py\n```\n', encoding='utf-8')
        for settings in ({}, {'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'cp1252'}):
            with self.subTest(settings=settings):
                result = self.run_gate(settings)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assert_summary(result, 7, 0)
                self.assertIn('中文 ⚠', result.stdout)
                env = dict(os.environ)
                env.pop('PYTHONUTF8', None); env.pop('PYTHONIOENCODING', None)
                env.update(settings)
                standalone = subprocess.run([BASH, str(self.scripts / 'docs/docs_smoke.sh')],
                    cwd=self.root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
                self.assertEqual(standalone.returncode, 0, standalone.stdout + standalone.stderr)
                self.assertIn('--help scripts', standalone.stdout)
                self.assertNotIn('compile ', standalone.stdout)


if __name__ == '__main__':
    unittest.main()
