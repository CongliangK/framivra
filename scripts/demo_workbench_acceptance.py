#!/usr/bin/env python3
"""Opt-in, real two-pass acceptance of the fixed editable Demo (#419).

Writes a new external project, runs full native renders twice, and keeps evidence.
No tool fallback, recovery, or retry is performed by this runner.
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
import wave

ROOT = Path(__file__).resolve().parents[1]
# Direct script execution needs the checkout on Python's normal import path.
sys.path.insert(0, str(ROOT))
from vtx.api import timeline_transaction, timeline_edit_plan
from scripts.gfx._bench_runtime import verify_media
from scripts.demo import DemoError, check_output_path
from scripts.demo_workbench import add_soundtrack_option, graphics_spec


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def revision(path):
    return 'sha256:' + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exercise_edit(timeline_path):
    """Adopt this exact demo, then plan and commit one semantic edit batch.

    Evidence is saved beside the project; failed/unknown outcomes stop immediately.
    This deliberately does not support arbitrary timelines.
    """
    path = Path(timeline_path)
    evidence = path.parent / 'acceptance' / 'edit'
    evidence.mkdir(parents=True, exist_ok=True)
    doc = json.loads(path.read_text(encoding='utf-8'))
    require(doc.get('project') == {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 900},
            'not the fixed workbench demo')
    cue_track = doc['video_tracks'][2]
    cue = cue_track['clips'][0]
    hit = next(track for track in doc['audio_tracks'] if track['name'] == 'sfx')['events'][1]
    require(cue['dst_in'] == hit['in'] == 462 and cue['dur'] == hit['out'] - hit['in'] == 34,
            'unexpected cue/event placement')
    require(doc['subtitles'][2]['text'] == '把提示和音效放在同一帧。' and '_vtx' not in doc,
            'expected fresh, unmodified demo')
    before_plate = doc['video_tracks'][1]['clips']
    adopt_request = dict(operation='adopt', op_id='demo-acceptance-adopt', base_revision=revision(path))
    dump(evidence / 'adopt-request.json', adopt_request)
    adopted = timeline_transaction(path, adopt_request)
    dump(evidence / 'adopt-result.json', adopted)
    require(adopted.get('ok'), 'adopt failed; inspect receipt (no retry): ' + str(adopted))
    doc = json.loads(path.read_text(encoding='utf-8'))
    cue_track = doc['video_tracks'][2]
    audio_track = next(track for track in doc['audio_tracks'] if track['name'] == 'sfx')
    operations = [
        dict(op='block.move', id=cue_track['clips'][0]['id'], track_id=cue_track['id'], at=396),
        dict(op='block.move', id=audio_track['events'][1]['id'], track_id=audio_track['id'], at=396),
        dict(op='parameters.update', id=doc['subtitles'][2]['id'], path=['text'], value='提示已提前。'),
    ]
    request = dict(operation='edit', op_id='demo-acceptance-edit', base_revision=revision(path), operations=operations)
    dump(evidence / 'edit-request.json', request)
    plan = timeline_edit_plan(path, request)
    dump(evidence / 'plan.json', plan)
    require(plan.get('ok'), 'plan failed; no commit: ' + str(plan))
    committed = timeline_transaction(path, request)
    dump(evidence / 'commit.json', committed)
    require(committed.get('ok'), 'commit failed/unknown; no retry: ' + str(committed))
    after = json.loads(path.read_text(encoding='utf-8'))
    strip_id = lambda clips: [{k: v for k, v in clip.items() if k != 'id'} for clip in clips]
    require(strip_id(after['video_tracks'][1]['clips']) == before_plate, 'scene plate unexpectedly changed')
    after_hit = next(track for track in after['audio_tracks'] if track['name'] == 'sfx')['events'][1]
    require(after['video_tracks'][2]['clips'][0]['dst_in'] == 396 and
            after_hit['in'] == 396 and after_hit['out'] == 430, 'move did not preserve duration')
    return {'adopt': adopted, 'plan': plan, 'commit': committed}


def run(argv, log, env):
    with Path(log).open('xb') as stream:
        result = subprocess.run([str(a) for a in argv], cwd=ROOT, env=env,
                                stdout=stream, stderr=subprocess.STDOUT)
    require(result.returncode == 0, f'command failed ({result.returncode}); see {log}')


def pixels(ffmpeg, final, frames, crop):
    import numpy as np
    x, y, w, h = crop
    select = '+'.join(f'eq(n\\,{f})' for f in frames)
    result = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(final), '-an',
                             '-vf', f'select={select},crop={w}:{h}:{x}:{y}',
                             '-fps_mode', 'passthrough', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(result.returncode == 0, 'pixel decode failed: ' + result.stderr.decode(errors='replace'))
    require(len(result.stdout) == len(frames) * w * h * 3, 'pixel decode count mismatch')
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(len(frames), h, w, 3).astype(float)


def visual_checks(ffmpeg, final, edited):
    import numpy as np
    start = 396 if edited else 462
    frames = [start - 1, start, start + 33, start + 34]
    badge = pixels(ffmpeg, final, frames, (1576, 80, 224, 112))
    # The stable interior at (1588,140) is PAPER, not the dark outside border.
    # A coral disc and blue bars independently distinguish this from the scene plate.
    markers = [(12, 60, [246, 241, 230]), (48, 50, [242, 97, 69]), (158, 50, [60, 94, 218])]
    observed = []
    for image in badge:
        observed.append(all(np.max(np.abs(image[y-2:y+3, x-2:x+3].mean(axis=(0, 1)) - rgb)) < 30
                            for x, y, rgb in markers))
    require(observed == [False, True, True, False], f'visible cue boundaries wrong: {observed}')
    # Native footage has a constant lower region: no scene animation can mimic a subtitle edge.
    lower = pixels(ffmpeg, final, [303, 304, 374, 375], (300, 940, 1320, 100))
    onset, offset = [float(np.mean(np.abs(lower[a] - lower[b]))) for a, b in [(0, 1), (2, 3)]]
    require(onset > 1 and offset > 1, f'native subtitle boundary pixels absent: {onset}, {offset}')
    return {'cue_frames': frames, 'cue_visible': observed, 'native_subtitle_boundary_mean_delta': [onset, offset]}


def read_audio(path):
    import numpy as np
    with wave.open(str(path), 'rb') as wav:
        require((wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) ==
                (48000, 2, 2, 1440000), 'WAV must be exactly 30s, 48kHz stereo s16')
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(float).reshape(-1, 2)
    require(float(np.max(np.abs(samples))) > 0, 'silent WAV')
    return samples


def audio_compare(old_path, new_path):
    import numpy as np
    old, new = read_audio(old_path), read_audio(new_path)
    # Estimate one normalization gain in an unchanged window, away from both hits.
    a, b = old[280*1600:290*1600], new[280*1600:290*1600]
    gain = float(np.sum(a*b) / np.sum(a*a))
    require(0.1 < gain < 10, 'invalid normalization gain')
    old = old * gain
    residual = new - old
    moved = residual[396*1600:430*1600].reshape(-1)
    removed = -residual[462*1600:496*1600].reshape(-1)
    quiet = residual[280*1600:290*1600]
    rms = lambda x: float(np.sqrt(np.mean(x*x)))
    correlation = float(np.dot(moved, removed) / np.sqrt(np.dot(moved, moved)*np.dot(removed, removed)))
    require(rms(moved) > max(2, 5*rms(quiet)) and rms(removed) > max(2, 5*rms(quiet)) and correlation > 0.8,
            'relocated cue not measurably present in both fixed audio windows')
    return {'normalization_gain': gain, 'moved_rms': rms(moved), 'removed_rms': rms(removed),
            'unchanged_rms': rms(quiet), 'relocated_correlation': correlation}


def render_pass(project, name, backend, env, ffmpeg, ffprobe):
    evidence = project / 'acceptance' / name
    evidence.mkdir()
    for index, command in enumerate([['subs', 'check'], ['all'], ['qc', '--strict']]):
        run([sys.executable, '-m', 'vtx', '-t', project / 'timeline.json',
             '--events-jsonl', evidence / f'{index}.events.jsonl', *command], evidence / f'{index}.log', env)
    receipt = json.loads((project / 'out/receipt.json').read_text(encoding='utf-8'))
    dump(evidence / 'receipt.json', receipt)
    gfx = receipt['stages']['gfx']
    require(gfx.get('ok'), 'gfx stage receipt is not successful')
    requested = 'svg-sequence' if backend == 'svg' else backend
    require(gfx.get('gfx_backend', {}).get('requested_backend') == requested, 'unexpected gfx receipt backend')
    text = gfx.get('detail', '')
    if backend == 'svg':
        require('Inkscape static sequence' in text, 'missing Inkscape gfx receipt')
    else:
        log = (evidence / '1.log').read_text(encoding='utf-8')
        require(not any(x in log for x in ('回落 CLI', 'CLI回落', 'cli 6 并发')),
                'actual renderer qualification prohibits CLI fallback')
        if name == 'before':
            label = 'CDP' if backend == 'cdp' else 'CEF Alpha'
            for clip, spec in graphics_spec().items():
                count = spec['frames']
                matches = re.findall(rf'^✓ {clip}\[[^\]]+\]: 渲染 {count} 帧.*{label}.*$', log, re.MULTILINE)
                require(len(matches) == 1, f'actual {backend} completion missing: {clip}')
    if name == 'after':
        before = json.loads((evidence.parent / 'before/receipt.json').read_text(encoding='utf-8'))
        require(before['stages']['gfx']['gfx_backend'] == gfx['gfx_backend'], 'gfx environment changed after edit')
    media = verify_media(project, evidence / 'media', ffmpeg, ffprobe, 600, env)
    dump(evidence / 'media.json', media)
    require(media['status'] == 'passed', 'full PTS/decode verification failed: ' + str(media['error']))
    # Core cache mode normally drops this handoff after mux. Reproduce it via
    # the product and bind its bytes to the mix actually used by this all run.
    mix = project / 'out/audio.wav'
    if not mix.exists():
        run([sys.executable, '-m', 'vtx', '-t', project / 'timeline.json', 'audio'],
            evidence / 'audio-reproduction.log', env)
    require(hashlib.sha256(mix.read_bytes()).hexdigest() == receipt['stages']['audio']['post_loudness']['sha256'],
            'WAV evidence does not match the full-render mix receipt')
    for filename in ['final.mp4', 'audio.wav', 'subs_check.json']:
        shutil.copyfile(project / 'out' / filename, evidence / filename)
    shutil.copyfile(project / 'timeline.json', evidence / 'timeline.json')
    read_audio(evidence / 'audio.wav')
    visible = visual_checks(ffmpeg, evidence / 'final.mp4', name == 'after')
    dump(evidence / 'visible.json', visible)
    return visible


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', required=True, choices=['svg', 'cdp', 'cef'])
    parser.add_argument('--output', required=True, help='new absolute project directory outside the checkout')
    parser.add_argument('--fonts-dir', required=True)
    add_soundtrack_option(parser)
    args = parser.parse_args(argv)
    require(Path(args.output).is_absolute(), '--output must be absolute')
    project = check_output_path(args.output)
    env = os.environ.copy()
    env['VTX_GFX'] = 'cdp' if args.backend == 'svg' else args.backend
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    require(ffmpeg and ffprobe, 'ffmpeg and ffprobe must be on PATH')
    # Keep prepare logs outside its not-yet-created output directory.
    prepare_log = project.parent / (project.name + '.prepare.log')
    require(not prepare_log.exists(), 'prepare evidence already exists')
    run([sys.executable, ROOT / 'scripts/demo_workbench.py', 'prepare', '--format',
         'svg' if args.backend == 'svg' else 'html', '--output', project,
         '--fonts-dir', args.fonts_dir, '--soundtrack', args.soundtrack], prepare_log, env)
    evidence = project / 'acceptance'
    evidence.mkdir()
    shutil.copyfile(prepare_log, evidence / 'prepare.log')
    report = {'backend': args.backend, 'status': 'failed'}
    try:
        report['before'] = render_pass(project, 'before', args.backend, env, ffmpeg, ffprobe)
        exercise_edit(project / 'timeline.json')
        report['after'] = render_pass(project, 'after', args.backend, env, ffmpeg, ffprobe)
        report['audio'] = audio_compare(evidence / 'before/audio.wav', evidence / 'after/audio.wav')
        import numpy as np
        old = pixels(ffmpeg, evidence / 'before/final.mp4', [480], (300, 940, 1320, 100))
        new = pixels(ffmpeg, evidence / 'after/final.mp4', [480], (300, 940, 1320, 100))
        report['edited_subtitle_mean_delta'] = float(np.mean(np.abs(old-new)))
        require(report['edited_subtitle_mean_delta'] > 1, 'same-frame edited subtitle area did not change')
        report['status'] = 'passed'
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        dump(evidence / 'result.json', report)
    print(evidence / 'result.json')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (DemoError, OSError, ValueError, KeyError, ImportError) as error:
        print(f'demo-workbench-acceptance: {error}', file=sys.stderr)
        raise SystemExit(1)
