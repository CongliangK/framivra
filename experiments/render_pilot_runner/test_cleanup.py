"""Explicit cleanup regressions: ordinary mocks and one task-owned child positive.

No Chrome, restricted-link test, global signal, CI registration or media gate.
"""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import cleanup, contract, runner
from vtx import proc_supervisor as owner


def fixture_plan(root):
    chrome=root/'chrome-fixture';chrome.write_text('mock, not executable')
    return {'base':{'commit':contract.BASE_COMMIT,'tree':contract.BASE_TREE},
            'bound':{'root':str(root/'bound')}},chrome


def pair_mocks(stack):
    stack.enter_context(patch.object(runner.sys,'platform','darwin'))
    stack.enter_context(patch.object(runner,'verify_plan'))
    stack.enter_context(patch.object(runner,'clock_probe',return_value={'status':'MOCK_CLOCK'}))
    stack.enter_context(patch.object(runner,'git_identity',return_value={'commit':'a'*40,'tree':'b'*40}))
    stack.enter_context(patch.object(owner,'supervision_available',return_value=True))


def ps_fixture(extra=''):
    return f'{os.getpid()} {os.getpgid(0)} S\n'+extra


def external_fixture(argv,**kwargs):
    return SimpleNamespace(stdout=ps_fixture() if argv[0]=='/bin/ps' else '')


class DeadlineTests(unittest.TestCase):
    def test_each_identity_git_gets_remaining_deadline(self):
        clock=[10.0];timeouts=[];values=iter(['a'*40,'b'*40,''])
        def git(argv,**kwargs):
            timeouts.append(kwargs['timeout']);clock[0]+=.01
            return SimpleNamespace(stdout=next(values))
        with patch.object(contract.time,'clock_gettime',side_effect=lambda _:clock[0]),\
             patch.object(contract.subprocess,'run',side_effect=git):
            identity=contract.git_identity('/unused','a'*40,'b'*40,deadline=10.03)
        self.assertEqual(identity['tree'],'b'*40)
        self.assertEqual(len(timeouts),3)
        for got,want in zip(timeouts,[.03,.02,.01]):self.assertAlmostEqual(got,want)
        with patch.object(contract.time,'clock_gettime',return_value=20),\
             patch.object(contract.subprocess,'run') as run:
            with self.assertRaises(TimeoutError):contract.git_identity('/unused',deadline=20)
            run.assert_not_called()

    def test_parent_diff_wait_is_bounded_before_worker(self):
        calls=[]
        def slow_git(argv,**kwargs):
            calls.append(kwargs['timeout'])
            self.assertLessEqual(kwargs['timeout'],.03)
            time.sleep(kwargs['timeout'])
            raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);plan,chrome=fixture_plan(root);pair_mocks(stack)
            stack.enter_context(patch.object(runner,'DEADLINE_SECONDS',.03))
            stack.enter_context(patch.object(runner.subprocess,'run',side_effect=slow_git))
            spawn=stack.enter_context(patch.object(owner,'SupervisedProcessGroup'))
            before=time.monotonic();code=runner.execute_pair(plan,root/'pair',chrome,'a'*40,'b'*40)
            elapsed=time.monotonic()-before
            result=json.loads((root/'pair'/'result.json').read_bytes())
            self.assertEqual(code,1);self.assertEqual(result['status'],'FAILED')
            self.assertEqual(result['cleanup_status'],'PASS')  # no owned process was created
            self.assertEqual(len(calls),1);spawn.assert_not_called()
            self.assertLess(elapsed,.12)

    def test_explicit_shared_clock_no_fallback_and_parent_child(self):
        with patch.object(contract.time,'monotonic',return_value=-999):
            value=contract.clock_now()
            self.assertGreaterEqual(value,0)
        with patch.object(contract.time,'clock_gettime',None):
            with self.assertRaisesRegex(ValueError,'unavailable'):contract.clock_now()
        with patch.object(contract.time,'clock_gettime',side_effect=OSError('clock unavailable')):
            with self.assertRaises(OSError):contract.clock_now()
        with patch.object(contract.time,'clock_gettime',return_value=float('nan')):
            with self.assertRaises(ValueError):contract.clock_now()
        result=contract.clock_probe(contract.clock_now()+5)
        self.assertEqual(result['status'],'PASS_CLOCK_ONLY')
        self.assertLessEqual(result['parent_before'],result['child'])
        self.assertLessEqual(result['child'],result['parent_after'])
        for child in ({'clock_kind':contract.CLOCK_KIND,'value':99},
                      {'clock_kind':'time.monotonic','value':100}):
            with patch.object(contract.time,'clock_gettime',return_value=100),\
                 patch.object(contract.subprocess,'run',return_value=SimpleNamespace(stdout=json.dumps(child))):
                with self.assertRaisesRegex(ValueError,'origins'):contract.clock_probe()

    def test_snapshot_remaining_and_independent_cleanup_budget(self):
        captured=[]
        def snapshot(argv,**kwargs):
            captured.append(kwargs['timeout']);return SimpleNamespace(stdout=ps_fixture())
        with patch.object(cleanup.time,'clock_gettime',return_value=100),\
             patch.object(cleanup.subprocess,'run',side_effect=snapshot):
            cleanup.owned_process_snapshot([876543],deadline=100.2,observation_deadline=130)
            cleanup.owned_process_snapshot([876543],deadline=99,observation_deadline=100.3)
            with self.assertRaises(TimeoutError):
                cleanup.owned_process_snapshot([876543],deadline=99,observation_deadline=99.9)
        self.assertEqual(len(captured),2)
        self.assertAlmostEqual(captured[0],.2);self.assertAlmostEqual(captured[1],.3)


class GroupCleanupTests(unittest.TestCase):
    def fake_group(self,root):
        proc=SimpleNamespace(pid=876543,returncode=0,poll=lambda:0,wait=lambda timeout:0)
        guard=SimpleNamespace(pid=876545,poll=lambda:0)
        return SimpleNamespace(pgid=proc.pid,proc=proc,guard_proc=guard,
                               run_dir=root/'removed-run',profile_dir=root/'removed-run'/'profile')

    def test_real_terminate_false_cannot_pass_or_start_next_arm(self):
        states=[];real_terminate=owner.SupervisedProcessGroup._terminate_worker
        fixture=self
        class Supervisor:
            def __init__(self,ctx,argv,**kwargs):
                self.__dict__.update(vars(fixture.fake_group(ctx.root)))
                self.root=ctx.root;states.append(self)
            def __enter__(self):
                (self.root/'baseline'/'result.json').write_text(json.dumps({'attempts':[]}))
                return self
            def __exit__(self,*args):real_terminate(self)
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);plan,chrome=fixture_plan(root);pair_mocks(stack)
            stack.enter_context(patch.object(runner.subprocess,'run',side_effect=external_fixture))
            stack.enter_context(patch.object(owner,'SupervisedProcessGroup',Supervisor))
            stack.enter_context(patch.object(owner,'group_live_members',return_value=[876544]))
            stack.enter_context(patch.object(cleanup.subprocess,'run',side_effect=lambda argv,**kw:
                SimpleNamespace(stdout=ps_fixture('876544 876543 S\n') if argv[0]=='/bin/ps' else '')))
            killed=stack.enter_context(patch.object(owner,'kill_group',return_value=False))
            code=runner.execute_pair(plan,root/'pair',chrome,'a'*40,'b'*40)
            result=json.loads((root/'pair'/'result.json').read_bytes())
            self.assertEqual(code,1);self.assertEqual(len(states),1);killed.assert_called_once_with(876543)
            self.assertEqual(result['cleanup_status'],'FAIL')
            self.assertEqual(result['processes'][0]['cleanup']['live_members'],[876544])
            self.assertFalse((root/'pair'/'candidate').exists())

    def test_unknown_inventory_guard_and_remaining_directory_are_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=self.fake_group(root)
            with patch.object(cleanup.subprocess,'run',side_effect=external_fixture):
                self.assertEqual(cleanup.verify_group_stopped(cleanup.preserve_group(group))['status'],'PASS')
                with patch.object(cleanup.subprocess,'run',side_effect=PermissionError('mock unavailable')):
                    self.assertEqual(cleanup.verify_group_stopped(cleanup.preserve_group(group))['status'],'UNKNOWN')
                with patch.object(cleanup.subprocess,'run',side_effect=subprocess.TimeoutExpired('ps',10)):
                    self.assertEqual(cleanup.verify_group_stopped(cleanup.preserve_group(group))['status'],'UNKNOWN')
                group.guard_proc.poll=lambda:None
                self.assertEqual(cleanup.verify_group_stopped(cleanup.preserve_group(group))['status'],'FAIL')
                group.guard_proc.poll=lambda:0;group.run_dir.mkdir()
                self.assertNotEqual(cleanup.verify_group_stopped(cleanup.preserve_group(group))['status'],'PASS')

    def test_timeout_performance_failure_keeps_nested_cleanup_unknown(self):
        states=[];fixture=self
        class Supervisor:
            def __init__(self,ctx,argv,**kwargs):
                self.__dict__.update(vars(fixture.fake_group(ctx.root)))
                self.root=ctx.root;states.append(self)
                self.proc.wait=lambda timeout:(_ for _ in ()).throw(subprocess.TimeoutExpired('mock arm',timeout))
            def __enter__(self):
                (self.root/'baseline'/'result.json').write_text(json.dumps({'attempts':[{}]}))
                return self
            def __exit__(self,*args):self.proc.returncode=-15
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);plan,chrome=fixture_plan(root);pair_mocks(stack)
            stack.enter_context(patch.object(runner.subprocess,'run',side_effect=external_fixture))
            stack.enter_context(patch.object(owner,'SupervisedProcessGroup',Supervisor))
            stack.enter_context(patch.object(owner,'group_live_members',return_value=[]))
            code=runner.execute_pair(plan,root/'pair',chrome,'a'*40,'b'*40)
            result=json.loads((root/'pair'/'result.json').read_bytes())
            self.assertEqual(code,1);self.assertEqual(len(states),1)
            self.assertEqual(result['cleanup_status'],'UNKNOWN')
            self.assertEqual(result['processes'][0]['cleanup']['status'],'PASS')
            self.assertEqual(result['processes'][0]['nested_cleanup']['status'],'UNKNOWN')
            self.assertTrue(result['processes'][0]['deadline_expired'])
            self.assertFalse((root/'pair'/'candidate').exists())

    def test_browser_stop_retains_failure_and_original_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=self.fake_group(root);errors=[];checks=[]
            class Browser:
                def __init__(self):self.group=group
                def stop(self):self.group=None
            gfx=SimpleNamespace(Browser=Browser)
            trace=SimpleNamespace(fail=lambda where,error:errors.append((where,str(error))))
            with patch.object(cleanup.subprocess,'run',return_value=SimpleNamespace(stdout=ps_fixture('876544 876543 S\n'))),\
                 patch.object(owner,'proc_starttime',return_value=42):
                with cleanup.browser_cleanup_scope(gfx,trace,checks,root/'browser-cleanup.json'):
                    with self.assertRaises(cleanup.CleanupUnverified):gfx.Browser().stop()
            self.assertIs(gfx.Browser,Browser);self.assertEqual(len(errors),1)
            self.assertEqual(checks[0]['status'],'FAIL')
            journal=json.loads((root/'browser-cleanup.json').read_bytes())
            self.assertEqual(journal['owned']['pgid'],876543)
            self.assertEqual(journal['state'],'FAIL')

    def test_interrupted_journal_unknown_and_reaper_residual_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);job=root/'00-job';job.mkdir();path=job/'browser-cleanup.json'
            path.write_text(json.dumps({'state':'STARTING','owned':None}))
            with patch.object(owner,'reap_orphan_workers',return_value={'removed':0}) as reap:
                report=cleanup.nested_postflight(root)
            self.assertEqual(report['status'],'UNKNOWN');reap.assert_called_once()
            self.assertEqual(json.loads(path.read_bytes())['state'],'STARTING')
            owned={'pgid':876543,'guard_pid':876545,'guard_starttime':42,
                   'run_dir':str(root/'removed-run'),'profile_dir':str(root/'removed-run'/'profile')}
            path.write_text(json.dumps({'state':'ACTIVE','owned':owned}))
            original_starttime=owner.proc_starttime
            with patch.object(owner,'reap_orphan_workers',return_value={'removed':1}) as reap,\
                 patch.object(owner,'proc_starttime',side_effect=lambda pid:None if pid==876545 else original_starttime(pid)),\
                 patch.object(cleanup.subprocess,'run',return_value=SimpleNamespace(stdout=ps_fixture('876544 876543 S\n'))):
                report=cleanup.nested_postflight(root)
            self.assertEqual(report['status'],'FAIL');reap.assert_called_once()
            self.assertEqual(report['journals'][0]['record']['owned'],owned)
            self.assertEqual(report['journals'][0]['live_members'],[876544])

    def test_mock_pair_pass_requires_three_verified_outer_groups(self):
        states=[];fixture=self
        class Supervisor:
            def __init__(self,ctx,argv,backend_id,**kwargs):
                self.__dict__.update(vars(fixture.fake_group(ctx.root)))
                self.root=ctx.root;self.arm=backend_id.removeprefix('pilot_');states.append(self)
            def __enter__(self):
                if self.arm=='quality':
                    result={'equal':True,'status':'LOCAL_RGBA_PAIR_PASSED_FULL_SCENE_UNRUN'}
                else:
                    result={'environment_sha256':'same','attempts':[{'cleanup_checks':[{'status':'PASS'}]}]}
                    job=self.root/self.arm/'00-job';job.mkdir()
                    (job/'browser-cleanup.json').write_text(json.dumps({'state':'PASS','owned':{
                        'pgid':876543,'guard_pid':876545,'guard_starttime':42,
                        'run_dir':str(job/'removed'),'profile_dir':str(job/'removed'/'profile')}}))
                (self.root/self.arm/'result.json').write_text(json.dumps(result))
                return self
            def __exit__(self,*args):pass
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);plan,chrome=fixture_plan(root);pair_mocks(stack)
            stack.enter_context(patch.object(runner.subprocess,'run',side_effect=external_fixture))
            stack.enter_context(patch.object(owner,'SupervisedProcessGroup',Supervisor))
            stack.enter_context(patch.object(owner,'group_live_members',return_value=[]))
            code=runner.execute_pair(plan,root/'pair',chrome,'a'*40,'b'*40)
            result=json.loads((root/'pair'/'result.json').read_bytes())
            self.assertEqual(code,0);self.assertEqual(len(states),3)
            self.assertEqual(result['cleanup_status'],'PASS')
            self.assertTrue(all(p['cleanup']['status']=='PASS' for p in result['processes']))

    def test_snapshot_errors_and_unreadable_native_rows_cannot_be_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            group=self.fake_group(Path(tmp));saved=cleanup.preserve_group(group)
            for stdout in ('',f'{os.getpid()}\n',ps_fixture()+'876544 bad S\n',
                           ps_fixture()+ps_fixture(),ps_fixture()+'876544 876543 ?\n'):
                with self.subTest(stdout=stdout),patch.object(cleanup.subprocess,'run',return_value=SimpleNamespace(stdout=stdout)):
                    self.assertEqual(cleanup.verify_group_stopped(saved)['status'],'UNKNOWN')
            # Native per-PID reads may fail, but the explicit group snapshot
            # contains a live member and must never be interpreted as empty.
            with patch.object(owner,'_darwin_info',return_value=None),\
                 patch.object(owner,'_proc_stat_tail',return_value=None),\
                 patch.object(cleanup.subprocess,'run',return_value=SimpleNamespace(stdout=ps_fixture('876544 876543 S\n'))) as run:
                result=cleanup.verify_group_stopped(saved)
            self.assertEqual(result['status'],'FAIL');self.assertEqual(result['live_members'],[876544])
            self.assertEqual(run.call_args.kwargs['timeout'],10)
            self.assertEqual(run.call_args.args[0],['/bin/ps','-axo','pid=,pgid=,stat='])

    def test_unrelated_native_unreadable_pid_does_not_block_empty_owned_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved=cleanup.preserve_group(self.fake_group(Path(tmp)))
            with patch.object(owner,'_darwin_info',return_value=None),\
                 patch.object(owner,'_proc_stat_tail',return_value=None),\
                 patch.object(cleanup.subprocess,'run',return_value=SimpleNamespace(stdout=ps_fixture('876540 876540 S\n'))):
                result=cleanup.verify_group_stopped(saved)
            self.assertEqual(result['status'],'PASS')
            self.assertEqual(result['observation']['matching_rows'],[])

    def test_pair_snapshot_unknown_blocks_next_arm(self):
        states=[];fixture=self
        class Supervisor:
            def __init__(self,ctx,argv,**kwargs):
                self.__dict__.update(vars(fixture.fake_group(ctx.root)))
                self.root=ctx.root;states.append(self)
            def __enter__(self):
                (self.root/'baseline'/'result.json').write_text(json.dumps({'attempts':[]}))
                return self
            def __exit__(self,*args):pass
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);plan,chrome=fixture_plan(root);pair_mocks(stack)
            stack.enter_context(patch.object(runner.subprocess,'run',return_value=SimpleNamespace(stdout='')))
            stack.enter_context(patch.object(owner,'SupervisedProcessGroup',Supervisor))
            code=runner.execute_pair(plan,root/'pair',chrome,'a'*40,'b'*40)
            result=json.loads((root/'pair'/'result.json').read_bytes())
            self.assertEqual(code,1);self.assertEqual(len(states),1)
            self.assertEqual(result['cleanup_status'],'UNKNOWN')
            self.assertFalse((root/'pair'/'candidate').exists())

    def test_cancel_and_original_error_survive_cleanup_failure(self):
        for original in (KeyboardInterrupt('cancel fixture'),RuntimeError('worker fixture')):
            for residual in (False,True):
                with self.subTest(original=type(original).__name__,residual=residual),tempfile.TemporaryDirectory() as tmp:
                    group=self.fake_group(Path(tmp));record={}
                    # checked_process_group receives the real ownership attrs.
                    class Supervisor:
                        def __init__(self):self.__dict__.update(vars(group))
                        def __enter__(self):return self
                        def __exit__(self,*args):pass
                    stdout=ps_fixture('876544 876543 S\n' if residual else '')
                    with patch.object(cleanup.subprocess,'run',return_value=SimpleNamespace(stdout=stdout)):
                        with self.assertRaises(cleanup.CleanupUnverified if residual else type(original)):
                            with cleanup.checked_process_group(Supervisor(),record):raise original
                    self.assertEqual(record['primary_error']['type'],type(original).__name__)
                    self.assertEqual(record['primary_error']['message'],str(original))
                    self.assertEqual(record['cleanup']['status'],'FAIL' if residual else 'PASS')

    def test_task_owned_child_positive_outer_and_nested(self):
        # Only the child created here is started/stopped by the unchanged owner.
        # No Chrome, no forced failure, and no unrelated process is addressed.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ctx=SimpleNamespace(root=root,cache=root/'cache')
            record={}
            group=owner.SupervisedProcessGroup(ctx,[sys.executable,'-c','pass'],backend_id='pilot_offline_positive')
            with cleanup.checked_process_group(group,record):group.proc.wait(timeout=5)
            self.assertEqual(record['cleanup']['status'],'PASS')
            checks=[];errors=[];job=root/'00-job';job.mkdir()
            class Browser:
                def __init__(self):self.group=None
                def start(self):
                    self.group=owner.SupervisedProcessGroup(ctx,[sys.executable,'-c','import time; time.sleep(30)'],
                                                           backend_id='pilot_offline_browser_fixture')
                    self.group.start()
                def stop(self):
                    group,self.group=self.group,None
                    if group is not None:group.stop()
            gfx=SimpleNamespace(Browser=Browser)
            trace=SimpleNamespace(fail=lambda *args:errors.append(args))
            with cleanup.browser_cleanup_scope(gfx,trace,checks,job/'browser-cleanup.json'):
                browser=gfx.Browser()
                try:browser.start()
                finally:browser.stop()
            self.assertEqual(errors,[]);self.assertEqual([r['status'] for r in checks],['PASS'])
            self.assertEqual(cleanup.nested_postflight(root)['status'],'PASS')


if __name__=='__main__':unittest.main()
