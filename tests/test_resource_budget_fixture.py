"""Retained evidence harness checks; synthetic work only, no signals or GPU."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/resource_budget_fixture.py'


@unittest.skipUnless(sys.platform == 'linux', 'fixture requires Linux')
class FixtureTests(unittest.TestCase):
    def invoke(self, output):
        return subprocess.run([sys.executable, '-B', str(SCRIPT), '--output', str(output)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_fresh_run_and_repeat_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / 'evidence'
            result = self.invoke(output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((output / 'result.json').read_text())['result'], 'PASS')
            config = json.loads((output / 'config.json').read_text())
            self.assertFalse(config['physical_peak_claim'])
            for number in range(config['workers']):
                rows = [json.loads(line) for line in
                        (output / f'project-{number}' / 'events.jsonl').read_text().splitlines()]
                self.assertEqual(sum(row['event'] == 'acquired' for row in rows),
                                 config['iterations_per_worker'])
            original = (output / 'result.json').read_bytes()
            self.assertNotEqual(self.invoke(output).returncode, 0)
            self.assertEqual((output / 'result.json').read_bytes(), original)

    def test_bad_destinations_do_not_write(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent)
            target = root / 'target'
            target.mkdir()
            file = root / 'file'
            file.write_text('sentinel')
            link = root / 'link'
            link.symlink_to(target, target_is_directory=True)
            dangling = root / 'dangling'
            dangling.symlink_to(root / 'absent')
            for output in (file, target, link, dangling, link / 'new',
                           root / 'missing' / 'new', link / '..' / 'new'):
                with self.subTest(output=output):
                    self.assertNotEqual(self.invoke(output).returncode, 0)
            self.assertEqual(file.read_text(), 'sentinel')
            self.assertEqual(list(target.iterdir()), [])
            self.assertFalse((root / 'new').exists())
            self.assertFalse((root / 'absent').exists())

    def test_failure_keeps_partial_evidence(self):
        spec = importlib.util.spec_from_file_location('budget_fixture', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        previous = os.open('.', os.O_RDONLY | os.O_DIRECTORY)
        try:
            with tempfile.TemporaryDirectory() as parent:
                output = Path(parent) / 'failed'
                def fail(out, events):
                    module.write_json('config.json', {'fixture': 'injected failure'})
                    module.record(events, 'partial')
                    raise RuntimeError('injected')
                with mock.patch.object(sys, 'argv', [str(SCRIPT), '--output', str(output)]), \
                        mock.patch.object(module, 'run', side_effect=fail), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(module.main(), 1)
                self.assertTrue((output / 'config.json').exists())
                rows = [json.loads(line) for line in (output / 'events.jsonl').read_text().splitlines()]
                self.assertEqual([row['event'] for row in rows], ['partial', 'failure'])
                self.assertEqual(json.loads((output / 'result.json').read_text())['result'], 'FAIL')
                os.fchdir(previous)
        finally:
            os.fchdir(previous)
            os.close(previous)


if __name__ == '__main__':
    unittest.main()
