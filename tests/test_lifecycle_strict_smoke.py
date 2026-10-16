"""Bounded strict smoke wiring and artifact controls; no real runtime launches."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from tests import run_lifecycle_strict_smoke as smoke
from tests import run_gfx_dependency_acceptance as accepted
from tests import test_gfx_experiment as fixtures
from vtx import events, gfx_render, gfx_runtime


class StrictSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def project(self):
        project=self.root/'project'; project.mkdir()
        (project/'page.html').write_text('<p>strict</p>')
        (project/'font.ttf').write_bytes(b'synthetic-only-font')
        timeline=project/'timeline.json'
        timeline.write_text(json.dumps({'project':{'fps':30,'width':1920,'height':1080,
            'duration_frames':1,'gfx_dependency_policy':'strict'},'sources':[], 'audio_tracks':[],
            'subtitles':[],'video_tracks':[{'kind':'gfx','clips':[{'src':'page.html',
            'dur':1,'dst_in':0,'cache_dependencies':['font.ttf']}]}]}))
        return timeline

    def invoke(self, missing_token=False):
        timeline=self.project(); trace=self.root/'trace.json'; producer=self.root/'producer.json'
        png=io.BytesIO(); Image.new('RGBA',(1920,1080),(1,2,3,255)).save(png,format='PNG')
        def main(execution):
            execution.activate(events.open_sink(trace.with_suffix('.events.jsonl')))
            self.assertEqual(execution.schema,events.LIFECYCLE_SCHEMA)
            self.assertTrue(execution.lifecycle_operations)
            self.assertIn('--lifecycle-events',__import__('sys').argv)
            ledger=execution.gfx_publications
            with execution.task('gfx').scope():
                helper=fixtures.ExperimentTests()
                token=helper.bind_source(ledger,width=1920,height=1080)
                directory=Path(next(iter(ledger.targets))); directory.mkdir(parents=True)
                gfx_render._atomic_write_bytes(directory/'f0000.png',png.getvalue(),
                    producer_token=None if missing_token else token)
            return 0
        with patch.object(accepted.subprocess,'check_output',return_value='a'*40+'\n'), \
                patch('vtx.cli._main',side_effect=main), \
                patch('scripts.bench_lifecycle.host_manifest',side_effect=fixtures.manifest):
            if missing_token:
                with self.assertRaisesRegex(ValueError,'incomplete publication'):
                    accepted.worker(timeline,trace,publication_evidence=producer)
                self.assertFalse(producer.exists())
            else:
                accepted.worker(timeline,trace,publication_evidence=producer)
                data=json.loads(producer.read_text())
                self.assertTrue(data['complete'])
                self.assertEqual(len(data['instances'][0]['frames']),1)
                self.assertTrue(data['producer_binding']['runtime'])
                self.assertTrue(data['producer_binding']['page_witnesses'])
            self.assertTrue(producer.with_suffix('.runtime.json').is_file())
            self.assertEqual(json.loads(trace.read_text())['fallback_calls'],0)
        return timeline

    def test_existing_worker_closes_real_file_producer_receipt(self):
        self.invoke()

    def test_missing_token_fails_and_keeps_partial_runtime(self):
        self.invoke(missing_token=True)

    def test_legacy_or_warm_cannot_masquerade_as_cold_strict(self):
        timeline=self.project()
        with patch.object(accepted.subprocess,'check_output',return_value='a'*40+'\n'):
            for kwargs in ({'stage':'validate'},{'injection':'local'}):
                with self.assertRaisesRegex(ValueError,'cold successful'):
                    accepted.worker(timeline,self.root/'trace',publication_evidence=self.root/'p',**kwargs)
            from vtx.infra import Ctx
            from vtx.hygiene import gfx_dir
            context=Ctx(timeline); directory=gfx_dir(context,context.tl['video_tracks'][0]['clips'][0])
            directory.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError,'fresh targets'):
                accepted.worker(timeline,self.root/'trace',publication_evidence=self.root/'p')
            data=json.loads(timeline.read_text());data['project']['gfx_dependency_policy']='legacy'
            timeline.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'strict fixture'):
                accepted.worker(timeline,self.root/'trace',publication_evidence=self.root/'p')

    def test_workflow_separate_gate_and_shared_budget(self):
        import yaml
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/lifecycle-63.yml').read_text()
        job=yaml.load(text,Loader=yaml.BaseLoader)['jobs']['paired']
        steps={s['name']:s for s in job['steps'] if 'name' in s}
        run=steps['Separate cold strict publication smoke']['run']
        self.assertIn('timeout --signal=TERM --kill-after=10s 60s',run)
        self.assertIn('exit "$rc"',run)
        self.assertIn('exit-code.txt',run)
        self.assertIn('fonts-dejavu-core',steps['Install finite profile dependencies']['run'])
        self.assertEqual(steps['Audit separate strict smoke artifact budget']['if'],'always()')
        self.assertEqual(steps['Upload bounded combined evidence']['if'],'always()')
        self.assertIn('--select-uploads',steps['Enforce shared evidence upload budget']['run'])
        self.assertIn('selected-upload',steps['Upload bounded combined evidence']['with']['path'])
        self.assertIn('30m',steps['Three paired cold rounds in both scopes']['run'])
        self.assertIn('--rounds 3 --scope both',steps['Three paired cold rounds in both scopes']['run'])

    def diagnostic_chain(self, *, diagnostic_failure=False, oversized_bundle=False):
        from types import SimpleNamespace
        from vtx import gfx_provenance
        from scripts import bench_lifecycle
        timeline=self.project(); evidence=self.root/'evidence'; evidence.mkdir()
        trace=evidence/'strict-trace.json'; producer=evidence/'strict-producer.json'
        raw={'schema':1,'source':'live-cdp','errors':{'actual_executable':'process_identity_unavailable',
             'SECRET_SENTINEL':'SECRET_SENTINEL'},'effective_argv':['SECRET_SENTINEL'],
             'provider_secret':'SECRET_SENTINEL'}
        failures=[]
        def main(execution):
            execution.activate(events.open_sink(trace.with_suffix('.events.jsonl')))
            with execution.task('gfx').scope():
                gfx_provenance.browser_ready(SimpleNamespace(port=12345))
                observed=next(iter(execution.gfx_publications.browser_observations.values()))
                try:gfx_runtime.identity(observed)
                except ValueError as error:
                    failures.append(error)
                    raise
        original=bench_lifecycle.runtime_observation_status
        def diagnostic(value):
            if diagnostic_failure:raise RuntimeError('SECRET_SENTINEL')
            return original(value)
        with patch.object(accepted.subprocess,'check_output',return_value='a'*40+'\n'), \
             patch('vtx.cli._main',side_effect=main),patch.object(gfx_runtime,'collect',return_value=raw), \
             patch.object(bench_lifecycle,'runtime_observation_status',side_effect=diagnostic):
            with self.assertRaisesRegex(ValueError,'incomplete runtime observation') as caught:
                accepted.worker(timeline,trace,publication_evidence=producer)
        self.assertIs(caught.exception,failures[0])
        self.assertFalse(producer.exists())
        self.assertIn('SECRET_SENTINEL',producer.with_suffix('.runtime.json').read_text())
        (evidence/'exit-code.txt').write_text('1')
        (self.root/'smoke.raw.log').write_text('SECRET_SENTINEL')
        sealed=smoke.seal(self.root);self.assertFalse(sealed['complete'])
        bundle=self.root/'bundle';legacy_parent=self.root/'legacy'
        if oversized_bundle:
            bundle.mkdir();legacy_parent.mkdir()
            with (bundle/'oversize').open('wb') as stream:stream.truncate(32*1024*1024+1)
        self.assertFalse(smoke.select_uploads(self.root,bundle,legacy_parent=legacy_parent))
        selection=self.root/'selected-upload'
        value=json.loads((selection/'partial-status.json').read_text())
        self.assertFalse(value['complete']);self.assertEqual(value['return_code'],1)
        self.assertEqual(value['legacy_status'],'bundle_invalid_or_overbudget' if oversized_bundle else 'not_run')
        text=''.join(p.read_text() for p in selection.iterdir())
        self.assertNotIn('SECRET_SENTINEL',text)
        self.assertLess(sum(p.stat().st_size for p in selection.iterdir()),8192)
        status=value['runtime_diagnostics']
        if diagnostic_failure:self.assertEqual(status['state'],'unavailable')
        else:
            row=status['observations'][0]
            self.assertEqual(row['fields']['actual_executable'],'missing')
            self.assertEqual(row['errors']['actual_executable'],'process_identity_unavailable')
            self.assertTrue(row['unrecognized_error_fields'])
        return value

    def test_worker_runtime_failure_reaches_actual_upload_selection(self):
        self.diagnostic_chain()

    def test_diagnostic_failure_preserves_primary_and_cannot_pass(self):
        self.diagnostic_chain(diagnostic_failure=True)

    def test_shared_overbudget_preserves_runtime_categories(self):
        self.diagnostic_chain(oversized_bundle=True)

    def test_diagnostic_projection_rejects_provider_strings_and_bounds_rows(self):
        from scripts import bench_lifecycle as bench
        projected=bench.runtime_observation_status({'SECRET_SENTINEL':{}})
        self.assertNotIn('SECRET_SENTINEL',json.dumps(projected))
        projected['observations'][0]['fields']['actual_executable']='SECRET_SENTINEL'
        with self.assertRaises(ValueError):bench.validate_runtime_observation_status(projected)
        self.assertEqual(bench.runtime_observation_status(dict.fromkeys(range(257),{}))['state'],
                         'invalid_or_overbound')

    def test_diagnostic_write_failure_after_success_still_fails_worker(self):
        from scripts import bench_lifecycle
        with patch.object(bench_lifecycle,'runtime_observation_status',side_effect=RuntimeError('SECRET_SENTINEL')):
            with self.assertRaisesRegex(RuntimeError,'strict smoke runtime diagnostic unavailable'):
                self.invoke()
