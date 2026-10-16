"""Real-file retained-evidence adversaries; synthetic runtime is not Chrome proof."""
import hashlib
import json
import uuid
from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw
from tests import run_lifecycle_strict_smoke as smoke
from tests import run_gfx_dependency_acceptance as accepted
from tests import test_gfx_experiment as fixtures
from tests import test_gfx_dependency_acceptance as dependency_tests
from tests.test_execution_events import Buffer
from scripts import gfx_evidence, bench_lifecycle
from vtx import events, gfx_provenance, gfx_render, gfx_runtime
from vtx.gfx_dependencies import resolve
from vtx.hygiene import gfx_key,gfx_dir


class StrictArtifactTests(unittest.TestCase):
    def setUp(self):
        self.case=dependency_tests.AcceptanceContracts();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.root=self.case.root;self.c=self.case.c;self.evidence=self.root/'evidence';self.evidence.mkdir()
        self.identity=dict(head='a'*40,tree='b'*40,status='')
        for target,value in [('tests.run_lifecycle_strict_smoke.source_identity',self.identity),
            ('tests.run_gfx_dependency_acceptance.font_provenance',self.case.provenance)]:
            mock=patch(target,return_value=value);mock.start();self.addCleanup(mock.stop)
        self.fixture()

    def write(self,name,value):
        (self.evidence/name).write_text(json.dumps(value))

    def fixture(self):
        c=self.c;clips=c.tl['video_tracks'][0]['clips'];run_id=str(uuid.uuid4())
        stream=Buffer();execution=events.ExecutionContext(stream,lifecycle=True)
        targets=[dict(src=cl['src'],dur=cl['dur'],gfx_key=gfx_key(c,cl),frame_dir=str(gfx_dir(c,cl))) for cl in clips]
        ledger=gfx_provenance.PublicationLedger(execution,run_id,targets,require_runtime=True,color_profile='srgb')
        receipt={'stages':{},'timeline':hashlib.sha256(c.tl_path.read_bytes()).hexdigest()}
        with execution.task('gfx',kind='stage').scope() as task:
            for index,cl in enumerate(clips):
                def render():
                    image=Image.new('RGBA',(1920,1080),'white');draw=ImageDraw.Draw(image)
                    draw.rectangle((64,0,87,23),fill=(34,68,102,255))
                    draw.rectangle((0,26,9,35),fill='black')
                    if index==0:
                        draw.rectangle((0,0,23,23),fill=(238,34,17,255));draw.rectangle((32,0,55,23),fill=(17,68,238,255))
                    else:draw.rectangle((96,0,119,23),fill=(34,204,68,255))
                    directory=gfx_dir(c,cl);directory.mkdir(parents=True)
                    import io
                    buf=io.BytesIO();image.save(buf,format='PNG')
                    for frame in range(2):
                        token=fixtures.ExperimentTests().bind_source(ledger,width=1920,height=1080)
                        gfx_render._atomic_write_bytes(directory/f'f{frame:04d}.png',buf.getvalue(),producer_token=token)
                task.attempt(render)
            execution.receipt('gfx',True,lambda ref:receipt['stages'].update(gfx=dict(ok=True,observed_by=ref)))
        execution.finish();data=stream.getvalue()
        (self.evidence/'strict-trace.events.jsonl').write_text(data)
        records=ledger.completion_records(width=1920,height=1080,events_data=data)
        runtime=gfx_runtime.identity(next(iter(ledger.browser_observations.values())))
        bundle=ledger.runtime_bundle(width=1920,height=1080,expected=runtime)
        manifest=fixtures.manifest(runtime,run_id,[]);manifest['git_sha']=self.identity['head']
        invocation=dict(stage='gfx',timeline=str(c.tl_path),timeline_sha256=receipt['timeline'],head=self.identity['head'],tree=self.identity['tree'])
        producer=dict(schema='vtx.strict_publication_smoke/1',complete=True,invocation=invocation,
            execution_run_id=execution.run_id,run_id=run_id,events_sha256=hashlib.sha256(data.encode()).hexdigest(),
            instances=records,producer_binding=bundle,env_manifest=manifest,
            input_fingerprint=gfx_evidence.input_fingerprint(c,[(cl['src'],cl['dur']) for cl in clips]),
            runtime_identity=runtime,host_identity=bench_lifecycle.stable_host(manifest),timeline_path=str(c.tl_path),
            events_path=str(self.evidence/'strict-trace.events.jsonl'),
            config=dict(scope='strict_smoke',lifecycle=True,process_samples=False,trace=False))
        dependencies=[resolve(c,cl) for cl in clips]
        urls=sorted({r['url'] for record in dependencies for r in record['resource_requests']})
        trace=dict(invocation=invocation,execution_run_id=execution.run_id,screenshot_attempts=4,
            metrics=[dict(font=True,family='Fixture',faces=[dict(family='Fixture',status='loaded')],
                platform_fonts=[dict(isCustomFont=True,glyphCount=4,familyName='DejaVu Sans')],images=[]) for _ in range(4)],
            fallback_calls=0,pending_acknowledgements=[],
            requests=[dict(url=url,request_id=str(i),decision='Fetch.fulfillRequest') for i,url in enumerate(urls)],
            acknowledgements=[dict(request_id=str(i),error=None,method='Fetch.fulfillRequest') for i in range(len(urls))])
        native=accepted.check_native_events(self.evidence/'strict-trace.events.jsonl',trace,timeline=c.tl_path,
            stage='gfx',identity=self.identity,receipt=receipt,gfx_attempts=2)
        report=dict(schema='vtx.strict_publication_smoke_report/1',passed=True,identity=self.identity,
            full_issue63_complete=False,max_frames=4,native_binding=native,frames=smoke.check_pixels(c,clips))
        for name,value in [('strict-producer.json',producer),('strict-producer.runtime.json',ledger.browser_observations),
            ('strict-producer.status.json',bench_lifecycle.runtime_observation_status(ledger.browser_observations)),
            ('strict-trace.json',trace),('receipt.json',receipt),('dependencies.json',dependencies),
            ('font-provenance.json',self.case.provenance),('smoke-report.json',report)]:self.write(name,value)
        (self.evidence/'timeline.json').write_bytes(c.tl_path.read_bytes())
        (self.evidence/'font-LICENSE.txt').write_text(self.case.provenance['notice_text'])
        (self.evidence/'exit-code.txt').write_text('0')
        (self.root/'smoke.raw.log').write_text('synthetic file fixture, never Chrome acceptance\n')

    def assert_partial(self):
        result=smoke.seal(self.root);self.assertFalse(result['complete'])
        output=self.root/'upload';self.assertTrue((output/'partial-status.json').is_file())
        self.assertLess(sum(p.stat().st_size for p in output.iterdir()),16384)
        self.assertNotIn('SECRET_SENTINEL',''.join(p.read_text() for p in output.iterdir()))
        return result

    def mutate(self,name,fn):
        path=self.evidence/name;value=json.loads(path.read_text());fn(value);path.write_text(json.dumps(value))

    def test_valid_real_file_fixture(self):
        result=smoke.seal(self.root);self.assertTrue(result['complete'])
        self.assertEqual(result['identity'],self.identity)
        self.assertEqual(set(result['files']),smoke.REQUIRED)

    def test_empty_payloads_rejected(self):
        for name in smoke.REQUIRED-{'smoke-report.json','exit-code.txt','smoke.log'}:(self.evidence/name).write_text('{}')
        self.assert_partial()

    def test_wrong_producer_identity_and_empty_instances_rejected(self):
        self.mutate('strict-producer.json',lambda v:v.update(invocation={'head':'f'*40},instances=[]));self.assert_partial()

    def test_wrong_report_tree_rejected(self):
        self.mutate('smoke-report.json',lambda v:v['identity'].update(tree='0'*40));self.assert_partial()

    def test_wrong_host_source_rejected(self):
        self.mutate('strict-producer.json',lambda v:v['env_manifest'].update(git_sha='e'*40));self.assert_partial()

    def test_rehashed_malformed_events_rejected(self):
        path=self.evidence/'strict-trace.events.jsonl';data=path.read_text().splitlines();path.write_text('\n'.join(data[:-1])+'\n')
        self.mutate('strict-producer.json',lambda v:v.update(events_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        self.assert_partial()

    def test_wrong_frame_linkage_rejected(self):
        self.mutate('strict-producer.json',lambda v:v['instances'][0]['frames']['0000'].update(publication_seq=9999));self.assert_partial()

    def test_missing_frame_rejected(self):
        self.mutate('strict-producer.json',lambda v:v['instances'][1]['frames'].pop('0001'));self.assert_partial()

    def test_missing_outcome_preserves_safe_status(self):
        (self.evidence/'exit-code.txt').unlink();self.assert_partial()

    def test_oversized_secret_failure_log_preserves_original(self):
        path=self.root/'smoke.raw.log';path.write_bytes(b'SECRET_SENTINEL'+b'x'*smoke.LIMIT)
        (self.evidence/'exit-code.txt').write_text('124');self.assert_partial();self.assertGreater(path.stat().st_size,smoke.LIMIT)

    def test_partial_budget_failure_has_no_false_pass(self):
        with patch.object(smoke,'LIMIT',100):self.assert_partial()

    def test_shared_budget_failure_keeps_diagnostic_selection(self):
        self.assertTrue(smoke.seal(self.root)['complete'])
        bundle=self.root/'bundle';bundle.mkdir();(bundle/'huge').write_bytes(b'x'*(32*1024*1024+1))
        self.assertFalse(smoke.select_uploads(self.root,bundle))
        selected=self.root/'selected-upload'
        self.assertTrue((selected/'partial-status.json').is_file())
        self.assertLess(sum(p.stat().st_size for p in selected.iterdir()),16384)
        self.assertTrue((bundle/'huge').exists())

    def test_shared_selection_success_keeps_both_namespaces(self):
        self.assertTrue(smoke.seal(self.root)['complete'])
        bundle=self.root/'bundle';bundle.mkdir();(bundle/'evidence-status.json').write_text('{}')
        self.assertTrue(smoke.select_uploads(self.root,bundle))
        self.assertTrue((self.root/'selected-upload/strict/artifact-manifest.json').is_file())
        self.assertTrue((self.root/'selected-upload/lifecycle/evidence-status.json').is_file())

    def test_wrong_native_receipt_link_rejected(self):
        self.mutate('receipt.json',lambda v:v['stages']['gfx']['observed_by'].update(event_id=['f'*36,9999]));self.assert_partial()

    def test_wrong_trace_source_tree_rejected(self):
        self.mutate('strict-trace.json',lambda v:v['invocation'].update(tree='0'*40));self.assert_partial()

    def test_changed_raw_runtime_rejected(self):
        self.mutate('strict-producer.runtime.json',lambda v:next(iter(v.values()))['actual_executable'].update(sha256='0'*64));self.assert_partial()

    def test_rehashed_report_pixel_claim_rejected(self):
        self.mutate('smoke-report.json',lambda v:v['frames'][0].update(rgba_sha256='0'*64));self.assert_partial()

    def test_extra_payload_does_not_become_trimmed_success(self):
        (self.evidence/'unexpected-font.ttf').write_bytes(b'font');self.assert_partial()

    def test_nonzero_outcome_retained_without_acceptance(self):
        (self.evidence/'exit-code.txt').write_text('124')
        result=self.assert_partial();self.assertEqual(result['return_code'],124)

    def test_symlink_payload_rejected_without_reading_target(self):
        path=self.evidence/'strict-producer.json';path.unlink();path.symlink_to(self.root/'smoke.raw.log')
        self.assert_partial()

    def test_missing_shared_bundle_still_uploads_safe_status(self):
        self.assertTrue(smoke.seal(self.root)['complete'])
        self.assertFalse(smoke.select_uploads(self.root,self.root/'missing-bundle'))
        self.assertTrue((self.root/'selected-upload/partial-status.json').is_file())

    def test_malformed_diagnostic_never_leaks_into_failure_selection(self):
        self.mutate('strict-producer.status.json',lambda v:v.update(state='SECRET_SENTINEL'))
        result=self.assert_partial()
        self.assertEqual(result['runtime_diagnostics']['state'],'unavailable')
        self.assertFalse(smoke.select_uploads(self.root,self.root/'missing',legacy_parent=self.root/'not-run'))
        text=''.join(p.read_text() for p in (self.root/'selected-upload').iterdir())
        self.assertNotIn('SECRET_SENTINEL',text)

    def test_diagnostic_cannot_contradict_raw_success_evidence(self):
        self.mutate('strict-producer.status.json',lambda v:v['observations'][0]['fields'].update(actual_executable='missing'))
        self.assert_partial()
