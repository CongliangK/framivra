"""Project acceptance package v1. Run with python -m vtx.acceptance --help.

An externally retained manifest digest anchors integrity, not producer honesty.
Full hashes here seal exported evidence; they do not upgrade runtime cache keys.
Only project-relative regular files are supported. Use a trusted, quiescent parent
for output and inputs: symlink checks are not a hostile-filesystem sandbox.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from .cache_state import file_digest, media_stat_digest
from . import events, execution_links

SCHEMA = 'vtx.acceptance/1'
STAGES = ('validate', 'audit', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc')
STATUSES = {'passed', 'failed', 'skipped', 'unknown'}
RESERVED_GATES = {'process', 'input_stability', 'tool_versions', 'visual_content', 'media'}
REPO = Path(__file__).resolve().parents[1]
ENV_POLICY = 'isolated: PATH, temporary HOME/XDG_CONFIG_HOME, LC_ALL=C.UTF-8, optional SYSTEMROOT, explicit overrides only'


def safe_path(path):
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('parent traversal is not supported')
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f'symlink is not supported: {part}')
    return path


def relative(name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('invalid relative path')
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ('', '.', '..') for x in name.split('/')) or ':' in name:
        raise ValueError(f'unsafe relative path: {name}')
    return name


def regular(path):
    path = safe_path(path)
    if not path.is_file():
        raise ValueError(f'not a regular file: {path}')
    return path


def read_json(path):
    return events.strict_json_loads(regular(path).read_text(encoding='utf-8'),
                                   duplicate='duplicate JSON key: {value}',
                                   nonfinite='nonfinite JSON number: {value}')


def write_json(path, data):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')


def describe(path):
    p = regular(path)
    return {'sha256': file_digest(p), 'size': p.stat().st_size}


def collect_inputs(timeline):
    """Existing receipt dependency surface, with explicit external-closure limits."""
    tl = read_json(timeline)
    names = {timeline.name: 'timeline'}
    for source in tl.get('sources', []):
        names[relative(source['path'])] = 'source'
    for track in tl.get('video_tracks', []):
        if track.get('kind') == 'gfx':
            for clip in track.get('clips', []):
                names.setdefault(relative(clip['src']), 'gfx')
    from types import SimpleNamespace
    from .cache_state import all_declared_dependencies
    for name in all_declared_dependencies(SimpleNamespace(tl=tl)):
        names.setdefault(relative(name), 'declared_dependency')
    if tl.get('subtitles_src'):
        names.setdefault(relative(tl['subtitles_src']), 'subtitles_src')
    if names.get(timeline.name) != 'timeline':
        raise ValueError('timeline path collides with a dependency')
    for name in names:
        if name.split('/')[0] in ('cache', 'out'):
            raise ValueError('input cannot occupy generated cache/out directories')
    return names


def safe_environment(home=None, overrides=None):
    """Single allowlist for fixture, probe and renderer subprocesses."""
    env = {'PATH': os.environ.get('PATH', os.defpath), 'LC_ALL': 'C.UTF-8'}
    if os.name == 'nt' and 'SYSTEMROOT' in os.environ:
        env['SYSTEMROOT'] = os.environ['SYSTEMROOT']
    if home is not None:
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(Path(home) / 'config'))
    env.update(overrides or {})
    return env


def capture(argv, allow_empty=False):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=15, env=safe_environment())
        return {'status': 'passed' if p.returncode == 0 and (p.stdout.strip() or allow_empty) else 'unknown',
                'argv': argv, 'exit_code': p.returncode,
                'stdout': p.stdout, 'stderr': p.stderr}
    except (OSError, subprocess.SubprocessError) as e:
        return {'status': 'unknown', 'argv': argv, 'error': str(e)}


def environment():
    from . import __version__
    return {'python': platform.python_version(), 'framivra': __version__,
            'platform': platform.platform(),
            'git': capture(['git', '-C', str(REPO), 'rev-parse', 'HEAD']),
            'git_status': capture(['git', '-C', str(REPO), 'status', '--porcelain'], allow_empty=True),
            'kernel_files': {p.relative_to(REPO).as_posix(): describe(p)
                for p in sorted((REPO / 'vtx').rglob('*'))
                if p.suffix in ('.py', '.json') and p.is_file()},
            'tools': {name: capture([name, '-version']) for name in ('ffmpeg', 'ffprobe')},
            'runtime_backend_truth': {'status': 'unknown',
                'reason': 'No browser runtime probe; CPU request is not GPU attestation.'}}


def gates(receipt, qc, exit_code, stable, env):
    if RESERVED_GATES & set(receipt.get('stages', {})):
        raise ValueError('receipt stage collides with a reserved package gate')
    rows = {}
    for stage in sorted(set(STAGES) | set(receipt.get('stages', {}))):
        row = receipt.get('stages', {}).get(stage, {})
        ok = row.get('ok')
        rows[stage] = {'status': 'passed' if ok is True else 'failed' if ok is False else 'unknown',
                       'detail': row.get('detail', 'No stage receipt')}
    checks = qc.get('checks', [])
    skipped = qc.get('skipped_probes', [])
    if not checks or not all(isinstance(x, dict) and type(x.get('pass')) is bool for x in checks):
        rows['qc'] = {'status': 'unknown', 'detail': 'Missing or invalid QC checks'}
    elif any(not x['pass'] for x in checks):
        rows['qc']['status'] = 'failed'
    elif skipped:
        rows['qc'] = {'status': 'skipped', 'detail': 'QC contains unexecuted probes; see raw report'}
    rows['visual_content'] = {'status': 'unknown', 'detail': 'No independent visual-content oracle was run; structural QC is not full visual acceptance'}
    rows['process'] = {'status': 'passed' if exit_code == 0 else 'failed', 'detail': str(exit_code)}
    rows['input_stability'] = {'status': 'passed' if stable else 'failed',
                               'detail': 'Full exported-input hashes checked before/after build'}
    known = all(env.get('tools', {}).get(n, {}).get('status') == 'passed' and
                env['tools'][n].get('stdout', '').strip() for n in ('ffmpeg', 'ffprobe'))
    rows['tool_versions'] = {'status': 'passed' if known else 'unknown',
                             'detail': 'ffmpeg and ffprobe -version evidence'}
    return rows


def inputs_match(root, records):
    try:
        return all(describe(root / r['source_path']) ==
                   {'sha256': r['sha256'], 'size': r['size']} for r in records)
    except (OSError, ValueError):
        return False


def build(timeline, output, *, chrome=None):
    """Copy inputs, render in a fresh private workspace, retain success OR failure."""
    timeline = regular(timeline)
    output = safe_path(output)
    names = collect_inputs(timeline)
    if any(name.split('/')[0] == execution_links.DIRECTORY for name in names):
        raise ValueError('input cannot occupy reserved observation directory')
    sources = {n: regular(timeline.parent / n) for n in names}
    # No overwrite, reuse, or writing under the source project.
    if output == timeline.parent or timeline.parent in output.parents:
        raise ValueError('output must be outside the input project')
    output.mkdir(mode=0o700)  # exclusive; parent must already exist
    (output / 'inputs').mkdir()
    (output / 'artifacts').mkdir()
    records = []
    for name, role in sorted(names.items()):
        src = sources[name]
        before = describe(src)
        target = output / 'inputs' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        if describe(target) != before or describe(src) != before:
            raise ValueError(f'input changed during snapshot: {name}')
        records.append({'path': 'inputs/' + name, 'source_path': name, 'role': role,
                        'identity_level': 'Verified', **before,
                        'runtime_identity_level': 'Fast' if role == 'source' else 'Verified',
                        'runtime_identity': media_stat_digest(src) if role == 'source' else before['sha256']})
    export_id = str(uuid.uuid4())
    env = environment()
    write_json(output / 'environment.json', env)
    policy = {'VTX_NO_NVENC': '1', 'VTX_SEG_NVENC': '0', 'VTX_PIPELINE': '0',
              'VTX_FILTER_THREADS': '1', 'PYTHONPATH': str(REPO)}
    if chrome is not None:
        policy.update(VTX_CHROME=str(regular(chrome)), VTX_GFX='cdp', VTX_VIDEO_SEGMENTS='2')
    with tempfile.TemporaryDirectory(prefix='vtx-acceptance-') as temp:
        project = Path(temp) / 'project'
        shutil.copytree(output / 'inputs', project)
        observation_enabled = execution_links.initialize(project)
        config = Path(temp) / 'empty-config.json'
        write_json(config, {})
        config_digest = file_digest(config)
        policy['VTX_CONFIG'] = str(config)
        prefix = [sys.executable, '-m', 'vtx', '-t', str(project / timeline.name)]
        # Avoid hidden VTX knobs, user plugin configuration, and inherited FFREPORT.
        child_env = safe_environment(temp, policy)
        commands = [prefix + ['all', '--until', 'mux'], prefix + ['qc', '--strict']]
        executions = []
        links = [execution_links.not_attempted(i) for i in range(2)]
        with (output / 'build.log').open('xb') as log:
            for index, command in enumerate(commands):
                request = execution_links.prepare_request(project, export_id, index, enabled=observation_enabled)
                log.write((json.dumps({'argv': command}) + '\n').encode())
                log.flush()
                try:
                    result = subprocess.run(command, cwd=REPO, env=child_env,
                                            stdout=log, stderr=subprocess.STDOUT)
                except OSError as e:
                    log.write(str(e).encode())
                    code = -1
                    launch_status = 'launch_error'
                else:
                    code = result.returncode
                    launch_status = 'returned'
                executions.append({'argv': command, 'exit_code': code})
                links[index] = execution_links.retain_attempt(project, output, request, launch_status, code, enabled=observation_enabled)
                if code != 0:
                    break
        execution_links.seal(output, export_id, links)
        stable = inputs_match(project, records)
        for name in ('final.mp4', 'receipt.json', 'qc_report.json', 'audio_report.json'):
            src = project / 'out' / name
            if src.exists():
                shutil.copyfile(regular(src), output / 'artifacts' / name)
    stable = stable and inputs_match(timeline.parent, records)
    receipt = read_json(output / 'artifacts/receipt.json') if (output / 'artifacts/receipt.json').exists() else {}
    qc = read_json(output / 'artifacts/qc_report.json') if (output / 'artifacts/qc_report.json').exists() else {}
    statuses = gates(receipt, qc, code, stable, env)
    if not (output / 'artifacts/final.mp4').exists():
        statuses['media'] = {'status': 'unknown', 'detail': 'No final media produced'}
    else:
        statuses['media'] = {'status': 'passed', 'detail': 'final.mp4 retained'}
    files = {str(p.relative_to(output)): describe(p) for p in sorted(output.rglob('*')) if p.is_file()}
    # Portable logical commands; raw log retains the actual temporary argv.
    def logical(command):
        return command[:4] + ['$PROJECT/' + timeline.name] + command[5:]
    commands = [logical(command) for command in commands]
    executions = [{**row, 'argv': logical(row['argv'])} for row in executions]
    manifest = {'schema': SCHEMA, 'run_id': export_id,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'timeline': 'inputs/' + timeline.name, 'inputs': records,
        'dependency_closure': {'status': 'unknown', 'self_contained': False,
            'reason': 'Declared receipt dependencies only; HTML imports, system fonts, plugins and external resources are not exhaustively discovered.'},
        'receipt_identity_level': 'Fast', 'receipt_input_fingerprint': receipt.get('input_fingerprint'),
        'build': {'workspace_timeline': timeline.name, 'commands': commands, 'executions': executions, 'environment_overrides': policy,
                  'plugin_config': {'content': {}, 'sha256': config_digest},
                  'environment_policy': ENV_POLICY, 'exit_code': code,
                  'inputs_stable': stable},
        'gates': statuses, 'files': files,
        'accepted': all(x['status'] == 'passed' for x in statuses.values())}
    write_json(output / 'manifest.json', manifest)
    return {'manifest_sha256': file_digest(output / 'manifest.json'), 'run_id': manifest['run_id'],
            'accepted': manifest['accepted'], 'package': str(output)}


def keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise ValueError(f'missing or unknown {label} fields')


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'missing {label}')


def digest_record(value):
    keys(value, 'sha256 size', 'digest')
    if not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256']):
        raise ValueError('invalid SHA-256 digest')
    if type(value['size']) is not int or value['size'] < 0:
        raise ValueError('invalid file size')


def validate_probe(probe, expected_argv, allow_empty=False):
    if not isinstance(probe, dict):
        raise ValueError('invalid probe record')
    if 'error' in probe:
        keys(probe, 'status argv error', 'error probe')
        if probe['status'] != 'unknown':
            raise ValueError('error probe cannot pass')
        nonempty(probe['error'], 'probe error')
    else:
        keys(probe, 'status argv exit_code stdout stderr', 'result probe')
        if type(probe['exit_code']) is not int or not isinstance(probe['stdout'], str) or not isinstance(probe['stderr'], str):
            raise ValueError('invalid probe result types')
        passed = probe['exit_code'] == 0 and (bool(probe['stdout'].strip()) or allow_empty)
        if probe['status'] != ('passed' if passed else 'unknown'):
            raise ValueError('probe status disagrees with result')
    if probe['argv'] != expected_argv:
        raise ValueError('probe command disagrees with named tool')


def validate_metadata(m, env):
    """Versioned fields fail closed; this is consistency, not an attestation."""
    nonempty(m['created_at_utc'], 'creation time')
    offset = datetime.fromisoformat(m['created_at_utc']).utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError('creation time is not UTC')
    if str(uuid.UUID(m['run_id'])) != m['run_id']:
        raise ValueError('noncanonical run UUID')
    if not isinstance(m['files'], dict) or not isinstance(m['gates'], dict):
        raise ValueError('invalid file/gate maps')
    if not isinstance(m['timeline'], str) or m['timeline'] != 'inputs/' + PurePosixPath(m['timeline']).name:
        raise ValueError('invalid logical timeline path')
    receipt_present = 'artifacts/receipt.json' in m['files']
    fingerprint = m['receipt_input_fingerprint']
    if receipt_present:
        if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
            raise ValueError('invalid receipt input fingerprint')
    elif fingerprint is not None:
        raise ValueError('fingerprint without receipt evidence')
    if type(m['accepted']) is not bool or not isinstance(m['inputs'], list):
        raise ValueError('invalid acceptance/input metadata')
    keys(m['dependency_closure'], 'status self_contained reason', 'dependency closure')
    if m['dependency_closure']['status'] != 'unknown' or m['dependency_closure']['self_contained'] is not False:
        raise ValueError('unsupported dependency closure claim')
    nonempty(m['dependency_closure']['reason'], 'closure reason')
    for row in m['inputs']:
        keys(row, 'path source_path role identity_level sha256 size runtime_identity_level runtime_identity', 'input')
        digest_record({k: row[k] for k in ('sha256', 'size')})
    for value in m['files'].values():
        digest_record(value)
    for name, row in m['gates'].items():
        nonempty(name, 'gate name')
        keys(row, 'status detail', 'gate')
        if row['status'] not in STATUSES or not isinstance(row['detail'], str):
            raise ValueError('invalid gate status/detail')
    keys(env, 'python framivra platform git git_status kernel_files tools runtime_backend_truth', 'environment')
    nonempty(env['platform'], 'platform')
    for field in ('python', 'framivra'):
        if not isinstance(env[field], str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+[-+.a-zA-Z0-9]*', env[field]):
            raise ValueError('invalid ' + field + ' version')
    keys(env['tools'], 'ffmpeg ffprobe', 'tool versions')
    source_root = m['build'].get('environment_overrides', {}).get('PYTHONPATH')
    nonempty(source_root, 'source repository path')
    validate_probe(env['git'], ['git', '-C', source_root, 'rev-parse', 'HEAD'])
    validate_probe(env['git_status'], ['git', '-C', source_root, 'status', '--porcelain'], allow_empty=True)
    if env['git']['status'] == 'passed' and not re.fullmatch('[0-9a-f]{40,64}', env['git']['stdout'].strip()):
        raise ValueError('invalid git commit identity')
    for name in ('ffmpeg', 'ffprobe'):
        validate_probe(env['tools'][name], [name, '-version'])
    if not isinstance(env['kernel_files'], dict) or not env['kernel_files']:
        raise ValueError('missing kernel identity')
    for name, value in env['kernel_files'].items():
        relative(name)
        digest_record(value)
    keys(env['runtime_backend_truth'], 'status reason', 'backend truth')
    if env['runtime_backend_truth']['status'] != 'unknown':
        raise ValueError('unsupported runtime backend claim')
    nonempty(env['runtime_backend_truth']['reason'], 'backend reason')
    b = m['build']
    keys(b, 'workspace_timeline commands executions environment_overrides plugin_config environment_policy exit_code inputs_stable', 'build')
    if type(b['exit_code']) is not int or type(b['inputs_stable']) is not bool:
        raise ValueError('invalid build exit/stability')
    relative(b['workspace_timeline'])
    if m['timeline'] != 'inputs/' + b['workspace_timeline']:
        raise ValueError('workspace timeline disagrees with packaged timeline')
    commands = b['commands']
    if not isinstance(commands, list) or len(commands) != 2 or any(
            not isinstance(cmd, list) or not all(isinstance(x, str) and x for x in cmd) for cmd in commands):
        raise ValueError('invalid build commands')
    if len(commands[0]) != 8 or commands[0][1:4] != ['-m', 'vtx', '-t'] or commands[0][5:] != ['all', '--until', 'mux'] or commands[1] != commands[0][:5] + ['qc', '--strict']:
        raise ValueError('unsupported build command sequence')
    if commands[0][4] != '$PROJECT/' + b['workspace_timeline']:
        raise ValueError('execution timeline disagrees with packaged timeline')
    executions = b['executions']
    if not isinstance(executions, list) or not 1 <= len(executions) <= 2:
        raise ValueError('missing or excessive build executions')
    for index, row in enumerate(executions):
        keys(row, 'argv exit_code', 'execution')
        if row['argv'] != commands[index] or type(row['exit_code']) is not int:
            raise ValueError('execution does not match declared command')
        if row['exit_code'] != 0 and index != len(executions) - 1:
            raise ValueError('execution continued after failure')
    if b['exit_code'] != executions[-1]['exit_code'] or (b['exit_code'] == 0 and len(executions) != 2):
        raise ValueError('aggregate exit disagrees with execution evidence')
    overrides = b['environment_overrides']
    base = set('VTX_NO_NVENC VTX_SEG_NVENC VTX_PIPELINE VTX_FILTER_THREADS PYTHONPATH VTX_CONFIG'.split())
    runtime = {'VTX_CHROME', 'VTX_GFX', 'VTX_VIDEO_SEGMENTS'}
    if set(overrides) not in (base, base | runtime):
        raise ValueError('unsupported environment overrides')
    if runtime <= set(overrides):
        nonempty(overrides['VTX_CHROME'], 'chrome executable')
        if overrides['VTX_GFX'] != 'cdp' or overrides['VTX_VIDEO_SEGMENTS'] != '2':
            raise ValueError('unsupported explicit CDP runtime policy')
    for key, value in {'VTX_NO_NVENC': '1', 'VTX_SEG_NVENC': '0', 'VTX_PIPELINE': '0', 'VTX_FILTER_THREADS': '1'}.items():
        if overrides[key] != value:
            raise ValueError('unsupported CPU parameter')
    for key in ('PYTHONPATH', 'VTX_CONFIG'):
        nonempty(overrides[key], key)
    keys(b['plugin_config'], 'content sha256', 'plugin config')
    if b['plugin_config'] != {'content': {}, 'sha256': 'ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356'}:
        raise ValueError('unsupported effective plugin config')
    if b['environment_policy'] != ENV_POLICY:
        raise ValueError('unsupported environment policy')


def verify(package, expected_manifest_sha256, source_root=None):
    """Independent byte verification. Never executes commands found in a package."""
    package = safe_path(package)
    if not re.fullmatch('[0-9a-f]{64}', expected_manifest_sha256):
        raise ValueError('an externally retained SHA-256 manifest digest is required')
    if file_digest(regular(package / 'manifest.json')) != expected_manifest_sha256:
        raise ValueError('manifest digest mismatch')
    m = read_json(package / 'manifest.json')
    fields = {'schema', 'run_id', 'created_at_utc', 'timeline', 'inputs', 'dependency_closure',
              'receipt_identity_level', 'receipt_input_fingerprint', 'build', 'gates', 'files', 'accepted'}
    if not isinstance(m, dict) or set(m) != fields:
        raise ValueError('missing or unknown manifest fields')
    if m.get('schema') != SCHEMA or not isinstance(m.get('run_id'), str):
        raise ValueError('unsupported package schema or missing run_id')
    uuid.UUID(m['run_id'])
    actual = set()
    for p in package.rglob('*'):
        safe_path(p)
        if p.is_file():
            actual.add(p.relative_to(package).as_posix())
        elif not p.is_dir():
            raise ValueError('non-regular package entry')
    if actual != set(m['files']) | {'manifest.json'}:
        raise ValueError('package file inventory mismatch')
    for name, value in m['files'].items():
        if describe(package / relative(name)) != value:
            raise ValueError(f'file digest mismatch: {name}')
    for required in ('environment.json', 'build.log', m['timeline']):
        if required not in m['files']:
            raise ValueError(f'missing required evidence: {required}')
    env = read_json(package / 'environment.json')
    validate_metadata(m, env)
    if m['dependency_closure'].get('self_contained') is not False or m['receipt_identity_level'] != 'Fast':
        raise ValueError('unsupported dependency or runtime identity claim')
    timeline = package / relative(m['timeline'])
    expected_inputs = collect_inputs(timeline)
    seen = {}
    for row in m['inputs']:
        name = relative(row['source_path'])
        if name in seen or row['path'] != 'inputs/' + name or row['role'] != expected_inputs.get(name):
            raise ValueError('input inventory mismatch')
        seen[name] = row
        level = 'Fast' if row['role'] == 'source' else 'Verified'
        if row.get('runtime_identity_level') != level:
            raise ValueError('runtime identity level mismatch')
        token = row.get('runtime_identity')
        if level == 'Fast':
            if not isinstance(token, str) or not re.fullmatch(r'[0-9]+:[0-9]+:[0-9a-f]{16}', token):
                raise ValueError('invalid Fast identity token')
            recorded_size, _, sampled = token.split(':')
            actual_size, _, actual_sampled = media_stat_digest(package / row['path']).split(':')
            if recorded_size != actual_size or sampled != actual_sampled:
                raise ValueError('Fast identity disagrees with packaged source bytes')
        elif token != row['sha256']:
            raise ValueError('runtime content identity mismatch')
        if row['identity_level'] != 'Verified' or m['files'].get(row['path']) != {k: row[k] for k in ('sha256', 'size')}:
            raise ValueError('input identity mismatch')
        if source_root is not None and describe(safe_path(source_root) / name) != {k: row[k] for k in ('sha256', 'size')}:
            raise ValueError(f'live input changed: {name}')
    if set(seen) != set(expected_inputs):
        raise ValueError('input inventory incomplete')
    receipt = read_json(package / 'artifacts/receipt.json') if 'artifacts/receipt.json' in m['files'] else {}
    qc = read_json(package / 'artifacts/qc_report.json') if 'artifacts/qc_report.json' in m['files'] else {}
    if 'artifacts/receipt.json' in m['files'] and (receipt.get('timeline') != m['files'][m['timeline']]['sha256'] or
                    receipt.get('input_fingerprint') != m['receipt_input_fingerprint']):
        raise ValueError('receipt is not bound to the packaged timeline/build')
    if not receipt and m['build']['exit_code'] == 0:
        raise ValueError('successful build lacks receipt')
    env = read_json(package / 'environment.json')
    b = m['build']
    expected_gates = gates(receipt, qc, b['exit_code'], b['inputs_stable'], env)
    expected_gates['media'] = {'status': 'passed' if 'artifacts/final.mp4' in m['files'] else 'unknown',
                             'detail': 'final.mp4 retained' if 'artifacts/final.mp4' in m['files'] else 'No final media produced'}
    if m['gates'] != expected_gates:
        raise ValueError('gate report disagrees with raw evidence')
    accepted = all(x['status'] == 'passed' for x in expected_gates.values())
    if m['accepted'] is not accepted:
        raise ValueError('acceptance claim disagrees with gates')
    return {'integrity_verified': True, 'accepted': accepted, 'run_id': m['run_id'],
            'gates': expected_gates, 'live_inputs_checked': source_root is not None,
            'dependency_closure': m['dependency_closure'],
            'execution_linkage': execution_links.verify_links(package, m, receipt)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='Render a fresh CPU build and seal its evidence (writes a NEW directory)')
    b.add_argument('timeline')
    b.add_argument('output')
    b.add_argument('--chrome', help='explicit CDP executable; CPU rendering and two video segments')
    v = sub.add_parser('verify', help='Read-only verification against an independently retained digest')
    v.add_argument('package')
    v.add_argument('--manifest-sha256', required=True)
    v.add_argument('--source-root')
    args = parser.parse_args(argv)
    try:
        result = build(args.timeline, args.output, chrome=args.chrome) if args.command == 'build' else verify(
            args.package, args.manifest_sha256, args.source_root)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result['accepted'] else 2
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(json.dumps({'error': str(e)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
