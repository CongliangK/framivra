"""Locked Windows tools: real ZIP/file transactions, no native installation."""
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
        found = self.tools()
        del found['ffmpeg']
        run = mock.Mock()
        with mock.patch.object(native, 'acquire', side_effect=native.IntegrityError('bad hash')) as acquire:
            with self.assertRaises(native.IntegrityError):
                native.prepare(self.root, found, yes=True, run=run)
        acquire.assert_called_once()
        run.assert_not_called()

    def test_official_policy_excludes_git_domestic_mirrors_no_install_timeout(self):
        found = self.tools()
        del found['git']
        commands = []
        def run(argv, **kwargs):
            commands.append((argv, kwargs))
            if len(commands) == 1:
                executable = native._target(self.root, native.PACKAGES['git']) / 'cmd/git.exe'
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_bytes(b'exe')
            return 'git version fixture'
        with mock.patch.object(native, 'acquire', return_value=Path(self.temp.name) / 'git.exe') as acquire:
            result = native.prepare(self.root, found, network='official', yes=True, run=run)
        self.assertEqual(acquire.call_args.args[1], native.PACKAGES['git']['urls'][-1:])
        self.assertIsNone(commands[0][1]['timeout'])
        self.assertEqual(commands[1][1]['timeout'], 60)
        self.assertIn('/o:PathOption=BashOnly', commands[0][0])
        self.assertEqual(native.discover(self.root)['git'], result['git'])

    def test_interrupted_git_resumes_only_with_matching_ownership(self):
        found = self.tools()
        del found['git']
        target = native._target(self.root, native.PACKAGES['git'])
        with mock.patch.object(native, 'acquire', return_value=Path(self.temp.name) / 'git.exe'):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                native.prepare(self.root, found, yes=True, run=mock.Mock(side_effect=RuntimeError('interrupted')))
            self.assertTrue((target / '.installing.json').is_file())
            self.assertNotIn('git', native.discover(self.root))
            def finish(argv, **kwargs):
                if '/CURRENTUSER' in argv:
                    (target / 'cmd').mkdir()
                    (target / 'cmd/git.exe').write_bytes(b'exe')
                return 'git fixture'
            native.prepare(self.root, found, yes=True, run=finish)
        self.assertIn('git', native.discover(self.root))

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


if __name__ == '__main__':
    unittest.main()
