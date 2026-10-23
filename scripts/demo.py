#!/usr/bin/env python3
"""Prepare and verify the single canonical 30-second Demo; never render or re-freeze.

The original template owns generation. This entry owns trusted source selection,
new-directory boundaries, raw diagnostics and complete input verification. An
explicit canonical audio transfer is not a new profile or a generation result.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / 'templates/demo-timeline-svg'
BASELINE = PACKAGE / 'benchmark/baseline.json'
INPUT_LOCK = PACKAGE / 'benchmark/input-lock.json'
BENCHMARK_ID = 'framivra-promo-timeline-svg-30s'
SOURCE_EXCLUSIONS = ('README.md',)
UNRUN_FIELDS = ('render', 'preview', 'qc', 'cef', 'visual')
AUDIO_SAMPLES = {'assets/audio/bed-premaster.wav': 1440000,
                 'assets/audio/content-cue.wav': 54400}
STAGES = ('copy-source', 'generate-inputs', 'anchor-verify', 'schema', 'validate')


class DemoError(Exception):
    """A diagnosed failure, never an invitation to accept newly generated hashes."""


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DemoError('duplicate JSON key: %r' % key)
        result[key] = value
    return result


def _no_nonfinite(value):
    raise DemoError('non-finite JSON number: ' + value)


def load_json_strict(data):
    try:
        return json.loads(data, object_pairs_hook=_no_duplicate_keys,
                          parse_constant=_no_nonfinite)
    except (ValueError, UnicodeError) as error:
        raise DemoError('invalid JSON: ' + str(error)) from error


def load_json_file_strict(path):
    return load_json_strict(Path(path).read_bytes())


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def valid_hash(value, length=64):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % length, value)


def validate_manifest(data):
    """The repository source selector is trusted; an instance cannot replace it."""
    if not isinstance(data, dict) or data.get('format') != 'framivra-source-baseline/v1' or data.get('baseline_id') != BENCHMARK_ID:
        raise DemoError('unknown source baseline identity')
    source = data.get('source', {})
    if (not isinstance(source, dict) or not valid_hash(source.get('commit'), 40) or not valid_hash(source.get('git_tree'), 40)
            or source.get('path') != 'templates/demo-timeline-svg'):
        raise DemoError('invalid source commit/tree/path')
    workload = data.get('workload', {})
    if not isinstance(workload, dict):
        raise DemoError('invalid source workload')
    for key, expected in {'duration_frames': 900, 'fps': 30, 'width': 1920,
                          'height': 1080, 'gfx_format': 'svg-sequence',
                          'timeline': 'timeline.json'}.items():
        if workload.get(key) != expected:
            raise DemoError('source workload mismatch: ' + key)
    hashes = data.get('runtime_files_sha256')
    if not isinstance(hashes, dict) or len(hashes) != 22:
        raise DemoError('source baseline must pin exactly 22 runtime files')
    for name, digest in hashes.items():
        if (not isinstance(name, str) or not name or '\0' in name
                or name in SOURCE_EXCLUSIONS or name.split('/')[0] == 'benchmark'
                or Path(name).as_posix() != name or not valid_hash(digest)):
            raise DemoError('invalid runtime source identity: ' + str(name))
        safe_relpath(PACKAGE, name)
    return data


def load_baseline():
    return validate_manifest(load_json_file_strict(regular(BASELINE)))


def validate_input_lock(data, baseline):
    fields = {'format', 'baseline_id', 'source_git_tree', 'provenance', 'audio'}
    if (not isinstance(data, dict) or set(data) != fields
            or data['format'] != 'framivra-generated-input-lock/v1'
            or data['baseline_id'] != BENCHMARK_ID
            or data['source_git_tree'] != baseline['source']['git_tree']):
        raise DemoError('invalid canonical input lock identity')
    origin = data['provenance']
    if not isinstance(origin, dict) or set(origin) != {'tools', 'numpy_version'} or not isinstance(origin['numpy_version'], str) or not origin['numpy_version']:
        raise DemoError('missing canonical generation provenance')
    if not isinstance(origin['tools'], dict) or set(origin['tools']) != {'python', 'node', 'ffmpeg', 'ffprobe'}:
        raise DemoError('incomplete canonical tool provenance')
    for name, tool in origin['tools'].items():
        if (not isinstance(tool, dict) or set(tool) != {'version_line', 'binary_sha256'}
                or not isinstance(tool['version_line'], str) or not tool['version_line']
                or not valid_hash(tool['binary_sha256'])):
            raise DemoError('invalid tool provenance: ' + name)
    if not isinstance(data['audio'], dict) or set(data['audio']) != set(AUDIO_SAMPLES):
        raise DemoError('canonical lock must contain exactly the two authored WAVs')
    for name, samples in AUDIO_SAMPLES.items():
        row = data['audio'][name]
        if not isinstance(row, dict) or set(row) != {'sha256', 'size', 'pcm_sha256', 'sample_frames', 'sample_rate', 'channels', 'sample_width'}:
            raise DemoError('invalid audio lock fields: ' + name)
        if not valid_hash(row['sha256']) or not valid_hash(row['pcm_sha256']) or type(row['size']) is not int or row['size'] <= 0:
            raise DemoError('invalid audio digest/size: ' + name)
        for key, value in {'sample_frames': samples, 'sample_rate': 48000,
                           'channels': 2, 'sample_width': 3}.items():
            if type(row[key]) is not int or row[key] != value:
                raise DemoError('invalid canonical audio specification: ' + name)
    return data


def load_input_lock(baseline):
    if not INPUT_LOCK.is_file():
        raise DemoError('BLOCKED: independently reviewed canonical input-lock.json is missing')
    return validate_input_lock(load_json_file_strict(regular(INPUT_LOCK)), baseline)


def symlink_components(path):
    return [p for p in (path, *path.parents) if p.is_symlink()]


def check_output_path(raw):
    """New physical directory, trusted existing parent, outside repo/HOME/cache."""
    if not raw or not str(raw).strip():
        raise DemoError('empty --output')
    candidate = Path(os.path.abspath(os.path.expanduser(str(raw))))
    if candidate.exists() or symlink_components(candidate):
        raise DemoError('output exists or contains a symlink: ' + str(candidate))
    if not candidate.parent.is_dir():
        raise DemoError('output parent directory does not exist')
    home = Path.home().resolve()
    cache = Path(os.environ.get('XDG_CACHE_HOME', home / '.cache')).resolve()
    for forbidden in (ROOT, home, cache):
        if candidate == forbidden or forbidden in candidate.parents:
            raise DemoError('output is inside repo, HOME or cache')
    return candidate


def safe_relpath(base, relpath):
    rel = Path(relpath)
    if (not str(relpath) or rel == Path('.') or rel.is_absolute() or '..' in rel.parts
            or '\\' in str(relpath) or re.match(r'^[A-Za-z]:', str(relpath))):
        raise DemoError('path escape rejected: ' + str(relpath))
    target = base / rel
    if symlink_components(target):
        raise DemoError('symlink rejected: ' + str(target))
    return target


def regular(path):
    if symlink_components(path) or not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise DemoError('expected a physical regular file: ' + str(path))
    return path


def source_files(baseline):
    """Read only the pinned current runtime source; never execute source neighbors."""
    result = {}
    for name, expected in sorted(validate_manifest(baseline)['runtime_files_sha256'].items()):
        data = regular(safe_relpath(PACKAGE, name)).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise DemoError('fixed runtime source bytes differ: ' + name)
        # All 22 original runtime blobs were independently verified as 100644.
        result[name] = (data, 0o644)
    return result


def engine_identity():
    """Optional VCS facts, never a requirement for an unpacked source Demo."""
    unknown = {'commit': None, 'tree': None, 'status': None, 'identity_known': False}
    def query(*args):
        result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, timeout=10,
                                env={**os.environ, 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_OPTIONAL_LOCKS': '0'})
        if result.returncode:
            raise DemoError('Git metadata unavailable')
        return result.stdout.decode('utf-8')
    try:
        if Path(query('rev-parse', '--show-toplevel').strip()).resolve() != ROOT:
            return {**unknown, 'reason': 'Git root is not this source directory'}
        ids = query('rev-parse', 'HEAD', 'HEAD^{tree}').splitlines()
        if len(ids) != 2 or not all(valid_hash(value, 40) for value in ids):
            raise DemoError('invalid Git commit/tree')
        return {'commit': ids[0], 'tree': ids[1], 'status': query('status', '--porcelain'),
                'identity_known': True, 'reason': None}
    except FileNotFoundError:
        return {**unknown, 'reason': 'Git executable unavailable'}
    except (DemoError, OSError, UnicodeError, subprocess.SubprocessError):
        return {**unknown, 'reason': 'Git metadata unavailable'}


def tool_identity(name):
    executable = sys.executable if name == 'python' else shutil.which(name)
    if not executable:
        return None
    flag = '-version' if name in ('ffmpeg', 'ffprobe') else '--version'
    result = subprocess.run([executable, flag], capture_output=True, timeout=30)
    lines = (result.stdout or result.stderr).decode('utf-8', 'replace').splitlines()
    if result.returncode or not lines:
        raise DemoError('tool version probe failed: ' + name)
    return {'version_line': lines[0], 'binary_sha256': sha256_file(Path(executable).resolve())}


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def dependency_identity(transfer):
    tools = {name: tool_identity(name) for name in ('python', 'node', 'ffmpeg', 'ffprobe')}
    numpy = package_version('numpy')
    if any(tool is None for tool in tools.values()) or not package_version('jsonschema') or (not transfer and not numpy):
        raise DemoError('BLOCKED: required existing tools/jsonschema' + ('/NumPy' if not transfer else '') + ' unavailable')
    return {'tools': tools, 'numpy_version': numpy}


def check_fonts(files, fonts_dir):
    fonts = load_json_strict(files['fonts.lock.json'][0])['fonts']
    for font in fonts:
        source = Path(fonts_dir) / Path(font['path']).name if fonts_dir else Path(font['system_path_hint'])
        if not source.is_file() or sha256_file(source) != font['sha256']:
            raise DemoError('BLOCKED: missing pinned font ' + Path(font['path']).name)
    return fonts


def audio_spec(path):
    with wave.open(str(path), 'rb') as wav:
        count = wav.getnframes()
        pcm = wav.readframes(count)
        result = {'sample_frames': count, 'sample_rate': wav.getframerate(),
                  'channels': wav.getnchannels(), 'sample_width': wav.getsampwidth(),
                  'pcm_sha256': hashlib.sha256(pcm).hexdigest()}
        if wav.getcomptype() != 'NONE' or len(pcm) != count * result['channels'] * result['sample_width']:
            raise DemoError('incomplete or compressed canonical PCM: ' + str(path))
    return {'sha256': sha256_file(path), 'size': path.stat().st_size, **result}


def check_audio(base, lock, transfer=False):
    checks = {}
    if transfer:
        if symlink_components(base) or not base.is_dir():
            raise DemoError('canonical audio directory missing or symlinked')
        if {p.name for p in base.iterdir()} != {Path(p).name for p in AUDIO_SAMPLES}:
            raise DemoError('canonical audio directory must contain exactly two WAVs')
    for name, expected in lock['audio'].items():
        path = regular(safe_relpath(base, Path(name).name if transfer else name))
        observed = audio_spec(path)
        diffs = [key for key in expected if observed[key] != expected[key]]
        checks[name] = {'observed': observed, 'mismatched_fields': diffs}
        if diffs:
            raise DemoError('canonical audio mismatch: %s (%s)' % (name, ', '.join(diffs)))
    return checks


def run_logged(argv, logs, name):
    started = time.monotonic()
    result = subprocess.run([str(x) for x in argv], cwd=ROOT, capture_output=True,
                            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    (logs / (name + '.stdout')).write_bytes(result.stdout)
    (logs / (name + '.stderr')).write_bytes(result.stderr)
    return {'argv': [str(x) for x in argv], 'exit_code': result.returncode,
            'seconds': round(time.monotonic() - started, 3),
            'stdout': 'logs/' + name + '.stdout', 'stderr': 'logs/' + name + '.stderr'}


def check_inputs(project, files, lock, logs=None):
    if symlink_components(project) or not project.is_dir():
        raise DemoError('project directory missing or symlinked')
    generated = {f'gfx/frames-svg/f{frame:04d}.svg' for frame in range(900)}
    fonts = load_json_strict(files['fonts.lock.json'][0])['fonts']
    allowed = set(files) | generated | set(AUDIO_SAMPLES) | {f['path'] for f in fonts} | {'gfx/demo.frames.json', 'realization.json'}
    for path in project.rglob('*'):
        rel = path.relative_to(project).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise DemoError('non-regular input in project: ' + rel)
        if path.is_file() and rel not in allowed and rel.split('/')[0] not in ('.generated', 'logs', 'cache', 'out'):
            raise DemoError('unexpected project input: ' + rel)
    for name, (data, _) in files.items():
        if regular(safe_relpath(project, name)).read_bytes() != data:
            raise DemoError('fixed source bytes differ: ' + name)
    actual_frames = {p.relative_to(project).as_posix() for p in (project / 'gfx/frames-svg').rglob('*') if p.is_file()}
    if actual_frames != generated:
        raise DemoError('expected exactly all 900 canonical SVG frame paths')
    # Valid UTF-8 has unique bytes; the original checker's full string comparison
    # is byte-exact only after rejecting malformed/non-canonical encodings here.
    for name in generated:
        regular(safe_relpath(project, name)).read_bytes().decode('utf-8', 'strict')
    for font in fonts:
        if sha256_file(regular(safe_relpath(project, font['path']))) != font['sha256']:
            raise DemoError('pinned font bytes differ: ' + font['path'])
    # Only execute the original checker after every source blob has been verified.
    argv = ['node', project / 'test_demo.js', '--prepared', '--schema', ROOT / 'vtx/timeline.schema.json']
    if logs:
        result = run_logged(argv, logs, 'input-check')
        code = result['exit_code']
    else:
        result = subprocess.run([str(x) for x in argv], cwd=ROOT, capture_output=True)
        code = result.returncode
    if code:
        raise DemoError('canonical 900-frame/manifest/font/audio template check failed')
    return {'status': 'checked', 'media': check_audio(project, lock)}


def cmd_preflight(args):
    checks = {}
    def check(name, operation):
        try:
            value = operation()
            checks[name] = {'ok': True}
            return value
        except (DemoError, OSError, ValueError, wave.Error, subprocess.SubprocessError) as error:
            checks[name] = {'ok': False, 'error': str(error)}
    baseline = check('baseline', load_baseline)
    files = check('source', lambda: source_files(baseline)) if baseline else None
    lock = check('input_lock', lambda: load_input_lock(baseline)) if baseline else None
    check('dependencies', lambda: dependency_identity(bool(args.audio_inputs)))
    if files:
        check('fonts', lambda: check_fonts(files, args.fonts_dir))
    else:
        checks['fonts'] = {'ok': False, 'error': 'fixed source unavailable'}
    if args.audio_inputs:
        check('audio_inputs', lambda: check_audio(Path(os.path.abspath(args.audio_inputs)), lock, True)) if lock else checks.update(audio_inputs={'ok': False, 'error': 'canonical input lock unavailable'})
    print(json.dumps({'schema_version': 1, 'baseline_id': BENCHMARK_ID, 'command': 'preflight',
        'read_only': True, 'checks': checks, 'engine': engine_identity(), 'prepare_supported': True,
        'prepare_ready': all(row['ok'] for row in checks.values()),
        'render': {'ready': False, 'reason': 'Native rendering has not run', 'cef': None,
                   'cef_reason': 'CEF is an independent regression, UNRUN'},
        'regressions': [{'path': 'tests/gfx_bench_suite/suite_manifest.json', 'mode': 'reference-only', 'execution': 'UNRUN'}],
        'unrun': list(UNRUN_FIELDS)}, indent=2))
    return 0


def cmd_prepare(args):
    project = check_output_path(args.output)
    baseline = load_baseline(); lock = load_input_lock(baseline)
    files = source_files(baseline)
    identity = dependency_identity(bool(args.audio_inputs))
    fonts_dir = str(Path(args.fonts_dir).expanduser().resolve()) if args.fonts_dir else None
    check_fonts(files, fonts_dir)
    transfer = Path(os.path.abspath(args.audio_inputs)) if args.audio_inputs else None
    if transfer:
        check_audio(transfer, lock, True)
    logs = project / 'logs'
    record = {'schema_version': 1, 'baseline_id': BENCHMARK_ID,
              'source': baseline['source'], 'baseline_sha256': sha256_file(BASELINE),
              'excluded_paths': list(SOURCE_EXCLUSIONS), 'runtime_source_count': len(files),
              'canonical_input_lock_sha256': sha256_file(INPUT_LOCK),
              'generator': {'sha256': sha256_file(Path(__file__))},
              'audio_mode': 'canonical-transfer' if transfer else 'generated',
              **identity, 'engine': engine_identity(),
              'stages': [], 'results': {name: 'UNRUN' for name in UNRUN_FIELDS}, 'complete': False}
    def save():
        (project / 'realization.json').write_text(json.dumps(record, indent=2) + '\n')
    project.mkdir()
    try:
        logs.mkdir(); save()
        for name, (data, mode) in files.items():
            path = safe_relpath(project, name); path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); path.chmod(mode)
        record['stages'].append({'stage': 'copy-source', 'exit_code': 0}); save()
        argv = ['node', project / 'build_demo.js', '--prepare', '--python', sys.executable]
        if fonts_dir:
            argv += ['--fonts-dir', fonts_dir]
        if transfer:
            argv += ['--graphics-only']
        generated = run_logged(argv, logs, 'generate-inputs')
        record['stages'].append({'stage': 'generate-inputs', **generated}); save()
        if generated['exit_code']:
            raise DemoError('input generation failed; raw logs retained')
        if transfer:
            (project / 'assets/audio').mkdir(parents=True, exist_ok=True)
            for name in AUDIO_SAMPLES:
                shutil.copyfile(regular(safe_relpath(transfer, Path(name).name)), safe_relpath(project, name))
        check_inputs(project, files, lock, logs)
        record['stages'].append({'stage': 'anchor-verify', 'exit_code': 0}); save()
        for name, argv in [('schema', [sys.executable, project / 'test_schema.py', '--schema', ROOT / 'vtx/timeline.schema.json']),
                           ('validate', [sys.executable, '-X', 'utf8', '-m', 'vtx.agent_diagnostics', '-t', project / 'timeline.json', 'validate', '--format', 'agent-json'])]:
            row = run_logged(argv, logs, name)
            record['stages'].append({'stage': name, **row}); save()
            if row['exit_code']:
                raise DemoError(name + ' failed; raw logs retained')
            if name == 'validate' and load_json_file_strict(logs / 'validate.stdout').get('exit_code') != 0:
                raise DemoError('validate returned inconsistent agent-json')
        record['complete'] = True
    except (DemoError, OSError, ValueError, wave.Error, subprocess.SubprocessError) as error:
        record['error'] = str(error)
    save()
    print(json.dumps({'schema_version': 1, 'project': str(project), 'ok': record['complete'],
                      'audio_mode': record['audio_mode'], 'stages': record['stages'], 'unrun': list(UNRUN_FIELDS)}, indent=2))
    return 0 if record['complete'] else 1


def cmd_verify(args):
    project = Path(os.path.abspath(args.project))
    baseline = load_baseline(); lock = load_input_lock(baseline)
    files = source_files(baseline)
    record = load_json_file_strict(regular(safe_relpath(project, 'realization.json')))
    if (not isinstance(record, dict) or not isinstance(record.get('stages'), list)
            or any(not isinstance(row, dict) for row in record['stages'])):
        raise DemoError('invalid preparation record/stage structure')
    if (record.get('schema_version') != 1 or record.get('baseline_id') != BENCHMARK_ID
            or record.get('source') != baseline['source'] or record.get('baseline_sha256') != sha256_file(BASELINE)
            or record.get('excluded_paths') != list(SOURCE_EXCLUSIONS) or record.get('runtime_source_count') != len(files)
            or record.get('canonical_input_lock_sha256') != sha256_file(INPUT_LOCK)
            or record.get('generator') != {'sha256': sha256_file(Path(__file__))}
            or record.get('audio_mode') not in ('generated', 'canonical-transfer')
            or record.get('complete') is not True
            or [row.get('stage') for row in record.get('stages', [])] != list(STAGES)
            or any(row.get('exit_code') != 0 for row in record.get('stages', []))):
        raise DemoError('invalid preparation identity or incomplete stages')
    decoded = check_inputs(project, files, lock)
    print(json.dumps({'schema_version': 1, 'command': 'verify', 'baseline_id': BENCHMARK_ID,
                      'source_checks': 'fixed runtime subset matches', 'decoded_checks': decoded,
                      'engine': record.get('engine'),
                      'failures': [], 'ok': True, 'unrun': list(UNRUN_FIELDS)}, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name, handler in [('preflight', cmd_preflight), ('prepare', cmd_prepare), ('verify', cmd_verify)]:
        node = sub.add_parser(name)
        if name == 'verify':
            node.add_argument('--project', required=True)
        else:
            node.add_argument('--fonts-dir')
            node.add_argument('--audio-inputs', help='explicit directory containing the two canonical WAVs; never automatic fallback')
            if name == 'prepare':
                node.add_argument('--output', required=True)
        node.set_defaults(func=handler)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (DemoError, OSError, ValueError, wave.Error, subprocess.SubprocessError) as error:
        print(json.dumps({'schema_version': 1, 'command': args.command, 'ok': False, 'error': str(error)}), file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
