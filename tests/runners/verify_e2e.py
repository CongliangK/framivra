"""Full-resolution event/actual-segment boundaries and full PCM comparisons."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.runners._capability_fixture import BOUNDARIES, WORKLOADS
p = argparse.ArgumentParser()
p.add_argument('root', type=Path); p.add_argument('--ffmpeg', default='ffmpeg')
p.add_argument('--scope', choices=('full', 'capability'), default='full')
a = p.parse_args(); root = a.root.resolve()
workloads = WORKLOADS[a.scope]
prior = json.loads((root / 'results.json').read_text(encoding='utf-8'))
if (prior.get('scope') != a.scope or prior.get('workloads') != [name for name, _ in workloads]
    or prior.get('pass') is not True or prior.get('full_pass') is not (a.scope == 'full')
    or prior.get('release_pass') is not False):
    p.error('successful runner scope/workloads must exactly match the requested verification')
checks = {}


def decode(path, args):
    return subprocess.check_output([a.ffmpeg, '-v', 'error', '-i', str(path), *args])


for workload, count in workloads:
    videos = [root / (workload + '-' + case + ' 中文 # project') / 'out/final.mp4' for case in ('parallel', 'single')]
    audio = [decode(video, ['-vn', '-ar', '48000', '-ac', '2', '-f', 's16le', '-']) for video in videos]
    row = {'audio_pcm_identical': audio[0] == audio[1],
           'audio_samples_per_channel': len(audio[0]) // 4,
           'audio_duration_within_aac_padding': abs(len(audio[0]) // 4 - count * 1600) <= 1024}
    # Public-film edits/cues and beginning/end; the capability fixture separately
    # exercises ordinary-media/HTML/subtitle/loop and observed segmented encoding.
    probes = set(BOUNDARIES if workload == 'capability' else (
        0, 29, 30, 89, 90, 149, 150, 209, 210, 227, 228, 261, 262,
        299, 300, 449, 450, 461, 462, 495, 496, 599, 600, 749, 750, 898, 899))
    text = (root / (workload + '-parallel-all.txt')).read_text(encoding='utf-8')
    cuts = sorted({cut for group in re.findall(r'分段渲染：\d+ 段，切点 (\[[0-9, ]*\])', text)
                   for cut in json.loads(group)})
    for cut in cuts:
        probes.update((cut - 1, cut, cut + 1))
    if workload == 'capability':
        row['actual_segment_cuts'] = cuts
        row['subtitle_crosses_actual_segment'] = bool(cuts) and any(40 < cut < 115 for cut in cuts)
    boundary = {}
    for frame in sorted(probes):
        data = [np.frombuffer(decode(video, ['-vf', f'select=eq(n\\,{frame})', '-frames:v', '1',
                 '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-']), dtype=np.uint8).astype(float) for video in videos]
        if any(len(value) != 1920 * 1080 * 3 for value in data):
            raise RuntimeError(f'missing full-resolution boundary frame: {workload}/{frame}')
        boundary[frame] = float(np.abs(data[0] - data[1]).mean())
    row['full_resolution_probes'] = boundary
    row['full_resolution_comparison_pass'] = max(boundary.values()) < 2
    for frame in ((44, 90, 134) if workload == 'capability' else (228, 450, 750)):
        subprocess.run([a.ffmpeg, '-v', 'error', '-y', '-i', str(videos[0]), '-vf',
            f'select=eq(n\\,{frame}),scale=960:540', '-frames:v', '1', str(root / f'{workload}-frame-{frame}.png')], check=True)
    row['sha256'] = {str(video.relative_to(root)): hashlib.sha256(video.read_bytes()).hexdigest() for video in videos}
    row['pass'] = (row['audio_pcm_identical'] and row['audio_duration_within_aac_padding']
                   and row['full_resolution_comparison_pass']
                   and row.get('subtitle_crosses_actual_segment', True))
    checks[workload] = row
checks['pass'] = all(row['pass'] for row in checks.values())
checks.update(scope=a.scope, workloads=[name for name, _ in workloads],
              full_pass=checks['pass'] and a.scope == 'full', release_pass=False)
(root / 'additional-checks.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
print(json.dumps(checks, indent=2))
if not checks['pass']:
    sys.exit(1)
