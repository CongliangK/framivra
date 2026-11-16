"""Pure/mock CDP frame claims: event-driven ownership, no browser or timings."""
import base64
from collections import Counter
import contextlib
import os
from pathlib import Path
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit
import zlib

from vtx.render import gfx_render as gfx
from vtx.render.gfx_cli import png_invalid_reason


def png(frame=0):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes((0, frame % 256, 2, 3, 128)))) + chunk(b'IEND', b''))


class FakeTab:
    def __init__(self, browser, index):
        self.browser, self.index, self.frame = browser, index, None
        self.closed = False
        self.busy = threading.Lock()

    def navigate(self, url, timeout=5):
        self.frame = int(parse_qs(urlsplit(url).query)['f'][0])

    def eval(self, expression):
        if '__vtx_static_spans' in expression:
            return self.browser.spans
        if 'typeof window.__vtx_export_png' in expression:
            return True
        if 'typeof window.__vtx_render' in expression:
            return False
        if expression == 'window.__vtx_export_png()':
            if not self.busy.acquire(blocking=False):
                raise AssertionError('concurrent requests on one tab')
            try:
                with self.browser.records_lock:
                    self.browser.attempts.append((self.index, self.frame, self.browser.generation))
                    attempt = self.browser.counts[self.frame] = self.browser.counts.get(self.frame, 0) + 1
                if self.browser.hook:
                    self.browser.hook(self.index, self.frame)
                if self.frame in self.browser.permanent_fail or (self.frame in self.browser.fail_once and attempt == 1):
                    raise ConnectionError('owned mock frame failure')
                return 'data:image/png;base64,' + base64.b64encode(png(self.frame)).decode()
            finally:
                self.busy.release()
        return None

    def call(self, *args, **kwargs):
        return {}

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, tabs=4, spans=None, hook=None, permanent_fail=(), fail_once=()):
        self.tabs_n, self.spans, self.hook = tabs, spans, hook
        self.permanent_fail, self.fail_once = set(permanent_fail), set(fail_once)
        self.lock, self.records_lock = threading.Lock(), threading.Lock()
        self.generation, self.starts, self.stops = 0, 0, 0
        self.attempts, self.created, self.counts, self.tabs = [], [], {}, []

    def start(self):
        self.starts += 1

    def stop(self):
        self.stops += 1

    def open_tabs(self):
        self.tabs = [FakeTab(self, i) for i in range(self.tabs_n)]
        self.created.extend(self.tabs)
        return self.tabs

    def hard_restart(self):
        # Model the old browser/process closing its sessions before replacement.
        for tab in self.tabs:
            tab.close()
        self.generation += 1


class WorkClaimsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='vtx_claims_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.page = self.root / 'page.html'
        self.page.write_text('<html></html>')
        self.out = self.root / 'frames'
        self.out.mkdir()

    def run_page(self, browser, n, frames=None, threading_api=threading):
        # Remove only the fixed setup sleep in this fake; never use time to
        # coordinate the workers. The real readiness implementation is untouched.
        clock = SimpleNamespace(monotonic=gfx.time.monotonic, sleep=lambda seconds: None)
        with mock.patch.object(gfx, 'Browser', return_value=browser), \
                mock.patch.object(gfx, '_HAS_NUMPY', True), \
                mock.patch.object(gfx, 'time', clock), \
                mock.patch.object(gfx, 'threading', threading_api), \
                mock.patch.dict(os.environ, {'VTX_GFX_NAVFREE': '0', 'VTX_GFX_STATIC': '1'}):
            return gfx.render_page(self.page, self.out, n, tabs=browser.tabs_n,
                                   width=1, height=1, frames=frames)

    def assert_valid(self, frames):
        for i in frames:
            self.assertIsNone(png_invalid_reason(self.out / f'f{i:04d}.png', width=1, height=1))

    def test_fast_tab_claims_work_beyond_old_stride_while_other_tabs_are_busy(self):
        blocked = [threading.Event() for _ in range(3)]
        release = threading.Event()
        extra_claim = threading.Event()
        hook_failures = []
        fast_frames = []
        def hook(tab, frame):
            # Timeouts are deadlock guards, not elapsed-time/performance assertions.
            if tab < 3:
                blocked[tab].set()
                if not release.wait(5):
                    hook_failures.append('missing fast-tab completion signal')
                return
            if not all(event.wait(5) for event in blocked):
                hook_failures.append('missing slow-tab entry signal')
                release.set()
                return
            fast_frames.append(frame)
            if len(fast_frames) > 4:  # old 16-frame/four-tab stride had only four
                extra_claim.set()
            if frame == 15:
                release.set()
        browser = FakeBrowser(hook=hook)
        # Admit slow tabs in order, using their hook-entry signals rather than
        # relying on OS thread-start scheduling to assign frames 0/1/2.
        def thread_factory(*args, **kwargs):
            thread = threading.Thread(*args, **kwargs)
            index = kwargs['args'][0]
            original_start = thread.start
            def start():
                original_start()
                if index < 3 and not blocked[index].wait(5):
                    raise RuntimeError('missing slow-tab admission')
            thread.start = start
            return thread
        api = SimpleNamespace(Thread=thread_factory, Lock=threading.Lock, Event=threading.Event)
        try:
            result = self.run_page(browser, 16, threading_api=api)
        finally:
            release.set()
        self.assertEqual(hook_failures, [])
        self.assertTrue(extra_claim.is_set())
        self.assertEqual(fast_frames, list(range(3, 16)))
        self.assertEqual(Counter(frame for _, frame, _ in browser.attempts), Counter(range(16)))
        self.assertEqual((result['rendered'], result['failed'], result['path_counts']['canvas']), (16, [], 16))
        self.assertEqual(result['tabs'], 4)
        self.assertEqual(browser.starts, 1)
        self.assertEqual(browser.stops, 1)
        self.assertTrue(all(tab.closed for tab in browser.created))
        self.assert_valid(range(16))

    def test_selected_frames_skip_valid_cache_and_leave_outside_frames_untouched(self):
        for i in (1, 3, 6):
            (self.out / f'f{i:04d}.png').write_bytes(png(200 + i))
        before = {i: (self.out / f'f{i:04d}.png').read_bytes() for i in (1, 3, 6)}
        browser = FakeBrowser()
        result = self.run_page(browser, 8, frames=[1, 2, 3, 4, 5])
        self.assertEqual(Counter(i for _, i, _ in browser.attempts), Counter([2, 4, 5]))
        self.assertEqual((result['rendered'], result['failed']), (3, []))
        for i, data in before.items():
            self.assertEqual((self.out / f'f{i:04d}.png').read_bytes(), data)
        self.assertFalse((self.out / 'f0000.png').exists())
        self.assertFalse((self.out / 'f0007.png').exists())
        self.assert_valid([1, 2, 3, 4, 5])

    def test_repeated_selected_items_preserve_baseline_work_and_stats(self):
        browser = FakeBrowser()
        result = self.run_page(browser, 8, frames=[5, 2, 5, 4, 2])
        # Dispatch owns each queued item once; it does not normalize callers'
        # duplicate IDs or change the baseline's per-item rendered/path counts.
        self.assertEqual(Counter(i for _, i, _ in browser.attempts), Counter([5, 2, 5, 4, 2]))
        self.assertEqual((result['rendered'], result['failed'], result['path_counts']['canvas']), (5, [], 5))
        self.assert_valid([2, 4, 5])

    def test_invalid_selected_cache_is_rebuilt_through_full_gate(self):
        (self.out / 'f0001.png').write_bytes(b'not a png')
        browser = FakeBrowser()
        result = self.run_page(browser, 4, frames=[1, 3])
        self.assertEqual(Counter(i for _, i, _ in browser.attempts), Counter([1, 3]))
        self.assertEqual((result['rendered'], result['failed']), (2, []))
        self.assert_valid([1, 3])

    def test_static_pruning_claims_missing_cross_block_head_not_members(self):
        browser = FakeBrowser(spans=[[2, 5]])
        result = self.run_page(browser, 7, frames=[3, 4, 5])
        self.assertEqual(Counter(i for _, i, _ in browser.attempts), Counter([2, 5]))
        self.assertEqual((result['rendered'], result['failed'], result['path_counts']['static_copied']), (2, [], 2))
        self.assertEqual((self.out / 'f0003.png').read_bytes(), png(2))
        self.assertEqual((self.out / 'f0004.png').read_bytes(), png(2))
        self.assertEqual(sorted(p.name for p in self.out.glob('f*.png')),
                         ['f0002.png', 'f0003.png', 'f0004.png', 'f0005.png'])
        self.assert_valid([2, 3, 4, 5])

    def test_static_copy_failure_reinserts_member_before_claims_are_built(self):
        (self.out / 'f0002.png').write_bytes(png(2))
        browser = FakeBrowser(spans=[[2, 5]])
        class Source:
            def copy_to(self, path):
                if str(path).endswith('f0003.png'):
                    raise OSError('owned copy failure')
                Path(path).write_bytes(png(2))
        @contextlib.contextmanager
        def source(*args, **kwargs):
            yield Source()
        with mock.patch.object(gfx.file_clone, 'validated_source', source):
            result = self.run_page(browser, 6, frames=[3, 4, 5])
        self.assertEqual(Counter(i for _, i, _ in browser.attempts), Counter([3, 5]))
        self.assertEqual((result['rendered'], result['failed'], result['path_counts']['static_copied']), (2, [], 1))
        self.assert_valid([3, 4, 5])

    def test_all_static_copies_leave_no_worker_claims(self):
        (self.out / 'f0002.png').write_bytes(png(2))
        browser = FakeBrowser(spans=[[2, 4]])
        result = self.run_page(browser, 4, frames=[2, 3])
        self.assertEqual(browser.attempts, [])
        self.assertEqual((result['rendered'], result['failed'], result['path_counts']['static_copied']), (0, [], 1))
        self.assertTrue(all(tab.closed for tab in browser.created))
        self.assert_valid([2, 3])

    def test_all_cached_and_empty_selection_do_not_start_browser(self):
        (self.out / 'f0001.png').write_bytes(png(1))
        with mock.patch.object(gfx, 'Browser') as browser, mock.patch.object(gfx, '_HAS_NUMPY', True):
            for selected in ([1], []):
                result = gfx.render_page(self.page, self.out, 4, tabs=4, width=1, height=1, frames=selected)
                self.assertEqual((result['rendered'], result['failed']), (0, []))
        browser.assert_not_called()

    def test_permanent_failure_stays_owned_during_retries_then_other_frames_continue(self):
        browser = FakeBrowser(permanent_fail=[0])
        result = self.run_page(browser, 8)
        self.assertEqual(browser.counts[0], 3)  # original attempt plus two retries
        self.assertEqual({i: count for i, count in browser.counts.items() if i != 0}, dict.fromkeys(range(1, 8), 1))
        self.assertEqual((result['rendered'], result['failed'], result['path_counts']['failed']), (7, [0], 1))
        self.assertEqual(result['path_counts']['canvas'], 7)
        self.assertEqual(browser.generation, 2)
        self.assertTrue(all(tab.closed for tab in browser.created))
        self.assert_valid(range(1, 8))

    def test_generation_restart_retries_same_claim_once_and_keeps_unique_other_claims(self):
        browser = FakeBrowser(fail_once=[0])
        result = self.run_page(browser, 8)
        self.assertEqual(browser.counts[0], 2)
        self.assertEqual({i: count for i, count in browser.counts.items() if i != 0}, dict.fromkeys(range(1, 8), 1))
        self.assertEqual((result['rendered'], result['failed'], result['path_counts']['canvas']), (8, [], 8))
        self.assertEqual(browser.generation, 1)
        self.assertTrue(all(tab.closed for tab in browser.created))
        self.assert_valid(range(8))

    def run_abort_case(self, mode, cleanup_error=False):
        class WorkerAbort(BaseException):
            pass
        original_error = WorkerAbort('owned worker fatal') if mode == 'worker' else KeyboardInterrupt('owned owner cancel')
        owner = threading.get_ident()
        entered = [threading.Event() for _ in range(4)]
        events, threads, failures = [], [], []
        active = set()
        active_lock = threading.Lock()
        wait_interrupted = [False]

        class Event(threading.Event):
            def __init__(self):
                super().__init__()
                self.ordinal = len(events)
                events.append(self)

            def wait(self, timeout=None):
                # Ordinal 0 is stop, ordinal 1 is worker-0's target completion.
                if (mode == 'wait' and self.ordinal == 1 and threading.get_ident() == owner
                        and not wait_interrupted[0]):
                    wait_interrupted[0] = True
                    if not all(signal.wait(5) for signal in entered):
                        failures.append('workers did not enter before wait cancellation')
                    raise original_error
                return super().wait(timeout)

        def hook(index, frame):
            with active_lock:
                active.add(index)
            entered[index].set()
            try:
                if mode == 'worker' and index == 0:
                    if not all(signal.wait(5) for signal in entered):
                        failures.append('workers did not enter before fatal')
                    raise original_error
                if not events[0].wait(5):
                    failures.append('stop was not published')
                # An ordinary frame failure observed after stop must not heal,
                # restart or take another queue item, even though retries exist.
                raise ConnectionError('owned in-flight failure after stop')
            finally:
                with active_lock:
                    active.remove(index)

        def thread_factory(*args, **kwargs):
            thread = threading.Thread(*args, **kwargs)
            index = kwargs['args'][0]
            threads.append(thread)
            original_start = thread.start
            def start():
                if mode == 'partial_start' and index == 1:
                    raise original_error  # this thread never starts
                original_start()
                if mode in ('start', 'partial_start'):
                    if not entered[index].wait(5):
                        failures.append('started worker did not enter')
                    if mode == 'start' and index == 3:
                        raise original_error  # cancellation after the OS start
            thread.start = start
            return thread

        browser = FakeBrowser(hook=hook)
        original_stop = browser.stop
        cleanup_seen = []
        def stop():
            with active_lock:
                cleanup_seen.append(set(active))
            if any(thread.is_alive() for thread in threads):
                failures.append('owner cleanup preceded worker settlement')
            original_stop()
            if cleanup_error:
                raise RuntimeError('secondary cleanup error')
        browser.stop = stop
        api = SimpleNamespace(Thread=thread_factory, Lock=threading.Lock, Event=Event)
        with self.assertRaises(type(original_error)) as caught:
            self.run_page(browser, 40, threading_api=api)
        self.assertIs(caught.exception, original_error)
        self.assertEqual(failures, [])
        self.assertEqual(cleanup_seen, [set()])
        self.assertTrue(events[0].is_set())
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(browser.generation, 0)
        self.assertEqual(browser.stops, 1)
        self.assertTrue(all(tab.closed for tab in browser.created))
        expected = 1 if mode == 'partial_start' else 4
        self.assertEqual(len(browser.attempts), expected)
        self.assertEqual(sum(browser.counts.values()), expected)
        self.assertTrue(all(count == 1 for count in browser.counts.values()))
        for index, thread in enumerate(threads):
            if thread.ident is not None:
                self.assertTrue(events[index + 1].is_set())

    def test_worker_fatal_stops_claims_and_is_rethrown_after_all_workers_settle(self):
        self.run_abort_case('worker')

    def test_owner_cancel_during_completion_wait_stops_before_cleanup(self):
        self.run_abort_case('wait')

    def test_owner_cancel_after_thread_start_joins_every_started_worker(self):
        self.run_abort_case('start')

    def test_owner_cancel_partway_through_start_does_not_wait_for_unstarted_workers(self):
        self.run_abort_case('partial_start')

    def test_secondary_cleanup_error_does_not_replace_first_worker_fatal(self):
        self.run_abort_case('worker', cleanup_error=True)

    def test_owner_interrupt_during_actual_join_preserves_cause_and_settles_threads(self):
        error = KeyboardInterrupt('owned join cancel')
        release = threading.Event()
        tails = [threading.Event() for _ in range(4)]
        threads, failures = [], []
        interrupted = [False]
        class Thread(threading.Thread):
            def run(self):
                index = self._args[0]
                super().run()
                # The production target has signalled done, but OS thread
                # teardown is intentionally held so owner must really join it.
                tails[index].set()
                if not release.wait(5):
                    failures.append('thread-tail release missing')

            def join(self, *args, **kwargs):
                if not interrupted[0]:
                    interrupted[0] = True
                    if not all(tail.wait(5) for tail in tails):
                        failures.append('not all thread tails entered')
                    release.set()
                    raise error
                return super().join(*args, **kwargs)

        def factory(*args, **kwargs):
            thread = Thread(*args, **kwargs)
            threads.append(thread)
            return thread
        browser = FakeBrowser()
        original_stop = browser.stop
        def stop():
            if any(thread.is_alive() for thread in threads):
                failures.append('cleanup before actual join settlement')
            original_stop()
        browser.stop = stop
        api = SimpleNamespace(Thread=factory, Lock=threading.Lock, Event=threading.Event)
        try:
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.run_page(browser, 8, threading_api=api)
        finally:
            release.set()
        self.assertIs(caught.exception, error)
        self.assertTrue(interrupted[0])
        self.assertEqual(failures, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(browser.stops, 1)
        self.assertTrue(all(tab.closed for tab in browser.created))


if __name__ == '__main__':
    unittest.main()
