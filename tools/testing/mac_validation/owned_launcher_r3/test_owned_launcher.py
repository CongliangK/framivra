"""Only root-approved resource mocks and two ordinary self-owned processes."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import owned_launcher as launcher


def safe_row(t=0, free=55, swap=None):
    return dict(wall_seconds=t, tree_rss_bytes=launcher.GIB,
                disk_free_bytes=50*launcher.GIB, memory_free_percent=free, vm=swap)


class LauncherBudgetTests(unittest.TestCase):
    def assert_observed_stop_skips_size_scan(self, *, rss=launcher.GIB, disk=50*launcher.GIB, pressure=55):
        runtime=SimpleNamespace(clock=lambda:1.0,remaining=lambda d,c:c,
                                ps_resources=lambda d:[],pressure=lambda d:(pressure,'mock pressure'))
        registry=SimpleNamespace(update=lambda rows:[dict(rss_kib=rss//1024)],
                                 processes={},nested_coverage=lambda:{'status':'PASS'})
        fs=SimpleNamespace(f_bavail=disk,f_frsize=1)
        with tempfile.TemporaryDirectory() as tmp, patch.object(launcher.os,'statvfs',return_value=fs), patch.object(launcher,'logical_bytes') as scan, patch.object(launcher,'cleanup',return_value={'status':'PASS'}) as clean:
            result=launcher.monitor(SimpleNamespace(poll=lambda:None),registry,runtime,Path(tmp),[Path(tmp)],30,0)
        scan.assert_not_called(); clean.assert_called_once()
        self.assertTrue(result['stop_reasons']); self.assertFalse(result['errors'])

    def test_rss_6gib_observation_requests_stop(self):
        row=safe_row(); row['tree_rss_bytes']=6*launcher.GIB
        self.assertIn('tree_rss_at_or_above_6GiB',launcher.BudgetState().observe(row)['stop_reasons'])
        self.assert_observed_stop_skips_size_scan(rss=6*launcher.GIB)

    def test_free_disk_20gib_observation_requests_stop(self):
        row=safe_row(); row['disk_free_bytes']=20*launcher.GIB-1
        self.assertIn('volume_free_below_20GiB',launcher.BudgetState().observe(row)['stop_reasons'])
        self.assert_observed_stop_skips_size_scan(disk=20*launcher.GIB-1)

    def test_total_new_logical_24gib_observation_requests_stop(self):
        self.assertIn('new_logical_bytes_at_or_above_24GiB',launcher.BudgetState().observe(safe_row(),24*launcher.GIB)['stop_reasons'])

    def test_pressure_one_10_or_two_15_samples_requests_stop(self):
        self.assertIn('system_memory_free_observation_floor',launcher.BudgetState().observe(safe_row(free=10))['stop_reasons'])
        budget=launcher.BudgetState()
        self.assertFalse(budget.observe(safe_row(free=15))['stop_reasons'])
        self.assertIn('system_memory_free_observation_floor',budget.observe(safe_row(5,free=15))['stop_reasons'])
        self.assert_observed_stop_skips_size_scan(pressure=10)

    def test_swap_warning_and_frozen_combined_stop_windows(self):
        vm=lambda n:dict(page_size_bytes=16384,swapouts_pages=n)
        budget=launcher.BudgetState(); budget.observe(safe_row(0,swap=vm(0)))
        result=budget.observe(safe_row(5,swap=vm(1)))
        self.assertTrue(result['warnings']); self.assertFalse(result['stop_reasons'])
        result=budget.observe(safe_row(60,free=25,swap=vm(32768)))
        self.assertIn('sustained_system_swapout_and_low_free',result['stop_reasons'])
        budget=launcher.BudgetState()
        for i in range(3):
            self.assertFalse(budget.observe(safe_row(i*5,free=20,swap=vm(i)))['stop_reasons'])
        self.assertIn('sustained_system_swapout_and_low_free',budget.observe(safe_row(15,free=20,swap=vm(3)))['stop_reasons'])

    def test_missing_swap_keeps_unknown_without_forced_stop(self):
        result=launcher.BudgetState().observe(safe_row())
        self.assertEqual(result['swap_status'],'UNKNOWN'); self.assertFalse(result['stop_reasons'])

    def test_sampling_exception_records_failure_and_runs_cleanup(self):
        runtime=SimpleNamespace(clock=lambda:1.0,remaining=lambda d,c:c,
                                ps_resources=lambda d:(_ for _ in ()).throw(OSError('mock sample failure')))
        child=SimpleNamespace(poll=lambda:None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(launcher,'cleanup',return_value={'status':'UNKNOWN'}) as cleanup:
            result=launcher.monitor(child,object(),runtime,Path(tmp),[Path(tmp)],30,0)
        self.assertEqual(len(result['errors']),1); cleanup.assert_called_once()
        self.assertTrue(cleanup.call_args.kwargs['request_stop'])

    def test_remaining_deadline_bounds_probe_sleep_and_wait(self):
        runtime=launcher.Runtime(object())
        with patch.object(runtime,'clock',return_value=100), patch.object(runtime,'run',return_value=SimpleNamespace(stdout='')) as run:
            runtime.query(['/bin/ps'],100.2)
            self.assertAlmostEqual(run.call_args.kwargs['timeout'],.2)
            self.assertAlmostEqual(runtime.remaining(100.1,5),.1)
            with self.assertRaises(TimeoutError): runtime.remaining(100,5)
        late=SimpleNamespace(clock=lambda:31.0)
        with tempfile.TemporaryDirectory() as tmp, patch.object(launcher,'cleanup',return_value={'status':'PASS'}):
            result=launcher.monitor(SimpleNamespace(poll=lambda:0),object(),late,Path(tmp),[Path(tmp)],30,0)
        self.assertIn('run_deadline_reached_or_completion_observed_late',result['stop_reasons'])

    def test_owned_descendant_identity_unavailable_fails_rss_observation(self):
        for previously_observed in (False, True):
            with self.subTest(previously_observed=previously_observed):
                registry=launcher.OwnedRegistry.__new__(launcher.OwnedRegistry)
                registry.owner=SimpleNamespace(proc_starttime=lambda pid:100 if pid == 876543 else None)
                registry.processes={876543:dict(pid=876543,pgid=876543,start_identity=100)}
                if previously_observed:
                    registry.processes[876544]=dict(pid=876544,pgid=876543,start_identity=200)
                rows=[dict(pid=876543,ppid=1,pgid=876543),dict(pid=876544,ppid=876543,pgid=876543)]
                with self.assertRaisesRegex(RuntimeError,'RSS coverage incomplete'):
                    registry.update(rows)

    def test_expired_deadline_skips_precleanup_size_scan(self):
        runtime=SimpleNamespace(clock=lambda:31.0)
        with tempfile.TemporaryDirectory() as tmp, patch.object(launcher,'logical_bytes') as scan, patch.object(launcher,'cleanup',return_value={'status':'PASS'}) as clean:
            result=launcher.monitor(SimpleNamespace(poll=lambda:0),object(),runtime,Path(tmp),[Path(tmp)],30,0)
        scan.assert_not_called(); clean.assert_called_once()
        self.assertTrue(clean.call_args.kwargs['request_stop'])
        self.assertEqual(result['terminal_resource_status'],'SKIPPED_STOP_OR_DEADLINE')


class FakeChild:
    pid=876543
    def __init__(self,wait_timeout=False):
        self.wait_timeout=wait_timeout; self.returncode=None if wait_timeout else 0
    def poll(self): return self.returncode
    def wait(self,timeout):
        if timeout <= 0: raise AssertionError('Wait must have positive bound')
        if self.wait_timeout: raise subprocess.TimeoutExpired('owned mock',timeout)
        return self.returncode


class FakeRegistry:
    root_pid=876543
    def __init__(self,nested='PASS'):
        self.groups={self.root_pid}; self.generations={}; self.project=Path('/ordinary-unused-mock')
        self.nested=nested
    def can_signal(self,pgid): return False
    def read_ledgers(self): pass
    def nested_coverage(self): return {'status':self.nested}


def fake_runtime(observations):
    owner=SimpleNamespace(kill_group=lambda *a,**k:False,_assert_kill_whitelisted=lambda p:None)
    runtime=launcher.Runtime(owner)
    runtime.observe_groups=lambda *a: next(observations)
    return runtime


class LauncherCleanupTests(unittest.TestCase):
    def test_final_wait_timeout_keeps_unreaped_failure(self):
        runtime=fake_runtime(iter([{'live_rows':[]}]*4))
        result=launcher.cleanup(FakeChild(True),FakeRegistry(),runtime,request_stop=True,term_seconds=.01,kill_reap_seconds=.01)
        self.assertEqual(result['status'],'UNKNOWN'); self.assertTrue(result['unreaped_root'])

    def test_root_exit_requires_independent_group_empty_observation(self):
        live={'live_rows':[{'pid':876544,'pgid':876543,'state':'S'}]}
        runtime=fake_runtime(iter([live,{'live_rows':[]},{'live_rows':[]}]))
        result=launcher.cleanup(FakeChild(),FakeRegistry(),runtime,request_stop=False)
        self.assertEqual(result['status'],'UNKNOWN'); self.assertTrue(result['unexpected_live_members_after_success'])

    def test_missing_nested_generation_evidence_remains_unknown(self):
        runtime=fake_runtime(iter([{'live_rows':[]}]*3))
        result=launcher.cleanup(FakeChild(),FakeRegistry('UNKNOWN'),runtime,request_stop=False)
        self.assertEqual(result['status'],'UNKNOWN'); self.assertEqual(result['nested']['status'],'UNKNOWN')

    def test_cleanup_observation_timeout_blocks_next_arm(self):
        runtime=launcher.Runtime(object())
        runtime.observe_groups=lambda *a:(_ for _ in ()).throw(subprocess.TimeoutExpired('owned ps observation',.01))
        result=launcher.cleanup(FakeChild(),FakeRegistry(),runtime,request_stop=True)
        self.assertEqual(result['status'],'UNKNOWN'); self.assertIn('TimeoutExpired',result['error'])

    def test_unavailable_owned_anchor_prevents_nested_signal(self):
        registry=FakeRegistry(); registry.groups.add(876545)
        live={'live_rows':[{'pid':876546,'pgid':876545,'state':'S'}]}
        runtime=fake_runtime(iter([{'live_rows':[]},live,{'live_rows':[]}]))
        with patch.object(launcher.os,'killpg') as kill:
            result=launcher.cleanup(FakeChild(),registry,runtime,request_stop=True)
        kill.assert_not_called()
        self.assertEqual(result['status'],'UNKNOWN')
        self.assertIn(dict(action='no_signal_identity_unknown',pgid=876545),result['steps'])

    def test_signal_revalidates_after_lineage_check(self):
        for sig in (signal.SIGTERM,signal.SIGKILL):
            with self.subTest(sig=sig):
                events=[]
                registry=SimpleNamespace(can_signal=lambda pgid:events.append('identity') or False)
                owner=SimpleNamespace(_assert_kill_whitelisted=lambda pgid:events.append('lineage'))
                runtime=launcher.Runtime(owner)
                with patch.object(launcher.os,'killpg') as kill:
                    result=launcher.signal_owned(registry,runtime,876543,sig,runtime.clock()+2)
                kill.assert_not_called(); self.assertEqual(events,['lineage','identity'])
                self.assertEqual(result['action'],'no_signal_identity_unknown')

    def test_owned_empty_run_directory_uses_complete_context(self):
        registry=FakeRegistry(); run=registry.project/'cache'/'tmp'/'ordinary-owned-run'
        path=run/'.worker_pgid.json'; raw=b'{}'; removed=[]
        registry.generations={'ordinary':dict(path=str(path),run_dir=str(run),
            source_sha256=launcher.hashlib.sha256(raw).hexdigest(),record={'pgid':876545})}
        registry.groups.add(876545)
        runtime=fake_runtime(iter([{'live_rows':[]}]*4))
        def remove(context,directory):
            self.assertEqual((context.root,context.cache,context.out),
                             (registry.project,registry.project/'cache',registry.project/'out'))
            self.assertEqual(directory,run); removed.append(directory); return True
        runtime.owner._remove_run_dir=remove
        with patch.object(Path,'exists',lambda target:target in (path,run) and not removed), patch.object(Path,'read_bytes',return_value=raw):
            result=launcher.cleanup(FakeChild(),registry,runtime,request_stop=False)
        self.assertEqual(removed,[run]); self.assertEqual(result['status'],'PASS',result)


class LauncherOwnedProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Explicit caller-bound reviewed production tree. No repository or system mutation.
        cls.repo=Path(os.environ['FRAMIVRA_VALIDATION_REPO']).absolute()
        sys.path.insert(0,str(cls.repo))
        from vtx import proc_supervisor
        cls.owner=proc_supervisor

    def run_owned(self,ignore_term):
        code="import signal,time; " + ("signal.signal(signal.SIGTERM,signal.SIG_IGN); " if ignore_term else '') + "print('READY',flush=True); time.sleep(30)"
        child=subprocess.Popen([sys.executable,'-B','-c',code],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        registry=None
        with tempfile.TemporaryDirectory() as tmp:
            try:
                registry=launcher.OwnedRegistry(child,self.owner,Path(tmp),0)
                self.assertTrue(select.select([child.stdout],[],[],2)[0],'Owned child readiness not received')
                self.assertEqual(child.stdout.readline().strip(),'READY')
                runtime=launcher.Runtime(self.owner)
                result=launcher.cleanup(child,registry,runtime,request_stop=True,total_seconds=5,term_seconds=.1,kill_reap_seconds=1)
                self.assertEqual(result['status'],'PASS',result)
                self.assertIsNotNone(child.poll()); self.assertEqual(result['final_observation']['live_rows'],[])
                self.assertLess(result['wall_seconds'],5)
                signals=[x['action'] for x in result['steps']]
                self.assertIn('SIGTERM',signals)
                self.assertEqual('SIGKILL' in signals,ignore_term)
                with (Path(__file__).parent/'owned-process-observations.jsonl').open('a') as evidence:
                    evidence.write(json.dumps(dict(test_id=self.id(),root_identity=registry.processes[child.pid],
                        cleanup=result,platform=sys.platform,recorded_at=time.time()))+'\n')
            finally:
                # Emergency cleanup remains restricted to this exact self-created child.
                if child.poll() is None and registry is not None and registry.can_signal(child.pid):
                    launcher.signal_owned(registry,launcher.Runtime(self.owner),child.pid,signal.SIGKILL,time.monotonic()+2)
                try: child.wait(timeout=2)
                except subprocess.TimeoutExpired: pass
                child.stdout.close(); child.stderr.close()

    def test_owned_sleep_timeout_term_reaped_empty_group(self):
        self.run_owned(False)

    def test_owned_ignore_term_escalates_with_bounded_reap(self):
        self.run_owned(True)

if __name__ == '__main__':
    unittest.main()
