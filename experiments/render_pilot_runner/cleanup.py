"""Postflight checks for groups created by the existing production supervisor.

No process is discovered by name, signalled, or reparented here. Ownership and
cleanup stay with SupervisedProcessGroup and its guard. Keep actual objects before
stop clears them; absence of a ledger is never process-cleanup evidence.
"""
from contextlib import contextmanager
import os
import json
import time
import subprocess
import hashlib
import re
from pathlib import Path
from .contract import require, remaining_timeout, clock_now

OBSERVATION_SECONDS=30.0


class CleanupUnverified(RuntimeError):
    pass


def preserve_group(group):
    return {'pgid':getattr(group,'pgid',None),'proc':getattr(group,'proc',None),
            'guard':getattr(group,'guard_proc',None),
            'run_dir':getattr(group,'run_dir',None),'profile_dir':getattr(group,'profile_dir',None)}


def owned_process_snapshot(pgids, *, deadline=None, observation_deadline=None):
    """One bounded, strictly parsed PID/PGID/state observation; never swallow gaps.

    /bin/ps has the same explicit columns on Linux and Darwin. It supplies group
    membership only, not permission to signal or proof of process identity. All
    IDs being tested were retained from this task's existing supervisor. Failed,
    empty, malformed or inconsistent snapshots remain UNKNOWN at the caller.
    """
    require(all(type(p) is int and p>1 for p in pgids),'Invalid retained group identity')
    argv=['/bin/ps','-axo','pid=,pgid=,stat=']
    timeout=remaining_timeout(observation_deadline,10)
    if deadline is not None and deadline>clock_now():
        timeout=min(timeout,remaining_timeout(deadline,10))
    completed=subprocess.run(argv,check=True,capture_output=True,text=True,timeout=timeout)
    rows=[]
    for line in completed.stdout.splitlines():
        fields=line.split()
        require(len(fields)==3 and fields[0].isdigit() and fields[1].isdigit(),
                'Malformed process snapshot row')
        pid,pgid=int(fields[0]),int(fields[1]);state=fields[2]
        require(pid>0 and pgid>=0 and re.fullmatch(r'[A-Za-z?][A-Za-z0-9+<>=_?-]*',state),
                'Unknown process snapshot fields')
        rows.append({'pid':pid,'pgid':pgid,'state':state})
    require(rows and len({r['pid'] for r in rows})==len(rows),'Empty or duplicate process snapshot')
    mine=[r for r in rows if r['pid']==os.getpid()]
    require(len(mine)==1 and mine[0]['pgid']==os.getpgid(0) and mine[0]['state'][0] in 'RSDTtXIWUKP',
            'Process snapshot does not observe the calling process consistently')
    matching=[r for r in rows if r['pgid'] in pgids]
    require(all(r['state'][0] in 'RSDTtZXIWUKP' for r in matching),
            'Unknown state in a retained process group')
    return {'command':argv,'timeout_seconds':timeout,'enumerated_pids':len(rows),
            'snapshot_sha256':hashlib.sha256(completed.stdout.encode()).hexdigest(),
            'matching_rows':matching,
            'live_by_pgid':{str(p):[r['pid'] for r in matching if r['pgid']==p and not r['state'].startswith('Z')]
                            for p in pgids}}


def verify_group_stopped(saved, *, deadline=None, observation_deadline=None):
    """Read-only postflight; nonempty and unknown results both fail closed."""
    result={'status':'UNKNOWN','pgid':saved.get('pgid') if saved else None}
    try:
        require(saved is not None,'No captured group ownership')
        pgid,proc,guard=saved['pgid'],saved['proc'],saved['guard']
        require(type(pgid) is int and pgid>1 and proc is not None and proc.pid==pgid,
                'Missing or inconsistent owned process-group identity')
        result['process_returncode']=proc.poll()
        if result['process_returncode'] is None:result['status']='FAIL'
        require(result['process_returncode'] is not None,'Owned worker is still running')
        require(guard is not None and type(guard.pid) is int and guard.pid>1,'Guard identity unavailable')
        result['guard_pid']=guard.pid
        result['guard_returncode']=guard.poll()
        if result['guard_returncode'] is None:result['status']='FAIL'
        require(result['guard_returncode'] is not None,'Owned guard has not exited')
        snapshot=owned_process_snapshot([pgid,guard.pid],deadline=deadline,observation_deadline=observation_deadline)
        result['observation']=snapshot
        result['guard_live_members']=snapshot['live_by_pgid'][str(guard.pid)]
        if result['guard_live_members']:
            result['status']='FAIL'
            raise CleanupUnverified('Owned guard group still has live members')
        live=snapshot['live_by_pgid'][str(pgid)]
        result['live_members']=live
        if live:
            result['status']='FAIL'
            raise CleanupUnverified('Owned process group still has live members')
        for key in ('run_dir','profile_dir'):
            path=saved[key]
            require(isinstance(path,Path),'Missing '+key+' ownership')
            result[key]=str(path)
            if path.exists() or path.is_symlink():
                result['status']='FAIL'
                raise CleanupUnverified('Owned '+key+' remains after stop')
        result['status']='PASS'
    except (OSError,ValueError,RuntimeError,AttributeError,TypeError,subprocess.SubprocessError) as error:
        result['error']=type(error).__name__+': '+str(error)
    return result


@contextmanager
def checked_process_group(group,record, *, deadline=None):
    saved=None
    cleanup_start=None
    try:
        with group:
            saved=preserve_group(group)
            try:yield group
            except BaseException as error:
                record['primary_error']={'type':type(error).__name__,'message':str(error),
                                         'cancelled':isinstance(error,KeyboardInterrupt)}
                raise
            finally:cleanup_start=clock_now()
    except BaseException as error:
        record['scope_error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        observation_deadline=(cleanup_start if cleanup_start is not None else clock_now())+OBSERVATION_SECONDS
        record['cleanup']=verify_group_stopped(saved,deadline=deadline,observation_deadline=observation_deadline)
        record['cleanup']['observation_deadline_monotonic']=observation_deadline
        record['cleanup']['observation_budget_seconds']=OBSERVATION_SECONDS
        if cleanup_start is not None:
            record['cleanup']['wall_seconds']=clock_now()-cleanup_start
        if record['cleanup']['status']!='PASS':
            raise CleanupUnverified(record['cleanup'].get('error','Owned cleanup unverified'))


@contextmanager
def browser_cleanup_scope(gfx,trace,observations,evidence_path, *, deadline=None):
    """Wrap only the dedicated experiment Browser; invoke its original stop."""
    from .runner import write_json
    original=gfx.Browser
    def save(state,group=None,cleanup=None):
        owned=None
        if group is not None:
            from vtx import proc_supervisor as owner
            snap=preserve_group(group)
            guard=snap['guard']
            owned={'pgid':snap['pgid'],'guard_pid':getattr(guard,'pid',None),
                   'guard_starttime':owner.proc_starttime(guard.pid) if guard else None,
                   'run_dir':str(snap['run_dir']) if snap['run_dir'] else None,
                   'profile_dir':str(snap['profile_dir']) if snap['profile_dir'] else None}
        if owned is None and Path(evidence_path).is_file():
            owned=json.loads(Path(evidence_path).read_bytes()).get('owned')
        write_json(evidence_path,{'schema':'pilot-browser-cleanup/1','state':state,'owned':owned,'cleanup':cleanup})
    class CheckedBrowser(original):
        def start(self):
            save('STARTING')
            try:
                result=super().start()
                save('ACTIVE',getattr(self,'group',None))
                return result
            except BaseException:
                save('START_FAILED',getattr(self,'group',None))
                raise
        def stop(self):
            group=getattr(self,'group',None)
            if group is None:
                return super().stop()  # construction failed before a group existed
            saved=preserve_group(group)
            save('STOPPING',group)
            stop_error=None
            cleanup_start=clock_now()
            try:
                return super().stop()
            except BaseException as error:
                stop_error={'type':type(error).__name__,'message':str(error)}
                trace.fail('browser-stop',error)
                raise
            finally:
                result=verify_group_stopped(saved,deadline=deadline,
                    observation_deadline=cleanup_start+OBSERVATION_SECONDS)
                result['wall_seconds']=clock_now()-cleanup_start
                if stop_error is not None:result['original_stop_error']=stop_error
                observations.append(result)
                save(result['status'],cleanup=result)
                if result['status']!='PASS':
                    error=CleanupUnverified(result.get('error','Browser cleanup unverified'))
                    trace.fail('browser-group-cleanup',error)
                    raise error
    gfx.Browser=CheckedBrowser
    try:yield
    finally:gfx.Browser=original


def nested_postflight(arm_root, *, deadline=None, observation_deadline=None):
    """Inspect only this arm's owned journals/cache; never infer empty from exit.

    An interrupted STARTING record can lack a guard identity. Retain UNKNOWN in
    that case, even if the production orphan reaper removes a residual directory.
    """
    from types import SimpleNamespace
    from vtx import proc_supervisor as owner
    root=Path(arm_root)
    if observation_deadline is None:observation_deadline=clock_now()+OBSERVATION_SECONDS
    report={'status':'UNKNOWN','journals':[],'orphan_reap':None,
            'observation_deadline_monotonic':observation_deadline}
    try:
        journals=sorted(root.glob('*/browser-cleanup.json'))
        if not journals:
            # No browser attempt is a known safe boundary only when the child's
            # durable attempt list is present and empty.
            arm=json.loads((root/'result.json').read_bytes())
            require(arm.get('attempts')==[],'Browser cleanup journal missing')
            report.update(status='PASS',browser_state='NOT_STARTED')
            return report
        pending=[]
        for path in journals:
            row=json.loads(path.read_bytes())
            report['journals'].append({'path':str(path),'record':row,'status':'UNKNOWN'})
            if row.get('state')!='PASS':pending.append(row)
        if pending:
            # This is the existing identity/lineage-checked reaper, limited to
            # the arm cache created empty by this invocation. Preserve journals
            # before it can remove the production ledgers.
            cache=root/'cache'
            ledgers=[]
            for path in sorted((cache/'tmp').glob('*/'+owner.WORKER_LEDGER)):
                ledgers.append({'path':str(path),'record':json.loads(path.read_bytes())})
            report['owned_ledgers_before_reap']=ledgers
            report['orphan_reap']=owner.reap_orphan_workers(SimpleNamespace(root=root,cache=cache))
        for item in report['journals']:
            saved=item['record'].get('owned')
            require(isinstance(saved,dict),'Interrupted browser start has unknown ownership')
            pgid,guard=saved.get('pgid'),saved.get('guard_pid')
            require(type(pgid) is int and pgid>1 and type(guard) is int and guard>1
                    and type(saved.get('guard_starttime')) is int,'Incomplete owned browser/guard identity')
            # The guard exits after the dead arm closes its pipe. Observe only
            # that exact identity for at most three seconds, without signalling.
            until=min(clock_now()+3.0,observation_deadline)
            if deadline is not None and deadline>clock_now():until=min(until,deadline)
            while owner.proc_starttime(guard)==saved['guard_starttime'] and clock_now()<until:
                time.sleep(.02)
            if owner.proc_starttime(guard)==saved['guard_starttime']:
                item['status']='FAIL'
                raise CleanupUnverified('Owned browser guard still running')
            snapshot=owned_process_snapshot([pgid,guard],deadline=deadline,observation_deadline=observation_deadline)
            item['observation']=snapshot
            guard_live=snapshot['live_by_pgid'][str(guard)]
            item['guard_live_members']=guard_live
            require(not guard_live,'Owned browser guard group still has live members')
            live=snapshot['live_by_pgid'][str(pgid)]
            item['live_members']=live
            require(not live,'Owned browser group still has live members')
            for key in ('run_dir','profile_dir'):
                require(isinstance(saved.get(key),str),'Missing nested '+key)
                path=Path(saved[key])
                require(root in path.parents,'Nested path is outside this arm')
                if path.exists() or path.is_symlink():
                    item['status']='FAIL'
                    raise CleanupUnverified('Nested '+key+' remains')
            item['status']='PASS'
        report['status']='PASS'
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,subprocess.SubprocessError) as error:
        report['error']=type(error).__name__+': '+str(error)
        if any(item['status']=='FAIL' or item.get('live_members') or item.get('guard_live_members')
               for item in report['journals']):report['status']='FAIL'
    return report
