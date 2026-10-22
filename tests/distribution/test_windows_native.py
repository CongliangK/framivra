"""Native Windows safety: actual kernel handles, process trees and cleanup races."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vtx.runtime import fstxn


@unittest.skipUnless(os.name == 'nt', 'Windows kernel APIs required')
class WindowsCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / '中文 # cache'
        self.target.mkdir()
        (self.target / 'data').write_text('owned')

    def remove(self, expected=None, preserve=lambda p: False):
        fstxn.remove_pinned(self.target, expected or fstxn.cleanup_identity(self.target), preserve=preserve)

    def test_tree_deletion_and_lock_preservation(self):
        lock = self.target / 'data.lock'
        lock.write_text('keep')
        self.remove(preserve=lambda p: p.suffix == '.lock')
        self.assertEqual(list(self.target.iterdir()), [lock])
        lock.unlink()
        self.remove()
        self.assertFalse(self.target.exists())

    def test_replaced_entry_refused(self):
        expected = fstxn.cleanup_identity(self.target)
        self.target.rename(self.root / 'old')
        self.target.mkdir()
        victim = self.target / 'new'
        victim.write_text('protected')
        with self.assertRaises(ValueError):
            self.remove(expected)
        self.assertEqual(victim.read_text(), 'protected')

    def test_open_ancestors_cannot_be_renamed(self):
        from vtx.runtime.windows_cleanup import pinned
        with pinned(self.target, directory=True):
            with self.assertRaises(OSError):
                self.target.rename(self.root / 'moved')
        self.assertTrue((self.target / 'data').is_file())

    def test_pinned_directory_rejects_reparse_write_handle(self):
        from vtx.runtime.windows_cleanup import pinned
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with pinned(self.target, directory=True):
            handle = kernel.CreateFileW(str(self.target), 0x40000000, 7, None, 3,
                                         0x02000000 | 0x00200000, None)
            if handle != wintypes.HANDLE(-1).value:
                kernel.CloseHandle(handle)
                self.fail('write handle could mutate a pinned directory reparse point')
            self.assertEqual(ctypes.get_last_error(), 32)  # sharing violation

    def test_concurrent_new_file_keeps_directory(self):
        original = sorted
        def late_child(iterator):
            children = original(iterator)
            (self.target / 'new.lock').write_text('keep new child')
            return children
        with patch('vtx.runtime.windows_cleanup.sorted', side_effect=late_child, create=True):
            self.remove()
        self.assertTrue((self.target / 'new.lock').is_file())

    def test_marker_guard_retains_pinned_root_and_ancestor(self):
        for leaf in (self.target, self.target / 'data'):
            with self.subTest(leaf=leaf):
                expected = fstxn.cleanup_identity(leaf)
                marker = self.target / '.worker_pgid.json'
                marker.write_text('unsettled')
                fstxn.remove_pinned(leaf, expected, preserve=lambda p: False,
                                    retain_if_present=(self.target, marker.name))
                self.assertEqual((self.target / 'data').read_text(), 'owned')
                self.assertEqual(marker.read_text(), 'unsettled')
                marker.unlink()

    def test_junction_ancestor_refused(self):
        outside = self.root / 'outside'; outside.mkdir()
        (outside / 'data').write_text('protected')
        link = self.root / 'link'
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        from vtx.runtime.windows_cleanup import pinned
        with self.assertRaises(ValueError):
            with pinned(link, directory=True):
                self.fail('junction accepted')
        self.assertEqual((outside / 'data').read_text(), 'protected')

    def test_gc_internal_junction_remains_unlink_only(self):
        from types import SimpleNamespace
        from vtx.cache import hygiene
        from vtx.runtime.proc_supervisor import WORKER_LEDGER
        cache = self.root / 'cache'
        run = cache / 'tmp' / 'worker'
        run.mkdir(parents=True)
        (run / 'data').write_text('owned')
        (run / WORKER_LEDGER).write_text('unsettled')
        os.utime(run, (1, 1))
        link = run.with_name('alias')
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(run)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        c = SimpleNamespace(root=self.root, cache=cache, out=self.root / 'out')
        with patch.object(hygiene, '_current_keys', return_value=(set(), set(), set())), \
                patch.object(hygiene._plugin_hooks, 'get_cache_dirs', return_value=set()), \
                patch.object(hygiene._plugin_hooks, 'get_cache_dir_keys', return_value={}):
            plan = hygiene.gc_plan(c)
        plan.execute()
        self.assertFalse(os.path.lexists(link))
        self.assertEqual((run / 'data').read_text(), 'owned')
        self.assertTrue((run / WORKER_LEDGER).is_file())


@unittest.skipUnless(os.name == 'nt', 'Windows Job Objects required')
class WindowsBrowserJob(unittest.TestCase):
    def wait_pids(self, pids, action):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handles = [kernel.OpenProcess(0x100000, False, p) for p in pids]
        self.assertTrue(all(handles), pids)
        try:
            action()
            for handle in handles:
                self.assertEqual(kernel.WaitForSingleObject(handle, 10000), 0)
        finally:
            for handle in handles:
                if handle: kernel.CloseHandle(handle)

    def tree(self, root):
        code = "import subprocess,sys,time,json,os; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); open(sys.argv[1],'w').write(json.dumps([os.getpid(),p.pid])); time.sleep(120)"
        return [sys.executable, '-c', code, str(root / 'pids.json')]

    def pids(self, root):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            try:
                return json.loads((root / 'pids.json').read_text())
            except (FileNotFoundError, ValueError):
                time.sleep(.05)
        self.fail('tree did not start')

    def test_close_kills_entire_tree(self):
        from vtx.runtime.windows_browser import BrowserJob
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            job = BrowserJob(self.tree(root))
            self.addCleanup(job.close)
            self.wait_pids(self.pids(root), job.close)

    def test_host_termination_kills_entire_tree(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            code = 'from vtx.runtime.windows_browser import BrowserJob; import sys,json,time; job=BrowserJob(json.loads(sys.argv[1])); time.sleep(120)'
            host = subprocess.Popen([sys.executable, '-c', code, json.dumps(self.tree(root))])
            self.addCleanup(lambda: host.kill() if host.poll() is None else None)
            def kill():
                host.kill(); host.wait(timeout=5)
            self.wait_pids(self.pids(root), kill)

@unittest.skipUnless(os.name == 'nt', 'Windows renderer resolver required')
class WindowsEnvironment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        from vtx.render import gfx_environment as env
        chrome = Path(os.environ.get('VTX_TEST_CHROME', r'C:\Program Files\Google\Chrome\Application\chrome.exe'))
        if not chrome.is_file():
            raise AssertionError('native Windows resolver tests require Chrome')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.application = Path(cls.tmp.name) / 'Application'; cls.application.mkdir()
        cls.binary = cls.application / 'chrome.exe'
        shutil.copyfile(chrome, cls.binary)
        version = env._windows_version(cls.binary)
        runtime = cls.application / version; runtime.mkdir()
        cls.library = runtime / 'chrome.dll'; cls.library.write_bytes(b'fixture runtime A')
        cls.base = env.collect('cdp', cls.binary)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_actual_axes_and_offline_certificate(self):
        from vtx.render import gfx_environment as env
        axes = dict(self.base, viewport={'width': 640, 'height': 360}, fps=30)
        cert = {'schema': env.SCHEMA, 'status': 'input-bound', 'axes': axes, 'sha256': env.digest(axes)}
        self.assertEqual(env.verify_certificate(cert), cert['sha256'])
        for name in ('registry', 'graphics_libraries', 'host_boot'):
            broken = dict(axes); broken.pop(name)
            with self.assertRaises(env.EnvironmentError):
                env.verify_certificate(dict(cert, axes=broken, sha256=env.digest(broken)))
        self.assertTrue(axes['fonts']['files'])
        self.assertTrue(axes['fonts']['configs'])

    def test_runtime_registry_and_boot_changes_invalidate(self):
        from vtx.render import gfx_environment as env
        self.library.write_bytes(b'fixture runtime B')
        changed = env.collect('cdp', self.binary)
        self.assertNotEqual(env.digest(self.base), env.digest(changed))
        with patch.object(env, '_windows_boot', return_value='123'):
            changed_boot = env.collect('cdp', self.binary)
        self.assertNotEqual(env.digest(changed), env.digest(changed_boot))
        original = env._windows_registry
        with patch.object(env, '_windows_registry', side_effect=lambda root, name:
                          dict(original(root, name), injected='changed input')):
            changed_registry = env.collect('cdp', self.binary)
        self.assertNotEqual(env.digest(changed), env.digest(changed_registry))

    def test_unreadable_inputs_and_unsupported_backend_fail_closed(self):
        from vtx.render import gfx_environment as env
        with patch.object(env, '_files', side_effect=PermissionError('fixture unreadable')):
            with self.assertRaises(env.EnvironmentError):
                env.collect('cdp', self.binary)
        with self.assertRaises(env.EnvironmentError):
            env.collect('cef', self.binary)

    def test_telemetry_exclusion_keeps_fonts_resources_registry_and_boot_bound(self):
        from types import SimpleNamespace
        from vtx.render import gfx_environment as env
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, LOCALAPPDATA=d):
            fonts = Path(d) / 'Microsoft/Windows/Fonts'; fonts.mkdir(parents=True)
            font = fonts / 'fixture.ttf'; font.write_bytes(b'font A')
            setup = self.application / 'SetupMetrics'; setup.mkdir(exist_ok=True)
            pma = setup / '123_456.pma'; pma.write_bytes(b'metrics A')
            before = env.collect('cdp', self.binary)
            c = SimpleNamespace(_gfx_environment=(None, before, self.binary, None))
            with patch.object(env, 'resolve', return_value=before):
                pma.unlink(); setup.rmdir()
                env.recheck(c)
                for resource in (font, self.library):
                    saved, content = resource.stat(), resource.read_bytes()
                    resource.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
                    os.utime(resource, ns=(saved.st_atime_ns, saved.st_mtime_ns))
                    with self.subTest(path=resource.name), self.assertRaises(env.EnvironmentError):
                        env.recheck(c)
                    resource.write_bytes(content)
                original = env._windows_registry
                with patch.object(env, '_windows_registry', side_effect=lambda root, name:
                        dict(original(root, name), fixture='changed')):
                    with self.assertRaises(env.EnvironmentError):
                        env.recheck(c)
                with patch.object(env, '_windows_boot', return_value='123'):
                    with self.assertRaises(env.EnvironmentError):
                        env.recheck(c)

    def test_same_metadata_token_never_reuses_windows_content_hash(self):
        from types import SimpleNamespace
        from vtx.render import gfx_environment as env
        content = self.library.read_bytes()
        self.addCleanup(self.library.write_bytes, content)
        with patch.object(env, '_file_token', return_value=('identical metadata token',)):
            before = env.collect('cdp', self.binary)
            first_hash = env._file(self.library)[1]
            self.library.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
            self.assertNotEqual(first_hash, env._file(self.library)[1])
            c = SimpleNamespace(_gfx_environment=(None, before, self.binary, None))
            with patch.object(env, 'resolve', return_value=before):
                with self.assertRaisesRegex(env.EnvironmentError, 'runtime'):
                    env.recheck(c)

@unittest.skipUnless(os.name == 'nt', 'Windows staging and CLI failure contract')
class WindowsCliFailures(unittest.TestCase):
    def test_nonzero_and_success_without_valid_png_are_failures(self):
        from vtx.render.gfx_cli import run_cli_shot, GfxCliError
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); html = root / '中文 # page.html'; html.write_text('<html/>')
            for mode in ('nonzero', 'missing', 'invalid'):
                def shot(cmd, **kwargs):
                    if mode == 'invalid':
                        output = next(s.split('=', 1)[1] for s in cmd if s.startswith('--screenshot='))
                        Path(output).write_bytes(b'invalid png')
                    return subprocess.CompletedProcess(cmd, 7 if mode == 'nonzero' else 0, b'', b'fixture')
                with self.subTest(mode=mode), patch('vtx.runtime.windows_browser.run_browser', side_effect=shot):
                    with self.assertRaises(GfxCliError):
                        run_cli_shot(0, root, html, 'chrome.exe', width=640, height=360)
                    self.assertFalse((root / 'f0000.png').exists())


@unittest.skipUnless(os.name == 'nt', 'Windows legacy console encoding')
class WindowsCliEncoding(unittest.TestCase):
    def test_all_entry_points_override_legacy_redirected_streams(self):
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); timeline = root / 'timeline.json'
            timeline.write_text(json.dumps({'version': 2, 'project': {'fps': 30,
                'width': 640, 'height': 360, 'duration_frames': 30},
                'sources': [], 'video_tracks': [], 'subtitles': [], 'audio_tracks': []}), encoding='utf-8')
            entries = [[str(repo / 'vtx/vtx.py')], ['-m', 'vtx'],
                       ['-c', 'from vtx.cli.commands import main; main()']]
            for entry in entries:
                result = subprocess.run([sys.executable, *entry, '-t', str(timeline), 'validate'],
                    cwd=repo, env=dict(os.environ, PYTHONUTF8='0', PYTHONIOENCODING='gbk'),
                    capture_output=True, timeout=30)
                with self.subTest(entry=entry):
                    self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
                    self.assertIn('✓ 校验通过', result.stdout.decode('utf-8'))
            self.assertTrue((root / 'out/receipt.json').is_file())
