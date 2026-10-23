"""Compare the only public Demo and independent capabilities, cold and warm.

python run_e2e.py --repo REPO --output NEW_DIR --ffmpeg FFMPEG --chrome CHROME
The output must be new; the public input verifier must pass its trusted anchor.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import numpy as np

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='backslashreplace')
# AppImage wrappers are not necessarily Python interpreters.
PYTHON = os.environ.get('VTX_TEST_PYTHON') or (sys.executable if not sys.executable.endswith('.AppImage') else shutil.which('python3'))
p = argparse.ArgumentParser()
for name in ('repo', 'output', 'ffmpeg', 'chrome'):
    p.add_argument('--' + name, required=True)
p.add_argument('--scope', choices=('full', 'capability'), default='full')
p.add_argument('--fonts-dir'); p.add_argument('--audio-inputs')
a = p.parse_args()
if a.scope == 'capability' and (a.fonts_dir is not None or a.audio_inputs is not None):
    p.error('--fonts-dir and --audio-inputs require --scope full')
# Child cwd is the repository; keep caller-relative paths and symlink checks intact.
promo_inputs = []
for name in ('fonts-dir', 'audio-inputs'):
    value = getattr(a, name.replace('-', '_'))
    if value is not None:
        promo_inputs.extend(('--' + name, value if os.path.isabs(value) else str(Path.cwd() / value)))
repo = Path(a.repo).resolve(); root = Path(a.output).resolve()
root.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(repo))
from scripts.release.release_github import observed_cdp
from tests.runners._capability_fixture import WORKLOADS
workloads = WORKLOADS[a.scope]
ff = Path(a.ffmpeg).resolve()
env = os.environ.copy()
env.update(PYTHONUTF8='1', VTX_CHROME=a.chrome, VTX_GFX_TABS='2',
           VTX_IO_WORKERS='2', TEMP=str(root), TMP=str(root))
env['PATH'] = str(ff.parent) + os.pathsep + env['PATH']
# Preserve explicit backend requests; they are not backend attestation.
env.setdefault('VTX_GFX', 'cdp')
backend = {'requested_backend': env['VTX_GFX'], 'cdp_observed': {'parallel': 'UNRUN', 'single': 'UNRUN'}}
results = []
cache_checks = {}


def run(cmd, log, extra=None):
    child = dict(env); child.update(extra or {})
    started = time.monotonic()
    result = subprocess.run([str(x) for x in cmd], cwd=repo, capture_output=True, env=child, timeout=900)
    Path(log).write_bytes(result.stdout + result.stderr)
    results.append({'step': str(Path(log).relative_to(root)), 'exit': result.returncode,
                    'seconds': round(time.monotonic() - started, 3)})
    print(results[-1], flush=True)
    if result.returncode:
        print('\n'.join((result.stdout + result.stderr).decode('utf-8', errors='replace').splitlines()[-60:]), flush=True)
        (root / 'steps.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        (root / 'results.json').write_text(json.dumps({'pass': False, 'full_pass': False, 'release_pass': False,
            'scope': a.scope, 'workloads': [name for name, _ in workloads], 'steps': results,
            'failed_step': str(Path(log).relative_to(root))}, indent=2), encoding='utf-8')
        sys.exit(1)
    return result


# Explicit capability scope is a regression probe, never a public-film fallback.
if a.scope == 'full':
    run([PYTHON, repo / 'scripts/demo.py', 'prepare', '--output', root / 'promo-input'] + promo_inputs, root / 'promo-prepare.txt')
    run([PYTHON, repo / 'scripts/demo.py', 'verify', '--project', root / 'promo-input'], root / 'promo-verify.txt')
run([PYTHON, repo / 'tests/runners/_capability_fixture.py', '--output', root / 'capability-input', '--ffmpeg', ff], root / 'capability-prepare.txt')
checks = {}
for workload, expected in workloads:
    frames = []
    for case, segments in (('parallel', 2), ('single', 1)):
        key = workload + '-' + case
        project = root / (key + ' 中文 # project')
        shutil.copytree(root / (workload + '-input'), project)
        cmd = [PYTHON, '-X', 'utf8', repo / 'vtx/vtx.py', '-t', project / 'timeline.json']
        run(cmd + ['all'], root / (key + '-all.txt'), {'VTX_VIDEO_SEGMENTS': str(segments)})
        if workload == 'capability' and env['VTX_GFX'].strip().lower() == 'cdp':
            backend['cdp_observed'][case] = observed_cdp(root / (key + '-all.txt'))
        cached = {str(path.relative_to(project)): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                  for path in (project / 'cache').rglob('*') if path.is_file()
                  and path.suffix in ('.png', '.wav', '.mp4') and 'tmp' not in path.relative_to(project / 'cache').parts}
        run(cmd + ['all'], root / (key + '-warm.txt'), {'VTX_VIDEO_SEGMENTS': str(segments)})
        cache_checks[key] = {'files': len(cached), 'unchanged': bool(cached) and all(
            path.is_file() and (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) == before
            for name, before in cached.items() for path in [project / name])}
        run(cmd + ['qc', '--strict'], root / (key + '-strict.txt'))
        video = project / 'out/final.mp4'
        run([ff, '-v', 'error', '-i', video, '-f', 'null', '-'], root / (key + '-decode.txt'))
        run([ff, '-hide_banner', '-nostats', '-i', video, '-af', 'ebur128=peak=true:framelog=quiet',
             '-vf', 'blackdetect=d=0.01:pix_th=0.1', '-f', 'null', '-'], root / (key + '-signal.txt'))
        raw = run([ff, '-v', 'error', '-i', video, '-vf', 'scale=160:90', '-pix_fmt', 'rgb24',
                   '-f', 'rawvideo', '-'], root / (key + '-frames.rgb'))
        # Keep raw stdout separately: stderr diagnostics are not RGB pixels.
        (root / (key + '-frames.rgb')).write_bytes(raw.stdout)
        frames.append(np.frombuffer(raw.stdout, dtype=np.uint8).reshape(-1, 90, 160, 3))
    same_count = all(len(value) == expected for value in frames)
    difference = np.abs(frames[0].astype(float) - frames[1].astype(float)).mean(axis=(1, 2, 3)) if same_count else None
    checks[workload] = {'same_frame_count': same_count, 'mean_rgb_difference': float(difference.mean()) if same_count else None,
        'max_frame_mean_difference': float(difference.max()) if same_count else None,
        'largest_difference_frame': int(difference.argmax()) if same_count else None,
        'comparison_pass': same_count and bool(difference.max() < 2)}
    for case in ('parallel', 'single'):
        text = (root / (workload + '-' + case + '-signal.txt')).read_text(errors='replace')
        peaks = re.findall(r'Peak:\s*(-?[\d.]+) dBFS', text)
        checks[workload][case] = {'no_black_intervals': 'black_start:' not in text,
                                'true_peak_dbfs': float(peaks[-1]) if peaks else None}
checks['cache'] = cache_checks
summary = {'scope': a.scope, 'workloads': [name for name, _ in workloads], 'steps': results, 'checks': checks, 'backend': backend, 'source_sha256': {
    path.relative_to(repo / 'vtx').as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted((repo / 'vtx').rglob('*.py'))}}
summary['pass'] = (len(cache_checks) == 2 * len(workloads) and all(value['unchanged'] for value in cache_checks.values())
    and all(row['exit'] == 0 for row in results)
    and all(checks[workload]['same_frame_count'] and checks[workload]['comparison_pass']
            and all(checks[workload][case]['no_black_intervals']
                    and checks[workload][case]['true_peak_dbfs'] is not None
                    and checks[workload][case]['true_peak_dbfs'] <= -1 for case in ('parallel', 'single'))
            for workload, _ in workloads))
summary.update(full_pass=summary['pass'] and a.scope == 'full', release_pass=False)
(root / 'steps.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
(root / 'results.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(checks, indent=2))
if not summary['pass']:
    sys.exit(1)
