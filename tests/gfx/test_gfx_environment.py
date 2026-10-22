"""#48 falsification of environment identity and production cache contracts."""
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from vtx import gfx_environment as env, gfx_cli, hygiene
from tests._support import temporary_root
from tests.gfx._png import png


class UnitEnvironmentIdentity(unittest.TestCase):
    def setUp(self):
        from tests.gfx import _environment
        self.fixture = _environment
        self.root = temporary_root(self)
        self.binary = self.root / 'input.bin'
        self.binary.write_bytes(b'fixture')

    @staticmethod
    def legacy_digest(content):
        return hashlib.sha256(json.dumps(content.hex(), sort_keys=True,
            ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def test_streamed_identity_matches_legacy_record_and_chunk_boundaries(self):
        unit = bytes(range(256))
        for size in (0, 1, 256, 1024 * 1024 - 1, 1024 * 1024, 1024 * 1024 + 1):
            with self.subTest(size=size):
                content = (unit * ((size + 255) // 256))[:size]
                self.binary.write_bytes(content)
                self.assertEqual(self.fixture.collect('cdp', self.binary)['binary'],
                                 self.legacy_digest(content))

        for path in (self.root / 'missing', self.root):
            with self.subTest(path=path), \
                    patch.object(Path, 'open', side_effect=AssertionError('nonfile read')):
                self.assertEqual(self.fixture.collect('cef', path, 'offscreen'), {
                    'schema': env.SCHEMA, 'status': 'input-bound', 'backend': 'cef',
                    'mode': 'offscreen', 'unit_fixture': True, 'binary': str(path)})
        record = self.fixture.collect('cdp', self.binary)
        self.assertNotEqual(record, self.fixture.collect('cli', self.binary))
        self.assertNotEqual(record, self.fixture.collect('cdp', self.binary, 'offscreen'))
        other = self.root / 'same-content.bin'
        other.write_bytes(self.binary.read_bytes())
        self.assertEqual(record, self.fixture.collect('cdp', other))
        self.binary.unlink()
        self.assertEqual(self.fixture.collect('cdp', self.binary), dict(record, binary=str(self.binary)))

    def test_streamed_identity_is_uncached_and_preserves_io_contract(self):
        content = b'a' * (1024 * 1024 + 1)
        self.binary.write_bytes(content)
        saved = self.binary.stat()
        before = self.fixture.collect('cdp', self.binary)
        for position in (0, len(content) // 2, len(content) - 1):
            with self.subTest(position=position):
                changed = bytearray(content)
                changed[position] = ord('b')
                self.binary.write_bytes(changed)
                os.utime(self.binary, ns=(saved.st_atime_ns, saved.st_mtime_ns))
                self.assertNotEqual(before, self.fixture.collect('cdp', self.binary))
        self.binary.write_bytes(content)
        self.assertEqual(before, self.fixture.collect('cdp', self.binary))

        with self.subTest(operation='short reads'), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded read')), \
                patch.object(Path, 'open') as opened:
            stream = opened.return_value.__enter__.return_value
            stream.read.side_effect = [b'\x00\xff', b'\x80', b'\x01', b'']
            record = self.fixture.collect('cdp', self.binary)
        self.assertEqual(record['binary'], self.legacy_digest(b'\x00\xff\x80\x01'))
        opened.assert_called_once_with('rb')
        self.assertEqual(stream.read.call_args_list, [call(1024 * 1024)] * 4)
        failure = OSError('injected read failure')
        with self.subTest(operation='read error'), patch.object(Path, 'open') as opened:
            opened.return_value.__enter__.return_value.read.side_effect = [b'prefix', failure]
            with self.assertRaises(OSError) as caught:
                self.fixture.collect('cdp', self.binary)
        self.assertIs(caught.exception, failure)


class CheckedEnvironmentDigest(unittest.TestCase):
    """Owned file facts; no host inventory, browser or native media commands."""
    def setUp(self):
        from vtx import cache_state
        self.checked = cache_state
        self.root = temporary_root(self).resolve()
        self.path = self.root / 'library.bin'
        self.path.write_bytes(b'library A')
        memo = patch.object(env, '_hashes', {})
        memo.start(); self.addCleanup(memo.stop)

    def test_regular_and_symlink_share_only_unchanged_file_facts(self):
        alias = self.root / 'alias.bin'; alias.symlink_to(self.path)
        expected = (str(self.path), hashlib.sha256(b'library A').hexdigest())
        with patch.object(self.checked, 'file_digest', wraps=self.checked.file_digest) as read:
            self.assertEqual(env._file(self.path), expected)
            self.assertEqual(env._file(alias), expected)
            self.assertEqual(env._file(self.path), expected)
        self.assertEqual(read.call_count, 3 if os.name == 'nt' else 1)

    def test_restored_mtime_replacement_and_alias_retarget_invalidate(self):
        alias = self.root / 'alias.bin'; alias.symlink_to(self.path)
        original = env._file(alias); saved = self.path.stat()
        self.path.write_bytes(b'library B')
        os.utime(self.path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
        self.assertNotEqual(original, env._file(alias))
        replacement = self.root / 'replacement.bin'
        replacement.write_bytes(b'library C')
        os.utime(replacement, ns=(saved.st_atime_ns, saved.st_mtime_ns))
        replacement.replace(self.path)
        self.assertEqual(env._file(alias)[1], hashlib.sha256(b'library C').hexdigest())
        other = self.root / 'other.bin'; other.write_bytes(b'library D')
        alias.unlink(); alias.symlink_to(other)
        self.assertEqual(env._file(alias), (str(other), hashlib.sha256(b'library D').hexdigest()))

    def test_read_failure_is_not_memoized_and_retry_reads_again(self):
        failure = OSError('owned injected read failure')
        with patch.object(self.checked, 'file_digest', side_effect=failure):
            with self.assertRaises(OSError) as caught:
                env._file(self.path)
        self.assertIs(caught.exception, failure)
        self.assertNotIn(str(self.path), env._hashes)
        with patch.object(self.checked, 'file_digest', wraps=self.checked.file_digest) as read:
            self.assertEqual(env._file(self.path)[1], hashlib.sha256(b'library A').hexdigest())
        read.assert_called_once_with(self.path)

    def test_read_time_mutation_and_checked_value_error_fail_closed(self):
        original = self.checked.file_digest
        def changed(path):
            value = original(path)
            self.path.write_bytes(b'library changed during read')
            return value
        with patch.object(self.checked, 'file_digest', side_effect=changed):
            with self.assertRaisesRegex(env.EnvironmentError, 'changed while hashing'):
                env._file(self.path)
        self.assertNotIn(str(self.path), env._hashes)
        with patch.object(self.checked, 'file_digest', side_effect=ValueError('not regular')):
            with self.assertRaisesRegex(env.EnvironmentError, 'not regular'):
                env._file(self.path)
        self.assertNotIn(str(self.path), env._hashes)

    def test_nonregular_and_missing_inputs_never_gain_a_digest(self):
        with patch.object(self.checked, 'file_digest', side_effect=AssertionError('nonfile read')):
            with self.assertRaises(env.EnvironmentError): env._file(self.root)
            with self.assertRaises(OSError): env._file(self.root / 'missing')
        self.assertEqual(env._hashes, {})

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO primitive unavailable')
    def test_regular_to_fifo_open_race_is_nonblocking_and_refused(self):
        original = os.open
        descriptors = []
        def replaced(path, flags, *args, **kwargs):
            if Path(path) == self.path:
                self.assertTrue(flags & os.O_NONBLOCK)
                self.path.unlink(); os.mkfifo(self.path)
                fd = original(path, flags, *args, **kwargs)
                descriptors.append(fd)
                return fd
            return original(path, flags, *args, **kwargs)
        with patch.object(self.checked.os, 'open', side_effect=replaced):
            with self.assertRaisesRegex(env.EnvironmentError, 'not a regular file'):
                env._file(self.path)
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError): os.fstat(descriptors[0])
        self.assertNotIn(str(self.path), env._hashes)

    def test_windows_policy_simulation_still_reads_every_call(self):
        # Only the no-memo branch is simulated; this is not native Windows QA.
        token = lambda path, st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        with patch.object(env, 'os', SimpleNamespace(name='nt')), \
                patch.object(env, '_file_token', side_effect=token), \
                patch.object(self.checked, 'file_digest', wraps=self.checked.file_digest) as read:
            self.assertEqual(env._file(self.path), env._file(self.path))
        self.assertEqual(read.call_count, 2)
        self.assertEqual(env._hashes, {})


class ChromeInstallerTelemetry(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self).resolve() / 'Application'
        self.root.mkdir()
        (self.root / 'chrome.exe').write_bytes(b'browser')
        self.setup = self.root / 'SetupMetrics'; self.setup.mkdir()
        self.pma = self.setup / '1196_13435891563225954.pma'
        self.pma.write_bytes(b'installer histogram')

    def collect(self):
        return env._windows_chrome_runtime(self.root)

    def test_only_direct_pma_changes_are_ignored(self):
        before = self.collect()
        self.pma.write_bytes(b'new histogram')
        (self.setup / 'new.pma').write_bytes(b'new')
        self.assertEqual(before, self.collect())
        for p in self.setup.iterdir():
            p.unlink()
        self.setup.rmdir()
        self.assertEqual(before, self.collect())
        version = self.root / '155.0.8059.40'; version.mkdir()
        for p in (version / 'same.pma', version / 'chrome.dll', version / 'resources.pak',
                  self.root / 'initial_preferences', self.root / 'master_preferences'):
            with self.subTest(path=p.name):
                p.write_bytes(b'resource A')
                original = self.collect(); saved = p.stat()
                p.write_bytes(b'resource B')
                os.utime(p, ns=(saved.st_atime_ns, saved.st_mtime_ns))
                self.assertNotEqual(original, self.collect())

    def test_unknown_members_stay_bound_and_subdirectories_fail_closed(self):
        unknown = self.setup / 'resource.dll'; unknown.write_bytes(b'A')
        before = self.collect()
        unknown.write_bytes(b'B')
        self.assertNotEqual(before, self.collect())
        unknown.unlink()
        (self.setup / 'nested').mkdir()
        with self.assertRaises(env.EnvironmentError):
            self.collect()

    def test_unknown_member_disappearing_with_directory_changes_identity(self):
        unknown = self.setup / 'resource.dll'; unknown.write_bytes(b'A')
        before = self.collect()
        unknown.unlink(); self.pma.unlink(); self.setup.rmdir()
        self.assertNotEqual(before, self.collect())

    def test_e2e_failure_diagnostics_survive_cp1252_parent(self):
        import subprocess
        import sys
        script = Path(__file__).resolve().parents[1] / 'runners' / 'run_e2e.py'
        output = self.root / 'e2e-output'
        code = '''import runpy,sys,subprocess
from unittest.mock import patch
sys.argv = [sys.argv[1], '--repo', sys.argv[2], '--output', sys.argv[3], '--ffmpeg', sys.executable, '--chrome', sys.executable]
results = [subprocess.CompletedProcess([], n, '中文诊断\\n'.encode('utf-8'), b'') for n in (0,0,1)]
with patch.object(subprocess, 'run', side_effect=results):
    runpy.run_path(sys.argv[0], run_name='__main__')
'''
        child_env = dict(os.environ, PYTHONUTF8='0', PYTHONIOENCODING='cp1252')
        result = subprocess.run([sys.executable, '-X', 'utf8=0', '-c', code,
            str(script), str(script.parent.parent), str(output)], env=child_env, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('中文诊断', result.stdout.decode('utf-8'))
        self.assertNotIn(b'UnicodeEncodeError', result.stderr)
        self.assertFalse(json.loads((output / 'results.json').read_text(encoding='utf-8'))['pass'])

        # Exercise the real summary expression without launching the media run;
        # provenance is diagnostic evidence, not an additional pass predicate.
        import ast
        tree = ast.parse(script.read_text(encoding='utf-8'), filename=str(script))
        summary = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'summary'
                               for target in node.targets))
        source_hashes = next(value for key, value in zip(summary.keys, summary.values)
                             if isinstance(key, ast.Constant) and key.value == 'source_sha256')
        expression = compile(ast.Expression(source_hashes), str(script), 'eval')
        fixture_repo = self.root / 'source-repo'
        source_root = fixture_repo / 'vtx'
        sources = {
            'pluginlib/__init__.py': b'# plugin package\n',
            'media/nested/event.py': b'VALUE = 1\n',
            'media/__init__.py': b'# media package\n',
            'event.py': b'VALUE = 0\n',
            '__init__.py': b'# root package\n',
        }
        for name, content in sources.items():
            path = source_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (source_root / 'media' / 'README.txt').write_text('not Python')
        (fixture_repo / 'outside.py').write_text('# not in the package\n')

        def collect_source_hashes():
            return eval(expression, {'repo': fixture_repo, 'hashlib': hashlib})

        with self.subTest(contract='recursive source paths preserve same basenames'):
            before = collect_source_hashes()
            self.assertEqual(list(before), sorted(sources))
            self.assertEqual(before, {name: hashlib.sha256(content).hexdigest()
                                      for name, content in sources.items()})
        with self.subTest(contract='one changed byte updates only its nested digest'):
            changed = source_root / 'media' / 'nested' / 'event.py'
            saved = changed.stat()
            changed.write_bytes(b'VALUE = 2\n')
            os.utime(changed, ns=(saved.st_atime_ns, saved.st_mtime_ns))
            after = collect_source_hashes()
            self.assertEqual(list(after), list(before))
            self.assertEqual(after['media/nested/event.py'], hashlib.sha256(b'VALUE = 2\n').hexdigest())
            self.assertEqual([name for name in before if before[name] != after[name]],
                             ['media/nested/event.py'])

    def test_reparse_telemetry_directory_and_pma_fail_closed(self):
        original = Path.lstat
        for target in (self.setup, self.pma):
            def attributes(p, *args, **kwargs):
                value = original(p, *args, **kwargs)
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400) if p == target else value
            with self.subTest(path=target.name), patch.object(Path, 'lstat', attributes):
                with self.assertRaises(env.EnvironmentError):
                    self.collect()

    def test_only_expected_deletion_races_are_tolerated(self):
        before = self.collect()
        original_iter, original_stat = Path.iterdir, Path.lstat
        def removed_directory(p):
            if p == self.setup:
                raise FileNotFoundError(str(p))
            return original_iter(p)
        with patch.object(Path, 'iterdir', removed_directory):
            self.assertEqual(before, self.collect())
        def removed_leaf(p, *args, **kwargs):
            if p == self.pma:
                raise FileNotFoundError(str(p))
            return original_stat(p, *args, **kwargs)
        with patch.object(Path, 'lstat', removed_leaf):
            self.assertEqual(before, self.collect())
        unknown = self.setup / 'resource.pak'; unknown.write_bytes(b'resource')
        def removed_unknown(p, *args, **kwargs):
            if p == unknown:
                raise FileNotFoundError(str(p))
            return original_stat(p, *args, **kwargs)
        with patch.object(Path, 'lstat', removed_unknown), self.assertRaises(FileNotFoundError):
            self.collect()
        def denied(p):
            if p == self.setup:
                raise PermissionError(str(p))
            return original_iter(p)
        with patch.object(Path, 'iterdir', denied), self.assertRaises(PermissionError):
            self.collect()


class EnvironmentContracts(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self).resolve()
        self.dist = self.root / 'dist'; self.dist.mkdir()
        self.binary = self.dist / 'chrome'; self.binary.write_bytes(b'\x7fELFfixture')
        self.font = self.root / 'font.ttf'; self.font.write_bytes(b'font A')
        self.config = self.root / 'fonts.conf'; self.config.write_text('<fontconfig/>')
        self.lib = self.root / 'libfont.so'; self.lib.write_bytes(b'library A')
        self.tool = self.root / 'fc-list'; self.tool.write_bytes(b'fontconfig tool')
        original_files = env._files
        self.patches = [
            patch.dict(os.environ, {'VTX_GFX': 'cdp', 'FONTCONFIG_FILE': str(self.config),
                'LD_LIBRARY_PATH': '', 'LD_PRELOAD': '', 'LIBGL_DRIVERS_PATH': '',
                'VK_ICD_FILENAMES': '', 'VK_DRIVER_FILES': ''}),
            patch.object(env.shutil, 'which', side_effect=lambda s:
                         str(self.tool) if s in ('fc-list', 'fc-conflist') else s),
            patch.object(env.platform, 'system', return_value='Linux'),
            patch.object(env, '_command', side_effect=self.command),
            patch.object(env, '_python_runtime', return_value={'fixture': True}),
            patch.object(env, '_files', side_effect=lambda paths: original_files(
                [{'/fixture/font.ttf': self.font, '/fixture/libfont.so': self.lib,
                  '/fixture/fonts.conf': self.config, '/etc/machine-id': self.lib,
                  '/proc/sys/kernel/random/boot_id': self.lib, '/etc/ld.so.cache': self.lib}.get(str(p), p)
                 for p in paths]))]

        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def command(self, args):
        if len(args) == 2 and args[1] in ('--version', '--sanity'):
            return 'fixture renderer version 1'
        if args[0] == 'fc-list':
            return '/fixture/font.ttf\n'
        if args[0] == 'fc-conflist':
            return '+ /fixture/fonts.conf: fixture\n'
        if args[0] == 'ldd':
            return 'libfont.so => /fixture/libfont.so (0x1234)\n'
        self.fail(str(args))

    def collect(self):
        # Real temp file hashes + inventory, no renderer and no external command.
        return env.collect('cdp', self.binary)

    def test_font_runtime_config_and_library_bytes_invalidate_with_restored_mtime(self):
        a = self.collect()
        for path in (self.font, self.binary, self.config, self.lib):
            original, st = path.read_bytes(), path.stat()
            path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.assertNotEqual(env.digest(a), env.digest(self.collect()), str(path))
            path.write_bytes(original)
        self.assertEqual(a, self.collect())

    def test_linux_inventory_preserves_cross_drive_native_font_path(self):
        # The Linux protocol inventory is independent of the host filesystem.
        # Simulate the hosted runner's D: font while hashing real temp bytes;
        # no drive mapping or system configuration is created by this fixture.
        physical_font = self.font
        self.font = Path('D:/renderer-fixture/font.ttf')
        original_file, observed = env._file, []
        def hash_input(path):
            if str(path) == str(self.font):
                observed.append(str(path))
                return original_file(physical_font)
            return original_file(path)
        with patch.object(env, '_file', side_effect=hash_input):
            before = self.collect()
            st = physical_font.stat()
            physical_font.write_bytes(b'font B')
            os.utime(physical_font, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.assertNotEqual(env.digest(before), env.digest(self.collect()))
        self.assertTrue(observed)
        self.assertTrue(all(path.startswith('D:') for path in observed))

    def test_new_distribution_member_and_visual_flags_invalidate(self):
        a = self.collect()
        (self.dist / 'resources.pak').write_bytes(b'new runtime resources')
        self.assertNotEqual(a, self.collect())
        with patch.dict(os.environ, VTX_GFX_NAVFREE='1'):
            self.assertNotEqual(a['environment'], self.collect()['environment'])

    def test_png_compression_does_not_invalidate_rgba_identity(self):
        with patch.dict(os.environ, VTX_GFX_PNG_LEVEL='0', VTX_GFX_PAGES='1'):
            a = self.collect()
        with patch.dict(os.environ, VTX_GFX_PNG_LEVEL='9', VTX_GFX_PAGES='7'):
            self.assertEqual(a, self.collect())

    def test_missing_font_unresolved_library_and_unknown_platform_fail_closed(self):
        self.font.unlink()
        with self.assertRaises(env.EnvironmentError):
            self.collect()
        with patch.object(env, '_command', return_value='libcef.so => not found'):
            with self.assertRaises(env.EnvironmentError):
                self.collect()
        with patch.object(env.platform, 'system', return_value='unsupported'):
            with self.assertRaises(env.EnvironmentError):
                self.collect()

    def test_context_pins_rechecks_and_refuses_unknown_certificate(self):
        c = SimpleNamespace(w=6, h=4, fps=30)
        record = self.collect()
        with patch.object(env, 'collect', return_value=record), \
                patch('vtx.media.encoders.find_chrome', return_value=str(self.binary)):
            cert = env.certificate(c)
            self.assertEqual(env.verify_certificate(cert), cert['sha256'])
            env.recheck(c)
            changed = copy.deepcopy(record); changed['driver'] = {'changed': 'driver'}
            with patch.object(env, 'collect', return_value=changed):
                with self.assertRaises(env.EnvironmentError):
                    env.recheck(c)
        with patch.object(env, 'collect', side_effect=env.EnvironmentError('unavailable')), \
                patch('vtx.media.encoders.find_chrome', return_value=str(self.binary)):
            unknown = SimpleNamespace(w=6, h=4, fps=30)
            self.assertEqual(env.resolve(unknown)['status'], 'unknown')
            for _ in range(2):
                with self.assertRaises(env.EnvironmentError):
                    env.certificate(unknown)
        cert['axes']['fonts'] = {}
        with self.assertRaises(env.EnvironmentError):
            env.verify_certificate(cert)

    def test_recheck_diagnoses_runtime_members_without_values_or_host_paths(self):
        before = self.collect()
        before['runtime'] = {'/private/distribution': {
            'SetupMetrics/setup.pma': ['private target', 'secret digest'],
            'version/resources.pak': ['private target', 'secret digest']}}
        after = copy.deepcopy(before)
        del after['runtime']['/private/distribution']['SetupMetrics/setup.pma']
        after['runtime']['/private/distribution']['version/resources.pak'][1] = 'new secret'
        after['environment']['HOME'] = '/private/new-home'
        after['registry'] = {'private preference': 'secret value'}
        c = SimpleNamespace(_gfx_environment=(None, before, self.binary, None))
        with patch.object(env, 'resolve', return_value=before), \
                patch.object(env, 'collect', return_value=after):
            with self.assertRaises(env.EnvironmentError) as error:
                env.recheck(c)
        message = str(error.exception)
        for label in ('runtime/SetupMetrics/setup.pma', 'runtime/version/resources.pak',
                      'environment', 'registry', 'restart with current inputs'):
            self.assertIn(label, message)
        for value in ('/private', 'secret', 'preference'):
            self.assertNotIn(value, message)
        self.assertIs(c._gfx_environment[1], before)

    def test_change_diagnostics_bound_output_and_escape_labels(self):
        before = {'runtime': {'/private': {}}}
        after = {'runtime': {'/private': {str(i): ['target', 'digest'] for i in range(30)}}}
        labels = json.loads(env._changed_inputs(before, after))
        self.assertEqual(len(labels), 21)
        self.assertEqual(labels[-1], '...')
        after = {'runtime': {'/private': {'C:/private/input': [], '\nmember': []}}}
        diagnostic = env._changed_inputs(before, after)
        self.assertNotIn('C:/private', diagnostic)
        self.assertNotIn('\n', diagnostic)
        diagnostic = env._changed_inputs(
            {'host_boot': {'boot_filetime': 'secret before', 'machine_guid': 'unchanged'}},
            {'host_boot': {'boot_filetime': 'secret after', 'machine_guid': 'unchanged'}})
        self.assertEqual(json.loads(diagnostic), ['host_boot/boot_filetime'])

    def test_environment_key_and_receipt_axes_share_dependency_resolver(self):
        from tests.gfx._environment import bind_environment
        from vtx.infra import Ctx
        bind_environment(self)
        (self.root / 'p.html').write_text('<html>text</html>')
        tp = self.root / 'timeline.json'
        tp.write_text(json.dumps({'project': {'fps': 30, 'width': 6, 'height': 4,
            'duration_frames': 1}, 'video_tracks': [{'kind': 'gfx', 'clips': [
                {'src': 'p.html', 'dur': 1, 'dst_in': 0}]}]}))
        a = Ctx(tp); clip = a.tl['video_tracks'][0]['clips'][0]
        key = hygiene.gfx_key(a, clip)
        with patch.dict(os.environ, VTX_GFX='cli'):
            b = Ctx(tp)
            self.assertNotEqual(key, hygiene.gfx_key(b, clip))
            self.assertNotEqual(a.input_fingerprint, b.input_fingerprint)


class PageEnvironmentContracts(unittest.TestCase):
    def setUp(self):
        self.d = temporary_root(self)
        self.environment = {'schema': env.SCHEMA, 'sha256': 'unit-only', 'status': 'input-bound'}
        for i in range(2):
            (self.d / f'f{i:04d}.png').write_bytes(png())

    def inspect(self, **kw):
        return gfx_cli.inspect_page(self.d, 2, 'key', 6, 4,
                                     environment=self.environment, **kw)

    def complete(self, **kw):
        gfx_cli.complete_page(self.d, 2, 'key', 6, 4, verify_inputs=lambda: None,
                              environment=self.environment, **kw)

    def test_old_pngs_and_old_marker_cannot_be_recertified(self):
        self.assertEqual(len(self.inspect()[0]), 2)
        gfx_cli.complete_page(self.d, 2, 'key', 6, 4, verify_inputs=lambda: None)
        self.assertEqual(len(self.inspect()[0]), 2)
        self.complete()
        self.assertEqual(self.inspect(), ([], False))
        self.environment['sha256'] = 'different browser/font/runtime'
        self.assertEqual(len(self.inspect()[0]), 2)

    def test_equal_unknown_records_never_authorize_reuse_or_completion(self):
        self.environment['status'] = 'unknown'
        (self.d / gfx_cli.COMPLETE_FILE).write_text(json.dumps({'environment': self.environment}))
        self.assertEqual(len(self.inspect()[0]), 2)
        with self.assertRaises(gfx_cli.GfxCliError):
            self.complete()

    def test_unknown_marker_schema_or_missing_frame_hashes_cannot_be_recertified(self):
        for changed in ({'schema': 999}, {'schema': True}, {'sha256': []}, {'sha256': None}):
            self.complete()
            marker = self.d / gfx_cli.COMPLETE_FILE
            record = json.loads(marker.read_text()); record.update(changed)
            marker.write_text(json.dumps(record))
            self.assertEqual(len(self.inspect()[0]), 2)

    def test_pending_identity_is_page_bound_and_mixed_producer_never_warm_hits(self):
        gfx_cli.begin_environment_page(self.d, self.environment, 'wrong-key', 2, 6, 4)
        self.assertEqual(len(self.inspect()[0]), 2)
        gfx_cli.begin_environment_page(self.d, self.environment, 'key', 2, 6, 4)
        self.assertEqual(self.inspect(), ([], True))
        with self.assertRaisesRegex(gfx_cli.GfxCliError, 'VTX_GFX=cli'):
            self.complete(mixed_producer=True)
        self.assertEqual(len(self.inspect()[0]), 2)
        self.assertFalse((self.d / gfx_cli.ENV_PENDING_FILE).exists())

    def test_read_only_consumer_rejects_legacy_and_accepts_current_certificate(self):
        from vtx.gfx_cache_reader import resolve_frames, GfxCacheError
        c = SimpleNamespace(root=self.d, cache=self.d, w=6, h=4, fps=30,
                            tl={'project': {}})
        source = SimpleNamespace(directory=self.d, frame_count=2)
        # The directory's actual key is used by the production reader.
        def publish():
            gfx_cli.complete_page(self.d, 2, self.d.name, 6, 4, verify_inputs=lambda: None,
                                  environment=self.environment)
        with patch('vtx.frame_source.png_source', return_value=source), \
                patch.object(env, 'certificate', return_value=self.environment), \
                patch.object(env, 'recheck'), patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS=''):
            with self.assertRaises(GfxCacheError):
                resolve_frames(c, {'src': 'p.html', 'dur': 2})
            publish()
            self.assertEqual(len(resolve_frames(c, {'src': 'p.html', 'dur': 2})), 2)

    def test_frame_source_requires_certificate_and_rejects_valid_png_replacement(self):
        from vtx.frame_source import png_source
        c = SimpleNamespace(root=self.d, cache=self.d.parent, w=6, h=4, fps=30,
                            tl={'project': {}})
        with patch('vtx.frame_source.gfx_dir', return_value=self.d), \
                patch.object(env, 'certificate', return_value=self.environment):
            source = png_source(c, {'src': 'p.html', 'dur': 2})
            with self.assertRaisesRegex(ValueError, 'certificate missing'):
                source.require_ready()
            gfx_cli.complete_page(self.d, 2, self.d.name, 6, 4,
                                  verify_inputs=lambda: None, environment=self.environment)
            source.require_ready()
            (self.d / 'f0001.png').write_bytes(png(value=77))
            with self.assertRaisesRegex(ValueError, 'digest changed'):
                source.require_ready()


class ArchivedEnvironmentEvidence(unittest.TestCase):
    def test_archived_full_frame_comparisons_match_committed_pngs(self):
        import hashlib
        from PIL import Image
        root = Path(__file__).parents[1] / 'fixtures/gfx-environment'
        report = json.loads((root / 'acceptance-2026-10-18.json').read_text(encoding='utf-8'))
        self.assertTrue(report['passed'])
        self.assertTrue(report['cef_runtime_negative']['worker_unchanged'])
        self.assertNotEqual(report['cef_runtime_negative']['library_before'],
                            report['cef_runtime_negative']['library_after'])
        pairs = {'font_change': ('cdp-font-a', 'cdp-font-b'),
                 'browser_change': ('cdp-font-b', 'alternate-browser'),
                 'cdp_cli': ('cdp-font-b', 'cli-font-b'),
                 'cef_library_replacement': ('cef-runtime-a', 'cef-runtime-b'),
                 'cef_font_change': ('cef-runtime-a', 'cef-font-a')}
        for comparison, sides in pairs.items():
            self.assertEqual(len(report['comparisons'][comparison]), 4)
            for row in report['comparisons'][comparison]:
                pixels = []
                for side in sides:
                    path = root / side / row['frame']
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                     report['runs'][side]['frames'][row['frame']])
                    with Image.open(path) as im:
                        self.assertEqual(im.size, (640, 180))
                        pixels.append(im.convert('RGBA').tobytes())
                self.assertEqual(pixels[0] == pixels[1], row['equal_rgba'])
