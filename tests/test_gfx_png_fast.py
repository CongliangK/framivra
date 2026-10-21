"""Explicit CDP PNG compression contracts. CPU/mock only, no browser launch."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from vtx import cli, gfx_backend, gfx_cli, gfx_environment, gfx_render, hygiene
from vtx.infra import Ctx
from tests.gfx_environment_fixture import bind_environment
from tests.test_gfx_cache_integrity import png


def protocol(parameter=None):
    return json.dumps({'domains': [{'domain': 'Page', 'commands': [
        {'name': 'captureScreenshot', 'parameters': [parameter or {
            'name': 'optimizeForSpeed', 'type': 'boolean', 'optional': True}]}]}]}).encode()


class FakeTab(gfx_render.Tab):
    """Uses the real shot method; only transport/page operations are synthetic."""
    canvas = False
    spans = None
    override = True
    fail_shot = None
    calls = []

    def __init__(self, port, path, width, height, *, png_fast=False):
        self.png_fast = png_fast
        self.capture_calls = {'succeeded': 0, 'failed': 0}
        self.shots = 0
        self.request_policy = None
        self.width, self.height = width, height

    def call(self, method, **kwargs):
        type(self).calls.append((method, kwargs))
        if method == 'Page.captureScreenshot':
            self.shots += 1
            if self.shots == self.fail_shot:
                raise RuntimeError('synthetic capture failure')
            return {'data': base64.b64encode(png(self.width, self.height, 16)).decode()}
        if method == 'Emulation.setDefaultBackgroundColorOverride' and not self.override:
            raise RuntimeError('synthetic missing override')
        if method == 'Runtime.evaluate':
            return {'result': {'value': True}}
        return {}

    def navigate(self, url):
        pass

    def eval(self, expression):
        if expression.startswith('typeof window.__vtx_export_png'):
            return bool(self.canvas)
        if expression == 'window.__vtx_static_spans || null':
            return self.spans
        if expression.startswith('typeof window.__vtx_render') or '__vtx_render(' in expression:
            return True
        if expression == 'window.__vtx_export_png()':
            if self.canvas == 'invalid':
                return 'invalid'
            return 'data:image/png;base64,' + base64.b64encode(png(6, 4, 16)).decode()
        return None

    def close(self):
        pass


class FakeBrowser(gfx_render.Browser):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).instances.append(self)
        self.stopped = False

    def start(self):
        self.port = 12345
        if self.png_fast:
            self._check_png_fast_protocol()
        self.tab_ws_urls = ['/fake'] * self.tabs_n

    def stop(self):
        self.stopped = True


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='vtx_png_fast_contract_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.html = self.root / 'page.html'
        self.html.write_text('<html><body>fixture</body></html>')
        self.out = self.root / 'frames'
        FakeTab.calls = []
        FakeBrowser.instances = []
        for target, value in [('canvas', False), ('spans', None), ('override', True),
                              ('fail_shot', None)]:
            patcher = mock.patch.object(FakeTab, target, value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {'VTX_GFX': 'cdp', 'VTX_GFX_PNG_FAST': '0',
            'VTX_GFX_NAVFREE': '0', 'VTX_GFX_STATIC': '1', 'VTX_GFX_TABS': '1',
            'VTX_GFX_SPLIT_FRAMES': '0', 'VTX_GFX_PAGES': '1', 'VTX_GFX_CACHE_NS': ''})
        patcher.start(); self.addCleanup(patcher.stop)

    @contextlib.contextmanager
    def transport(self):
        with mock.patch.object(gfx_render, 'Browser', FakeBrowser), \
                mock.patch.object(gfx_render, 'Tab', FakeTab), \
                mock.patch.object(gfx_render.urllib.request, 'urlopen',
                                  side_effect=lambda *a, **k: io.BytesIO(protocol())) as request, \
                mock.patch.object(gfx_render.time, 'sleep'):
            yield request

    def render(self, **kwargs):
        return gfx_render.render_page(self.html, self.out, 3, width=6, height=4,
                                      tabs=1, **kwargs)


class NavigationReadiness(unittest.TestCase):
    """Exercise real navigation without creating a browser or WebSocket."""

    def tab(self, replies):
        tab = object.__new__(gfx_render.Tab)
        tab.call = mock.Mock(side_effect=[{}] + [
            {'result': {'value': reply}} for reply in replies])
        return tab

    def test_ready_document_returns_without_sleep(self):
        for url, query in [('file:///page.html?f=7', '?f=7'),
                           ('file:///page.html', '')]:
            with self.subTest(url=url):
                tab = self.tab([query + '|complete'])
                with mock.patch.object(gfx_render.time, 'monotonic', return_value=0), \
                        mock.patch.object(gfx_render.time, 'sleep') as sleep:
                    tab.navigate(url)
                self.assertEqual(tab.call.call_args_list, [
                    mock.call('Page.navigate', url=url, timeout=10),
                    mock.call('Runtime.evaluate',
                              expression="location.search+'|'+document.readyState",
                              returnByValue=True)])
                sleep.assert_not_called()

    def test_exact_query_and_complete_required_at_each_poll(self):
        url = 'file:///page.html?f=7&token=%2B'
        # Sleep can advance the clock differently under scheduler load; it must
        # never substitute for checking the current document and exact query.
        for clock_scale in [0.5, 1, 4]:
            with self.subTest(clock_scale=clock_scale):
                tab = self.tab(['?f=70&token=%2B|complete',
                                '?f=7&token=+|complete',
                                '?f=7&token=%2B|interactive',
                                '?f=7&token=%2B|complete'])
                now = [0.0]

                def advance(seconds):
                    self.assertGreater(seconds, 0)
                    self.assertLessEqual(seconds, 0.005)
                    now[0] += seconds * clock_scale

                with mock.patch.object(gfx_render.time, 'monotonic',
                                       side_effect=lambda: now[0]), \
                        mock.patch.object(gfx_render.time, 'sleep',
                                          side_effect=advance) as sleep:
                    tab.navigate(url)
                self.assertEqual(sleep.call_count, 3)
                self.assertEqual(tab.call.call_args_list, [
                    mock.call('Page.navigate', url=url, timeout=10)] + [
                    mock.call('Runtime.evaluate',
                              expression="location.search+'|'+document.readyState",
                              returnByValue=True)] * 4)

    def test_unready_document_times_out(self):
        url = 'file:///page.html?f=7'
        for value in ['?f=0|complete', '?f=7|loading', None]:
            with self.subTest(value=value):
                tab = object.__new__(gfx_render.Tab)
                tab.call = mock.Mock(return_value={'result': {'value': value}})
                now = [0.0]

                def advance(seconds):
                    self.assertGreater(seconds, 0)
                    self.assertLessEqual(seconds, 0.005)
                    now[0] += seconds

                with mock.patch.object(gfx_render.time, 'monotonic',
                                       side_effect=lambda: now[0]), \
                        mock.patch.object(gfx_render.time, 'sleep',
                                          side_effect=advance) as sleep:
                    with self.assertRaises(TimeoutError) as raised:
                        tab.navigate(url, timeout=0.012)
                self.assertEqual(str(raised.exception), 'page load timeout: ' + url)
                self.assertGreaterEqual(now[0], 0.012)
                self.assertLess(now[0], 0.017)
                self.assertGreaterEqual(sleep.call_count, 2)
                self.assertEqual(tab.call.call_count, 1 + sleep.call_count)

    def test_navigation_and_evaluation_errors_propagate(self):
        for replies in [[ConnectionError('navigate failed')],
                        [{}, RuntimeError('evaluation failed')]]:
            with self.subTest(replies=replies):
                tab = object.__new__(gfx_render.Tab)
                tab.call = mock.Mock(side_effect=replies)
                with mock.patch.object(gfx_render.time, 'monotonic', return_value=0), \
                        mock.patch.object(gfx_render.time, 'sleep') as sleep:
                    with self.assertRaises(type(replies[-1])) as raised:
                        tab.navigate('file:///page.html?f=7')
                self.assertIs(raised.exception, replies[-1])
                sleep.assert_not_called()


class FastPngConfig(Fixture):
    def test_default_off_and_explicit_values(self):
        os.environ.pop('VTX_GFX_PNG_FAST')
        self.assertFalse(gfx_backend.png_fast_requested())
        for value, expected in [('0', False), ('1', True)]:
            with mock.patch.dict(os.environ, VTX_GFX_PNG_FAST=value):
                self.assertIs(gfx_backend.png_fast_requested(), expected)
        for value in ['', 'true', '01', ' 1', '2']:
            with mock.patch.dict(os.environ, VTX_GFX_PNG_FAST=value):
                with self.assertRaisesRegex(ValueError, '0\\|1'):
                    gfx_backend.png_fast_requested()
        # Compression remains absent from the existing explicit pixel env allowlist.
        self.assertNotIn('VTX_GFX_PNG_FAST', gfx_environment.PIXEL_ENV)

    def test_reject_non_cdp_and_svg(self):
        for backend in ['cli', 'cef', 'electron']:
            with mock.patch.dict(os.environ, VTX_GFX=backend, VTX_GFX_PNG_FAST='1'):
                with self.assertRaisesRegex(ValueError, 'requires CDP'):
                    gfx_backend.png_fast_requested()
        with mock.patch.dict(os.environ, VTX_GFX_PNG_FAST='1'), \
                mock.patch.object(cli, 'budget_gate'), \
                mock.patch('vtx.gfx_svg.route', return_value='svg-sequence'), \
                mock.patch('vtx.gfx_svg_stage.render_stage') as svg:
            with self.assertRaisesRegex(ValueError, 'SVG unsupported'):
                cli._gfx(mock.Mock())
            svg.assert_not_called()


class FastPngWire(Fixture):
    def test_off_request_unchanged(self):
        with self.transport() as request:
            result = self.render()
        request.assert_not_called()
        self.assertNotIn('png_capture', result)
        shots = [kw for method, kw in FakeTab.calls if method == 'Page.captureScreenshot']
        self.assertTrue(shots)
        self.assertEqual(shots, [{'format': 'png', 'timeout': 30}] * len(shots))

    def test_fast_all_shots_and_instance_isolation(self):
        # All screenshot paths share the real Tab.shot, including setup probes,
        # white/black captures, navfree, and canvas-export fallback.
        for mode in ['override', 'opaque', 'twoshot', 'navfree', 'canvas', 'canvas-fallback', 'static']:
            with self.subTest(mode=mode):
                self.out = self.root / mode
                FakeTab.calls = []
                FakeTab.canvas = ('invalid' if mode == 'canvas-fallback' else mode == 'canvas')
                FakeTab.override = mode not in ['opaque', 'twoshot']
                FakeTab.spans = [[0, 3]] if mode == 'static' else None
                with self.transport(), \
                        mock.patch.dict(os.environ, VTX_GFX_NAVFREE='1' if mode == 'navfree' else '0'), \
                        mock.patch.object(gfx_render, 'page_transparent', return_value=mode == 'twoshot'):
                    result = self.render(png_fast=True)
                self.assertFalse(result['failed'])
                shots = [kw for method, kw in FakeTab.calls if method == 'Page.captureScreenshot']
                self.assertTrue(all(kw == {'format': 'png', 'timeout': 30,
                                           'optimizeForSpeed': True} for kw in shots))
                cap = result['png_capture']
                self.assertEqual(cap['capture_calls']['succeeded'], len(shots))
                self.assertEqual(cap['protocol_checks'], {'succeeded': 1, 'failed': 0})
                if mode == 'canvas':
                    self.assertEqual(len(shots), 0)
                    self.assertEqual(cap['canvas_frames'], 3)
                    self.assertIsNone(cap['actual_parameters'])
                else:
                    self.assertTrue(shots)
                    self.assertEqual(cap['actual_parameters'], cap['requested_parameters'])
                if mode == 'static':
                    self.assertEqual(cap['static_copied_frames'], 2)
        # Instance state is independent even while the environment requests fast.
        FakeTab.calls = []
        with mock.patch.dict(os.environ, VTX_GFX_PNG_FAST='1'):
            fast = FakeTab(1, '/a', 6, 4, png_fast=True)
            ordinary = FakeTab(1, '/b', 6, 4)
            fast.shot(); ordinary.shot()
        self.assertTrue(FakeTab.calls[0][1]['optimizeForSpeed'])
        self.assertNotIn('optimizeForSpeed', FakeTab.calls[1][1])

    def test_protocol_optional_boolean_once_each_start(self):
        # Exercise production start/hard_restart; no processes or sockets escape mocks.
        browser = gfx_render.Browser('synthetic-chrome', 2, png_fast=True)
        calls = []
        def request(url, **kwargs):
            address = url.full_url if hasattr(url, 'full_url') else url
            calls.append((address, kwargs))
            if address.endswith('/json/protocol'):
                return io.BytesIO(protocol())
            return io.BytesIO(json.dumps({'webSocketDebuggerUrl': 'ws://127.0.0.1/fake'}).encode())
        with mock.patch.object(gfx_render.socket, 'socket') as sock, \
                mock.patch.object(gfx_render.subprocess, 'Popen') as proc, \
                mock.patch.object(gfx_render.tempfile, 'mkdtemp', return_value=str(self.root / 'profile')), \
                mock.patch.object(browser, 'stop'), \
                mock.patch.object(gfx_render.urllib.request, 'urlopen', side_effect=request):
            sock.return_value.getsockname.return_value = ('127.0.0.1', 12345)
            proc.return_value.poll.return_value = None
            browser.start()
            with mock.patch.object(gfx_render, 'Tab', FakeTab):
                browser.open_tabs(); browser.open_tabs()
            browser.hard_restart()
        checks = [(url, kw) for url, kw in calls if url.endswith('/json/protocol')]
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(kw == {'timeout': 5} for _, kw in checks))
        self.assertEqual(browser.png_capture()['protocol_checks'], {'succeeded': 2, 'failed': 0})
        self.assertEqual(browser.generation, 1)

    def test_protocol_rejects_unsupported_and_io_failure(self):
        for parameter in [{'name': 'other', 'type': 'boolean', 'optional': True},
                          {'name': 'optimizeForSpeed', 'type': 'string', 'optional': True},
                          {'name': 'optimizeForSpeed', 'type': 'boolean'},
                          {'name': 'optimizeForSpeed', 'type': 'boolean', 'optional': 1}]:
            with self.subTest(parameter=parameter), self.transport(), \
                    mock.patch.object(gfx_render.urllib.request, 'urlopen',
                                      return_value=io.BytesIO(protocol(parameter))):
                with self.assertRaises(gfx_render.FastPNGError) as cm:
                    self.render(png_fast=True)
                self.assertEqual(cm.exception.png_capture['protocol_checks']['failed'], 1)
                self.assertIsNone(cm.exception.png_capture['actual_parameters'])
                self.assertTrue(FakeBrowser.instances[-1].stopped)
        with self.transport(), mock.patch.object(gfx_render.urllib.request, 'urlopen',
                                                side_effect=TimeoutError('synthetic')):
            with self.assertRaises(gfx_render.FastPNGError) as cm:
                self.render(png_fast=True)
            self.assertEqual(cm.exception.png_capture['protocol_checks']['failed'], 1)

    def test_warm_skips_browser(self):
        self.out.mkdir()
        for i in range(3):
            (self.out / f'f{i:04d}.png').write_bytes(png())
        with mock.patch.object(gfx_render, 'Browser') as browser, \
                mock.patch.object(gfx_render.urllib.request, 'urlopen') as request:
            cap = self.render(png_fast=True)['png_capture']
        browser.assert_not_called(); request.assert_not_called()
        self.assertEqual(cap['reused_frames'], 3)
        self.assertIsNone(cap['actual_parameters'])
        self.assertEqual(cap['protocol_checks'], {'succeeded': 0, 'failed': 0})

    def test_worker_fatal_reaches_caller(self):
        # First setup probe succeeds; frame screenshot fails inside the real worker.
        FakeTab.fail_shot = 2
        with self.transport():
            with self.assertRaisesRegex(gfx_render.FastPNGError, 'worker failed') as cm:
                self.render(png_fast=True)
        self.assertEqual(cm.exception.png_capture['capture_calls'], {'succeeded': 1, 'failed': 1})
        self.assertTrue(FakeBrowser.instances[-1].stopped)
        self.assertEqual(len(FakeBrowser.instances), 1)
        # A non-Exception termination in a worker is also returned to the main caller.
        self.out = self.root / 'fatal'
        FakeTab.fail_shot = None
        with self.transport(), mock.patch.object(gfx_render, '_atomic_write_bytes',
                                                side_effect=SystemExit('synthetic worker exit')):
            with self.assertRaisesRegex(gfx_render.FastPNGError, 'worker failed'):
                self.render(png_fast=True)

    def test_strict_propagates_option(self):
        self.out.mkdir()
        record = {'policy': 'strict', 'closure': 'static-subset-v1', 'html_uri': self.html.as_uri()}
        with self.transport(), mock.patch('vtx.gfx_dependencies.RequestPolicy') as policy:
            policy.return_value.observed = {'fixture'}
            result = self.render(png_fast=True, dependency_record=record)
        self.assertEqual(result['png_capture']['capture_calls'], {'succeeded': 3, 'failed': 0})
        self.assertTrue(all(kw['optimizeForSpeed'] is True for method, kw in FakeTab.calls
                            if method == 'Page.captureScreenshot'))
        FakeTab.fail_shot = 1
        with self.transport(), mock.patch('vtx.gfx_dependencies.RequestPolicy'):
            with self.assertRaises(gfx_render.FastPNGError) as cm:
                self.render(png_fast=True, dependency_record=record)
        self.assertEqual(cm.exception.png_capture['capture_calls']['failed'], 1)

    def test_cleanup_failure_preserves_provenance_and_primary_error(self):
        record = {'policy': 'strict', 'closure': 'static-subset-v1',
                  'html_uri': self.html.as_uri()}
        for strict in (False, True):
            for operation, owner in [('close', FakeTab), ('stop', FakeBrowser)]:
                with self.subTest(strict=strict, operation=operation):
                    self.out = self.root / f'cleanup-{strict}-{operation}'
                    self.out.mkdir()
                    cleanup = RuntimeError('synthetic cleanup failure')
                    with self.transport(), mock.patch.object(owner, operation, side_effect=cleanup), \
                            mock.patch('vtx.gfx_dependencies.RequestPolicy'):
                        with self.assertRaises(RuntimeError) as cm:
                            self.render(png_fast=True, dependency_record=record if strict else None)
                    self.assertIs(cm.exception, cleanup)
                    cap = cm.exception.png_capture
                    self.assertGreaterEqual(cap['capture_calls']['succeeded'], 3)
                    self.assertEqual(cap['protocol_checks'], {'succeeded': 1, 'failed': 0})
                    self.assertEqual(cap['actual_parameters'], cap['requested_parameters'])
                    self.assertEqual(cap['cleanup_failures'], [{
                        'operation': 'tab.close' if operation == 'close' else 'browser.stop',
                        'error_type': 'RuntimeError'}])
                    self.assertEqual(cm.exception.png_cleanup_errors, (cleanup,))
                    if operation == 'close':
                        self.assertTrue(FakeBrowser.instances[-1].stopped)

        # Preserve the exact already-raised object (or worker's explicit cause),
        # rather than replacing it with a later cleanup exception.
        for phase in ['start', 'setup', 'worker', 'strict']:
            with self.subTest(primary=phase):
                self.out = self.root / ('primary-' + phase)
                self.out.mkdir()
                primary = gfx_render.FastPNGError('synthetic primary failure')
                cleanup = RuntimeError('synthetic later cleanup failure')
                owner, method = {'start': (FakeBrowser, 'start'),
                    'setup': (FakeTab, 'navigate'), 'worker': (gfx_render, '_atomic_write_bytes'),
                    'strict': (FakeTab, 'shot')}[phase]
                with self.transport(), mock.patch.object(owner, method, side_effect=primary), \
                        mock.patch.object(FakeBrowser, 'stop', side_effect=cleanup), \
                        mock.patch('vtx.gfx_dependencies.RequestPolicy'):
                    with self.assertRaises(gfx_render.FastPNGError) as cm:
                        self.render(png_fast=True, dependency_record=record if phase == 'strict' else None)
                self.assertIs(cm.exception.__cause__ if phase == 'worker' else cm.exception, primary)
                self.assertEqual(cm.exception.png_cleanup_errors, (cleanup,))
                self.assertEqual(cm.exception.png_capture['cleanup_failures'], [
                    {'operation': 'browser.stop', 'error_type': 'RuntimeError'}])

        # Default-off cleanup follows the historical exception/ordering behavior.
        self.out = self.root / 'ordinary-cleanup'
        cleanup = RuntimeError('ordinary close failure')
        with self.transport(), mock.patch.object(FakeTab, 'close', side_effect=cleanup):
            with self.assertRaises(RuntimeError) as cm:
                self.render(png_fast=False)
        self.assertIs(cm.exception, cleanup)
        self.assertFalse(hasattr(cm.exception, 'png_capture'))
        self.assertFalse(FakeBrowser.instances[-1].stopped)


class FastPngStage(Fixture):
    def setUp(self):
        super().setUp()
        bind_environment(self)
        self.timeline = self.root / 'timeline.json'
        self.timeline.write_text(json.dumps(dict(project=dict(fps=30, width=6, height=4,
            duration_frames=3), sources=[], subtitles=[], audio_tracks=[],
            video_tracks=[dict(kind='gfx', clips=[dict(src='page.html', dst_in=0, dur=3)])])))
        self.c = Ctx(self.timeline)
        self.clip = self.c.tl['video_tracks'][0]['clips'][0]
        self.key = hygiene.gfx_key(self.c, self.clip)
        self.out = hygiene.gfx_dir(self.c, self.clip)

    def stage(self):
        with mock.patch.dict(os.environ, VTX_GFX_PNG_FAST='1'), \
                mock.patch.object(cli, 'find_chrome', return_value='synthetic'), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.gfx(self.c)

    def receipt(self):
        return json.loads((self.c.out / 'receipt.json').read_text())['stages']['gfx']

    def test_fast_disables_unavailable_block_and_frame_fallback(self):
        for mode in ['unavailable', 'block', 'frame']:
            with self.subTest(mode=mode), mock.patch.object(gfx_render, 'available', return_value=mode != 'unavailable'), \
                    mock.patch.object(gfx_cli, 'render_cli') as fallback, \
                    mock.patch.object(gfx_render, 'render_page') as render:
                if mode == 'block':
                    error = gfx_render.FastPNGError('synthetic block failure')
                    error.png_capture = gfx_backend.png_capture_provenance()
                    error.png_capture['protocol_checks']['failed'] = 1
                    render.side_effect = error
                else:
                    render.return_value = {'failed': [0], 'png_capture': gfx_backend.png_capture_provenance()}
                with self.assertRaises(SystemExit):
                    self.stage()
                fallback.assert_not_called()
                self.assertFalse(self.receipt()['ok'])
                self.assertFalse((self.out / gfx_cli.COMPLETE_FILE).exists())
                if mode == 'block':
                    self.assertEqual(self.receipt()['png_capture']['protocol_checks']['failed'], 1)

    def test_dry_and_warm_receipts_are_reuse_only(self):
        with self.transport():
            self.stage()
        with mock.patch.object(gfx_render, 'Browser') as browser, \
                mock.patch.object(gfx_render.urllib.request, 'urlopen') as request:
            self.stage()
        browser.assert_not_called(); request.assert_not_called()
        cap = self.receipt()['png_capture']
        self.assertTrue(self.receipt()['ok'])
        self.assertEqual(cap['reused_frames'], 3)
        self.assertEqual(cap['capture_calls'], {'succeeded': 0, 'failed': 0})
        self.assertIsNone(cap['actual_parameters'])
        before = (self.c.out / 'receipt.json').read_bytes()
        (self.out / 'f0001.png').write_bytes(b'broken')
        self.c.dry = True
        with mock.patch.object(gfx_render, 'Browser') as browser, \
                mock.patch.object(gfx_render.urllib.request, 'urlopen') as request:
            self.stage()
        browser.assert_not_called(); request.assert_not_called()
        self.assertEqual((self.c.out / 'receipt.json').read_bytes(), before)
        self.assertEqual(self.c._gfx_png_capture['protocol_checks'], {'succeeded': 0, 'failed': 0})

    def test_fast_partial_and_split_provenance(self):
        with self.transport(), mock.patch.dict(os.environ, VTX_GFX_SPLIT_FRAMES='1'):
            self.stage()
        first = self.receipt()['png_capture']
        self.assertEqual(first['protocol_checks'], {'succeeded': 3, 'failed': 0})
        self.assertEqual(first['capture_calls']['succeeded'], 6)
        (self.out / 'f0001.png').write_bytes(b'broken')
        with self.transport(), mock.patch.dict(os.environ, VTX_GFX_SPLIT_FRAMES='1'):
            self.stage()
        cap = self.receipt()['png_capture']
        self.assertEqual(cap['protocol_checks'], {'succeeded': 1, 'failed': 0})
        self.assertEqual(cap['reused_frames'], 2)
        self.assertEqual(cap['capture_calls']['succeeded'], 2)
        # One failed split must not publish a page or successful stage receipt.
        (self.out / 'f0001.png').write_bytes(b'broken')
        FakeTab.fail_shot = 2
        with self.transport(), mock.patch.dict(os.environ, VTX_GFX_SPLIT_FRAMES='1'), \
                mock.patch.object(gfx_cli, 'render_cli') as fallback:
            with self.assertRaises(SystemExit):
                self.stage()
        self.assertFalse(self.receipt()['ok'])
        self.assertEqual(self.receipt()['png_capture']['capture_calls']['failed'], 1)
        self.assertFalse((self.out / gfx_cli.COMPLETE_FILE).exists())
        fallback.assert_not_called()
