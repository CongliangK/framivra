"""Execute the Bash bootstrap without network or native package installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

BOOTSTRAP = Path(__file__).resolve().parents[2] / 'scripts/bootstrap.sh'
SCRIPTS = BOOTSTRAP.parent
COMMIT = 'a' * 40


@unittest.skipUnless(os.name == 'posix', 'UNRUN: Bash bootstrap is for POSIX hosts')
class BootstrapPosix(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='framivra-bash-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.scratch = self.base / 'scratch'
        self.scratch.mkdir()
        self.log = self.base / 'requests.jsonl'
        self.root = self.base / 'managed'
        self.bash = shutil.which('bash')
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        TMPDIR=str(self.scratch), FRAMIVRA_TEST_LOG=str(self.log),
                        FRAMIVRA_TEST_SCRIPTS=str(SCRIPTS))
        self.executable('uname', '#!/bin/sh\nprintf "%s\\n" "${FRAMIVRA_TEST_SYSTEM:-Linux}"\n')
        (self.bin / 'python3').symlink_to(sys.executable)
        self.executable('curl', '#!' + sys.executable + '\n' + '''import json,os,pathlib,sys
url = next(x for x in sys.argv[1:] if x.startswith('https://'))
with open(os.environ['FRAMIVRA_TEST_LOG'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps(dict(url=url, args=sys.argv[1:])) + '\\n')
if url.endswith('/commits/main'):
    print(json.dumps(dict(sha='a' * 40)))
elif url.endswith('/Homebrew/install/HEAD/install.sh'):
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(os.environ['FRAMIVRA_TEST_BREW_SCRIPT'])
else:
    name = url.rsplit('/', 1)[-1]
    data = (pathlib.Path(os.environ['FRAMIVRA_TEST_SCRIPTS']) / name).read_text(encoding='utf-8')
    if name == 'install_source.py':
        # Load every real sibling import/definition but do not execute install().
        data = ("if __name__ == '__main__':\\n"
                " import json,runpy,sys\\n"
                " ns=runpy.run_path(__file__,run_name='installer_probe')\\n"
                " import shutil\\n"
                " print(json.dumps(dict(argv=sys.argv[1:],bundle=ns['BUNDLE'],brew=shutil.which('brew'))))\\n"
                " raise SystemExit(0)\\n") + data
    destination = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])
    destination.write_text(data, encoding='utf-8')
''')

    def executable(self, name, content):
        target = self.bin / name
        target.write_text(content, encoding='utf-8')
        target.chmod(0o755)

    def run_bootstrap(self, *args):
        return subprocess.run([self.bash, str(BOOTSTRAP), *args], env=self.env,
                              capture_output=True, text=True, timeout=60,
                              start_new_session=True)

    def test_plan_never_fetches_or_creates_the_installation_root(self):
        result = self.run_bootstrap('--plan', '--network', 'official', '--root', str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('No network or files changed', result.stdout)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.root.exists())
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_bundle_imports_at_one_commit_and_preserves_ref_and_explicit_pin(self):
        for selected in ((), ('--ref', 'v2.0'), ('--ref', 'main', '--source-commit', 'c' * 40)):
            with self.subTest(selected=selected):
                self.log.write_text('')
                args = ('--yes', '--no-system', '--network', 'official', '--root', str(self.root), *selected)
                result = self.run_bootstrap(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                requests = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertEqual(len(requests), 8)
                self.assertTrue(requests[0]['url'].endswith('/commits/main'))
                self.assertEqual({r['url'].rsplit('/', 1)[-1] for r in requests[1:]}, set(report['bundle']))
                self.assertTrue(all('/' + COMMIT + '/scripts/' in r['url'] for r in requests[1:]))
                expected = list(args)
                if not selected:
                    expected += ['--source-commit', COMMIT]
                self.assertEqual(report['argv'], expected)
                self.assertFalse(self.root.exists())
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_mac_respects_homebrew_already_on_path(self):
        self.env['FRAMIVRA_TEST_SYSTEM'] = 'Darwin'
        self.executable('brew', '#!/bin/sh\nexit 99\n')
        result = self.run_bootstrap('--yes', '--no-system')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['brew'], str(self.bin / 'brew'))

    def test_new_brew_preserves_yes_and_distinguishes_trust_from_transport_failure(self):
        if shutil.which('brew') or any(Path(p).is_file() for p in ('/opt/homebrew/bin/brew', '/usr/local/bin/brew')):
            self.skipTest('UNRUN: host already has Homebrew outside the fixture')
        for case, (error, attempts) in enumerate((('Error: SHA256 mismatch', 1), ('Aborted.', 1), ('fatal: Could not resolve host', 2))):
            with self.subTest(error=error):
                state = self.base / ('brew-state-' + str(case))
                self.env.update(FRAMIVRA_TEST_SYSTEM='Darwin', FRAMIVRA_TEST_BREW_STATE=str(state),
                                FRAMIVRA_TEST_BREW_ERROR=error, HOMEBREW_API_DOMAIN='https://user-mirror.invalid')
                self.env['FRAMIVRA_TEST_BREW_SCRIPT'] = '''printf '%s|%s\\n' "${NONINTERACTIVE-unset}" "${HOMEBREW_API_DOMAIN-unset}" >> "$FRAMIVRA_TEST_BREW_STATE"
if [ ! -f "$FRAMIVRA_TEST_BREW_STATE.once" ]; then
  : > "$FRAMIVRA_TEST_BREW_STATE.once"
  printf '%s\\n' "$FRAMIVRA_TEST_BREW_ERROR" >&2
  exit 1
fi
'''
                result = self.run_bootstrap('--yes', '--root', str(self.root))
                records = state.read_text().splitlines()
                self.assertEqual(len(records), attempts, result.stdout + result.stderr)
                self.assertEqual(records[0], '1|https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api')
                if attempts == 1:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('not retrying another source', result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(records[1], '1|unset')
                self.assertFalse(self.root.exists())
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_missing_python_no_system_fails_before_network_or_install(self):
        if any(Path(p).is_file() for p in ('/opt/homebrew/opt/python@3.13/bin/python3.13',
                                          '/usr/local/opt/python@3.13/bin/python3.13')):
            self.skipTest('UNRUN: host has an absolute Homebrew Python outside the PATH fixture')
        self.env['PATH'] = str(self.bin)
        (self.bin / 'python3').unlink()
        self.executable('python3', '#!/bin/sh\nexit 1\n')
        self.executable('python', '#!/bin/sh\nexit 1\n')
        result = self.run_bootstrap('--no-system', '--yes', '--root', str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python >=3.9 is missing; no changes made', result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.root.exists())
        self.assertEqual(list(self.scratch.iterdir()), [])
