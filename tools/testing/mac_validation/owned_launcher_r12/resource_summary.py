"""Read-only retained Mac evidence summary. No probes, processes, or control."""
import argparse
import json
import math
import re
from pathlib import Path

CENSUS_SCHEMA = 'mac-resource-census/1'


def validate_census(row):
    """Validate a separate reported census; never promote candidates to owners."""
    candidates, verified = row['census_processes'], row['verified_processes']
    if not isinstance(candidates, list) or not candidates or not isinstance(verified, list):
        raise ValueError('Missing census or verified-row list')
    pids = [p['pid'] for p in candidates]
    if any(type(pid) is not int or pid <= 0 for pid in pids) or len(set(pids)) != len(pids):
        raise ValueError('Invalid or duplicate census PID')
    by_pid = {p['pid']: p for p in candidates}
    known = [p['pid'] for p in verified]
    if len(set(known)) != len(known) or not set(known) <= set(pids):
        raise ValueError('Verified rows not a unique census subset')
    for p in verified:
        if type(p.get('start_identity')) is not int or p['start_identity'] <= 0:
            raise ValueError('Verified census row lacks kernel identity')
        if number(p['rss_kib']) != number(by_pid[p['pid']]['rss_kib']):
            raise ValueError('Verified census RSS mismatch')
    unknown = set(pids) - set(known)
    if set(row['unverified_candidate_pids']) != unknown:
        raise ValueError('Unknown census PID set mismatch')
    if any('start_identity' in by_pid[pid] or 'command' in by_pid[pid] for pid in unknown):
        raise ValueError('Unverified census row carries identity or command')
    total = sum(number(p['rss_kib']) for p in candidates)
    subtotal = sum(number(p['rss_kib']) for p in verified)
    if (total != number(row['reported_census_rss_kib'])
            or subtotal != number(row['verified_live_rss_kib'])
            or total-subtotal != number(row['unverified_reported_rss_kib'])):
        raise ValueError('Reported census subtotals do not reconcile')
    partial = bool(unknown or row['observation_errors']
                   or row['census_scope_status'] != 'OBSERVED_CAPTURED_CLOSURE')
    if row['attribution_status'] != ('PARTIAL' if unknown else 'VERIFIED_CAPTURED_ROWS'):
        raise ValueError('Census attribution status mismatch')
    return total*1024, partial


def number(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0:
        raise ValueError('Expected a nonnegative finite number')
    return x


def parse_cpu_time(text):
    m = re.fullmatch(r'(?:(\d+)-)?(?:(\d+):)?(\d+):(\d{2}(?:\.\d+)?)', text.strip())
    if not m:
        raise ValueError('Unsupported CPU time')
    d, h, m, s = m.groups()
    if float(s) >= 60 or (h is not None and int(m) >= 60) or (d is not None and (h is None or int(h) >= 24)):
        raise ValueError('Invalid time fields')
    return number(int(d or 0)*86400 + int(h or 0)*3600 + int(m)*60 + float(s))


def summarize_time_l(text, logical_cpus=None):
    if logical_cpus is not None and (type(logical_cpus) is not int or logical_cpus <= 0):
        raise ValueError('Verified positive logical CPU count required')
    rows = re.findall(r'^\s*(\d+(?:\.\d+)?)\s+real\s+(\d+(?:\.\d+)?)\s+user\s+(\d+(?:\.\d+)?)\s+sys\s*$', text, re.M)
    out = dict(status='UNKNOWN', logical_cpu_count=logical_cpus, host_normalized_cpu_percent=None,
               scope='time-l accounted command and reaped children; not guaranteed complete tree CPU')
    if len(rows) != 1:
        return dict(out, reason='Exactly one time-l record required')
    real, user, system = map(float, rows[0])
    if not real:
        return dict(out, reason='Zero real duration')
    cpu = user + system
    out.update(status='MEASURED_ACCOUNTING', real_s=real, user_s=user, system_s=system,
               accounted_cpu_seconds=cpu, accounted_average_cores=cpu/real,
               accounted_single_core_percent=100*cpu/real)
    if logical_cpus:
        out['host_normalized_cpu_percent'] = 100*cpu/(real*logical_cpus)
    else:
        out['host_normalized_unavailable_reason'] = 'Verified logical CPU count unavailable'
    rss = re.findall(r'^\s*(\d+)\s+maximum resident set size\s*$', text, re.M)
    out['time_l_maxrss_bytes'] = int(rss[0]) if len(rss) == 1 else None
    out['maxrss_scope'] = 'Darwin time-l metric; separate from sampled tree sum-RSS'
    return out


def summarize_samples(samples):
    out = dict(sample_count=len(samples), valid_sample_count=0, errors=[],
               sampled_tree_sum_rss_max_bytes=None, disk_free_min_bytes=None,
               tree_ps_recent_cpu_percent_sum_max=None, exact_cpu_seconds_from_ps_percent=None,
               ps_percent_note='Recent decaying percentage; never integrated into exact CPU seconds',
               sampled_observed_cpu_seconds_lower_bound=None, cumulative_cpu_coverage='UNKNOWN',
               qualified_terminal_rows_excluded_count=0,
               cpu_observation_scope='Per-(PID,start identity) observed maxima; misses unseen lifetimes and trailing CPU',
               rss_scope='Verified live-process ps sum-RSS; qualified terminal rows excluded without estimating residual/kernel/corpse memory; shared mappings may be counted twice; between-sample peaks and short processes missed',
               interval_min_s=None, interval_max_s=None)
    times, rss, free, pcpu, cpu, missing = [], [], [], [], {}, 0
    census = dict(sample_count=0, valid_sample_count=0, partial_sample_count=0,
                  captured_reported_sum_rss_max_bytes=None, status='NOT_PRESENT',
                  scope='Captured ps-reported census including unknown rows; not an upper or lower '
                        'bound on actual memory; printed zero is not absence or terminal proof')
    census_values = []
    for i, row in enumerate(samples):
        try:
            if row.get('record_schema') == CENSUS_SCHEMA:
                census['sample_count'] += 1
                value, partial = validate_census(row)
                number(row['wall_seconds']); number(row['disk_free_bytes'])
                census_values.append(value)
                census['valid_sample_count'] += 1
                census['partial_sample_count'] += int(partial)
                # Legacy metrics remain verified-only, never the wider census.
                row = dict(row, processes=row['verified_processes'],
                           tree_rss_kib=row['verified_live_rss_kib'])
            t = number(row['wall_seconds']); rows = row['processes']
            if not isinstance(rows, list) or not rows:
                raise ValueError('No owned process rows; not zero usage')
            pids = [p['pid'] for p in rows]
            if any(type(p) is not int or p <= 0 for p in pids) or len(set(pids)) != len(pids):
                raise ValueError('Invalid or duplicate process IDs')
            terminals = row.get('terminal_process_observations', [])
            if not isinstance(terminals, list) or any(not isinstance(p, dict) for p in terminals):
                raise ValueError('Invalid terminal observation list')
            terminal_pids = [p['pid'] for p in terminals]
            if (any(type(pid) is not int or pid <= 0 for pid in terminal_pids)
                    or len(set(terminal_pids)) != len(terminal_pids)
                    or set(terminal_pids) & set(pids)
                    or any(p.get('status') != 'QUALIFIED_TERMINAL' for p in terminals)):
                raise ValueError('Unqualified or overlapping terminal observation')
            r = sum(number(p['rss_kib']) for p in rows)
            if r != number(row['tree_rss_kib']):
                raise ValueError('Tree RSS sum mismatch')
            c = sum(number(p['cpu_percent']) for p in rows); f = number(row['disk_free_bytes'])
            if times and t <= times[-1]:
                raise ValueError('Non-increasing sample clock')
        except (KeyError, TypeError, ValueError) as e:
            out['errors'].append(dict(sample_index=i, reason=str(e))); continue
        times.append(t); rss.append(r*1024); free.append(f); pcpu.append(c)
        out['qualified_terminal_rows_excluded_count'] += len(terminals)
        for p in rows:
            try:
                ident = p['start_identity']
                if type(ident) is not int or ident < 0:
                    raise ValueError('Missing kernel start identity')
                value = parse_cpu_time(p['cpu_time']); key = (p['pid'], ident)
            except (KeyError, TypeError, AttributeError, ValueError):
                missing += 1; continue
            if key in cpu and value < cpu[key]:
                out['errors'].append(dict(sample_index=i, reason='CPU counter regressed'))
            cpu[key] = max(value, cpu.get(key, 0))
    out['valid_sample_count'] = len(times); out['cpu_rows_missing_identity_or_time'] = missing
    if times:
        out.update(sampled_tree_sum_rss_max_bytes=max(rss), disk_free_min_bytes=min(free),
                   tree_ps_recent_cpu_percent_sum_max=max(pcpu))
    if len(times) > 1:
        delta = [b-a for a,b in zip(times,times[1:])]
        out.update(interval_min_s=min(delta), interval_max_s=max(delta))
    if cpu:
        out.update(sampled_observed_cpu_seconds_lower_bound=sum(cpu.values()),
                   cumulative_cpu_coverage='PARTIAL' if missing or out['errors'] or census['partial_sample_count']
                       else 'SAMPLED_LOWER_BOUND')
    out['status'] = 'UNKNOWN' if not times else 'PARTIAL' if out['errors'] or census['partial_sample_count'] else 'OBSERVED_SAMPLES'
    if census['sample_count']:
        census['status'] = ('UNKNOWN' if not census_values else 'PARTIAL'
                            if census['partial_sample_count'] or census['valid_sample_count'] != census['sample_count']
                            else 'OBSERVED_REPORTED_CENSUS')
        census['captured_reported_sum_rss_max_bytes'] = max(census_values) if census_values else None
        out['diagnostic_census'] = census
    return out


def parse_vm_stat_snapshot(text):
    m = re.search(r'page size of (\d+) bytes', text)
    if not m or int(m[1]) <= 0:
        raise ValueError('Missing VM page size')
    out = dict(page_size_bytes=int(m[1]))
    for label, field in [('Swapins','swapins_pages'), ('Swapouts','swapouts_pages'), ('Pages occupied by compressor','compressor_resident_pages')]:
        rows = re.findall(r'^'+re.escape(label)+r':\s*(\d+)\.?\s*$',text,re.M)
        out[field] = int(rows[0]) if len(rows) == 1 else None
    return out


def summarize_vm_delta(before, after):
    out = dict(status='UNKNOWN', swapout_page_equivalent_bytes=None, swapin_page_equivalent_bytes=None,
               swap_occupied_bytes=None, scope='System cumulative swap I/O; not occupied swap or task attribution')
    try:
        size = number(before['page_size_bytes'])
        if not size or size != after['page_size_bytes']:
            raise ValueError('Invalid or changed VM page size')
        delta = {}
        for field, target in [('swapins_pages','swapin_page_equivalent_bytes'),('swapouts_pages','swapout_page_equivalent_bytes')]:
            a,b = number(before[field]),number(after[field])
            if b < a:
                raise ValueError('VM counter reset or regressed')
            delta[target] = (b-a)*size
        out.update(status='OBSERVED_SYSTEM_IO', **delta)
    except (KeyError,TypeError,ValueError) as e:
        out['reason'] = str(e)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('samples','time-log','out'):
        p.add_argument('--'+key, required=True)
    p.add_argument('--logical-cpus',type=int); p.add_argument('--vm-start'); p.add_argument('--vm-end')
    a=p.parse_args()
    if bool(a.vm_start) != bool(a.vm_end):
        p.error('Both VM snapshots required')
    rows=[json.loads(x) for x in Path(a.samples).read_text().splitlines() if x.strip()]
    out=dict(schema='mac-resource-summary/1', samples=summarize_samples(rows),
             time_l=summarize_time_l(Path(a.time_log).read_text(),a.logical_cpus),
             vm=dict(status='UNKNOWN',reason='No retained VM snapshots supplied'))
    if a.vm_start:
        out['vm']=summarize_vm_delta(parse_vm_stat_snapshot(Path(a.vm_start).read_text()),parse_vm_stat_snapshot(Path(a.vm_end).read_text()))
    with Path(a.out).open('x') as f:
        json.dump(out,f,indent=2,allow_nan=False); f.write('\n')

if __name__ == '__main__':
    main()
