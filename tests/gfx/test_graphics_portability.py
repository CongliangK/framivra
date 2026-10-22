"""Run: python -m unittest discover -s tests -v

Set VTX_TEST_CHROME and/or VTX_TEST_FFMPEG to enable real integration checks.
All fixtures are synthetic. No private footage or machine paths are required.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.environ.get('VTX_TEST_MODULE_DIR', str(Path(__file__).resolve().parents[2])))
from vtx.render import gfx_render as gfx
from vtx.pipeline import infra
from vtx.cli import commands as vtx


class CDPDeadlineMock(unittest.TestCase):
    """Owned clock/byte stream only: exercise the real Tab and WS frame loops."""

    class Socket:
        def __init__(self, clock, data, *, timeout=20, delays=(), send_delay=0, chunk=None):
            self.clock, self.data = clock, bytearray(data)
            self.timeout, self.delays = timeout, list(delays)
            self.send_delay, self.chunk = send_delay, chunk
            self.calls, self.sent, self.closed = [], [], 0
            self.error = None

        def gettimeout(self): return self.timeout
        def settimeout(self, value): self.timeout = value
        def close(self): self.closed += 1

        def sendall(self, data):
            self.calls.append(('send', self.timeout))
            self.clock[0] += self.send_delay
            if self.error and self.error[0] == 'send': raise self.error[1]
            self.sent.append(data)

        def recv(self, n):
            self.calls.append(('recv', self.timeout))
            self.clock[0] += self.delays.pop(0) if self.delays else 0
            if self.error and self.error[0] == 'recv': raise self.error[1]
            n = min(n, self.chunk) if self.chunk else n
            data = bytes(self.data[:n]); del self.data[:n]
            return data

    def setUp(self):
        self.clock = [0.0]
        # Keep the test runner's shared stdlib clock real throughout subtests.
        clock = SimpleNamespace(monotonic=lambda: self.clock[0])
        for patcher in (mock.patch.object(gfx, 'time', clock),
                        mock.patch.object(gfx.os, 'urandom', lambda n: b'\0' * n)):
            patcher.start(); self.addCleanup(patcher.stop)

    @staticmethod
    def frame(message, op=0x81, length_bytes=0, masked=False):
        data = json.dumps(message).encode() if isinstance(message, dict) else message
        length_bytes = length_bytes or (2 if len(data) >= 126 else 0)
        tag = (126 if length_bytes == 2 else 127) if length_bytes else len(data)
        return (bytes([op, tag | (0x80 if masked else 0)])
                + (len(data).to_bytes(length_bytes, 'big') if length_bytes else b'')
                + (b'\0' * 4 if masked else b'') + data)

    def tab(self, data=None, **kwargs):
        self.clock[0] = 0
        sock = self.Socket(self.clock, self.frame({'id': 1, 'result': {'ok': True}})
                           if data is None else data, **kwargs)
        ws = gfx.WSConn.__new__(gfx.WSConn); ws.sock = sock; ws._rbuf = bytearray()
        tab = gfx.Tab.__new__(gfx.Tab)
        tab.ws, tab._mid, tab.request_policy, tab._policy_commands = ws, 0, None, set()
        return tab, sock

    def assert_timeout(self, tab, sock, timeout=1):
        previous = sock.timeout
        with self.assertRaisesRegex(TimeoutError, 'CDP Runtime.evaluate timeout'):
            tab.call('Runtime.evaluate', timeout=timeout)
        self.assertEqual(sock.timeout, previous)
        self.assertEqual(sock.closed, 1)
        self.assertIsNone(tab.ws._deadline)

    def test_matching_reply_before_at_and_after_deadline(self):
        for delay in (.999, 1, 2):
            with self.subTest(delay=delay):
                tab, sock = self.tab(delays=[0, delay])
                if delay < 1:
                    self.assertEqual(tab.call('Runtime.evaluate', timeout=1), {'ok': True})
                    self.assertEqual(sock.closed, 0)
                    self.assertEqual(sock.timeout, 20)
                    self.assertIsNone(tab.ws._deadline)
                else:
                    self.assert_timeout(tab, sock)
        for timeout in (0, -1):
            tab, sock = self.tab()
            self.assert_timeout(tab, sock, timeout)
            self.assertEqual(sock.calls, [])

    def test_send_consumes_the_same_deadline(self):
        tab, sock = self.tab(send_delay=1)
        self.assert_timeout(tab, sock)
        self.assertEqual([op for op, _ in sock.calls], ['send'])

    def test_remaining_timeout_never_extends_socket_timeout_and_restores(self):
        for previous in (None, 20, .05):
            with self.subTest(previous=previous):
                tab, sock = self.tab(timeout=previous, send_delay=.2, delays=[.2, .2])
                self.assertEqual(tab.call('Runtime.evaluate', timeout=1), {'ok': True})
                for (_, actual), remaining in zip(sock.calls, (1, .8, .6)):
                    self.assertAlmostEqual(actual, remaining if previous is None else min(previous, remaining))
                self.assertEqual(len(sock.calls), 3)
                self.assertEqual(sock.timeout, previous)
                self.assertIsNone(tab.ws._deadline)

    def test_control_and_fragment_floods_cannot_refresh_deadline(self):
        reply = self.frame({'id': 1, 'result': {'ok': True}})
        for op in (0x89, 0x8a, 0x00):
            with self.subTest(op=op):
                data = ((self.frame(b'', 0x01) + self.frame(b'', op) * 20
                         + self.frame(json.dumps({'id': 1, 'result': {'ok': True}}).encode(), 0x80))
                        if op == 0 else self.frame(b'p', op) * 20 + reply)
                tab, sock = self.tab(data, delays=[.1] * 100)
                self.assert_timeout(tab, sock)
                self.assertTrue(sock.data)
                self.assertLess(len(sock.sent), 20)

    def test_fragmentation_ping_mask_and_extended_lengths_preserve_success(self):
        reply = json.dumps({'id': 1, 'result': {'ok': True, 'padding': 'x' * 130}}).encode()
        for width in (2, 8):
            data = (self.frame(reply[:12], 0x01) + self.frame(b'p', 0x89)
                    + self.frame(b'p', 0x8a) + self.frame(reply[12:], 0x80, width, True))
            tab, sock = self.tab(data, chunk=1, delays=[.001] * 300)
            self.assertEqual(tab.call('Runtime.evaluate', timeout=1), {'ok': True, 'padding': 'x' * 130})
            self.assertEqual(sock.sent[-1][0], 0x8a)
            self.assertEqual(sock.closed, 0)
            self.assertEqual(sock.timeout, 20)

    def test_partial_header_extended_length_mask_and_body_are_bounded(self):
        reply = self.frame({'id': 1, 'result': {'ok': True}}, length_bytes=8, masked=True)
        for delays in ([1], [0, 1], [0, 0, 1], [0, 0, 0, 1]):
            with self.subTest(delays=delays):
                tab, sock = self.tab(reply, delays=delays)
                self.assert_timeout(tab, sock)
        tab, sock = self.tab(reply, chunk=1, delays=[.1] * 100)
        self.assert_timeout(tab, sock)
        self.assertTrue(sock.data)

    def test_buffered_control_frames_and_delayed_recv_facade_are_bounded(self):
        tab, sock = self.tab(b'')
        tab.ws._rbuf = bytearray(self.frame(b'', 0x8a) * 100)
        def ticking():
            self.clock[0] += .1
            return self.clock[0]
        with mock.patch.object(gfx.time, 'monotonic', ticking):
            self.assert_timeout(tab, sock)
        self.assertFalse(any(op == 'recv' for op, _ in sock.calls))
        tab, sock = self.tab()
        def late_reply():
            self.clock[0] = 2
            return 1, json.dumps({'id': 1, 'result': {'ok': True}})
        with mock.patch.object(tab.ws, 'recv_msg', late_reply):
            self.assert_timeout(tab, sock)

    def test_strict_ack_is_drained_and_errors_still_propagate(self):
        event = {'method': 'Fetch.requestPaused', 'params': {'requestId': 'owned'}}
        response = {'id': 1, 'result': {'ok': True}}
        for outcome in ('success', 'error', 'timeout'):
            with self.subTest(outcome=outcome):
                ack = {'id': 2, 'error': {'message': 'denied'}} if outcome == 'error' else {'id': 2, 'result': {}}
                tab, sock = self.tab(b''.join(self.frame(m) for m in (event, response, ack)),
                                     delays=[0, 0, 0, 0, 1] if outcome == 'timeout' else [])
                policy = tab.request_policy = mock.Mock()
                policy.response.return_value = ('Fetch.fulfillRequest', {'requestId': 'owned'})
                if outcome == 'timeout':
                    self.assert_timeout(tab, sock)
                    self.assertEqual(tab._policy_commands, {2})
                elif outcome == 'error':
                    with self.assertRaisesRegex(RuntimeError, 'strict request enforcement failed'):
                        tab.call('Runtime.evaluate', timeout=1)
                else:
                    self.assertEqual(tab.call('Runtime.evaluate', timeout=1), {'ok': True})
                    policy.check.assert_called_once_with()
                self.assertEqual(len(sock.sent), 2)
                if outcome != 'timeout':
                    self.assertEqual(tab._policy_commands, set())
                    self.assertFalse(sock.data)
                    self.assertEqual(sock.closed, 0)
                self.assertEqual(sock.timeout, 20)
                self.assertIsNone(tab.ws._deadline)

    def test_policy_check_cannot_return_late_success(self):
        tab, sock = self.tab()
        tab.request_policy = mock.Mock()
        tab.request_policy.check.side_effect = lambda: self.clock.__setitem__(0, 1)
        self.assert_timeout(tab, sock)

    def test_policy_response_cannot_send_after_deadline(self):
        event = {'method': 'Fetch.requestPaused', 'params': {'requestId': 'owned'}}
        tab, sock = self.tab(self.frame(event))
        tab.request_policy = mock.Mock()
        def response(params):
            self.clock[0] = 1
            return 'Fetch.fulfillRequest', {'requestId': 'owned'}
        tab.request_policy.response.side_effect = response
        self.assert_timeout(tab, sock)
        self.assertEqual(len(sock.sent), 1)
        self.assertEqual(tab._policy_commands, {2})

    def test_io_exceptions_restore_timeout_and_deadline(self):
        for phase in ('send', 'recv'):
            for error in (OSError('owned failure'), TimeoutError('owned timeout'), KeyboardInterrupt('owned stop')):
                with self.subTest(phase=phase, error=type(error).__name__):
                    tab, sock = self.tab(); sock.error = (phase, error)
                    if isinstance(error, TimeoutError):
                        self.assert_timeout(tab, sock)
                    else:
                        with self.assertRaises(type(error)) as caught:
                            tab.call('Runtime.evaluate', timeout=1)
                        self.assertIs(caught.exception, error)
                        self.assertEqual(sock.closed, 0)
                    self.assertEqual(sock.timeout, 20)
                    self.assertIsNone(tab.ws._deadline)

    def test_protocol_errors_and_prior_scope_are_preserved(self):
        for data, kind in ((b'', ConnectionError), (self.frame(b'', 0x88), ConnectionError),
                           (self.frame(b'not-json'), ValueError),
                           (self.frame({'id': 1, 'error': {'message': 'owned'}}), RuntimeError)):
            tab, sock = self.tab(data); tab.ws._deadline = 99
            with self.assertRaises(kind): tab.call('Runtime.evaluate', timeout=1)
            self.assertEqual(sock.timeout, 20)
            self.assertEqual(tab.ws._deadline, 99)
            self.assertEqual(sock.closed, 0)

    def test_initialization_failure_keeps_fd_cleanup(self):
        tab, sock = self.tab(delays=[0, 31])
        def init(ws, *args, **kwargs):
            ws.sock, ws._rbuf = sock, bytearray()
        with mock.patch.object(gfx.WSConn, '__init__', init):
            with self.assertRaises(TimeoutError): gfx.Tab(1, '/owned')
        self.assertGreaterEqual(sock.closed, 1)
        self.assertEqual(sock.timeout, 20)

    def test_standalone_transport_keeps_its_original_timeout(self):
        tab, sock = self.tab(timeout=.5)
        tab.ws.send_text('owned')
        op, data = tab.ws.recv_msg()
        self.assertEqual((op, json.loads(data)['result']), (1, {'ok': True}))
        self.assertEqual(sock.calls, [('send', .5), ('recv', .5), ('recv', .5)])
        self.assertEqual(sock.closed, 0)


def _cached_png(value=0):
    """A real 6×4 cache fixture; file existence alone is not validity."""
    import numpy as np
    return gfx.png_encode_rgba(np.full((4, 6, 4), value, dtype=np.uint8))


class ExportValidation(unittest.TestCase):
    def test_valid_and_invalid_png(self):
        import numpy as np
        png = gfx.png_encode_rgba(np.zeros((2, 3, 4), dtype=np.uint8))
        def url(data):
            return 'data:image/png;base64,' + base64.b64encode(data).decode()
        self.assertEqual(gfx.decode_canvas_export(url(png), 3, 2), png)
        for bad in [None, 'data:,', 'data:image/png;base64,!!!', url(png[:24]),
                    url(png[:-1]), url(png[:40] + bytes([png[40] ^ 1]) + png[41:])]:
            with self.subTest(bad=str(bad)[:40]):
                with self.assertRaises(gfx.GfxRenderError):
                    gfx.decode_canvas_export(bad, 3, 2)
        with self.assertRaises(gfx.GfxRenderError):
            gfx.decode_canvas_export(url(png), 2, 3)


class _FakeTab:
    """按 mode 伪造 CDP tab，驱动 render_page 走不同导出分支（不依赖 Chrome）：
    override  — 截图带真 alpha（flatten 探针判 not flat → 走 override 快路径）
    twoshot   — 截图全不透明但白黑注入有差异（flatten 守卫回落白黑两截）
    canvas    — window.__vtx_export_png() 返回合法画布 dataURL
    canvas_bad— typeof 为 function 但载荷非法 → 回落两截并计 canvas_fallback
    fail      — canvas 页形态 + 截图必抛错（canvas 页 setup 零截图 → 首截即
                worker 帧内失败）→ 全帧重试耗尽计 failed
    navfree   — 有 __vtx_render（资格判定过）：navfree 截图路径
    navfree_eval_fail — 资格过但页内 __vtx_render 抛错（哨兵 0）→ 逐帧转回导航
    spans     — 有 __vtx_render + browser.spans 声明（静态跨度去重）
    """
    def __init__(self, browser, mode):
        self.browser, self.mode, self.bg = browser, mode, 'white'

    def call(self, method, timeout=30, **params):
        return {}

    def close(self):
        # 3c415b2 起 render_page finally 段显式 t.close() 释放 tab CDP fd——桩随迁(记录型,供断言扩展)
        self.closed = True

    def navigate(self, url, timeout=5.0):
        self.browser.navs += 1

    def eval(self, expression):
        # 加速特性标记必须先于 __vtx_export_png 系列判断
        if 'typeof window.__vtx_render' in expression:      # navfree 资格判定
            self.browser.elig_evals += 1
            return self.mode in ('navfree', 'navfree_eval_fail', 'spans')
        if '__vtx_static_spans' in expression:              # 静态跨度声明读取
            return self.browser.spans
        if 'catch(e){return 0}' in expression:              # navfree 安全 eval 哨兵
            return 0 if self.mode == 'navfree_eval_fail' else 1
        if '__vtx_export_png()' in expression:
            if self.mode == 'canvas':
                import numpy as np
                arr = np.zeros((4, 6, 4), dtype=np.uint8)
                arr[:, :, 3] = 128
                return 'data:image/png;base64,' + base64.b64encode(gfx.png_encode_rgba(arr)).decode()
            return 'invalid'  # canvas_bad / fail：载荷非法触发回落
        if '__vtx_export_png' in expression:
            return self.mode in ('canvas', 'canvas_bad', 'fail')  # typeof 探测
        if '#fff' in expression:
            self.bg = 'white'
        elif '#000' in expression:
            self.bg = 'black'
        return 'ok'

    def shot(self):
        import numpy as np
        self.browser.shots += 1
        if self.mode == 'fail':
            raise RuntimeError('fake shot failure')
        if self.mode in ('navfree', 'navfree_eval_fail', 'spans'):
            v = 200   # 加速特性模式模拟自带不透明背景的页：白黑注入无差异
        else:
            v = 200 if self.bg == 'white' else 10
        arr = np.full((4, 6, 4), v, dtype=np.uint8)
        arr[:, :, 3] = 255
        if self.mode == 'override':
            arr[0, 0, 3] = 128  # 有真 alpha → flatten 探针立刻判 not flat → 走 override
        return gfx.png_encode_rgba(arr)


class _CorruptJsonTab(_FakeTab):
    """canvas 页 + CDP 响应损坏：__vtx_export_png() 的 Runtime.evaluate 响应经
    Tab.call 的 json.loads 抛 JSONDecodeError（ValueError 子类）。setup 的 typeof
    资格 eval 正常返回（canvas_export=True），仅帧内 export eval 损坏——用于验证
    do_frame 的 canvas except (GfxRenderError, TypeError) 不吞 JSONDecodeError，
    异常穿透到 worker 重试路径（B6 语义）。"""

    def call(self, method, timeout=30, **params):
        if params.get("expression") == "window.__vtx_export_png()":
            raise json.JSONDecodeError("Expecting value", doc="", pos=0)
        return {}

    def eval(self, expression):
        # 沿真实 Tab.eval→call 链路；这里 call 直接抛，等价于响应字节损坏
        if 'typeof window.__vtx_export_png' in expression:
            return 'function'                       # 资格判定过：canvas 页
        r = self.call("Runtime.evaluate", expression=expression)
        return r.get("result", {}).get("value")

    def shot(self):
        raise AssertionError('JSONDecodeError 被吞成回落时才会走到截图')


class _FakeBrowser:
    def __init__(self, mode, tabs_n=1, spans=None):
        self.lock = threading.Lock()
        self.generation = 0
        self.mode, self.tabs_n, self.shots = mode, tabs_n, 0
        self.navs = 0          # 导航次数（navfree 每 tab 一次 vs 旧路径逐帧导航）
        self.elig_evals = 0    # navfree 资格判定 eval 次数（env 关闭时必须为 0）
        self.spans = spans     # __vtx_static_spans 声明（None=无声明）

    def start(self):
        pass

    def stop(self):
        pass

    def open_tabs(self):
        cls = _CorruptJsonTab if self.mode == 'canvas_json_err' else _FakeTab
        return [cls(self, self.mode) for _ in range(self.tabs_n)]

    def hard_restart(self):
        self.generation += 1


class GfxStatsMock(unittest.TestCase):
    """mock 级（不依赖 Chrome）：path_counts/timings_ms 字段存在且与实际分支一致。"""

    def _run(self, mode, n=3):
        with tempfile.TemporaryDirectory(prefix='vtx_stats_') as tmp:
            root = Path(tmp)
            page = root / 'p.html'
            page.write_text('<html></html>', encoding='utf-8')
            browser = _FakeBrowser(mode, 1)
            with mock.patch.object(gfx, 'Browser', lambda *a, **k: browser):
                st = gfx.render_page(page, root / 'out', n, chrome_path='fake',
                                     tabs=1, width=6, height=4)
            return st

    def test_stats_fields_backward_compatible(self):
        st = self._run('override')
        for key in ('n', 'rendered', 'failed', 'transparent', 'tabs', 'ms_total',
                    'ms_per_frame', 'canvas_export', 'path_counts', 'timings_ms'):
            self.assertIn(key, st)
        self.assertEqual((st['n'], st['rendered'], st['failed']), (3, 3, []))
        self.assertIsInstance(st['canvas_export'], bool)
        self.assertEqual(set(st['timings_ms']), {'nav', 'export', 'decode', 'write'})
        for s in st['timings_ms'].values():
            self.assertEqual(set(s), {'p50', 'p95', 'sum'})

    def test_path_counts_match_actual_branch(self):
        for mode, expect in [('override', 'override'), ('twoshot', 'twoshot'),
                             ('canvas', 'canvas')]:
            with self.subTest(mode=mode):
                st = self._run(mode)
                pc = st['path_counts']
                self.assertEqual(pc[expect], 3)
                self.assertEqual(pc['failed'], 0)
                self.assertEqual(pc['canvas_fallback'], 0)
                self.assertEqual(sum(v for k, v in pc.items() if k != 'canvas_fallback'), 3)
                for s in st['timings_ms'].values():
                    self.assertGreaterEqual(s['sum'], 0.0)
                    self.assertGreaterEqual(s['p95'], s['p50'])

    def test_canvas_fallback_counted_as_twoshot(self):
        st = self._run('canvas_bad', n=2)
        pc = st['path_counts']
        self.assertEqual((pc['canvas_fallback'], pc['twoshot'], pc['canvas']), (2, 2, 0))
        self.assertEqual(st['canvas_export'], True)

    def test_failed_frames_counted_not_timed(self):
        st = self._run('fail', n=2)
        self.assertEqual((st['rendered'], st['path_counts']['failed']), (0, 2))
        # 失败帧不进耗时样本（避免重试噪声）→ 各分项无样本全 0
        self.assertEqual(st['timings_ms']['nav']['sum'], 0.0)
        self.assertEqual(st['timings_ms']['export']['p50'], 0.0)

    def test_all_cached_zero_stats(self):
        with tempfile.TemporaryDirectory(prefix='vtx_stats_') as tmp:
            root = Path(tmp)
            page = root / 'p.html'
            page.write_text('<html></html>', encoding='utf-8')
            out = root / 'out'
            out.mkdir()
            for i in range(2):
                (out / f'f{i:04d}.png').write_bytes(_cached_png())  # 帧全在 → work 空、早退
            with mock.patch.object(gfx, 'Browser', lambda *a, **k: _FakeBrowser('override', 1)):
                st = gfx.render_page(page, out, 2, chrome_path='fake', tabs=1, width=6, height=4)
        self.assertEqual(st['path_counts'],
                         dict.fromkeys(('override', 'canvas', 'twoshot', 'opaque',
                                        'navfree', 'canvas_fallback', 'static_copied',
                                        'failed'), 0))
        self.assertEqual(st['static_spans'], 0)
        self.assertEqual(st['timings_ms']['nav'], {'p50': 0.0, 'p95': 0.0, 'sum': 0.0})

    def test_timings_summary_math(self):
        s = gfx._timings_summary([0.1, 0.2, 0.3, 0.4])
        self.assertAlmostEqual(s['sum'], 1000.0)
        self.assertAlmostEqual(s['p50'], 250.0)
        self.assertAlmostEqual(s['p95'], 385.0)
        self.assertEqual(gfx._timings_summary([]), {'p50': 0.0, 'p95': 0.0, 'sum': 0.0})


class FramesParamMock(unittest.TestCase):
    """方案 v2 阶段 1 页内分片：render_page frames 参数只做缺失交集；全缓存早退
    不构造 Browser（零 Chrome）。"""

    def _prep(self, tmp, present=()):
        root = Path(tmp)
        page = root / 'p.html'
        page.write_text('<html></html>', encoding='utf-8')
        out = root / 'out'
        out.mkdir()
        for i in present:
            (out / f'f{i:04d}.png').write_bytes(_cached_png())
        return page, out

    def test_frames_intersects_missing(self):
        # frames=[1..5]、其中 1/3 已缓存 → 只渲缺失的 2/4/5，frames 外的 0 不碰
        with tempfile.TemporaryDirectory(prefix='vtx_split_') as tmp:
            page, out = self._prep(tmp, present=(1, 3))
            browser = _FakeBrowser('override', 1)
            with mock.patch.object(gfx, 'Browser', lambda *a, **k: browser):
                st = gfx.render_page(page, out, 6, chrome_path='fake', tabs=1, width=6, height=4,
                                     frames=[1, 2, 3, 4, 5])
            self.assertEqual(st['n'], 6)                       # n 仍=页总帧数
            self.assertEqual(st['rendered'], 3)
            self.assertEqual(st['path_counts']['override'], 3)
            self.assertEqual(st['failed'], [])
            self.assertEqual((out / 'f0001.png').read_bytes(), _cached_png())  # 已存在未被重写
            for i in (2, 4, 5):
                self.assertTrue((out / f'f{i:04d}.png').exists(), i)
            self.assertFalse((out / 'f0000.png').exists())     # frames 之外不渲

    def test_frames_all_cached_early_return_zero_chrome(self):
        # 块内全部帧已缓存 → work 空 → 不构造/启动 Browser 即早退
        with tempfile.TemporaryDirectory(prefix='vtx_split_') as tmp:
            page, out = self._prep(tmp, present=(0, 1))

            def _no_chrome(*a, **k):
                raise AssertionError('全缓存块不得构造 Browser（白起 Chrome）')

            with mock.patch.object(gfx, 'Browser', _no_chrome):
                st = gfx.render_page(page, out, 2, chrome_path='fake', tabs=1, width=6, height=4,
                                     frames=[0, 1])
            self.assertEqual((st['rendered'], st['failed']), (0, []))
            self.assertEqual(st['ms_total'], 0.0)
            self.assertEqual(st['path_counts']['override'], 0)

    def test_frames_empty_list_is_noop(self):
        with tempfile.TemporaryDirectory(prefix='vtx_split_') as tmp:
            page, out = self._prep(tmp)
            with mock.patch.object(gfx, 'Browser',
                                   lambda *a, **k: (_ for _ in ()).throw(AssertionError('no chrome'))):
                st = gfx.render_page(page, out, 3, chrome_path='fake', tabs=1, width=6, height=4,
                                     frames=[])
            self.assertEqual(st['rendered'], 0)
            self.assertFalse(list(out.glob('f*.png')))

    def test_frames_none_keeps_whole_page_semantics(self):
        # frames=None（默认）行为不变：扫全页缺失帧
        with tempfile.TemporaryDirectory(prefix='vtx_split_') as tmp:
            page, out = self._prep(tmp, present=(1,))
            browser = _FakeBrowser('override', 1)
            with mock.patch.object(gfx, 'Browser', lambda *a, **k: browser):
                st = gfx.render_page(page, out, 3, chrome_path='fake', tabs=1, width=6, height=4)
            self.assertEqual(st['rendered'], 2)
            self.assertTrue((out / 'f0000.png').exists())
            self.assertTrue((out / 'f0002.png').exists())


class RenderedViaFormat(unittest.TestCase):
    """B7：_cdp_via 纯格式化单测——路径计数键序固定零值也显示、分项 p50 拼接、
    cli_fallback 追加；只读 stats 字段，不做字符串状态判断。"""

    @staticmethod
    def _stats(**pc_over):
        pc = dict.fromkeys(("override", "canvas", "twoshot", "opaque",
                            "canvas_fallback", "failed"), 0) | pc_over
        tm = {c: {"p50": float(i), "p95": 0.0, "sum": 0.0}
              for i, c in enumerate(("nav", "export", "decode", "write"))}
        return {"tabs": 4, "ms_per_frame": 100.0, "path_counts": pc, "timings_ms": tm}

    def test_zero_keys_still_displayed_for_grep_alignment(self):
        line = vtx._cdp_via(self._stats(override=3))
        self.assertIn("override×3", line)
        for k in ("canvas×0", "twoshot×0", "opaque×0", "canvas_fallback×0", "failed×0"):
            self.assertIn(k, line)
        self.assertIn("4 tabs CDP", line)
        self.assertIn("100ms/帧", line)

    def test_cli_fallback_appended_when_present(self):
        st = self._stats(override=1, failed=2)
        st["path_counts"]["cli_fallback"] = 2
        self.assertIn("cli_fallback×2", vtx._cdp_via(st))
        self.assertNotIn("cli_fallback", vtx._cdp_via(self._stats(override=1)))

    def test_timing_categories_joined(self):
        line = vtx._cdp_via(self._stats())
        for c in ("nav", "export", "decode", "write"):
            self.assertIn(c + " ", line)

    def test_batch_keys_displayed(self):
        # v2 阶段 2 新键：navfree/static_copied 进打印行，span×K 追加
        st = self._stats(navfree=5, static_copied=2)
        st["static_spans"] = 3
        line = vtx._cdp_via(st)
        for frag in ("navfree×5", "static_copied×2", "span×3"):
            self.assertIn(frag, line)
        self.assertNotIn("span×", vtx._cdp_via(self._stats(override=1)))


class NavfreeMock(unittest.TestCase):
    """特性 1 nav-free 截图（mock 级）：资格判定三分支、每 tab 一次导航、
    页内执行失败逐帧转回导航路径、env 关闭红线。"""

    def _run(self, mode, n, env=None, spans=None):
        env = env or {}
        with tempfile.TemporaryDirectory(prefix='vtx_navfree_') as tmp:
            root = Path(tmp)
            page = root / 'p.html'
            page.write_text('<html></html>', encoding='utf-8')
            browser = _FakeBrowser(mode, 1, spans=spans)
            with mock.patch.dict(os.environ, env):
                with mock.patch.object(gfx, 'Browser', lambda *a, **k: browser):
                    st = gfx.render_page(page, root / 'out', n, chrome_path='fake',
                                         tabs=1, width=6, height=4)
            return st, browser

    def test_navfree_happy_path(self):
        # 资格-正常：navfree×N 打标；导航次数 = setup 1 + flatten 守卫探针 2 +
        # navfree 每 tab 1（免掉 N-1 次逐帧导航）
        st, browser = self._run('navfree', 5, env={'VTX_GFX_NAVFREE': '1'})
        pc = st['path_counts']
        self.assertEqual((pc['navfree'], pc['override'], pc['failed']), (5, 0, 0))
        self.assertEqual(browser.navs, 1 + 2 + 1)
        self.assertEqual(st['failed'], [])

    def test_navfree_env_off_is_red_line(self):
        # 红线：NAVFREE=0（默认）→ 零资格判定 eval、逐帧导航、无 navfree 计数
        st, browser = self._run('navfree', 3, env={'VTX_GFX_NAVFREE': '0'})
        pc = st['path_counts']
        self.assertEqual((pc['override'], pc['navfree']), (3, 0))
        self.assertEqual(browser.elig_evals, 0)
        self.assertEqual(browser.navs, 1 + 2 + 3)  # setup 1 + flatten 2 + 每帧 3

    def test_eligibility_no_render_function_untouched(self):
        # 资格-无函数：判定 eval 照做但不启用，行为同旧路径（逐帧导航）
        st, browser = self._run('override', 3, env={'VTX_GFX_NAVFREE': '1'})
        pc = st['path_counts']
        self.assertEqual((pc['override'], pc['navfree']), (3, 0))
        self.assertEqual(browser.elig_evals, 1)
        self.assertEqual(browser.navs, 1 + 1 + 3)  # setup 1 + 首个 flatten 探针即破 1 + 每帧 3

    def test_navfree_eval_fail_falls_back_to_nav(self):
        # 页内 __vtx_render 抛错（哨兵 0）→ 该帧立即转回导航路径，绝不静默截错帧
        st, browser = self._run('navfree_eval_fail', 3, env={'VTX_GFX_NAVFREE': '1'})
        pc = st['path_counts']
        self.assertEqual((pc['navfree'], pc['override'], pc['failed']), (0, 3, 0))
        self.assertEqual(st['failed'], [])
        self.assertEqual(browser.navs, 1 + 2 + 1 + 3)  # + navfree 首导航 + 3 帧回落导航


class StaticSpanMock(unittest.TestCase):
    """特性 2 静态跨度去重（mock 级）：与分片 frames 参数组合的四种情形 +
    声明解析失败惰性降级 + plan_static_spans 纯函数不变量。"""

    def _run(self, n, frames=None, spans=None, present=(), env=None):
        env = env or {}
        with tempfile.TemporaryDirectory(prefix='vtx_span_') as tmp:
            root = Path(tmp)
            page = root / 'p.html'
            page.write_text('<html></html>', encoding='utf-8')
            out = root / 'out'
            out.mkdir()
            for i in present:
                (out / f'f{i:04d}.png').write_bytes(_cached_png(i))
            browser = _FakeBrowser('spans', 1, spans=spans)
            with mock.patch.dict(os.environ, env):
                with mock.patch.object(gfx, 'Browser', lambda *a, **k: browser):
                    st = gfx.render_page(page, out, n, chrome_path='fake', tabs=1,
                                         width=6, height=4, frames=frames)
            files = {p.name: p.read_bytes() for p in out.glob('f*.png')}
            return st, files

    def test_head_cached_members_copied(self):
        # 情形·头已缓存：跨度 [2,5) 头帧 f0002 已存在 → 缺失成员 3/4 立即字节复制，
        # 复制帧与头帧逐字节相同；渲染集只剩 {0,1,5}
        st, files = self._run(6, spans=[[2, 5]], present=(2,))
        self.assertEqual((st['rendered'], st['failed']), (3, []))
        self.assertEqual(st['path_counts']['static_copied'], 2)
        self.assertEqual(st['static_spans'], 1)
        self.assertIn('f0002.png', files)
        self.assertEqual(files['f0003.png'], files['f0002.png'])
        self.assertEqual(files['f0004.png'], files['f0002.png'])
        # work = 全部缺失帧 - 被复制成员 = {0,1,5}，已渲 3 帧
        for i in (0, 1, 5):
            self.assertIn(f'f{i:04d}.png', files)

    def test_head_missing_in_this_call(self):
        # 情形·头缺失且在本调用范围：头照常渲染，成员渲染后字节复制头帧
        st, files = self._run(6, spans=[[2, 5]])
        self.assertEqual((st['rendered'], st['failed']), (4, []))
        self.assertEqual(st['path_counts']['static_copied'], 2)
        self.assertEqual(files['f0003.png'], files['f0002.png'])
        self.assertEqual(files['f0004.png'], files['f0002.png'])
        self.assertEqual(len(files), 6)   # 全部帧落盘（0,1,2,5 渲染 + 3,4 复制）

    def test_head_outside_frames_param_merged_in(self):
        # 情形·头不在本调用 frames 参数（跨分片块）：头并入本调用 work 渲染，
        # 成员复制；frames 外且非成员的帧（f0001）不碰。渲染集 = {头2, 5}
        st, files = self._run(6, frames=[3, 4, 5], spans=[[2, 5]])
        self.assertEqual((st['rendered'], st['failed']), (2, []))
        self.assertEqual(st['path_counts']['static_copied'], 2)
        self.assertEqual(files['f0003.png'], files['f0002.png'])
        self.assertEqual(files['f0004.png'], files['f0002.png'])
        self.assertNotIn('f0001.png', files)   # frames 参数外不渲

    def test_members_partially_cached_untouched(self):
        # 情形·成员部分缓存：已存在成员（f0003）原样保留不覆盖，缺失成员复制
        st, files = self._run(6, spans=[[2, 5]], present=(2, 3))
        self.assertEqual(files['f0003.png'], _cached_png(3))      # 已存在不动
        self.assertEqual(files['f0004.png'], files['f0002.png'])    # 缺失成员复制
        self.assertEqual(st['path_counts']['static_copied'], 1)
        self.assertEqual(st['static_spans'], 1)

    def test_bad_declaration_is_lazy_noop(self):
        # 声明解析失败 → warning + 无缩减原路径（像素/帧集与无声明完全一致）
        st, files = self._run(4, spans="garbage")
        self.assertEqual((st['rendered'], st['failed']), (4, []))
        self.assertEqual(st['path_counts']['static_copied'], 0)
        self.assertEqual(st['static_spans'], 0)
        # env 关闭同理：零影响
        st2, files2 = self._run(4, spans=[[1, 3]], env={'VTX_GFX_STATIC': '0'})
        self.assertEqual(st2['path_counts']['static_copied'], 0)
        self.assertEqual(st2['static_spans'], 0)
        self.assertEqual(len(files2), 4)

    def test_all_missing_frames_are_copies_zero_render(self):
        # 缺失帧全是已缓存头帧的跨度成员 → 复制后 work 清空，rendered=0 也不丢帧
        st, files = self._run(4, frames=[2, 3], spans=[[2, 4]], present=(2,))
        self.assertEqual((st['rendered'], st['failed']), (0, []))
        self.assertEqual(st['path_counts']['static_copied'], 1)
        self.assertEqual(files['f0003.png'], files['f0002.png'])

    def test_plan_static_spans_invariants(self):
        # 纯函数不变量：work = (want - 被复制成员) ∪ 缺失头；成员只取 want；
        # 重叠声明首占
        import tempfile as _tf
        with _tf.TemporaryDirectory(prefix='vtx_plan_') as tmp:
            out = Path(tmp)
            (out / 'f0002.png').write_bytes(_cached_png())     # 头2已缓存
            spans = [(2, 5), (3, 6)]                  # 重叠：4 被第一个跨度认领
            work, copy_now, deferred, n_spans = gfx.plan_static_spans(
                spans, [3, 4, 9], str(out))
            self.assertEqual(work, [9])               # 3/4 被复制退出渲染集，头2已缓存
            self.assertEqual(sorted(m for m, _ in copy_now), [3, 4])
            self.assertEqual(deferred, [])
            self.assertEqual(n_spans, 1)
            # 头缺失且不在 want → 并入 work；被复制成员同样退出 want
            (out / 'f0002.png').unlink()
            work2, _cn, deferred2, n2 = gfx.plan_static_spans(spans, [3, 4], str(out))
            self.assertEqual(work2, [2])
            self.assertEqual([(h, sorted(ms)) for h, ms in deferred2], [(2, [3, 4])])
            self.assertEqual(n2, 1)


class AtomicWriteAndResilienceMock(unittest.TestCase):
    """m3 健壮性三测（mock 级）：
    ① CDP 响应损坏 → Tab.call json.loads 抛 JSONDecodeError（ValueError 子类），
       断言不被 canvas except 吞掉、走 worker 重试路径（B6 语义回归锁定）；
    ② 写盘失败重试不双计：os.replace 首两次注错 → path_counts 只计最终成功那次
       的路径标签、failed 不误增（B5 语义）；
    ③ M1 帧落盘原子性：同一路径并发双写（一好一坏字节），最终文件恒为二者之一
       的完整字节（tmp+os.replace 生效，无 truncate/write 交错混合），且 tmp 无残留。"""

    def test_corrupt_cdp_json_not_swallowed_by_canvas_except(self):
        with tempfile.TemporaryDirectory(prefix='vtx_jsonerr_') as tmp:
            root = Path(tmp)
            page = root / 'p.html'
            page.write_text('<html></html>', encoding='utf-8')
            browser = _FakeBrowser('canvas_json_err', 1)
            with mock.patch.object(gfx, 'Browser', lambda *a, **k: browser):
                st = gfx.render_page(page, root / 'out', 2, chrome_path='fake',
                                     tabs=1, width=6, height=4)
            self.assertEqual(st['canvas_export'], True)  # canvas 页：export eval 触发损坏响应
            self.assertEqual(st['failed'], [0, 1])       # 异常穿透 → 重试耗尽进 failed
            self.assertEqual(st['rendered'], 0)
            pc = st['path_counts']
            self.assertEqual(pc['failed'], 2)            # 每帧只计一次失败（非每次尝试）
            self.assertEqual(pc['canvas_fallback'], 0)   # 未被误吞成「载荷失效」回落
            self.assertEqual(pc['twoshot'], 0)           # 也未回落截图路径静默出帧

    def test_write_failure_retry_not_double_counted(self):
        with tempfile.TemporaryDirectory(prefix='vtx_wfail_') as tmp:
            root = Path(tmp)
            page = root / 'p.html'
            page.write_text('<html></html>', encoding='utf-8')
            real_replace, calls = os.replace, [0]

            def flaky_replace(src, dst, **kwargs):
                # fstxn 的 dir_fd 原子替换带 src_dir_fd/dst_dir_fd 关键字（#59）
                calls[0] += 1
                if calls[0] <= 2:                        # 帧 0 前两个尝试注错
                    raise OSError(16, 'injected transient write failure')
                return real_replace(src, dst, **kwargs)

            browser = _FakeBrowser('override', 1)
            with mock.patch.object(os, 'replace', flaky_replace), \
                    mock.patch.object(gfx, 'Browser', lambda *a, **k: browser):
                st = gfx.render_page(page, root / 'out', 3, chrome_path='fake',
                                     tabs=1, width=6, height=4)
            self.assertEqual(calls[0], 3 + 2)            # 2 次注错 + 每帧恰 1 次成功替换
            self.assertEqual((st['rendered'], st['failed']), (3, []))
            pc = st['path_counts']
            self.assertEqual((pc['override'], pc['failed']), (3, 0))  # 只计最终成功那次
            self.assertEqual((root / 'out' / 'f0000.png').read_bytes()[:8],
                             b'\x89PNG\r\n\x1a\n')       # 落盘为完整真实 PNG

    def test_atomic_write_concurrent_no_torn_file(self):
        # 同帧并发双写（同页 split×k 双块并发渲同一跨度头帧的等价场景）
        import numpy as np
        good = gfx.png_encode_rgba(np.zeros((4, 6, 4), dtype=np.uint8))
        bad = b'CORRUPT-BYTES-' * 300                     # 与 good 长度不同，混合必可检出
        with tempfile.TemporaryDirectory(prefix='vtx_atomic_') as tmp:
            target = Path(tmp) / 'f0000.png'
            def write_pair():
                target.unlink(missing_ok=True)  # 每轮必须有新产物，不能复用上轮字节。
                barrier = threading.Barrier(2)

                def write(payload):
                    barrier.wait(timeout=10)
                    gfx._atomic_write_bytes(str(target), payload)

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(write, payload) for payload in (good, bad)]
                    for future in futures:
                        future.result()  # 任一线程异常必须在测试线程中失败。

            for _ in range(64):
                write_pair()
                self.assertIn(target.read_bytes(), (good, bad))
            real_write = gfx._atomic_write_bytes
            for rejected in (good, bad):
                def fail_one(path, payload):
                    if payload == rejected:
                        raise OSError('injected writer failure')
                    return real_write(path, payload)
                with self.subTest(rejected_bytes=len(rejected)), \
                        mock.patch.object(gfx, '_atomic_write_bytes', side_effect=fail_one), \
                        self.assertRaisesRegex(OSError, 'injected writer failure'):
                    write_pair()
            self.assertEqual(list(Path(tmp).glob('.*.tmp*')), [])  # 成功路径不留 tmp

    def test_atomic_write_failure_cleans_tmp_keeps_target(self):
        # 异常路径：replace 抛错 → OSError 穿透、目标文件保持旧完整内容、tmp 被清理
        import numpy as np
        good = gfx.png_encode_rgba(np.zeros((4, 6, 4), dtype=np.uint8))
        with tempfile.TemporaryDirectory(prefix='vtx_atomic_err_') as tmp:
            target = Path(tmp) / 'f0001.png'
            target.write_bytes(good)
            with mock.patch.object(os, 'replace', side_effect=OSError(5, 'injected')):
                with self.assertRaises(OSError):
                    gfx._atomic_write_bytes(str(target), b'torn-partial')
            self.assertEqual(target.read_bytes(), good)          # 目标未被半写破坏
            self.assertEqual(list(Path(tmp).glob('.*.tmp*')), [])


class VtxSplitMath(unittest.TestCase):
    """vtx 页内分片：切块数学（阈值/上限/关闭态）与多块 stats 聚合。"""

    def test_merge_cdp_stats_sums_batch_keys(self):
        # v2 阶段 2：navfree/static_copied 路径计数逐块求和，static_spans 取 max
        # （跨块跨度求和会重复计数）
        def block(rendered, navfree, copied, spans):
            return {"rendered": rendered, "failed": [], "tabs": 4,
                    "ms_total": 10000.0, "static_spans": spans,
                    "path_counts": dict.fromkeys(
                        ("override", "canvas", "twoshot", "opaque", "navfree",
                         "canvas_fallback", "static_copied", "failed"), 0)
                    | {"navfree": navfree, "static_copied": copied},
                    "timings_ms": {"nav": {"p50": 0.0, "p95": 0.0, "sum": 0.0},
                                   "export": {"p50": 0.0, "p95": 0.0, "sum": 0.0},
                                   "decode": {"p50": 0.0, "p95": 0.0, "sum": 0.0},
                                   "write": {"p50": 0.0, "p95": 0.0, "sum": 0.0}}}
        m = vtx._merge_cdp_stats([block(100, 100, 5, 2), block(50, 30, 4, 7)])
        self.assertEqual(m['path_counts']['navfree'], 130)
        self.assertEqual(m['path_counts']['static_copied'], 9)
        self.assertEqual(m['static_spans'], 7)
        via = vtx._cdp_via(m)
        self.assertIn('navfree×130', via)
        self.assertIn('static_copied×9', via)
        self.assertIn('span×7', via)

    def test_split_page_disabled_and_small(self):
        self.assertIsNone(vtx._split_page(1153, 0, 3))    # SPLIT_FRAMES=0 关闭态
        self.assertIsNone(vtx._split_page(600, 600, 3))   # 不大于阈值不拆
        self.assertIsNone(vtx._split_page(100, 600, 3))
        self.assertIsNone(vtx._split_page(601, 600, 0))   # 阈值≤0 或上限<1 一律关

    def test_split_page_ceil_and_contiguous(self):
        self.assertEqual(vtx._split_page(601, 600, 3), [301, 300])    # ceil=2
        self.assertEqual(vtx._split_page(1153, 600, 3), [577, 576])   # 基准最大页
        self.assertEqual(vtx._split_page(1200, 600, 3), [600, 600])   # 整除

    def test_split_page_max_cap(self):
        self.assertEqual(vtx._split_page(3000, 600, 3), [1000, 1000, 1000])  # ceil=5→截3
        self.assertEqual(vtx._split_page(5000, 600, 4), [1250] * 4)          # ceil=9→截4
        self.assertEqual(vtx._split_page(12000, 600, 2), [6000, 6000])

    def test_split_page_invariants(self):
        for n in (601, 1153, 2000, 3000, 4321, 12024):
            for sf in (600, 1000):
                sizes = vtx._split_page(n, sf, 3)
                if sizes is None:
                    self.assertLessEqual(n, sf)
                    continue
                self.assertEqual(sum(sizes), n)
                self.assertLessEqual(len(sizes), 3)
                self.assertTrue(all(s > 0 for s in sizes))
                self.assertTrue(all(b - a <= 1 for a, b in zip(sizes, sizes[1:])))

    def test_merge_cdp_stats_sums_and_weights(self):
        def block(rendered, ms_total, p50):
            return {"rendered": rendered, "failed": [], "tabs": 4,
                    "ms_total": ms_total,
                    "path_counts": dict.fromkeys(
                        ("override", "canvas", "twoshot", "opaque",
                         "canvas_fallback", "failed"), 0)
                    | {"override": rendered},
                    "timings_ms": {"nav": {"p50": p50, "p95": 0.0, "sum": 0.0},
                                   "export": {"p50": p50, "p95": 0.0, "sum": 0.0},
                                   "decode": {"p50": 0.0, "p95": 0.0, "sum": 0.0},
                                   "write": {"p50": 0.0, "p95": 0.0, "sum": 0.0}}}
        m = vtx._merge_cdp_stats([block(100, 20000.0, 10.0), block(300, 30000.0, 30.0)])
        self.assertAlmostEqual(m['ms_per_frame'], 125.0)      # Σms/Σ帧 = 50000/400
        self.assertEqual(m['path_counts']['override'], 400)   # 路径计数求和
        self.assertAlmostEqual(m['timings_ms']['nav']['p50'], 25.0)  # 帧数加权
        via = vtx._cdp_via(m)                                 # 可直接进 _cdp_via 格式化
        self.assertIn('override×400', via)
        self.assertIn('125ms/帧', via)
        self.assertIsNone(vtx._merge_cdp_stats([]))


@unittest.skipUnless(os.environ.get('VTX_TEST_CHROME'), 'set VTX_TEST_CHROME')
class BrowserIntegration(unittest.TestCase):
    def test_canvas_html_and_invalid_export(self):
        import numpy as np
        with tempfile.TemporaryDirectory(prefix="vtx 中文 # space '") as tmp:
            root = Path(tmp)
            html = '''<html style="background:transparent"><body style="margin:0;background:transparent">
<canvas width="1920" height="1080" id="c"></canvas><script>
const c=document.getElementById('c'),x=c.getContext('2d');
const f=Number(new URLSearchParams(location.search).get('f'));
x.fillStyle=f?'rgba(0,255,0,0.5)':'rgba(255,0,0,0.5)';x.fillRect(100,100,200,200);
EXPORT
</script></body></html>'''
            frames = {}
            for kind, export in [('canvas', "window.__vtx_export_png=()=>c.toDataURL('image/png');"),
                                 ('html',''), ('invalid', "window.__vtx_export_png=()=> 'invalid';")]:
                page=root/(kind+'.html');page.write_text(html.replace('EXPORT',export),encoding='utf-8')
                out=root/kind
                result=gfx.render_page(page,out,2,chrome_path=os.environ['VTX_TEST_CHROME'],tabs=1)
                self.assertEqual(result['failed'], [])
                arr=[gfx.png_decode_rgba((out/f'f{i:04d}.png').read_bytes()) for i in range(2)]
                self.assertTrue(arr[0][150,150,0]>240)
                self.assertTrue(arr[1][150,150,1]>240)
                self.assertTrue(125<=arr[0][150,150,3]<=130)
                self.assertEqual(int(arr[0][500,500,3]),0)
                frames[kind]=arr
            for kind in ['html','invalid']:
                for i in range(2):
                    for background in [0,255]:
                        def composite(a):
                            a=a.astype(float);alpha=a[:,:,3:4]/255
                            return a[:,:,:3]*alpha+background*(1-alpha)
                        self.assertLess(np.abs(composite(frames[kind][i])-composite(frames['canvas'][i])).mean(),.1)

    def test_path_counts_and_timings(self):
        # 观测字段真实链路：透明页（override 或两截）与 canvas 载荷失效回落两截
        with tempfile.TemporaryDirectory(prefix="vtx_stats_real_") as tmp:
            root = Path(tmp)
            base = ('<html style="background:transparent"><body style="margin:0;'
                    'background:transparent">EXPORT</body></html>')
            pages = {'shot': base.replace('EXPORT', ''),
                     'invalid': base.replace(
                         'EXPORT', "<script>window.__vtx_export_png=()=>'invalid';</script>")}
            for kind, text in pages.items():
                page = root / (kind + '.html')
                page.write_text(text, encoding='utf-8')
                st = gfx.render_page(page, root / kind, 2,
                                     chrome_path=os.environ['VTX_TEST_CHROME'], tabs=1)
                self.assertEqual(st['failed'], [])
                pc = st['path_counts']
                if kind == 'invalid':
                    self.assertEqual((pc['canvas_fallback'], pc['twoshot']), (2, 2))
                else:
                    self.assertEqual(pc['override'] + pc['twoshot'] + pc['opaque'], 2)
                for s in st['timings_ms'].values():
                    self.assertGreaterEqual(s['p95'], s['p50'])
                    self.assertGreaterEqual(s['sum'], 0.0)


def _toy_gfx_page(render_body="window.__vtx_render=function(f){"
                  "const t=f/30;"
                  "document.getElementById('bar').style.transform='translateY('+(f*3%900)+'px)';"
                  "document.getElementById('chip').style.transform='translate('+(200+300*Math.sin(t))+'px,'+(150+250*Math.cos(t))+'px) rotate('+(f*2%360)+'deg)';"
                  "};",
                  static_spans=None):
    """toy gfx 页（1920×1080，无文本/无 webfont/无外链图）：__vtx_render 纯函数 +
    ?f= 加载路径同语义，navfree 与逐帧导航两通道可比。页形貌遵循 gfx 页协议：
    body 透明 + 内部满幅 #bg 元素承担背景（背景写在 html/body 会传播到根画布，
    被透明背景 override 打穿，两通道就没有可比性了）。"""
    spans_js = '' if static_spans is None else \
        f'window.__vtx_static_spans={json.dumps(static_spans)};'
    return f'''<!doctype html><html style="background:transparent"><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1920px;height:1080px;overflow:hidden;background:transparent}}
#bg{{position:absolute;inset:0;background:rgb(245,246,248)}}
#bar{{position:absolute;left:0;top:0;width:1920px;height:120px;background:#1f6feb}}
#chip{{position:absolute;left:0;top:0;width:340px;height:140px;background:#d29922}}
</style></head><body>
<div id="bg"></div><div id="bar"></div><div id="chip"></div>
<script>{render_body}
{spans_js}
const q=Number(new URLSearchParams(location.search).get('f')||'0');
__vtx_render(q);
</script></body></html>'''


@unittest.skipUnless(os.environ.get('VTX_TEST_CHROME'), 'set VTX_TEST_CHROME')
class NavfreeSpanIntegration(unittest.TestCase):
    """方案 v2 阶段 2（转向后）真 Chrome 集成：navfree vs 逐帧导航逐像素一致；
    静态跨度成员帧字节全同；无声明页行为不变；__vtx_render 抛错页回落导航。"""

    def _render(self, root, name, html, n, navfree):
        page = root / (name + '.html')
        page.write_text(html, encoding='utf-8')
        env = {'VTX_GFX_NAVFREE': '1' if navfree else '0'}
        with mock.patch.dict(os.environ, env):
            st = gfx.render_page(page, root / ('out_' + name), n, tabs=2,
                                 chrome_path=os.environ['VTX_TEST_CHROME'])
        out = root / ('out_' + name)
        return st, out, [out / f'f{i:04d}.png' for i in range(n)]

    def test_navfree_vs_navigate_pixel_identical(self):
        import numpy as np
        with tempfile.TemporaryDirectory(prefix="vtx_navfree_real_") as tmp:
            root = Path(tmp)
            html = _toy_gfx_page()
            st_nf, _, paths_nf = self._render(root, 'navfree', html, 5, navfree=True)
            st_nav, _, paths_nav = self._render(root, 'nav', html, 5, navfree=False)
            # navfree 证据：全部帧打 navfree 标、无失败
            self.assertEqual(st_nf['failed'], [])
            self.assertEqual(st_nf['path_counts']['navfree'], 5)
            self.assertEqual(st_nav['path_counts']['navfree'], 0)
            # 核心断言：两通道逐像素一致（JS 突变后 captureScreenshot 出当前帧）
            for i in range(5):
                a = gfx.png_decode_rgba(paths_nf[i].read_bytes())
                b = gfx.png_decode_rgba(paths_nav[i].read_bytes())
                self.assertTrue(np.array_equal(a, b), f'帧 {i} navfree 与导航像素不一致')

    def test_static_spans_copied_frames_byte_identical(self):
        with tempfile.TemporaryDirectory(prefix="vtx_span_real_") as tmp:
            root = Path(tmp)
            # 帧态在 [2,5) 恒定（截断 500px），f≥5 变 800px → 声明区间外有差异
            html = _toy_gfx_page(
                render_body="window.__vtx_render=function(f){"
                            "var y=f<2?f*250:(f<5?500:800);"
                            "document.getElementById('bar').style.transform="
                            "'translateY('+y+'px)';};",
                static_spans=[[2, 5]])
            st, out, paths = self._render(root, 'span', html, 6, navfree=False)
            self.assertEqual(st['failed'], [])
            self.assertEqual(st['static_spans'], 1)
            self.assertEqual(st['path_counts']['static_copied'], 2)  # 成员 3/4 复制
            self.assertEqual(st['rendered'], 4)                      # 0,1,2,5 渲染
            h2 = paths[2].read_bytes()
            self.assertEqual(paths[3].read_bytes(), h2)   # 复制帧与头帧逐字节相同
            self.assertEqual(paths[4].read_bytes(), h2)
            self.assertNotEqual(paths[5].read_bytes(), h2)  # 非成员帧独立渲染

    def test_no_declaration_page_unchanged(self):
        import numpy as np
        with tempfile.TemporaryDirectory(prefix="vtx_nodecl_real_") as tmp:
            root = Path(tmp)
            html = _toy_gfx_page()
            st, _, paths = self._render(root, 'plain', html, 3, navfree=False)
            self.assertEqual(st['failed'], [])
            self.assertEqual(st['static_spans'], 0)
            self.assertEqual(st['path_counts']['static_copied'], 0)
            self.assertEqual(st['path_counts']['navfree'], 0)
            for p in paths:
                arr = gfx.png_decode_rgba(p.read_bytes())
                self.assertEqual(arr.shape, (1080, 1920, 4))
                self.assertTrue((arr[:, :, 3] == 255).all())   # 不透明页 alpha 完整

    def test_throwing_render_falls_back_to_nav(self):
        with tempfile.TemporaryDirectory(prefix="vtx_throw_real_") as tmp:
            root = Path(tmp)
            html = _toy_gfx_page(
                render_body="window.__vtx_render=function(f){"
                            "if(f>0){throw new Error('boom');}};")
            st, _, paths = self._render(root, 'throw', html, 3, navfree=True)
            # 帧 0 页内执行成功走 navfree；f>0 抛错（哨兵 0）→ 立即转回导航路径
            self.assertEqual(st['failed'], [])
            self.assertEqual(st['path_counts']['navfree'], 1)
            self.assertEqual(st['path_counts']['override'], 2)
            self.assertEqual(st['rendered'], 3)
            for p in paths:
                self.assertEqual(gfx.png_decode_rgba(p.read_bytes()).shape,
                                 (1080, 1920, 4))


@unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'), 'set VTX_TEST_FFMPEG')
class SubtitleIntegration(unittest.TestCase):
    def test_special_character_filename(self):
        with tempfile.TemporaryDirectory(prefix="vtx 中文 #,[]; '") as tmp:
            p=Path(tmp)/"sub 'x,[a];.srt"
            p.write_text('1\n00:00:00,000 --> 00:00:01,000\nTEST\n',encoding='utf-8')
            cmd=[os.environ['VTX_TEST_FFMPEG'],'-v','error','-f','lavfi','-i','color=black:s=320x180:d=1',
                 '-vf','subtitles=filename='+infra.filter_filename(p),'-frames:v','1',
                 '-f','rawvideo','-pix_fmt','gray','-']
            result=subprocess.run(cmd,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            self.assertEqual(len(result.stdout),320*180)
            self.assertGreater(max(result.stdout),150)  # Text actually burned into the frame.

if __name__=='__main__':unittest.main()
