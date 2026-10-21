"""Real-file #47/#100 composition: diagnostics observe, never replay producers."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vtx import cache_state, gfx_dependencies as deps


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
