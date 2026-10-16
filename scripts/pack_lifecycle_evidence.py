#!/usr/bin/env python3
"""Bounded private hosted #63 evidence bundle; never certifies full #63 completion."""
import argparse
import hashlib
import json
import os
import math
import re
import statistics
import subprocess
import sys
import uuid
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LIMIT = 32 * 1024 * 1024
TOP = {'owner.json', 'preflight.json', 'summary.json'}
ARM = {'timeline.json', 'events.jsonl', 'runtime-raw.json', 'gfx-run.json',
       'command-result.json', 'lifecycle-summary.json'}


def read_regular(path, remaining):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > remaining:
            raise ValueError('evidence file unavailable or bundle exceeds 32 MiB')
        data = stream.read(remaining + 1)
        after = os.fstat(stream.fileno())
    if len(data) != before.st_size or before != after:
        # atime changes caused by the read itself are immaterial.
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if len(data) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in keys):
            raise ValueError('evidence changed while packing')
    return data


def checkout_identity():
    command = ['git', '-C', str(ROOT)]
    head = subprocess.check_output(command + ['rev-parse', 'HEAD'], text=True, timeout=5).strip()
    clean = not subprocess.check_output(command + ['status', '--porcelain'], text=True, timeout=5).strip()
    return head, clean


def validate_bundle(payload, prefix, root):
    """Check retained producer records, without rerendering or reading PNGs."""
    from scripts import bench_lifecycle as bench, gfx_evidence as evidence
    from vtx import events, gfx_runtime
    require = evidence.require
    def obj(name):
        value = json.loads(payload[str(prefix / name)], parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        require(isinstance(value, dict) and bool(value), 'missing/nonobject record: ' + name)
        evidence.digest(value)  # reject nonfinite numbers, including exponent overflow
        return value
    def uuid_ok(value):
        try:
            return isinstance(value, str) and str(uuid.UUID(value)) == value
        except (ValueError, AttributeError):
            return False
    def zero(value):
        return type(value) is int and value == 0
    def number(value):
        return type(value) in (float, int) and math.isfinite(value) and value >= 0
    source = json.loads(payload['source.json'])
    result = json.loads(payload['runner-result.json'])
    evidence.digest(source); evidence.digest(result)
    require(isinstance(source, dict) and all(isinstance(source.get(k), str) and re.fullmatch('[0-9a-f]{40}', source[k])
            for k in ('head_sha', 'expected_head_sha')) and source.get('clean') is True,
            'missing or invalid clean source identity')
    head = source['head_sha']
    actual, clean = checkout_identity()
    require(clean and head == source['expected_head_sha'] == actual and
            ('EXPECTED_HEAD' not in os.environ or os.environ['EXPECTED_HEAD'] == head), 'actual/workflow source mismatch')
    require(isinstance(result, dict) and zero(result.get('return_code')), 'runner failed/invalid return code')
    owner, preflight, summary = (obj(name) for name in ('owner.json', 'preflight.json', 'summary.json'))
    require(owner.get('profile') == bench.PROFILE and uuid_ok(owner.get('id')), 'invalid owner identity')
    runtime, environment = preflight['runtime'], preflight['environment']
    require(uuid_ok(runtime.get('run_id')) and uuid_ok(environment.get('run_id')), 'invalid preflight identity')
    identity = gfx_runtime.identity(runtime)
    host = bench.stable_host(environment)
    require(host['git_sha'] == head and host['git_clean'] is True, 'preflight source mismatch')
    require(isinstance(host['font_configuration_files'], dict) and bool(host['font_configuration_files']) and
            all(isinstance(k, str) and k and isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v)
                for k, v in host['font_configuration_files'].items()) and isinstance(host['font_environment'], dict),
            'invalid font configuration inventory')
    # Media is deliberately excluded from the archive. Hash its existing bounded
    # fixture bytes now, retaining the digest alongside the HTML/timeline hashes.
    fixture = obj('fixture/timeline.json')
    project = fixture.get('project', {})
    require(all(project.get(k) == v for k, v in {'fps': 30, 'width': 1920, 'height': 1080,
            'duration_frames': 60, 'cache_dependencies': ['media.mp4']}.items()), 'wrong bounded fixture')
    required = {(f'page{i}.html', 30) for i in range(4)}
    clips = [(clip['src'], clip['dur']) for track in fixture['video_tracks'] if track.get('kind') == 'gfx'
             for clip in track['clips']]
    require(len(clips) == 4 and set(clips) == required, 'wrong fixture pages')
    hashes = {}
    for name in ['media.mp4'] + [f'page{i}.html' for i in range(4)]:
        data = read_regular(root / 'fixture' / name, 128 * 1024 * 1024) if name == 'media.mp4' else payload[str(prefix / 'fixture' / name)]
        require(bool(data), 'empty fixture input')
        hashes[name] = hashlib.sha256(data).hexdigest()
    timeline = payload[str(prefix / 'fixture/timeline.json')]
    fingerprint = evidence.digest({'timeline_sha256': hashlib.sha256(timeline).hexdigest(),
                                  'files': [[name, hashes[name]] for name in sorted(hashes)]})
    expected = {(i, scope) for i in range(3) for scope in ('gfx', 'chain')}
    rounds = summary.get('rounds')
    require(summary.get('profile') == bench.PROFILE and summary.get('paired_runs_complete') is True and
            summary.get('full_issue63_complete') is False and isinstance(rounds, list) and len(rounds) == 6,
            'incomplete paired matrix')
    require(all(isinstance(r, dict) and type(r.get('round')) is int and isinstance(r.get('scope'), str) for r in rounds), 'invalid round types')
    round_map = {(r['round'], r['scope']): r for r in rounds}
    require(set(round_map) == expected, 'invalid matrix scopes/rounds')
    seen_runs, seen_executions = set(), set()
    for i, scope in sorted(expected):
        row = round_map[i, scope]
        plan, golden, comparison = (obj(f'{name}-{i}-{scope}.json') for name in ('plan', 'golden', 'comparison'))
        require(plan.get('schema') == 'vtx.gfx_comparison_plan/1' and
                set(evidence.instances(plan.get('required_instances'))) == required and
                plan.get('input_fingerprint') == fingerprint and plan.get('runtime_identity') == identity and
                plan.get('host_identity') == host and plan.get('variable_axes') == ['config.lifecycle', 'config.process_samples'],
                'frozen plan mismatch')
        require(isinstance(plan.get('arms'), dict) and set(plan['arms']) == {'baseline', 'candidate'}, 'invalid plan arms')
        evidence._manifest({'run_id': environment['run_id'], 'env_manifest': environment,
            'config': plan['arms']['baseline']['config']}, plan['arms']['baseline'], '1920x1080')
        require(environment['runtime_backend_truth']['evidence'] == identity['evidence'], 'preflight backend mismatch')
        plan_hash = evidence.digest(plan)
        require(comparison.get('schema') == 'vtx.gfx_evidence_report/1' and comparison.get('ok') is True and
                comparison.get('same_version_causal') is True and comparison.get('compared_frames') == 120 and
                comparison.get('hashed_frames') == 240 and comparison.get('plan_sha256') == plan_hash and
                all(comparison.get(k) == [] for k in ('different_frames', 'missing_frames', 'unknown_provenance',
                    'uncovered_instances', 'errors', 'confounders')), 'incomplete/inconsistent comparison')
        require(row.get('ok') is True and row.get('compared_frames') == 120 and
                row.get('order') == (['baseline', 'candidate'] if i % 2 == 0 else ['candidate', 'baseline']), 'round summary mismatch')
        runs = {}
        for side in ('baseline', 'candidate'):
            directory = f'round-{i}-{scope}-{side}'
            run, command, raw, lifecycle = (obj(directory + '/' + name) for name in (
                'gfx-run.json', 'command-result.json', 'runtime-raw.json', 'lifecycle-summary.json'))
            runs[side] = run
            config = bench.arm_config(side, scope)
            arm = plan['arms'][side]
            require(uuid_ok(arm.get('run_id')) and arm['run_id'] not in seen_runs, 'invalid/repeated arm UUID')
            seen_runs.add(arm['run_id'])
            require(run.get('schema') == 'vtx.gfx_run/1' and run.get('complete') is True and
                    run.get('run_id') == arm['run_id'] and run.get('config') == arm.get('config') == config and
                    run.get('plan_sha256') == plan_hash and run.get('input_fingerprint') == fingerprint,
                    'invalid arm receipt/plan/config binding')
            evidence._manifest(run, arm, '1920x1080')
            require(evidence.digest(run['env_manifest']) == run.get('env_sha256') and
                    bench.stable_host(run['env_manifest']) == host and
                    run['env_manifest']['runtime_backend_truth']['evidence'] == identity['evidence'], 'arm environment/source mismatch')
            require(payload[str(prefix / directory / 'timeline.json')] == timeline, 'arm timeline drift')
            require(run.get('timeline_path') == str(root / directory / 'timeline.json') and
                    run.get('events_path') == str(root / directory / 'events.jsonl'), 'arm path mismatch')
            data = payload[str(prefix / directory / 'events.jsonl')]
            parsed = events.read_events(data.decode('utf-8'))
            eid = run.get('execution_run_id')
            require(uuid_ok(eid) and eid not in seen_executions and parsed['complete'] and zero(parsed['exit_code']) and
                    parsed['run_id'] == eid and hashlib.sha256(data).hexdigest() == run.get('events_sha256'),
                    'unclosed/mismatched arm event stream')
            seen_executions.add(eid)
            require(zero(command.get('return_code')) and command.get('execution_run_id') == eid and
                    number(command.get('wall_seconds')) and command['wall_seconds'] > 0 and
                    all(isinstance(command.get(k), list) and len(command[k]) == 3 and all(number(v) for v in command[k])
                        for k in ('load_before', 'load_after')), 'failed/invalid arm command')
            require(lifecycle == events.lifecycle_summary(data.decode('utf-8')) and row['lifecycle'][side] == lifecycle and
                    row['wall_seconds'][side] == command['wall_seconds'] and
                    row['scope_gfx_frames_per_second'][side] == 120 / command['wall_seconds'], 'arm summary mismatch')
            binding = run['producer_binding']; runtimes, witnesses = binding['runtime'], binding['page_witnesses']
            require(isinstance(raw, dict) and 0 < len(raw) <= 256 and all(uuid_ok(k) for k in raw) and
                    {evidence.digest(v): v for v in raw.values()} == runtimes and bool(witnesses), 'raw runtime binding mismatch')
            for key, value in runtimes.items():
                require(key == evidence.digest(value) and value.get('run_id') == run['run_id'] and
                        gfx_runtime.identity(value) == identity and [a for a in value['effective_argv'] if a.startswith('--force-color-profile')] ==
                        ['--force-color-profile=srgb'], 'runtime mismatch')
            for key, value in witnesses.items():
                require(key == evidence.digest(value) and value.get('source') == 'page-cdp-eval' and
                        value.get('viewport') == {'width': 1920, 'height': 1080, 'device_scale': 1} and
                        value.get('alpha_path') in ('opaque', 'override', 'twoshot', 'canvas'), 'page witness mismatch')
            commits = {r['seq']: r['extensions'] for r in parsed['events'] if r['event'] == 'frame_commit'}
            for fields in commits.values():
                require(fields.get('runtime_sha256') in runtimes and fields.get('page_witness_sha256') in witnesses, 'unbound publication')
            instances = evidence.instances(run.get('instances'))
            require(set(instances) == required, 'arm instance mismatch')
            used_sequences, instance_keys = set(), set()
            for (src, dur), instance in instances.items():
                frames = instance.get('frames')
                require(isinstance(frames, dict) and set(frames) == {f'{n:04d}' for n in range(dur)}, 'frame coverage mismatch')
                keys = {f.get('gfx_key') for f in frames.values()}
                require(len(keys) == 1 and not keys & instance_keys, 'aliased instance publication keys')
                instance_keys.update(keys)
                for n, frame in frames.items():
                    require(type(frame.get('publication_seq')) is int and frame['publication_seq'] not in used_sequences, 'reused publication sequence')
                    used_sequences.add(frame['publication_seq'])
                    fields = commits.get(frame.get('publication_seq'))
                    require(isinstance(fields, dict) and frame.get('run_id') == run['run_id'] and
                            frame.get('origin') == fields['frame_origin'] == 'rendered' and fields['frame_index'] == int(n) and
                            re.fullmatch('[0-9a-f]{64}', frame.get('sha256', '')) and all(frame.get(k) == fields.get(k)
                            for k in ('gfx_key', 'png_sha256', 'runtime_sha256', 'page_witness_sha256')), 'frame/event binding mismatch')
                    gold = golden['pages'][src]['durs'][str(dur)]
                    require(gold.get('mode') == 'full' and gold.get('dur') == dur and set(gold['frames']) == set(frames) and gold['frames'].get(n) == frame['sha256'], 'Golden/arm digest mismatch')
        require(golden['meta'].get('run_evidence') == runs['baseline'] and
                golden['meta'].get('env_manifest') == runs['baseline']['env_manifest'] and
                golden['meta'].get('fps') == 30 and golden['meta'].get('viewport') == '1920x1080' and
                comparison.get('run_ids') == {s: r['run_id'] for s, r in runs.items()} and
                comparison.get('actual_environments') == {s: r['env_manifest'] for s, r in runs.items()} and
                row.get('paired_delta_seconds') == row['wall_seconds']['candidate'] - row['wall_seconds']['baseline'],
                'Golden/comparison/round identities differ')
    for scope in ('gfx', 'chain'):
        for side in ('baseline', 'candidate'):
            walls = [round_map[i, scope]['wall_seconds'][side] for i in range(3)]
            require(summary['summary'][scope][side] == {'median_seconds': statistics.median(walls),
                'range_seconds': [min(walls), max(walls)]}, 'matrix statistics mismatch')
    return hashes


def pack(parent, output):
    parent, output = Path(parent), Path(output)
    if parent.resolve(strict=True) != parent or not parent.is_dir():
        raise ValueError('evidence parent must be canonical')
    output.mkdir()  # never overwrite prior evidence
    status = {'schema': 'issue63.hosted_bundle/1', 'evidence_complete': False,
              'partial_acceptance': False, 'full_issue63_complete': False, 'errors': []}
    payload = {}
    def include(path, name):
        if len(payload) >= 256:
            raise ValueError('too many selected evidence files')
        payload[name] = read_regular(path, LIMIT - sum(map(len, payload.values())))
    try:
        for name in ('source.json', 'runner-result.json', 'runner.log'):
            include(parent / name, name)
        roots = list(parent.glob('issue63-*'))
        if len(roots) != 1 or roots[0].is_symlink() or not roots[0].is_dir():
            raise ValueError('expected exactly one fresh experiment root')
        root = roots[0]
        members = sorted(root.iterdir())
        if len(members) > 256:
            raise ValueError('too many evidence entries')
        for path in members:
            if path.is_symlink():
                raise ValueError('symlink in experiment evidence')
            if path.name in TOP or path.name.startswith(('plan-', 'golden-', 'comparison-')) and path.suffix == '.json':
                include(path, str(path.relative_to(parent)))
            elif path.is_dir() and (path.name == 'fixture' or path.name.startswith('round-')):
                children = sorted(path.iterdir())
                if len(children) > 256:
                    raise ValueError('too many arm entries')
                for child in children:
                    if child.name in ARM or path.name == 'fixture' and child.suffix == '.html':
                        include(child, str(child.relative_to(parent)))
        if len(payload) > 256:
            raise ValueError('too many selected evidence files')
        status['fixture_file_sha256'] = validate_bundle(payload, root.relative_to(parent), root)
        status['evidence_complete'] = status['partial_acceptance'] = True
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError, subprocess.SubprocessError) as error:
        status['errors'].append(type(error).__name__ + ': ' + str(error))
    # Any over-budget collection is a failed evidence gate. Never silently trim a
    # successful receipt set. Available partial payload is explicitly incomplete.
    status['files'] = {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                       for name, data in sorted(payload.items())}
    status['payload_bytes'] = sum(map(len, payload.values()))
    metadata = (json.dumps(status, sort_keys=True, indent=2) + '\n').encode()
    if status['payload_bytes'] + 2 * len(metadata) + 1024 * len(payload) > LIMIT:
        status.update(evidence_complete=False, partial_acceptance=False, payload_bytes=0)
        status['errors'].append('bundle metadata exceeds total budget; payload omitted')
        payload = {}
        metadata = (json.dumps(status, sort_keys=True, indent=2) + '\n').encode()
    with zipfile.ZipFile(output / 'evidence.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('evidence-status.json', metadata)
        for name, data in sorted(payload.items()):
            archive.writestr(name, data)
    (output / 'evidence-status.json').write_bytes(metadata)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if pack(args.parent, args.output)['partial_acceptance'] else 1)


if __name__ == '__main__':
    main()
