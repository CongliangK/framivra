"""Locked Windows tools: real ZIP/file transactions, no native installation."""
import contextlib
import ctypes
import importlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts import install_native_windows as native


class NativeWindows(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='framivra-native-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'managed root'
        self.root.mkdir()
        self.package = native.PACKAGES['ffmpeg']

    def archive(self, entries=None):
        path = Path(self.temp.name) / 'fixture.zip'
        if entries is None:
            entries = {**{p: b'fixture executable' for p in self.package['files'].values()},
                       'ffmpeg-9.0.2-full_build/LICENSE': b'GPL license',
                       'ffmpeg-9.0.2-full_build/bin/companion.dll': b'dll'}
        with zipfile.ZipFile(path, 'w') as output:
            for name, value in entries.items():
                output.writestr(name, value)
        return path

    def tools(self):
        return {name: '/existing/' + name for name in ('git', 'ffmpeg', 'ffprobe', 'sox', 'chrome')}

    def git_entries(self):
        # Paths checked against the pinned ordinary MinGit ZIP, not a DLL subset
        # that could pass --version yet fail Git's HTTPS/shell helper execution.
        return {'cmd/git.exe': b'fixture Git executable',
                'ucrt64/bin/git.exe': b'actual Git executable',
                'ucrt64/bin/git-remote-https.exe': b'HTTPS helper',
                'ucrt64/bin/libcurl-4.dll': b'curl runtime',
                'ucrt64/bin/zlib1.dll': b'compression runtime',
                'ucrt64/etc/ssl/certs/ca-bundle.crt': b'certificate bundle',
                'usr/bin/sh.exe': b'shell helper', 'usr/bin/msys-2.0.dll': b'MSYS runtime',
                'etc/gitconfig': b'package configuration', 'LICENSE.txt': b'Git license'}

    def test_complete_extract_keeps_companions_and_reuses_atomically(self):
        archive = self.archive()
        found = native.unpack(archive, self.root, self.package)
        target = native._target(self.root, self.package)
        self.assertTrue((target / 'ffmpeg-9.0.2-full_build/LICENSE').is_file())
        self.assertTrue((Path(found['ffmpeg']).parent / 'companion.dll').is_file())
        self.assertEqual(native.discover(self.root), found)
        with mock.patch.object(zipfile, 'ZipFile', side_effect=AssertionError('must reuse')):
            self.assertEqual(native.unpack(archive, self.root, self.package), found)

    def test_discovery_ignores_unpublished_interrupted_staging(self):
        stage = self.root / 'tools/windows/.ffmpeg-9.0.2-interrupted'
        stage.mkdir(parents=True)
        (stage / 'ffmpeg.exe').write_text('partial')
        self.assertEqual(native.discover(self.root), {})
        found = native.unpack(self.archive(), self.root, self.package)
        self.assertIn('ffprobe', found)
        self.assertTrue(stage.is_dir())  # Never delete another invocation's files.

    def test_partial_final_directory_is_not_adopted(self):
        target = native._target(self.root, self.package)
        target.mkdir(parents=True)
        (target / 'user-file').write_text('do not overwrite')
        with self.assertRaisesRegex(native.IntegrityError, 'unowned'):
            native.unpack(self.archive(), self.root, self.package)
        self.assertEqual((target / 'user-file').read_text(), 'do not overwrite')

    def test_wrong_or_broken_completion_identity_is_hard_failure(self):
        native.unpack(self.archive(), self.root, self.package)
        marker = native._target(self.root, self.package) / '.complete.json'
        marker.write_text(json.dumps({'archiveSha256': 'not the lock'}))
        with self.assertRaises(native.IntegrityError):
            native.discover(self.root)

    def test_missing_ffprobe_never_publishes_complete_directory(self):
        with self.assertRaisesRegex(native.IntegrityError, 'missing required file'):
            native.unpack(self.archive({'ffmpeg-9.0.2-full_build/bin/ffmpeg.exe': b'exe'}), self.root, self.package)
        self.assertFalse(native._target(self.root, self.package).exists())
        self.assertEqual(native.discover(self.root), {})

    def test_unsafe_windows_paths_are_rejected_before_any_extract(self):
        for name in ('../escape', '/absolute', 'C:/escape', 'folder\\..\\escape',
                     'folder/file:stream', 'folder/con.txt', 'folder/trailing. ',
                     'folder/../escape', 'folder//file'):
            with self.subTest(name=name):
                with self.assertRaises(native.IntegrityError):
                    native.unpack(self.archive({name: b'unsafe'}), self.root, self.package)
                self.assertFalse(native._target(self.root, self.package).exists())
        self.assertFalse((self.root / 'escape').exists())

    def test_case_aliases_and_links_rejected(self):
        path = self.archive({'directory/File.exe': b'first', 'directory/file.exe': b'second'})
        with self.assertRaisesRegex(native.IntegrityError, 'Duplicate'):
            native.unpack(path, self.root, self.package)
        link = zipfile.ZipInfo('directory/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(link, '../outside')
        with self.assertRaisesRegex(native.IntegrityError, 'Linked'):
            native.unpack(path, self.root, self.package)

    def test_native_ancestor_symlink_is_not_followed(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        try:
            (self.root / 'tools').symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Creating symlinks requires platform permission')
        with self.assertRaisesRegex(native.IntegrityError, 'symlink/junction'):
            native.discover(self.root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_qualified_existing_tools_need_no_download_or_approval(self):
        found = self.tools()
        with mock.patch.object(native, 'acquire') as acquire:
            self.assertEqual(native.prepare(self.root, found), found)
        acquire.assert_not_called()
        self.assertFalse((self.root / 'cache').exists())

    def test_no_system_and_missing_approval_are_zero_write_failures(self):
        found = self.tools()
        del found['ffmpeg']
        with mock.patch.object(native, 'acquire') as acquire:
            for options in ({'no_system': True, 'yes': True}, {'yes': False}):
                with self.assertRaises(native.DownloadError):
                    native.prepare(self.root, found, **options)
        acquire.assert_not_called()
        self.assertFalse((self.root / 'cache').exists())

    def test_either_missing_ffmpeg_tool_prepares_same_locked_pair(self):
        found = self.tools()
        del found['ffprobe']
        with mock.patch.object(native, 'acquire', return_value=self.archive()) as acquire:
            result = native.prepare(self.root, found, yes=True)
        self.assertEqual(result['git'], found['git'])
        self.assertEqual(Path(result['ffmpeg']).parent, Path(result['ffprobe']).parent)
        self.assertNotEqual(result['ffmpeg'], found['ffmpeg'])
        self.assertEqual(acquire.call_args.args[1], self.package['urls'])
        self.assertEqual(acquire.call_args.args[2], self.package['sha256'])

    def test_hash_failure_never_runs_installer_or_different_provider(self):
        for name in ('ffmpeg', 'git'):
            with self.subTest(name=name):
                found = self.tools()
                del found[name]
                run = mock.Mock()
                with mock.patch.object(native, 'acquire', side_effect=native.IntegrityError('bad hash')) as acquire:
                    with self.assertRaises(native.IntegrityError):
                        native.prepare(self.root, found, yes=True, run=run)
                acquire.assert_called_once()
                run.assert_not_called()
                self.assertFalse(native._target(self.root, native.PACKAGES[name]).exists())

    def test_portable_git_preserves_runtime_without_touching_legacy_or_running_installer(self):
        found = self.tools()
        del found['git']
        package = native.PACKAGES['git']
        legacy = self.root / 'tools/windows/git-2.56.0.4'
        legacy.mkdir(parents=True)
        old_marker = legacy / '.installing.json'
        old_marker.write_bytes(b'legacy Inno ownership record')
        old_cache = self.root / 'cache/downloads/legacy/git-2.56.0.4.exe'
        old_cache.parent.mkdir(parents=True)
        old_cache.write_bytes(b'preserved old installer cache')
        entries = self.git_entries()
        archive = self.archive(entries)
        physical, iterdir = native._physical, Path.iterdir
        def reject_legacy(path):
            if Path(path) == legacy or legacy in Path(path).parents:
                raise PermissionError('Legacy elevated Inno tree must not be inspected')
        def checked_physical(path):
            reject_legacy(path)
            return physical(path)
        def checked_iterdir(path):
            reject_legacy(path)
            return iterdir(path)
        run = mock.Mock()
        with mock.patch.object(native, '_physical', side_effect=checked_physical), \
             mock.patch.object(Path, 'iterdir', checked_iterdir), \
             mock.patch.object(native, 'acquire', return_value=archive) as acquire:
            result = native.prepare(self.root, found, network='official', yes=True, run=run)
            self.assertEqual(native.discover(self.root)['git'], result['git'])
        target = native._target(self.root, package)
        self.assertEqual(target.name, 'mingit-2.56.0.4-x64')
        self.assertEqual(result, dict(found, git=str(target / 'cmd/git.exe')))
        for name, data in entries.items():
            self.assertEqual((target / name).read_bytes(), data)
        self.assertEqual(old_marker.read_bytes(), b'legacy Inno ownership record')
        self.assertEqual(old_cache.read_bytes(), b'preserved old installer cache')
        self.assertEqual(acquire.call_args.args[1], package['urls'][-1:])
        self.assertEqual(acquire.call_args.args[2], package['sha256'])
        self.assertEqual(acquire.call_args.kwargs['size'], 39846926)
        self.assertTrue(acquire.call_args.kwargs['filename'].endswith('.zip'))
        run.assert_not_called()

    def test_invalid_git_archive_or_crc_never_publishes_native_completion(self):
        found = self.tools()
        del found['git']
        archive = self.archive(self.git_entries())
        corrupt = bytearray(archive.read_bytes())
        corrupt[corrupt.index(b'fixture Git executable')] ^= 1
        for payload in (b'not a ZIP archive', corrupt):
            archive.write_bytes(payload)
            run = mock.Mock()
            with self.subTest(payload=type(payload).__name__), \
                 mock.patch.object(native, 'acquire', return_value=archive):
                with self.assertRaisesRegex(native.IntegrityError, 'Invalid native ZIP'):
                    native.prepare(self.root, found, yes=True, run=run)
                self.assertFalse(native._target(self.root, native.PACKAGES['git']).exists())
                self.assertNotIn('git', native.discover(self.root))
                run.assert_not_called()

    def test_chrome_uses_dynamic_official_manifest_without_total_timeout(self):
        found = self.tools()
        del found['chrome']
        run = mock.Mock()
        with mock.patch.object(native, '_browser', side_effect=[None, '/native/edge.exe']), mock.patch.object(native.shutil, 'which', return_value='winget.exe'):
            result = native.prepare(self.root, found, yes=True, run=run)
        self.assertEqual(result['chrome'], '/native/edge.exe')
        self.assertIn('--source', run.call_args.args[0])
        self.assertIn('winget', run.call_args.args[0])
        self.assertIsNone(run.call_args.kwargs['timeout'])


class PythonRuntimeWindows(unittest.TestCase):
    """Injected child-process contracts; not a Windows native installation test."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='framivra-crt-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'managed & unicode 字体'
        self.python = self.root / 'venv/Scripts/python.exe'
        self.environment = {'PATH': '/existing/native path', 'USER_SETTING': 'unchanged'}
        self.executable = self.base / 'runtime & 签名.exe'
        self.executable.write_bytes(b'mocked acquire verifies the real pin')
        patch = mock.patch.object(native.shutil, 'which', return_value='/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')
        patch.start()
        self.addCleanup(patch.stop)

    def failure(self, error_type='ImportError', dll_errors=None):
        dlls = {name: {'ok': True} for name in native.CRT_DLLS}
        for name, code in (dll_errors if dll_errors is not None else {'MSVCP140.dll': 126}).items():
            dlls[name] = {'ok': False, 'winerror': code, 'error': 'fixture load error'}
        return json.dumps({'ok': False, 'error_type': error_type,
                           'error': 'original soxr_ext DLL load failed', 'dlls': dlls})

    def prepare(self, run, **kwargs):
        self.plan = io.StringIO()
        with contextlib.redirect_stdout(self.plan):
            return native.ensure_python_runtime(self.root, self.python, self.environment, run=run, **kwargs)

    def test_healthy_real_import_probe_needs_no_runtime_download_or_system_write(self):
        before = dict(self.environment)
        run = mock.Mock(return_value='{"ok":true}')
        with mock.patch.object(native, 'acquire') as fetch:
            self.assertEqual(self.prepare(run), {'status': 'available', 'installationAttempted': False})
        fetch.assert_not_called()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][:3], [str(self.python), '-I', '-c'])
        script = run.call_args.args[0][3]
        compile(script, '<runtime probe>', 'exec')
        self.assertTrue(script.isascii())
        for module in ('numpy', 'scipy', 'soundfile', 'soxr', 'PIL'):
            self.assertIn("'" + module + "'", script)
        self.assertIn('soxr.resample(numpy.ones(32), 48000, 44100)', script)
        self.assertIn('numpy.isfinite(audio).all()', script)
        self.assertEqual(self.environment, before)
        self.assertFalse(self.root.exists())

    def test_unrelated_imports_unknown_or_denied_dll_loads_never_install(self):
        failures = (self.failure('RuntimeError'), self.failure('ModuleNotFoundError'),
                    self.failure(dll_errors={}),
                    self.failure(dll_errors={'MSVCP140.dll': 126, 'VCRUNTIME140.dll': 5}),
                    self.failure(dll_errors={'MSVCP140.dll': 193}),
                    self.failure(dll_errors={'MSVCP140.dll': 127}))
        for payload in failures:
            with self.subTest(payload=payload), mock.patch.object(native, 'acquire') as fetch:
                run = mock.Mock(return_value=payload)
                with self.assertRaisesRegex(native.DownloadError, 'original soxr_ext'):
                    self.prepare(run, yes=True)
                fetch.assert_not_called()
                self.assertEqual(run.call_count, 1)
                self.assertFalse(self.root.exists())

    def test_ctypes_wrapped_126_preserves_exception_details_but_generic_none_error_is_blocked(self):
        for failure in (FileNotFoundError('ctypes wrapped missing module'), OSError('unknown DLL error')):
            def load(name):
                if name == 'MSVCP140.dll':
                    raise failure
                return object()
            output = io.StringIO()
            with mock.patch.object(importlib, 'import_module', side_effect=ImportError('soxr_ext missing dependency')), \
                 mock.patch.object(ctypes, 'WinDLL', side_effect=load, create=True), \
                 contextlib.redirect_stdout(output):
                exec(native.PYTHON_RUNTIME_PROBE, {})
            probe = json.loads(output.getvalue())
            state = probe['dlls']['MSVCP140.dll']
            self.assertEqual(state['error_type'], type(failure).__name__)
            self.assertIsNone(state['winerror'])
            self.assertEqual(state['wrapped126'], isinstance(failure, FileNotFoundError))
            run = mock.Mock(side_effect=[output.getvalue(), '{"exitcode":0}', '{"ok":true}'])
            with mock.patch.object(native, 'acquire', return_value=self.executable) as fetch:
                if isinstance(failure, FileNotFoundError):
                    metadata = self.prepare(run, yes=True)
                    self.assertEqual(metadata['crtProbe']['MSVCP140.dll'], state)
                    fetch.assert_called_once()
                else:
                    with self.assertRaisesRegex(native.DownloadError, 'blocked CRT load error'):
                        self.prepare(run, yes=True)
                    fetch.assert_not_called()
                    self.assertEqual(run.call_count, 1)

    def test_no_system_and_declined_or_missing_consent_stop_before_download(self):
        for kwargs, answer in (({'no_system': True, 'yes': True}, 'yes'), ({}, 'no'), ({}, EOFError())):
            with self.subTest(kwargs=kwargs, answer=answer), mock.patch.object(native, 'acquire') as fetch, \
                 mock.patch('builtins.input', side_effect=answer if isinstance(answer, Exception) else None,
                            return_value=answer) as consent:
                run = mock.Mock(return_value=self.failure())
                with self.assertRaises(native.DownloadError):
                    self.prepare(run, **kwargs)
                fetch.assert_not_called()
                self.assertEqual(run.call_count, 1)
                if kwargs.get('no_system'):
                    consent.assert_not_called()
                self.assertFalse(self.root.exists())

    def test_exact_official_pin_signature_elevation_and_unicode_argument_policy(self):
        before = dict(self.environment)
        run = mock.Mock(side_effect=[self.failure(), '{"exitcode":0}', '{"ok":true}'])
        with mock.patch.object(native, 'acquire', return_value=self.executable) as fetch:
            result = self.prepare(run, yes=True, network='official')
        fetch.assert_called_once_with(self.root / 'cache/downloads', [native.VC_RUNTIME['url']],
                                      'cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b',
                                      size=25635768, filename='VC_redist.x64.exe')
        self.assertIn('shared SYSTEM runtime', self.plan.getvalue())
        self.assertIn('UAC/admin approval', self.plan.getvalue())
        self.assertIn('restart', self.plan.getvalue())
        self.assertEqual(result['packageVersion'], '14.44.35211.0')
        self.assertEqual(result['installerExitCode'], 0)
        self.assertFalse(result['rebootRequired'])
        self.assertEqual(result['missingCrt'], ['MSVCP140.dll'])
        self.assertEqual(run.call_count, 3)
        wrapper = run.call_args_list[1]
        argv = wrapper.args[0]
        self.assertEqual(argv[1:4], ['-NoProfile', '-NonInteractive', '-EncodedCommand'])
        script = native.base64.b64decode(argv[4]).decode('utf-16le')
        self.assertEqual(script, native.VC_RUNTIME_INSTALL)
        self.assertLess(script.index('Get-FileHash'), script.index('Get-AuthenticodeSignature'))
        self.assertLess(script.index('Get-AuthenticodeSignature'), script.index('Start-Process'))
        self.assertIn("$signature.Status -ne 'Valid'", script)
        self.assertIn('CN=Microsoft Corporation', script)
        self.assertIn("-Verb RunAs -ArgumentList @('/install', '/passive', '/norestart')", script)
        self.assertIn('$handle = $process.Handle', script)
        self.assertIn('$process.WaitForExit()', script)
        self.assertNotIn('/log', script)
        self.assertNotIn(str(self.root), script)
        self.assertNotIn(str(self.executable), script)
        self.assertNotIn(str(self.python), script)
        self.assertEqual(wrapper.kwargs['env']['FRAMIVRA_VCREDIST_FILE'], str(self.executable))
        self.assertEqual(wrapper.kwargs['env']['FRAMIVRA_VCREDIST_SHA256'], native.VC_RUNTIME['sha256'])
        self.assertIsNone(wrapper.kwargs['timeout'])
        self.assertEqual(self.environment, before)
        self.assertNotIn('FRAMIVRA_VCREDIST_FILE', run.call_args.kwargs['env'])

    def test_hash_or_powershell_wrapper_trust_uac_errors_are_not_reprobed_or_retried(self):
        with mock.patch.object(native, 'acquire', side_effect=native.IntegrityError('bad hash')) as fetch:
            run = mock.Mock(return_value=self.failure())
            with self.assertRaisesRegex(native.IntegrityError, 'bad hash'):
                self.prepare(run, yes=True)
            fetch.assert_called_once()
            self.assertEqual(run.call_count, 1)
        for message in ('signature status is not Valid', 'Microsoft publisher mismatch', 'UAC denied', 'PowerShell hash mismatch'):
            with self.subTest(message=message), mock.patch.object(native, 'acquire', return_value=self.executable) as fetch:
                run = mock.Mock(side_effect=[self.failure(), RuntimeError(message), '{"ok":true}'])
                with self.assertRaisesRegex(RuntimeError, message):
                    self.prepare(run, yes=True)
                fetch.assert_called_once()
                self.assertEqual(run.call_count, 2)

    def test_native_nonzero_requires_actual_fresh_probe_and_never_masks_cancellation_or_reboot(self):
        for code in (0, 1638, 0x80070666, 3010, 1641, 1223, 1602, 0x800704c7, 0x80070642, 5):
            for healthy in (False, True):
                with self.subTest(code=code, healthy=healthy), mock.patch.object(native, 'acquire', return_value=self.executable):
                    run = mock.Mock(side_effect=[self.failure(), json.dumps({'exitcode': code}),
                                                 '{"ok":true}' if healthy else self.failure()])
                    if healthy and code in (0, 1638, 0x80070666):
                        self.assertEqual(self.prepare(run, yes=True)['installerExitCode'], code)
                    else:
                        with self.assertRaises(native.DownloadError) as failure:
                            self.prepare(run, yes=True)
                        self.assertIn(str(code), str(failure.exception))
                        if code == 3010:
                            self.assertIn('restart', str(failure.exception))
                        if code in (1223, 1602):
                            self.assertIn('cancelled', str(failure.exception))
                    self.assertEqual(run.call_count, 3)
                    self.assertEqual(run.call_args.args[0], run.call_args_list[0].args[0])


if __name__ == '__main__':
    unittest.main()
