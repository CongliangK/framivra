"""Explicit ordinary mocks for arm order; no browser, media or real worker."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import cleanup, contract, quality, runner
from vtx import proc_supervisor as owner


class ArmOrderTests(unittest.TestCase):
    @contextlib.contextmanager
    def pair(self, order=None, *, first_exit=0, nested='PASS', outer='PASS',
             timeout=False, expire=False, equal=True, changed_environment=False):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root=Path(tmp); chrome=root/'chrome'; chrome.write_text('mock only')
            plan={'mode':'fast-png','base':{'commit':contract.BASE_COMMIT,'tree':contract.BASE_TREE},
                  'bound':{'root':str(root/'bound')}}
            output=root/'pair'; stages=[]; compared=[]; now=[100.0]; deadlines=[]
            class Supervisor:
                def __init__(self,ctx,argv,backend_id,**kwargs):
                    self.arm=backend_id[len('pilot_'):]; stages.append(self.arm)
                    self.pgid=876543
                    self.run_dir=root/'removed-run'; self.profile_dir=self.run_dir/'profile'
                    self.guard_proc=SimpleNamespace(pid=876545,poll=lambda:0)
                    self.proc=SimpleNamespace(pid=self.pgid,returncode=None,poll=lambda:self.proc.returncode,
                                              wait=self.wait)
                    self.argv=argv
                def __enter__(self):
                    if self.arm!='quality':
                        result={'arm':self.arm,'environment_sha256':self.arm if changed_environment else 'same',
                                'attempts':[{'status':'FAILED' if first_exit else 'RENDERED_UNVERIFIED',
                                             'exit_code':first_exit if len(stages)==1 else 0,
                                             'png':[{'bytes':123,'global':1380}],
                                             'cleanup_checks':[{'status':'PASS'}]}]}
                        (output/self.arm/'result.json').write_text(json.dumps(result))
                    return self
                def wait(self, timeout):
                    if self.arm=='quality':
                        self.proc.returncode=quality.run_quality(output/'spec.json')
                    elif len(stages)==1:
                        if expire:now[0]+=contract.DEADLINE_SECONDS+1
                        if timeout_requested:raise subprocess.TimeoutExpired('mock first arm',timeout)
                        self.proc.returncode=first_exit
                    else:self.proc.returncode=0
                    return self.proc.returncode
                def __exit__(self,*args):
                    if self.proc.returncode is None:self.proc.returncode=-15
            timeout_requested=timeout
            def compare(arms,deadline):
                compared.extend(a['arm'] for a in arms); deadlines.append(deadline)
                return {'equal':equal,'frames':[],'fixture':'comparison transport mocked'}
            def external(argv,**kwargs):
                if argv[0]=='/bin/ps':
                    if outer=='UNKNOWN':return SimpleNamespace(stdout='')
                    extra='876544 876543 S\n' if outer=='FAIL' else ''
                    return SimpleNamespace(stdout=f'{os.getpid()} {os.getpgid(0)} S\n'+extra)
                self.assertEqual(argv[0],'git'); self.assertGreater(kwargs['timeout'],0)
                return SimpleNamespace(stdout='')
            stack.enter_context(patch.object(runner.sys,'platform','darwin'))
            stack.enter_context(patch.object(runner,'verify_plan'))
            stack.enter_context(patch.object(runner,'clock_probe',return_value={'status':'MOCK_CLOCK'}))
            stack.enter_context(patch.object(contract.time,'clock_gettime',side_effect=lambda _:now[0]))
            identity=stack.enter_context(patch.object(runner,'git_identity',return_value={'commit':'a'*40,'tree':'b'*40}))
            stack.enter_context(patch.object(owner,'supervision_available',return_value=True))
            stack.enter_context(patch.object(owner,'SupervisedProcessGroup',Supervisor))
            stack.enter_context(patch.object(runner.subprocess,'run',side_effect=external))
            postflight=stack.enter_context(patch.object(cleanup,'nested_postflight',return_value={'status':nested}))
            stack.enter_context(patch.object(quality,'compare_rendered_pngs',side_effect=compare))
            code=runner.execute_pair(plan,output,chrome,'a'*40,'b'*40,arm_order=order)
            yield SimpleNamespace(code=code,root=output,stages=stages,compared=compared,deadlines=deadlines,
                                  result=json.loads((output/'result.json').read_bytes()),
                                  spec=json.loads((output/'spec.json').read_bytes()),
                                  identity=identity,postflight=postflight)

    def test_default_ab_and_ba_keep_named_roles_and_quality_direction(self):
        for order,expected in ((None,['baseline','candidate']),('AB',['baseline','candidate']),
                               ('BA',['candidate','baseline'])):
            with self.subTest(order=order),self.pair(order) as run:
                self.assertEqual(run.code,0)
                self.assertEqual(run.stages,expected+['quality'])
                self.assertEqual([p['arm'] for p in run.result['processes']],run.stages)
                self.assertEqual(run.compared,['baseline','candidate'])
                self.assertEqual(run.result['arm_order'],order or 'AB')
                self.assertEqual(run.spec['arm_order'],order or 'AB')
                self.assertEqual(run.result['arm_sequence'],expected)
                self.assertEqual(run.spec['arm_sequence'],expected)
                self.assertEqual(run.spec['deadline_monotonic'],100+contract.DEADLINE_SECONDS)
                self.assertEqual(run.deadlines,[run.spec['deadline_monotonic']])
                self.assertEqual(run.identity.call_count,2)
                self.assertEqual(run.postflight.call_count,2)
                self.assertEqual(run.result['cleanup_status'],'PASS')

    def test_failed_first_arm_retains_original_exit_and_stops(self):
        for order,first in (('AB','baseline'),('BA','candidate')):
            with self.subTest(order=order),self.pair(order,first_exit=7) as run:
                self.assertEqual(run.code,1); self.assertEqual(run.stages,[first])
                self.assertEqual(run.result['processes'][0]['returncode'],7)
                self.assertEqual(run.result['processes'][0]['returncode_after_cleanup'],7)
                original=json.loads((run.root/first/'result.json').read_bytes())
                self.assertEqual(original['attempts'][0]['exit_code'],7)
                self.assertEqual(original['attempts'][0]['png'][0]['bytes'],123)
                self.assertEqual(run.compared,[])

    def test_ba_outer_or_nested_unknown_and_residual_stop_before_baseline(self):
        for field in ('outer','nested'):
            for state in ('FAIL','UNKNOWN'):
                with self.subTest(field=field,state=state),self.pair('BA',**{field:state}) as run:
                    self.assertEqual(run.code,1); self.assertEqual(run.stages,['candidate'])
                    self.assertEqual(run.result['cleanup_status'],state)
                    self.assertFalse((run.root/'baseline').exists())

    def test_ba_timeout_preserves_failure_and_stops(self):
        with self.pair('BA',timeout=True) as run:
            self.assertEqual(run.code,1); self.assertEqual(run.stages,['candidate'])
            record=run.result['processes'][0]
            self.assertTrue(record['deadline_expired'])
            self.assertEqual(record['primary_error']['type'],'TimeoutExpired')
            self.assertEqual(record['returncode_after_cleanup'],-15)

    def test_ba_shared_deadline_exhaustion_never_starts_baseline(self):
        with self.pair('BA',expire=True) as run:
            self.assertEqual(run.code,1); self.assertEqual(run.stages,['candidate'])
            self.assertTrue(run.result['deadline_exceeded'])
            self.assertGreater(run.result['complete_wall_seconds'],contract.DEADLINE_SECONDS)

    def test_ba_quality_and_environment_failures_still_reject(self):
        with self.pair('BA',equal=False) as run:
            self.assertEqual(run.code,1); self.assertEqual(run.stages,['candidate','baseline','quality'])
            self.assertEqual(run.result['status'],'FAILED')
            self.assertEqual(run.compared,['baseline','candidate'])
        with self.pair('BA',changed_environment=True) as run:
            self.assertEqual(run.code,1); self.assertEqual(run.stages,['candidate','baseline'])
            self.assertEqual(run.compared,[])

    def test_invalid_or_non_fast_explicit_order_refused_before_side_effects(self):
        for mode,order in (('fast-png','AA'),('fast-png',''),('fast-png',[]),
                           ('group','AB'),('group','BA'),('unknown','AB')):
            with self.subTest(mode=mode,order=order),patch.object(runner,'clock_now') as clock,\
                 patch.object(runner,'fresh') as fresh,patch.object(owner,'SupervisedProcessGroup') as spawn:
                with self.assertRaises(ValueError):
                    runner.execute_pair({'mode':mode},'/unused','/unused','a','b',arm_order=order)
                clock.assert_not_called(); fresh.assert_not_called(); spawn.assert_not_called()

    def test_cli_default_and_explicit_orders_forwarded_and_bad_choice_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan=Path(tmp)/'plan.json'; plan.write_text('{"mode":"fast-png"}')
            args=['run','--plan',str(plan),'--output','/unused','--chrome','/unused',
                  '--implementation-commit','a'*40,'--implementation-tree','b'*40]
            for order in (None,'AB','BA'):
                with self.subTest(order=order),patch.object(runner,'execute_pair',return_value=0) as execute:
                    self.assertEqual(runner.main(args+(['--arm-order',order] if order else [])),0)
                    self.assertEqual(execute.call_args.kwargs,{'arm_order':order})
            with patch.object(runner,'execute_pair') as execute,contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:runner.main(args+['--arm-order','AA'])
                self.assertEqual(error.exception.code,2); execute.assert_not_called()

    def test_arm_order_is_not_accepted_on_worker_or_clock_commands(self):
        for args in (['clock-probe'],['_arm','--spec','/unused','--arm','baseline'],
                     ['_quality','--spec','/unused']):
            with self.subTest(command=args[0]),contextlib.redirect_stderr(io.StringIO()),\
                 patch.object(runner,'clock_probe') as probe,patch.object(runner,'render_arm') as render,\
                 patch.object(quality,'run_quality') as compare:
                with self.assertRaises(SystemExit) as error:runner.main(args+['--arm-order','BA'])
                self.assertEqual(error.exception.code,2)
                probe.assert_not_called(); render.assert_not_called(); compare.assert_not_called()


if __name__=='__main__':unittest.main()
