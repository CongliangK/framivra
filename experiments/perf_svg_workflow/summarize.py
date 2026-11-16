"""Offline SVG #345 evidence projection. Never imports the runner or opens media.

Run only after the observer has stopped writing evidence. stdout is public JSON;
exit 0 means evidence-qualified and median cold all new/base <= .70, not artistic
approval. Exit 1 preserves an incomplete/failed result. No raw evidence is copied.
"""
import argparse
from collections import defaultdict
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import statistics
import sys

# Anchor at the repository root, never at repo/vtx (which shadows stdlib modules).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vtx.runtime.events import read_events


HEX = re.compile(r'^[0-9a-f]{64}$')
STAGES = {'validate', 'audit', 'prepare', 'gfx', 'audio', 'video', 'mux', 'qc', 'finish'}
LIMITATIONS = [
    'Cold means new project cache/out, not OS cold; warm follows independent verification.',
    'RSS is sampled descendant RSS with shared-page double counting and missed peaks/children; not peak physical memory.',
    'CPU is a sum of sampled per-PID maxima, a lower bound with platform precision and missing-child limitations.',
    'IO is sampled per-PID lifetime-counter maxima, not exact command deltas, physical device IO, latency or logical file bytes; PID reuse and missing children can undercount.',
    'IO sampling is bounded to 256 processes and a cooperative 0.1 second budget; kernel calls cannot be interrupted; unsupported counters are null.',
    'Host memory is systemwide before/after snapshots outside command elapsed time, not peaks or causal attribution; free-percent proxy differs from kernel pressure level.',
    'Logical cache/output/PNG bytes are not exclusive physical disk space.',
    'PNG cache/certificate snapshots are retained per-state after separate strict QC, not independent pre-strict all snapshots. MP4 snapshots precede strict QC and the runner checks final bytes remain unchanged.',
    'Default core cleanup removes the intermediate mixed WAV. Final decoded audio sample-clock/digest equality is not direct mixed-WAV sample/hash evidence; missing mixed-WAV facts are unavailable, never inferred.',
    'No GPU, energy, OS-cold, manual full viewing, artistic or listening approval is claimed.',
    'Stage wall attribution uses interval unions, not summed parallel task work. Certificate raster times describe original build provenance, never warm work.',
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def object_digest(value):
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and '..' not in path.parts and all(re.fullmatch(r'[A-Za-z0-9_.+ -]+', p) for p in path.parts)


def hashes(value, canonical=False):
    """Old screen observers included preparation provenance in input_sha256."""
    result = {}
    for key, val in (value or {}).items():
        if not safe_relative(key) or not isinstance(val, str) or not HEX.fullmatch(val):
            continue
        if canonical and (PurePosixPath(key).parts[0] in {'logs', '.generated'} or key == 'realization.json'):
            continue
        result[key] = val
    return result


def numeric_fields(value, keys):
    return {key: value[key] for key in keys if key in value and (value[key] is None or number(value[key]))}


def diagnostics(value):
    # Error strings can contain argv, host paths or environment; export counts only.
    return {'error_count': len(value.get('errors') or []),
            'reason_present': bool(value.get('reason') or any((value.get('reasons') or {}).values()))}


def resources(command):
    raw = command.get('resources') or {}
    out = numeric_fields(raw, ['samples', 'sample_interval_seconds', 'peak_tree_rss_bytes',
                               'cpu_seconds_lower_bound', 'peak_sampled_processes'])
    out['status'] = raw.get('status') if raw.get('status') in {'sampled', 'partial', 'unavailable', 'unsupported'} else 'unknown'
    out['method'] = 'ps-descendant-tree' if raw.get('method') == 'ps-descendant-tree' else 'unknown'
    out.update(diagnostics(raw))
    io = raw.get('disk_io') or {}
    out['disk_io'] = numeric_fields(io, ['read_bytes_lower_bound', 'written_bytes_lower_bound',
        'selected_process_observations', 'attempted_process_observations',
        'successful_process_observations', 'observed_processes'])
    out['disk_io'].update(diagnostics(io))
    out['disk_io']['status'] = io.get('status') if io.get('status') in {'sampled', 'partial', 'unavailable', 'unsupported'} else 'unknown'
    host = command.get('host_memory') or {}
    out['host_memory'] = {}
    for endpoint in ['before', 'after']:
        row = host.get(endpoint) or {}
        item = numeric_fields(row, ['available_metrics', 'expected_metrics'])
        item.update(diagnostics(row))
        for key, fields in [('memory_pressure', ['system_free_percent']), ('pressure_level', ['value']),
                            ('swapusage', ['total_bytes', 'used_bytes', 'free_bytes'])]:
            item[key] = numeric_fields(row.get(key) or {}, fields)
        out['host_memory'][endpoint] = item
    out['observer_overhead_seconds'] = numeric_fields(command.get('overhead_seconds') or {}, ['setup', 'sampler_join', 'after_wait'])
    return out


def command_projection(raw):
    return {'elapsed_seconds': raw.get('elapsed_seconds') if number(raw.get('elapsed_seconds')) else None,
            'exit_code': raw.get('exit_code') if isinstance(raw.get('exit_code'), int) else None,
            'timed_out': raw.get('timed_out') is True, 'error_present': bool(raw.get('error')),
            'resources': resources(raw)}


def command_ok(raw):
    return (raw.get('exit_code') == 0 and raw.get('timed_out') is False
            and not raw.get('error') and number(raw.get('elapsed_seconds')))


def union_seconds(intervals):
    end = total = 0.0
    for start, stop in sorted(intervals):
        total += max(0, stop - max(start, end))
        end = max(end, stop)
    return total / 1000


def event_projection(rows):
    """Accept only stage terminal intervals; process/task/attempt spans overlap."""
    stages = defaultdict(list)
    valid = bool(rows) and rows[0].get('event') == 'run_start' and rows[-1].get('event') == 'run_terminal'
    run_id = rows[0].get('run_id') if rows else None
    starts, terminals = {}, set()
    for i, row in enumerate(rows, 1):
        valid &= row.get('seq') == i and row.get('run_id') == run_id
        task = row.get('task_id')
        if row.get('event') == 'task_start':
            valid &= task not in starts
            starts[task] = (row.get('stage'), row.get('scope'))
        if row.get('event') == 'task_terminal':
            valid &= task in starts and task not in terminals and starts.get(task) == (row.get('stage'), row.get('scope'))
            terminals.add(task)
        if row.get('event') == 'receipt_commit':
            valid &= task in starts and task not in terminals and starts.get(task, (None,))[0] == row.get('stage')
            valid &= (row.get('extensions') or {}).get('receipt_stage') == row.get('stage')
        if row.get('event', '').endswith('_terminal'):
            valid &= row.get('exit_code') == 0 and row.get('outcome') == 'succeeded' and not row.get('error')
        if row.get('scope') != 'stage' or row.get('event') != 'task_terminal':
            continue
        stage = row.get('stage')
        timing = row.get('timing') or {}
        start, stop = timing.get('start_ms'), timing.get('offset_ms')
        if stage not in STAGES or timing.get('unit') != 'ms' or not number(start) or not number(stop) or stop < start:
            valid = False
            continue
        stages[stage].append((start, stop))
    valid &= set(starts) == terminals and bool(stages)
    valid &= bool(rows and (rows[-1].get('extensions') or {}).get('evidence_complete') is True)
    return {'complete_success': bool(valid),
            'stage_union_seconds': {key: union_seconds(val) for key, val in sorted(stages.items())},
            'all_stages_union_seconds': union_seconds([span for spans in stages.values() for span in spans])}


class Evidence:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.index = {}

    def load(self, relative, lines=False):
        if not safe_relative(relative):
            raise ValueError('unsafe evidence filename')
        path = self.root / relative
        # Symlinks cannot escape the evidence root, even for text artifacts.
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('evidence escapes root')
        data = path.read_bytes()
        self.index[relative] = {'sha256': digest(data), 'bytes': len(data)}
        if lines:
            # Preserve original bytes: newline truncation, duplicate keys, attempts,
            # parent lifetimes and receipt attribution belong to the strict reducer.
            parsed = read_events(data)
            if not parsed['complete'] or parsed['exit_code'] != 0:
                raise ValueError('incomplete or failed execution evidence')
            return parsed['events']
        return json.loads(data)


def qc_pass(value):
    checks = value.get('checks')
    return bool(checks) and all(row.get('pass') is True for row in checks) and not value.get('skipped_probes')


def exact_pts_pass(value):
    try:
        stream = value['streams'][0]
        tb = Fraction(stream['time_base'])
        frames = value['frames']
        return (len(frames) == 900 and stream['duration_ts'] * tb == 30
                and all(frame['pts'] * tb == Fraction(i, 30) for i, frame in enumerate(frames)))
    except (KeyError, IndexError, ValueError, TypeError, ZeroDivisionError):
        return False


def summarize_trial(evidence, name):
    issues = []
    result = {'trial': name, 'issues': issues, 'states': {}}
    try:
        raw = evidence.load(name + '/summary.json')
        result['passed'] = raw.get('status') == 'passed'
        if not result['passed']:
            issues.append('trial_not_passed')
        result['failure_present'] = bool(raw.get('error'))
        result['source_sha256'] = hashes(raw.get('source_sha256'))
        result['input_sha256'] = hashes(raw.get('input_sha256'), canonical=True)
        canonical_raw = {key: val for key, val in (raw.get('input_sha256') or {}).items()
                         if key != 'realization.json' and not key.startswith(('logs/', '.generated/'))}
        if len(result['source_sha256']) != len(raw.get('source_sha256') or {}) or len(result['input_sha256']) != len(canonical_raw):
            issues.append('invalid_identity_manifest_entry')
        result['preparation_provenance_count'] = len(raw.get('preparation_provenance_sha256') or {}) + len(hashes(raw.get('input_sha256'))) - len(result['input_sha256'])
        # Tool paths are private; public labels are opaque stable positions by basename.
        tools = raw.get('tools_sha256') or {}
        result['tools_sha256'] = sorted(v for v in tools.values() if isinstance(v, str) and HEX.fullmatch(v))
        for key in ['harness_sha256', 'helper_sha256']:
            val = raw.get(key)
            result[key] = val if isinstance(val, str) and HEX.fullmatch(val) else None
        if not all(result.get(key) for key in ['source_sha256', 'input_sha256', 'tools_sha256', 'harness_sha256', 'helper_sha256']):
            issues.append('missing_identity')
        environment = raw.get('environment') or raw.get('env') or {}
        result['default_configuration_observed'] = bool(environment) and not any(key.startswith('VTX_') and key not in {'VTX_CONFIG', 'VTX_INKSCAPE'} for key in environment)
        if not result['default_configuration_observed']:
            issues.append('default_configuration_not_established')
        commands = raw.get('commands') or {}
        result['commands'] = {}
        for label in ['prepare', 'verify', 'cold-all', 'warm-all', 'cold-strict', 'warm-strict']:
            command = commands.get(label) or {}
            result['commands'][label] = command_projection(command)
            if not command_ok(command):
                issues.append(label + '_command_failed_or_missing')
        prepare = result['commands']['prepare']['elapsed_seconds']
        result['prepare_seconds'] = prepare
        for state in ['cold', 'warm']:
            matching = [row for row in raw.get('states', []) if row.get('state') == state]
            if len(matching) != 1:
                issues.append(state + '_missing_or_duplicate')
                continue
            row = matching[0]
            media = row.get('media') or {}
            item = {'all_seconds': result['commands'][state + '-all']['elapsed_seconds'],
                    'strict_seconds': result['commands'][state + '-strict']['elapsed_seconds']}
            result['states'][state] = item
            item['prepare_plus_all_seconds'] = prepare + item['all_seconds'] if state == 'cold' and number(prepare) and number(item['all_seconds']) else None
            item['media_checks_passed'] = (media.get('status') == 'passed' and bool(media.get('checks'))
                                          and all(v is True for v in media.get('checks', {}).values()) and not media.get('error'))
            item['digests'] = {key: media.get(key) for key in ['video_digest', 'audio_digest', 'audio_timeline_digest'] if isinstance(media.get(key), str) and HEX.fullmatch(media[key])}
            mix = row.get('mix_audio') or {}
            item['mixed_wav'] = numeric_fields(mix, ['samples', 'sample_rate', 'channels', 'sample_bytes'])
            if isinstance(mix.get('sha256'), str) and HEX.fullmatch(mix['sha256']):
                item['mixed_wav']['sha256'] = mix['sha256']
            item['mixed_wav_evidence_available'] = bool(item['mixed_wav'])
            timing = row.get('decoded_audio') or media.get('audio_timing') or {}
            item['decoded_audio'] = numeric_fields(timing, ['sample_rate', 'channels'])
            samples = timing.get('samples', timing.get('sample_frames'))
            item['decoded_audio']['sample_frames'] = samples if number(samples) else None
            if timing.get('sample_format') == 's16le':
                item['decoded_audio']['sample_format'] = 's16le'
            for key in ['decoded_duration', 'source_duration', 'start']:
                expected = '0' if key == 'start' else '30'
                # Fixed public values only; never export arbitrary string content.
                if str(timing.get(key)) == expected:
                    item['decoded_audio'][key] = expected
            item['decoded_audio']['audio_digest'] = item['digests'].get('audio_digest')
            item['decoded_audio_clock_passed'] = (samples == 1440000
                and timing.get('sample_rate') == 48000 and timing.get('channels') == 2
                and item['decoded_audio'].get('decoded_duration') == '30'
                and item['decoded_audio'].get('source_duration') == '30'
                and bool(item['decoded_audio']['audio_digest'])
                and (not row.get('decoded_audio') or timing.get('pcm_sha256') == item['decoded_audio']['audio_digest']))
            item['decoded_audio_timing_source'] = 'decoded_audio' if row.get('decoded_audio') else 'media.audio_timing'
            receipt = row.get('mixed_audio_receipt') or {}
            item['mixed_audio_receipt'] = {key: receipt[key] for key in ['input_i', 'output_i', 'gain_db', 'target', 'tp_dbfs']
                if key in receipt and (receipt[key] is None or (isinstance(receipt[key], (int, float))
                    and not isinstance(receipt[key], bool) and math.isfinite(receipt[key])))}
            if isinstance(receipt.get('sha256'), str) and HEX.fullmatch(receipt['sha256']):
                item['mixed_audio_receipt']['sha256'] = receipt['sha256']
            item['mixed_audio_receipt']['scope'] = 'product post-loudness receipt, not an independently retained WAV snapshot'
            item['logical_bytes'] = numeric_fields(row.get('disk_logical_bytes') or {}, ['cache', 'out'])
            item['png_bytes'] = row.get('png_bytes') if number(row.get('png_bytes')) else None
            certificate = evidence.load(name + '/' + state + '-svg-complete.json')
            sequence = certificate.get('sha256') or []
            valid_png = len(sequence) == 900 and all(isinstance(v, str) and HEX.fullmatch(v) for v in sequence)
            item['png_sequence_sha256'] = digest(json.dumps(sequence).encode()) if valid_png else None
            cache = evidence.load(name + '/' + state + '-cache.json')
            cache_pngs = {PurePosixPath(key).name: val.get('sha256') for key, val in cache.items() if safe_relative(key)}
            item['png_900_passed'] = bool(valid_png and len(cache) == len(cache_pngs) == 900
                and [cache_pngs.get('f%04d.png' % i) for i in range(900)] == sequence
                and item['png_sequence_sha256'] == row.get('png_sequence_sha256') and certificate.get('export_count') == 900)
            item['certificate_build_provenance'] = numeric_fields(certificate, ['raster_seconds', 'batch_count', 'export_count'])
            item['exact_900_pts_30sec'] = row.get('exact_pts') is True and exact_pts_pass(evidence.load(name + '/' + state + '-exact-pts.json'))
            for mode in ['all', 'strict']:
                item[mode + '_events'] = event_projection(evidence.load(name + '/' + state + '-' + mode + '.events.jsonl', lines=True))
                item[mode + '_qc_passed'] = qc_pass(evidence.load(name + '/' + state + ('-all-qc_report.json' if mode == 'all' else '-strict-qc.json')))
            if (row.get('status') != 'passed' or not item['media_checks_passed'] or len(item['digests']) != 3
                    or not item['decoded_audio_clock_passed'] or not item['png_900_passed'] or not item['exact_900_pts_30sec']
                    or any(not item[mode + '_events']['complete_success'] or not item[mode + '_qc_passed'] for mode in ['all', 'strict'])):
                issues.append(state + '_quality_failed')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        # Exception messages may expose local paths. Keep type only.
        issues.append('unreadable_or_invalid_evidence_' + type(error).__name__)
    result['qualified'] = not issues
    return result


def equality_signature(trial):
    states = trial.get('states') or {}
    return [dict(digests=row.get('digests'), decoded_audio=row.get('decoded_audio'),
                 png_sequence_sha256=row.get('png_sequence_sha256')) for row in states.values()]


def summarize(root, formal_directory='formal', failed_formal_directories=()):
    if not safe_relative(formal_directory) or any(not safe_relative(name) for name in failed_formal_directories):
        raise ValueError('formal directories must be relative evidence names')
    evidence = Evidence(root)
    issues = []
    trials = []
    pairs = []
    for i, order in enumerate([['baseline', 'candidate'], ['candidate', 'baseline'], ['baseline', 'candidate']], 1):
        pair = {}
        for arm in order:
            trial = summarize_trial(evidence, formal_directory + '/pair-%d-%s' % (i, arm))
            trial['arm'] = arm
            trial['pair'] = i
            trials.append(trial)
            pair[arm] = trial
        ratios = {}
        for state in ['cold', 'warm']:
            base = pair['baseline'].get('states', {}).get(state, {}).get('all_seconds')
            new = pair['candidate'].get('states', {}).get(state, {}).get('all_seconds')
            ratios[state + '_all_new_over_base'] = new / base if number(base) and base > 0 and number(new) else None
        pairs.append({'pair': i, 'expected_order': order, **ratios})
    if any(not row['qualified'] for row in trials):
        issues.append('formal_trials_incomplete_or_failed')
    reference = trials[0]
    signatures = [sig for row in trials for sig in equality_signature(row)]
    equal_outputs = len(signatures) == 12 and all(sig == signatures[0] for sig in signatures)
    equal_inputs = bool(reference.get('input_sha256')) and all(row.get('input_sha256') == reference.get('input_sha256') for row in trials)
    equal_tools = bool(reference.get('tools_sha256')) and all(row.get('tools_sha256') == reference.get('tools_sha256') for row in trials)
    equal_observer = all(row.get('harness_sha256') == reference.get('harness_sha256') and row.get('helper_sha256') == reference.get('helper_sha256') for row in trials)
    stable_sources = all(len({object_digest(row.get('source_sha256')) for row in trials if row['arm'] == arm}) == 1 for arm in ['baseline', 'candidate'])
    if not all([equal_outputs, equal_inputs, equal_tools, equal_observer, stable_sources]):
        issues.append('cross_trial_identity_or_output_mismatch')
    formal_status = postmeasure_status = 'missing'
    foreign_count = None
    identities = {}
    try:
        formal = evidence.load(formal_directory + '/summary.json')
        formal_status = 'passed' if formal.get('status') == 'passed' else 'not_passed'
        expected_order = [(i, role) for i, roles in enumerate([['baseline', 'candidate'], ['candidate', 'baseline'], ['baseline', 'candidate']], 1) for role in roles]
        actual_order = [(row.get('pair'), row.get('role')) for row in formal.get('rows', [])]
        if formal_status != 'passed' or actual_order != expected_order or any(row.get('status') != 'passed' for row in formal.get('rows', [])):
            issues.append('formal_controller_incomplete_failed_or_wrong_order')
        samples = formal.get('foreign_media_observations')
        foreign_count = len(samples) if isinstance(samples, list) else None
        if foreign_count != 0:
            issues.append('foreign_media_observed_or_observer_missing')
        for arm in ['baseline', 'candidate']:
            identity = (formal.get('identities') or {}).get(arm) or {}
            identities[arm] = {key: val for key, val in identity.items()
                               if key in {'commit', 'tree', 'original_runtime_commit', 'base_commit', 'observational_patch_sha256', 'events_py_sha256'}
                               and isinstance(val, str) and re.fullmatch(r'[0-9a-f]{7,64}', val)}
            patch = identity.get('common_instrumentation_patch')
            if isinstance(patch, str) and re.fullmatch(r'[0-9a-f]{40}: vtx/runtime/events.py only', patch):
                identities[arm]['common_instrumentation_patch_commit'] = patch[:40]
                identities[arm]['common_instrumentation_patch_scope'] = 'vtx/runtime/events.py only'
        patched = any('common_instrumentation_patch_commit' in row for row in identities.values())
        if patched:
            base_patch = identities['baseline'].get('common_instrumentation_patch_commit')
            base_events = identities['baseline'].get('events_py_sha256')
            if not base_patch or not base_events or any(identity.get('common_instrumentation_patch_commit') != base_patch or identity.get('events_py_sha256') != base_events for identity in identities.values()) or any(row.get('source_sha256', {}).get('vtx/runtime/events.py') != base_events for row in trials):
                issues.append('common_instrumentation_patch_mismatch')
        if any(row.get('harness_sha256') != formal.get('runner_sha256') or row.get('helper_sha256') != formal.get('helper_sha256') for row in trials):
            issues.append('controller_observer_identity_mismatch')
        baseline = identities.get('baseline') or {}
        if not any(baseline.get(key, '').startswith('8b882fa') for key in ['commit', 'original_runtime_commit', 'base_commit']):
            issues.append('baseline_8b882fa_identity_not_established')
        candidate = identities.get('candidate') or {}
        if not candidate.get('commit'):
            issues.append('candidate_measured_identity_missing')
        if not any(candidate.get(key, '').startswith(prefix) for key in ['commit', 'original_runtime_commit', 'base_commit'] for prefix in ['f5762ea', 'b423988', 'f8f6a75']):
            issues.append('candidate_runtime_mapping_not_established')
    except (OSError, ValueError, AttributeError, TypeError):
        issues.append('formal_controller_missing_or_invalid')
    post_commands = {}
    try:
        post = evidence.load('postmeasure.json')
        commands = post.get('commands') or {}
        # Command labels may be local paths too; expose opaque ordinal labels.
        post_commands = {'command_%d' % i: command_projection(row) for i, row in enumerate(commands.values(), 1)}
        postmeasure_status = 'passed' if post.get('status') == 'passed' else 'not_passed'
        if postmeasure_status != 'passed' or not commands or not all(command_ok(row) for row in commands.values()):
            issues.append('postmeasure_missing_or_failed')
    except (OSError, ValueError, AttributeError, TypeError):
        issues.append('postmeasure_missing_or_invalid')
    medians = {}
    for state in ['cold', 'warm']:
        values = [pair[state + '_all_new_over_base'] for pair in pairs]
        medians[state + '_all_new_over_base'] = statistics.median(values) if all(number(v) for v in values) else None
    target = medians['cold_all_new_over_base'] is not None and medians['cold_all_new_over_base'] <= .70
    if not target:
        issues.append('cold_all_median_target_not_met_or_unavailable')
    excluded = []
    for name in ['screen-base', 'screen-candidate', 'ablation-serial']:
        trial = summarize_trial(evidence, name)
        excluded.append({'reason': 'serial_ablation_never_a_denominator' if name == 'ablation-serial' else 'exploration_development_test_overlap', **trial})
    failed_attempts = []
    for directory in failed_formal_directories:
        attempt = {'directory': directory, 'excluded_from_medians': True, 'trials': []}
        try:
            old = evidence.load(directory + '/summary.json')
            attempt['failed'] = old.get('status') == 'failed'
            attempt['failure_present'] = bool(old.get('error'))
            old_foreign = old.get('foreign_media_observations')
            attempt['observed_foreign_media_sample_count'] = len(old_foreign) if isinstance(old_foreign, list) else None
            for row in old.get('rows', []):
                if row.get('pair') in [1, 2, 3] and row.get('role') in ['baseline', 'candidate']:
                    attempt['trials'].append(summarize_trial(evidence, directory + '/pair-%d-%s' % (row['pair'], row['role'])))
        except (OSError, ValueError, AttributeError, TypeError):
            attempt['unreadable'] = True
            issues.append('excluded_failed_attempt_unreadable')
        failed_attempts.append(attempt)
    return {'schema': 'framivra.svg345.public-evidence/1', 'ready': not issues, 'issues': issues,
            'formal_directory': formal_directory, 'measured_identities': identities,
            'excluded_failed_attempts': failed_attempts, 'postmeasure_commands': post_commands,
            'revision_mapping': {'baseline_default_revision': '8b882fa',
                'candidate_public_runtime_revision': 'b423988', 'candidate_prepublication_revision': 'f5762ea',
                'candidate_same_whole_tree': '64a29fa', 'observer_added_revision': 'f8f6a75',
                'mapping_basis': 'operator-supplied metadata rewrite mapping; measured identities are the per-trial source hashes, not an invented measured commit'},
            'trials': trials, 'pairs': pairs, 'median_ratios': medians, 'cold_all_median_le_0_70': target,
            'equality': {'canonical_inputs': equal_inputs, 'tools': equal_tools, 'observer': equal_observer,
                         'source_stable_within_arm': stable_sources, 'all_12_output_signatures': equal_outputs},
            'formal_controller_status': formal_status, 'postmeasure_status': postmeasure_status,
            'observed_foreign_media_sample_count': foreign_count, 'excluded_samples': excluded,
            'limitations': LIMITATIONS, 'evidence_index': evidence.index}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--formal-directory', default='formal', help='explicit completed attempt, relative to evidence root')
    parser.add_argument('--failed-formal-directory', action='append', default=[], help='retain a failed attempt, excluded from medians; repeatable')
    args = parser.parse_args(argv)
    if not safe_relative(args.formal_directory) or any(not safe_relative(name) for name in args.failed_formal_directory):
        parser.error('formal directories must be relative evidence names')
    result = summarize(args.evidence_root, args.formal_directory, args.failed_formal_directory)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    sys.exit(main())
