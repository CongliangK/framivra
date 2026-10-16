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


from tests import test_gfx_experiment as experiment_tests
from scripts import gfx_golden
from vtx import gfx_dependencies

class LifecycleDependencies(unittest.TestCase):
    setUp = experiment_tests.ExperimentTests.setUp
    bind_source = experiment_tests.ExperimentTests.bind_source

    def fixture(self, root):
        root.mkdir()
        (root / 'page.html').write_text('<p>font</p>')
        (root / 'font.woff2').write_bytes(b'first-font')
        (root / 'timeline.json').write_text(json.dumps({'project': {'fps':30,'width':1,'height':1,'duration_frames':1}, 'sources':[], 'audio_tracks':[], 'subtitles':[], 'video_tracks':[{'kind':'gfx','clips':[{'src':'page.html','dur':1,'dst_in':0,'cache_dependencies':['font.woff2']}]}]}))
        return bench.Ctx(root / 'timeline.json')

    def test_declared_font_bytes_and_copied_roots(self):
        c = self.fixture(self.root / 'a')
        other = bench.Ctx(bench.copy_fixture(c.root, self.root / 'b'))
        before = evidence.input_fingerprint(c, [('page.html', 1)])
        self.assertEqual(before, evidence.input_fingerprint(other, [('page.html', 1)]))
        (other.root / 'font.woff2').write_bytes(b'other-font')
        self.assertNotEqual(before, evidence.input_fingerprint(other, [('page.html', 1)]))

    def test_full_clip_rejects_same_src_duration_dependency_ambiguity(self):
        c = self.fixture(self.root / 'a')
        (c.root / 'other.woff2').write_bytes(b'other')
        c.tl['video_tracks'][0]['clips'].append({'src':'page.html','dur':1,'dst_in':0,'cache_dependencies':['other.woff2']})
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            evidence.input_fingerprint(c, [('page.html', 1)])

    def test_full_declared_clip_survives_paired_producer_verifier(self):
        template = self.root / 'template'; template.mkdir()
        (template / 'page.html').write_text('<p>font</p>')
        (template / 'font.woff2').write_bytes(b'declared-font')
        (template / 'timeline.json').write_text(json.dumps({'project': {'fps': 30, 'width': 1920,
            'height': 1080, 'duration_frames': 1}, 'sources': [], 'audio_tracks': [], 'subtitles': [],
            'video_tracks': [{'kind': 'gfx', 'clips': [{'src': 'page.html', 'dur': 1, 'dst_in': 0, 'cache_dependencies': ['font.woff2']}]}]}))
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


    def test_strict_renderer_publishes_existing_runtime_page_witness(self):
        c = self.fixture(self.root / 'a')
        c.tl['project']['gfx_dependency_policy'] = 'strict'
        clip = gfx_golden.full_gfx_clip(c, 'page.html', 1)
        record = gfx_dependencies.resolve(c, clip)
        directory = self.root / 'frames'; directory.mkdir()
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        ledger = provenance.PublicationLedger(execution, str(uuid.uuid4()), [
            {'src':'page.html','dur':1,'gfx_key':'a'*12,'frame_dir':str(directory)}], require_runtime=True)
        browser, tab = Mock(port=1234), Mock()
        tab.eval.return_value = {'width':1,'height':1,'device_scale':1}
        tab.call.return_value = {'result':{'value':True}}
        tab.shot.return_value = MINIMAL_PNG
        def start(): provenance.browser_ready(browser)
        def tabs(): provenance.bind_tab(browser,tab); return [tab]
        browser.start.side_effect = start; browser.open_tabs.side_effect = tabs
        def navigate(url):
            policy = tab.request_policy
            policy.response({'requestId':'main','resourceType':'Document','request':{'url':record['html_uri']}})
        tab.navigate.side_effect = navigate
        with execution.task('gfx').scope(), patch.object(gfx_render,'Browser',return_value=browser), patch.object(gfx_runtime,'collect',side_effect=lambda port,run_id:raw_observation(run_id)):
            result = gfx_render.render_page(c.root/'page.html',directory,1,width=1,height=1,dependency_record=record)
        execution.finish()
        self.assertEqual(result['failed'],[])
        records = ledger.completion_records(width=1,height=1,events_data=stream.getvalue())
        self.assertEqual(len(records[0]['frames']),1)
        self.assertEqual(next(iter(ledger.runtime_bundle(width=1,height=1,expected=gfx_runtime.identity(raw_observation(ledger.run_id)))['page_witnesses'].values()))['alpha_path'],'override')
        browser.stop.assert_called_once()

    def test_logical_css_alias_base_survives_shared_resolver_adapter(self):
        from vtx.hygiene import gfx_key
        c = self.fixture(self.root / 'alias-project')
        for name in ('real', 'alias'):
            (c.root / name).mkdir()
        (c.root / 'real/s.css').write_text('body{background:url("p.png")}')
        (c.root / 'alias/s.css').symlink_to('../real/s.css')
        (c.root / 'real/p.png').write_bytes(b'real-image')
        (c.root / 'alias/p.png').write_bytes(b'alias-image')
        (c.root / 'page.html').write_text('<link rel="stylesheet" href="alias/s.css">')
        clip = c.tl['video_tracks'][0]['clips'][0]
        clip['cache_dependencies'] = ['alias/s.css','alias/p.png','real/p.png']
        (c.root / 'timeline.json').write_text(json.dumps(c.tl))
        c = bench.Ctx(c.root / 'timeline.json')
        full = gfx_golden.full_gfx_clip(c, 'page.html', 1)
        record = gfx_dependencies.resolve(c, full)
        self.assertIn(str(c.root/'alias/p.png'), record['references'])
        self.assertNotIn(str(c.root/'real/p.png'), record['references'])
        first = gfx_key(c, full)
        before = evidence.input_fingerprint(c, [('page.html',1)])
        (c.root/'alias/p.png').write_bytes(b'changed-alias-image')
        self.assertNotEqual(first, gfx_key(c, full))
        self.assertNotEqual(before, evidence.input_fingerprint(c, [('page.html',1)]))
        full['cache_dependencies'].remove('alias/p.png')
        c.tl['project']['gfx_dependency_policy'] = 'strict'
        with self.assertRaisesRegex(gfx_dependencies.DependencyError, 'undeclared'):
            gfx_golden.full_gfx_clip(c,'page.html',1)
