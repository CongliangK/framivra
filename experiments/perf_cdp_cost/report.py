"""Read existing evidence without running renderers or following recorded paths."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from vtx import events

VERSION = 'cdp-cost-ledger/1'
TIMINGS = ('nav', 'export', 'decode', 'write')
LIMITS = [
    'No full-film speedup, quality equivalence or performance acceptance is inferred.',
    'Different inputs are separate observations; no cross-source total or A/B comparison.',
    'Work-time sums are not elapsed wall; inclusive and cross-thread categories overlap.',
    'No fixed 20 ms per frame; poll sleeps, browser encoding, paint and IPC remain unmeasured.',
    'No pooled percentile is reconstructed from per-attempt p50/p95 summaries.',
    'Recorded PNG bytes are inventory sizes, not physical IO; PNG paths are never read.',
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, label, integer=False):
    if value is None:
        return None
    require(type(value) is int if integer else type(value) in (int, float), label + ': invalid number')
    require(0 <= value <= 2**63 - 1 and math.isfinite(value), label + ': out-of-range/nonfinite number')
    return value


def metric(value=None, *, reason='field absent from source', status=None, unit=None):
    return dict(value=value, status=status or ('OBSERVED' if value is not None else 'UNOBSERVED'),
                reason=None if value is not None and status != 'UNKNOWN' else reason, unit=unit)


def total(values, unit=None):
    known = [v for v in values if v is not None]
    return dict(value=sum(known) if values and len(known) == len(values) else None,
                observed_subtotal=sum(known) if known else None,
                observed_records=len(known), expected_records=len(values), unit=unit,
                status='OBSERVED' if values and len(known) == len(values) else 'UNKNOWN',
                reason=None if values and len(known) == len(values) else 'empty or missing record fields; not zero')


def objects(value, label):
    if value is None:
        return None
    require(isinstance(value, list) and all(isinstance(x, dict) for x in value), label + ': expected object list')
    return value


def unique(rows, key, label):
    seen = set()
    for row in rows:
        value = number(row.get(key), label + '.' + key, integer=True)
        require(value is not None, label + ': missing identity')
        require(value not in seen, label + ': duplicate identity')
        seen.add(value)


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON field')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('nonfinite JSON number')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_input(path):
    data = path.read_bytes()
    return data, dict(path=str(path), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def union(intervals):
    """Return elapsed union, never sum overlapping work."""
    end, result = None, 0
    for a, b in sorted(intervals):
        number(a, 'interval start'); number(b, 'interval end')
        require(a is not None and b is not None and b >= a, 'invalid interval')
        result += b - a if end is None or a >= end else max(0, b - end)
        end = b if end is None else max(end, b)
    return result


def interval_rows(rows):
    result = []
    for row in rows:
        t = row['timing']
        if row['monotonic_duration'] is not None and t['start_ms'] is not None:
            result.append(dict(task_id=row['task_id'], attempt=row['attempt'], stage=row['stage'],
                               scope=row['scope'], outcome=row['outcome'], event_seq=row['seq'],
                               start_s=t['start_ms']/1000, end_s=t['offset_ms']/1000,
                               duration_s=row['monotonic_duration']/1000))
    return result


def intervals_summary(rows, *, complete, started_records, terminal_records):
    covered = union([(r['start_s'], r['end_s']) for r in rows])
    work = sum(r['duration_s'] for r in rows)
    measured = len(rows)
    known = complete and measured > 0 and measured == terminal_records == started_records
    return dict(intervals=rows, union_seconds=covered if known else None,
                work_seconds=work if known else None,
                observed_union_seconds=covered if rows else None,
                observed_work_seconds=work if rows else None,
                status='OBSERVED' if known else 'UNKNOWN',
                reason=None if known else 'empty or incomplete event coverage; subtotals are not complete costs',
                evidence_complete=complete, started_records=started_records,
                terminal_records=terminal_records, measured_records=measured,
                boundary='Intervals only; overlapping work is counted once in union, not in work_seconds.')


def summarize_events(data):
    parsed = events.read_events(data, replay=False)
    rows = parsed['events']
    terminals = [r for r in rows if r['event'] == 'run_terminal']
    attempts = [r for r in rows if r['event'] == 'attempt_terminal']
    tasks = [r for r in rows if r['event'] == 'task_terminal']
    starts = {r['task_id']: r for r in rows if r['event'] == 'task_start'}
    run = terminals[0] if terminals else None
    groups = {}
    for scope in ('stage', 'wave_job', 'gfx_block', 'process'):
        selected = [r for r in tasks if r['scope'] == scope]
        groups[scope] = intervals_summary(interval_rows(selected), complete=parsed['complete'],
            started_records=sum(r['scope'] == scope for r in starts.values()), terminal_records=len(selected))
    gfx_attempts = [r for r in attempts if starts.get(r['task_id'], {}).get('scope') == 'gfx_block']
    groups['gfx_attempts'] = intervals_summary(interval_rows(gfx_attempts), complete=parsed['complete'],
        started_records=sum(r['event'] == 'attempt_start' and starts.get(r['task_id'], {}).get('scope') == 'gfx_block' for r in rows),
        terminal_records=len(gfx_attempts))
    return dict(schema='vtx.events/1', run_id=parsed['run_id'], evidence_complete=parsed['complete'],
                status='OBSERVED' if parsed['complete'] else 'UNKNOWN', reason=parsed['reason'],
                exit_code=parsed['exit_code'], events_read=len(rows),
                internal_run_wall_seconds=metric(run['monotonic_duration']/1000 if run and run['monotonic_duration'] is not None else None, unit='s'),
                terminal_outcomes=dict(run= dict(Counter(r['outcome'] for r in terminals)),
                                       tasks=dict(Counter(r['outcome'] for r in tasks)),
                                       attempts=dict(Counter(r['outcome'] for r in attempts))),
                attempt_starts=sum(r['event'] == 'attempt_start' for r in rows),
                attempt_terminals=len(attempts), retry_requested=sum(r['event'] == 'retry_requested' for r in rows),
                recovery_attempts=metric(reason='retry_requested is not browser recovery instrumentation'),
                cleanup_failed=sum(r['event'] == 'cleanup_failed' for r in rows),
                intervals=groups, timing_sums_ms={k: metric(reason='all.events does not persist renderer timing sums', unit='ms') for k in TIMINGS},
                png_bytes=metric(reason='all.events contains no PNG byte inventory', unit='bytes'),
                unmeasured=unmeasured(),
                boundary='Counts/intervals cover parsed records only; incomplete streams are not complete-run counts.')


def summarize_time(data):
    text = data.decode('utf-8')
    pattern = r'^\s*([0-9]+(?:\.[0-9]+)?)\s+real\s+[0-9]+(?:\.[0-9]+)?\s+user\s+[0-9]+(?:\.[0-9]+)?\s+sys\s*$'
    hits = re.findall(pattern, text, flags=re.MULTILINE)
    require(len(hits) <= 1, 'ambiguous multiple time-l real lines')
    value = number(float(hits[0]), 'time-l real') if hits else None
    return dict(external_real_seconds=metric(value, reason='no supported time-l real line', unit='s'),
                boundary='External /usr/bin/time -l real; separate clock boundary from internal events and launcher.')


def unmeasured():
    return {k: metric(status='UNKNOWN', reason=reason) for k, reason in {
        'poll_sleep_seconds': 'no actual sleep timer in these schemas',
        'encoder_internal_seconds': 'no browser encoder-only timer',
        'process_tree_peak_bytes': 'no process-tree peak observation',
        'performance_acceptance': 'offline accounting does not grant performance acceptance',
        'quality_acceptance': 'no independent pixel/media decode performed by this tool',
    }.items()}


def safe_identity(data):
    """Return only verified-format hashes, never recorded paths/environment/URLs."""
    require(isinstance(data, dict), 'identity must be an object')
    result = {}
    for key in ('base_commit', 'base_tree', 'source_head', 'source_tree', 'renderer_sha256',
                'adapter_sha256', 'environment_sha256', 'profile_identity', 'plan_sha256'):
        value = data.get(key)
        if value is not None:
            size = 40 if key in ('base_commit', 'base_tree', 'source_head', 'source_tree') else 64
            require(isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % size, value) is not None, 'invalid identity: ' + key)
            result[key] = value
    return result


def summarize_capture(data):
    require(isinstance(data, dict) and data.get('schema') == 'fast-png-pilot-v1', 'unsupported capture schema')
    captures = objects(data.get('captures'), 'captures')
    operations = objects(data.get('operations'), 'operations')
    for rows, label in ((captures, 'captures'), (operations, 'operations')):
        if rows is not None:
            unique(rows, 'sequence', label)
    if captures is not None and data.get('capture_count') is not None:
        require(number(data['capture_count'], 'capture_count', True) == len(captures), 'capture_count mismatch')
    operation_rows = []
    if operations is not None:
        names = sorted({r.get('operation', 'UNKNOWN') for r in operations})
        require(all(isinstance(n, str) for n in names), 'invalid operation label')
        for name in names:
            selected = [r for r in operations if r.get('operation', 'UNKNOWN') == name]
            operation_rows.append(dict(operation=name, counts=dict(Counter(r.get('status', 'UNKNOWN') for r in selected)),
                                       work_seconds=total([number(r.get('wall_s'), 'operation wall') for r in selected], 's'),
                                       failed_work_seconds=total([number(r.get('wall_s'), 'failed operation wall') for r in selected if r.get('status') == 'failed'], 's')))
    browsers = objects(data.get('browsers'), 'browsers')
    versions = []
    for browser in browsers or []:
        version = browser.get('version', {})
        require(isinstance(version, dict), 'invalid browser version')
        versions.append(dict(version={k: version[k] for k in ('Browser', 'Protocol-Version', 'V8-Version') if k in version},
                             geometry={k: number(browser.get(k), 'browser.' + k, True) for k in ('width', 'height', 'tabs')}))
    errors = objects(data.get('errors'), 'errors')
    # Adapter errors are dictionaries; error text is deliberately not forwarded.
    failures = objects(data.get('protocol_failures'), 'protocol_failures')
    return dict(schema=data['schema'], trace_content_sha256=content_hash(data), identity=safe_identity(data),
                mode=data.get('mode', 'UNKNOWN'), producer_status=data.get('status', 'UNKNOWN'), browser_versions=versions,
                scope_wall_seconds=metric(number(data.get('scope_wall_s'), 'scope wall'), unit='s'),
                captures=metric(len(captures) if captures is not None else None, unit='requests'),
                capture_outcomes=dict(Counter(r.get('status', 'UNKNOWN') for r in captures or [])),
                capture_call_work_seconds=total([number(r.get('cdp_call_wall_s'), 'capture wall') for r in captures or []], 's'),
                base64_characters=total([number(r.get('base64_characters'), 'base64 length', True) for r in captures or []], 'characters'),
                operations=operation_rows,
                browser_start_attempts=metric(number(data.get('browser_start_attempts'), 'browser starts', True)),
                recovery_attempts=metric(number(data.get('recovery_attempts'), 'recovery attempts', True)),
                errors=metric(len(errors) if errors is not None else None),
                protocol_failures=metric(len(failures) if failures is not None else None),
                encoder_internal_effect=metric(reason='accepted capture request does not observe browser encoder internals'),
                boundary='Capture includes setup/probe calls; it is not a target-frame count. Operations may nest. '
                         'Capture call includes transport/JSON; no pure encoder time or missing failure duration is inferred.')


def timing_summary(row, *, rendered=None):
    values = {k: number(row.get(k), 'timing.' + k, k == 'count') for k in ('sum', 'count', 'p50', 'p95')}
    count, summed, p50, p95 = (values[k] for k in ('count', 'sum', 'p50', 'p95'))
    if p50 is not None and p95 is not None:
        require(p50 <= p95, 'timing p95 is less than p50')
    if summed is not None:
        for percentile in (p50, p95):
            if percentile is not None:
                require(percentile <= summed or math.isclose(percentile, summed, rel_tol=1e-12, abs_tol=1e-9),
                        'timing percentile exceeds nonnegative sample sum')
        if count is not None and count > 0:
            # gfx_render._timings_summary uses NumPy's linear percentile:
            # p=(1-f)*x[k]+f*x[k+1]. For nonnegative sorted samples, minimize
            # x[k]+(n-k-1)*x[k+1] at x[k]=x[k+1]=p or x[k]=0.
            # These are necessary bounds only; raw samples remain unavailable.
            for percent, percentile in ((50, p50), (95, p95)):
                if percentile is None:
                    continue
                rank, fraction = divmod(percent * (count - 1), 100)
                factor = count - rank
                if fraction:
                    factor = min(factor, (count - rank - 1) * 100 / fraction)
                minimum = factor * percentile
                require(summed >= minimum or math.isclose(summed, minimum, rel_tol=1e-12, abs_tol=1e-9),
                        'timing sum below nonnegative linear percentile/count bound')
    if count == 0:
        require(all(v in (None, 0) for v in (summed, p50, p95)), 'zero timing count with positive summary')
    if count == 1:
        present = [v for v in (summed, p50, p95) if v is not None]
        require(all(math.isclose(v, present[0], rel_tol=1e-12, abs_tol=1e-9) for v in present),
                'single-sample timing summaries disagree')
    if row.get('count_basis') == 'successful override frames' and count is not None and rendered is not None:
        require(count == rendered, 'timing count contradicts rendered successful override frames')
    return {k: metric(v, unit='frames' if k == 'count' else 'ms') for k, v in values.items()}


def attempt_summary(attempt):
    job = attempt.get('job', {})
    if job is None:
        job = {}
    require(isinstance(job, dict), 'invalid attempt job')
    batch = job.get('batch', 'UNKNOWN')
    require(isinstance(batch, str), 'invalid batch')
    mapping = objects(job.get('mapping'), 'target mapping')
    if mapping is not None:
        unique(mapping, 'local', 'target mapping local')
        unique(mapping, 'global', 'target mapping global')
    scope = dict(mapping_records=metric(len(mapping) if mapping is not None else None),
                 mapping_sha256=content_hash(mapping) if mapping is not None else None,
                 ranges={key: ([min(m[key] for m in mapping), max(m[key] for m in mapping)] if mapping else None) for key in ('global', 'local')},
                 boundary='Inclusive min/max only; sparse membership is not inferred from endpoints. Mapping fingerprint preserves exact selected set.')
    stats = attempt.get('raw_stats')
    require(stats is None or isinstance(stats, dict), 'invalid raw_stats')
    stats = stats or {}
    timing = attempt.get('timings_ms', stats.get('timings_ms', {}))
    require(isinstance(timing, dict), 'invalid timings_ms')
    timings = {}
    for name in TIMINGS:
        row = timing.get(name, {})
        require(isinstance(row, dict), 'invalid timing row')
        raw_timing = stats.get('timings_ms', {})
        require(isinstance(raw_timing, dict), 'invalid raw timing summaries')
        raw_row = raw_timing.get(name, {})
        require(isinstance(raw_row, dict), 'invalid raw timing row')
        for key in ('sum', 'p50', 'p95'):
            if row.get(key) is not None and raw_row.get(key) is not None:
                require(row[key] == raw_row[key], 'conflicting raw/validated timing summaries')
        timings[name] = timing_summary(row, rendered=number(stats.get('rendered'), 'rendered frames', True))
        timing_summary(raw_row)
    png = objects(attempt.get('png'), 'png')
    if png is not None:
        unique(png, 'local', 'png local frame')
        unique(png, 'global', 'png global frame')
        if mapping is not None:
            targets = {(m['global'], m['local']) for m in mapping}
            require(all((r['global'], r['local']) in targets for r in png), 'PNG frame pair outside target mapping')
    scope['png_mapping_coverage'] = dict(
        status='OBSERVED' if mapping is not None and png is not None else 'UNKNOWN',
        reason=None if mapping is not None and png is not None else 'mapping or PNG inventory absent',
        complete=(len(png) == len(mapping)) if mapping is not None and png is not None else None,
        mapped_png_records=len(png) if mapping is not None and png is not None else None,
        expected_target_records=len(mapping) if mapping is not None else None)
    failed = stats.get('failed')
    require(failed is None or isinstance(failed, list), 'invalid failed frames')
    code = attempt.get('exit_code')
    require(code is None or type(code) is int, 'invalid attempt exit code')
    state = attempt.get('status')
    outcome = ('rendered_unverified' if state == 'RENDERED_UNVERIFIED' and code == 0 else
               'failed' if state == 'FAILED' and code not in (None, 0) else 'UNKNOWN')
    if state == 'RENDERED_UNVERIFIED':
        require(code in (0, None), 'contradictory rendered attempt exit code')
        if mapping is not None and png is not None:
            require(len(png) == len(mapping), 'successful attempt PNG inventory does not cover target mapping')
    cleanup = objects(attempt.get('cleanup_checks'), 'cleanup checks')
    result = dict(cleanup_checks=metric(len(cleanup) if cleanup is not None else None),
                  cleanup_outcomes=dict(Counter(c.get('status', 'UNKNOWN') for c in cleanup or [])),
                  target_scope=scope, index=attempt['index'], batch=batch, outcome=outcome, exit_code=code,
                  attempt_wall_seconds=metric(number(attempt.get('attempt_wall_seconds'), 'attempt wall'), unit='s'),
                  render_page_wall_seconds=metric(number(attempt.get('render_page_wall_seconds'), 'render wall'), unit='s'),
                  renderer_ms_total=metric(number(stats.get('ms_total'), 'renderer ms_total'), unit='ms'),
                  rendered_layer_frames=metric(number(stats.get('rendered'), 'rendered frames', True)),
                  failed_frames=metric(len(failed) if failed is not None else None),
                  timings_ms=timings, png_records=metric(len(png) if png is not None else None),
                  png_bytes=total([number(r.get('bytes'), 'PNG bytes', True) for r in png or []], 'bytes'),
                  capture=summarize_capture(attempt['capture']) if attempt.get('capture') is not None else None)
    return result


def summarize_arm(data):
    require(isinstance(data, dict) and data.get('schema') == 'render-pilot-arm/1', 'unsupported arm schema')
    attempts = objects(data.get('attempts'), 'attempts')
    if attempts is not None:
        unique(attempts, 'index', 'attempt')
    code = data.get('process_exit_code')
    require(code is None or type(code) is int, 'invalid arm exit code')
    if data.get('status') == 'RENDERED_UNVERIFIED':
        require(code in (0, None), 'contradictory rendered arm exit code')
    rows = [attempt_summary(a) for a in attempts or []]
    attempt_fingerprints, trace_fingerprints = set(), set()
    for source, row in zip(attempts or [], rows):
        fingerprint = content_hash({k: v for k, v in source.items() if k != 'index'})
        require(fingerprint not in attempt_fingerprints, 'duplicate attempt content with different index')
        attempt_fingerprints.add(fingerprint)
        row['content_without_index_sha256'] = fingerprint
        if row['capture'] is not None:
            fingerprint = row['capture']['trace_content_sha256']
            require(fingerprint not in trace_fingerprints, 'duplicate embedded capture trace within arm')
            trace_fingerprints.add(fingerprint)
    batches = {}
    for batch in sorted({r['batch'] for r in rows}):
        selected = [r for r in rows if r['batch'] == batch]
        batches[batch] = dict(attempt_records=len(selected), outcomes=dict(Counter(r['outcome'] for r in selected)),
                             attempt_work_seconds=total([r['attempt_wall_seconds']['value'] for r in selected], 's'),
                             render_page_work_seconds=total([r['render_page_wall_seconds']['value'] for r in selected], 's'),
                             rendered_layer_frames=total([r['rendered_layer_frames']['value'] for r in selected]),
                             png_bytes=total([r['png_bytes']['value'] for r in selected], 'bytes'),
                             timing_sum_ms={k: total([r['timings_ms'][k]['sum']['value'] for r in selected], 'ms') for k in TIMINGS},
                             boundary='Per-attempt work sums, not an independently observed batch wall. No diagnostic throughput extrapolation.')
    return dict(schema=data['schema'], arm=data.get('arm', 'UNKNOWN'), identity=safe_identity(data),
                producer_status=data.get('status', 'UNKNOWN'), process_exit_code=data.get('process_exit_code'),
                internal_arm_wall_seconds=metric(number(data.get('arm_wall_seconds'), 'arm wall'), unit='s'),
                attempts_present=attempts is not None, attempts=rows, batches=batches, unmeasured=unmeasured(),
                failed_attempt_work_seconds=total([r['attempt_wall_seconds']['value'] for r in rows if r['outcome'] == 'failed'], 's'),
                recovered_frame_extra_wall_seconds=metric(reason='no disjoint failed/recovery-frame interval timer; per-operation work may nest', unit='s'),
                boundary='Timing summaries cover recorded successful renderer samples; failed/recovery work is not imputed. '
                         'renderer ms_total omits portions of the full render_page lifetime. '
                         'decode is twoshot host processing, not all downstream PNG decode. Pixels/quality remain unverified.')


def summarize_quality(data):
    require(isinstance(data, dict) and data.get('schema') == 'render-pilot-quality/1', 'unsupported quality schema')
    comparison = data.get('png_comparison')
    require(comparison is None or isinstance(comparison, dict), 'invalid png_comparison')
    frames = objects((comparison or {}).get('frames'), 'quality frames')
    keys = set()
    equal_counts = Counter()
    for row in frames or []:
        key = (row.get('entry'), number(row.get('global'), 'quality global', True))
        require(isinstance(key[0], str) and key[1] is not None and key not in keys, 'duplicate/missing quality frame identity')
        keys.add(key)
        equal = row.get('equal')
        require(equal is None or type(equal) is bool, 'invalid quality equal')
        equal_counts['equal' if equal is True else 'different' if equal is False else 'UNKNOWN'] += 1
    equal = data.get('equal')
    require(equal is None or type(equal) is bool, 'invalid overall quality equal')
    if equal is True:
        require(not equal_counts['different'], 'contradictory overall quality equal')
    return dict(schema=data['schema'], quality_content_sha256=content_hash(data),
                producer_equal=metric(equal), frame_records=metric(len(frames) if frames is not None else None),
                recorded_frame_outcomes=dict(equal_counts),
                wall_seconds=metric(number(data.get('wall_seconds'), 'quality wall'), unit='s'),
                full_scene_acceptance='UNKNOWN', independent_decode_by_this_tool='UNRUN',
                boundary='Reports source quality assertions only; does not independently verify PNG bytes, RGBA, audio or a complete scene.')


def summarize_pair(data):
    require(isinstance(data, dict) and data.get('schema') == 'render-pilot-result/1', 'unsupported pair schema')
    rows = objects(data.get('processes'), 'pair processes')
    processes, seen = [], set()
    for row in rows or []:
        arm = row.get('arm')
        require(isinstance(arm, str) and arm not in seen, 'duplicate/missing pair process arm')
        seen.add(arm)
        code = row.get('returncode')
        require(code is None or type(code) is int, 'invalid process returncode')
        cleanup = row.get('cleanup')
        require(cleanup is None or isinstance(cleanup, dict), 'invalid process cleanup')
        processes.append(dict(arm=arm, returncode=code,
                              supervised_wall_seconds=metric(number(row.get('complete_wall_seconds'), 'supervised wall'), unit='s'),
                              cleanup_status=(cleanup or {}).get('status', 'UNKNOWN')))
    identities = {}
    for name in ('implementation', 'base'):
        source = data.get(name)
        require(source is None or isinstance(source, dict), 'invalid pair source identity')
        identities[name] = safe_identity({'source_head':(source or {}).get('commit'), 'source_tree':(source or {}).get('tree')})
    return dict(schema=data['schema'], identity=identities, plan_identity=safe_identity({'plan_sha256':data.get('plan_sha256')}),
                runner_complete_wall_seconds=metric(number(data.get('complete_wall_seconds'), 'pair wall'), unit='s'),
                processes=processes, cleanup_status=data.get('cleanup_status', 'UNKNOWN'),
                quality=summarize_quality(data['quality']) if data.get('quality') is not None else None,
                unmeasured=unmeasured(),
                boundary='Runner complete wall and supervised process walls are distinct from internal arm and native all wall; no speedup is inferred.')


def observer_window(data, label):
    if data is None:
        return metric(reason=label + ' absent from source')
    require(isinstance(data, dict), 'invalid observer window')
    result = {}
    for key in ('wall_ns', 'observed_union_ns', 'outside_observed_functions_ns'):
        result[key] = number(data.get(key), key, True)
    wall, covered, outside = (result[k] for k in ('wall_ns', 'observed_union_ns', 'outside_observed_functions_ns'))
    if wall is not None and covered is not None:
        require(covered <= wall, 'observer union exceeds wall')
        if outside is not None:
            require(covered + outside == wall, 'observer coverage mismatch')
    complete = all(v is not None for v in (wall, covered, outside))
    def categories(value):
        nonlocal complete
        if value is None:
            complete = False
            return None
        require(isinstance(value, dict), 'invalid observer categories')
        out = {}
        for name, row in value.items():
            require(isinstance(row, dict), 'invalid observer category row')
            out[name] = {k: number(row.get(k), 'observer.' + k, True) for k in ('calls', 'inclusive_ns', 'exclusive_ns')}
            calls, inc, exc = (out[name][k] for k in ('calls', 'inclusive_ns', 'exclusive_ns'))
            complete = complete and all(v is not None for v in (calls, inc, exc))
            if inc is not None and exc is not None:
                require(exc <= inc, 'observer exclusive exceeds inclusive')
            if calls == 0:
                require(inc in (None, 0) and exc in (None, 0), 'zero observer calls with positive work')
            if calls is not None and inc is not None:
                # Each contributing span is clipped inside this window/union.
                # Repeated or nested calls may still sum to more than wall.
                for bound in (wall, covered):
                    if bound is not None:
                        require(inc <= calls * bound, 'observer inclusive exceeds calls times window/global union')
        return out
    result['categories'] = categories(data.get('categories'))
    by_thread = data.get('categories_by_thread')
    require(by_thread is None or isinstance(by_thread, dict), 'invalid observer threads')
    complete = complete and by_thread is not None
    result['categories_by_thread'] = {t: categories(c) for t, c in by_thread.items()} if by_thread is not None else None
    threads = data.get('thread_union_ns')
    require(threads is None or isinstance(threads, dict), 'invalid thread unions')
    complete = complete and threads is not None
    thread_unions = {t: number(v, 'thread union', True) for t, v in threads.items()} if threads is not None else None
    result['thread_union_ns'] = thread_unions
    if thread_unions is not None:
        complete = complete and all(v is not None for v in thread_unions.values())
        for value in thread_unions.values():
            for bound in (wall, covered):
                if value is not None and bound is not None:
                    require(value <= bound, 'observer thread union exceeds window/global union')
        if covered is not None and all(v is not None for v in thread_unions.values()):
            require(covered <= sum(thread_unions.values()), 'observer global union exceeds sum of thread unions')
    parsed_threads = result['categories_by_thread']
    if parsed_threads is not None and thread_unions is not None:
        require(set(parsed_threads) == set(thread_unions), 'observer thread category/union identity mismatch')
        for thread, cat in parsed_threads.items():
            if cat is not None and thread_unions[thread] is not None:
                for row in cat.values():
                    if row['calls'] is not None and row['inclusive_ns'] is not None:
                        require(row['inclusive_ns'] <= row['calls'] * thread_unions[thread],
                                'observer inclusive exceeds calls times thread union')
                exclusive = [row['exclusive_ns'] for row in cat.values()]
                if all(v is not None for v in exclusive):
                    require(sum(exclusive) <= thread_unions[thread], 'observer per-thread exclusive sum exceeds union')
    global_categories = result['categories']
    if global_categories is not None and parsed_threads is not None and all(c is not None for c in parsed_threads.values()):
        names = {name for categories in parsed_threads.values() for name in categories}
        require(set(global_categories) == names, 'observer global/per-thread category identity mismatch')
        for name in names:
            for field in ('calls', 'inclusive_ns', 'exclusive_ns'):
                values = [categories[name][field] for categories in parsed_threads.values() if name in categories]
                if global_categories[name][field] is not None and all(v is not None for v in values):
                    require(global_categories[name][field] == sum(values), 'observer global/per-thread category sum mismatch')
    result['status'] = 'OBSERVED' if complete and data.get('status') == 'OBSERVED' else 'UNKNOWN'
    result['reason'] = None if result['status'] == 'OBSERVED' else 'missing summary fields or producer coverage unknown'
    result['boundary'] = 'Producer summaries only; raw spans unavailable for independent union reconstruction. '
    result['boundary'] += 'Exclusive includes uninstrumented work/observer overhead. Cross-thread sums are work-time.'
    return result


def summarize_observer(data, context='direct observer output; workload identity not supplied by this schema'):
    require(isinstance(data, dict), 'invalid observer input')
    if data.get('schema') == 'perf-composite-calibration/1':
        reports = objects(data.get('observer_reports'), 'observer_reports')
        require(reports is not None, 'missing calibration observer_reports')
        return dict(schema=data['schema'], context='existing synthetic calibration; not actual Mac/full film',
                    reports=[summarize_observer(r, 'synthetic calibration') for r in reports])
    require(data.get('schema') == 'perf-composite-observer/1', 'unsupported observer schema')
    windows = objects(data.get('video_windows'), 'video_windows')
    truncated = data.get('truncated')
    require(truncated is None or type(truncated) is bool, 'invalid observer truncation')
    rows = []
    for w in windows or []:
        rows.append(dict(stage_outcome=w.get('stage_outcome', 'UNKNOWN'),
                         stage_start_offset_ns=number(w.get('stage_start_offset_ns'), 'stage offset', True),
                         first_ffmpeg_offset_ns=number(w.get('first_ffmpeg_offset_ns'), 'ffmpeg offset', True),
                         first_ffmpeg_role='UNKNOWN',
                         whole_video=observer_window(w.get('whole_video'), 'whole_video'),
                         pre_first_ctx_run=observer_window(w.get('pre_first_ctx_run'), 'pre_first_ctx_run'),
                         pre_first_ffmpeg=observer_window(w.get('pre_first_ffmpeg'), 'pre_first_ffmpeg')))
    whole = observer_window(data.get('whole_invocation'), 'whole_invocation')
    complete = (truncated is False and data.get('source_identity') == 'PASS' and
                whole.get('status') == 'OBSERVED' and windows is not None and
                all(w['whole_video'].get('status') == 'OBSERVED' and
                    w['stage_outcome'] in ('returned', 'raised', 'cancelled') for w in rows))
    return dict(schema=data['schema'], context=context,
                status='OBSERVED' if complete else 'UNKNOWN',
                source_identity=data.get('source_identity', 'UNKNOWN'), truncated=truncated,
                spans_recorded=metric(number(data.get('spans_recorded'), 'spans', True)),
                stage_outcomes=dict(Counter(w['stage_outcome'] for w in rows)),
                video_windows=rows, whole_invocation=whole,
                recovery_attempts=metric(reason='observer schema does not expose browser recoveries'),
                boundary='First FFmpeg is Python subprocess.run entry, not kernel spawn; role unknown. '
                         'No per-call failure counts or raw spans are exported. No performance or quality acceptance.')


def build_report(args):
    result = dict(schema=VERSION, tool=dict(version=VERSION, python=sys.version.split()[0],
                      source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      events_reducer_sha256=hashlib.sha256(Path(events.__file__).read_bytes()).hexdigest()),
                  limitations=LIMITS, inputs=[], observations=[])
    embedded = {}
    options = [('events', args.events, summarize_events), ('time_l', args.time_l, summarize_time),
               ('arm', args.arm, summarize_arm), ('capture', args.capture, summarize_capture),
               ('observer', args.observer, summarize_observer), ('pair', args.pair, summarize_pair),
               ('quality', args.quality, summarize_quality), ('identity', args.identity, safe_identity)]
    for kind, paths, summarizer in options:
        seen = set()
        for path in paths:
            data, source = read_input(path)
            require(source['sha256'] not in seen, 'duplicate input content for ' + kind)
            seen.add(source['sha256'])
            source.update(kind=kind, input_id=len(result['inputs']))
            result['inputs'].append(source)
            summary = summarizer(data if kind in ('events', 'time_l') else strict_json(data))
            item = dict(input_id=source['input_id'], kind=kind, report=summary)
            result['observations'].append(item)
            if kind == 'arm':
                for attempt in summary['attempts']:
                    if attempt['capture'] is not None:
                        key = attempt['capture']['trace_content_sha256']
                        embedded.setdefault(key, []).append(dict(input_id=source['input_id'], attempt_index=attempt['index']))
            if kind == 'capture':
                item['identical_embedded_traces'] = embedded.get(summary['trace_content_sha256'], [])
                item['accounting'] = 'Cross-check only when embedded match exists; never added to arm metrics.'
    return result


def readable(report):
    lines = ['CDP cost ledger (offline observations only)', 'Performance/quality acceptance: UNKNOWN; independent decode: UNRUN']
    for item in report['observations']:
        source = report['inputs'][item['input_id']]
        data = item['report']
        lines += ['', source['kind'] + ': ' + source['path'], 'SHA256 ' + source['sha256']]
        if item['kind'] == 'events':
            lines.append('Internal run seconds: ' + str(data['internal_run_wall_seconds']['value']))
            lines.append('Evidence complete: ' + str(data['evidence_complete']) + '; attempt outcomes: ' + str(data['terminal_outcomes']['attempts']))
            for scope, group in data['intervals'].items():
                lines.append(scope + ' union seconds: ' + str(group['union_seconds']) + '; work seconds: ' + str(group['work_seconds']))
        elif item['kind'] == 'time_l':
            lines.append('External time-l real seconds: ' + str(data['external_real_seconds']['value']))
        elif item['kind'] == 'arm':
            lines.append('Arm ' + str(data['arm']) + ' internal seconds: ' + str(data['internal_arm_wall_seconds']['value']))
            for name, batch in data['batches'].items():
                lines.append(name + ': layer frames=' + str(batch['rendered_layer_frames']['value']) + '; PNG bytes=' + str(batch['png_bytes']['value']) + '; outcomes=' + str(batch['outcomes']))
                lines.append('Successful-sample timing sums ms: ' + ', '.join(k + '=' + str(v['value']) for k, v in batch['timing_sum_ms'].items()))
        elif item['kind'] == 'capture':
            lines.append('Capture outcomes: ' + str(data['capture_outcomes']) + '; recovery attempts: ' + str(data['recovery_attempts']['value']))
            lines.append('Identical embedded traces: ' + str(item['identical_embedded_traces']) + '; never added to arm metrics')
        elif item['kind'] == 'pair':
            lines.append('Runner complete seconds: ' + str(data['runner_complete_wall_seconds']['value']) + '; reported cleanup: ' + str(data['cleanup_status']))
        elif item['kind'] == 'quality':
            lines.append('Recorded frame outcomes: ' + str(data['recorded_frame_outcomes']) + '; independent decode UNRUN')
        elif item['kind'] == 'observer':
            lines.append('Context: ' + data['context'] + '; raw spans unavailable')
        elif item['kind'] == 'identity':
            lines.append('Available producer hashes: ' + json.dumps(data, sort_keys=True))
        if 'boundary' in data:
            lines.append(data['boundary'])
    lines += ['', 'UNOBSERVED/UNKNOWN: actual poll sleep, encoder-only cost, process-tree peak, unrecorded failures, full-film effect.']
    lines += report['limitations']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('events', 'time-l', 'arm', 'capture', 'observer', 'pair', 'quality', 'identity'):
        parser.add_argument('--' + option, type=Path, action='append', default=[], help='Explicit existing input; repeatable')
    parser.add_argument('--format', choices=('json', 'text'), default='json')
    parser.add_argument('--output', type=Path, help='Explicit NEW JSON file; default stdout; parent must exist')
    args = parser.parse_args(argv)
    if not any(getattr(args, key) for key in ('events', 'time_l', 'arm', 'capture', 'observer', 'pair', 'quality', 'identity')):
        parser.error('At least one explicit input is required')
    try:
        report = build_report(args)
        payload = readable(report) if args.format == 'text' else json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        if args.output is None:
            sys.stdout.write(payload)
        else:
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(payload)
    except (ValueError, OSError, UnicodeError, TypeError) as error:
        print('CDP cost report refused: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
