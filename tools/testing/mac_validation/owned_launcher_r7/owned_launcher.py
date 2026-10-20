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


class ResourceSnapshot(list):
    """Parsed inventory plus query provenance; raw lines are filtered before output."""
    def __init__(self, rows, query_evidence, raw_by_pid):
        super().__init__(rows)
        self.query_evidence = query_evidence
        self.raw_by_pid = raw_by_pid


class ResourceIdentityUnavailable(RuntimeError):
    def __init__(self, pid, retained, verified_processes, before, after):
        label = 'retained process' if retained else 'owned descendant'
        super().__init__('Visible ' + label + ' identity unavailable; RSS coverage incomplete')
        self.pid = pid
        self.verified_processes = {key:dict(value) for key,value in verified_processes.items()}
        self.verified_pids = set(self.verified_processes)
        self.probe = dict(pid=pid, retained_identity=retained, returned_none=True,
                          started_monotonic=before, finished_monotonic=after)


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
        evidence = dict(argv=list(PS_ARGV), deadline_monotonic=deadline,
                        started_monotonic=self.clock())
        self.resource_query_evidence = evidence
        try:
            result = self.query(PS_ARGV, deadline)
        except Exception as error:
            evidence['query_error'] = type(error).__name__ + ': ' + str(error)
            evidence['exit_code'] = getattr(error, 'returncode', None)
            captured = getattr(error, 'stdout', None)
            if isinstance(captured, (str, bytes)):
                evidence['snapshot_sha256'] = hashlib.sha256(
                    captured.encode() if isinstance(captured, str) else captured).hexdigest()
            evidence['validation_status'] = 'QUERY_FAILED'
            raise
        finally:
            evidence['finished_monotonic'] = self.clock()
        evidence.update(exit_code=result.returncode,
                        snapshot_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
                        validation_status='INCOMPLETE')
        rows = []; raw_by_pid = {}
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
            rows.append(row); raw_by_pid[row['pid']] = line
        self._valid_inventory(rows)
        evidence['validation_status'] = 'VALID'
        return ResourceSnapshot(rows, evidence, raw_by_pid)

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
        pending_processes = dict(self.processes)

        def identity(pid, retained):
            before = time.monotonic()
            current = self.owner.proc_starttime(pid)
            after = time.monotonic()
            if current is None:
                verified = {key:pending_processes[key] for key in live_owned}
                raise ResourceIdentityUnavailable(pid, retained, verified, before, after)
            return current

        for pid, record in list(pending_processes.items()):
            row = by_pid.get(pid)
            if row is None:
                continue
            current_start = identity(pid, True)
            if current_start == record['start_identity']:
                if row['pgid'] != record['pgid']:
                    raise RuntimeError('Owned process changed group; ownership requires review')
                live_owned.add(pid)
        changed = True
        while changed:
            changed = False
            for row in rows:
                if row['pid'] not in live_owned and row['ppid'] in live_owned:
                    start = identity(row['pid'], False)
                    pending_processes[row['pid']] = dict(pid=row['pid'], start_identity=start, pgid=row['pgid'])
                    live_owned.add(row['pid']); changed = True
        self.processes = pending_processes
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
        # Only the explicitly launched root is a signal target. An observed
        # descendant, including a group leader, never confers signal authority.
        record = self.processes.get(self.root_pid)
        if pgid != self.root_pid or not record or record['pid'] != pgid or record['pgid'] != pgid:
            return False
        if self.owner.proc_starttime(pgid) != record['start_identity']:
            return False
        try:
            return os.getpgid(pgid) == pgid
        except ProcessLookupError:
            return False

    def nested_coverage(self):
        count = len(self.generations)
        expected = self.expected_nested_generations
        missing = [key for key, g in self.generations.items() if g['guard'] is None]
        return dict(status='PASS' if count == expected and not missing else 'UNKNOWN',
                    expected_generations=expected, observed_generations=count, missing_guard_identity=missing,
                    generations=list(self.generations.values()))


def signal_owned(registry, runtime, pgid, sig, deadline):
    """Signal only the verified explicitly launched root, never an auxiliary group."""
    if pgid != getattr(registry, 'root_pid', None):
        return dict(action='no_signal_auxiliary_group', pgid=pgid)
    root = getattr(registry, 'processes', {}).get(pgid)
    if not root or root.get('pid') != pgid or root.get('pgid') != pgid or root.get('start_identity') is None:
        return dict(action='no_signal_identity_unknown', pgid=pgid)
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


def cleanup_identities(registry, runtime, observed, deadline, checks):
    """Read-only validation of every live row in the retained groups."""
    for row in observed['live_rows']:
        record = registry.processes.get(row['pid'])
        check = dict(pid=row['pid'], pgid=row['pgid'], status='UNKNOWN')
        checks.append(check)
        if not record or record.get('pgid') != row['pgid'] or record.get('start_identity') is None:
            check['reason'] = 'live_member_not_covered_by_retained_identity'
            continue
        check['retained_start_identity'] = record['start_identity']
        check['reason'] = 'identity_probe_incomplete'
        runtime.remaining(deadline, 1)
        current = runtime.owner.proc_starttime(row['pid'])
        check['observed_start_identity'] = current
        runtime.remaining(deadline, 1)
        if current is None:
            check['reason'] = 'live_member_identity_unavailable'
        elif current != record['start_identity']:
            check['reason'] = 'live_member_identity_not_matched'
        else:
            check['status'] = 'PASS'
            del check['reason']
    return checks


def cleanup(child, registry, runtime, *, request_stop, total_seconds=60, term_seconds=10, kill_reap_seconds=10):
    start = runtime.clock(); deadline = start + total_seconds
    producer_succeeded = not request_stop and child.poll() == 0
    out = dict(status='UNKNOWN', steps=[], observations=[], failure_reasons=[], budget_seconds=total_seconds,
               budget_kind='cooperative observation; existing owner identity/lineage queries have their own bounded calls and may overrun the remaining outer budget',
               natural_wait=dict(eligible=producer_succeeded, used=False, status='NOT_USED', wall_seconds=0))
    groups = registry.groups
    natural_started = None
    natural_finished = None

    def observe(phase, observation_deadline):
        nonlocal natural_started, natural_finished
        entry = dict(phase=phase, cleanup_wall_seconds=runtime.clock()-start)
        out['observations'].append(entry)
        try:
            observed = runtime.observe_groups(groups, observation_deadline)
            entry['observation'] = observed
            out['final_observation'] = observed
            # Record the wait interval from the first observed auxiliary row.
            auxiliary = [r for r in observed['live_rows'] if r['pgid'] != registry.root_pid]
            if producer_succeeded and auxiliary and natural_started is None:
                natural_started = runtime.clock()
                out['natural_wait'].update(used=True, status='WAITING',
                    started_cleanup_wall_seconds=natural_started-start,
                    remaining_budget_at_start_seconds=max(0, deadline-natural_started))
            if not observed['live_rows'] and natural_started is not None and natural_finished is None:
                natural_finished = runtime.clock()
            entry['identity_checks'] = []
            cleanup_identities(registry, runtime, observed, observation_deadline, entry['identity_checks'])
            if any(c['status'] != 'PASS' for c in entry['identity_checks']):
                out['failure_reasons'].append(phase + ': live_identity_coverage_unknown')
            if producer_succeeded and observed['live_rows']:
                out['unexpected_live_members_after_success'] = True
                if any(r['pgid'] == registry.root_pid for r in observed['live_rows']):
                    out['unexpected_root_members_after_success'] = True
                    out['failure_reasons'].append(phase + ': root_group_live_after_success')
            elif auxiliary:
                out['failure_reasons'].append(phase + ': auxiliary_group_live_without_successful_producer')
            return observed
        except Exception as error:
            entry['error'] = type(error).__name__ + ': ' + str(error)
            out['failure_reasons'].append(phase + ': observation_failed')
            raise
        finally:
            entry['completed_cleanup_wall_seconds'] = runtime.clock()-start

    try:
        try:
            before = observe('initial', deadline)
            out['initial_observation'] = before
        except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
            before = dict(live_rows=[])
            out['initial_observation_error'] = type(error).__name__ + ': ' + str(error)
            request_stop = True
        # A successful root's residual groups are observation-only. On an actual
        # stop, retain TERM/KILL semantics for the explicit root and no other PGID.
        root_live = child.poll() is None or any(r['pgid'] == registry.root_pid for r in before['live_rows'])
        if not producer_succeeded and root_live:
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
        # Success uses the remaining 60-second cleanup budget, never a fresh
        # allocation. Other cleanup paths retain their <=40s observation bound.
        observation_deadline = deadline if producer_succeeded else min(deadline, runtime.clock() + 40)
        observed = observe('post_root_wait', observation_deadline)
        while observed['live_rows']:
            runtime.sleep(runtime.remaining(observation_deadline, .1))
            observed = observe('natural_wait' if producer_succeeded else 'post_stop_observation', observation_deadline)
        # Native reaper can signal on an absent leader. Do not call that branch:
        # only remove this invocation's retained run directories after empty proof.
        for generation in registry.generations.values():
            runtime.remaining(deadline, 1)
            path = Path(generation['path'])
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest() != generation['source_sha256']:
                    raise RuntimeError('Retained native ledger changed')
                observation = runtime.observe_groups({generation['record']['pgid']}, deadline)
                out.setdefault('run_directory_observations', []).append(dict(
                    cleanup_wall_seconds=runtime.clock()-start, path=str(path), observation=observation))
                if observation['live_rows']:
                    raise RuntimeError('Owned group became live before run-directory removal')
                context = SimpleNamespace(root=registry.project, cache=registry.project/'cache',
                                          out=registry.project/'out')
                removed = runtime.owner._remove_run_dir(context, path.parent)
                out['steps'].append(dict(action='owner.remove_owned_run_dir', path=str(path.parent), result=removed))
        out['nested'] = registry.nested_coverage()
        remaining_dirs = [g['run_dir'] for g in registry.generations.values() if Path(g['run_dir']).exists()]
        out['remaining_owned_run_dirs'] = remaining_dirs
        out['status'] = ('PASS' if child.poll() is not None and not out.get('unreaped_root')
                         and out['nested']['status'] == 'PASS' and not remaining_dirs
                         and not any(s['action'] == 'no_signal_identity_unknown' for s in out['steps'])
                         and not out['failure_reasons'] else 'UNKNOWN')
    except Exception as error:
        out['error'] = type(error).__name__ + ': ' + str(error)
    out['root_returncode'] = child.poll()
    out['wall_seconds'] = runtime.clock() - start
    out['outer_budget_overrun'] = out['wall_seconds'] > total_seconds
    if out['outer_budget_overrun']:
        out['status'] = 'UNKNOWN'
        out['failure_reasons'].append('cleanup_outer_budget_overrun')
    if natural_started is not None:
        out['natural_wait']['wall_seconds'] = (natural_finished if natural_finished is not None else runtime.clock()) - natural_started
        out['natural_wait']['status'] = 'PASS' if out['status'] == 'PASS' else 'UNKNOWN'
        if out['status'] == 'PASS':
            out['completion_basis'] = 'after bounded natural wait'
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


def resource_related_rows(rows, anchors):
    related = set(anchors)
    while True:
        expanded = related | {row['pid'] for row in rows if row['ppid'] in related}
        if expanded == related:
            break
        related = expanded
    return [dict(row) for row in rows if row['pid'] in related]


def resource_snapshot_evidence(rows, related, verified_pids):
    pids = {row['pid'] for row in related}
    raw = getattr(rows, 'raw_by_pid', {})
    safe_rows = []
    for row in related:
        safe = dict(row)
        if row['pid'] in raw:
            safe['raw_line_sha256'] = hashlib.sha256(raw[row['pid']].encode()).hexdigest()
        if row['pid'] not in verified_pids:
            safe.pop('command', None)
            safe['command_status'] = 'OMITTED_UNVERIFIED_IDENTITY'
        safe_rows.append(safe)
    return dict(query=dict(getattr(rows, 'query_evidence', {})), owned_related_rows=safe_rows,
                owned_related_raw_lines=[line for pid,line in raw.items() if pid in pids & verified_pids],
                unverified_resource_raw_prefixes=[re.match(r'^\s*\S+(?:\s+\S+){6}',line)[0]
                    for pid,line in raw.items() if pid in pids - verified_pids])


def resource_identity_failure(error, rows, registry, child, started, sample_id):
    # Registered identities are diagnostic anchors only. Including every anchor
    # makes the conservative coverage gate independent of traversal order.
    related = resource_related_rows(rows, set(registry.processes) | error.verified_pids | {error.pid})
    descendants = resource_related_rows(rows, {error.pid})
    descendants = [row['pid'] for row in descendants if row['pid'] != error.pid]
    verified = [dict(row) for row in rows if row['pid'] in error.verified_pids]
    lower_bound = sum(positive_number(row['rss_kib']) for row in verified) * 1024
    query = getattr(rows, 'query_evidence', {})
    return dict(sample_id=sample_id, sample_status='INCOMPLETE', resource_status='UNKNOWN',
                wall_seconds=query.get('started_monotonic', error.probe['started_monotonic'])-started,
                error=type(error).__name__ + ': ' + str(error), failed_pid=error.pid,
                root_identity_failure=error.pid == registry.root_pid, root_returncode=child.poll(),
                retained_start_identity=registry.processes.get(error.pid, {}).get('start_identity'),
                identity_probe=dict(error.probe, reason='API returned None; cause not exposed'),
                original_descendant_pids=descendants, verified_pids=sorted(error.verified_pids),
                unverified_related_pids=sorted({row['pid'] for row in related}
                                               - error.verified_pids - {error.pid}),
                verified_process_identities=list(error.verified_processes.values()),
                verified_rss_lower_bound_bytes=lower_bound,
                **resource_snapshot_evidence(rows, related, error.verified_pids))


def write_resource_record(stream, record):
    stream.write(json.dumps(record, allow_nan=False)+'\n'); stream.flush()


def observe_resources(child, registry, runtime, stream, status, deadline, started, before):
    rows = runtime.ps_resources(deadline)
    try:
        return registry.update(rows), before, None
    except ResourceIdentityUnavailable as original:
        sample_id = 'incomplete-' + str(status['incomplete_samples'] + 1)
        failure = resource_identity_failure(original, rows, registry, child, started, sample_id)
        # Flush the original failed observation before any recovery query. It has
        # no complete-sample process/RSS/disk fields, so the summary rejects it.
        write_resource_record(stream, failure)
        status['incomplete_samples'] += 1
        # Keep identities actually verified in the incomplete snapshot. A parent
        # may exit before the fresh query, leaving its verified child reparented;
        # that child must remain a revalidation candidate, independent of PPID.
        registry.processes.update(original.verified_processes)
        registry.groups.update(record['pgid'] for record in original.verified_processes.values())
        recovery = dict(original_sample_id=sample_id, status='UNKNOWN',
                        original_error=failure['error'], fresh_query_attempted=False)
        status['resource_identity_recoveries'].append(recovery)
        if failure['verified_rss_lower_bound_bytes'] >= 6 * GIB:
            recovery['reason'] = 'original_verified_rss_lower_bound_at_or_above_6GiB'
            status['stop_reasons'].append('tree_rss_at_or_above_6GiB')
            raise
        if failure['root_identity_failure']:
            recovery['reason'] = 'root_identity_failure_not_recoverable'
            raise
        if failure['original_descendant_pids']:
            recovery['reason'] = 'original_failed_process_has_descendants'
            raise
        if failure['unverified_related_pids']:
            recovery['reason'] = 'original_related_identity_coverage_incomplete'
            recovery['unverified_related_pids'] = failure['unverified_related_pids']
            raise
        recovery['fresh_query_attempted'] = True
        fresh = None
        try:
            fresh_before = runtime.clock()
            fresh = runtime.ps_resources(deadline)
            anchors = set(registry.processes) | {row['pid'] for row in failure['owned_related_rows']}
            related = resource_related_rows(fresh, anchors)
            # Until fresh identities are checked, retain only resource columns.
            recovery['fresh_snapshot'] = resource_snapshot_evidence(fresh, related, set())
            if any(row['pid'] == original.pid for row in fresh):
                recovery['reason'] = 'failed_pid_still_visible_including_zombie'
                raise RuntimeError('Failed resource identity PID remains visible in fresh inventory')
            recovery['failed_pid_visibility'] = 'confirmed_absent_after_probe'
            fresh_descendants = resource_related_rows(fresh, {original.pid})
            if fresh_descendants:
                recovery['reason'] = 'fresh_descendants_of_absent_failed_pid'
                recovery['fresh_failed_descendant_pids'] = [row['pid'] for row in fresh_descendants]
                raise RuntimeError('Fresh inventory has descendants of the absent failed resource PID')
            owned = registry.update(fresh)
            if not owned:
                raise RuntimeError('Fresh owned resource observation unavailable')
            verified = {row['pid'] for row in owned}
            recovery['fresh_snapshot'] = resource_snapshot_evidence(
                fresh, related, verified)
            uncovered = {row['pid'] for row in related} - verified
            if uncovered:
                recovery['reason'] = 'fresh_related_identity_coverage_incomplete'
                recovery['unverified_related_pids'] = sorted(uncovered)
                raise RuntimeError('Fresh related process rows lack verified owned identity coverage')
            recovery.update(status='FRESH_COMPLETE', reason='absent_leaf_and_fresh_identity_coverage')
            return owned, fresh_before, sample_id
        except Exception as error:
            recovery['fresh_error'] = type(error).__name__ + ': ' + str(error)
            if fresh is None:
                recovery['fresh_query'] = dict(getattr(runtime, 'resource_query_evidence', {}))
            if isinstance(error, ResourceIdentityUnavailable):
                second_id = 'incomplete-' + str(status['incomplete_samples'] + 1)
                write_resource_record(stream, resource_identity_failure(
                    error, fresh, registry, child, started, second_id))
                status['incomplete_samples'] += 1
                registry.processes.update(error.verified_processes)
                registry.groups.update(record['pgid'] for record in error.verified_processes.values())
                recovery['second_incomplete_sample_id'] = second_id
            raise


def monitor(child, registry, runtime, output, roots, deadline, started, *, sample_seconds=5, use_vm_stat=False):
    budget = BudgetState(); next_size = started; total = None
    status = dict(stop_reasons=[], errors=[], samples=0, incomplete_samples=0, resource_identity_recoveries=[],
                  final_new_logical_bytes=None, final_disk_free_bytes=None,
                  terminal_resource_status='UNKNOWN')
    vm_unavailable = None
    with (output/'resource-samples.jsonl').open('x') as stream:
        try:
            while child.poll() is None:
                runtime.remaining(deadline, 1)
                before = runtime.clock()
                owned, before, recovery_of = observe_resources(child, registry, runtime, stream,
                    status, deadline, started, before)
                if not owned:
                    raise RuntimeError('Owned resource observation unavailable')
                pressure, pressure_raw = runtime.pressure(deadline)
                stat = os.statvfs(str(roots[0]))
                row = dict(wall_seconds=before-started, utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                           disk_free_bytes=stat.f_bavail*stat.f_frsize, processes=owned,
                           tree_rss_kib=sum(p['rss_kib'] for p in owned), memory_free_percent=pressure,
                           memory_pressure_raw=pressure_raw)
                row['tree_rss_bytes'] = row['tree_rss_kib']*1024
                if recovery_of is not None:
                    row['recovery_of_incomplete_sample_id'] = recovery_of
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
                write_resource_record(stream, row)
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
