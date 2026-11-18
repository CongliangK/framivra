"""Native Windows safety: actual kernel handles, process trees and cleanup races."""
from collections import Counter
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import errno
import io
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
        timeline = self.root / 'timeline.json'
        timeline.write_text(json.dumps({'version': 2, 'project': {'fps': 30,
            'width': 640, 'height': 360, 'duration_frames': 30},
            'sources': [], 'video_tracks': [], 'subtitles': [], 'audio_tracks': []}), encoding='utf-8')
        c = SimpleNamespace(root=self.root, cache=cache, out=self.root / 'out', tl_path=timeline)
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
    # Use the actual interpreter rather than the venv redirector for exact root PID assertions.
    python = getattr(sys, '_base_executable', sys.executable)

    def isolated(self, fixture, *, cwd=None):
        """CONTRIBUTING:28: only a separate, bounded, own process group exercises termination."""
        repo = Path(__file__).resolve().parents[2]
        code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                'from tests.distribution.test_windows_native import WindowsBrowserJob; '
                'getattr(WindowsBrowserJob(), sys.argv[2])()')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(repo), fixture], cwd=cwd or repo,
                                start_new_session=True, creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                                                                      | subprocess.CREATE_NO_WINDOW),
                                capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))

    @contextmanager
    def own_member_handles(self, pids, job):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with ExitStack() as cleanup:
            handles = []
            for pid in pids:  # from an owned fixture, observation rights only
                handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
                self.assertTrue(handle, pid)
                cleanup.callback(kernel.CloseHandle, handle)
                member = wintypes.BOOL()
                self.assertTrue(kernel.IsProcessInJob(handle, job._job, ctypes.byref(member)))
                self.assertTrue(member.value, pid)
                self.assertEqual(kernel.WaitForSingleObject(handle, 0), 258)
                handles.append(handle)
            yield kernel.WaitForSingleObject, handles

    def tree(self, root, *, hold_pipes=False):
        leaf = ('from pathlib import Path; import sys,time; '
                'Path(sys.argv[1]).write_text("ready"); time.sleep(25)')
        code = '''import json, os, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
pipes = sys.argv[3] == 'True'
settings = dict(stdout=sys.stdout, stderr=sys.stderr) if pipes else {}
leaf = subprocess.Popen([sys.executable, '-I', '-B', '-c', sys.argv[2], str(root / 'leaf')], **settings)
(root / 'pids.json').write_text(json.dumps([os.getpid(), leaf.pid]))
for _ in range(200):
    if (root / 'leaf').exists():
        break
    time.sleep(.02)
else:
    raise RuntimeError('leaf did not start')
if pipes:
    sys.stdout.buffer.write(b'root stdout')
    sys.stderr.buffer.write(b'root stderr')
    sys.exit(19)
time.sleep(25)
'''
        return [self.python, '-I', '-B', '-c', code, str(root), leaf, str(hold_pipes)]

    def pids(self, root):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            try:
                if (root / 'leaf').exists():
                    return json.loads((root / 'pids.json').read_text())
            except (FileNotFoundError, ValueError):
                pass
            time.sleep(.02)
        self.fail('tree did not start')

    def test_close_kills_entire_tree(self):
        self.isolated('_close_fixture')

    def _close_fixture(self):
        from vtx.runtime.windows_browser import BrowserJob
        with tempfile.TemporaryDirectory() as d, ExitStack() as cleanup:
            root = Path(d)
            job = BrowserJob(self.tree(root))
            cleanup.callback(job.close)
            pids = self.pids(root)
            self.assertEqual(job.proc.pid, pids[0])
            self.assertIsNone(job.proc.poll())
            with self.own_member_handles(pids, job) as (wait, handles):
                job.close()
                job.close()
                self.assertIsNotNone(job.proc.poll())
                self.assertEqual(job.proc.wait(0), job.proc.returncode)
                self.assertIsNone(job._info.process)
                self.assertIsNone(job._info.thread)
                self.assertEqual([wait(h, 5000) for h in handles], [0, 0])

    def test_host_termination_kills_entire_tree(self):
        self.isolated('_host_fixture')

    def _host_fixture(self):
        from vtx.runtime.windows_browser import BrowserJob
        with tempfile.TemporaryDirectory() as d, ExitStack() as cleanup:
            root = Path(d)
            code = ('from vtx.runtime.windows_browser import BrowserJob; import sys,json,time; '
                    'job=BrowserJob(json.loads(sys.argv[1])); time.sleep(25)')
            host = BrowserJob([self.python, '-B', '-c', code, json.dumps(self.tree(root))])
            cleanup.callback(host.close)
            pids = self.pids(root)
            self.assertNotIn(host.proc.pid, pids)
            # Terminate only the exact handle returned by launch for this dedicated outer-job fixture.
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            with self.own_member_handles(pids, host) as (wait, members):
                self.assertTrue(kernel.TerminateProcess(host._info.process, 77))
                self.assertEqual(host.proc.wait(5), 77)
                self.assertEqual([wait(h, 5000) for h in members], [0, 0])

    def test_cli_drains_independent_large_pipes(self):
        self.isolated('_large_pipes_fixture')

    def _large_pipes_fixture(self):
        from vtx.render.gfx_cli import _run_cli_browser
        code = '''import sys, threading
from pathlib import Path
from os import environ
assert Path.cwd().samefile(sys.argv[1])
assert environ['VTX_NATIVE_JOB_TEST'] == '中文 env'
assert sys.argv[2:] == ['', '空 格 # 😺', 'with "quote"', 'slash ' + chr(92)]
writers = [threading.Thread(target=stream.buffer.write, args=(data * 1048576,))
           for stream, data in ((sys.stdout, b'out!'), (sys.stderr, b'err!'))]
for writer in writers:
    writer.start()
for writer in writers:
    writer.join()
sys.exit(7)
'''
        with patch.dict(os.environ, {'VTX_NATIVE_JOB_TEST': '中文 env'}):
            with tempfile.TemporaryDirectory() as d:
                args = [Path(self.python), '-I', '-B', '-c', code, str(Path.cwd()), '',
                        '空 格 # 😺', 'with "quote"', 'slash ' + chr(92)]
                result = _run_cli_browser(args, d, 15)
        self.assertEqual(result.returncode, 7, result.stderr[:500])
        self.assertEqual(result.stdout, b'out!' * 1048576)
        self.assertEqual(result.stderr, b'err!' * 1048576)

    def test_cli_timeout_stops_owned_descendant_holding_pipes(self):
        self.isolated('_pipe_timeout_fixture')

    def _pipe_timeout_fixture(self):
        from vtx.render.gfx_cli import _run_cli_browser
        from vtx.runtime.windows_browser import BrowserJob
        with tempfile.TemporaryDirectory() as d, ExitStack() as cleanup:
            root = Path(d)
            observed = []
            stop = BrowserJob._stop_job

            def observe_then_stop(job):
                if job._job.value and not observed:
                    self.assertEqual(job.proc.poll(), 19)  # root already exited, leaf still holds both writes
                    pids = self.pids(root)
                    self.assertEqual(job.proc.pid, pids[0])
                    observed.append(cleanup.enter_context(self.own_member_handles(pids[1:], job)))
                stop(job)

            start = time.monotonic()
            with patch.object(BrowserJob, '_stop_job', observe_then_stop):
                with self.assertRaises(subprocess.TimeoutExpired) as raised:
                    _run_cli_browser(self.tree(root, hold_pipes=True), d, 3)
            self.assertLess(time.monotonic() - start, 8)
            self.assertEqual(raised.exception.stdout, b'root stdout')
            self.assertEqual(raised.exception.stderr, b'root stderr')
            (wait, handles), = observed
            self.assertEqual(wait(handles[0], 5000), 0)

    def test_combined_capture_limit_is_hard_failure(self):
        self.isolated('_capture_limit_fixture')

    def _capture_limit_fixture(self):
        from vtx.runtime import windows_browser as wb
        with tempfile.TemporaryDirectory() as d, ExitStack() as cleanup:
            root = Path(d)
            stop = wb.BrowserJob._stop_job
            observed = []

            def observe_then_stop(job):
                if job._job.value and not observed:
                    pids = self.pids(root)
                    self.assertEqual(job.proc.pid, pids[0])
                    observed.append(cleanup.enter_context(self.own_member_handles(pids[1:], job)))
                stop(job)

            # The root writes exactly 11 bytes to each pipe. Neither stream can exceed
            # 21 alone, but together a single extra byte must fail and stop the live leaf.
            with patch.object(wb, '_CAPTURE_LIMIT', 22):
                exact = wb.run_browser([self.python, '-I', '-B', '-c',
                                        "import os; os.write(1,b'root stdout'); os.write(2,b'root stderr')"], 15)
                self.assertEqual((exact.returncode, exact.stdout, exact.stderr),
                                 (0, b'root stdout', b'root stderr'))
            with patch.object(wb, '_CAPTURE_LIMIT', 21), patch.object(wb.BrowserJob, '_stop_job', observe_then_stop):
                with self.assertRaises(OSError) as raised:
                    wb.run_browser(self.tree(root, hold_pipes=True), 15)
            self.assertEqual(raised.exception.errno, errno.EFBIG)
            (wait, handles), = observed
            self.assertEqual(wait(handles[0], 5000), 0)

    def test_discovery_binds_windows_native_exe_and_rejects_wrapper_override(self):
        import vt
        from vtx.media import encoders
        from vtx.render import browser_paths, gfx_render
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wrapper_dir = root / 'wrapper'
            native_dir = root / 'native'
            wrapper_dir.mkdir()
            native_dir.mkdir()
            wrapper = wrapper_dir / 'native-fixture.cmd'
            executable = native_dir / 'native-fixture.exe'
            installed = root / 'installed.exe'
            override = root / 'override.exe'
            for path in (wrapper, executable, installed, override):
                path.touch()  # discovery test only, never executed

            def assert_selected(path):
                self.assertEqual(encoders.find_chrome(), str(path))
                self.assertEqual(vt.find_chrome(), str(path))
                browser = gfx_render.Browser()
                browser._find_chrome()
                self.assertEqual(browser.chrome, str(path))

            with patch.object(browser_paths, 'chrome_install_paths', return_value=(str(installed),)), \
                    patch.dict(os.environ, {'PATH': os.pathsep.join((str(wrapper_dir), str(native_dir)))}):
                with patch.object(browser_paths, 'chrome_names', return_value=(str(override), 'native-fixture')):
                    assert_selected(override)
                with patch.object(browser_paths, 'chrome_names', return_value=(None, 'native-fixture')):
                    assert_selected(executable)
                    with patch.dict(os.environ), patch.object(os, 'defpath', str(native_dir)):
                        os.environ.pop('PATH', None)
                        assert_selected(installed)  # unset PATH never inherits a default search path
                    executable.unlink()
                    assert_selected(installed)
                with patch.object(browser_paths, 'chrome_names', return_value=(str(wrapper), 'native-fixture')):
                    self.assertIsNone(browser_paths.find_chrome_candidate('win32'))
                    self.assertIsNone(vt.find_chrome())
                    with self.assertRaisesRegex(RuntimeError, '^no chrome found$'):
                        gfx_render.Browser()._find_chrome()

    def test_browser_consumer_owns_devnull_child(self):
        self.isolated('_browser_fixture', cwd=Path(self.python).parent)

    def _browser_fixture(self):
        from vtx.render.gfx_render import Browser
        with tempfile.TemporaryDirectory() as d:
            ready = Path(d) / 'ready'
            code = '''import ctypes as c, msvcrt, os, sys, time
from ctypes import wintypes as w
from pathlib import Path
kernel = c.WinDLL('kernel32', use_last_error=True)
kernel.GetFileType.argtypes = (w.HANDLE,)
kernel.GetFinalPathNameByHandleW.argtypes = (w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD)
assert [kernel.GetFileType(msvcrt.get_osfhandle(fd)) for fd in (0, 1, 2)] == [2, 2, 2]
assert not sys.stdin.buffer.read(1)
os.write(1, b'discard'); os.write(2, b'discard')
other = c.create_unicode_buffer(32768)
if kernel.GetFinalPathNameByHandleW(int(sys.argv[2]), other, len(other), 0):
    assert Path(other.value).name != 'extra-inheritable-handle.txt'
ready = Path(sys.argv[1])
tmp = ready.with_suffix('.tmp')
tmp.write_text(str(os.getpid())); tmp.replace(ready)
time.sleep(15)
'''
            # An explicitly provided relative file is normalized without any PATH search.
            explicit = '.\\' + Path(self.python).name
            browser = Browser(explicit, tabs=1)
            try:
                with (Path(d) / 'extra-inheritable-handle.txt').open('wb') as sentinel:
                    import msvcrt
                    handle = msvcrt.get_osfhandle(sentinel.fileno())
                    os.set_handle_inheritable(handle, True)
                    flags = ('-I', '-B', '-c', code, str(ready), str(handle))
                    with patch('vtx.render.gfx_environment.CHROME_VISUAL_FLAGS', flags), \
                            patch('vtx.render.gfx_render.urllib.request.urlopen', side_effect=lambda *_a, **_k:
                                  io.BytesIO(b'{"webSocketDebuggerUrl":"ws://fixture/devtools/page/mock"}')):
                        browser.start()
                    deadline = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(int(ready.read_text()), browser.proc.pid)
                    root_proc = browser.proc
                    self.assertIsNone(root_proc.poll())
            finally:
                browser.stop()
            browser.stop()
            self.assertIsNone(browser.proc)
            self.assertIsNotNone(root_proc.returncode)

    def test_launch_rejects_bare_exe_and_failures_do_not_retry(self):
        from vtx.runtime import windows_browser as wb
        args = [self.python, '-I', '-B', '-c', 'raise SystemExit(11)']
        api = wb._api()
        with patch.object(api, 'CreateProcessW', wraps=api.CreateProcessW) as launch:
            with self.assertRaises(OSError):
                wb.run_browser(['python.exe', *args[1:]], timeout=10)
            launch.assert_not_called()
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(OSError) as raised:
                    wb.run_browser([Path(d) / 'missing.exe', *args[1:]], timeout=10)
            self.assertEqual(raised.exception.winerror, 2)
            launch.assert_called_once()

    def test_launch_attribute_init_failure_closes_acquired_handles(self):
        from vtx.runtime import windows_browser as wb
        api = wb._api()
        init = api.InitializeProcThreadAttributeList

        def fail_init(buffer, *args):
            if buffer is None:
                return init(buffer, *args)
            ctypes.set_last_error(87)
            return False

        with patch.object(api, 'InitializeProcThreadAttributeList', side_effect=fail_init), \
                patch.object(api, 'CreateProcessW') as launch, \
                patch.object(api, 'CreatePipe', wraps=api.CreatePipe) as pipes, \
                patch.object(api, 'CloseHandle', wraps=api.CloseHandle) as closed:
            with self.assertRaises(OSError) as raised:
                wb.run_browser([self.python, '-I', '-B', '-c', 'raise SystemExit(11)'])
        self.assertEqual(raised.exception.winerror, 87)
        launch.assert_not_called()
        self.assertEqual(pipes.call_count, 2)
        # Job + NUL + both ends of each real pipe; each original live handle closes once.
        self.assertEqual(sorted(Counter(call.args[0] for call in closed.call_args_list).values()), [1] * 6)

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
