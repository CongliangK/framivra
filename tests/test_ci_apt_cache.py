"""Archive-cache safety regressions; all APT/dpkg subprocesses are mocked."""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_apt_cache', ROOT / 'scripts/ci_apt_cache.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
DISTRO = {'ID': 'ubuntu', 'VERSION_ID': '24.04'}
IMAGE = {'ImageOS': 'ubuntu24', 'ImageVersion': '20261012.1.0'}
NAME = 'fonts-noto-cjk_1.0_all.deb'
CONTENT = b'test archive bytes, never a real executable package'
HASH = hashlib.sha256(CONTENT).hexdigest()
PLAN = "'https://archive.ubuntu.com/pool/{}' {} {} SHA256:{}\n".format(
    NAME, NAME, len(CONTENT), HASH)


class CacheIdentity(unittest.TestCase):
    def test_actual_image_distro_arch_package_declaration_and_schema_change_key(self):
        key = ci.cache_key(IMAGE, DISTRO, 'x86_64')
        self.assertIn('ubuntu24-20261012.1.0-ubuntu-24.04-x86_64-v1-', key)
        alternatives = [
            ci.cache_key(dict(IMAGE, ImageOS='ubuntu22'), DISTRO, 'x86_64'),
            ci.cache_key(dict(IMAGE, ImageVersion='20261013.1.0'), DISTRO, 'x86_64'),
            ci.cache_key(IMAGE, dict(DISTRO, ID='debian'), 'x86_64'),
            ci.cache_key(IMAGE, dict(DISTRO, VERSION_ID='22.04'), 'x86_64'),
            ci.cache_key(IMAGE, DISTRO, 'aarch64'),
            ci.cache_key(IMAGE, DISTRO, 'x86_64', packages=('ffmpeg',)),
            ci.cache_key(IMAGE, DISTRO, 'x86_64', schema='v2'),
        ]
        self.assertNotIn(key, alternatives)
        self.assertEqual(len(set(alternatives)), len(alternatives))

    def test_missing_or_unsafe_identity_never_creates_shared_fallback_key(self):
        for field in IMAGE:
            for value in ('', 'unknown\ncacheable=true', '../ubuntu', ' '):
                self.assertIsNone(ci.cache_key(dict(IMAGE, **{field: value}), DISTRO, 'x86_64'))
        self.assertIsNone(ci.cache_key({}, DISTRO, 'x86_64'))
        self.assertIsNone(ci.cache_key(IMAGE, {}, 'x86_64'))
        self.assertIsNone(ci.cache_key(IMAGE, DISTRO, ''))

    def test_separator_containing_image_identifiers_cannot_alias(self):
        first = {'ImageOS': 'ubuntu-24', 'ImageVersion': '2026'}
        second = {'ImageOS': 'ubuntu', 'ImageVersion': '24-2026'}
        self.assertNotEqual(ci.cache_key(first, DISTRO, 'x86_64'),
                            ci.cache_key(second, DISTRO, 'x86_64'))

    def test_os_release_is_parsed_as_data_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'os-release'
            path.write_text('ID=ubuntu\nVERSION_ID="24.04"\nNAME="Ubuntu"\n')
            self.assertEqual(ci.read_distro(path), DISTRO)


class CacheFiles(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cache, self.archives = self.root / 'cache', self.root / 'archives'
        self.cache.mkdir()
        self.archives.mkdir()
        self.plan = ci.parse_plan(PLAN)

    def test_sha256_plan_rejects_md5_missing_hash_duplicate_and_path_traversal(self):
        for plan in (PLAN.replace('SHA256:', 'MD5Sum:'), PLAN.replace(HASH, ''),
                     PLAN + PLAN, PLAN.replace(NAME, '../' + NAME),
                     PLAN.replace(str(len(CONTENT)) + ' SHA', '-1 SHA'),
                     "'truncated URI record\n"):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                ci.parse_plan(plan)

    def test_valid_cache_is_copied_and_exactly_verified(self):
        (self.cache / NAME).write_bytes(CONTENT)
        self.assertEqual(ci.stage_cache(self.cache, self.archives, self.plan), 1)
        self.assertEqual((self.archives / NAME).read_bytes(), CONTENT)
        self.assertNotEqual((self.cache / NAME).stat().st_ino,
                            (self.archives / NAME).stat().st_ino)

    def test_same_size_corruption_is_rejected_and_bad_copy_removed(self):
        (self.cache / NAME).write_bytes(b'x' * len(CONTENT))
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            ci.stage_cache(self.cache, self.archives, self.plan)
        self.assertFalse((self.archives / NAME).exists())

    def test_changed_signed_metadata_rejects_previously_valid_archive(self):
        (self.cache / NAME).write_bytes(CONTENT)
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            ci.stage_cache(self.cache, self.archives, {NAME: (len(CONTENT), '0' * 64)})

    def test_size_mismatch_and_old_version_are_rejected(self):
        (self.cache / NAME).write_bytes(CONTENT + b'changed')
        with self.assertRaisesRegex(ValueError, 'size'):
            ci.stage_cache(self.cache, self.archives, self.plan)
        with self.assertRaisesRegex(ValueError, 'stale'):
            ci.stage_cache(self.cache, self.archives, {})

    def test_symlink_hardlink_subdirectory_and_unexpected_file_are_rejected(self):
        external = self.root / 'outside'
        external.write_bytes(CONTENT)
        bad = self.cache / NAME
        cases = (lambda: bad.symlink_to(external),
                 lambda: os.link(external, bad), lambda: bad.mkdir())
        for create in cases:
            with self.subTest(create=create):
                create()
                with self.assertRaises(ValueError):
                    ci.stage_cache(self.cache, self.archives, self.plan)
                if bad.is_dir() and not bad.is_symlink():
                    bad.rmdir()
                else:
                    bad.unlink()
        (self.cache / 'install.sh').write_text('exit 0')
        with self.assertRaises(ValueError):
            ci.stage_cache(self.cache, self.archives, self.plan)
        self.assertEqual(external.read_bytes(), CONTENT)

    def test_symlink_cache_root_is_rejected_without_following_it(self):
        self.cache.rmdir()
        self.cache.symlink_to(self.archives, target_is_directory=True)
        with self.assertRaises(ValueError):
            ci.stage_cache(self.cache, self.archives, self.plan)

    def test_export_contains_only_verified_debs_not_lists_lock_or_partial(self):
        (self.archives / NAME).write_bytes(CONTENT)
        (self.archives / 'partial').mkdir()
        (self.archives / 'lock').touch()
        (self.cache / 'untrusted-script').touch()
        self.assertTrue(ci.export_cache(self.cache, self.archives, self.plan, self.root))
        self.assertEqual([path.name for path in self.cache.iterdir()], [NAME])
        self.assertEqual((self.cache / NAME).read_bytes(), CONTENT)


class InstallationFlow(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = dict(IMAGE, RUNNER_TEMP=str(self.root), GITHUB_OUTPUT=str(self.root / 'output'))
        self.cache = self.root / ('framivra-apt-archives-' + ci.SCHEMA)
        self.cache.mkdir()
        self.commands = []
        self.install_contents = None
        self.fail_update = self.fail_plan = self.fail_download = self.fail_install = False
        self.status = 'installed\ninstalled\n'

    def fake_run(self, command, **kwargs):
        self.commands.append(command)
        self.assertTrue(kwargs['check'])
        if command[0] == 'dpkg-query':
            return subprocess.CompletedProcess(command, 0, stdout=self.status)
        self.assertEqual(command[:5], ['sudo', 'env', 'DEBIAN_FRONTEND=noninteractive', 'LC_ALL=C', 'apt-get'])
        archives = Path(next(s.split('=', 1)[1] for s in command if s.startswith('Dir::Cache::archives=')))
        self.assertNotEqual(archives, self.cache)
        self.assertIn(self.root, archives.parents)
        if command[-1] == 'update':
            if self.fail_update:
                raise subprocess.CalledProcessError(100, command)
            return subprocess.CompletedProcess(command, 0)
        if '--print-uris' in command:
            if self.fail_plan:
                raise subprocess.CalledProcessError(100, command)
            self.assertEqual(list(archives.iterdir()), [])
            return subprocess.CompletedProcess(command, 0, stdout=PLAN)
        if '--download-only' in command:
            if self.fail_download:
                raise subprocess.CalledProcessError(100, command)
            if (archives / NAME).exists():
                self.assertEqual((archives / NAME).read_bytes(), CONTENT)
            (archives / NAME).write_bytes(CONTENT)
            return subprocess.CompletedProcess(command, 0)
        self.install_contents = {path.name: path.read_bytes() for path in archives.iterdir()}
        if self.fail_install:
            raise subprocess.CalledProcessError(100, command)
        return subprocess.CompletedProcess(command, 0)

    def run_install(self):
        with mock.patch.object(ci, 'read_distro', return_value=DISTRO), \
                mock.patch.object(ci.platform, 'machine', return_value='x86_64'), \
                mock.patch.object(ci.subprocess, 'run', side_effect=self.fake_run), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            ci.install(self.env)

    def test_hit_updates_authenticated_indices_verifies_downloads_and_always_installs(self):
        (self.cache / NAME).write_bytes(CONTENT)
        self.run_install()
        self.assertEqual(self.commands[0][-1], 'update')
        for command in self.commands[:-1]:
            for option in ('Acquire::AllowInsecureRepositories=false',
                           'Acquire::AllowDowngradeToInsecureRepositories=false',
                           'Acquire::AllowWeakRepositories=false',
                           'Acquire::Check-Valid-Until=true',
                           'APT::Get::AllowUnauthenticated=false',
                           'APT::Update::Error-Mode=any', 'Acquire::ForceHash=SHA256'):
                self.assertIn(option, command)
        self.assertEqual(self.install_contents, {NAME: CONTENT})
        self.assertEqual(self.commands[-1][0], 'dpkg-query')
        self.assertTrue((self.root / 'output').read_text().endswith('cacheable=true\n'))

    def test_miss_still_downloads_installs_and_populates_archive_only_cache(self):
        self.run_install()
        self.assertEqual(self.install_contents, {NAME: CONTENT})
        self.assertEqual([p.name for p in self.cache.iterdir()], [NAME])

    def test_corruption_and_unexpected_state_use_clean_download_then_install(self):
        for contents in (b'x' * len(CONTENT), CONTENT):
            with self.subTest(contents=contents):
                (self.cache / NAME).write_bytes(contents)
                (self.cache / 'dpkg-state').touch()
                self.run_install()
                self.assertEqual(self.install_contents, {NAME: CONTENT})
                download = [c for c in self.commands if '--download-only' in c and '--print-uris' not in c][-1]
                self.assertTrue(any('clean-archives' in s for s in download))

    def test_partial_verified_files_are_abandoned_after_a_later_hash_failure(self):
        (self.cache / NAME).write_bytes(CONTENT)
        with mock.patch.object(ci, 'stage_cache', side_effect=lambda cache, archives, plan:
                               ((archives / NAME).write_bytes(b'bad'),
                                (_ for _ in ()).throw(ValueError('late failure')))):
            self.run_install()
        self.assertEqual(self.install_contents, {NAME: CONTENT})

    def test_plan_download_and_export_failures_fall_back_to_empty_ordinary_install(self):
        for failure in ('fail_plan', 'fail_download', 'export'):
            self.fail_plan = failure == 'fail_plan'
            self.fail_download = failure == 'fail_download'
            patch = mock.patch.object(ci, 'export_cache', side_effect=OSError('disk full'))
            with self.subTest(failure=failure), contextlib.ExitStack() as stack:
                if failure == 'export':
                    stack.enter_context(patch)
                self.run_install()
                self.assertEqual(self.install_contents, {})
                self.assertTrue((self.root / 'output').read_text().endswith('cacheable=false\n'))

    def test_update_failure_is_fatal_and_never_uses_stale_indices(self):
        self.fail_update = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_install()
        self.assertEqual(len(self.commands), 1)

    def test_final_install_failure_is_fatal_even_after_successful_cache_work(self):
        self.fail_install = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_install()
        self.assertNotIn('cacheable=true', (self.root / 'output').read_text())

    def test_preflight_failure_is_fatal(self):
        self.status = 'installed\nunpacked\n'
        with self.assertRaisesRegex(ValueError, 'fully installed'):
            self.run_install()
        self.assertNotIn('cacheable=true', (self.root / 'output').read_text())

    def test_missing_image_disables_cache_but_update_install_and_preflight_remain(self):
        del self.env['ImageVersion']
        (self.cache / NAME).write_bytes(b'untrusted')
        self.run_install()
        self.assertEqual(len(self.commands), 3)
        self.assertEqual(self.install_contents, {})
        self.assertNotIn('cacheable=true', (self.root / 'output').read_text())

    def test_prepare_outputs_exact_path_key_without_executing_package_tools(self):
        with mock.patch.object(ci, 'read_distro', return_value=DISTRO), \
                mock.patch.object(ci.platform, 'machine', return_value='x86_64'), \
                mock.patch.object(ci.subprocess, 'run') as run, \
                contextlib.redirect_stdout(io.StringIO()):
            ci.prepare(self.env)
        run.assert_not_called()
        output = (self.root / 'output').read_text()
        self.assertIn('path=' + str(self.cache) + '\n', output)
        self.assertIn('key=' + ci.cache_key(IMAGE, DISTRO, 'x86_64') + '\n', output)
        self.assertIn('cacheable=true\n', output)

    def test_main_propagates_install_failure_as_nonzero(self):
        with mock.patch.object(ci, 'install', side_effect=subprocess.CalledProcessError(100, ['apt-get'])), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ci.main(['install']), 1)


if __name__ == '__main__':
    unittest.main()
