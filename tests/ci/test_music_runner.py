"""Music pytest is additive to the legacy full-discovery shell runner."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'POSIX bash runner')
class MusicRunnerTests(unittest.TestCase):
    def run_runner(self, unittest_rc=0, pytest_rc=0):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'scripts').mkdir()
            shutil.copyfile(ROOT / 'scripts/run_all_tests.sh', root / 'scripts/run_all_tests.sh')
            for directory in ('tests', 'apps/example/tests', 'plugins/example/tests',
                              'templates/example/tests', 'apps/bgm/bgm_gen/tests'):
                (root / directory).mkdir(parents=True, exist_ok=True)
            binary = root / 'bin'
            binary.mkdir()
            python = binary / 'python3'
            python.write_text(
                '#!/bin/sh\n'
                'printf "%s\\n" "$*" >> "$CALLS"\n'
                'case "$*" in\n'
                '  "-m pytest "*) echo "music pytest: 1 passed, 1 skipped"; '
                'exit "$PYTEST_RC";;\n'
                '  "-m unittest "*) exit "$UNITTEST_RC";;\n'
                '  *) exit 99;;\n'
                'esac\n', encoding='utf-8')
            python.chmod(0o755)
            calls = root / 'calls.txt'
            result = subprocess.run(
                ['bash', str(root / 'scripts/run_all_tests.sh')],
                env={**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                     'CALLS': str(calls), 'UNITTEST_RC': str(unittest_rc),
                     'PYTEST_RC': str(pytest_rc)},
                text=True, capture_output=True, check=False)
            return result, calls.read_text(encoding='utf-8').splitlines()

    def test_preserves_every_unittest_batch_and_adds_pytest(self):
        result, calls = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [
            '-m unittest discover -s tests -t . -v',
            '-m unittest discover -s apps/example/tests -t apps/example -v',
            '-m unittest discover -s plugins/example/tests -t plugins/example -v',
            '-m unittest discover -s templates/example/tests -t templates/example -v',
            '-m pytest apps/bgm/bgm_gen/tests -ra',
        ])
        self.assertIn('1 passed, 1 skipped', result.stdout)

    def test_music_failure_or_empty_collection_is_nonzero(self):
        for code in (1, 4, 5):
            with self.subTest(pytest_exit=code):
                result, calls = self.run_runner(pytest_rc=code)
                self.assertEqual(result.returncode, code)
                self.assertEqual(len(calls), 5)

    def test_successful_music_does_not_mask_unittest_failure(self):
        result, calls = self.run_runner(unittest_rc=2)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(calls), 5)


if __name__ == '__main__':
    unittest.main()
