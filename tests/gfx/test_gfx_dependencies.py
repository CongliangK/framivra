"""Dependency locking falsification, without launching a browser."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vtx import gfx_dependencies as deps, hygiene, gfx_render, gfx_cli
import copy
from vtx import cache_state


class DependencyContracts(unittest.TestCase):
    def setUp(self):
        from tests.gfx._environment import bind_environment
        bind_environment(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clip = {'src': 'page.html', 'dur': 2}
        self.tl = {'project': {}, 'video_tracks': [{'kind': 'gfx', 'clips': [self.clip]}]}
        self.c = SimpleNamespace(root=self.root, tl=self.tl, fps=30, w=1, h=1)
        self.write('page.html', '<html><body>plain</body></html>')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def strict(self):
        self.tl['project']['gfx_dependency_policy'] = 'strict'

    def test_resource_free_legacy_vector(self):
        from vtx.gfx_environment import identity
        blob = ((self.root / 'page.html').read_bytes() + b'|environment:'
                + identity(self.c).encode() + b'|30|1x1|2')
        self.assertEqual(hygiene.gfx_key(self.c, self.clip), hashlib.sha1(blob).hexdigest()[:12])
        self.strict()
        self.assertNotEqual(hygiene.gfx_key(self.c, self.clip), hashlib.sha1(blob).hexdigest()[:12])

    def test_same_bytes_different_resolution_directory(self):
        for d in ('a', 'b'):
            self.write(d + '/page.html', '<img src="p.png">')
            self.write(d + '/p.png', d)
        self.tl['project']['cache_dependencies'] = ['a/p.png', 'b/p.png']
        a, b = ({'src': d + '/page.html', 'dur': 2} for d in ('a', 'b'))
        self.assertNotEqual(hygiene.gfx_key(self.c, a), hygiene.gfx_key(self.c, b))

    def test_project_union_clip_and_restored_mtime(self):
        self.write('a.css', 'body {color:red}')
        self.write('b.css', 'body {color:red}')
        shared = self.write('shared.css', 'body {margin:0}')
        self.tl['project']['cache_dependencies'] = ['shared.css']
        a = dict(self.clip, cache_dependencies=['a.css'])
        b = dict(self.clip, cache_dependencies=['b.css'])
        old = [hygiene.gfx_key(self.c, cl) for cl in (a, b)]
        path = self.root / 'a.css'; st = path.stat()
        path.write_text('body {color:tan}'); os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        new = [hygiene.gfx_key(self.c, cl) for cl in (a, b)]
        self.assertNotEqual(old[0], new[0]); self.assertEqual(old[1], new[1])
        shared.write_text('body {margin:1}')
        self.assertTrue(all(hygiene.gfx_key(self.c, cl) != key for cl, key in zip((a,b),new)))

    def test_nested_css_font_image_encoded_path(self):
        self.strict()
        self.write('page.html', '<link rel="stylesheet" href="a.css"><img src="a%20b.png">')
        self.write('a.css', '@import "nested/b.css";')
        self.write('nested/b.css', '@font-face {font-family:X;src:url("f.woff2")} body {font-family:X}')
        self.write('nested/f.woff2', 'font'); self.write('a b.png', 'image')
        self.clip['cache_dependencies'] = ['a.css', 'nested/b.css', 'nested/f.woff2', 'a b.png']
        record = deps.resolve(self.c, self.clip)
        self.assertEqual(record['closure'], 'static-subset-v1')
        self.assertEqual(len(record['references']), 4)
        self.clip['cache_dependencies'].remove('nested/f.woff2')
        with self.assertRaisesRegex(deps.DependencyError, 'undeclared'):
            deps.resolve(self.c, self.clip)

    def test_unknown_active_and_remote_fail_closed(self):
        self.strict()
        for html in ('<script>fetch("later")</script>', '<img onerror="x()">',
                     '<iframe src="page.html">', '<base href="file:///tmp/">',
                     '<img src="https://example.invalid/a.png">', '<img src="//example.invalid/a.png">',
                     '<img src="data:image/png;base64,AA==">', '<svg></svg>',
                     '<img srcset="x.png 1x">', '<!--><img src="hidden.png">-->', '<style>body {background:image-set("x.png" 1x)}</style>',
                     '<style>@import url("a.css");</style>', '<script',
                     '<style>body{background:u\\72l("x.png")}</style>'):
            with self.subTest(html=html):
                self.write('page.html', html)
                with self.assertRaises(deps.DependencyError):
                    deps.resolve(self.c, self.clip)

    def test_runtime_rejects_remote_and_requires_actual_coverage(self):
        self.strict(); record = deps.resolve(self.c, self.clip)
        policy = deps.RequestPolicy(record)
        with self.assertRaisesRegex(deps.DependencyError, 'coverage unproven'):
            policy.check(True)
        method, args = policy.response({'requestId': 'r', 'resourceType': 'Document',
                                      'request': {'url': record['html_uri'] + '?f=0'}})
        self.assertEqual(method, 'Fetch.fulfillRequest'); policy.check(True)
        method, args = policy.response({'requestId': 'bad', 'resourceType': 'Image',
                                      'request': {'url': 'https://example.invalid/a.png'}})
        self.assertEqual(method, 'Fetch.failRequest')
        with self.assertRaisesRegex(deps.DependencyError, 'rejected'): policy.check()

    def test_interleaved_cdp_ack_not_dropped(self):
        self.strict(); record = deps.resolve(self.c, self.clip)
        tab = gfx_render.Tab.__new__(gfx_render.Tab)
        tab._mid = 0; tab._policy_commands = set(); tab.request_policy = deps.RequestPolicy(record)
        responses = [{'method':'Fetch.requestPaused','params':{'requestId':'r','resourceType':'Document',
                     'request':{'url':record['html_uri']}}}, {'id':1,'result':{'ok':1}},
                     {'id':2,'error':{'message':'fulfillment rejected'}}]
        tab.ws = SimpleNamespace(send_text=lambda text: None,
                                recv_msg=lambda: (1, json.dumps(responses.pop(0))))
        with self.assertRaisesRegex(RuntimeError, 'enforcement failed'):
            tab.call('Page.navigate', url=record['html_uri'])
        self.assertEqual(responses, [])

    def test_no_strict_cli_downgrade(self):
        from vtx import cli
        self.strict()
        self.c.dry = True
        with patch.object(cli, 'budget_gate'), patch.dict(os.environ, {'VTX_GFX':'cli'}):
            with self.assertRaisesRegex(deps.DependencyError, 'downgrade forbidden'):
                cli._gfx(self.c)

    def test_missing_strict_certificate_rebuilds_every_frame(self):
        from tests.gfx.test_gfx_cli_fallback import MINIMAL_PNG
        out = self.root / 'out'; out.mkdir()
        (out / 'f0000.png').write_bytes(MINIMAL_PNG)
        gfx_cli.complete_page(out, 1, 'key', 1, 1, verify_inputs=lambda: None)
        bad, marker = gfx_cli.inspect_page(out, 1, 'key', 1, 1, dependency_digest='strict')
        self.assertEqual([i for i, _ in bad], [0]); self.assertTrue(marker)
        with self.assertRaisesRegex(gfx_cli.GfxCliError, 'runtime enforcement'):
            gfx_cli.complete_page(out, 1, 'key', 1, 1, verify_inputs=lambda: None,
                                  dependency_digest='strict')
        self.assertFalse((out / gfx_cli.COMPLETE_FILE).exists())

    def test_cdp_crash_never_calls_cli(self):
        from vtx import cli
        from vtx.infra import Ctx
        self.strict()
        self.tl.update(version=2, sources=[], subtitles=[], audio_tracks=[])
        self.tl['project'].update(fps=30, width=1, height=1, duration_frames=2)
        self.clip['dst_in'] = 0
        timeline = self.write('timeline.json', json.dumps(self.tl))
        c = Ctx(timeline)
        with patch.object(cli, 'budget_gate'), patch.object(gfx_render, 'available', return_value=True), \
             patch.object(gfx_render, 'render_page', side_effect=RuntimeError('CDP crash')), \
             patch.object(cli, 'find_chrome', return_value='mock-chrome'), \
             patch.object(gfx_cli, 'render_cli') as fallback:
            with self.assertRaisesRegex(RuntimeError, 'CDP crash'):
                cli._gfx(c)
        fallback.assert_not_called()
        self.assertFalse((hygiene.gfx_dir(c, self.clip) / gfx_cli.COMPLETE_FILE).exists())

    def test_strict_render_installs_policy_before_navigation_and_fails_no_coverage(self):
        self.strict(); record = deps.resolve(self.c, self.clip)
        calls = []
        tab = SimpleNamespace(call=lambda method, **kw: (calls.append(method) or {'result': {'value': True}}),
                              navigate=lambda url: calls.append('navigate'), close=lambda: None)
        browser = SimpleNamespace(start=lambda: None, open_tabs=lambda: [tab], stop=lambda: None)
        with patch.object(gfx_render, 'Browser', return_value=browser):
            with self.assertRaisesRegex(deps.DependencyError, 'coverage unproven'):
                gfx_render.render_strict_page(self.root / 'page.html', self.root, 1,
                    'mock', 1, 1, None, None, record)
        self.assertLess(calls.index('Fetch.enable'), calls.index('navigate'))
        self.assertLess(calls.index('Emulation.setScriptExecutionDisabled'), calls.index('navigate'))
        self.assertNotIn('Page.captureScreenshot', calls)

    def test_declared_missing_has_structured_origin(self):
        self.clip['cache_dependencies'] = ['missing.css']
        with self.assertRaises(deps.DependencyError) as caught:
            deps.resolve(self.c, self.clip)
        self.assertEqual(caught.exception.origin[0]['scope'], 'clip')
        self.assertEqual(caught.exception.origin[0]['index'], 0)
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)

    def test_golden_preserves_full_clip_and_rejects_ambiguity(self):
        from scripts.gfx.gfx_golden import full_gfx_clip
        self.write('a.css', 'body{color:red}')
        self.write('b.css', 'body{color:blue}')
        self.clip['cache_dependencies'] = ['a.css']
        self.assertIs(full_gfx_clip(self.c, 'page.html', 2), self.clip)
        self.tl['video_tracks'][0]['clips'].append(dict(self.clip, cache_dependencies=['b.css']))
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            full_gfx_clip(self.c, 'page.html', 2)

    def test_strict_marker_retains_observed_evidence(self):
        from tests.gfx.test_gfx_cli_fallback import MINIMAL_PNG
        out = self.root / 'strict-out'; out.mkdir()
        (out / 'f0000.png').write_bytes(MINIMAL_PNG)
        observed = [str(self.root / 'page.html')]
        gfx_cli.complete_page(out, 1, 'key', 1, 1, verify_inputs=lambda: None,
            dependency_digest='strict', dependency_enforcement='fetch-pinned-local-v1',
            dependency_observed=observed)
        self.assertEqual(gfx_cli.inspect_page(out, 1, 'key', 1, 1, dependency_digest='strict'), ([], False))
        self.assertEqual(json.loads((out / gfx_cli.COMPLETE_FILE).read_text())['dependency_observed'], observed)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO required')
    def test_scanner_read_rejects_fifo_without_open_block(self):
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(deps.DependencyError, 'not a regular file'):
            deps.read_regular_bytes(fifo)

    def test_css_bytes_cannot_change_between_hash_and_scan(self):
        self.strict()
        self.write('page.html', '<link rel="stylesheet" href="a.css">')
        self.write('a.css', 'body{color:red}')
        self.clip['cache_dependencies'] = ['a.css']
        original = deps.read_regular_bytes
        def changed(path):
            return b'body{color:blue}' if Path(path).name == 'a.css' else original(path)
        with patch.object(deps, 'read_regular_bytes', side_effect=changed):
            with self.assertRaisesRegex(deps.DependencyError, 'CSS changed'):
                deps.resolve(self.c, self.clip)

    def test_nonvoid_selfclosing_style_variants_never_prove_inert(self):
        variants = [
            '<style/>body{background:url("p.png")}</style>',
            '<style />body{background:url("p.png")}</style>',
            '<STYLE/>body{background:url("p.png")}</STYLE>',
            '<style id="s"/>body{background:url("p.png")}</style>',
            '<table><style/>body{background:url("p.png")}</style></table>',
        ]
        for html in variants:
            with self.subTest(html=html):
                self.tl['project']['gfx_dependency_policy'] = 'legacy'
                clips = []
                for directory in ('a', 'b'):
                    self.write(directory + '/page.html', html)
                    self.write(directory + '/p.png', directory)
                    clip = {'src': directory + '/page.html', 'dur': 2}
                    record = deps.resolve(self.c, clip)
                    self.assertFalse(record['legacy_key_safe'])
                    self.assertEqual(record['closure'], 'unproven')
                    clips.append(clip)
                self.assertNotEqual(*(hygiene.gfx_key(self.c, cl) for cl in clips))
                self.strict()
                for clip in clips:
                    with self.assertRaisesRegex(deps.DependencyError, 'self-closing non-void'):
                        deps.resolve(self.c, clip)

    def test_explicit_html_and_css_boundary_subset(self):
        invalid = ['<style/>', '<div/>', '<span />', '<TiTlE/>',
                   '<div><span>', '<div><span></div></span>', '</div>', '<img></img>',
                   '<title><style>body{color:red}</style></title>',
                   '<style><style>body{color:red}</style></style>',
                   '<style>body{color:red</style>', '<style>body{content:"red}</style>',
                   "<style>body{content:'red}</style>", '<style>body{width:calc(1px}</style>',
                   '<style>body{color:red}}</style>']
        for html in invalid:
            with self.subTest(html=html):
                self.write('page.html', html)
                self.tl['project']['gfx_dependency_policy'] = 'legacy'
                self.assertFalse(deps.resolve(self.c, self.clip)['legacy_key_safe'])
                self.strict()
                with self.assertRaises(deps.DependencyError):
                    deps.resolve(self.c, self.clip)
        for html in ('<STYLE>body{color:rgb(1,2,3)}</STYLE>', '<div><span>ok</span></div>',
                     '<br/><hr />', '<style>body{content:"(){}[]"}</style>'):
            with self.subTest(valid=html):
                self.write('page.html', html)
                self.assertEqual(deps.resolve(self.c, self.clip)['closure'], 'static-subset-v1')

    def test_normal_style_control_does_scan_resource(self):
        self.strict()
        self.write('page.html', '<StYlE>body{background:url("p.png")}</sTyLe>')
        self.write('p.png', 'image')
        with self.assertRaisesRegex(deps.DependencyError, 'undeclared'):
            deps.resolve(self.c, self.clip)
        self.clip['cache_dependencies'] = ['p.png']
        self.assertEqual(deps.resolve(self.c, self.clip)['references'], [str(self.root / 'p.png')])

    def alias_fixture(self):
        self.strict()
        self.write('real/s.css', 'body{background:url("p.png")}')
        self.write('real/p.png', 'real image')
        self.write('alias/p.png', 'alias image')
        (self.root / 'alias/s.css').symlink_to(self.root / 'real/s.css')
        self.write('page.html', '<link rel="stylesheet" href="alias/s.css">')
        self.clip['cache_dependencies'] = ['alias/s.css', 'alias/p.png']

    def test_css_symlink_keeps_logical_request_base(self):
        self.alias_fixture()
        record = deps.resolve(self.c, self.clip)
        self.assertEqual(set(record['references']), {str(self.root / 'real/s.css'),
                                                    str(self.root / 'alias/p.png')})
        requests = {entry['url']: entry['path'] for entry in record['resource_requests']}
        self.assertEqual(requests[(self.root / 'alias/s.css').as_uri()], str(self.root / 'real/s.css'))
        self.assertIn((self.root / 'alias/p.png').as_uri(), requests)
        policy = deps.RequestPolicy(record)
        for url, kind in ((record['html_uri'], 'Document'),
                          ((self.root / 'alias/s.css').as_uri(), 'Stylesheet'),
                          ((self.root / 'alias/p.png').as_uri(), 'Image')):
            method, _ = policy.response({'requestId': url, 'resourceType': kind, 'request': {'url': url}})
            self.assertEqual(method, 'Fetch.fulfillRequest')
        policy.check(require_complete=True)

    def test_css_alias_double_declaration_cannot_hide_wrong_base(self):
        self.alias_fixture()
        self.clip['cache_dependencies'].append('real/p.png')
        record = deps.resolve(self.c, self.clip)
        self.assertIn(str(self.root / 'alias/p.png'), record['references'])
        self.assertNotIn(str(self.root / 'real/p.png'), record['references'])
        self.clip['cache_dependencies'].remove('alias/p.png')
        with self.assertRaisesRegex(deps.DependencyError, 'undeclared'):
            deps.resolve(self.c, self.clip)

    def test_same_css_target_through_two_aliases_scans_both_bases(self):
        self.alias_fixture()
        self.write('other/p.png', 'other image')
        (self.root / 'other/s.css').symlink_to(self.root / 'real/s.css')
        self.write('page.html', '<link rel="stylesheet" href="alias/s.css">'
                                '<link rel="stylesheet" href="other/s.css">')
        self.clip['cache_dependencies'].extend(['other/s.css', 'other/p.png'])
        refs = deps.resolve(self.c, self.clip)['references']
        self.assertIn(str(self.root / 'alias/p.png'), refs)
        self.assertIn(str(self.root / 'other/p.png'), refs)
        self.assertNotIn(str(self.root / 'real/p.png'), refs)

    def test_alias_nested_import_retains_request_base(self):
        self.alias_fixture()
        self.write('real/s.css', '@import "nested.css";')
        self.write('real/nested.css', 'body{background:url("p.png")}')
        (self.root / 'alias/nested.css').symlink_to(self.root / 'real/nested.css')
        self.clip['cache_dependencies'].append('alias/nested.css')
        record = deps.resolve(self.c, self.clip)
        self.assertIn(str(self.root / 'alias/p.png'), record['references'])
        self.assertNotIn(str(self.root / 'real/p.png'), record['references'])

    def test_malformed_urls_are_unknown_in_legacy_and_rejected_in_strict(self):
        for url in ('http://[', 'https://[::1', 'file://[', '//[', 'http://[vBAD]', 'bad%QQ.png'):
            with self.subTest(url=url):
                self.write('page.html', '<img src="' + url + '">')
                self.tl['project']['gfx_dependency_policy'] = 'legacy'
                record = deps.resolve(self.c, self.clip)
                self.assertFalse(record['legacy_key_safe'])
                self.assertEqual(record['closure'], 'unproven')
                self.assertIsInstance(hygiene.gfx_key(self.c, self.clip), str)
                self.strict()
                with self.assertRaises(deps.DependencyError):
                    deps.resolve(self.c, self.clip)

    def test_impact_retains_unknown_style_closure_warning(self):
        from vtx.impact import capture
        self.write('page.html', '<style/>body{background:url("p.png")}</style>')
        self.tl.update(sources=[], audio_tracks=[], subtitles=[])
        self.tl['project'].update(fps=30, width=1, height=1, duration_frames=2)
        self.clip['dst_in'] = 0
        snapshot = capture(self.tl, self.root)
        record = snapshot['nodes']['clip:0:0']['inputs']['dependency_record']
        self.assertFalse(record['legacy_key_safe'])
        self.assertEqual(record['closure'], 'unproven')
        self.assertIn({'node': 'clip:0:0', 'reason': 'gfx_dependency_closure_unproven'},
                      snapshot['warnings'])

    def test_url_normalization_does_not_swallow_css_io_failure(self):
        self.write('page.html', '<link rel="stylesheet" href="a.css">')
        self.write('a.css', 'body{color:red}')
        self.clip['cache_dependencies'] = ['a.css']
        original = deps.read_regular_bytes
        def denied(path):
            if Path(path).name == 'a.css':
                raise PermissionError('fixture denial')
            return original(path)
        with patch.object(deps, 'read_regular_bytes', side_effect=denied):
            with self.assertRaisesRegex(PermissionError, 'fixture denial'):
                deps.resolve(self.c, self.clip)



class IntegratedDependencyOperations(unittest.TestCase):
    def setUp(self):
        from tests.gfx._environment import bind_environment
        bind_environment(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clip = {'src': 'page.html', 'dur': 2}
        self.tl = {'project': {}, 'sources': [],
                   'video_tracks': [{'kind': 'gfx', 'clips': [self.clip]}]}
        self.write('timeline.json', '{}')

    def write(self, name, content):
        (self.root / name).write_text(content)

    def compare(self, invoke):
        outcomes = []
        for enabled in (False, True):
            c = SimpleNamespace(root=self.root, tl=copy.deepcopy(self.tl),
                                tl_path=self.root / 'timeline.json')
            if enabled:
                c.diagnostics = []
            opened = []
            original_open = os.open
            def observed_open(path, *args, **kwargs):
                opened.append(str(path))
                return original_open(path, *args, **kwargs)
            # Recording only: all path resolution, reads, parsing and hashes real.
            with patch.object(os, 'open', observed_open):
                try:
                    outcome = ('return', invoke(c))
                except Exception as error:
                    outcome = ('raise', type(error), str(error),
                               type(error.__cause__), str(error.__cause__),
                               getattr(error, 'reason', None),
                               getattr(error, 'resource', None),
                               getattr(error, 'origin', None),
                               getattr(error, '_vtx_diagnostic_handled', False))
            outcomes.append((outcome, opened, getattr(c, 'diagnostics', [])))
        self.assertEqual(outcomes[0][:2], outcomes[1][:2])
        return outcomes[1]

    def resolve(self, c):
        return deps.resolve(c, c.tl['video_tracks'][0]['clips'][0],
                            origin='$.video_tracks[0].clips[0]')

    def test_real_static_and_unknown_results_and_read_order(self):
        for strict in (False, True):
            for html in ('<p>plain</p>', '<link rel="stylesheet" href="a.css">',
                         '<style/>body{background:url("missing.png")}',
                         '<img src="http://[">'):
                with self.subTest(strict=strict, html=html):
                    self.write('page.html', html)
                    self.write('a.css', 'body{color:red}')
                    self.tl['project'] = {'cache_dependencies': ['a.css'],
                        'gfx_dependency_policy': 'strict' if strict else 'legacy'}
                    result, reads, diagnostics = self.compare(self.resolve)
                    self.assertEqual(diagnostics, [])  # no false native-operation label
                    self.assertEqual(reads[:2], [str(self.root/'a.css'), str(self.root/'page.html')])
                    unknown = html.startswith(('<style/>', '<img'))
                    self.assertEqual(result[0], 'raise' if strict and unknown else 'return')
                    if not strict and unknown:
                        self.assertFalse(result[1]['legacy_key_safe'])
                        self.assertIn('static_error', result[1])
                    if strict and unknown:
                        self.assertIs(result[1], deps.DependencyError)

    def test_real_missing_html_is_original_io_failure_without_native_misattribution(self):
        result, reads, diagnostics = self.compare(self.resolve)
        self.assertEqual(result[:2], ('raise', FileNotFoundError))
        self.assertEqual(reads, [str(self.root/'page.html')])
        self.assertEqual(diagnostics, [])
        self.assertFalse(result[-1])

    def test_real_missing_declaration_cause_and_origins(self):
        self.write('page.html', '<p>plain</p>')
        self.clip['cache_dependencies'] = ['missing.css']
        result, reads, diagnostics = self.compare(self.resolve)
        self.assertEqual(result[:2], ('raise', deps.DependencyError))
        self.assertIs(result[3], FileNotFoundError)
        self.assertEqual(result[5], 'declared dependency unreadable')
        self.assertEqual(result[7], [{'scope': 'clip', 'index': 0, 'declaration': 'missing.css'}])
        self.assertEqual(reads, [])  # file_digest stat fails before any open
        self.assertEqual(diagnostics, [])

    def test_native_src_failure_has_exact_producer_path_without_reads(self):
        self.clip['src'] = 42
        result, reads, diagnostics = self.compare(self.resolve)
        self.assertEqual(result[:2], ('raise', TypeError))
        self.assertEqual(reads, [])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]['json_path'], '$.video_tracks[0].clips[0].src')

    def test_strict_cdp_failure_preserves_failed_attempt_task_and_original_exception(self):
        from vtx import cli, events, gfx_cli, gfx_render
        from vtx.infra import Ctx
        from tests.execution.test_execution_events import Buffer
        self.write('page.html', '<p>plain</p>')
        self.tl.update(version=2, subtitles=[], audio_tracks=[])
        self.tl['project'].update(fps=30, width=64, height=64, duration_frames=2,
                                  gfx_dependency_policy='strict')
        self.clip['dst_in'] = 0
        self.write('timeline.json', json.dumps(self.tl))
        stream = Buffer()
        execution = events.ExecutionContext(stream)
        c = Ctx(self.root / 'timeline.json', execution=execution)
        error = RuntimeError('controlled CDP failure')
        with patch.object(cli, 'budget_gate'), \
                patch.object(gfx_render, 'available', return_value=True), \
                patch.object(gfx_render, 'render_page', side_effect=error), \
                patch.object(cli, 'find_chrome', return_value='mock-chrome'), \
                patch.object(gfx_cli, 'render_cli') as fallback:
            with self.assertRaises(RuntimeError) as caught:
                cli._gfx(c)
        self.assertIs(caught.exception, error)
        fallback.assert_not_called()
        execution.finish(1, error)
        parsed = events.read_events(stream.getvalue())
        self.assertTrue(parsed['complete'])
        attempts = [r for r in parsed['events'] if r['event'] == 'attempt_terminal']
        tasks = [r for r in parsed['events'] if r['event'] == 'task_terminal']
        self.assertEqual([r['outcome'] for r in attempts], ['failed'])
        self.assertEqual([r['outcome'] for r in tasks], ['failed'])
        self.assertEqual(attempts[0]['task_id'], tasks[0]['task_id'])
        self.assertEqual(list(self.root.rglob(gfx_cli.COMPLETE_FILE)), [])

    def test_actual_context_offline_missing_and_invalid_declarations(self):
        from vtx.infra import Ctx
        self.tl.update(version=2, subtitles=[], audio_tracks=[])
        for mode in ('legacy', 'strict'):
            for case in ('missing', 'bad-syntax', 'missing-declaration', 'bad-declaration'):
                with self.subTest(mode=mode, case=case):
                    page = self.root / 'page.html'
                    if page.exists():
                        page.unlink()
                    project = dict(fps=30, width=64, height=64, duration_frames=2,
                                   gfx_dependency_policy=mode)
                    self.tl['project'] = project
                    if case != 'missing':
                        self.write('page.html', '<style/>' if case == 'bad-syntax' else '<p>plain</p>')
                    if case == 'missing-declaration':
                        project['cache_dependencies'] = ['absent.css']
                    if case == 'bad-declaration':
                        project['cache_dependencies'] = [42]
                    self.write('timeline.json', json.dumps(self.tl))
                    def construct(c):
                        loaded = Ctx(c.tl_path, diagnostics=getattr(c, 'diagnostics', None))
                        return loaded.input_fingerprint
                    result, reads, diagnostics = self.compare(construct)
                    accepted = mode == 'legacy' and case in ('missing', 'bad-syntax')
                    self.assertEqual(result[0], 'return' if accepted else 'raise')
                    if case.endswith('declaration'):
                        self.assertEqual(diagnostics[0]['json_path'], '$.project.cache_dependencies[0]')
                    else:
                        self.assertEqual(diagnostics, [])

    def test_real_receipt_scanning_is_same_with_diagnostics_and_missing_legacy_is_valid(self):
        for html in (None, '<p>plain</p>', '<img src="https://example.invalid/a.png">', '<style/>'):
            with self.subTest(html=html):
                if html is not None:
                    self.write('page.html', html)
                result, reads, diagnostics = self.compare(cache_state.receipt_fingerprint)
                self.assertEqual(result[0], 'return')
                self.assertEqual(diagnostics, [])
                self.assertEqual(reads.count(str(self.root/'page.html')), 0 if html is None else 2)

if __name__ == '__main__':
    unittest.main()
