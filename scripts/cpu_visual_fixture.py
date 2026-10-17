#!/usr/bin/env python3
"""Opt-in Issue 91 CPU diagnostic oracle; never a generic visual acceptance gate.

Only a new, exclusively owned directory under a trusted parent may be used.
Requires the existing test dependencies numpy/Pillow and ffmpeg/ffprobe. No
browser, model, GPU, network, downloaded font, or private input is used.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vtx import acceptance as package

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, FPS, COUNT = 640, 360, 30, 180
# Frozen requirement thresholds, expressed on the 0..255 RGB scale.
RGB_MAE_MAX, LOCAL_MAE_MAX, CENTROID_ERROR_MAX = 2.0, 12.0, 1.0
BLOCK = 20
BUDGET = {'source_encodes': 1, 'vtx_builds': 1, 'control_encodes': 7,
          'full_decodes': 9, 'subprocess_timeout_seconds': 120,
          'overall_timeout_seconds': 600, 'owned_media_max_bytes': 1024**3}
CASES = ('static_background_repeat', 'permutation', 'repeat_moving', 'drop',
         'inversion', 'black_insert', 'local_corruption')


def dependencies():
    import numpy as np
    from PIL import Image, ImageDraw, __version__ as pillow_version
    return np, Image, ImageDraw, pillow_version


def scene(n):
    """Generator only. Oracle geometry and index decoding are independent."""
    np, Image, ImageDraw, _ = dependencies()
    image = Image.new('RGB', (WIDTH, HEIGHT), (28, 44, 60))
    draw = ImageDraw.Draw(image)
    # Pillow's built-in font: no machine font or path dependence.
    label = Image.new('RGB', (310, 35), (28, 44, 60))
    text = ImageDraw.Draw(label)
    text.text((0, 0), 'SYNTHETIC CPU DIAGNOSTIC', fill=(235, 240, 245))
    text.text((0, 17), 'FRAME %03d / 179' % n, fill=(235, 240, 245))
    image.paste(label.resize((620, 70), Image.Resampling.NEAREST), (20, 8))
    # Explicit binary frame counter supplements the human-readable counter.
    for bit in range(8):
        color = (235, 240, 245) if (n >> bit) & 1 else (28, 44, 60)
        draw.rectangle((24 + bit * 12, 82, 33 + bit * 12, 91), fill=color)
    draw.line((40, 160, 600, 160), fill=(70, 90, 110), width=2)
    x = 60 + 2 * n
    draw.rectangle((x, 130, x + 50, 180), fill=(255, 150, 60))
    draw.line((40, 275, 600, 275), fill=(70, 90, 110), width=2)
    x = 540 - 2 * n
    draw.ellipse((x - 25, 250, x + 25, 300), fill=(50, 210, 220))
    draw.text((24, 326), '6 seconds | 30 fps | 640 x 360 | 440 Hz tone',
              fill=(190, 205, 220))
    return np.asarray(image)


def metrics_pass(rgb_mae, local_mae, centroid_error):
    return (all(math.isfinite(x) for x in (rgb_mae, local_mae, centroid_error))
            and rgb_mae <= RGB_MAE_MAX and local_mae <= LOCAL_MAE_MAX
            and centroid_error < CENTROID_ERROR_MAX)


def pixel_metrics(reference, actual):
    np, _, _, _ = dependencies()
    difference = np.abs(actual.astype(np.int16) - reference.astype(np.int16))
    # Every sliding 20x20 block, including those straddling an aligned grid.
    integral = np.pad(difference.sum(axis=2, dtype=np.int64), ((1, 0), (1, 0)))
    integral = integral.cumsum(axis=0).cumsum(axis=1)
    sums = (integral[BLOCK:, BLOCK:] - integral[:-BLOCK, BLOCK:]
            - integral[BLOCK:, :-BLOCK] + integral[:-BLOCK, :-BLOCK])
    return float(difference.mean()), float(sums.max() / (BLOCK * BLOCK * 3))


def geometry(frame, n):
    """Infer shape centers/index from pixels, without calling scene()."""
    np, _, _, _ = dependencies()
    rows = []
    for kind, low, high, expected, area in (
            ('orange', 120, 190, (85 + 2 * n, 155), (2400, 2800)),
            ('cyan', 240, 310, (540 - 2 * n, 275), (1850, 2200))):
        roi = frame[low:high]
        r, g, b = (roi[:, :, k] for k in range(3))
        if kind == 'orange':
            mask = (r > 210) & (g > 100) & (g < 190) & (b < 100)
        else:
            mask = (r < 100) & (g > 170) & (b > 180)
        yy, xx = np.where(mask)
        if not len(xx):
            rows.append({'shape': kind, 'passed': False, 'centroid_error_px': None})
            continue
        center = (float(xx.mean()), float(yy.mean() + low))
        error = math.hypot(center[0] - expected[0], center[1] - expected[1])
        bounds = [int(xx.min()), int(yy.min() + low), int(xx.max()), int(yy.max() + low)]
        expected_bounds = [expected[0] - 25, expected[1] - 25,
                           expected[0] + 25, expected[1] + 25]
        shape_ok = (area[0] <= len(xx) <= area[1]
                    and max(abs(a - b) for a, b in zip(bounds, expected_bounds)) <= 2)
        rows.append({'shape': kind, 'centroid': center, 'centroid_error_px': error,
                     'bounds': bounds, 'pixels': len(xx),
                     'passed': bool(shape_ok and error < CENTROID_ERROR_MAX),
                     'median_color_mae_255': float(np.abs(np.median(roi[mask], axis=0) -
                         ((255, 150, 60) if kind == 'orange' else (50, 210, 220))).mean())})
        rows[-1]['passed'] = rows[-1]['passed'] and rows[-1]['median_color_mae_255'] <= RGB_MAE_MAX
    # Read the centers of the eight counter cells, not a source frame label.
    observed_index = sum((int(frame[85:89, 27 + bit * 12:31 + bit * 12].mean()) > 140) << bit
                         for bit in range(8))
    return rows, observed_index


def frame_report(reference, actual, n):
    rgb_mae, local_mae = pixel_metrics(reference, actual)
    shapes, observed_index = geometry(actual, n)
    np, _, _, _ = dependencies()
    background_error = float(np.abs(np.median(actual[100:320, :20], axis=(0, 1))
                                    - (28, 44, 60)).mean())
    errors = [shape['centroid_error_px'] for shape in shapes]
    centroid_error = max(errors) if all(x is not None for x in errors) else None
    passed = (centroid_error is not None and metrics_pass(rgb_mae, local_mae, centroid_error)
              and all(shape['passed'] for shape in shapes) and observed_index == n
              and background_error <= RGB_MAE_MAX)
    return {'frame': n, 'rgb_mae_255': rgb_mae, 'max_sliding_20x20_mae_255': local_mae,
            'centroid_error_px': centroid_error, 'shapes': shapes,
            'observed_frame_counter': observed_index, 'background_color_mae_255': background_error,
            'passed': bool(passed)}


def timing_report(probe, decoded_count):
    """Exact integer PTS * rational time_base, never rounded decimal timestamps."""
    failures, values = [], []
    try:
        streams = probe['streams']
        if len(streams) != 1:
            raise ValueError('expected exactly one selected video stream')
        stream = streams[0]
        if (stream['width'], stream['height']) != (WIDTH, HEIGHT):
            failures.append('dimensions')
        if Fraction(stream['avg_frame_rate']) != FPS:
            failures.append('frame_rate')
        time_base = Fraction(stream['time_base'])
        if time_base <= 0:
            raise ValueError('nonpositive time base')
        frames = probe['frames']
        for n, frame in enumerate(frames):
            pts = frame['pts']
            if type(pts) is not int:
                raise ValueError('missing integer presentation timestamp')
            value = Fraction(pts) * time_base
            values.append(str(value))
            if value != Fraction(n, FPS):
                failures.append('pts:%d' % n)
        if decoded_count != COUNT or len(frames) != COUNT:
            failures.append('frame_count')
        duration = Fraction(int(stream['duration_ts'])) * time_base
        if duration != Fraction(COUNT, FPS):
            failures.append('duration')
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        failures.append('invalid_probe:' + str(error))
    return {'status': 'failed' if failures else 'passed', 'failures': failures,
            'decoded_count': decoded_count, 'pts_seconds_rational': values}


class Runner:
    def __init__(self, root):
        self.root = root
        self.deadline = time.monotonic() + BUDGET['overall_timeout_seconds']
        self.commands = root / 'commands.jsonl'
        self.env = package.safe_environment(root, {'TMPDIR': str(root / 'tmp')})
        (root / 'tmp').mkdir()

    def run(self, argv, label, stdout_path=None, allowed=(0,), timeout=120):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('fixture overall execution budget exhausted')
        stdout_path = stdout_path or self.root / (label + '.stdout')
        stderr_path = self.root / (label + '.stderr')
        record = {'argv': list(map(str, argv)), 'cwd': str(ROOT), 'label': label,
                  'stdout': str(stdout_path.relative_to(self.root)),
                  'stderr': str(stderr_path.relative_to(self.root)), 'exit_code': None}
        start = time.monotonic()
        try:
            with stdout_path.open('xb') as out, stderr_path.open('xb') as err:
                process = subprocess.Popen(argv, cwd=ROOT, env=self.env, stdout=out, stderr=err,
                                           start_new_session=(os.name != 'nt'))
                try:
                    record['exit_code'] = process.wait(timeout=min(timeout, remaining))
                except BaseException:
                    # Kill only the new process group owned by this invocation.
                    if os.name != 'nt':
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    process.wait()
                    raise
            if record['exit_code'] not in allowed:
                raise RuntimeError('%s exited %s; evidence retained' % (label, record['exit_code']))
            if sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file()) > BUDGET['owned_media_max_bytes']:
                raise RuntimeError('owned output byte budget exceeded')
            return stdout_path
        finally:
            record['elapsed_seconds'] = time.monotonic() - start
            with self.commands.open('a', encoding='utf-8') as log:
                log.write(json.dumps(record, sort_keys=True) + '\n')

    def decode(self, path, label):
        np, _, _, _ = dependencies()
        raw = self.root / (label + '.rgb')
        self.run(['ffmpeg', '-v', 'error', '-xerror', '-threads', '1', '-i', str(path),
                  '-map', '0:v:0', '-fps_mode', 'passthrough', '-frames:v', str(COUNT + 1),
                  '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1'],
                 label + '-decode', stdout_path=raw)
        frame_bytes = WIDTH * HEIGHT * 3
        size = raw.stat().st_size
        if size % frame_bytes or not size:
            raise ValueError('invalid decoded RGB byte count: %s' % size)
        frames = np.memmap(raw, dtype=np.uint8, mode='r', shape=(size // frame_bytes, HEIGHT, WIDTH, 3))
        probe_path = self.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_streams', '-show_frames', '-show_entries',
            'stream=width,height,avg_frame_rate,time_base,duration_ts:frame=pts',
            '-of', 'json', str(path)], label + '-probe')
        timing = timing_report(json.loads(probe_path.read_text()), len(frames))
        return frames, raw, timing


def oracle(runner, source, candidate, label):
    frames, raw, timing = runner.decode(candidate, label)
    rows = [frame_report(source[n], frame, n)
            for n, frame in enumerate(frames[:COUNT])]
    report = {'schema': 'framivra.cpu-visual-oracle/1', 'scope': 'diagnostic_fixture_only',
              'status': 'passed' if timing['status'] == 'passed' and len(rows) == COUNT
                        and all(row['passed'] for row in rows) else 'failed',
              'media': package.describe(candidate), 'decoded_rgb': package.describe(raw),
              'timing': timing, 'per_frame': rows}
    package.write_json(runner.root / (label + '-oracle.json'), report)
    return report, frames, raw


def timeline():
    return {'version': 2, 'project': {'fps': FPS, 'width': WIDTH, 'height': HEIGHT,
        'duration_frames': COUNT}, 'sources': [{'id': 'source', 'path': 'source.mp4'}],
        'video_tracks': [{'name': 'diagnostic_scene', 'kind': 'aroll', 'clips': [
            {'src': 'source', 'src_in': 0, 'src_out': COUNT, 'dst_in': 0, 'dur': COUNT}]}],
        'subtitles': [], 'audio_tracks': [{'name': 'synthetic_tone', 'events': [
            {'src': 'source', 'src_in': 0, 'in': 0, 'out': COUNT, 'volume': 1.0}]}]}


def source_identity():
    def git(*args):
        return subprocess.check_output(['git', '-C', str(ROOT), *args],
                                       env=package.safe_environment(), timeout=15).decode().strip()
    names = git('ls-files', '-z').split('\0')
    # Include these explicitly while developing from a dirty worktree too.
    names += ['scripts/cpu_visual_fixture.py', 'tests/test_cpu_visual_fixture.py']
    files = {}
    for name in sorted(set(names) - {''}):
        path = ROOT / name
        if path.is_symlink():
            value = os.readlink(path).encode()
            files[name] = {'kind': 'symlink', 'sha256': hashlib.sha256(value).hexdigest(), 'size': len(value)}
        elif path.is_file():
            files[name] = {'kind': 'file', **package.describe(path)}
        else:
            files[name] = {'kind': 'missing'}
    np, _, _, pillow = dependencies()
    return {'head': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}'),
            'status': git('status', '--porcelain'), 'files': files,
            'numpy': np.__version__, 'Pillow': pillow}


def mutate(frame, n, case, final):
    """Only negative/control input encoding; never produces the positive final."""
    np, _, _, _ = dependencies()
    if case == 'drop' and n == 90:
        return None
    if case == 'permutation' and n in (89, 90):
        return final[179 - n]
    if case == 'repeat_moving' and n == 90:
        return final[89]
    if case == 'inversion' and n < COUNT - 1:
        return 255 - frame
    if case == 'black_insert' and n == 90:
        return np.zeros_like(frame)
    if case == 'local_corruption' and n == 90:
        frame = frame.copy()
        # Straddles an aligned 20px grid. Global MAE alone cannot reject it.
        frame[101:121, 301:321] = 255
    if case == 'static_background_repeat':
        frame = frame.copy()
        frame[100:320, :20] = final[0, 100:320, :20]
    return frame


def run_fixture(output):
    root = package.safe_path(output)
    if root == ROOT or ROOT in root.parents:
        raise ValueError('fixture output must be outside the source checkout')
    root.mkdir(mode=0o700)
    runner = Runner(root)
    report = {'schema': 'framivra.cpu-visual-fixture/1', 'status': 'failed',
              'generic_visual_content': 'unknown', 'scope': 'single_video_single_audio_CPU',
              'thresholds': {'rgb_mae_255_lte': RGB_MAE_MAX,
                  'sliding_20x20_mae_255_lte': LOCAL_MAE_MAX,
                  'centroid_error_px_lt': CENTROID_ERROR_MAX},
              'budget': BUDGET, 'cases': [], 'gates': {name: {'status': 'unknown'} for name in
                  ('source', 'package', 'positive_oracle', 'negative_matrix', 'source_stability')}}
    package.write_json(root / 'plan.json', report)
    try:
        before = source_identity()
        package.write_json(root / 'source-identity.json', before)
        project = root / 'source'
        project.mkdir()
        raw = root / 'generated-source.rgb'
        with raw.open('xb') as stream:
            for n in range(COUNT):
                stream.write(scene(n).tobytes())
        package.write_json(root / 'generated-source.json', package.describe(raw))
        runner.run(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pixel_format', 'rgb24',
            '-video_size', '640x360', '-framerate', '30', '-i', str(raw), '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=6', '-c:v', 'libx264', '-threads', '1',
            '-crf', '0', '-preset', 'medium', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
            '-b:a', '192k', '-t', '6', str(project / 'source.mp4')], 'source-encode')
        package.write_json(project / 'timeline.json', timeline())
        source, source_raw, source_timing = runner.decode(project / 'source.mp4', 'source')
        np, _, _, _ = dependencies()
        generated = np.memmap(raw, dtype=np.uint8, mode='r', shape=(COUNT, HEIGHT, WIDTH, 3))
        source_checks = [frame_report(generated[n], frame, n)
                         for n, frame in enumerate(source[:COUNT])]
        source_ok = (source_timing['status'] == 'passed' and len(source_checks) == COUNT
                     and all(row['passed'] for row in source_checks))
        package.write_json(root / 'source-check.json', {'status': 'passed' if source_ok else 'failed',
            'timing': source_timing, 'per_frame': source_checks,
            'decoded_rgb': package.describe(source_raw)})
        report['gates']['source']['status'] = 'passed' if source_ok else 'failed'
        if not source_ok:
            raise RuntimeError('generated source fails independent geometry/counter/PTS checks')
        report['gates']['source']['status'] = 'passed'
        # Existing #53 producer is the sole positive final path: vtx all --until mux,
        # then qc --strict. Its generic visual_content gate remains unknown.
        report['gates']['package']['status'] = 'failed'
        build_output = runner.run([sys.executable, '-m', 'vtx.acceptance', 'build',
            str(project / 'timeline.json'), str(root / 'package')], 'package-build',
            allowed=(0, 2), timeout=BUDGET['overall_timeout_seconds'])
        anchor = json.loads(build_output.read_text())
        package.write_json(root / 'anchor.json', anchor)
        verification = package.verify(root / 'package', anchor['manifest_sha256'], project)
        package.write_json(root / 'package-verification.json', verification)
        report['package_manifest_sha256'] = anchor['manifest_sha256']
        if (not verification['integrity_verified'] or verification['accepted']
                or verification['gates']['visual_content']['status'] != 'unknown'
                or any(verification['gates'][name]['status'] != 'passed' for name in
                    ('validate', 'prepare', 'video', 'audio', 'mux', 'qc', 'process', 'input_stability'))):
            raise RuntimeError('package/core QC failed or guarantee boundary changed')
        report['gates']['package']['status'] = 'passed'
        final_path = root / 'package/artifacts/final.mp4'
        positive, final, final_raw = oracle(runner, source, final_path, 'positive')
        report['final'] = package.describe(final_path)
        report['gates']['positive_oracle']['status'] = positive['status']
        if positive['status'] != 'passed':
            raise RuntimeError('positive final failed visual oracle; inspect retained per-frame results')
        # Source raw and positive final RGB bytes are retained; control RGB is
        # removed only after its hashes and complete oracle evidence are saved.
        controls = root / 'controls'
        controls.mkdir()
        report['gates']['negative_matrix']['status'] = 'failed'
        for case in CASES:
            mutated = controls / (case + '.rgb')
            with mutated.open('xb') as stream:
                for n, frame in enumerate(final):
                    changed = mutate(frame, n, case, final)
                    if changed is not None:
                        stream.write(changed.tobytes())
            video = controls / (case + '.mp4')
            runner.run(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                '-video_size', '640x360', '-framerate', '30', '-i', str(mutated), '-an',
                '-c:v', 'libx264rgb', '-threads', '1', '-crf', '0', '-preset', 'ultrafast',
                '-pix_fmt', 'rgb24', str(video)], case + '-encode')
            result, decoded, decoded_raw = oracle(runner, source, video, case)
            expected = 'passed' if case == 'static_background_repeat' else 'failed'
            affected = {'static_background_repeat': [], 'permutation': [89, 90],
                        'repeat_moving': [90], 'drop': list(range(90, 179)),
                        'inversion': list(range(179)), 'black_insert': [90],
                        'local_corruption': [90]}[case]
            observed = [entry['frame'] for entry in result['per_frame'] if not entry['passed']]
            raw_identity = package.describe(mutated)
            lossless = raw_identity == result['decoded_rgb']
            timing_expected = 'failed' if case == 'drop' else 'passed'
            row = {'case': case, 'expected': expected, 'observed': result['status'],
                   'passed': (result['status'] == expected and affected == observed and lossless
                              and result['timing']['status'] == timing_expected),
                   'expected_failed_frames': affected, 'observed_failed_frames': observed,
                   'lossless_roundtrip': lossless, 'media': package.describe(video),
                   'mutated_rgb': raw_identity, 'decoded_rgb': result['decoded_rgb']}
            if case == 'static_background_repeat':
                row['unchanged_positive_decode'] = raw_identity == positive['decoded_rgb']
                row['passed'] = row['passed'] and row['unchanged_positive_decode']
            report['cases'].append(row)
            package.write_json(root / (case + '-result.json'), row)
            if case == 'static_background_repeat' and not row['passed']:
                raise RuntimeError('unchanged lossless round-trip control failed; mutation outcomes not counted')
            # Keep failures intact. Unlink only our two exact owned transient files.
            if row['passed']:
                del decoded
                decoded_raw.unlink()
                mutated.unlink()
        matrix_ok = all(row['passed'] for row in report['cases'])
        report['gates']['negative_matrix']['status'] = 'passed' if matrix_ok else 'failed'
        after = source_identity()
        stable = before == after
        package.write_json(root / 'source-identity-after.json', after)
        package.verify(root / 'package', anchor['manifest_sha256'], project)
        report['gates']['source_stability']['status'] = 'passed' if stable else 'failed'
        if time.monotonic() > runner.deadline:
            raise TimeoutError('fixture overall execution budget exhausted')
        if not matrix_ok or not stable:
            raise RuntimeError('corruption matrix or source stability failed')
        report['status'] = 'passed'
    except Exception as error:
        report['error'] = '%s: %s' % (type(error).__name__, error)
    finally:
        report['elapsed_seconds'] = BUDGET['overall_timeout_seconds'] - (runner.deadline - time.monotonic())
        report['files'] = {p.relative_to(root).as_posix(): package.describe(p)
                           for p in sorted(root.rglob('*')) if p.is_file()}
        package.write_json(root / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='NEW directory outside checkout, under a trusted existing parent')
    args = parser.parse_args(argv)
    result = run_fixture(args.output)
    print(json.dumps({key: result[key] for key in ('status', 'gates', 'cases')}, sort_keys=True))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
