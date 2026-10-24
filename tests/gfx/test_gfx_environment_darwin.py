"""CPU contracts for #200; synthetic Mach-O/cache data is not Mac acceptance."""
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import struct
import tempfile
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from vtx.cache import cache_state
from vtx.render import gfx_environment as env


def macho(dependencies=(), rpaths=()):
    commands = []
    for kind, values in ((0xc, dependencies), (0x8000001c, rpaths)):
        for value in values:
            raw = value.encode() + b'\0'
            size = (12 + len(raw) + 7) // 8 * 8
            commands.append(struct.pack('<III', kind, size, 12) + raw + b'\0' * (size - 12 - len(raw)))
    data = b''.join(commands)
    return struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, len(commands), len(data), 0, 0) + data


def cache_header(value):
    data = bytearray(464)
    data[:16] = b'dyld_v1  arm64e'.ljust(16, b'\0')
    struct.pack_into('<I', data, 16, 464)
    data[88:104] = value
    return data


class DarwinEnvironmentContracts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.app = self.root / 'Google Chrome.app'
        self.binary = self.app / 'Contents/MacOS/Google Chrome'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(macho(['/usr/lib/libSystem.B.dylib']))
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'com.google.Chrome', 'CFBundleExecutable': 'Google Chrome'}))
        self.framework = self.app / 'Contents/Frameworks/Chrome.framework'
        version = self.framework / 'Versions/A'
        version.mkdir(parents=True)
        self.library = version / 'Chrome'
        self.library.write_bytes(macho(['/usr/lib/libSystem.B.dylib']))
        (self.framework / 'Versions/Current').symlink_to('A', target_is_directory=True)
        (self.framework / 'Chrome').symlink_to('Versions/Current/Chrome')
        self.resource = version / 'resources.pak'
        self.resource.write_bytes(b'resource A')
        self.font = self.root / 'font.otf'
        self.font.write_bytes(b'font A')
        self.ct = self.root / 'CoreText.framework'
        (self.ct / 'Resources').mkdir(parents=True)
        self.fontconfig = self.ct / 'Resources/DefaultFontFallbacks.plist'
        self.fontconfig.write_bytes(b'fallback A')
        self.uuid = bytes(range(1, 17))
        self.cache = self.root / 'dyld_shared_cache_arm64e'
        header = cache_header(self.uuid)
        struct.pack_into('<II', header, 392, 464, 1)
        self.sub_uuid = bytes(range(17, 33))
        self.cache.write_bytes(header + self.sub_uuid + struct.pack('<Q', 4096) + b'.01'.ljust(32, b'\0'))
        self.subcache = self.cache.with_name(self.cache.name + '.01')
        self.subcache.write_bytes(cache_header(self.sub_uuid) + b'system library A')
        self.system_file = self.root / 'SystemVersion.plist'
        self.system_file.write_bytes(b'OS build A')
        self.boot = '01234567-89ab-cdef-0123-456789abcdef'
        contains = lambda value: value == b'/usr/lib/libSystem.B.dylib'
        original_files = env._files
        system_paths = {'/System/Library/CoreServices/SystemVersion.plist',
                        '/usr/lib/dyld', '/usr/sbin/sysctl'}
        patches = [
            patch.dict(os.environ, {'HOME': str(self.root), 'VTX_GFX': 'cdp'}, clear=True),
            patch.object(env.platform, 'system', return_value='Darwin'),
            patch.object(env.platform, 'machine', return_value='arm64'),
            patch.object(env, '_darwin_dyld', return_value=(self.cache, self.uuid)),
            patch.object(env.ctypes, 'CDLL', return_value=SimpleNamespace(_dyld_shared_cache_contains_path=contains)),
            patch.object(env, '_CORETEXT', str(self.ct)),
            patch.object(env, '_darwin_font_inventory', return_value=([str(self.font)], ['Fixture-Regular'])),
            patch.object(env, '_files', side_effect=lambda paths: original_files(
                self.system_file if str(p) in system_paths else p for p in paths)),
            patch.object(env, '_python_runtime', return_value={'unit_fixture': True}),
            patch.object(env, '_command', side_effect=self.command),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def command(self, args):
        if args == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
            return self.boot
        if args == [str(self.binary), '--version']:
            return 'Google Chrome fixture 155'
        self.fail('Unexpected external command: ' + repr(args))

    def collect(self):
        return env.collect('cdp', self.binary)

    def test_cef_bundle_exact_identity_and_framework_closure(self):
        self.framework.rename(self.framework.with_name('Chromium Embedded Framework.framework'))
        def command(args):
            if args == [str(self.binary), '--sanity']:
                return 'CEF fixture sanity native arm64'
            return self.command(args)
        with patch.object(env, '_command', side_effect=command):
            record = env.collect('cef', self.binary, 'a0')
        self.assertEqual(record['platform_resolver'], 'darwin-arm64-cef/1')
        self.assertEqual(record['launch_flags'], ['mode=a0', 'sync-canary=exact', 'sync-mode=n',
            'reference-context=shared', 'png-encoder=spng', 'png-level=1',
            'png-filter=sub_up', 'encode-threads=2', 'capture-fps=120',
            '--use-mock-keychain'])
        self.assertEqual(record['cef_configuration']['runtime']['sync_mode'], 'n')
        self.assertEqual(record['cef_configuration']['runtime']['capture_fps'], 120)
        self.assertEqual(record['cef_configuration']['scheduling']['pages'], 7)
        with patch.object(env, '_command', side_effect=command), \
                patch.dict(os.environ, VTX_CEF_SYNC_MODE='p', VTX_CEF_PNG_FILTER='none',
                           VTX_GFX_PAGES='2', VTX_CEF_CAPTURE_FPS='60'):
            other = env.collect('cef', self.binary, 'a0')
        self.assertIn('sync-mode=p', other['launch_flags'])
        self.assertIn('png-filter=none', other['launch_flags'])
        self.assertIn('capture-fps=60', other['launch_flags'])
        self.assertEqual(other['cef_configuration']['runtime']['capture_fps'], 60)
        self.assertEqual(other['cef_configuration']['scheduling']['pages'], 2)
        self.assertNotEqual(env.digest(record), env.digest(other))
        with patch.object(env, 'resolve', return_value=record):
            certificate = env.certificate(SimpleNamespace(w=32, h=16, fps=30))
        self.assertEqual(certificate['axes']['fps'], 30)
        self.assertEqual(certificate['axes']['cef_configuration']['runtime']['capture_fps'], 120)
        self.assertEqual(env.verify_certificate(certificate), certificate['sha256'])

    def test_complete_native_identity_keeps_pixel_equivalence_unproven(self):
        record = self.collect()
        self.assertEqual(record, self.collect())
        self.assertEqual(record['status'], 'input-bound')
        self.assertEqual(record['pixel_equivalence'], 'unproven')
        self.assertEqual(len(record['libraries']), 2)
        self.assertIn(str(self.library), record['dependencies'])
        with patch.object(env, 'resolve', return_value=record):
            cert = env.certificate(SimpleNamespace(w=32, h=16, fps=30))
        self.assertEqual(env.verify_certificate(cert), cert['sha256'])

    def test_same_size_bytes_with_restored_mtime_invalidate_all_content_axes(self):
        original = self.collect()
        for path in (self.resource, self.font, self.fontconfig, self.subcache, self.system_file):
            with self.subTest(path=path):
                raw, st = path.read_bytes(), path.stat()
                path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
                os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
                self.assertNotEqual(original, self.collect())
                path.write_bytes(raw)
        self.assertEqual(original, self.collect())

    def test_bundle_binary_and_membership_invalidate(self):
        first = self.collect()
        self.library.write_bytes(self.library.read_bytes() + b'new binary tail')
        self.assertNotEqual(first, self.collect())
        self.library.write_bytes(macho(['/usr/lib/libSystem.B.dylib']))
        self.assertEqual(first, self.collect())
        (self.framework / 'new-resource.pak').write_bytes(b'new member')
        self.assertNotEqual(first, self.collect())

    def test_font_faces_and_boot_changes_invalidate(self):
        original = self.collect()
        with patch.object(env, '_darwin_font_inventory', return_value=([str(self.font)], ['Other-Face'])):
            self.assertNotEqual(original, self.collect())
        self.boot = '12345678-89ab-cdef-0123-456789abcdef'
        self.assertNotEqual(original, self.collect())

    def test_global_font_preferences_membership_and_bytes_invalidate(self):
        original = self.collect()
        preferences = self.root / 'Library/Preferences'
        preferences.mkdir(parents=True)
        p = preferences / '.GlobalPreferences.plist'
        p.write_bytes(b'font smoothing A')
        added = self.collect()
        self.assertNotEqual(original, added)
        p.write_bytes(b'font smoothing B')
        self.assertNotEqual(added, self.collect())

    def test_chrome_application_font_and_language_preferences_invalidate(self):
        preferences = self.root / 'Library/Preferences'
        preferences.mkdir(parents=True)
        path = preferences / 'com.google.Chrome.plist'
        absent = self.collect()
        path.write_bytes(plistlib.dumps({'AppleFontSmoothing': 0, 'AppleLanguages': ['en']}))
        before = self.collect()
        self.assertNotEqual(absent, before)
        st = path.stat()
        path.write_bytes(plistlib.dumps({'AppleFontSmoothing': 3, 'AppleLanguages': ['ja']}))
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertNotEqual(env.digest(before), env.digest(self.collect()))
        path.unlink()
        self.assertEqual(absent, self.collect())

    def test_actual_helper_domain_and_by_host_preferences_are_bound(self):
        helper_app = self.library.parent / 'Helpers/Google Chrome Helper.app'
        helper = helper_app / 'Contents/MacOS/Google Chrome Helper'
        helper.parent.mkdir(parents=True)
        helper.write_bytes(macho())
        domain = 'org.actual.fixture-renderer'
        info = helper_app / 'Contents/Info.plist'
        info.write_bytes(plistlib.dumps({'CFBundleIdentifier': domain}))
        before = self.collect()
        self.assertEqual(before['fonts']['inventory']['application_domains'],
                         ['com.google.Chrome', domain])
        preferences = self.root / 'Library/Preferences'
        (preferences / 'ByHost').mkdir(parents=True)
        for relative in (domain + '.plist', 'ByHost/' + domain + '.fixture-host.plist',
                         'ByHost/com.google.Chrome.fixture-host.plist',
                         'ByHost/com.apple.CoreText.fixture-host.plist'):
            path = preferences / relative
            with self.subTest(relative=relative):
                path.write_bytes(plistlib.dumps({'AppleLanguages': ['ja']}))
                self.assertNotEqual(before, self.collect())
                path.unlink()
                self.assertEqual(before, self.collect())

    def test_missing_helper_preference_domain_fails_closed(self):
        helper = self.library.parent / 'Helpers/Unknown.app/Contents/MacOS/Helper'
        helper.parent.mkdir(parents=True)
        helper.write_bytes(macho())
        with self.assertRaises(env.EnvironmentError):
            self.collect()

    def test_preference_values_stay_private_and_profiles_are_not_collected(self):
        preferences = self.root / 'Library/Preferences'
        preferences.mkdir(parents=True)
        secret = 'SYNTHETIC_PRIVATE_PREF_CANARY_91273'
        path = preferences / 'com.google.Chrome.plist'
        path.write_bytes(plistlib.dumps({'AppleLanguages': ['ja'], 'fixture_private': secret}))
        profile = self.root / 'Library/Application Support/Google/Chrome/Default'
        profile.mkdir(parents=True)
        history = profile / 'History'
        history.write_bytes(b'fixture history')
        record = self.collect()
        self.assertNotIn(secret, json.dumps(record))
        self.assertNotIn('fixture_private', json.dumps(record))
        self.assertNotIn(str(profile), json.dumps(record))
        history.write_bytes(b'changed history outside input scope')
        self.assertEqual(record, self.collect())
        path.unlink()
        path.symlink_to(history)
        with self.assertRaisesRegex(env.EnvironmentError, 'escapes'):
            self.collect()

    def test_selected_preference_aliases_are_refused_before_hashing(self):
        preferences = self.root / 'Library/Preferences'
        (preferences / 'ByHost').mkdir(parents=True)
        target = preferences / '.GlobalPreferences.plist'
        target.write_bytes(plistlib.dumps({'AppleLanguages': ['ja']}))
        before = self.collect()
        binary_digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        original = Path.open
        def opened(path, *args, **kwargs):
            self.assertNotEqual(path, target, 'unsupported preference alias target opened')
            return original(path, *args, **kwargs)
        original_fd = cache_state.os.open
        def opened_fd(path, flags, *args, **kwargs):
            self.assertNotEqual(Path(path), target, 'unsupported preference alias target opened')
            return original_fd(path, flags, *args, **kwargs)
        for relative in ('com.google.Chrome.plist',
                         'ByHost/com.google.Chrome.fixture-host.plist',
                         'ByHost/com.apple.CoreText.fixture-host.plist'):
            alias = preferences / relative
            alias.symlink_to(os.path.relpath(target, alias.parent))
            with self.subTest(relative=relative), patch.object(Path, 'open', new=opened), \
                    patch.object(cache_state.os, 'open', new=opened_fd), \
                    patch.object(env, '_hashes', {}):
                self.assertEqual(env._file(self.binary), (str(self.binary), binary_digest))
                with self.assertRaisesRegex(env.EnvironmentError, 'preference file symlink'):
                    self.collect()
            alias.unlink()
            self.assertEqual(before, self.collect())

    def test_selected_byhost_alias_cannot_reuse_pinned_certificate(self):
        preferences = self.root / 'Library/Preferences'
        (preferences / 'ByHost').mkdir(parents=True)
        (preferences / 'com.google.Chrome.plist').write_bytes(
            plistlib.dumps({'AppleLanguages': ['en']}))
        (preferences / '.GlobalPreferences.plist').write_bytes(
            plistlib.dumps({'AppleLanguages': ['ja']}))
        alias = preferences / 'ByHost/com.google.Chrome.fixture-host.plist'
        context = SimpleNamespace(w=32, h=16, fps=30)
        with patch('vtx.media.encoders.find_chrome', return_value=str(self.binary)):
            env.certificate(context)
            alias.symlink_to('../.GlobalPreferences.plist')
            with self.assertRaisesRegex(env.EnvironmentError, 'preference file symlink'):
                env.recheck(context)
            alias.unlink()
            env.recheck(context)

    def test_internal_byhost_directory_alias_is_refused_before_traversal(self):
        preferences = self.root / 'Library/Preferences'
        target = preferences / 'host-preferences'
        target.mkdir(parents=True)
        (target / 'com.google.Chrome.fixture-host.plist').write_bytes(b'fixture')
        alias = preferences / 'ByHost'
        alias.symlink_to(target.name, target_is_directory=True)
        original = Path.iterdir
        def entries(path):
            self.assertNotIn(path, (alias, target), 'unsupported ByHost alias traversed')
            return original(path)
        with patch.object(Path, 'iterdir', new=entries):
            with self.assertRaisesRegex(env.EnvironmentError, 'ByHost directory symlink'):
                self.collect()

    def test_internal_framework_link_is_bound_and_escape_refused(self):
        first = self.collect()
        other = self.framework / 'Versions/B'
        other.mkdir()
        (other / 'Chrome').write_bytes(self.library.read_bytes())
        link = self.framework / 'Versions/Current'
        link.unlink()
        link.symlink_to('B', target_is_directory=True)
        self.assertNotEqual(first, self.collect())
        link.unlink()
        (self.root / 'Chrome').write_bytes(macho())
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(env.EnvironmentError, 'escapes'):
            self.collect()

    def test_missing_font_cache_and_broken_bundle_link_fail_closed(self):
        for path in (self.font, self.subcache, self.library):
            with self.subTest(path=path):
                raw = path.read_bytes()
                path.unlink()
                with self.assertRaises(env.EnvironmentError):
                    self.collect()
                path.write_bytes(raw)

    def test_cache_uuid_missing_part_and_header_mismatch_fail_closed(self):
        with self.assertRaises(env.EnvironmentError):
            env._darwin_cache(self.cache, bytes(16))
        data = self.subcache.read_bytes()
        self.subcache.write_bytes(data[:88] + bytes(16) + data[104:])
        with self.assertRaisesRegex(env.EnvironmentError, 'mismatched'):
            self.collect()

    def test_cache_traversal_duplicate_and_oversized_inventory_fail_closed(self):
        original = self.cache.read_bytes()
        for suffix in (b'../outside', b'/absolute', b'.01/../../outside'):
            with self.subTest(suffix=suffix):
                self.cache.write_bytes(original[:-32] + suffix.ljust(32, b'\0'))
                with self.assertRaises(env.EnvironmentError):
                    self.collect()
        duplicate = bytearray(original + original[464:])
        struct.pack_into('<I', duplicate, 396, 2)
        self.cache.write_bytes(duplicate)
        with self.assertRaisesRegex(env.EnvironmentError, 'duplicate'):
            self.collect()
        data = bytearray(original)
        struct.pack_into('<I', data, 396, 100000)
        self.cache.write_bytes(data)
        with self.assertRaises(env.EnvironmentError):
            self.collect()

    @contextlib.contextmanager
    def collecting(self, collect, release):
        results, errors = [], []
        returned = threading.Event()

        def run():
            try:
                results.append(collect())
            except BaseException as error:
                errors.append(error)
            finally:
                returned.set()

        driver = threading.Thread(target=run, daemon=True)
        driver.start()
        try:
            yield results, errors, returned
        finally:
            # Assertions and worker failures must not strand an event waiter.
            release.set()
            driver.join(timeout=5)
            self.assertFalse(driver.is_alive(), 'identity collector did not finish')

    def joined_failure(self, collect, started, release, finished, workers):
        shutdown_entered = threading.Event()
        shutdown_waits = []

        class ObservedExecutor(env.ThreadPoolExecutor):
            def __init__(self, max_workers=None, **kwargs):
                self.observed = max_workers == workers
                super().__init__(max_workers=max_workers, **kwargs)

            def shutdown(self, wait=True, **kwargs):
                if self.observed:
                    shutdown_waits.append(wait)
                    shutdown_entered.set()
                return super().shutdown(wait=wait, **kwargs)

        with patch.object(env, 'ThreadPoolExecutor', ObservedExecutor), \
                self.collecting(collect, release) as (results, errors, returned):
            self.assertTrue(started.wait(timeout=5), 'later worker did not start')
            self.assertTrue(shutdown_entered.wait(timeout=5), 'collector did not enter shutdown')
            # Observe the real executor's join boundary, not a timing window.
            self.assertEqual(shutdown_waits, [True])
            self.assertFalse(finished.is_set())
            self.assertFalse(returned.is_set())
        self.assertTrue(finished.is_set())
        self.assertEqual(results, [], 'failed collection published a partial result')
        self.assertEqual(len(errors), 1)
        return errors[0]

    def test_darwin_cache_hashing_is_bounded_parallel_and_matches_serial_mapping(self):
        paths = []
        for index in range(7):
            path = self.root / f'cache-member-{index}'
            path.write_bytes(bytes([index]) * (1024 * 1024) + f'tail {index}'.encode())
            paths.append(path)
        alias = self.root / 'cache-member-alias'
        alias.symlink_to(paths[2].name)
        inputs = [paths[5], alias, paths[0], paths[2], paths[5], *paths[1:5], paths[6]]
        # The serial oracle must not warm the parallel collection's memo.
        with patch.object(env, '_hashes', {}):
            expected = env._files(inputs)
        self.assertEqual(expected, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
        original_digest, original_read = cache_state.file_digest, cache_state.os.read
        current = threading.local()
        state_lock = threading.Lock()
        readers_ready, release = threading.Event(), threading.Event()
        active = peak = 0
        byte_counts = []

        def digest(path):
            nonlocal active
            current.first, current.count = True, 0
            try:
                return original_digest(path)
            finally:
                with state_lock:
                    if not current.first:
                        active -= 1
                    byte_counts.append((str(path), current.count))
                del current.first

        def read(fd, size):
            nonlocal active, peak
            block = original_read(fd, size)
            if hasattr(current, 'first'):
                current.count += len(block)
                if current.first:
                    current.first = False
                    with state_lock:
                        active += 1
                        peak = max(peak, active)
                        if active == 4:
                            readers_ready.set()
                    self.assertTrue(release.wait(timeout=5), 'cold readers were not released')
            return block

        with patch.object(env, '_hashes', {}), \
                patch.object(env, '_file', wraps=env._file) as files, \
                patch.object(cache_state, 'file_digest', new=digest), \
                patch.object(cache_state.os, 'read', new=read), \
                self.collecting(lambda: env._darwin_cache_files(inputs), release) as (results, errors, returned):
            self.assertTrue(readers_ready.wait(timeout=5), 'four cold byte readers did not overlap')
            self.assertFalse(returned.is_set())
        self.assertEqual(errors, [])
        self.assertEqual(results, [expected])
        self.assertEqual(list(results[0]), sorted(expected))
        self.assertEqual(peak, 4)
        self.assertEqual(sorted(str(call.args[0]) for call in files.call_args_list),
                         sorted(set(map(str, inputs))))
        self.assertEqual({path for path, _ in byte_counts}, set(map(str, paths)))
        self.assertTrue(all(count == Path(path).stat().st_size for path, count in byte_counts))

    def test_darwin_cache_hash_worker_error_joins_and_preserves_only_checked_memo(self):
        paths = []
        for index in range(6):
            path = self.root / f'cache-error-member-{index}'
            path.write_bytes(f'cache bytes {index}'.encode())
            paths.append(path)
        original_digest = cache_state.file_digest
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        failure = PermissionError('denied cache bytes')

        def digest(path):
            if path == paths[0]:
                self.assertTrue(started.wait(timeout=5), 'later cache worker did not start')
                raise failure
            if path == paths[1]:
                started.set()
                try:
                    self.assertTrue(release.wait(timeout=5), 'later cache worker was not released')
                    return original_digest(path)
                finally:
                    finished.set()
            return original_digest(path)

        with patch.object(env, '_hashes', {}):
            with patch.object(cache_state, 'file_digest', new=digest):
                caught = self.joined_failure(lambda: env._darwin_cache_files(paths),
                                             started, release, finished, 4)
            self.assertIs(caught, failure)
            self.assertNotIn(str(paths[0]), env._hashes)
            if os.name != 'nt':
                self.assertEqual(set(env._hashes), set(map(str, paths[1:])))
            with patch.object(cache_state, 'file_digest', wraps=original_digest) as reads:
                recovered = env._darwin_cache_files(paths)
            self.assertEqual(sorted(str(call.args[0]) for call in reads.call_args_list),
                             list(map(str, paths if os.name == 'nt' else paths[:1])))
            self.assertEqual(recovered, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
            changed = paths[1]
            saved = changed.stat()
            changed.write_bytes(b'cache bytes X')
            os.utime(changed, ns=(saved.st_atime_ns, saved.st_mtime_ns))
            with patch.object(cache_state, 'file_digest', wraps=original_digest) as reads:
                rechecked = env._darwin_cache_files(paths)
            expected = dict(recovered)
            expected[str(changed)] = hashlib.sha256(changed.read_bytes()).hexdigest()
            self.assertNotEqual(rechecked, recovered)
            self.assertEqual(rechecked, expected)
            self.assertEqual(sorted(str(call.args[0]) for call in reads.call_args_list),
                             list(map(str, paths if os.name == 'nt' else [changed])))

    def test_darwin_three_identity_axes_overlap_and_preserve_font_preferences(self):
        barrier = threading.Barrier(3)
        original_cache = env._darwin_cache
        original_bundle = env._darwin_bundle
        original_catalog = env._darwin_font_catalog
        preferences = self.root / 'Library/Preferences'
        preferences.mkdir(parents=True)
        preference_file = preferences / 'com.google.Chrome.plist'
        preference_file.write_bytes(b'font preference fixture')

        def cache(*args):
            barrier.wait(timeout=5)
            return original_cache(*args)

        def bundle(*args):
            barrier.wait(timeout=5)
            return original_bundle(*args)

        def catalog(*args):
            barrier.wait(timeout=5)
            return original_catalog(*args)

        with patch.object(env, '_darwin_cache', side_effect=cache), \
                patch.object(env, '_darwin_bundle', side_effect=bundle), \
                patch.object(env, '_darwin_font_catalog', side_effect=catalog):
            record = self.collect()

        fonts = record['fonts']
        self.assertIs(fonts['libraries'], record['libraries'])
        self.assertIs(record['system_cache']['files'], record['libraries'])
        self.assertEqual(fonts['files'], env._files([self.font]))
        self.assertIn(str(preference_file), fonts['configs'])
        self.assertIn('urls', fonts['inventory'])
        self.assertIn('postscript_names', fonts['inventory'])
        self.assertIn('resources', fonts['inventory'])
        self.assertEqual(fonts['tools'], env._files([env.sys.executable,
                                                   env._ROOT / 'render/gfx_environment.py']))

    def test_darwin_font_catalog_failure_waits_for_cache_and_renderer(self):
        barrier = threading.Barrier(3)
        cache_finished = threading.Event()
        renderer_finished = threading.Event()
        font_error = PermissionError('CoreText font bytes denied')
        original_cache = env._darwin_cache
        original_renderer = env._collect_darwin_renderer

        def cache(*args):
            barrier.wait(timeout=5)
            result = original_cache(*args)
            cache_finished.set()
            return result

        def renderer(*args):
            barrier.wait(timeout=5)
            result = original_renderer(*args)
            renderer_finished.set()
            return result

        def catalog(*args):
            barrier.wait(timeout=5)
            raise font_error

        with patch.object(env, '_darwin_cache', side_effect=cache), \
                patch.object(env, '_collect_darwin_renderer', side_effect=renderer), \
                patch.object(env, '_darwin_font_catalog', side_effect=catalog):
            with self.assertRaises(env.EnvironmentError) as caught:
                self.collect()

        self.assertIs(caught.exception.__cause__, font_error)
        self.assertTrue(cache_finished.is_set())
        self.assertTrue(renderer_finished.is_set())

    def test_darwin_early_axis_failure_joins_later_font_and_reports_first_error(self):
        original_cache, original_catalog = env._darwin_cache, env._darwin_font_catalog
        for failing_axis in ('cache', 'renderer'):
            with self.subTest(failing_axis=failing_axis):
                started, release, finished = threading.Event(), threading.Event(), threading.Event()
                cache_error = PermissionError('cache bytes denied')
                renderer_error = RuntimeError('renderer inventory failed')
                renderer_finished = threading.Event()

                def cache(*args):
                    if failing_axis == 'cache':
                        self.assertTrue(started.wait(timeout=5), 'font worker did not start')
                        raise cache_error
                    return original_cache(*args)

                def renderer(*args):
                    self.assertTrue(started.wait(timeout=5), 'font worker did not start')
                    renderer_finished.set()
                    raise renderer_error

                def catalog():
                    started.set()
                    try:
                        self.assertTrue(release.wait(timeout=5), 'font worker was not released')
                        return original_catalog()
                    finally:
                        finished.set()

                with patch.object(env, '_darwin_cache', side_effect=cache), \
                        patch.object(env, '_collect_darwin_renderer', side_effect=renderer), \
                        patch.object(env, '_darwin_font_catalog', side_effect=catalog), \
                        patch.object(env, '_darwin_fonts', wraps=env._darwin_fonts) as fonts:
                    caught = self.joined_failure(self.collect, started, release, finished, 3)
                self.assertIsInstance(caught, env.EnvironmentError)
                self.assertIs(caught.__cause__, cache_error if failing_axis == 'cache' else renderer_error)
                self.assertTrue(renderer_finished.is_set())
                fonts.assert_not_called()

    def test_darwin_svg_runtime_uses_real_shared_cache_hash_helper(self):
        from vtx.render import gfx_svg
        original_file = env._file

        def file(path):
            return original_file(self.system_file if str(path) == '/usr/lib/dyld' else path)

        with patch.object(env, '_hashes', {}), patch.object(env, '_file', side_effect=file), \
                patch.object(env, '_darwin_cache_files', wraps=env._darwin_cache_files) as cache_files:
            record = gfx_svg._darwin_runtime(self.binary)
        cache_files.assert_called_once_with([self.cache, self.subcache])
        self.assertEqual(record['system_cache']['files'],
                         {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (self.cache, self.subcache)})

    def test_invalid_boot_empty_font_inventory_and_unknown_app_fail_closed(self):
        self.boot = 'unknown'
        with self.assertRaisesRegex(env.EnvironmentError, 'host/boot'):
            self.collect()
        self.boot = '01234567-89ab-cdef-0123-456789abcdef'
        with patch.object(env, '_darwin_font_inventory', return_value=([], [])):
            with self.assertRaises(env.EnvironmentError):
                self.collect()
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'other'}))
        with self.assertRaisesRegex(env.EnvironmentError, 'Google Chrome'):
            self.collect()

    def test_unknown_architecture_backend_and_loader_override_fail_closed(self):
        for backend in ('cef', 'cli', 'electron'):
            with self.subTest(backend=backend), self.assertRaises(env.EnvironmentError):
                env.collect(backend, self.binary)
        with patch.object(env.platform, 'machine', return_value='x86_64'):
            with self.assertRaises(env.EnvironmentError):
                self.collect()
        for key in ('DYLD_LIBRARY_PATH', 'DYLD_INSERT_LIBRARIES', '__XPC_DYLD_LIBRARY_PATH', 'VK_DRIVER_FILES'):
            with self.subTest(key=key), patch.dict(os.environ, {key: '/unknown'}):
                with self.assertRaisesRegex(env.EnvironmentError, 'override'):
                    self.collect()

    def test_external_or_unknown_system_library_fail_closed(self):
        external = self.root / 'external.dylib'
        external.write_bytes(macho())
        for name in (str(external), '/usr/lib/unknown.dylib', '@rpath/missing.dylib'):
            self.library.write_bytes(macho([name]))
            with self.subTest(name=name), self.assertRaises(env.EnvironmentError):
                self.collect()

    def test_internal_loader_dependency_is_in_bundle_closure(self):
        helper = self.library.parent / 'helper.dylib'
        helper.write_bytes(macho())
        self.library.write_bytes(macho(['@loader_path/helper.dylib']))
        self.assertEqual(self.collect()['dependencies'][str(self.library)],
                         {'@loader_path/helper.dylib': [str(helper)]})

    def test_inactive_intel_resource_is_bound_but_cannot_be_native_dependency(self):
        intel = self.library.parent / 'intel.dylib'
        intel.write_bytes(struct.pack('<8I', 0xfeedfacf, 0x1000007, 0, 6, 0, 0, 0, 0))
        first = self.collect()
        self.assertNotIn(str(intel), first['dependencies'])
        intel.write_bytes(intel.read_bytes() + b'changed resource')
        self.assertNotEqual(env.digest(first), env.digest(self.collect()))
        good = intel.read_bytes()
        intel.write_bytes(good[:8])
        with self.assertRaisesRegex(env.EnvironmentError, 'unsupported Darwin binary slice'):
            self.collect()
        intel.write_bytes(good)
        self.library.write_bytes(macho(['@loader_path/intel.dylib']))
        with self.assertRaisesRegex(env.EnvironmentError, 'native arm64'):
            self.collect()

    def test_standalone_helper_uses_its_own_executable_path(self):
        helper = self.library.parent / 'Helpers/web_app_shortcut_copier'
        helper.parent.mkdir()
        helper.write_bytes(macho(['@executable_path/../Chrome']))
        record = self.collect()
        self.assertEqual(record['dependencies'][str(helper)],
                         {'@executable_path/../Chrome': [str(self.library)]})
        helper.write_bytes(macho(['@executable_path/../../../../../../external']))
        with self.assertRaisesRegex(env.EnvironmentError, 'unresolved/external'):
            self.collect()

    def test_internal_rpath_dependency_is_resolved(self):
        helper = self.library.parent / 'helper.dylib'
        helper.write_bytes(macho())
        self.library.write_bytes(macho(['@rpath/helper.dylib'], ['@loader_path']))
        self.assertIn(str(helper), self.collect()['dependencies'][str(self.library)]['@rpath/helper.dylib'])

    def test_unused_external_search_path_and_embedded_loader_environment_are_refused(self):
        self.library.write_bytes(macho([], [str(self.root)]))
        with self.assertRaisesRegex(env.EnvironmentError, 'external'):
            self.collect()
        data = bytearray(macho(['DYLD_INSERT_LIBRARIES=/external']))
        struct.pack_into('<I', data, 32, 0x27)
        self.library.write_bytes(data)
        with self.assertRaisesRegex(env.EnvironmentError, 'embedded'):
            self.collect()

    def test_malformed_and_non_native_macho_fail_closed(self):
        for data in (b'\x7fELFbad', b'\xcf\xfa\xed\xfe',
                     struct.pack('<8I', 0xfeedfacf, 0x1000007, 0, 2, 0, 0, 0, 0),
                     struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, 1, 10000, 0, 0)):
            self.binary.write_bytes(data)
            with self.subTest(data=data), self.assertRaises(env.EnvironmentError):
                self.collect()

    def test_universal_native_slice_is_read_and_bound(self):
        arm = macho(['/usr/lib/libSystem.B.dylib'])
        self.binary.write_bytes(struct.pack('>7I', 0xcafebabe, 1, 0x100000c, 0, 32, len(arm), 0)
                                + bytes(4) + arm)
        self.assertEqual(self.collect()['architecture'], [0x100000c])

    def test_unknown_is_diagnostic_but_cannot_be_certified(self):
        self.font.unlink()
        context = SimpleNamespace(w=32, h=16, fps=30)
        with patch('vtx.media.encoders.find_chrome', return_value=str(self.binary)):
            self.assertEqual(env.resolve(context)['status'], 'unknown')
            with self.assertRaises(env.EnvironmentError):
                env.certificate(context)

    def test_new_loader_override_cannot_reuse_pinned_certificate(self):
        context = SimpleNamespace(w=32, h=16, fps=30)
        with patch('vtx.media.encoders.find_chrome', return_value=str(self.binary)):
            env.certificate(context)
            with patch.dict(os.environ, {'DYLD_INSERT_LIBRARIES': '/unbound/library'}):
                with self.assertRaisesRegex(env.EnvironmentError, 'override'):
                    env.certificate(context)

    def test_recheck_refuses_input_change_and_offline_certificate_requires_cache(self):
        context = SimpleNamespace(w=32, h=16, fps=30)
        with patch('vtx.media.encoders.find_chrome', return_value=str(self.binary)):
            certificate = env.certificate(context)
            env.recheck(context)
            self.resource.write_bytes(b'resource B')
            with self.assertRaisesRegex(env.EnvironmentError, 'changed'):
                env.recheck(context)
        for field in ('system_cache', 'architecture', 'host_boot', 'dependencies'):
            bad = copy.deepcopy(certificate)
            bad['axes'][field] = {}
            bad['sha256'] = env.digest(bad['axes'])
            with self.subTest(field=field), self.assertRaises(env.EnvironmentError):
                env.verify_certificate(bad)

    def test_unreadable_cache_does_not_fall_back_to_uuid_only(self):
        original_file = env._file
        cache_paths = {str(self.cache.resolve()), str(self.subcache.resolve())}
        def unreadable_cache(path):
            if str(Path(path).resolve()) in cache_paths:
                raise PermissionError('denied cache bytes')
            return original_file(path)
        with patch.object(env, '_file', side_effect=unreadable_cache):
            with self.assertRaisesRegex(env.EnvironmentError, 'denied cache bytes'):
                self.collect()


class CoreTextFFIContracts(unittest.TestCase):
    def test_dyld_uses_loaded_uuid_path_and_refuses_override(self):
        overridden = [False]
        def cache_uuid(value):
            for i in range(16):
                value[i] = i + 1
            return True
        lib = SimpleNamespace(dyld_shared_cache_file_path=lambda: b'/actual/dyld-cache',
                              _dyld_get_shared_cache_uuid=cache_uuid,
                              dyld_shared_cache_some_image_overridden=lambda: overridden[0],
                              dyld_has_inserted_or_interposing_libraries=lambda: False)
        with patch.object(env.ctypes, 'CDLL', return_value=lib):
            self.assertEqual(env._darwin_dyld(), (Path('/actual/dyld-cache'), bytes(range(1, 17))))
            overridden[0] = True
            with self.assertRaisesRegex(env.EnvironmentError, 'override'):
                env._darwin_dyld()

    def test_urls_and_faces_are_read_and_owned_arrays_released(self):
        released = []
        functions = {
            'CFArrayGetCount': lambda array: 1,
            'CFArrayGetValueAtIndex': lambda array, i: array,
            'CFRelease': lambda array: released.append(array),
        }
        def url(value, resolve, buf, length):
            buf.value = b'/actual/font.otf'
            return True
        def string(value, buf, length, encoding):
            self.assertEqual(encoding, 0x08000100)
            buf.value = b'Actual-Face'
            return True
        functions.update(CFURLGetFileSystemRepresentation=url, CFStringGetCString=string)
        cf = SimpleNamespace(**functions)
        ct = SimpleNamespace(CTFontManagerCopyAvailableFontURLs=lambda: 1,
                             CTFontManagerCopyAvailablePostScriptNames=lambda: 2)
        with patch.object(env.ctypes, 'CDLL', side_effect=[cf, ct]):
            self.assertEqual(env._darwin_font_inventory(), (['/actual/font.otf'], ['Actual-Face']))
        self.assertEqual(released, [1, 2])
        released.clear()
        cf.CFArrayGetCount = lambda array: 0
        with patch.object(env.ctypes, 'CDLL', side_effect=[cf, ct]):
            with self.assertRaisesRegex(env.EnvironmentError, 'empty'):
                env._darwin_font_inventory()
        self.assertEqual(released, [1])
        cf.CFArrayGetCount = lambda array: 1
        cf.CFURLGetFileSystemRepresentation = lambda *args: False
        with patch.object(env.ctypes, 'CDLL', side_effect=[cf, ct]):
            with self.assertRaisesRegex(env.EnvironmentError, 'unreadable'):
                env._darwin_font_inventory()
        self.assertEqual(released, [1, 1])


if __name__ == '__main__':
    unittest.main()
