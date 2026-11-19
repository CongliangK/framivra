"""Bounded native-worker lifecycle tests; no browser, signals or media tools."""
import contextlib
import threading
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vtx.render import gfx_render as render


class SocketCancellationTests(unittest.TestCase):
    def test_interrupt_does_not_close_readiness_fd_even_when_disconnected(self):
        import socket
        for error in (None, OSError('already disconnected')):
            with self.subTest(error=error):
                endpoint = mock.Mock()
                endpoint.shutdown.side_effect = error
                ws = render.WSConn.__new__(render.WSConn)
                ws.sock, ws._io_guard = endpoint, threading.Lock()
                ws.interrupt()
                self.assertEqual(endpoint.mock_calls, [mock.call.shutdown(socket.SHUT_RDWR)])
                ws.close()
                self.assertEqual(endpoint.mock_calls,
                                 [mock.call.shutdown(socket.SHUT_RDWR),
                                  mock.call.shutdown(socket.SHUT_RDWR), mock.call.close()])

    def test_close_retains_fd_until_inflight_io_has_returned(self):
        entered, shutdown, release, closed, close_acquiring = (threading.Event() for _ in range(5))
        lock = threading.Lock()

        class Guard:
            def __enter__(self):
                if shutdown.is_set():
                    close_acquiring.set()
                return lock.__enter__()

            def __exit__(self, *args):
                return lock.__exit__(*args)

        endpoint = mock.Mock()
        endpoint.shutdown.side_effect = lambda *_: shutdown.set()
        endpoint.close.side_effect = closed.set

        def receive():
            entered.set()
            if not release.wait(3):
                raise AssertionError('probe I/O was not released')
            return b''

        endpoint.recv.side_effect = receive
        ws = render.WSConn.__new__(render.WSConn)
        ws.sock, ws._io_guard = endpoint, Guard()
        reader = threading.Thread(target=lambda: ws._io(endpoint.recv))
        closer = threading.Thread(target=ws.close)
        reader.start()
        try:
            self.assertTrue(entered.wait(1))
            closer.start()
            self.assertTrue(shutdown.wait(1))
            self.assertTrue(close_acquiring.wait(1))
            self.assertFalse(closed.is_set(), 'close must preserve an active I/O fd')
        finally:
            release.set()
            reader.join(4)
            if closer.ident is not None:
                closer.join(4)
        self.assertFalse(reader.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertTrue(closed.is_set())

    def test_interrupt_wakes_blocked_receive_without_peer_exit(self):
        import socket
        local, peer = socket.socketpair()
        local.settimeout(3)
        entered, finished = threading.Event(), threading.Event()
        errors = []
        ws = render.WSConn.__new__(render.WSConn)
        ws.sock, ws._rbuf, ws._io_guard = local, bytearray(), threading.Lock()

        def receive():
            entered.set()
            try:
                ws.recv_msg()
            except (OSError, ConnectionError) as error:
                errors.append(error)
            finally:
                finished.set()

        thread = threading.Thread(target=receive)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            ws.interrupt()
            # Keep both fds alive: close can strand a concurrent readiness wait.
            self.assertTrue(finished.wait(1), 'owned receive must wake on shutdown')
            self.assertTrue(errors)
            ws.close()  # Already closed/disconnected is harmless.
        finally:
            peer.close()
            local.close()
            thread.join(4)
        self.assertFalse(thread.is_alive())


class RenderCancellationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.written = set()
        self.mode = 'cancel'
        case = self

        class Tab:
            def navigate(self, url):
                pass

            def eval(self, expression):
                return True if '__vtx_export_png' in expression else None

            def interrupt(self):
                case.events.append('interrupt')

            def close(self):
                case.events.append('close')

        class Browser:
            def __init__(self, *args, **kwargs):
                self.lock = threading.Lock()
                self.generation = 0

            def start(self):
                case.events.append('start')

            def open_tabs(self):
                return [Tab(), Tab()]

            def hard_restart(self):
                case.events.append('heal')
                self.generation += 1

            def stop(self):
                case.events.append('stop')
                case.release.set()

        self.browser = Browser

    def export(self, *args):
        self.entered.set()
        if self.mode == 'retry':
            if self.events.count('heal') == 0:
                raise ConnectionError('lost connection')
            return b'png'
        if not self.release.wait(2):
            raise AssertionError('test did not release worker')
        self.events.append('worker-failed')
        self.finished.set()
        raise ConnectionError('browser stopped')

    @contextlib.contextmanager
    def environment(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(render, 'Browser', self.browser))
            stack.enter_context(mock.patch.object(render, '_HAS_NUMPY', True))
            stack.enter_context(mock.patch.object(render, 'decode_canvas_export', side_effect=self.export))
            stack.enter_context(mock.patch.object(render, '_atomic_write_bytes', side_effect=self.write))
            stack.enter_context(mock.patch.object(render, '_timings_summary', return_value={}))
            stack.enter_context(mock.patch.object(render, 'verify_frames', side_effect=lambda out, frames, **kw: [(i, 'missing') for i in frames if i not in self.written]))
            stack.enter_context(mock.patch.object(render.time, 'sleep', return_value=None))
            stack.enter_context(mock.patch.dict('os.environ', {'VTX_GFX_STATIC': '0', 'VTX_GFX_NAVFREE': '0'}))
            yield directory

    def write(self, path, data, **kwargs):
        self.written.add(int(Path(path).stem[1:]))

    def test_interrupt_join_cancels_before_teardown_and_drains_workers(self):
        original_join = threading.Thread.join
        interrupted = False

        def join(thread, *args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                self.assertTrue(self.entered.wait(2))
                raise KeyboardInterrupt('mock join interruption')
            return original_join(thread, *args, **kwargs)

        with self.environment() as directory, mock.patch.object(threading.Thread, 'join', join):
            with self.assertRaisesRegex(KeyboardInterrupt, 'mock join'):
                render.render_page('page.html', directory, 2, tabs=2)
        self.assertTrue(self.finished.is_set())
        self.assertNotIn('heal', self.events)
        self.assertEqual(self.events.count('start'), 1)
        self.assertEqual(self.events.count('stop'), 1)
        self.assertLess(self.events.index('interrupt'), self.events.index('stop'))
        self.assertLess(self.events.index('worker-failed'), self.events.index('close'))
        self.assertEqual(self.written, set())

    def test_stage_cancel_token_wakes_render_and_drains_workers(self):
        cancel = threading.Event()

        def cancel_inflight():
            if self.entered.wait(2):
                cancel.set()

        signal_worker = threading.Thread(target=cancel_inflight)
        with self.environment() as directory:
            signal_worker.start()
            try:
                with self.assertRaisesRegex(render.GfxRenderError, 'cancelled'):
                    render.render_page('page.html', directory, 1, tabs=1, cancel=cancel)
            finally:
                signal_worker.join(3)
        self.assertTrue(self.finished.is_set())
        self.assertEqual(self.events.count('stop'), 1)
        self.assertNotIn('heal', self.events)
        self.assertLess(self.events.index('worker-failed'), self.events.index('close'))
        self.assertFalse(self.written)

    def test_teardown_waits_for_inflight_write_before_returning(self):
        original_join = threading.Thread.join
        interrupted = False

        def write(*args, **kwargs):
            self.entered.set()
            self.assertTrue(self.release.wait(2))
            self.events.append('write-settled')
            self.finished.set()

        def join(thread, *args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                self.assertTrue(self.entered.wait(2))
                raise KeyboardInterrupt()
            return original_join(thread, *args, **kwargs)

        with self.environment() as directory, mock.patch.object(render, 'decode_canvas_export', return_value=b'png'), mock.patch.object(render, '_atomic_write_bytes', side_effect=write), mock.patch.object(threading.Thread, 'join', join):
            with self.assertRaises(KeyboardInterrupt):
                render.render_page('page.html', directory, 1, tabs=1)
            self.assertTrue(self.finished.is_set())
            self.assertLess(self.events.index('stop'), self.events.index('write-settled'))

    def test_partial_thread_start_error_drains_started_worker(self):
        original_start = threading.Thread.start
        starts = 0

        def start(thread):
            nonlocal starts
            starts += 1
            if starts == 2:
                self.assertTrue(self.entered.wait(2))
                raise RuntimeError('mock start failure')
            return original_start(thread)

        with self.environment() as directory, mock.patch.object(threading.Thread, 'start', start):
            with self.assertRaisesRegex(RuntimeError, 'mock start failure'):
                render.render_page('page.html', directory, 2, tabs=2)
        self.assertTrue(self.finished.is_set())
        self.assertNotIn('heal', self.events)

    def test_delayed_native_bootstrap_enters_only_inert_target_after_cancel(self):
        pending = []
        base_browser = self.browser
        case = self

        class Browser(base_browser):
            def __getattribute__(self, name):
                if name == 'generation' and case.release.is_set():
                    raise AssertionError('late target accessed torn-down browser')
                return super().__getattribute__(name)

        class DelayedThread:
            ident = None

            def __init__(self, target, args):
                self.target, self.args = target, args
                pending.append(self)

            def start(self):
                # Model native launch having happened, but bootstrap not yet
                # publishing ident or entering the Python target when interrupted.
                raise KeyboardInterrupt('bootstrap pending')

            def join(self):
                case.fail('unbootstrapped thread must not be joined')

        progress = mock.Mock()
        with self.environment() as directory, mock.patch.object(render, 'Browser', Browser), mock.patch.object(render.threading, 'Thread', DelayedThread), mock.patch.dict('os.environ', {'VTX_GFX_NAVFREE': '1'}):
            with self.assertRaisesRegex(KeyboardInterrupt, 'bootstrap pending'):
                render.render_page('page.html', directory, 1, tabs=1, progress=progress)
            self.assertTrue(self.release.is_set())
            events_after_return = list(self.events)
            callbacks_after_return = list(progress.mock_calls)
            # Deliver the native bootstrap deterministically after API return.
            pending[0].target(*pending[0].args)
            self.assertEqual(self.events, events_after_return)
            self.assertEqual(progress.mock_calls, callbacks_after_return)
            self.assertEqual(self.written, set())
            self.assertFalse(self.entered.is_set())

    def test_cancellation_serializes_teardown_with_inflight_heal(self):
        healing = threading.Event()
        release_heal = threading.Event()
        original_join = threading.Thread.join
        base_browser = self.browser
        case = self

        class RestartLock:
            def __init__(self):
                self.lock = threading.Lock()

            def acquire(self):
                if threading.current_thread() is threading.main_thread():
                    release_heal.set()
                return self.lock.acquire()

            def release(self):
                self.lock.release()

            def __enter__(self):
                self.acquire()

            def __exit__(self, *args):
                self.release()

        class Browser(base_browser):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.lock = RestartLock()

            def hard_restart(self):
                healing.set()
                if not release_heal.wait(2):
                    raise AssertionError('teardown did not wait for heal')
                case.events.append('heal-finished')
                self.generation += 1

            def open_tabs(self):
                case.events.append('open-tabs')
                return super().open_tabs()

        interrupted = False

        def join(thread, *args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                self.assertTrue(healing.wait(2))
                raise KeyboardInterrupt()
            return original_join(thread, *args, **kwargs)

        with self.environment() as directory, mock.patch.object(render, 'Browser', Browser), mock.patch.object(render, 'decode_canvas_export', side_effect=ConnectionError('lost connection')), mock.patch.object(threading.Thread, 'join', join):
            with self.assertRaises(KeyboardInterrupt):
                render.render_page('page.html', directory, 2, tabs=2)
        self.assertEqual(self.events.count('open-tabs'), 1)
        self.assertEqual(self.events.count('heal-finished'), 1)
        self.assertLess(self.events.index('heal-finished'), self.events.index('stop'))
        self.assertEqual(self.written, set())

    def test_interruptions_in_close_and_stop_are_retried_before_escape(self):
        for cancel_join in (False, True):
            with self.subTest(cancel_join=cancel_join):
                self.events.clear()
                self.written.clear()
                self.release.clear()
                self.entered.clear()
                self.finished.clear()
                base_browser = self.browser
                close_calls = []
                stop_calls = []

                class Browser(base_browser):
                    def open_tabs(self):
                        pool = super().open_tabs()
                        for tab in pool:
                            close = mock.Mock(side_effect=[KeyboardInterrupt('close'), None])
                            close_calls.append(close)
                            tab.close = close
                        return pool

                    def stop(self):
                        stop_calls.append(True)
                        if len(stop_calls) == 1:
                            raise KeyboardInterrupt('stop')
                        return super().stop()

                original_join = threading.Thread.join
                interrupted = False

                def join(thread, *args, **kwargs):
                    nonlocal interrupted
                    if cancel_join and not interrupted:
                        interrupted = True
                        self.assertTrue(self.entered.wait(2))
                        raise KeyboardInterrupt('initial join')
                    return original_join(thread, *args, **kwargs)

                with self.environment() as directory, mock.patch.object(render, 'Browser', Browser), mock.patch.object(threading.Thread, 'join', join), contextlib.ExitStack() as stack:
                    if not cancel_join:
                        stack.enter_context(mock.patch.object(render, 'decode_canvas_export', return_value=b'png'))
                    expected = 'initial join' if cancel_join else 'stop'
                    with self.assertRaisesRegex(KeyboardInterrupt, expected):
                        render.render_page('page.html', directory, 1, tabs=1)
                self.assertEqual(len(stop_calls), 2)
                self.assertTrue(all(close.call_count == 2 for close in close_calls))
                self.assertTrue(self.release.is_set())
                if cancel_join:
                    self.assertTrue(self.finished.is_set())

    def test_windows_job_interruption_stays_primary_after_fallback_close(self):
        from vtx.runtime import windows_browser as wb

        render_browser = render.Browser
        base_browser = self.browser
        case = self
        for initial_cancel in (False, True):
            with self.subTest(initial_cancel=initial_cancel):
                self.release.clear()
                self.entered.clear()
                self.written.clear()
                original = KeyboardInterrupt('join cancelled')
                close_cancel = KeyboardInterrupt('accounting cancelled')
                job = wb.BrowserJob.__new__(wb.BrowserJob)
                job._job, job._info, job._captures = wb._H(10), wb._ProcessInfo(), []
                job._info.process = 20
                job._close_failed = False
                job.proc = mock.Mock()
                job._api = mock.Mock()
                job._api.QueryInformationJobObject.side_effect = close_cancel

                class Browser(base_browser):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        self.windows_job = job
                        self._windows_cleanup = None
                        self.group = None
                        self.proc = job.proc
                        self.user_data_dir = 'unconfirmed-profile'

                    def stop(self):
                        case.release.set()
                        return render_browser.stop(self)

                original_join = threading.Thread.join
                cancelled = False

                def join(thread):
                    nonlocal cancelled
                    if initial_cancel and not cancelled:
                        cancelled = True
                        self.assertTrue(self.entered.wait(2))
                        raise original
                    return original_join(thread)

                with self.environment() as directory, \
                        mock.patch.object(render, 'Browser', Browser), \
                        mock.patch.object(render, 'decode_canvas_export',
                                          side_effect=self.export if initial_cancel else lambda *_: b'png'), \
                        mock.patch.object(threading.Thread, 'join', join), \
                        mock.patch.object(render.shutil, 'rmtree', wraps=render.shutil.rmtree) as remove:
                    with self.assertRaises(KeyboardInterrupt) as failure:
                        render.render_page('page.html', directory, 1, tabs=1)
                    remove.assert_not_called()
                self.assertIs(failure.exception, original if initial_cancel else close_cancel)
                self.assertIsInstance(failure.exception.__cause__, wb.JobSettlementError)
                self.assertTrue(job._close_failed)
                self.assertIsNone(job._job.value)
                self.assertIsNone(job._info.process)
                self.assertEqual(job._api.CloseHandle.call_count, 2)
                job._api.QueryInformationJobObject.assert_called_once()

    def test_browser_stop_retains_owned_handles_after_interrupt(self):
        for attribute, operation in (('group', 'stop'), ('windows_job', 'close')):
            with self.subTest(attribute=attribute):
                browser = render.Browser()
                handle = mock.Mock()
                cleanup = getattr(handle, operation)
                cleanup.side_effect = [KeyboardInterrupt('cleanup'), None]
                setattr(browser, attribute, handle)
                with self.assertRaises(KeyboardInterrupt):
                    browser.stop()
                self.assertIs(getattr(browser, attribute), handle)
                browser.stop()
                self.assertIsNone(getattr(browser, attribute))
                self.assertEqual(cleanup.call_count, 2)

    def test_repeated_heals_close_superseded_pool_before_opening_next(self):
        base_browser = self.browser
        case = self
        opens = 0
        exports = 0

        class Browser(base_browser):
            def open_tabs(self):
                nonlocal opens
                case.assertEqual(case.events.count('close'), opens * 2)
                opens += 1
                return super().open_tabs()

        def export(*args):
            nonlocal exports
            exports += 1
            if exports % 2:
                raise ConnectionError('one failure per frame')
            return b'png'

        with self.environment() as directory, mock.patch.object(render, 'Browser', Browser), mock.patch.object(render, 'decode_canvas_export', side_effect=export):
            stats = render.render_page('page.html', directory, 10, tabs=1)
        self.assertEqual(stats['rendered'], 10)
        self.assertEqual(stats['failed'], [])
        self.assertEqual(opens, 11)
        self.assertEqual(self.events.count('close'), 22)

    def test_normal_connection_failure_still_heals_and_renders(self):
        self.mode = 'retry'
        with self.environment() as directory:
            stats = render.render_page('page.html', directory, 1, tabs=1)
        self.assertEqual(stats['rendered'], 1)
        self.assertEqual(stats['failed'], [])
        self.assertEqual(stats['path_counts']['canvas'], 1)
        self.assertEqual(self.events.count('heal'), 1)
        self.assertEqual(self.events.count('close'), 4)


if __name__ == '__main__':
    unittest.main()
