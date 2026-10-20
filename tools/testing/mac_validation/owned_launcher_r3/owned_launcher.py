"""Bounded observation/cleanup for one explicitly specified Mac production job.

Standalone validation tool, not a scheduler. Existing producer argv is unchanged.
Only run-spec-owned processes and the supplied fresh project's native ledgers are
eligible for cleanup. Observation budgets are cooperative, not OS resource limits.
"""
import argparse
from collections import deque
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

GIB = 1024 ** 3
MIB = 1024 ** 2
FINAL_TREE = '7d499f03b734cb87c0b4286947b9fb60bd5d303c'
PS_ARGV = ['/bin/ps', '-axo', 'pid=,ppid=,pgid=,stat=,rss=,pcpu=,time=,args=']
CLEAN_PS_ARGV = ['/bin/ps', '-axo', 'pid=,pgid=,stat=']


def dump(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def positive_number(x):
    import math
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0:
        raise ValueError('Invalid nonnegative measurement')
    return x


class BudgetState:
    def __init__(self):
        self.low_pressure_count = 0
        self.swap_growth_count = 0
        self.swap = deque()
        self.last_swap = None

    def observe(self, row, logical_bytes=None):
        rss = positive_number(row['tree_rss_bytes'])
        free = positive_number(row['disk_free_bytes'])
        pressure = positive_number(row['memory_free_percent'])
        now = positive_number(row['wall_seconds'])
        if pressure > 100:
            raise ValueError('Invalid memory free percentage')
        reasons, warnings = [], []
        if rss >= 6 * GIB:
            reasons.append('tree_rss_at_or_above_6GiB')
        if free < 20 * GIB:
            reasons.append('volume_free_below_20GiB')
        if logical_bytes is not None and positive_number(logical_bytes) >= 24 * GIB:
            reasons.append('new_logical_bytes_at_or_above_24GiB')
        self.low_pressure_count = self.low_pressure_count + 1 if pressure <= 15 else 0
        if pressure <= 10 or self.low_pressure_count >= 2:
            reasons.append('system_memory_free_observation_floor')
        vm = row.get('vm')
        swap_status = 'UNKNOWN'
        if isinstance(vm, dict) and type(vm.get('page_size_bytes')) is int and vm['page_size_bytes'] > 0 and type(vm.get('swapouts_pages')) is int and vm['swapouts_pages'] >= 0:
            current = (now, vm['swapouts_pages'], vm['page_size_bytes'])
            previous = self.last_swap
            if previous and (current[0] <= previous[0] or current[1] < previous[1] or current[2] != previous[2]):
                self.swap.clear(); self.swap_growth_count = 0
                warnings.append('swap_counter_reset_or_clock_change')
            elif previous:
                delta = current[1] - previous[1]
                self.swap_growth_count = self.swap_growth_count + 1 if delta > 0 else 0
                if delta > 0:
                    warnings.append('system_swapout_increased_not_task_attributed')
                swap_status = 'OBSERVED_SYSTEM_IO'
            self.swap.append(current)
            while self.swap and now - self.swap[0][0] > 60:
                self.swap.popleft()
            if self.swap:
                page_equivalent_bytes = (current[1] - self.swap[0][1]) * current[2]
                if (page_equivalent_bytes >= 512 * MIB and pressure <= 25) or (self.swap_growth_count >= 3 and pressure <= 20):
                    reasons.append('sustained_system_swapout_and_low_free')
            self.last_swap = current
        else:
            self.swap.clear(); self.last_swap = None; self.swap_growth_count = 0
        return dict(stop_reasons=reasons, warnings=warnings, swap_status=swap_status)


class Runtime:
    clock = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)
    run = staticmethod(subprocess.run)

    def __init__(self, owner):
        self.owner = owner

    def remaining(self, deadline, ceiling):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError('Observation deadline reached')
        return min(ceiling, remaining)

    def query(self, argv, deadline, ceiling=2):
        return self.run(argv, check=True, capture_output=True, text=True,
                        timeout=self.remaining(deadline, ceiling))

    def ps_resources(self, deadline):
        result = self.query(PS_ARGV, deadline)
        rows = []
        for line in result.stdout.splitlines():
            f = line.split(None, 7)
            if len(f) != 8:
                raise ValueError('Malformed ps resource observation')
            pid, ppid, pgid, state, rss, pcpu, cpu_time, command = f
            row = dict(pid=int(pid), ppid=int(ppid), pgid=int(pgid), state=state,
                       rss_kib=int(rss), cpu_percent=float(pcpu), cpu_time=cpu_time,
                       command=command)
            if row['pid'] <= 0 or row['ppid'] < 0 or row['pgid'] < 0 or not re.fullmatch(r'[A-Za-z?][A-Za-z0-9+<>=_?-]*', state):
                raise ValueError('Invalid ps process fields')
            positive_number(row['rss_kib']); positive_number(row['cpu_percent'])
            rows.append(row)
        self._valid_inventory(rows)
        return rows

    @staticmethod
    def _valid_inventory(rows):
        if not rows or len({r['pid'] for r in rows}) != len(rows):
            raise ValueError('Empty or duplicate ps process inventory')
        mine = [r for r in rows if r['pid'] == os.getpid()]
        if len(mine) != 1 or mine[0]['pgid'] != os.getpgid(0):
            raise ValueError('ps observer is absent or inconsistent')

    def observe_groups(self, groups, deadline):
        result = self.query(CLEAN_PS_ARGV, deadline)
        rows = []; matching_raw_lines = []
        for line in result.stdout.splitlines():
            f = line.split()
            if len(f) != 3:
                raise ValueError('Malformed cleanup process inventory')
            row = dict(pid=int(f[0]), pgid=int(f[1]), state=f[2])
            if row['pid'] <= 0 or row['pgid'] < 0 or not re.fullmatch(r'[A-Za-z?][A-Za-z0-9+<>=_?-]*', row['state']):
                raise ValueError('Invalid cleanup process fields')
            rows.append(row)
            if row['pgid'] in groups:
                matching_raw_lines.append(line)
        self._valid_inventory(rows)
        retained = [r for r in rows if r['pgid'] in groups]
        return dict(argv=CLEAN_PS_ARGV, exit_code=result.returncode,
                    matching_raw_lines=matching_raw_lines,
                    snapshot_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
                    matching_rows=retained, live_rows=[r for r in retained if not r['state'].startswith('Z')])

    def pressure(self, deadline):
        result = self.query(['/usr/bin/memory_pressure', '-Q'], deadline)
        match = re.findall(r'System-wide memory free percentage:\s*(\d+)%', result.stdout)
        if len(match) != 1 or not 0 <= int(match[0]) <= 100:
            raise ValueError('Memory free observation missing or ambiguous')
        return int(match[0]), result.stdout

    def vm(self, deadline):
        from resource_summary import parse_vm_stat_snapshot
        result = self.query(['/usr/bin/vm_stat'], deadline)
        return parse_vm_stat_snapshot(result.stdout), result.stdout


class OwnedRegistry:
    def __init__(self, child, owner, project, expected_nested_generations):
        self.owner = owner
        self.project = Path(project)
        self.root_pid = child.pid
        self.expected_nested_generations = expected_nested_generations
        self.boot = owner.boot_id()
        start = owner.proc_starttime(child.pid)
        if start is None or self.boot is None or os.getpgid(child.pid) != child.pid:
            raise RuntimeError('Owned root kernel identity unavailable')
        self.processes = {child.pid: dict(pid=child.pid, start_identity=start, pgid=child.pid)}
        self.groups = {child.pid}
        self.generations = {}

    def update(self, rows):
        by_pid = {r['pid']: r for r in rows}
        live_owned = set()
        for pid, record in list(self.processes.items()):
            row = by_pid.get(pid)
            if row is None:
                continue
            current_start = self.owner.proc_starttime(pid)
            if current_start is None:
                raise RuntimeError('Visible retained process identity unavailable; RSS coverage incomplete')
            if current_start == record['start_identity']:
                if row['pgid'] != record['pgid']:
                    raise RuntimeError('Owned process changed group; ownership requires review')
                live_owned.add(pid)
        changed = True
        while changed:
            changed = False
            for row in rows:
                if row['pid'] not in live_owned and row['ppid'] in live_owned:
                    start = self.owner.proc_starttime(row['pid'])
                    if start is None:
                        raise RuntimeError('Visible owned descendant identity unavailable; RSS coverage incomplete')
                    self.processes[row['pid']] = dict(pid=row['pid'], start_identity=start, pgid=row['pgid'])
                    live_owned.add(row['pid']); changed = True
        owned = []
        for row in rows:
            if row['pid'] in live_owned:
                copy = dict(row, start_identity=self.processes[row['pid']]['start_identity'])
                owned.append(copy); self.groups.add(row['pgid'])
        self.read_ledgers()
        # Arguments classify an ALREADY-owned process; they never confer ownership.
        for generation in self.generations.values():
            host = generation['record']['host_pid']
            for row in owned:
                match = re.search(r'(?:^|\s)-m\s+vtx\.proc_supervisor\s+guard\s+(\d+)\s+\d+(?:\s+\d+)?\s*$', row['command'])
                if match and int(match[1]) == generation['record']['pgid'] and row['ppid'] == host and row['pgid'] == row['pid']:
                    generation['guard'] = dict(pid=row['pid'], pgid=row['pgid'], start_identity=row['start_identity'])
        return owned

    def read_ledgers(self):
        tmp = self.project / 'cache' / 'tmp'
        if not tmp.exists():
            return
        for path in tmp.glob('*/' + self.owner.WORKER_LEDGER):
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                continue  # Producer normally removes completed native ledgers.
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError('Native ledger is not an object')
            host = self.processes.get(record.get('host_pid'))
            if host is None or host['start_identity'] != record.get('host_start_time_ticks') or record.get('boot_id') != self.boot:
                raise RuntimeError('Native ledger not attributable to this invocation')
            pgid, start = record.get('pgid'), record.get('start_time_ticks')
            if type(pgid) is not int or pgid <= 1 or type(start) is not int:
                raise RuntimeError('Native ledger identity incomplete')
            key = str(pgid) + ':' + str(start)
            self.groups.add(pgid)
            self.generations.setdefault(key, dict(path=str(path), run_dir=str(path.parent),
                record=record, source_sha256=hashlib.sha256(raw).hexdigest(),
                raw_utf8=raw.decode('utf-8'), first_observed_monotonic=time.monotonic(),
                first_observed_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), guard=None))

    def can_signal(self, pgid):
        # At least one retained exact live identity must still anchor this group.
        for pid, record in self.processes.items():
            if record['pgid'] == pgid and self.owner.proc_starttime(pid) == record['start_identity']:
                try:
                    if os.getpgid(pid) == pgid:
                        return True
                except ProcessLookupError:
                    pass
        return False

    def nested_coverage(self):
        count = len(self.generations)
        expected = self.expected_nested_generations
        missing = [key for key, g in self.generations.items() if g['guard'] is None]
        return dict(status='PASS' if count == expected and not missing else 'UNKNOWN',
                    expected_generations=expected, observed_generations=count, missing_guard_identity=missing,
                    generations=list(self.generations.values()))


def signal_owned(registry, runtime, pgid, sig, deadline):
    """Keep the native lineage deny-list, then recheck exact ownership immediately."""
    runtime.remaining(deadline, 1)
    runtime.owner._assert_kill_whitelisted(pgid)
    runtime.remaining(deadline, 1)
    if not registry.can_signal(pgid):
        return dict(action='no_signal_identity_unknown', pgid=pgid)
    try:
        os.killpg(pgid, sig)
        return dict(action=signal.Signals(sig).name, pgid=pgid)
    except ProcessLookupError:
        return dict(action='group_gone_before_signal', pgid=pgid)


def cleanup(child, registry, runtime, *, request_stop, total_seconds=60, term_seconds=10, kill_reap_seconds=10):
    start = runtime.clock(); deadline = start + total_seconds
    out = dict(status='UNKNOWN', steps=[], budget_seconds=total_seconds,
               budget_kind='cooperative observation; existing owner identity/lineage queries have their own bounded calls and may overrun the remaining outer budget')
    groups = registry.groups
    try:
        try:
            before = runtime.observe_groups(groups, deadline)
            out['initial_observation'] = before
        except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
            before = dict(live_rows=[])
            out['initial_observation_error'] = type(error).__name__ + ': ' + str(error)
            request_stop = True
        needs_stop = request_stop or child.poll() is None or bool(before['live_rows'])
        if not request_stop and before['live_rows']:
            out['unexpected_live_members_after_success'] = True
        root_live = child.poll() is None or any(r['pgid'] == registry.root_pid for r in before['live_rows'])
        if needs_stop and root_live:
            out['steps'].append(signal_owned(registry, runtime, registry.root_pid, signal.SIGTERM, deadline))
        try:
            child.wait(timeout=runtime.remaining(deadline, term_seconds))
        except subprocess.TimeoutExpired:
            out['steps'].append(signal_owned(registry, runtime, registry.root_pid, signal.SIGKILL, deadline))
            try:
                child.wait(timeout=runtime.remaining(deadline, kill_reap_seconds))
            except subprocess.TimeoutExpired:
                out['unreaped_root'] = True
        runtime.remaining(deadline, 1)
        registry.read_ledgers()
        # Retained task-owned guard/non-browser groups may outlive the top-level parent.
        observed = runtime.observe_groups(groups, deadline)
        for pgid in sorted({r['pgid'] for r in observed['live_rows']}):
            out['steps'].append(signal_owned(registry, runtime, pgid, signal.SIGKILL, deadline))
        # At most the remaining independent-observation allocation, never unbounded polling.
        observation_deadline = min(deadline, runtime.clock() + 40)
        while True:
            observed = runtime.observe_groups(groups, observation_deadline)
            out['final_observation'] = observed
            if not observed['live_rows']:
                break
            runtime.sleep(runtime.remaining(observation_deadline, .1))
        # Native reaper can signal on an absent leader. Do not call that branch:
        # only remove this invocation's retained run directories after empty proof.
        for generation in registry.generations.values():
            runtime.remaining(deadline, 1)
            path = Path(generation['path'])
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest() != generation['source_sha256']:
                    raise RuntimeError('Retained native ledger changed')
                observation = runtime.observe_groups({generation['record']['pgid']}, deadline)
                if observation['live_rows']:
                    raise RuntimeError('Owned group became live before run-directory removal')
                context = SimpleNamespace(root=registry.project, cache=registry.project/'cache',
                                          out=registry.project/'out')
                removed = runtime.owner._remove_run_dir(context, path.parent)
                out['steps'].append(dict(action='owner.remove_owned_run_dir', path=str(path.parent), result=removed))
        out['root_returncode'] = child.poll()
        out['nested'] = registry.nested_coverage()
        remaining_dirs = [g['run_dir'] for g in registry.generations.values() if Path(g['run_dir']).exists()]
        out['remaining_owned_run_dirs'] = remaining_dirs
        out['status'] = ('PASS' if out['root_returncode'] is not None and not out.get('unreaped_root')
                         and out['nested']['status'] == 'PASS' and not remaining_dirs
                         and not any(s['action'] == 'no_signal_identity_unknown' for s in out['steps'])
                         and not out.get('unexpected_live_members_after_success')
                         and not out.get('initial_observation_error') else 'UNKNOWN')
    except Exception as error:
        out['error'] = type(error).__name__ + ': ' + str(error)
    out['wall_seconds'] = runtime.clock() - start
    out['outer_budget_overrun'] = out['wall_seconds'] > total_seconds
    if out['outer_budget_overrun']:
        out['status'] = 'UNKNOWN'
    return out


def logical_bytes(roots, runtime, deadline):
    total = 0
    def failed_walk(error):
        raise error
    for root in roots:
        runtime.remaining(deadline, 1)
        for directory, _, files in os.walk(root, onerror=failed_walk):
            runtime.remaining(deadline, 1)
            for name in files:
                runtime.remaining(deadline, 1)
                try:
                    total += (Path(directory)/name).stat().st_size
                except FileNotFoundError:
                    pass  # Concurrently removed task-owned temporary file.
    return total


def monitor(child, registry, runtime, output, roots, deadline, started, *, sample_seconds=5, use_vm_stat=False):
    budget = BudgetState(); next_size = started; total = None
    status = dict(stop_reasons=[], errors=[], samples=0,
                  final_new_logical_bytes=None, final_disk_free_bytes=None,
                  terminal_resource_status='UNKNOWN')
    vm_unavailable = None
    with (output/'resource-samples.jsonl').open('x') as stream:
        try:
            while child.poll() is None:
                runtime.remaining(deadline, 1)
                before = runtime.clock()
                rows = runtime.ps_resources(deadline); owned = registry.update(rows)
                if not owned:
                    raise RuntimeError('Owned resource observation unavailable')
                pressure, pressure_raw = runtime.pressure(deadline)
                stat = os.statvfs(str(roots[0]))
                row = dict(wall_seconds=before-started, utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                           disk_free_bytes=stat.f_bavail*stat.f_frsize, processes=owned,
                           tree_rss_kib=sum(p['rss_kib'] for p in owned), memory_free_percent=pressure,
                           memory_pressure_raw=pressure_raw)
                row['tree_rss_bytes'] = row['tree_rss_kib']*1024
                if use_vm_stat and vm_unavailable is None:
                    try:
                        row['vm'], row['vm_raw'] = runtime.vm(deadline)
                    except (OSError,ValueError,subprocess.SubprocessError) as error:
                        vm_unavailable = type(error).__name__ + ': ' + str(error)
                if vm_unavailable is not None:
                    row['vm'] = None; row['vm_unavailable_reason'] = vm_unavailable
                decision = budget.observe(row, total)
                if not decision['stop_reasons'] and before >= next_size:
                    total = logical_bytes(roots, runtime, deadline); next_size = runtime.clock()+60
                    row['new_logical_bytes'] = total
                    if total >= 24 * GIB:
                        decision['stop_reasons'].append('new_logical_bytes_at_or_above_24GiB')
                row.update(decision, sample_duration_s=runtime.clock()-before)
                stream.write(json.dumps(row,allow_nan=False)+'\n'); stream.flush()
                dump(output/'ownership.json', dict(processes=list(registry.processes.values()), nested=registry.nested_coverage()))
                status['samples'] += 1
                if decision['stop_reasons']:
                    status['stop_reasons'] = decision['stop_reasons']; break
                remaining = runtime.remaining(deadline, sample_seconds)
                try:
                    child.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    pass
            if runtime.clock() >= deadline:
                status['stop_reasons'].append('run_deadline_reached_or_completion_observed_late')
            if status['stop_reasons']:
                status['terminal_resource_status'] = 'SKIPPED_STOP_OR_DEADLINE'
            else:
                status['final_new_logical_bytes'] = logical_bytes(roots, runtime, deadline)
                runtime.remaining(deadline, 1)
                fs = os.statvfs(str(roots[0]))
                runtime.remaining(deadline, 1)
                status['final_disk_free_bytes'] = fs.f_bavail * fs.f_frsize
                status['terminal_resource_status'] = 'OBSERVED'
                if status['final_new_logical_bytes'] >= 24 * GIB:
                    status['stop_reasons'].append('new_logical_bytes_at_or_above_24GiB')
                if status['final_disk_free_bytes'] < 20 * GIB:
                    status['stop_reasons'].append('volume_free_below_20GiB')
        except BaseException as error:
            status['errors'].append(type(error).__name__+': '+str(error))
            status['stop_reasons'].append('deadline_or_resource_observation_failed')
        finally:
            status['observed_run_wall_seconds_before_cleanup'] = runtime.clock() - started
            status['cleanup'] = cleanup(child, registry, runtime,
                request_stop=bool(status['stop_reasons'] or status['errors']))
    return status


def load_spec(path):
    spec = json.loads(Path(path).read_bytes())
    required = ('repo','expected_commit','expected_tree','project','evidence_dir','environment_file','argv','new_output_roots','expected_nested_generations','run_deadline_seconds')
    if not all(key in spec for key in required):
        raise ValueError('Run-spec fields incomplete')
    if not 0 < positive_number(spec['run_deadline_seconds']) <= 7200:
        raise ValueError('Explicit run deadline must be positive and no more than 7200 seconds')
    if spec['expected_tree'] != FINAL_TREE:
        raise ValueError('Run-spec is not the root-frozen production tree')
    if type(spec['expected_nested_generations']) is not int or spec['expected_nested_generations'] < 0:
        raise ValueError('Explicit expected browser generation count required')
    if type(spec.get('use_vm_stat', False)) is not bool:
        raise ValueError('use_vm_stat must be an explicit boolean when supplied')
    if not isinstance(spec['argv'], list) or not spec['argv'] or not all(isinstance(x,str) for x in spec['argv']):
        raise ValueError('Explicit argv required')
    roots = [Path(p).absolute() for p in spec['new_output_roots']]
    if not roots or any(a == b or a in b.parents or b in a.parents for i,a in enumerate(roots) for b in roots[i+1:]):
        raise ValueError('Explicit disjoint new output roots required')
    for field in ('project','evidence_dir'):
        value = Path(spec[field]).absolute()
        if not any(value == root or root in value.parents for root in roots):
            raise ValueError('Project and evidence must be included in logical-byte accounting')
    spec['new_output_roots'] = [str(p) for p in roots]
    return spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True)
    args = parser.parse_args()
    spec = load_spec(args.spec)
    if sys.platform != 'darwin':
        raise RuntimeError('Production launcher is Mac-only; Linux qualification uses explicit self-owned tests')
    repo = Path(spec['repo']).absolute()
    identity = subprocess.run(['git','-C',str(repo),'rev-parse','HEAD','HEAD^{tree}'],check=True,capture_output=True,text=True,timeout=10).stdout.split()
    clean = subprocess.run(['git','-C',str(repo),'status','--porcelain','--untracked-files=no'],check=True,capture_output=True,text=True,timeout=10).stdout
    if identity != [spec['expected_commit'],spec['expected_tree']] or clean:
        raise RuntimeError('Frozen tracked source identity is not clean and exact')
    sys.path.insert(0,str(repo))
    owner = importlib.import_module('vtx.proc_supervisor')
    if Path(owner.__file__).absolute() != repo/'vtx'/'proc_supervisor.py' or not owner.supervision_available():
        raise RuntimeError('Frozen native process supervision unavailable')
    project=Path(spec['project'])
    if (project/'cache').exists() or (project/'out').exists():
        raise RuntimeError('Run-spec project cache/out must be fresh before a production arm')
    output=Path(spec['evidence_dir']); output.mkdir(parents=True,exist_ok=False)
    env = json.loads(Path(spec['environment_file']).read_bytes())
    if not isinstance(env,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in env.items()):
        raise ValueError('Explicit string environment mapping required')
    runtime = Runtime(owner); preflight_deadline=runtime.clock()+15
    pressure, pressure_raw=runtime.pressure(preflight_deadline)
    roots=[Path(p) for p in spec['new_output_roots']]
    used=logical_bytes(roots,runtime,preflight_deadline)
    runtime.remaining(preflight_deadline, 1)
    fs=os.statvfs(str(roots[0])); free=fs.f_bavail*fs.f_frsize
    runtime.remaining(preflight_deadline, 1)
    if used >= 24*GIB or free < 20*GIB + (24*GIB-used) or pressure <= 15:
        raise RuntimeError('Prelaunch disk or memory reserve observation failed')
    dump(output/'preflight.json',dict(commit=identity[0],tree=identity[1],new_logical_bytes=used,disk_free_bytes=free,
        memory_free_percent=pressure,memory_pressure_raw=pressure_raw,environment_sha256=hashlib.sha256(Path(spec['environment_file']).read_bytes()).hexdigest(),
        tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),run_spec=spec))
    started=runtime.clock(); deadline=started+spec['run_deadline_seconds']
    child=None; registry=None; result=dict(state='STARTING')
    def cancelled(signum, frame):
        raise KeyboardInterrupt('Launcher cancellation signal ' + str(signum))
    signal.signal(signal.SIGTERM, cancelled)
    with (output/'stdout.log').open('xb') as stdout,(output/'stderr-time-l.log').open('xb') as stderr:
        try:
            child=subprocess.Popen(spec['argv'],cwd=repo,env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True)
            registry=OwnedRegistry(child,owner,spec['project'],spec['expected_nested_generations'])
            dump(output/'status.json',dict(state='RUNNING',root_pid=child.pid,root_identity=registry.processes[child.pid],argv=spec['argv']))
            result=monitor(child,registry,runtime,output,roots,deadline,started,use_vm_stat=spec.get('use_vm_stat',False))
        except BaseException as error:
            result=dict(state='FAILED',primary_error=type(error).__name__+': '+str(error))
            if child is not None and registry is not None:
                result['cleanup']=cleanup(child,registry,runtime,request_stop=True)
            else:
                result['cleanup']=dict(status='UNKNOWN',reason='No verified root identity; no signal sent')
        result['exit']=child.poll() if child else None
        result['root_pid']=child.pid if child else None
        result['root_identity']=registry.processes[child.pid] if registry else None
        result['wall_seconds_with_cleanup']=runtime.clock()-started
        result['state']='PASS' if result.get('exit') == 0 and not result.get('stop_reasons') and not result.get('errors') and not result.get('primary_error') and result.get('cleanup',{}).get('status')=='PASS' else 'FAILED'
        result['next_arm_allowed']=result['state']=='PASS'
        dump(output/'status.json',result)
    return 0 if result['state']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
