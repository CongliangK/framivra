"""Explicit ordinary offline mocks only; no browser, Mac, path attack or discovery."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import cleanup, contract as c, profiles, quality as q, runner, runtime_identity
from vtx import proc_supervisor as owner


def bound_fixture():
    return {'root':'/offline-fixture','inventory_path':'/offline-inventory.json',
            'entries':[dict(zip(profiles.ENTRY_KEYS,v)) for v in profiles.ENTRY_VALUES]}


def plan_fixture():
    return c.build_plan(bound_fixture(),'fast-png',target_profile=profiles.CONTINUOUS300)


def arm_fixture(plan, arm):
    attempts=[]
    for index,job in enumerate(j for j in plan['jobs'] if j['arm']==arm):
        attempts.append({'status':'RENDERED_UNVERIFIED','exit_code':0,'job':copy.deepcopy(job),
                         'png':[dict(row,path=f"/offline/{arm}/{index}/f{row['local']:04d}.png",sha256='mock')
                                for row in job['mapping']]})
    return {'arm':arm,'status':'RENDERED_UNVERIFIED','attempts':attempts}


class ProfileTests(unittest.TestCase):
    def test_old_default_plan_is_deep_equal_and_digest_unchanged(self):
        b=bound_fixture()
        old_jobs=[]
        for arm in ('baseline','candidate'):
            for batch,frames in c.BATCHES.items():
                for entry in b['entries']:
                    mapping=c.mapped(entry,frames)
                    old_jobs.append({'arm':arm,'batch':batch,'entry':entry,'project':b['root'],
                                     'mapping':mapping,'expected_rendered':len(mapping)})
        expected={'schema':'render-pilot/1','status':'UNRUN','base':{'commit':c.BASE_COMMIT,'tree':c.BASE_TREE},
                  'today_plan_sha256':c.PLAN_SHA256,'mode':'fast-png','bound':b,'profile':c.PROFILE,
                  'geometry':[1920,1080,30],'deadline_seconds':900,'targets':c.TARGETS,'batches':c.BATCHES,
                  'jobs':old_jobs,'group_plan':None,'group_plan_sha256':None,'group_verified':None,
                  'quality_scope':'given target GFX/pair pixels only; full N1 scene/subtitles/audio UNRUN'}
        actual=c.build_plan(b,'fast-png')
        self.assertEqual(actual,expected)
        # Computed from 9a520099 baseline contract bytes using this exact bound fixture.
        self.assertEqual(c.digest(c.canonical(actual)),
                         '2a6498b6bb29b58b262716974cc46b841f57987cc7f217447a87d7032a2c24dc')
        self.assertEqual(len(actual['targets']),120)
        self.assertEqual(len(actual['jobs']),8)
        self.assertEqual(sum(j['expected_rendered'] for j in actual['jobs'] if j['arm']=='baseline'),238)
        with patch.object(runner,'read_bound',return_value=b):runner.verify_plan(actual)

    def test_new_exact_mapping_counts_and_old_constants_are_not_mutated(self):
        before=c.canonical({'targets':c.TARGETS,'batches':c.BATCHES})
        plan=plan_fixture()
        self.assertEqual(plan['targets'],list(range(1380,1680)))
        self.assertEqual(plan['batches'],{'continuous':list(range(1380,1680))})
        self.assertEqual(len(plan['jobs']),4)
        for arm in ('baseline','candidate'):
            jobs=[j for j in plan['jobs'] if j['arm']==arm]
            self.assertEqual([j['entry']['paintIndex'] for j in jobs],[6,7])
            self.assertEqual([j['entry']['duration'] for j in jobs],[3582,3600])
            self.assertEqual(sum(j['expected_rendered'] for j in jobs),600)
            self.assertEqual(jobs[0]['mapping'][0],{'global':1380,'local':1362})
            self.assertEqual(jobs[0]['mapping'][-1],{'global':1679,'local':1661})
            self.assertEqual(jobs[1]['mapping'][0],{'global':1380,'local':1380})
            self.assertEqual(jobs[1]['mapping'][-1],{'global':1679,'local':1679})
        self.assertEqual(plan['deadline_seconds'],900)
        self.assertEqual(plan['resource_policy']['thresholds'],'UNFROZEN')
        self.assertEqual(c.canonical({'targets':c.TARGETS,'batches':c.BATCHES}),before)
        with patch.object(runner,'read_bound',return_value=bound_fixture()):runner.verify_plan(plan)

    def test_fixed_schema_profile_rebuild_rejects_mutations(self):
        changes=[('target_profile','other'),('mode','group'),('schema','render-pilot/3'),
                 ('deadline_seconds',901),('expected_rgba_pairs',600.0),('targets',[1380]),
                 ('batches',c.BATCHES),('resource_policy',{'promotion':'PASS'})]
        for key,value in changes:
            plan=plan_fixture();plan[key]=value
            with self.subTest(key=key),patch.object(runner,'read_bound',return_value=bound_fixture()),self.assertRaises(ValueError):
                runner.verify_plan(plan)
        plan=plan_fixture();plan['jobs'][0]['mapping'][0]['local']=1380
        with patch.object(runner,'read_bound',return_value=bound_fixture()),self.assertRaises(ValueError):runner.verify_plan(plan)
        b=bound_fixture();b['entries'].reverse()
        with self.assertRaises(ValueError):c.build_plan(b,'fast-png',target_profile=profiles.CONTINUOUS300)

    def test_invalid_profile_mode_combinations_have_no_work_side_effects(self):
        for schema,profile,mode in [('render-pilot/2','other','fast-png'),('render-pilot/2',profiles.CONTINUOUS300,'group'),
                                    ('render-pilot/2',None,'fast-png'),('render-pilot/1',profiles.CONTINUOUS300,'fast-png'),
                                    ('render-pilot/3',profiles.CONTINUOUS300,'fast-png')]:
            with self.subTest(schema=schema,profile=profile,mode=mode),patch.object(runner,'fresh') as fresh,\
                 patch.object(runner,'clock_now') as clock,patch.object(owner,'SupervisedProcessGroup') as spawn:
                with self.assertRaises(ValueError):
                    runner.execute_pair({'schema':schema,'target_profile':profile,'mode':mode},'unused','unused','a','b')
                fresh.assert_not_called();spawn.assert_not_called();clock.assert_not_called()
        with patch.object(runner,'read_bound') as read,contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                runner.main(['plan','--bound-project','unused','--inventory','unused','--mode','group',
                             '--target-profile','continuous300'])
            read.assert_not_called()

    def test_cli_explicit_plan_profile_only_and_unknown_refused(self):
        for selected in ([],['--target-profile','continuous300']):
            output=io.StringIO()
            with patch.object(runner,'read_bound',return_value=bound_fixture()),contextlib.redirect_stdout(output):
                self.assertEqual(runner.main(['plan','--bound-project','unused','--inventory','unused',
                                              '--mode','fast-png']+selected),0)
            self.assertEqual(json.loads(output.getvalue())['schema'],'render-pilot/2' if selected else 'render-pilot/1')
        with contextlib.redirect_stderr(io.StringIO()),patch.object(runner,'read_bound') as read:
            with self.assertRaises(SystemExit):
                runner.main(['plan','--bound-project','unused','--inventory','unused','--mode','fast-png',
                             '--target-profile','arbitrary'])
            read.assert_not_called()


class CoverageTests(unittest.TestCase):
    def compare(self,arms,plan):
        with patch.object(runner,'read_bound',return_value=bound_fixture()),patch.object(q,'file_hash',return_value='mock'),\
             patch.object(q,'png_comparison',return_value={'equal':True}) as pixels:
            out=q.compare_rendered_pngs(arms,c.clock_now()+30,plan)
            return out,pixels.call_count

    def test_exact_600_pairs_and_rejection_of_both_missing_duplicate_wrong_local(self):
        plan=plan_fixture();arms=[arm_fixture(plan,a) for a in ('baseline','candidate')]
        result,count=self.compare(arms,plan)
        self.assertTrue(result['equal']);self.assertEqual(count,600);self.assertEqual(len(result['frames']),600)
        for kind in ('one_missing','same_missing','duplicate','wrong_local','wrong_job','wrong_arm','wrong_name'):
            changed=copy.deepcopy(arms)
            if kind=='one_missing':changed[0]['attempts'][0]['png'].pop()
            if kind=='same_missing':
                for arm in changed:arm['attempts'][0]['png'].pop()
            if kind=='duplicate':changed[0]['attempts'][0]['png'][1]=copy.deepcopy(changed[0]['attempts'][0]['png'][0])
            if kind=='wrong_local':changed[0]['attempts'][0]['png'][0]['local']=1380
            if kind=='wrong_job':changed[0]['attempts'][0]['job']['entry']['duration']=300
            if kind=='wrong_arm':changed.reverse()
            if kind=='wrong_name':changed[0]['attempts'][0]['png'][0]['path']='/offline/f0000.png'
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.compare(changed,plan)

    def test_exact_rgba_difference_cannot_pass_or_use_group_fallback(self):
        plan=plan_fixture();arms=[arm_fixture(plan,a) for a in ('baseline','candidate')]
        with patch.object(runner,'read_bound',return_value=bound_fixture()),patch.object(q,'file_hash',return_value='mock'),\
             patch.object(q,'png_comparison',return_value={'equal':False}):
            self.assertFalse(q.compare_rendered_pngs(arms,c.clock_now()+30,plan)['equal'])
        with patch.object(runner,'read_bound',return_value=bound_fixture()),patch.object(q,'file_hash',return_value='mock'),self.assertRaises(ValueError):
            q.compare_rendered_pngs(arms,0,plan)


class PairTests(unittest.TestCase):
    @contextlib.contextmanager
    def pair(self,order,*,first_error=None,nested='PASS',outer='PASS',equal=True,identity_ok=True):
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);chrome=root/'chrome';chrome.write_text('mock only')
            output=root/'pair';plan=plan_fixture();stages=[];now=[100.0];supervisors=[];deadlines=[]
            def identity(spec,path,arm,pid):
                return {'status':'BOUND_CURRENT_WORKER' if identity_ok else 'UNKNOWN',
                        'restoration':'MATCH_FROZEN_REFERENCES_AND_SOURCE','namespace':spec['namespace'],
                        'run':spec['output'],'arm':arm,'pid':pid,'pgid':pid,'supervisor_starttime':777,
                        'spec_sha256':c.file_hash(path),'plan_sha256':c.digest(c.canonical(spec['plan'])),
                        'implementation':spec['implementation']}
            class Binding:
                def __init__(self,spec,path,arm,module):self.report=identity(spec,path,arm,supervisors[-1].pgid)
                def restored(self):pass
            class Supervisor:
                def __init__(self,ctx,argv,backend_id,**kwargs):
                    self.arm=backend_id[len('pilot_'):];stages.append(self.arm);supervisors.append(self)
                    self.pgid=876543+len(stages)*10;self.run_dir=root/'removed';self.profile_dir=self.run_dir/'profile'
                    self.guard_proc=SimpleNamespace(pid=self.pgid+1,poll=lambda:0)
                    self.proc=SimpleNamespace(pid=self.pgid,returncode=None,poll=lambda:self.proc.returncode,wait=self.wait)
                def __enter__(self):
                    if self.arm!='quality':
                        path=output/'spec.json';spec=json.loads(path.read_bytes())
                        result={'arm':self.arm,'environment_sha256':'same','runtime_identity':identity(spec,path,self.arm,self.pgid),
                                'attempts':[{'cleanup_checks':[{'status':'PASS'}]}]}
                        (output/self.arm/'result.json').write_text(json.dumps(result))
                    return self
                def wait(self,timeout):
                    deadlines.append(timeout)
                    if len(stages)==1:
                        if first_error=='timeout':raise subprocess.TimeoutExpired('mock',timeout)
                        if first_error=='cancel':raise KeyboardInterrupt('mock cancellation')
                        if first_error=='expired':now[0]+=901
                        self.proc.returncode=7 if first_error=='exit' else 0
                    elif self.arm=='quality':self.proc.returncode=q.run_quality(output/'spec.json')
                    else:self.proc.returncode=0
                    return self.proc.returncode
                def __exit__(self,*args):
                    if self.proc.returncode is None:self.proc.returncode=-15
            def external(argv,**kwargs):
                if argv[0]=='/bin/ps':
                    if outer=='UNKNOWN':return SimpleNamespace(stdout='')
                    return SimpleNamespace(stdout=f'{os.getpid()} {os.getpgid(0)} S\n')
                return SimpleNamespace(stdout='')
            stack.enter_context(patch.object(runner.sys,'platform','darwin'))
            stack.enter_context(patch.object(runner,'verify_plan'))
            stack.enter_context(patch.object(runner,'clock_probe',return_value={'status':'MOCK_CLOCK'}))
            stack.enter_context(patch.object(c.time,'clock_gettime',side_effect=lambda _:now[0]))
            stack.enter_context(patch.object(runner,'git_identity',return_value={'commit':'a'*40,'tree':'b'*40}))
            stack.enter_context(patch.object(owner,'supervision_available',return_value=True))
            stack.enter_context(patch.object(owner,'SupervisedProcessGroup',Supervisor))
            stack.enter_context(patch.object(runner.subprocess,'run',side_effect=external))
            stack.enter_context(patch.object(owner,'proc_starttime',return_value=777))
            stack.enter_context(patch.object(cleanup,'nested_postflight',return_value={'status':nested}))
            stack.enter_context(patch.object(runtime_identity,'WorkerBinding',Binding))
            comparison=stack.enter_context(patch.object(q,'compare_rendered_pngs',return_value={'equal':equal,'frames':[]}))
            code=runner.execute_pair(plan,output,chrome,'a'*40,'b'*40,arm_order=order)
            yield SimpleNamespace(code=code,stages=stages,result=json.loads((output/'result.json').read_bytes()),
                                  spec=json.loads((output/'spec.json').read_bytes()),comparison=comparison)

    def test_ab_ba_keep_roles_one_deadline_and_unknown_resource_separate(self):
        for order,expected in [('AB',['baseline','candidate']),('BA',['candidate','baseline'])]:
            with self.subTest(order=order),self.pair(order) as run:
                self.assertEqual(run.code,0);self.assertEqual(run.stages,expected+['quality'])
                self.assertEqual(run.spec['deadline_monotonic'],100+900)
                self.assertEqual(run.result['arm_sequence'],expected)
                args=run.comparison.call_args.args
                self.assertEqual([a['arm'] for a in args[0]],['baseline','candidate'])
                self.assertEqual(args[1],run.spec['deadline_monotonic']);self.assertEqual(args[2],run.spec['plan'])
                self.assertEqual(run.result['resource_status'],'UNKNOWN');self.assertFalse(run.result['promotion_eligible'])

    def test_first_arm_exit_timeout_cancel_deadline_or_unknown_identity_blocks_next(self):
        for failure in ('exit','timeout','cancel','expired','identity'):
            with self.subTest(failure=failure),self.pair('BA',first_error=failure,identity_ok=failure!='identity') as run:
                self.assertEqual(run.code,1);self.assertEqual(run.stages,['candidate'])
                self.assertEqual(run.result['status'],'FAILED')
                if failure=='cancel':
                    self.assertTrue(run.result['processes'][0]['primary_error']['cancelled'])

    def test_cleanup_unknown_and_quality_mismatch_cannot_pass(self):
        for field in ('nested','outer'):
            with self.subTest(field=field),self.pair('AB',**{field:'UNKNOWN'}) as run:
                self.assertEqual(run.code,1);self.assertEqual(run.stages,['baseline'])
                self.assertEqual(run.result['cleanup_status'],'UNKNOWN')
        with self.pair('AB',equal=False) as run:
            self.assertEqual(run.code,1);self.assertEqual(run.stages,['baseline','candidate','quality'])
            self.assertFalse(run.result.get('quality',{}).get('equal',False))


class WorkerTests(unittest.TestCase):
    def test_two_original_duration_jobs_and_600_explicit_local_targets(self):
        from vtx import gfx_render,gfx_environment,gfx_cli
        from experiments.fast_png_pilot import adapter
        for fail,restoration_failure in ((False,False),(True,False),(True,True)):
            with self.subTest(fail=fail,restoration_failure=restoration_failure),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'baseline').mkdir();plan=plan_fixture();calls=[]
                spec={'plan':plan,'output':tmp,'namespace':'ordinary-worker-fixture','chrome':'unused',
                      'implementation':{'commit':'a'*40,'tree':'b'*40},'clock_kind':c.CLOCK_KIND,
                      'deadline_monotonic':c.clock_now()+30}
                path=root/'spec.json';path.write_text(json.dumps(spec))
                class Trace:
                    def assert_valid(self,stats,expected_frames):self.expected=expected_frames
                    def report(self):return {'capture_count':getattr(self,'expected',0)+1}
                class Binding:
                    def __init__(self,*args):self.report={'pid':123,'restoration':'NOT_CHECKED'}
                    def installed(self):return {'status':'BOUND_ORIGINAL_DELEGATES'}
                    def restored(self):
                        if restoration_failure:raise ValueError('mock restoration failure')
                        self.report['restoration']='MATCH_FROZEN_REFERENCES_AND_SOURCE'
                @contextlib.contextmanager
                def scope(**kwargs):
                    self.assertEqual(kwargs['mode'],'baseline')
                    self.assertEqual(set(kwargs),{'mode','evidence_dir'})
                    yield Trace()
                @contextlib.contextmanager
                def clean(gfx,trace,observations,path,**kwargs):
                    yield
                    observations.append({'status':'PASS'})
                def render(html,out,duration,**kwargs):
                    calls.append((html,duration,kwargs))
                    if fail:raise RuntimeError('original renderer failure')
                    for local in kwargs['frames']:(Path(out)/f'f{local:04d}.png').write_bytes(b'ordinary mock PNG')
                    return {'rendered':300,'failed':[],'tabs':1,'canvas_export':False,
                            'path_counts':{'override':300,'canvas':0,'navfree':0,'twoshot':0,'opaque':0,
                                           'canvas_fallback':0,'failed':0,'static_copied':0},
                            'timings_ms':{k:{'sum':1.0,'p50':1.0,'p95':1.0} for k in ('nav','export','decode','write')}}
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.dict(os.environ,c.PROFILE))
                    stack.enter_context(patch.object(runner,'read_bound',return_value=bound_fixture()))
                    stack.enter_context(patch.object(runner,'git_identity'))
                    stack.enter_context(patch.object(owner,'supervision_available',return_value=True))
                    stack.enter_context(patch.object(gfx_environment,'collect',return_value={'status':'input-bound'}))
                    stack.enter_context(patch.object(gfx_cli,'png_invalid_reason',return_value=None))
                    stack.enter_context(patch.object(cleanup,'browser_cleanup_scope',clean))
                    stack.enter_context(patch.object(adapter,'capture_scope',scope))
                    stack.enter_context(patch.object(runtime_identity,'WorkerBinding',Binding))
                    stack.enter_context(patch.object(gfx_render,'render_page',side_effect=render))
                    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                    code=runner.render_arm(path,'baseline')
                report=json.loads((root/'baseline'/'result.json').read_bytes())
                self.assertEqual(code,1 if fail else 0)
                self.assertEqual(len(calls),1 if fail else 2)
                self.assertEqual(calls[0][1],3582);self.assertEqual(calls[0][2]['frames'],list(range(1362,1662)))
                self.assertEqual(calls[0][2]['tabs'],1)
                if not fail:
                    self.assertEqual(calls[1][1],3600);self.assertEqual(calls[1][2]['frames'],list(range(1380,1680)))
                    self.assertEqual(sum(len(a['png']) for a in report['attempts']),600)
                    self.assertEqual(list(report['batch_totals']),['continuous'])
                    self.assertEqual(report['batch_totals']['continuous']['actual_setup_probe_captures'],2)
                    self.assertEqual(report['batch_totals']['continuous']['actual_captures'],602)
                else:
                    self.assertEqual(report['error'],'original renderer failure')
                if restoration_failure:self.assertIn('mock restoration failure',report['runtime_identity_error']['error'])


if __name__=='__main__':unittest.main()
