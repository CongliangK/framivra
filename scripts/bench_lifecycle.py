#!/usr/bin/env python3
"""Opt-in short #63 profile. Creates fresh roots; never deletes project data.

Actual Chrome/FFmpeg execution occurs only through main/run_experiment. Source
unit tests patch producers; they are not hosted acceptance. Trace is unsupported.
"""
import argparse
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import gfx_evidence as evidence, gfx_golden as golden
from vtx import cli, env_manifest, events, fstxn, gfx_provenance, gfx_runtime
from vtx.hygiene import gfx_dir, gfx_key
from vtx.infra import Ctx

PROFILE = 'issue63-short-four-pages-v1'
CONTROLS = {'VTX_GFX': 'cdp', 'VTX_GFX_STATIC': '0', 'VTX_GFX_NAVFREE': '0',
    'VTX_GFX_PAGES': '2', 'VTX_GFX_TABS': '2', 'VTX_GFX_SPLIT_FRAMES': '450',
    'VTX_GFX_SPLIT_MAX': '3', 'VTX_NO_NVENC': '1', 'VTX_SEG_NVENC': '0', 'VTX_PIPELINE': '1'}
COLOR_CONTRACT = {'source': 'explicit_config', 'value': 'srgb', 'measured': False}


def write_json(path, value):
    fstxn.atomic_write(path, (json.dumps(value, sort_keys=True, ensure_ascii=True,
                                       allow_nan=False, indent=2) + '\n').encode())


def new_root(parent):
    parent = Path(parent)
    if not parent.is_absolute() or '..' in parent.parts or parent.resolve(strict=True) != parent:
        raise ValueError('benchmark parent must be an existing canonical absolute directory')
    if not parent.is_dir():
        raise ValueError('benchmark parent is not a directory')
    root = Path(tempfile.mkdtemp(prefix='issue63-', dir=parent))
    write_json(root / 'owner.json', {'profile': PROFILE, 'id': str(uuid.uuid4())})
    return root


@contextlib.contextmanager
def controlled_environment(chrome):
    old = dict(os.environ)
    try:
        # This is process-local experiment configuration, never a host setting.
        for key in list(os.environ):
            if key.startswith('VTX_'):
                del os.environ[key]
        os.environ.update(CONTROLS)
        os.environ['VTX_CHROME'] = str(chrome)
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


def fixture(root, *, create_media=True):
    """120 GFX frames, no text/font glyphs; 2-second CPU media underneath."""
    root.mkdir()
    styles = [
        'background:rgb(20,50,90)',
        'background:linear-gradient(35deg,#f33,#25f);filter:blur(0.4px)',
        'background:transparent',
        'background:transparent;opacity:0.6',
    ]
    for index, style in enumerate(styles):
        html = ('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;width:100%;height:100%;'
                'overflow:hidden;font-size:0}body{' + style + '}#shape{position:absolute;width:600px;height:400px;'
                'top:200px;background:rgba(30,220,80,0.6);border-radius:80px}</style>'
                '<div id="shape"></div><script>const f=Number(new URLSearchParams(location.search).get("f")||0);'
                'document.getElementById("shape").style.left=(100+f*7)+"px";</script>')
        if index == 1:
            svg = '<svg width="1920" height="1080" style="position:absolute">' + ''.join(
                f'<circle cx="{40 + (i % 16) * 120}" cy="{50 + (i // 16) * 150}" r="{15 + i % 30}" fill="rgb({i * 17 % 256},{i * 29 % 256},{i * 41 % 256})"/>'
                for i in range(96)) + '</svg>'
            html = html.replace('<div id="shape">', svg + '<div id="shape">')
        (root / f'page{index}.html').write_text(html)
    if create_media:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=1920x1080:rate=30:duration=2', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=2', '-c:v', 'libx264',
            '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest',
            str(root / 'media.mp4')], check=True, timeout=120)
    timeline = {'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080,
        'duration_frames': 60, 'cache_dependencies': ['media.mp4']},
        'sources': [{'id': 'base', 'path': 'media.mp4'}],
        'video_tracks': [{'name': 'base', 'kind': 'aroll', 'clips': [
            {'src': 'base', 'src_in': 0, 'src_out': 60, 'dst_in': 0, 'dur': 60}]}] + [
            {'name': f'gfx{index}', 'kind': 'gfx', 'clips': [
                {'src': f'page{index}.html', 'dst_in': (index % 2) * 30, 'dur': 30}]}
            for index in range(4)],
        'audio_tracks': [{'name': 'tone', 'events': [{'src': 'base', 'in': 0, 'out': 60, 'volume': 0.5}]}],
        'subtitles': []}
    write_json(root / 'timeline.json', timeline)
    return root / 'timeline.json'


def copy_fixture(template, destination):
    destination.mkdir()  # Existing roots are never reused or cleaned.
    for source in sorted(template.iterdir()):
        if source.is_symlink() or not source.is_file() or source.stat().st_nlink != 1:
            raise ValueError('invalid frozen fixture member')
        shutil.copyfile(source, destination / source.name)
    return destination / 'timeline.json'


def config_inventory(paths):
    result = {}
    for value in paths:
        path = Path(value).resolve(strict=True)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 16 * 1024 * 1024:
                raise ValueError('font configuration file unavailable or oversized')
            data = stream.read(16 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        if (len(data) != before.st_size or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or
                path.stat().st_ino != after.st_ino or path.stat().st_dev != after.st_dev):
            raise ValueError('font configuration changed during observation')
        result[str(path)] = hashlib.sha256(data).hexdigest()
    if not result:
        raise ValueError('explicit font configuration inventory required')
    return result


def host_manifest(runtime_identity, run_id, font_configs):
    if [arg for arg in runtime_identity['argv'] if arg.startswith('--force-color-profile')] != ['--force-color-profile=srgb']:
        raise ValueError('actual browser sRGB configuration flag missing or conflicting')
    truth = {'source': 'cdp', 'effective_backend': runtime_identity['effective_backend'],
        'chromium_version': runtime_identity['chromium_version'], 'viewport': '1920x1080',
        'color_space': 'srgb', 'color_space_contract': COLOR_CONTRACT,
        'evidence': runtime_identity['evidence']}
    manifest = env_manifest.collect_env_manifest('cdp', truth)
    manifest['run_id'] = run_id
    manifest['font_configuration_files'] = config_inventory(font_configs)
    manifest['font_scope'] = 'recorded_files_and_configuration_not_general_fallback_closure'
    manifest['font_environment'] = {key: os.environ.get(key) for key in (
        'FONTCONFIG_FILE', 'FONTCONFIG_PATH', 'LANG', 'LC_ALL', 'LC_CTYPE')}
    if manifest.get('git_clean') is not True or not isinstance(manifest.get('git_sha'), str):
        raise ValueError('benchmark requires a frozen clean source tree')
    if runtime_identity['effective_backend'] in ('software_skia', 'swiftshader'):
        manifest['gpu_driver_and_vram'] = {'status': 'not_applicable', 'reason': 'software_backend'}
    return manifest


def stable_host(manifest):
    return {field: manifest[field] for field in ('os_kernel', 'cpu_model', 'display_server',
        'gpu_driver_and_vram', 'fonts_fingerprint', 'font_configuration_files',
        'font_environment', 'git_sha', 'git_clean')}


def runtime_preflight_status(raw):
    """Fixed-size diagnostic only; never serialize provider values or exceptions."""
    fields = {'browser_version': dict, 'system_info': dict,
              'actual_executable': dict, 'effective_argv': list,
              'process_binding': dict, 'viewport': dict,
              'color_space': str, 'alpha_path': str}
    reasons = ('probe_failed', 'process_identity_unavailable',
               'root_session_unavailable', 'close_failed',
               'page_observation_required', 'not_observed')
    value = raw if type(raw) is dict else {}
    errors = value.get('errors')
    errors = errors if type(errors) is dict else {}
    status = {}
    for field, expected in fields.items():
        item = value.get(field)
        status[field] = ('missing' if item is None else
                         'present' if type(item) is expected else 'invalid_shape')
    # No provider-controlled keys/values, argv, paths, headers or environment.
    classified = {}
    for field in (*fields, 'session_close'):
        if field in errors:
            reason = errors[field]
            classified[field] = reason if type(reason) is str and reason in reasons else 'unrecognized_error'
    return {'schema': 'issue63.runtime_preflight_status/1',
            'diagnostic_only': True, 'raw_shape_valid': type(raw) is dict,
            'schema_valid': type(value.get('schema')) is int and value.get('schema') == 1,
            'source_valid': value.get('source') == 'live-cdp',
            'errors_shape_valid': type(value.get('errors')) is dict,
            'unrecognized_error_fields': any(key not in (*fields, 'session_close') for key in errors),
            'fields': status, 'errors': classified}



def runtime_observation_status(observations):
    """Bounded diagnostic projection of existing ledger data, without raw keys."""
    if type(observations) is not dict or len(observations) > 256:
        return {'schema': 'issue63.runtime_observation_status/1',
                'state': 'invalid_or_overbound', 'observations': []}
    return {'schema': 'issue63.runtime_observation_status/1', 'state': 'observed',
            'observations': [runtime_preflight_status(raw) for raw in observations.values()]}


def validate_runtime_observation_status(value):
    """Accept only the same finite projection, never provider strings or keys."""
    if type(value) is not dict or set(value) != {'schema', 'state', 'observations'}:
        raise ValueError('invalid runtime diagnostic')
    if value['schema'] != 'issue63.runtime_observation_status/1' or value['state'] not in (
            'observed', 'invalid_or_overbound', 'unavailable'):
        raise ValueError('invalid runtime diagnostic schema/state')
    rows = value['observations']
    if type(rows) is not list or len(rows) > 256 or (value['state'] != 'observed' and rows):
        raise ValueError('invalid runtime diagnostic rows')
    template = runtime_preflight_status({})
    reasons = {'probe_failed', 'process_identity_unavailable', 'root_session_unavailable',
               'close_failed', 'page_observation_required', 'not_observed', 'unrecognized_error'}
    for row in rows:
        if type(row) is not dict or set(row) != set(template):
            raise ValueError('invalid runtime diagnostic row')
        for key, default in template.items():
            if type(default) is bool and type(row[key]) is not bool:
                raise ValueError('invalid runtime diagnostic flag')
        if row['schema'] != template['schema'] or row['diagnostic_only'] is not True:
            raise ValueError('invalid runtime diagnostic identity')
        fields, errors = row['fields'], row['errors']
        if (type(fields) is not dict or set(fields) != set(template['fields']) or
                any(type(v) is not str or v not in ('missing','present','invalid_shape') for v in fields.values()) or
                type(errors) is not dict or set(errors) - (set(fields) | {'session_close'}) or
                any(type(v) is not str or v not in reasons for v in errors.values())):
            raise ValueError('invalid runtime diagnostic categories')
    return value

def preflight(chrome, font_configs, *, diagnostic_root=None):
    from vtx.gfx_render import Browser
    browser = Browser(str(chrome), tabs=1, width=1920, height=1080, experiment_color_profile="srgb")
    try:
        browser.start()
        raw = gfx_runtime.collect(browser.port, run_id=str(uuid.uuid4()))
        diagnostic_failed = False
        if diagnostic_root is not None:
            try:
                write_json(diagnostic_root / 'runtime-preflight-status.json', runtime_preflight_status(raw))
            except Exception:
                diagnostic_failed = True
        try:
            identity = gfx_runtime.identity(raw)
        except Exception as failure:
            if diagnostic_failed:
                raise failure from RuntimeError('runtime preflight diagnostic unavailable')
            raise
        if diagnostic_failed:
            raise RuntimeError('runtime preflight diagnostic unavailable') from None
    finally:
        browser.stop()
    manifest = host_manifest(identity, str(uuid.uuid4()), font_configs)
    return identity, manifest, raw


def arm_config(side, scope):
    from vtx.gfx_render import PNG_COMPRESS_LEVEL
    return {'profile': PROFILE, 'scope': scope, **CONTROLS,
        'png_compress_level': PNG_COMPRESS_LEVEL, 'color_space_contract': COLOR_CONTRACT,
        'lifecycle': side == 'candidate', 'process_samples': side == 'candidate', 'trace': False}


def make_plan(c, identity, frozen_host, scope):
    configs = {side: arm_config(side, scope) for side in ('baseline', 'candidate')}
    return {'schema': 'vtx.gfx_comparison_plan/1',
        'required_instances': [{'src': src, 'dur': dur} for src, dur in golden.gfx_clips(c)],
        'input_fingerprint': evidence.input_fingerprint(c, golden.gfx_clips(c)),
        'variable_axes': ['config.lifecycle', 'config.process_samples'],
        'runtime_identity': identity, 'host_identity': frozen_host,
        'arms': {side: {'run_id': str(uuid.uuid4()), 'backend_id': 'cdp',
            'effective_backend': identity['effective_backend'], 'chromium_version': identity['chromium_version'],
            'config': configs[side]} for side in configs}}


@contextlib.contextmanager
def argv(values):
    previous = sys.argv
    sys.argv = ['vtx'] + values
    try:
        yield
    finally:
        sys.argv = previous


def run_arm(timeline, plan, side, font_configs):
    arm = plan['arms'][side]
    event_path = timeline.parent / 'events.jsonl'
    if (side not in ('baseline', 'candidate') or arm['config'].get('scope') not in ('gfx', 'chain') or
            arm['config'] != arm_config(side, arm['config']['scope']) or
            any(os.environ.get(key) != value for key, value in CONTROLS.items()) or
            any(key.startswith('VTX_') and key not in {*CONTROLS, 'VTX_CHROME'} for key in os.environ)):
        raise ValueError('actual experiment configuration mismatch')
    c = Ctx(timeline)
    clips = golden.gfx_clips(c)
    targets = []
    for track in c.tl['video_tracks']:
        if track.get('kind') == 'gfx':
            for clip in track['clips']:
                targets.append({'src': clip['src'], 'dur': clip['dur'],
                    'gfx_key': gfx_key(c, clip), 'frame_dir': str(gfx_dir(c, clip))})
    before = host_manifest(plan['runtime_identity'], arm['run_id'], font_configs)
    if stable_host(before) != plan['host_identity'] or evidence.input_fingerprint(c, clips) != plan['input_fingerprint']:
        raise ValueError('pre-run input/environment drift')
    execution = events.ExecutionContext(events.open_sink(event_path), lifecycle=True,
                                         lifecycle_operations=arm['config']['lifecycle'])
    ledger = gfx_provenance.PublicationLedger(execution, arm['run_id'], targets, require_runtime=True, color_profile="srgb")
    started, load_before, code, failure = time.monotonic(), os.getloadavg(), 0, None
    try:
        with execution.task(kind='task').scope():
            commands = [('gfx', False)] if arm['config']['scope'] == 'gfx' else [('all', False), ('qc', True)]
            for command, strict in commands:
                args = ['-t', str(timeline), command] + (['--strict'] if strict else [])
                if side == 'candidate':
                    args += ['--lifecycle-events', '--lifecycle-process-samples']
                with argv(args):
                    returned = cli._main(execution)
                    if returned not in (None, 0):
                        raise SystemExit(returned)
    except BaseException as error:
        code, failure = events.exit_code(error), error
        raise
    finally:
        execution.finish(code, failure)
        try:
            write_json(timeline.parent / 'command-result.json', {'return_code': code,
                'wall_seconds': time.monotonic() - started, 'load_before': load_before,
                'load_after': os.getloadavg(), 'execution_run_id': execution.run_id})
            write_json(timeline.parent / 'runtime-raw.json', ledger.browser_observations)
        except Exception:
            if failure is None:
                raise
            events.observation_warning('benchmark evidence write failed; original command failure preserved')
    data = event_path.read_text()
    instances = ledger.completion_records(width=c.w, height=c.h, events_data=data)
    bundle = ledger.runtime_bundle(width=c.w, height=c.h, expected=plan['runtime_identity'])
    actual_identity = gfx_runtime.identity(next(iter(bundle['runtime'].values())))
    after = host_manifest(actual_identity, arm['run_id'], font_configs)
    if (stable_host(after) != stable_host(before) or evidence.input_fingerprint(c, clips) != plan['input_fingerprint'] or
            any(os.environ.get(key) != value for key, value in CONTROLS.items())):
        raise ValueError('post-run input/environment drift')
    run = {'schema': 'vtx.gfx_run/1', 'complete': True, 'run_id': arm['run_id'],
        'plan_sha256': evidence.digest(plan), 'input_fingerprint': plan['input_fingerprint'],
        'config': arm['config'], 'env_manifest': after, 'env_sha256': evidence.digest(after),
        'instances': instances, 'producer_binding': bundle, 'timeline_path': str(timeline),
        'events_sha256': hashlib.sha256(data.encode()).hexdigest(), 'events_path': str(event_path),
        'execution_run_id': execution.run_id}
    evidence._manifest(run, arm, f'{c.w}x{c.h}')
    evidence._producer_binding(run, plan, f'{c.w}x{c.h}')
    write_json(timeline.parent / 'gfx-run.json', run)
    write_json(timeline.parent / 'lifecycle-summary.json', events.lifecycle_summary(data))
    return run


def compare_pair(timeline, plan, runs):
    c = Ctx(timeline)
    baseline = runs['baseline']
    snapshot = {'meta': {'fps': c.fps, 'viewport': f'{c.w}x{c.h}',
        'run_evidence': baseline, 'env_manifest': baseline['env_manifest']}, 'pages': {}}
    for instance in baseline['instances']:
        snapshot['pages'].setdefault(instance['src'], {'durs': {}})['durs'][str(instance['dur'])] = {
            'dur': instance['dur'], 'mode': 'full',
            'frames': {key: value['sha256'] for key, value in instance['frames'].items()}}
    report = evidence.verify(c, golden.gfx_clips(c), snapshot, plan, runs['candidate'])
    return snapshot, report


def run_experiment(parent, chrome, font_configs, *, rounds=3, scopes=('gfx', 'chain')):
    if rounds not in (1, 3) or not scopes or set(scopes) - {'gfx', 'chain'} or len(set(scopes)) != len(scopes):
        raise ValueError('unsupported bounded experiment matrix')
    root = new_root(parent)
    with controlled_environment(chrome):
        template = root / 'fixture'
        timeline = fixture(template)
        c = Ctx(timeline)
        identity, manifest, raw = preflight(chrome, font_configs, diagnostic_root=root)
        write_json(root / 'preflight.json', {'runtime': raw, 'environment': manifest})
        plans = {(round_index, scope): make_plan(c, identity, stable_host(manifest), scope)
                 for round_index in range(rounds) for scope in scopes}
        for (round_index, scope), plan in plans.items():
            write_json(root / f'plan-{round_index}-{scope}.json', plan)
        results = []
        for (round_index, scope), plan in plans.items():
            order = ('baseline', 'candidate') if round_index % 2 == 0 else ('candidate', 'baseline')
            runs, paths = {}, {}
            for side in order:
                frozen = root / f'plan-{round_index}-{scope}.json'
                if evidence.digest(json.loads(frozen.read_text())) != evidence.digest(plan):
                    raise ValueError('frozen plan changed before execution')
                paths[side] = copy_fixture(template, root / f'round-{round_index}-{scope}-{side}')
                runs[side] = run_arm(paths[side], plan, side, font_configs)
                if evidence.digest(json.loads(frozen.read_text())) != evidence.digest(plan):
                    raise ValueError('frozen plan changed during execution')
            snapshot, report = compare_pair(paths['candidate'], plan, runs)
            write_json(root / f'golden-{round_index}-{scope}.json', snapshot)
            write_json(root / f'comparison-{round_index}-{scope}.json', report)
            if not report['ok']:
                raise ValueError('strict full-frame comparison failed')
            timings = {side: json.loads((paths[side].parent / 'command-result.json').read_text())['wall_seconds']
                       for side in order}
            observations = {side: json.loads((paths[side].parent / 'lifecycle-summary.json').read_text())
                            for side in order}
            results.append({'round': round_index, 'scope': scope, 'order': order, 'wall_seconds': timings,
                'lifecycle': observations, 'paired_delta_seconds': timings['candidate'] - timings['baseline'],
                'scope_gfx_frames_per_second': {side: report['compared_frames'] / timings[side] for side in order},
                'compared_frames': report['compared_frames'], 'ok': True})
        summary = {'profile': PROFILE, 'paired_runs_complete': True, 'full_issue63_complete': False,
            'scope_note': 'short fixture; trace overhead and final acceptance gates remain separate',
            'rounds': results, 'summary': {scope: {side: {
                'median_seconds': statistics.median([r['wall_seconds'][side] for r in results if r['scope'] == scope]),
                'range_seconds': [min(r['wall_seconds'][side] for r in results if r['scope'] == scope),
                                  max(r['wall_seconds'][side] for r in results if r['scope'] == scope)]}
                for side in ('baseline', 'candidate')} for scope in scopes}}
        write_json(root / 'summary.json', summary)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-parent', required=True, type=Path)
    parser.add_argument('--chrome', required=True, type=Path)
    parser.add_argument('--font-config', action='append', required=True, type=Path)
    parser.add_argument('--rounds', type=int, choices=(1, 3), default=3)
    parser.add_argument('--scope', choices=('gfx', 'chain', 'both'), default='both')
    args = parser.parse_args()
    scopes = ('gfx', 'chain') if args.scope == 'both' else (args.scope,)
    root = run_experiment(args.output_parent, args.chrome, args.font_config, rounds=args.rounds, scopes=scopes)
    print(root)


if __name__ == '__main__':
    main()
