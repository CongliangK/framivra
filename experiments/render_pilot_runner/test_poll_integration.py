"""O1 fixed-plan and persisted-evidence integration with mock CDP only.

The original renderer executes against a one-pixel mock transport. No browser,
real supervisor worker, FFmpeg, Mac, network, or performance measurement runs.
"""
import contextlib
import copy
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import contract as c, profiles, runner, quality, cleanup, runtime_identity
from .test_continuous300 import bound_fixture, plan_fixture
from experiments.fast_png_pilot import adapter, poll_observer
from experiments.fast_png_pilot import test_poll_observer as poll_fixtures
from vtx import gfx_render as gfx, gfx_environment, proc_supervisor as owner


class ContractTests(unittest.TestCase):
    def test_fixed_original_120_eight_jobs_baseline_only_and_distinct_schema(self):
        bound=bound_fixture();old=c.build_plan(bound,'fast-png');plan=c.build_plan(bound,'poll-observation')
        self.assertEqual(plan['schema'],'render-pilot/2');self.assertEqual(plan['target_profile'],profiles.POLL120)
        self.assertEqual(plan['targets'],old['targets']);self.assertEqual(plan['jobs'],old['jobs'])
        self.assertEqual(plan['batches'],old['batches']);self.assertEqual(len(plan['jobs']),8)
        self.assertEqual(plan['expected_rgba_pairs'],238);self.assertEqual(plan['deadline_seconds'],900)
        self.assertEqual(plan['capture_modes'],{'baseline':'baseline','candidate':'baseline'})
        self.assertIs(plan['poll_observation']['baseline'],False);self.assertIs(plan['poll_observation']['candidate'],True)
        self.assertEqual(plan['poll_observation']['limits'],poll_observer.LIMITS)
        with patch.object(runner,'read_bound',return_value=bound):runner.verify_plan(plan)
        for field,value in [('target_profile',profiles.CONTINUOUS300),('mode','fast-png'),('targets',list(range(1380,1680))),
                            ('capture_modes',{'baseline':'baseline','candidate':'fast'})]:
            changed=copy.deepcopy(plan);changed[field]=value
            with self.subTest(field=field),patch.object(runner,'read_bound',return_value=bound),self.assertRaises(ValueError):
                runner.verify_plan(changed)
        with self.assertRaises(ValueError):c.build_plan(bound,'poll-observation',target_profile=profiles.CONTINUOUS300)
        with self.assertRaises(ValueError):c.build_plan(bound,'fast-png',target_profile=profiles.POLL120)
        self.assertEqual(c.build_plan(bound,'fast-png'),old)
        self.assertNotIn('poll_observation',plan_fixture())
        with patch.object(runner,'fresh') as fresh,patch.object(runner,'clock_now') as clock:
            with self.assertRaises(ValueError):
                runner.execute_pair({'schema':'render-pilot/1','mode':'poll-observation'},'unused','unused','a','b')
            fresh.assert_not_called();clock.assert_not_called()

    def test_cli_profile_mixing_has_no_input_read_and_fixed_mode_constructs(self):
        base=['plan','--bound-project','unused','--inventory','unused','--mode','poll-observation']
        for extra in (['--target-profile','continuous300'],['--group-plan','unused']):
            with patch.object(runner,'read_bound') as read,self.assertRaises(ValueError):runner.main(base+extra)
            read.assert_not_called()
        stream=io.StringIO()
        with patch.object(runner,'read_bound',return_value=bound_fixture()),contextlib.redirect_stdout(stream):
            self.assertEqual(runner.main(base),0)
        self.assertEqual(json.loads(stream.getvalue())['target_profile'],profiles.POLL120)

    def test_runtime_binding_covers_observer_symmetrically_and_exact_delegates(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec={'plan':c.build_plan(bound_fixture(),'poll-observation'),'namespace':'offline','output':tmp,
                  'implementation':{'commit':'OFFLINE_UNQUALIFIED','tree':'OFFLINE_UNQUALIFIED'}}
            path=Path(tmp)/'spec.json';path.write_text(json.dumps(spec))
            bindings=[runtime_identity.WorkerBinding(spec,path,a,runner) for a in ('baseline','candidate')]
            self.assertEqual(bindings[0].sources,bindings[1].sources)
            self.assertEqual(len(bindings[0].sources),7)
            for enabled,binding in zip((False,True),bindings):
                original_tab,original_browser=gfx.Tab,gfx.Browser
                tab,browser=adapter._classes(object(),original_tab,original_browser,object() if enabled else None)
                def wrap(original):
                    def observed(*args,**kwargs):return original(*args,**kwargs)
                    return observed
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(gfx,'Tab',tab));stack.enter_context(patch.object(gfx,'Browser',browser))
                    for name,original in binding.original_helpers.items():stack.enter_context(patch.object(gfx,name,wrap(original)))
                    self.assertEqual(binding.installed(poll_observation=enabled)['poll_observation'],enabled)
                    with self.assertRaises(ValueError):binding.installed(poll_observation=not enabled)
                binding.restored()


class IntegratedPairTests(unittest.TestCase):
    @contextlib.contextmanager
    def pair(self,order='AB',failure=None):
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);output=root/'pair';chrome=root/'mock-chrome';chrome.write_text('not executable')
            plan=c.build_plan(bound_fixture(),'poll-observation');stages=[];active=[];requests_by_arm={}
            requests,_=stack.enter_context(poll_fixtures.PollObserverTests().render_fixture())
            # Preserve the requested PROFILE; source transport returns no static spans.
            stack.enter_context(patch.dict(os.environ,c.PROFILE))
            class Binding:
                def __init__(self,spec,path,arm,module):
                    pid=active[-1].pgid
                    self.report={'status':'BOUND_CURRENT_WORKER','restoration':'NOT_CHECKED',
                        'pid':pid,'pgid':pid,'supervisor_starttime':777,'namespace':spec['namespace'],
                        'run':spec['output'],'arm':arm,'implementation':spec['implementation'],
                        'spec_sha256':c.file_hash(path),'plan_sha256':c.digest(c.canonical(spec['plan']))}
                def installed(self,**kwargs):return {'status':'MOCK_BINDING_ONLY'}
                def restored(self):self.report['restoration']='MATCH_FROZEN_REFERENCES_AND_SOURCE'
            class Supervisor:
                def __init__(self,ctx,argv,backend_id,**kwargs):
                    self.arm=backend_id[len('pilot_'):];stages.append(self.arm);active.append(self)
                    self.pgid=876543+10*len(stages);self.run_dir=root/'removed';self.profile_dir=self.run_dir/'profile'
                    self.guard_proc=SimpleNamespace(pid=self.pgid+1,poll=lambda:0)
                    self.proc=SimpleNamespace(pid=self.pgid,returncode=None,poll=lambda:self.proc.returncode,wait=self.wait)
                def __enter__(self):return self
                def __exit__(self,*args):
                    if self.proc.returncode is None:self.proc.returncode=-15
                def wait(self,timeout):
                    if len(stages)==1 and failure=='timeout':raise subprocess.TimeoutExpired('ordinary mock',timeout)
                    if len(stages)==1 and failure=='cancel':raise KeyboardInterrupt('ordinary mock cancellation')
                    before=len(requests)
                    if self.arm=='quality':
                        if failure=='pixels':
                            result=json.loads((output/'candidate'/'result.json').read_bytes())
                            row=result['attempts'][-1]['png'][-1];path=Path(row['path'])
                            pixels=gfx.png_decode_rgba(path.read_bytes()).copy();pixels[0,0,0]^=1
                            path.write_bytes(gfx.png_encode_rgba(pixels));row['sha256']=c.file_hash(path)
                            (output/'candidate'/'result.json').write_text(json.dumps(result))
                        self.proc.returncode=quality.run_quality(output/'spec.json')
                    else:self.proc.returncode=runner.render_arm(output/'spec.json',self.arm)
                    requests_by_arm[self.arm]=copy.deepcopy(requests[before:])
                    return self.proc.returncode
            @contextlib.contextmanager
            def browser_cleanup(gfx,trace,observations,path,**kwargs):
                yield
                observations.append({'status':'PASS','fixture':'mock CDP transport; no browser'})
            def external(argv,**kwargs):
                return SimpleNamespace(stdout=f'{os.getpid()} {os.getpgid(0)} S\n' if argv[0]=='/bin/ps' else '')
            original_write=adapter._write_json
            def write(path,value):
                if failure=='trace' and path.name=='trace.json' and 'candidate' in path.parts:
                    raise OSError('mock successful-sidecar trace-index write failure')
                return original_write(path,value)
            compare=quality.png_comparison
            stack.enter_context(patch.object(runner.sys,'platform','darwin'))
            stack.enter_context(patch.object(runner,'read_bound',return_value=bound_fixture()))
            stack.enter_context(patch.object(runner,'git_identity',return_value={'commit':'a'*40,'tree':'b'*40}))
            stack.enter_context(patch.object(runner,'clock_probe',return_value={'status':'MOCK_CLOCK'}))
            stack.enter_context(patch.object(owner,'supervision_available',return_value=True))
            stack.enter_context(patch.object(owner,'proc_starttime',return_value=777))
            stack.enter_context(patch.object(owner,'SupervisedProcessGroup',Supervisor))
            stack.enter_context(patch.object(runner.subprocess,'run',side_effect=external))
            stack.enter_context(patch.object(cleanup,'nested_postflight',return_value={'status':'UNKNOWN' if failure=='cleanup' else 'PASS'}))
            stack.enter_context(patch.object(cleanup,'browser_cleanup_scope',browser_cleanup))
            stack.enter_context(patch.object(gfx_environment,'collect',return_value={'status':'input-bound'}))
            stack.enter_context(patch.object(runtime_identity,'WorkerBinding',Binding))
            stack.enter_context(patch.object(runner,'WIDTH',1));stack.enter_context(patch.object(runner,'HEIGHT',1))
            stack.enter_context(patch.object(quality,'png_comparison',side_effect=lambda a,b:compare(a,b,1,1)))
            stack.enter_context(patch.object(adapter,'_write_json',side_effect=write))
            if failure=='incomplete':stack.enter_context(patch.object(poll_observer.PollObserver,'status',return_value='INCOMPLETE'))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            code=runner.execute_pair(plan,output,chrome,'a'*40,'b'*40,arm_order=order)
            yield SimpleNamespace(root=output,code=code,stages=stages,requests=requests_by_arm,
                                  result=json.loads((output/'result.json').read_bytes()),
                                  spec=json.loads((output/'spec.json').read_bytes()))

    def test_ab_ba_full_old_target_mapping_exact_pngs_and_identical_requests(self):
        for order in ('AB','BA'):
            with self.subTest(order=order),self.pair(order) as run:
                self.assertEqual(run.code,0,run.result)
                self.assertEqual(run.stages,(['baseline','candidate'] if order=='AB' else ['candidate','baseline'])+['quality'])
                self.assertEqual(run.requests['baseline'],run.requests['candidate'])
                self.assertTrue(all(params=={'format':'png'} for method,timeout,params in run.requests['candidate']
                                    if method=='Page.captureScreenshot'))
                self.assertEqual(len(run.result['quality']['png_comparison']['frames']),238)
                self.assertEqual([s['status'] for s in run.result['poll_observation']],['DISABLED','COMPLETE'])
                self.assertFalse(run.result['promotion_eligible']);self.assertEqual(run.spec['plan']['deadline_seconds'],900)
                for arm,status in [('baseline','DISABLED'),('candidate','COMPLETE')]:
                    result=json.loads((run.root/arm/'result.json').read_bytes())
                    self.assertEqual(len(result['attempts']),4)
                    self.assertEqual(sum(len(a['png']) for a in result['attempts']),238)
                    self.assertTrue(all(a['poll_validation']['status']==status for a in result['attempts']))
                self.assertEqual(len(list((run.root/'baseline').glob('*/capture/poll-observation.json'))),0)
                self.assertEqual(len(list((run.root/'candidate').glob('*/capture/poll-observation.json'))),4)

    def test_complete_sidecar_with_trace_failure_never_passes_or_starts_next_arm(self):
        with self.pair('BA',failure='trace') as run:
            self.assertEqual(run.code,1);self.assertEqual(run.stages,['candidate'])
            result=json.loads((run.root/'candidate'/'result.json').read_bytes());attempt=result['attempts'][0]
            self.assertEqual(attempt['poll_observation']['status'],'COMPLETE')
            self.assertEqual(attempt['capture_scope_status'],'FAILED')
            self.assertEqual(attempt['exit_code'],1);self.assertEqual(result['status'],'FAILED')
            self.assertEqual(len(result['attempts']),1);self.assertIn('trace-index write failure',result['error'])
            with self.assertRaises(ValueError):runner.verify_poll_attempt(attempt,run.spec)

    def test_incomplete_scope_or_pixel_difference_never_passes(self):
        with self.pair('BA',failure='incomplete') as run:
            self.assertEqual(run.code,1);self.assertEqual(run.stages,['candidate'])
            result=json.loads((run.root/'candidate'/'result.json').read_bytes())
            self.assertEqual(result['attempts'][0]['poll_observation']['status'],'INCOMPLETE')
        with self.pair('AB',failure='pixels') as run:
            self.assertEqual(run.code,1);self.assertEqual(run.stages,['baseline','candidate','quality'])
            quality_result=json.loads((run.root/'quality'/'result.json').read_bytes())
            self.assertFalse(quality_result['equal'])

    def test_o1_timeout_cancel_or_cleanup_unknown_stops_before_next_arm(self):
        for failure in ('timeout','cancel','cleanup'):
            with self.subTest(failure=failure),self.pair('BA',failure=failure) as run:
                self.assertEqual(run.code,1);self.assertEqual(run.stages,['candidate'])
                self.assertEqual(run.result['status'],'FAILED')
                if failure=='timeout':self.assertTrue(run.result['processes'][0]['deadline_expired'])
                if failure=='cancel':self.assertTrue(run.result['processes'][0]['primary_error']['cancelled'])
                if failure=='cleanup':self.assertEqual(run.result['cleanup_status'],'UNKNOWN')

    def test_revalidation_rejects_missing_trace_sidecar_digest_or_other_scope(self):
        with self.pair('AB') as run:
            self.assertEqual(run.code,0)
            report=json.loads((run.root/'candidate'/'result.json').read_bytes());original=report['attempts'][0]
            for change in ('scope_failed','missing_descriptor','digest','scope','missing_trace','missing_sidecar'):
                attempt=copy.deepcopy(original);capture=Path(attempt['directory'])/'capture'
                trace_path=capture/'trace.json';side_path=capture/'poll-observation.json'
                trace_bytes=trace_path.read_bytes();side_bytes=side_path.read_bytes()
                try:
                    if change=='scope_failed':attempt['capture_scope_status']='FAILED'
                    if change=='missing_descriptor':attempt.pop('poll_observation')
                    if change=='digest':side_path.write_bytes(side_bytes+b' ')
                    if change=='missing_trace':trace_path.unlink()
                    if change=='missing_sidecar':side_path.unlink()
                    if change=='scope':
                        data=json.loads(side_bytes);data['scope']['job_context']['run_id']='different-run'
                        raw=json.dumps(data).encode();side_path.write_bytes(raw)
                        attempt['poll_observation'].update(bytes=len(raw),sha256=c.digest(raw))
                        trace=json.loads(trace_bytes);trace['poll_observation']=attempt['poll_observation']
                        trace_path.write_text(json.dumps(trace))
                    with self.subTest(change=change),self.assertRaises((ValueError,FileNotFoundError)):
                        runner.verify_poll_attempt(attempt,run.spec)
                finally:
                    trace_path.write_bytes(trace_bytes);side_path.write_bytes(side_bytes)


if __name__=='__main__':unittest.main()
