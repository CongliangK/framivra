"""POSIX native preparation: local config fixtures and injected commands, no installs."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import install_native_posix as native


class NativePosix(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='framivra-native-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.complete = {name: '/tools/' + name for name in
                         (*native.TOOLS, 'chrome', 'fc-cache', 'fc-list')}
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_real_stderr_security_errors_stop_but_transport_can_fallback(self):
        for message, expected in (('Hash Sum mismatch', [True]), ('Error: SHA256 mismatch', [True]),
                                  ('SHA512 mismatch', [True]), ('The repository is not signed.', [True]),
                                  ('SSL/TLS negotiation failed', [True]), ('GPG check FAILED', [True]),
                                  ('Connection timed out', [True, False])):
            attempts = []
            def operation(stage):
                attempts.append(stage)
                native._command([sys.executable, '-c',
                                 'import os,sys; assert os.environ["LC_ALL"] == "C"; '
                                 'print(sys.argv[1],file=sys.stderr); sys.exit(100)', message], timeout=10)
            progress = io.StringIO()
            with self.subTest(message=message), contextlib.redirect_stderr(progress):
                with self.assertRaises(native.NativeInstallError) as error:
                    native._retry([True, False], operation)
                self.assertIn(message, str(error.exception))
                self.assertIn(message, progress.getvalue())
                self.assertEqual(attempts, expected)

    def test_runner_inherits_stdin_stdout_and_tees_only_bounded_stderr(self):
        code = '''import sys
from scripts.install_native_posix import run_process
child = "import sys; print('progress',flush=True); print(input(),flush=True); sys.stderr.buffer.write(b'x'*40000+b'\\\\xffHash Sum mismatch')"
result = run_process([sys.executable, '-c', child], timeout=10)
assert result.stdout is None and result.returncode == 0
assert len(result.stderr) <= 16384 and result.stderr.endswith('Hash Sum mismatch')
captured = run_process([sys.executable, '-c', "print('captured')"], capture=True, timeout=10)
assert captured.stdout == 'captured\\n' and captured.stderr == ''
print('runner-ok')
'''
        result = subprocess.run([sys.executable, '-c', code], input='confirmed\n', capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=20, start_new_session=True)
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertIn('progress\nconfirmed\n', result.stdout)
        self.assertIn('runner-ok', result.stdout)
        self.assertGreater(len(result.stderr), 40000)
        self.assertIn('Hash Sum mismatch', result.stderr)

    def test_runner_timeout_reaps_child_in_an_isolated_test_session(self):
        marker = self.root / 'timeout-child.pid'
        code = '''import os,subprocess,sys,time
from scripts.install_native_posix import run_process
child = "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)"
started = time.monotonic()
try:
 run_process([sys.executable, '-c', child, sys.argv[1]], timeout=0.5)
 raise AssertionError('missing timeout')
except subprocess.TimeoutExpired:
 pass
assert time.monotonic()-started < 4
if os.name == 'posix':
 pid = int(open(sys.argv[1]).read())
 try: os.kill(pid, 0)
 except ProcessLookupError: pass
 else: raise AssertionError('timed out child is still alive')
print('timeout-reaped')
'''
        result = subprocess.run([sys.executable, '-c', code, str(marker)], capture_output=True,
                                text=True, timeout=10, start_new_session=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('timeout-reaped', result.stdout)

    def test_prepare_supplies_c_locale_to_injected_package_runner_only(self):
        original = dict(os.environ)
        observed = []
        def command(argv, **options):
            result = native.run_process(argv, **options)
            observed.append(result.stdout.strip())
            return result.stdout.strip()
        def handler(root, found, needs_venv, network, yes, run, metadata):
            run([sys.executable, '-c', 'import os; print(os.environ["LC_ALL"] + ":" + os.environ["LANG"])'],
                env=dict(os.environ, LC_ALL='zh_CN.UTF-8', LANG='zh_CN.UTF-8'), capture=True, timeout=10)
        found = {k: v for k, v in self.complete.items() if k != 'git'}
        with mock.patch.object(native.sys, 'platform', 'linux'), \
             mock.patch.object(native.shutil, 'which', return_value='/usr/bin/apt-get'), \
             mock.patch.object(native, '_apt', side_effect=handler), \
             mock.patch.object(native, 'discover', return_value=dict(self.complete)):
            native.prepare(self.root, found, yes=True, run=command)
        self.assertEqual(observed, ['C:C'])
        self.assertEqual(dict(os.environ), original)
        with mock.patch.object(native.os, 'geteuid', return_value=1000):
            self.assertIn('LC_ALL,LANG', native._privileged(['apt-get', 'update'])[1])

    def test_capability_inventory_is_complete_and_detects_ass(self):
        self.assertEqual(len(native.REQUIRED_ENCODERS), 10)
        self.assertEqual(len(native.REQUIRED_FILTERS), 30)
        def run(argv, **kwargs):
            return ' '.join(native.REQUIRED_ENCODERS if '-encoders' in argv
                            else [n for n in native.REQUIRED_FILTERS if n != 'ass'])
        self.assertEqual(native.missing_ffmpeg(self.complete, run), ['ass'])
        self.assertEqual(native.missing_ffmpeg({}, run), ['ffmpeg'])

    def test_no_system_is_zero_write_and_no_package_command(self):
        absent = self.root / 'must-not-exist'
        run = mock.Mock()
        with mock.patch.object(native.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(native.NativeInstallError, 'no-system'):
                native.prepare(absent, {}, no_system=True, yes=True, run=run)
            with self.assertRaisesRegex(native.NativeInstallError, 'ensurepip'):
                native.prepare(absent, self.complete, needs_venv=True, no_system=True, run=run)
        run.assert_not_called()
        self.assertFalse(absent.exists())

    def test_complete_resume_is_read_only(self):
        run = mock.Mock()
        with mock.patch.object(native.sys, 'platform', 'linux'):
            self.assertEqual(native.prepare(self.root, self.complete, run=run), self.complete)
        run.assert_not_called()

    def test_declined_transaction_runs_nothing(self):
        with mock.patch('builtins.input', return_value='n'), self.assertRaisesRegex(native.NativeInstallError, 'declined'):
            native._confirm('Google Chrome package registers a system update source', False)

    def test_apt_legacy_deb822_preserve_identity_and_trust(self):
        etc = self.root / 'apt'
        parts = etc / 'sources.list.d'
        parts.mkdir(parents=True)
        legacy = 'deb [arch=amd64 signed-by=/usr/share/keyrings/debian.gpg] https://deb.debian.org/debian trixie main\n'
        modern = ('Types: deb\nURIs: https://deb.debian.org/debian-security\nSuites: trixie-security\n'
                  'Components: main contrib\nArchitectures: amd64 arm64\nSigned-By: /usr/share/keyrings/debian.gpg\n')
        (etc / 'sources.list').write_text(legacy)
        (parts / 'debian.sources').write_text(modern)
        target = self.root / 'sources'
        args = native.apt_sources(target, 'debian', 'https://mirrors.tuna.tsinghua.edu.cn', etc)
        combined = '\n'.join(p.read_text() for p in target.iterdir())
        self.assertIn('trixie main', combined)
        self.assertIn('Suites: trixie-security', combined)
        self.assertIn('Architectures: amd64 arm64', combined)
        self.assertIn('Signed-By: /usr/share/keyrings/debian.gpg', combined)
        self.assertIn('signed-by=/usr/share/keyrings/debian.gpg', combined)
        self.assertIn('https://mirrors.tuna.tsinghua.edu.cn/debian-security', combined)
        self.assertNotIn('Trusted: yes', combined)
        self.assertIn('Acquire::Retries=2', args)
        self.assertEqual((etc / 'sources.list').read_text(), legacy)
        self.assertEqual((parts / 'debian.sources').read_text(), modern)
        official = native.apt_mirror_text(combined, 'debian')
        self.assertIn('https://deb.debian.org/debian', official)
        self.assertIn('https://security.debian.org/debian-security', official)

    def test_apt_ubuntu_ports_security_custom_sources(self):
        text = ('deb https://ports.ubuntu.com/ubuntu-ports noble main universe\n'
                'deb https://security.ubuntu.com/ubuntu noble-security main universe\n'
                'deb https://company.invalid/custom noble main\n')
        result = native.apt_mirror_text(text, 'ubuntu', 'https://mirrors.tuna.tsinghua.edu.cn')
        self.assertIn('https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports noble main universe', result)
        self.assertIn('https://security.ubuntu.com/ubuntu noble-security main universe', result)
        self.assertIn('https://company.invalid/custom', result)

    def test_apt_failed_mirror_retries_official_and_cleans_temp(self):
        etc = self.root / 'apt'
        etc.mkdir()
        (etc / 'sources.list').write_text('deb https://deb.debian.org/debian trixie main\n')
        original = native.apt_sources
        captured = []
        commands = []
        def sources(path, distro, mirror):
            captured.append(path)
            return original(path, distro, mirror, etc)
        def run(argv, **kwargs):
            commands.append(argv)
            self.assertIsNone(kwargs['timeout'])
            if len(commands) == 1:
                raise native.NativeInstallError('temporary network failure')
        found = dict(self.complete)
        found.pop('sox')
        metadata = {'transactions': [], 'system_side_effects': []}
        with mock.patch.object(native, 'os_release', return_value={'ID': 'debian'}), \
             mock.patch.object(native, 'apt_sources', side_effect=sources), \
             mock.patch.object(native, '_privileged', side_effect=lambda x: x):
            native._apt(self.root, found, False, 'cn', True, run, metadata)
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[-1][-3:], ['install', '-y', 'sox'])
        self.assertEqual([p.name for p in captured], ['cn', 'official'])
        self.assertTrue(all(not p.exists() for p in captured))

    def test_ubuntu_google_key_scope_and_disclosed_vendor_side_effects(self):
        from scripts import install_downloads
        etc = self.root / 'ubuntu-apt'
        etc.mkdir()
        (etc / 'sources.list').write_text('deb https://archive.ubuntu.com/ubuntu noble main universe\n')
        key = self.root / 'downloaded-key'
        key.write_text('test key bytes; acquire is injected')
        original = native.apt_sources
        found = {k: v for k, v in self.complete.items() if k != 'chrome'}
        metadata = {'transactions': [], 'system_side_effects': []}
        calls, confirmations, google_sources = [], [], []
        def sources(path, distro, mirror):
            return original(path, distro, mirror, etc)
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[0] == 'gpg':
                return 'fpr:::::::::' + native.GOOGLE_KEY_FINGERPRINT + ':'
            for part in argv:
                if part.startswith('Dir::Etc::sourceparts='):
                    directory = Path(part.split('=', 1)[1])
                    if (directory / 'google-chrome.list').exists():
                        google_sources.append((directory / 'google-chrome.list').read_text())
            return ''
        with mock.patch.object(native, 'os_release', return_value={'ID': 'ubuntu'}), \
             mock.patch.object(native.platform, 'machine', return_value='x86_64'), \
             mock.patch.object(native, 'apt_sources', side_effect=sources), \
             mock.patch.object(native, '_privileged', side_effect=lambda x: x), \
             mock.patch.object(native, '_confirm', side_effect=lambda text, yes: confirmations.append(text)), \
             mock.patch.object(install_downloads, 'acquire', return_value=key) as acquire:
            native._apt(self.root, found, False, 'cn', True, run, metadata)
        self.assertEqual(acquire.call_args.args[2], native.GOOGLE_KEY_SHA256)
        self.assertEqual(acquire.call_args.args[0], self.root / 'cache/downloads')
        self.assertTrue(any('signed-by=' in text and 'arch=amd64' in text for text in google_sources))
        self.assertTrue(any('persistently register' in text for text in confirmations))
        self.assertTrue(any('google-chrome-stable' in argv for argv in calls))
        self.assertFalse(any('snap' in argv for argv in calls))
        self.assertIn('persistent Google update repository', metadata['system_side_effects'][0])

    def test_wrong_google_key_stops_before_chrome_install(self):
        from scripts import install_downloads
        etc = self.root / 'ubuntu-apt'
        etc.mkdir()
        (etc / 'sources.list').write_text('deb https://archive.ubuntu.com/ubuntu noble main universe\n')
        key = self.root / 'wrong-key'
        key.write_text('fixture')
        original = native.apt_sources
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return 'fpr:::::::::0000000000000000000000000000000000000000:'
        with mock.patch.object(native, 'os_release', return_value={'ID': 'ubuntu'}), \
             mock.patch.object(native.platform, 'machine', return_value='x86_64'), \
             mock.patch.object(native, 'apt_sources', side_effect=lambda p, d, m: original(p, d, m, etc)), \
             mock.patch.object(native, '_privileged', side_effect=lambda x: x), \
             mock.patch.object(install_downloads, 'acquire', return_value=key), \
             self.assertRaisesRegex(native.NativeInstallError, 'fingerprint'):
            native._apt(self.root, {k: v for k, v in self.complete.items() if k != 'chrome'},
                        False, 'cn', True, run, {'transactions': [], 'system_side_effects': []})
        self.assertFalse(any('google-chrome-stable' in argv for argv in calls))

    def test_integrity_failure_never_falls_back(self):
        operation = mock.Mock(side_effect=native.NativeInstallError('GPG signature verification failed'))
        with self.assertRaisesRegex(native.NativeInstallError, 'signature'):
            native._retry([True, False], operation)
        operation.assert_called_once_with(True)

    def test_dnf_temporary_mirror_preserves_gpg_and_custom_repos(self):
        source = self.root / 'repos'
        source.mkdir()
        text = ('[fedora]\nmetalink=https://mirrors.fedoraproject.org/metalink?repo=fedora-$releasever&arch=$basearch\n'
                'gpgcheck=1\ngpgkey=file:///etc/pki/rpm-gpg/vendor\nsslverify=1\n'
                '[updates]\nmetalink=https://mirrors.fedoraproject.org/metalink?repo=updates\ngpgcheck=1\n'
                '[custom]\nbaseurl=https://company.invalid/rpm\nenabled=0\ngpgcheck=1\n')
        (source / 'fedora.repo').write_text(text)
        dest = self.root / 'temporary-repos'
        native.dnf_repositories(dest, True, source)
        result = (dest / 'fedora.repo').read_text()
        self.assertIn('https://mirrors.ustc.edu.cn/fedora/releases/$releasever/Everything/$basearch/os/', result)
        self.assertIn('https://mirrors.ustc.edu.cn/fedora/updates/$releasever/Everything/$basearch/', result)
        self.assertIn('gpgcheck = 1', result)
        self.assertIn('gpgkey = file:///etc/pki/rpm-gpg/vendor', result)
        self.assertIn('https://company.invalid/rpm', result)
        self.assertIn('enabled = 0', result)
        self.assertEqual((source / 'fedora.repo').read_text(), text)

    def test_rpmfusion_only_free_and_verified_key(self):
        native.rpmfusion_repository(self.root, Path('/signed-fedora-key'), True)
        text = (self.root / 'framivra-rpmfusion-free.repo').read_text()
        self.assertIn('gpgcheck=1', text)
        self.assertIn('sslverify=1', text)
        self.assertIn('gpgkey=file:///signed-fedora-key', text)
        self.assertNotIn('nonfree', text)
        self.assertNotIn('tainted', text)
        self.assertIn('updates/$releasever/$basearch/', text)

    def test_fedora_full_swap_is_separately_confirmed_and_bounded(self):
        found = {k: v for k, v in self.complete.items() if k not in ('ffmpeg', 'ffprobe')}
        calls = []
        confirmations = []
        def run(argv, **kwargs):
            calls.append(argv)
            self.assertIsNone(kwargs.get('timeout')) if argv[0] == 'dnf' else None
            return ''
        def repos(path, mirror):
            path.mkdir()
            return ['--setopt=reposdir=' + str(path)]
        metadata = {'transactions': [], 'system_side_effects': []}
        with mock.patch.object(native, 'os_release', return_value={'ID': 'fedora', 'VERSION_ID': '45'}), \
             mock.patch.object(native, 'dnf_repositories', side_effect=repos), \
             mock.patch.object(Path, 'is_file', return_value=True), \
             mock.patch.object(native, '_privileged', side_effect=lambda x: x), \
             mock.patch.object(native, '_confirm', side_effect=lambda text, yes: confirmations.append(text)):
            native._dnf(self.root, found, False, 'cn', False, run, metadata)
        self.assertIn('distribution-gpg-keys', calls[0])
        self.assertIn('--allowerasing', calls[-1])
        self.assertIn('swap', calls[-1])
        self.assertNotIn('-y', calls[-1])  # DNF still asks about the concrete removal transaction.
        self.assertEqual(len(confirmations), 2)
        self.assertIn('RPM Fusion free', confirmations[-1])
        self.assertIn('key', metadata['system_side_effects'][0])

    def test_pacman_include_retains_signatures_and_custom_repo(self):
        mirrors = self.root / 'mirrorlist'
        mirrors.write_text('Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch\n')
        config = self.root / 'pacman.conf'
        text = ('[options]\nArchitecture = auto\nSigLevel = Required DatabaseOptional\n'
                '[core]\nInclude = %s\n[extra]\nInclude = %s\n'
                '[company]\nServer = https://company.invalid/$repo\n' % (mirrors, mirrors))
        config.write_text(text)
        result = native.pacman_configuration(config, mirror=True)
        self.assertIn('SigLevel = Required DatabaseOptional', result)
        self.assertEqual(result.count('https://mirrors.ustc.edu.cn/archlinux/$repo/os/$arch'), 2)
        self.assertIn('https://company.invalid/$repo', result)
        self.assertEqual(config.read_text(), text)
        self.assertIn('geo.mirror.pkgbuild.com', native.pacman_configuration(config))

    def test_brew_process_environment_leaves_global_and_official_fallback(self):
        original = {'https_proxy': 'http://user-proxy:1234', 'NO_PROXY': 'localhost',
                    'HOMEBREW_ARTIFACT_DOMAIN_NO_FALLBACK': '1',
                    'HOMEBREW_ARTIFACT_DOMAIN': 'https://unrelated.invalid',
                    'HOMEBREW_NO_INSTALL_FROM_API': '1'}
        with mock.patch.dict(os.environ, original, clear=True):
            env = native._brew_environment('cn')
            self.assertEqual(dict(os.environ), original)
        self.assertEqual(env['https_proxy'], original['https_proxy'])
        self.assertIn('localhost', env['NO_PROXY'])
        self.assertIn('mirrors.tuna.tsinghua.edu.cn', env['NO_PROXY'])
        self.assertNotIn('HOMEBREW_ARTIFACT_DOMAIN_NO_FALLBACK', env)
        self.assertNotIn('HOMEBREW_ARTIFACT_DOMAIN', env)
        self.assertNotIn('HOMEBREW_NO_INSTALL_FROM_API', env)
        self.assertNotIn('HOMEBREW_BREW_GIT_REMOTE', env)

    def test_mac_keg_only_pair_and_every_dependency_force_bottled(self):
        prefix = self.root / 'ffmpeg-full'
        (prefix / 'bin').mkdir(parents=True)
        for name in ('ffmpeg', 'ffprobe'):
            target = prefix / 'bin' / name
            target.write_text('fixture')
            target.chmod(0o755)
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[1] == 'deps':
                return 'libass\nx264\n'
            if argv[1] == '--prefix':
                return str(prefix)
            if argv[1] == 'install':
                self.assertIsNone(kwargs['timeout'])
                self.assertTrue(Path(kwargs['env']['HOMEBREW_CURLRC']).exists())
            return ''
        found = {k: v for k, v in self.complete.items() if k not in ('ffmpeg', 'ffprobe')}
        metadata = {'transactions': [], 'system_side_effects': []}
        with mock.patch.object(native.shutil, 'which', return_value='/opt/homebrew/bin/brew'), \
             mock.patch.object(native.platform, 'machine', return_value='arm64'), \
             mock.patch.object(native.platform, 'mac_ver', return_value=('15.7', (), '')):
            result = native._brew(self.root, found, False, 'cn', True, run, metadata)
        self.assertEqual(result['ffmpeg'], str(prefix / 'bin/ffmpeg'))
        self.assertEqual(result['ffprobe'], str(prefix / 'bin/ffprobe'))
        self.assertIn(['brew', 'install', '--force-bottle', 'libass', 'x264', 'ffmpeg-full'], calls)
        self.assertFalse(any('link' in argv or '--force' in argv for argv in calls))

    def test_old_intel_mac_never_falls_into_source_build(self):
        run = mock.Mock()
        metadata = {'transactions': [], 'system_side_effects': []}
        with mock.patch.object(native.shutil, 'which', return_value='/usr/local/bin/brew'), \
             mock.patch.object(native.platform, 'machine', return_value='x86_64'), \
             self.assertRaisesRegex(native.NativeInstallError, 'no automatic source build'):
            native._brew(self.root, {}, False, 'cn', True, run, metadata)
        run.assert_not_called()

    def test_linux_discovery_respects_native_override_and_rejects_wrappers(self):
        browser = self.root / 'chrome'
        browser.write_bytes(b'\x7fELFfixture')
        wrapper = self.root / 'wrapper'
        wrapper.write_text('#!/bin/sh\n')
        with mock.patch.object(native.sys, 'platform', 'linux'), \
             mock.patch.object(native, 'browser_candidates', return_value=[str(wrapper), str(browser)]), \
             mock.patch.object(native.shutil, 'which', return_value=None):
            self.assertEqual(native.discover(self.root)['chrome'], str(browser))
            self.assertEqual(native.discover(self.root, {'chrome': '/explicit/native'})['chrome'], '/explicit/native')


if __name__ == '__main__':
    unittest.main()
