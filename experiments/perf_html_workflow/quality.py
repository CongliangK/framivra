#!/usr/bin/env python3
"""Independent decode evidence for the fixed 600-frame HTML study (writes evidence)."""
import argparse
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import time


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def png_inventory(project, expected_frames=600, expected_size=(1920, 1080)):
    """Decode original bytes read-only; pack validity is storage integrity ONLY."""
    from PIL import Image, __version__ as pillow_version
    from vtx.render.gfx_pack import PagePack, inspect_pack
    paths = sorted((project / 'cache' / 'gfx').rglob('f*.png'))
    packs = sorted((project / 'cache' / 'gfx_packs').rglob('*.vtxp'))
    frames, sources = [], []

    def decode(name, location, data):
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'PNG':
                raise ValueError('not PNG: ' + location)
            image.load()
            rgba = image.convert('RGBA')
            if rgba.size != expected_size:
                raise ValueError('wrong PNG size: ' + location)
            frames.append({'name': name, 'path': location, 'bytes': len(data),
                           'file_sha256': hashlib.sha256(data).hexdigest(),
                           'rgba_sha256': hashlib.sha256(rgba.tobytes()).hexdigest(),
                           'size': list(rgba.size)})

    if packs:
        if len(packs) != 1 or paths:
            raise ValueError('expected one packed page without ambiguous loose frames')
        pack = packs[0]
        before = sha(pack)
        header = inspect_pack(pack, root=project)
        record = header['record']
        if (record.get('frames'), record.get('width'), record.get('height')) != (expected_frames, *expected_size):
            raise ValueError('packed page workload dimensions/frame count mismatch')
        metadata = {}
        with PagePack(pack, expected_record=record, root=project,
                      max_frame_bytes=expected_size[0] * expected_size[1] * 8 + 65536) as page:
            for index in range(expected_frames):
                name = 'f%04d.png' % index
                decode(name, str(pack.relative_to(project)) + '::' + name, page.read_frame(index))
            for name in page.metadata_names:
                raw = page.read_metadata(name)
                metadata[name] = {'sha256': hashlib.sha256(raw).hexdigest(),
                                  'bytes': len(raw), 'utf8': raw.decode('utf-8')}
        if sha(pack) != before:
            raise ValueError('packed source changed during verification')
        sources.append({'path': str(pack.relative_to(project)), 'sha256': before,
                        'bytes': pack.stat().st_size, 'header': header, 'metadata': metadata})
        storage = 'page_pack'
    else:
        if (len(paths) != expected_frames or len({p.parent for p in paths}) != 1 or
                [p.name for p in paths] != ['f%04d.png' % i for i in range(expected_frames)]):
            raise ValueError('expected one complete sequential loose PNG page')
        for path in paths:
            decode(path.name, str(path.relative_to(project)), path.read_bytes())
        storage = 'loose_png'
    return {'frames': frames, 'storage': storage, 'packed_sources': sources,
            'pillow_version': pillow_version,
            'storage_integrity': 'passed', 'cache_eligibility': 'not independently assessed; actual warm all checks current eligibility',
            'file_aggregate_sha256': digest([[f['name'], f['file_sha256']] for f in frames]),
            'rgba_aggregate_sha256': digest([[f['name'], f['rgba_sha256']] for f in frames])}


def audio_timeline(text, stream):
    """Validate original decoded audio PTS, without resampling or shifting timestamps."""
    if int(stream['sample_rate']) != 48000 or int(stream['channels']) != 2:
        raise ValueError('original audio must be stereo 48000Hz')
    source_base = Fraction(stream['time_base'])
    source_duration = int(stream['duration_ts']) * source_base
    if source_base <= 0 or Fraction(stream['start_time']) != 0:
        raise ValueError('original audio time base/start invalid')
    if abs(source_duration - 20) > Fraction(1024, 48000):
        raise ValueError('original audio duration outside AAC padding allowance')
    tb, samples, frames = None, 0, 0
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('#tb 0:'):
            if tb is not None:
                raise ValueError('duplicate audio time base')
            tb = Fraction(line.split(':', 1)[1].strip())
            if tb <= 0:
                raise ValueError('invalid audio time base')
        elif line and not line.startswith('#'):
            fields = [field.strip() for field in line.split(',')]
            if tb is None or len(fields) != 6 or fields[0] != '0' or not re.fullmatch('[0-9a-f]{32}', fields[5]):
                raise ValueError('invalid decoded audio record')
            dts, pts, duration, size = map(int, fields[1:5])
            count = size // 4
            if (size <= 0 or size % 4 or duration <= 0 or dts != pts or
                    pts * tb != Fraction(samples, 48000) or duration * tb != Fraction(count, 48000)):
                raise ValueError('decoded audio offset, gap, overlap or duration mismatch')
            samples += count
            frames += 1
            if samples > 961024:
                raise ValueError('decoded audio exceeds AAC padding allowance')
    if tb is None or not frames or abs(samples - 960000) > 1024:
        raise ValueError('decoded audio incomplete or duration outside allowance')
    return {'start': '0', 'sample_frames': samples, 'sample_rate': 48000,
            'channels': 2, 'format': 's16le', 'duration': str(Fraction(samples, 48000)),
            'source_duration': str(source_duration), 'continuity_valid': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--video', type=Path, help='retained cold/warm movie; PNGs still read from project')
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--ffprobe', required=True)
    args = parser.parse_args()
    project = args.project.resolve(strict=True)
    output = args.output.absolute()
    evidence = output.with_name(output.stem + '.evidence')
    if output.exists() or output.is_symlink():
        parser.error('output must be new')
    evidence.mkdir(exist_ok=False)
    report = {'schema': 'framivra.html-study-quality/1', 'project': str(project),
              'status': 'failed', 'errors': [], 'commands': [], 'png': {},
              'probe': None, 'decoded_video': None, 'decoded_audio': None, 'no_black': None,
              'limitations': ['Not strict QC, human visual acceptance, or Verified Exact.',
                              'Audio hash is normalized stereo 48kHz s16le PCM.']}

    def check(ok, message):
        if not ok:
            report['errors'].append(message)

    def command(name, argv):
        stdout, stderr = evidence / (name + '.stdout'), evidence / (name + '.stderr')
        row = {'argv': argv, 'stdout': str(stdout), 'stderr': str(stderr),
               'exit_code': None, 'duration_seconds': None}
        report['commands'].append(row)
        start = time.monotonic()
        try:
            with stdout.open('xb') as out, stderr.open('xb') as err:
                row['exit_code'] = subprocess.run(argv, stdout=out, stderr=err).returncode
        except Exception as exc:
            row['error'] = str(exc)
        finally:
            row['duration_seconds'] = time.monotonic() - start
        check(row['exit_code'] == 0, name + ' command failed')
        row['stdout_sha256'] = sha(stdout) if stdout.exists() else None
        row['stderr_sha256'] = sha(stderr) if stderr.exists() else None
        return row, stdout, stderr

    try:
        report['png'] = png_inventory(project)
        report['pillow_version'] = report['png']['pillow_version']
    except Exception as exc:
        check(False, 'PNG inventory failed: ' + str(exc))
    final = args.video.resolve() if args.video is not None else project / 'out' / 'final.mp4'
    report['video_path'] = str(final)
    original_hash = sha(final) if final.is_file() else None
    audio = []
    row, out, _ = command('ffprobe', [args.ffprobe, '-v', 'error', '-count_frames',
                          '-show_streams', '-show_format', '-of', 'json', str(final)])
    try:
        probe = json.loads(out.read_text())
        report['probe'] = probe
        video = [s for s in probe['streams'] if s['codec_type'] == 'video']
        audio = [s for s in probe['streams'] if s['codec_type'] == 'audio']
        check(len(video) == 1 and len(audio) == 1, 'expected one video and one audio stream')
        v = video[0]
        check((v['width'], v['height'], int(v['nb_read_frames'])) == (1920, 1080, 600),
              'ffprobe geometry or decoded frame count mismatch')
        check(Fraction(v['avg_frame_rate']) == 30 and Fraction(v['r_frame_rate']) == 30,
              'ffprobe frame rate mismatch')
        check(Fraction(v['time_base']) > 0 and Fraction(v.get('start_time', '0')) == 0,
              'invalid video time base/start')
    except Exception as exc:
        check(False, 'ffprobe validation failed: ' + str(exc))
    common = [args.ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(final)]
    row, out, _ = command('video-framemd5', common + ['-map', '0:v:0', '-an', '-vsync', '0', '-f', 'framemd5', '-'])
    try:
        text = out.read_text()
        tb = Fraction(re.search(r'^#tb 0:\s*(\S+)', text, re.M).group(1))
        frames = [[x.strip() for x in line.split(',')] for line in text.splitlines()
                  if line.strip() and not line.startswith('#')]
        valid = len(frames) == 600 and all(len(f) == 6 and int(f[0]) == 0 and
                    int(f[1]) == int(f[2]) and int(f[2]) * tb == Fraction(i, 30) and
                    int(f[3]) * tb == Fraction(1, 30) and int(f[4]) > 0 and
                    re.fullmatch('[0-9a-f]{32}', f[5]) for i, f in enumerate(frames))
        report['decoded_video'] = {'framemd5_sha256': sha(out), 'frames': len(frames),
                                   'time_base': str(tb), 'timing_valid': bool(valid)}
        check(valid, 'full video decode frame count/PTS/duration invalid')
    except Exception as exc:
        check(False, 'video framemd5 validation failed: ' + str(exc))
    row, out, _ = command('audio-pcm', common + ['-map', '0:a:0', '-vn', '-ac', '2',
                          '-ar', '48000', '-c:a', 'pcm_s16le', '-f', 's16le', '-'])
    size = out.stat().st_size if out.exists() else 0
    report['decoded_audio'] = {'pcm_sha256': sha(out) if out.exists() else None,
                              'bytes': size, 'sample_frames': size // 4,
                              'format': 's16le', 'sample_rate': 48000, 'channels': 2}
    check(size > 0 and size % 4 == 0 and abs(size // 4 - 960000) <= 1024,
          'decoded audio length outside 20s plus/minus 1024 samples')
    # copyts retains the original stream offset; no -ar/-ac conversion can hide it.
    row, out, _ = command('audio-framemd5', [args.ffmpeg, '-nostdin', '-v', 'error',
                          '-xerror', '-copyts', '-i', str(final), '-map', '0:a:0',
                          '-vn', '-c:a', 'pcm_s16le', '-f', 'framemd5', '-'])
    try:
        timeline = audio_timeline(out.read_text(), audio[0])
        check(timeline['sample_frames'] == size // 4, 'PCM/timestamp decoded sample counts differ')
        report['decoded_audio'].update(timeline=timeline, timeline_sha256=digest(timeline),
                                      framemd5_sha256=sha(out))
    except Exception as exc:
        report['decoded_audio']['timeline'] = None
        report['decoded_audio']['timeline_unavailable_reason'] = str(exc)
        check(False, 'original audio timeline validation failed: ' + str(exc))
    row, _, err = command('blackdetect', [args.ffmpeg, '-nostdin', '-v', 'info', '-xerror',
                              '-i', str(final), '-map', '0:v:0', '-an', '-vf',
                              'blackdetect=d=0:pix_th=0.10:pic_th=0.98', '-f', 'null', '-'])
    if row['exit_code'] == 0:
        report['no_black'] = 'black_start:' not in err.read_text(errors='replace')
        check(report['no_black'], 'blackdetect found black frames')
    check(original_hash is not None and final.is_file() and sha(final) == original_hash,
          'final movie missing or changed during quality checks')
    report['final_sha256'] = original_hash
    report['status'] = 'passed' if not report['errors'] else 'failed'
    with output.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
