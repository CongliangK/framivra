"""Producer/runner source tests. No actual browser, FFmpeg or hosted acceptance."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch
from PIL import Image
from scripts import bench_lifecycle as bench, gfx_evidence as evidence
from vtx import events, gfx_provenance as provenance, gfx_render, gfx_runtime, env_manifest
from tests.test_execution_events import Buffer
from tests.test_gfx_cli_fallback import MINIMAL_PNG


def raw_observation(run_id):
    return {'schema': 1, 'source': 'live-cdp', 'run_id': run_id, 'observed_at': 'fixture',
        'browser_version': {'product': 'Chrome/152.0.1.2'},
        'system_info': {'gpu': {'featureStatus': {'gpu_compositing': 'disabled_software'}}},
        'actual_executable': {'path': '/private/browser', 'sha256': 'a' * 64, 'size': 3,
                              'observation_method': 'linux-proc-exe'},
        'effective_argv': ['/private/browser', '--headless', '--force-color-profile=srgb', '--remote-debugging-port=1234',
                           '--user-data-dir=/private/profile'],
        'process_binding': {'method': 'root-cdp-and-linux-proc-lifetime', 'pid': 42, 'start_ticks': 10},
        'viewport': None, 'color_space': None, 'alpha_path': None,
        'errors': {'viewport': 'page_observation_required', 'color_space': 'not_observed',
                   'alpha_path': 'page_observation_required'}}


def manifest(identity, run_id, configs):
    font = {'status': 'ok', 'files': {'fixture.ttf': 'b' * 64}}
    return {'schema': 'vtx.env_manifest/1', 'run_id': run_id, 'backend_id': 'cdp',
        'runtime_backend_truth': env_manifest._runtime_backend_truth('cdp', {
            'source': 'cdp', 'effective_backend': identity['effective_backend'],
            'chromium_version': identity['chromium_version'], 'viewport': '1920x1080',
            'color_space': 'srgb', 'evidence': identity['evidence']}),
        'fonts_fingerprint': {'status': 'ok', 'required': {
            family: copy.deepcopy(font) for family in env_manifest.REQUIRED_FONT_FAMILIES},
            'sans-serif': copy.deepcopy(font)},
        'font_configuration_files': {'fixture.conf': 'c' * 64},
        'font_environment': {}, 'git_sha': 'd' * 40, 'git_clean': True,
        'os_kernel': {'system': 'Linux', 'release': 'fixture', 'version': 'fixture', 'machine': 'fixture'},
        'cpu_model': 'fixture', 'display_server': {'session_type': 'none',
            'env': {'DISPLAY': None, 'WAYLAND_DISPLAY': None, 'XDG_SESSION_TYPE': None}},
        'gpu_driver_and_vram': {'status': 'not_applicable', 'reason': 'software_backend'}}


class ExperimentTests(unittest.TestCase):
    def test_color_policy_requires_actual_unique_flag(self):
        identity = gfx_runtime.identity(raw_observation(str(uuid.uuid4())))
        for flags in ([], ['--force-color-profile=display-p3'],
                      ['--force-color-profile=srgb', '--force-color-profile=display-p3']):
            broken = {**identity, 'argv': flags}
            with self.assertRaisesRegex(ValueError, 'sRGB'):
                bench.host_manifest(broken, str(uuid.uuid4()), [])

    def test_wave_boundary_encloses_jobs_and_preserves_failure(self):
        from types import SimpleNamespace
        from vtx.infra import run_wave
        for fails in (False, True):
            stream = Buffer()
            execution = events.ExecutionContext(stream, lifecycle=True)
            def job():
                if fails:
                    raise ValueError('business failure')
            if fails:
                with self.assertRaises(SystemExit):
                    run_wave(SimpleNamespace(execution=execution), [('first', job), ('second', lambda: None)])
            else:
                run_wave(SimpleNamespace(execution=execution), [('first', job), ('second', lambda: None)])
            execution.finish(1 if fails else 0)
            rows = [json.loads(line) for line in stream.getvalue().splitlines()]
            wave = [row for row in rows if row.get('extensions', {}).get('operation') == 'wave']
            wave = [row for row in rows if row.get('task_id') == wave[0]['task_id']]
            self.assertEqual([row['event'] for row in wave], ['task_start', 'task_terminal'])
            children = [row for row in rows if row.get('parent_task_id') == wave[0]['task_id']]
            self.assertEqual(len(children), 4)
            self.assertLess(wave[0]['seq'], min(row['seq'] for row in children))
            self.assertGreater(wave[1]['seq'], max(row['seq'] for row in children))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def ledger(self):
        stream = Buffer()
        execution = events.ExecutionContext(stream, lifecycle=True, lifecycle_operations=False)
        ledger = provenance.PublicationLedger(execution, str(uuid.uuid4()), [
            {'src': 'page.html', 'dur': 1, 'gfx_key': 'a' * 12, 'frame_dir': str(self.root)}], require_runtime=True)
        return execution, stream, ledger

    def bind_source(self, ledger, *, width=1, height=1):
        browser, tab = Mock(port=1234), Mock()
        with patch.object(gfx_runtime, 'collect', side_effect=lambda port, run_id: raw_observation(run_id)):
            provenance.browser_ready(browser)
        provenance.bind_tab(browser, tab)
        tab.eval.return_value = {'width': width, 'height': height, 'device_scale': 1}
        return provenance.frame_source(tab, 'opaque')

    def test_actual_publication_is_bound_to_runtime_and_page_digest_without_private_event_fields(self):
        execution, stream, ledger = self.ledger()
        with execution.task('gfx').scope():
            token = self.bind_source(ledger)
            gfx_render._atomic_write_bytes(self.root / 'f0000.png', MINIMAL_PNG, producer_token=token)
        execution.finish()
        records = ledger.completion_records(width=1, height=1, events_data=stream.getvalue())
        bundle = ledger.runtime_bundle(width=1, height=1, expected=gfx_runtime.identity(raw_observation(ledger.run_id)))
        frame = records[0]['frames']['0000']
        self.assertIn(frame['runtime_sha256'], bundle['runtime'])
        self.assertIn(frame['page_witness_sha256'], bundle['page_witnesses'])
        self.assertNotIn('/private', stream.getvalue())
        self.assertTrue(events.read_events(stream.getvalue())['complete'])
        expected = gfx_runtime.identity(raw_observation(ledger.run_id)); expected['executable_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            ledger.runtime_bundle(width=1, height=1, expected=expected)

    def test_cli_or_unknown_producer_cannot_inherit_cdp_observations(self):
        execution, stream, ledger = self.ledger()
        with execution.task('gfx').scope():
            self.bind_source(ledger)
            gfx_render._atomic_write_bytes(self.root / 'f0000.png', MINIMAL_PNG)
        execution.finish()
        self.assertEqual((self.root / 'f0000.png').read_bytes(), MINIMAL_PNG)
        self.assertFalse(events.read_events(stream.getvalue())['complete'])
        with self.assertRaises(ValueError):
            ledger.completion_records(width=1, height=1, events_data=stream.getvalue())

    def test_browser_actual_start_and_restart_each_collect_and_bind_new_tabs(self):
        execution, stream, ledger = self.ledger()
        browser = gfx_render.Browser('/fake/browser', tabs=1)
        ledger.color_profile = 'srgb'
        sock = Mock(); sock.getsockname.return_value = ('127.0.0.1', 1234)
        response = b'{"webSocketDebuggerUrl":"ws://127.0.0.1:1234/devtools/page/fixture"}'
        with execution.task('gfx').scope(), \
             patch.object(gfx_render.socket, 'socket', return_value=sock), \
             patch.object(gfx_render.subprocess, 'Popen', return_value=Mock()) as popen, \
             patch.object(gfx_render.tempfile, 'mkdtemp', return_value='/fixture'), \
             patch.object(gfx_render.shutil, 'rmtree'), \
             patch.object(gfx_render.urllib.request, 'urlopen', side_effect=lambda *a, **k: io.BytesIO(response)), \
             patch.object(gfx_render, 'Tab', side_effect=lambda *a: Mock()), \
             patch.object(gfx_runtime, 'collect', side_effect=lambda port, run_id: raw_observation(run_id)) as collect:
            browser.start(); first = browser.publication_browser_id
            self.assertEqual(browser.open_tabs()[0].publication_browser_id, first)
            browser.hard_restart(); second = browser.publication_browser_id
            self.assertNotEqual(first, second)
            self.assertEqual(browser.open_tabs()[0].publication_browser_id, second)
            self.assertEqual(collect.call_count, 2)
            for call in popen.call_args_list:
                self.assertEqual(call.args[0].count('--force-color-profile=srgb'), 1)
            browser.stop()
        execution.finish()

    def test_default_path_never_calls_runtime_collector(self):
        with patch.object(gfx_runtime, 'collect', side_effect=AssertionError('default probe')):
            provenance.browser_ready(Mock())
            self.assertIsNone(provenance.frame_source(Mock(), 'opaque'))

    def test_fresh_root_never_reuses_or_deletes_and_symlink_parent_rejects(self):
        first, second = bench.new_root(self.root), bench.new_root(self.root)
        self.assertNotEqual(first, second)
        self.assertTrue((first / 'owner.json').exists())
        link = self.root / 'alias'; link.symlink_to(first, target_is_directory=True)
        with self.assertRaises(ValueError):
            bench.new_root(link)
        template = self.root / 'template'; template.mkdir(); (template / 'a').write_text('keep')
        target = self.root / 'target'; bench.copy_fixture(template, target)
        with self.assertRaises(FileExistsError):
            bench.copy_fixture(template, target)
        self.assertEqual((target / 'a').read_text(), 'keep')

    def test_missing_color_policy_cannot_be_promoted_by_repeated_normalization(self):
        raw = {'source': 'cdp', 'effective_backend': 'software_skia', 'chromium_version': '152.0.1.2',
               'viewport': '1920x1080', 'evidence': gfx_runtime.identity(raw_observation('test'))['evidence']}
        first = env_manifest._runtime_backend_truth('cdp', raw)
        self.assertEqual(first['color_space'], 'srgb')  # Legacy display only.
        self.assertEqual(first['color_space_contract']['source'], 'unavailable')
        again = env_manifest._runtime_backend_truth('cdp', first)
        self.assertEqual(again['color_space_contract']['source'], 'unavailable')
        identity = gfx_runtime.identity(raw_observation('test'))
        m = manifest(identity, 'test', [])
        m['runtime_backend_truth'] = again
        with self.assertRaisesRegex(ValueError, 'explicit sRGB'):
            evidence._manifest({'run_id': 'test', 'env_manifest': m, 'config': {}},
                {'backend_id': 'cdp', 'effective_backend': 'software_skia', 'chromium_version': '152.0.1.2', 'config': {}}, '1920x1080')

    def test_mock_producer_issues_closed_plan_bound_receipts_and_strict_full_frame_pair(self):
        template = self.root / 'template'; template.mkdir()
        (template / 'page.html').write_text('<html/>')
        (template / 'timeline.json').write_text(json.dumps({'project': {'fps': 30, 'width': 1920,
            'height': 1080, 'duration_frames': 1}, 'sources': [], 'audio_tracks': [], 'subtitles': [],
            'video_tracks': [{'kind': 'gfx', 'clips': [{'src': 'page.html', 'dur': 1, 'dst_in': 0}]}]}))
        identity = gfx_runtime.identity(raw_observation('preflight'))
        c = bench.Ctx(template / 'timeline.json')
        plan = bench.make_plan(c, identity, bench.stable_host(manifest(identity, 'preflight', [])), 'gfx')
        png = io.BytesIO(); Image.new('RGBA', (1920, 1080), (20, 30, 40, 255)).save(png, format='PNG')
        def actual_publication(execution):
            ledger = execution.gfx_publications
            with execution.task('gfx').scope():
                token = self.bind_source(ledger, width=1920, height=1080)
                directory = Path(next(iter(ledger.targets)))
                directory.mkdir(parents=True)
                gfx_render._atomic_write_bytes(directory / 'f0000.png', png.getvalue(), producer_token=token)
        runs, paths = {}, {}
        with bench.controlled_environment('/fixture/browser'), patch.object(bench, 'host_manifest', side_effect=manifest), \
             patch.object(bench.cli, '_main', side_effect=actual_publication):
            for side in ('baseline', 'candidate'):
                paths[side] = bench.copy_fixture(template, self.root / side)
                runs[side] = bench.run_arm(paths[side], plan, side, [])
        snapshot, report = bench.compare_pair(paths['candidate'], plan, runs)
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['compared_frames'], 1)
        self.assertEqual(report['hashed_frames'], 2)
        tampered = copy.deepcopy(runs['candidate'])
        tampered['producer_binding']['runtime'].clear()
        bad = evidence.verify(bench.Ctx(paths['candidate']), [('page.html', 1)], snapshot, plan, tampered)
        self.assertFalse(bad['ok'])
        event_path = paths['candidate'].parent / 'events.jsonl'
        event_path.write_text(event_path.read_text().rsplit('\n', 2)[0] + '\n')
        bad = evidence.verify(bench.Ctx(paths['candidate']), [('page.html', 1)], snapshot, plan, runs['candidate'])
        self.assertFalse(bad['ok'])

    def test_three_round_matrix_freezes_all_plans_before_any_fresh_arm(self):
        identity = gfx_runtime.identity(raw_observation('preflight'))
        original_fixture = bench.fixture
        def fixture_without_process(root):
            result = original_fixture(root, create_media=False)
            (root / 'media.mp4').write_bytes(b'fixture media, never decoded')
            return result
        calls = []
        def arm(timeline, plan, side, font_configs):
            root = timeline.parent.parent
            self.assertEqual(len(list(root.glob('plan-*.json'))), 6)
            self.assertFalse((timeline.parent / 'cache').exists())
            calls.append((plan['arms'][side]['config']['scope'], side, timeline.parent))
            bench.write_json(timeline.parent / 'command-result.json', {'wall_seconds': 1.0})
            bench.write_json(timeline.parent / 'lifecycle-summary.json', {'synthetic': True})
            return {'synthetic': True}
        with patch.object(bench, 'fixture', side_effect=fixture_without_process), \
             patch.object(bench, 'preflight', return_value=(identity, manifest(identity, 'preflight', []), {})), \
             patch.object(bench, 'run_arm', side_effect=arm), \
             patch.object(bench, 'compare_pair', return_value=({}, {'ok': True, 'compared_frames': 120})):
            root = bench.run_experiment(self.root, '/fixture/browser', [], rounds=3)
        self.assertEqual(len(calls), 12)
        self.assertEqual(len({path for _, _, path in calls}), 12)
        self.assertEqual([side for scope, side, _ in calls if scope == 'gfx'],
                         ['baseline', 'candidate', 'candidate', 'baseline', 'baseline', 'candidate'])
        summary = json.loads((root / 'summary.json').read_text())
        self.assertFalse(summary['full_issue63_complete'])
        self.assertEqual(len(summary['rounds']), 6)

    def test_configuration_inventory_hashes_bytes_and_refuses_empty_or_fifo(self):
        path = self.root / 'fonts.conf'; path.write_text('first')
        first = bench.config_inventory([path])
        path.write_text('other')
        self.assertNotEqual(first, bench.config_inventory([path]))
        with self.assertRaises(ValueError):
            bench.config_inventory([])
        fifo = self.root / 'fifo'; os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            bench.config_inventory([fifo])

    def test_real_render_worker_and_heal_use_the_actual_tab_generation_binding(self):
        from tests.test_graphics_portability import _FakeBrowser
        directory = self.root / 'frames'
        page = self.root / 'page.html'; page.write_text('<html/>')
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True, lifecycle_operations=False)
        ledger = provenance.PublicationLedger(execution, str(uuid.uuid4()), [
            {'src': 'page.html', 'dur': 2, 'gfx_key': 'a' * 12, 'frame_dir': str(directory)}], require_runtime=True)
        browser = _FakeBrowser('override', 1); browser.port = 1234
        browser.start = lambda: provenance.browser_ready(browser)
        original_open = browser.open_tabs
        failure = [False]
        def open_tabs():
            tabs = original_open()
            for tab in tabs:
                provenance.bind_tab(browser, tab)
                original_eval, original_shot = tab.eval, tab.shot
                tab.eval = lambda expression, fn=original_eval: ({'width': 6, 'height': 4, 'device_scale': 1}
                    if 'window.innerWidth' in expression else fn(expression))
                def shot(fn=original_shot):
                    if browser.shots == 1 and not failure[0]:
                        failure[0] = True
                        raise ConnectionError('synthetic first frame failure')
                    return fn()
                tab.shot = shot
            return tabs
        browser.open_tabs = open_tabs
        def restart():
            browser.generation += 1
            provenance.browser_ready(browser)
        browser.hard_restart = restart
        with execution.task('gfx').scope(), patch.object(gfx_render, 'Browser', return_value=browser), \
             patch.object(gfx_runtime, 'collect', side_effect=lambda port, run_id: {
                 **raw_observation(run_id), 'observed_at': str(browser.generation)}):
            result = gfx_render.render_page(page, directory, 2, chrome_path='fake', tabs=1, width=6, height=4)
        execution.finish()
        self.assertEqual(result['failed'], [])
        self.assertTrue(failure[0])
        self.assertEqual(len(ledger.browser_observations), 2)
        frames = ledger.completion_records(width=6, height=4, events_data=stream.getvalue())[0]['frames']
        bundle = ledger.runtime_bundle(width=6, height=4, expected=gfx_runtime.identity(raw_observation(ledger.run_id)))
        self.assertEqual(len(frames), 2)
        for frame in frames.values():
            self.assertEqual(bundle['runtime'][frame['runtime_sha256']]['observed_at'], '1')
