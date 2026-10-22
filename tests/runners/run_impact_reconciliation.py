"""Real CPU warm-cache reconciliation; HTML acceptance is a separate route.

Explicit --output must be a new directory. This runner records actual product
paths and mtimes across independent CLI processes, not mocked key functions.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import math
import struct
import wave


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', required=True, type=Path)
    args = ap.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    from vtx.inspection.impact import capture, compare
    from tests.runners.impact_inventory import POLICY, observe, delta, validate_sidecars
    env = dict(os.environ, VTX_SEG_NVENC='0', VTX_VIDEO_SEGMENTS='8')
    (root / 'inventory-policy.json').write_text(json.dumps(POLICY, indent=2))
    def run(argv, label):
        result = subprocess.run([str(a) for a in argv], capture_output=True, env=env)
        (root / (label + '.log')).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f'{label}: exit {result.returncode}')
        return result.stdout.decode()
    run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
         'testsrc2=size=64x64:rate=30:duration=4', '-f', 'lavfi', '-i',
         'sine=frequency=440:duration=4', '-c:v', 'libx264', '-threads', '1',
         '-c:a', 'aac', root / 'source.mp4'], 'generate')
    def tone(hz):
        with wave.open(str(root / 'tone.wav'), 'wb') as stream:
            stream.setparams((2, 2, 48000, 48000, 'NONE', 'not compressed'))
            stream.writeframes(b''.join(struct.pack('<hh', *(int(8000 * math.sin(2 * math.pi * hz * i / 48000)),) * 2)
                                        for i in range(48000)))
    tone(440)
    tl = {'version': 2, 'project': {'fps': 30, 'width': 64, 'height': 64,
                                   'duration_frames': 2700},
          'sources': [{'id': 's', 'path': 'source.mp4'}, {'id': 'tone', 'path': 'tone.wav'}],
          'video_tracks': [{'name': 'scenes', 'kind': 'aroll', 'clips': [
              {'src': 's', 'src_in': i, 'src_out': i + 90, 'dst_in': i * 90, 'dur': 90}
              for i in range(30)]}],
          'subtitles': [], 'audio_tracks': [{'name': 'tone', 'events': [
              {'src': 'tone', 'src_in': 0, 'in': 0, 'out': 30, 'volume': 1}]}]}
    def save():
        (root / 'timeline.json').write_text(json.dumps(tl))
    def render(label):
        save()
        for stage in ('prepare', 'video'):
            run([sys.executable, repo / 'vtx/vtx.py', '-t', root / 'timeline.json', stage],
                label + '-' + stage)
    verified_frame_counts = {}
    def products(label):
        observation = observe(root)
        (root / f'{label}-inventory.json').write_text(json.dumps(observation, indent=2))
        def probe(path):
            raw = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames',
                       '-show_entries', 'stream=nb_read_frames', '-of', 'json', path],
                      f'{label}-frames-{path.stem}')
            return json.loads(raw)['streams'][0]['nb_read_frames']
        observation['sidecar_checks'] = validate_sidecars(root, observation['products'], probe, verified_frame_counts)
        (root / f'{label}-inventory.json').write_text(json.dumps(observation, indent=2))
        return observation['products']
    render('cold')
    before = capture(tl, root)
    initial = products('cold')
    render('noop')
    if products('warm') != initial:
        raise AssertionError('no-op warm build rewrote cached products')
    results = []
    def check(label):
        nonlocal before, initial
        after = capture(tl, root)
        prediction = compare(before, after)
        render(label)
        current = products(label)
        result = dict(case=label, predicted=prediction,
                      **delta(initial, current, prediction['new_artifacts']))
        # Keep the historical pass field for consumers; all evidence is retained.
        result['pass'] = result['passed']
        results.append(result)
        (root / 'reconciliation.json').write_text(json.dumps(results, indent=2))
        if not result['passed']:
            raise AssertionError(f'{label}: prediction differs from actual cache writes')
        before, initial = after, current
    tl['video_tracks'][0]['clips'][7]['src_in'] = 30
    tl['video_tracks'][0]['clips'][7]['src_out'] = 120
    check('scene-eight-trim')
    tl['subtitles'] = [{'in': 640, 'out': 670, 'text': 'changed'}]
    check('scene-eight-subtitle')
    tl['project']['duration_frames'] += 30
    check('duration')
    st = (root / 'tone.wav').stat()
    tone(880)
    os.utime(root / 'tone.wav', ns=(st.st_atime_ns, st.st_mtime_ns))
    check('audio-restored-mtime')
    print(json.dumps({'pass': True, 'scenes': 30, 'cases': len(results),
                      'html_render_acceptance': 'NOT RUN'}, indent=2))


if __name__ == '__main__':
    main()
