"""Managed source deployment: real local Git transactions, no network/packages."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import install_source as install
from vtx.runtime.managed import configure_environment


class SourceInstall(unittest.TestCase):
    def setUp(self):
        # These local filesystem fixtures do not perform elevated deployments.
        elevation = mock.patch.object(install, 'windows_elevated', return_value=False)
        elevation.start()
        self.addCleanup(elevation.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='framivra-source-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'managed installation'
        self.root.mkdir()
        self.upstream = self.base / 'upstream'
        self.upstream.mkdir()
        self.run_git('init', '-b', 'main')
        self.run_git('config', 'user.name', 'Test')
        self.run_git('config', 'user.email', 'test@example.invalid')
        for name in install.REQUIRED_SOURCE:
            path = self.upstream / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# fixture\n', encoding='utf-8')
        (self.upstream / '.gitignore').write_text('config.local.json\n*.egg-info/\n', encoding='utf-8')
        self.commit('initial')

    def run_git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.upstream, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.run_git('add', '-A')
        self.run_git('commit', '-m', message)
        return self.run_git('rev-parse', 'HEAD')

    def deploy(self, update=False):
        return install.update_source(self.root, str(self.upstream), 'main', update)

    def test_default_locations_are_shared_across_bootstrap_and_host(self):
        home = Path('/home/test')
        self.assertEqual(install.default_root('linux', home, {}), home / '.local/share/framivra')
        self.assertEqual(install.default_root('darwin', home, {}), home / '.local/share/framivra')
        self.assertEqual(install.default_root('win32', home, {'LOCALAPPDATA': '/users/test/local'}), Path('/users/test/local/framivra'))

    def test_plan_is_read_only(self):
        destination = self.base / 'not-created'
        found = {name: '/tools/' + name for name in (*install.TOOL_NAMES, 'chrome')}
        with mock.patch.object(install, 'tools', return_value=found), mock.patch.object(install, 'command') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(['--root', str(destination), '--plan']), 0)
        self.assertFalse(destination.exists())
        run.assert_not_called()

    def test_elevated_windows_install_stops_before_creating_root_or_tools(self):
        destination = self.base / 'elevated-root'
        with mock.patch.object(install, 'windows_elevated', return_value=True), \
                mock.patch.object(install, 'prepare_tools') as prepare, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(install.main(['--root', str(destination), '--yes']), 1)
        self.assertIn('non-administrator PowerShell', errors.getvalue())
        self.assertFalse(destination.exists())
        prepare.assert_not_called()

    def test_elevated_read_only_plan_never_probes_elevation_or_writes(self):
        destination = self.base / 'plan-root'
        found = {name: '/tools/' + name for name in (*install.TOOL_NAMES, 'chrome')}
        with mock.patch.object(install, 'windows_elevated', return_value=True) as elevation, \
                mock.patch.object(install, 'tools', return_value=found), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(['--root', str(destination), '--plan']), 0)
        elevation.assert_not_called()
        self.assertFalse(destination.exists())

    def test_persistent_root_inherits_windows_acl_but_keeps_posix_private_mode(self):
        for system in ('win32', 'linux'):
            destination = self.base / (system + '-root')
            with self.subTest(system=system), \
                    mock.patch.object(install.sys, 'platform', system), \
                    mock.patch.object(install.platform, 'machine', return_value='AMD64'), \
                    mock.patch.object(install, 'windows_elevated', return_value=False), \
                    mock.patch.object(Path, 'mkdir', autospec=True) as mkdir, \
                    mock.patch.object(install, 'installation_lock', side_effect=install.InstallError('after root creation')), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(install.main(['--root', str(destination), '--yes']), 1)
            self.assertIn('after root creation', errors.getvalue())
            options = {} if system == 'win32' else {'mode': 0o700}
            mkdir.assert_called_once_with(destination, parents=True, exist_ok=True, **options)
            self.assertFalse(destination.exists())

    def test_real_clone_pin_resume_and_explicit_update(self):
        first = self.deploy()
        self.assertEqual(first, self.run_git('rev-parse', 'HEAD'))
        self.assertEqual(json.loads((self.root / 'installation.json').read_text())['status'], 'preparing')
        (self.upstream / 'new-file').write_text('new', encoding='utf-8')
        second = self.commit('new')
        self.assertEqual(self.deploy(), first)
        self.assertEqual(self.deploy(update=True), second)
        self.assertEqual(self.run_git('rev-parse', 'HEAD', cwd=self.root / 'source'), second)

    def test_incompatible_update_does_not_change_installed_head(self):
        first = self.deploy()
        (self.upstream / 'scripts/install_source.py').unlink()
        self.commit('incompatible source')
        with self.assertRaisesRegex(install.InstallError, 'required regular blob'):
            self.deploy(update=True)
        self.assertEqual(self.run_git('rev-parse', 'HEAD', cwd=self.root / 'source'), first)
        self.assertEqual(self.deploy(), first)

    def test_partial_venv_without_pip_can_be_repaired(self):
        import venv
        with mock.patch.object(venv.EnvBuilder, '_setup_pip', side_effect=subprocess.CalledProcessError(1, 'ensurepip')):
            with self.assertRaisesRegex(install.InstallError, 'ensurepip'):
                install.prepare_python(self.root, self.upstream, {'chrome': '/unused/chrome'})
        real_command = install.command
        def no_network(argv, **kwargs):
            if '-c' in argv:
                return real_command(argv, **kwargs)
            return ''  # Only skip network pip install; real ensurepip is vendored.
        with mock.patch.object(install, 'command', side_effect=no_network), mock.patch.object(install.python_packages, 'install', return_value={}):
            python, env = install.prepare_python(self.root, self.upstream, {'chrome': '/unused/chrome'})
        subprocess.run([str(python), '-I', '-c', 'import pip'], env=env, check=True, capture_output=True)

    def test_dirty_checkout_and_local_commits_are_not_overwritten(self):
        self.deploy()
        source = self.root / 'source'
        (source / 'untracked.txt').write_text('keep me', encoding='utf-8')
        with self.assertRaisesRegex(install.InstallError, 'local changes'):
            self.deploy(update=True)
        self.assertEqual((source / 'untracked.txt').read_text(), 'keep me')
        (source / 'untracked.txt').unlink()
        self.run_git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'local', cwd=source)
        with self.assertRaisesRegex(install.InstallError, 'local commits'):
            self.deploy(update=True)

    def test_ignored_incoming_collision_preserves_user_file(self):
        self.deploy()
        private = self.root / 'source/config.local.json'
        private.write_text('private', encoding='utf-8')
        (self.upstream / 'config.local.json').write_text('upstream', encoding='utf-8')
        self.run_git('add', '-f', 'config.local.json')
        self.run_git('commit', '-m', 'new tracked configuration')
        with self.assertRaisesRegex(install.InstallError, 'ignored local files'):
            self.deploy(update=True)
        self.assertEqual(private.read_text(), 'private')

    @unittest.skipIf(os.name == 'nt', 'symlink fixture requires POSIX privileges')
    def test_symlinked_root_and_record_are_rejected(self):
        link = self.base / 'redirect'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(install.InstallError):
            install.physical(link / 'source')
        secret = self.base / 'outside.json'
        secret.write_text('{}', encoding='utf-8')
        (self.root / 'installation.json').symlink_to(secret)
        with self.assertRaises(install.InstallError):
            self.deploy()
        self.assertFalse((self.root / 'source').exists())

    def test_interrupted_fetch_can_be_retried_without_adopting_a_clone(self):
        real_git = install.git
        def fail_fetch(*args, **kwargs):
            if 'fetch' in args:
                raise install.InstallError('network interrupted')
            return real_git(*args, **kwargs)
        with mock.patch.object(install, 'git', side_effect=fail_fetch):
            with self.assertRaisesRegex(install.InstallError, 'network interrupted'):
                self.deploy(update=True)
        self.assertFalse((self.root / 'source').exists())
        pending = json.loads((self.root / '.source-progress.json').read_text())
        approved = pending['commit']
        stage = self.root / pending['stage']
        self.assertTrue((stage / '.git').is_dir())
        (self.upstream / 'later').write_text('main advanced during interruption')
        later = self.commit('newer main while interrupted')
        self.assertEqual(install.update_source(self.root, str(self.upstream), 'main', update=True, source_commit=later), approved)
        self.assertFalse(stage.exists())
        self.assertFalse((self.root / '.source-progress.json').exists())
        self.assertEqual(self.deploy(update=True), later)

    def test_clean_approved_checkout_recovers_after_receipt_publication_interruption(self):
        first = self.deploy()
        (self.upstream / 'later').write_text('new source')
        second = self.commit('update')
        real_git = install.git
        def crash_after_checkout(*args, **kwargs):
            value = real_git(*args, **kwargs)
            if 'checkout' in args:
                raise install.InstallError('interrupted after checkout')
            return value
        with mock.patch.object(install, 'git', side_effect=crash_after_checkout):
            with self.assertRaisesRegex(install.InstallError, 'after checkout'):
                self.deploy(update=True)
        self.assertEqual(json.loads((self.root / 'installation.json').read_text())['commit'], first)
        self.assertEqual(self.deploy(), second)
        self.assertEqual(json.loads((self.root / 'installation.json').read_text())['status'], 'preparing')
        self.assertFalse((self.root / '.source-progress.json').exists())

    def test_fresh_bootstrap_pin_is_source_identity_but_retry_does_not_follow_new_main(self):
        first = self.run_git('rev-parse', 'HEAD')
        (self.upstream / 'later').write_text('later')
        second = self.commit('advance main before source fetch')
        self.assertEqual(install.update_source(self.root, str(self.upstream), 'main', source_commit=first), first)
        self.assertEqual(install.update_source(self.root, str(self.upstream), 'main', source_commit=second), first)
        self.assertEqual(install.update_source(self.root, str(self.upstream), 'main', update=True, source_commit=second), second)

    def test_no_system_reuses_receipt_bound_keg_only_pair(self):
        selected = {}
        for name in (*install.TOOL_NAMES, 'chrome'):
            path = self.root / 'private-full' / 'bin' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('native fixture')
            selected[name] = str(path)
        (self.root / 'installation.json').write_text(json.dumps(dict(
            schema=install.SCHEMA, sourceRoot=str(self.root / 'source'), tools=selected)))
        capabilities = ' '.join((*install.REQUIRED_ENCODERS, *install.REQUIRED_FILTERS))
        with mock.patch.object(install.sys, 'platform', 'darwin'), mock.patch.object(install.shutil, 'which', return_value=None), mock.patch.object(install, 'native_browser', return_value=None), mock.patch.object(install, 'command', return_value=capabilities) as run:
            actual = install.prepare_tools(True, self.root)
        self.assertEqual(actual['ffmpeg'], selected['ffmpeg'])
        self.assertEqual(actual['ffprobe'], selected['ffprobe'])
        self.assertEqual(run.call_count, 2)  # capability inventory, no package install

    def test_source_recovery_does_not_adopt_a_journal_escaping_root(self):
        (self.root / '.source-progress.json').write_text(json.dumps(dict(
            schema='framivra.source-progress/v1', kind='clone', repository=str(self.upstream),
            ref='main', commit=self.run_git('rev-parse', 'HEAD'), stage='../upstream')))
        with self.assertRaisesRegex(install.InstallError, 'staging path'):
            self.deploy()
        self.assertFalse((self.root / 'source').exists())

    def test_same_update_after_dependency_failure_keeps_approved_source(self):
        approved = self.run_git('rev-parse', 'HEAD')
        args = ['--root', str(self.root), '--repo', str(self.upstream), '--yes', '--update']
        with mock.patch.object(install, 'prepare_tools', return_value={}), mock.patch.object(install, 'verify_native_tools'), mock.patch.object(install, 'prepare_python', side_effect=install.InstallError('fixture dependency failure')), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(install.main(args), 1)
            (self.upstream / 'advanced').write_text('main moved after source approval')
            self.commit('main advanced during dependency interruption')
            self.assertEqual(install.main(args), 1)
        self.assertEqual(self.run_git('rev-parse', 'HEAD', cwd=self.root / 'source'), approved)
        self.assertEqual(json.loads((self.root / 'installation.json').read_text())['status'], 'preparing')

    def test_foreign_receipt_cannot_authorize_an_unmanaged_venv(self):
        foreign = self.root / 'venv' / 'marker'
        foreign.parent.mkdir()
        foreign.write_text('unrelated environment')
        receipts = ({}, dict(schema='other/v1', status='preparing', repository=str(self.upstream), sourceRoot=str(self.root / 'source')),
                    dict(schema=install.SCHEMA, status='preparing', repository=str(self.upstream), sourceRoot=str(self.base / 'other/source')))
        for receipt in receipts:
            with self.subTest(receipt=receipt):
                encoded = json.dumps(receipt)
                (self.root / 'installation.json').write_text(encoded)
                with mock.patch.object(install, 'prepare_tools') as native, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(install.main(['--root', str(self.root), '--repo', str(self.upstream), '--yes']), 1)
                native.assert_not_called()
                self.assertEqual(foreign.read_text(), 'unrelated environment')
                self.assertEqual((self.root / 'installation.json').read_text(), encoded)
                self.assertFalse((self.root / 'source').exists())

    def test_unowned_clone_is_never_adopted(self):
        self.run_git('clone', str(self.upstream), str(self.root / 'source'))
        with self.assertRaisesRegex(install.InstallError, 'ownership'):
            self.deploy()

    def test_source_channels_are_explicit(self):
        for ref in ('stable', 'latest', '--upload-pack=evil', 'branch/guess'):
            with self.subTest(ref=ref), self.assertRaises(install.InstallError):
                install.update_source(self.root, str(self.upstream), ref)
        self.assertFalse((self.root / 'source').exists())

    def test_native_plan_has_no_ai_and_deduplicates_windows_ffmpeg(self):
        found = {'git': '/git', 'chrome': '/chrome'}
        plans = install.platform_packages(found, 'win32')
        ids = [plan[plan.index('--id') + 1] for plan in plans]
        self.assertEqual(ids.count('Gyan.FFmpeg'), 1)
        self.assertIn('ChrisBagwell.SoX', ids)
        self.assertNotIn('torch', ' '.join(install.PYTHON_PACKAGES).lower())
        self.assertIn('scipy', ' '.join(install.PYTHON_PACKAGES))

    def test_windows_plan_names_portable_git_and_conditional_runtime_without_probes(self):
        with mock.patch.object(install.windows, 'ensure_python_runtime') as runtime:
            plans = install.platform_packages({}, 'win32')
            self.assertIn(['managed-original-download', '--id', 'Git.MinGit'], plans)
            conditional = ['conditional-python-runtime', '--id', 'Microsoft.VCRedist.2015+.x64']
            self.assertIn(conditional, plans)
            found = {name: '/tools/' + name for name in (*install.TOOL_NAMES, 'chrome')}
            self.assertEqual(install.platform_packages(found, 'win32'), [conditional])
        runtime.assert_not_called()

    def test_windows_runtime_failure_preserves_prepared_dependencies_and_never_reaches_ready(self):
        def prepared(root, source, found, **kwargs):
            state = install.json_record(root / 'installation.json')
            install.atomic_json(root / 'installation.json', dict(state, pythonDependenciesPrepared=True))
            return root / 'venv/Scripts/python.exe', {'PATH': '/fixture'}
        with mock.patch.object(install.sys, 'platform', 'win32'), \
                mock.patch.object(install.platform, 'machine', return_value='AMD64'), \
                mock.patch.object(install, 'prepare_tools', return_value={}), \
                mock.patch.object(install, 'verify_native_tools'), \
                mock.patch.object(install, 'prepare_python', side_effect=prepared), \
                mock.patch.object(install.windows, 'ensure_python_runtime',
                                  side_effect=install.InstallError('runtime requires restart')) as runtime, \
                mock.patch.object(install, 'prepare_configuration') as configure, \
                mock.patch.object(install, 'verify') as verify, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(install.main(['--root', str(self.root), '--repo', str(self.upstream), '--yes']), 1)
        state = install.json_record(self.root / 'installation.json')
        self.assertEqual(state['status'], 'preparing')
        self.assertTrue(state['sourceApproved'])
        self.assertTrue(state['pythonDependenciesPrepared'])
        self.assertEqual(runtime.call_args.kwargs['network'], 'cn')
        self.assertTrue(runtime.call_args.kwargs['yes'])
        self.assertFalse(runtime.call_args.kwargs['no_system'])
        configure.assert_not_called()
        verify.assert_not_called()

    def test_linux_fontconfig_and_ensurepip_are_planned_without_replacing_browser(self):
        found = {name: '/tools/' + name for name in (*install.TOOL_NAMES, 'chrome')}
        self.assertEqual(install.platform_packages(found, 'linux', 'apt-get'),
                         [['apt-get', 'install', '-y', 'fontconfig']])
        found.update({'fc-cache': '/tools/fc-cache', 'fc-list': '/tools/fc-list'})
        self.assertEqual(install.platform_packages(found, 'linux', 'apt-get', needs_venv=True),
                         [['apt-get', 'install', '-y', 'python3-venv']])
        self.assertEqual(install.platform_packages(found, 'linux', 'apt-get'), [])

    def test_existing_user_configuration_is_preserved(self):
        target = self.root / 'config.json'
        original = '{"asr":{"enabled":true},"private":"preserve"}\n'
        target.write_text(original, encoding='utf-8')
        install.prepare_configuration(self.root, self.upstream, self.root / 'venv/bin/python')
        self.assertEqual(target.read_text(), original)

    def test_failed_dependency_preparation_never_registers_ready(self):
        with mock.patch.object(install, 'prepare_tools', return_value={}), mock.patch.object(install, 'verify_native_tools'), mock.patch.object(install, 'prepare_python', side_effect=install.InstallError('fixture failure')), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(install.main(['--root', str(self.root), '--repo', str(self.upstream), '--yes']), 1)
        self.assertEqual(json.loads((self.root / 'installation.json').read_text())['status'], 'preparing')
        self.assertFalse((self.root / 'bin/vtx').exists())


class ManagedEnvironment(unittest.TestCase):
    def test_only_prefix_bound_receipt_is_used_and_explicit_choices_survive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source, prefix = root / 'source', root / 'venv'
            source.mkdir(); prefix.mkdir()
            config = root / 'config.json'
            config.write_text('{}', encoding='utf-8')
            tools = {}
            for name in (*install.TOOL_NAMES, 'chrome'):
                path = root / 'tools' / name
                path.parent.mkdir(exist_ok=True)
                path.write_text('fixture', encoding='utf-8')
                tools[name] = str(path)
            font_env = {}
            if os.name != 'nt' and sys_platform() == 'linux':
                font = root / 'fonts/fonts.conf'
                font.parent.mkdir(); font.write_text('<fontconfig/>', encoding='utf-8')
                font_env = {'FONTCONFIG_FILE': str(font)}
            receipt = dict(schema=install.SCHEMA, sourceRoot=str(source), commit='a' * 40,
                           status='ready', profile='non-ai', renderer='cdp', tools=tools,
                           configFile=str(config), environment=font_env)
            (root / 'installation.json').write_text(json.dumps(receipt), encoding='utf-8')
            env = {'PATH': '/host-trusted', 'VTX_CONFIG': '/explicit/config.json'}
            self.assertTrue(configure_environment(prefix, source, env))
            self.assertEqual(env['VTX_CONFIG'], '/explicit/config.json')
            self.assertEqual(env['VTX_GFX'], 'cdp')
            self.assertIn(str(root / 'tools'), env['PATH'])
            self.assertFalse(configure_environment(root / 'unrelated-python', source, {}))
            receipt['sourceRoot'] = '/other/source'
            (root / 'installation.json').write_text(json.dumps(receipt), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'another source'):
                configure_environment(prefix, source, {})


def sys_platform():
    import sys
    return sys.platform
