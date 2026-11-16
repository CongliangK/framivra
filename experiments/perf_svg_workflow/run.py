"""Opt-in canonical SVG cold/warm trial, not a performance or quality shortcut.

Writes a NEW external output directory; invokes official demo prepare/verify,
complete default all and separate strict QC. Run trials serially, including
other media/GUI work on the measurement host. Never clears OS or user caches.
Resource/media helpers are shared with the existing browser benchmark.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from fractions import Fraction


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    parser.add_argument('--inkscape', type=Path, required=True)
    parser.add_argument('--fonts-dir', type=Path, required=True)
    parser.add_argument('--helper-directory', type=Path,
                        help='explicit trusted directory containing the existing _bench_runtime.py; defaults to repo/scripts/gfx')
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error('output must be a new directory')
    parent = output.parent.resolve(strict=True)
    output = parent / output.name
    if output == repo or repo in output.parents or output == Path.home():
        parser.error('output must be external to the source checkout and not HOME')
    python = args.python.absolute()  # Keep the selected venv entry, not its base symlink.
    ffmpeg = args.ffmpeg.resolve(strict=True)
    ffprobe = ffmpeg.parent / 'ffprobe'
    inkscape = args.inkscape.resolve(strict=True)
    fonts = args.fonts_dir.resolve(strict=True)
    helpers = (args.helper_directory or repo / 'scripts/gfx').resolve(strict=True)
    sys.path.insert(0, str(helpers))
    from _bench_runtime import run_command, verify_media
    sys.path.insert(0, str(repo))
    from vtx.runtime.events import read_events

    output.mkdir(mode=0o700)
    config = output / 'config.json'
    config.write_text('{}\n', encoding='utf-8')
    project = output / 'project'
    # No inherited VTX options, Python path, loader or quality/concurrency knobs.
    # Tool directories only; product SVG subprocesses further isolate their env.
    path = os.pathsep.join(dict.fromkeys([str(ffmpeg.parent), str(python.parent),
                                         *os.environ.get('PATH', os.defpath).split(os.pathsep)]))
    env = dict(PATH=path, HOME=str(Path.home()), LANG='C', LC_ALL='C', TZ='UTC',
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               TMPDIR=str(parent), VTX_CONFIG=str(config), VTX_INKSCAPE=str(inkscape))
    report = dict(scope='portable-v2 canonical SVG 900/1080p30; project cold, not OS cold; actual scheduling/quality/cache defaults',
                  status='running', environment=env, commands={}, states=[], source_sha256={},
                  harness_sha256=sha(__file__), helper_sha256=sha(helpers / '_bench_runtime.py'),
                  limitations=['Raw evidence contains local paths; review and redact before publishing.',
                               'Human artistic/audio approval is separate and not performed by this runner.',
                               'Resource numbers retain the shared helper sampling limitations.'])

    def save():
        (output / 'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')

    def run(label, argv):
        result = run_command(list(map(str, argv)), repo, env, output / (label + '.log'),
                             3600, sample_interval=0.5)
        report['commands'][label] = result
        save()
        if result['exit_code'] != 0 or result['error'] or result['timed_out']:
            raise RuntimeError(label + ' failed; original evidence retained')
        return result

    for base in ['vtx', 'plugins', 'scripts', 'templates/demo-timeline-svg']:
        for path in sorted((repo / base).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                report['source_sha256'][str(path.relative_to(repo))] = sha(path)
    report['tools_sha256'] = {str(path): sha(path) for path in [python, ffmpeg, ffprobe, inkscape]}
    save()
    try:
        run('prepare', [python, repo / 'scripts/demo.py', 'prepare', '--output', project, '--fonts-dir', fonts])
        run('verify', [python, repo / 'scripts/demo.py', 'verify', '--project', project])
        # Preparation provenance varies by engine, path and elapsed time. Retain
        # it separately; only rendering inputs belong in cross-arm equality.
        report['input_sha256'], report['preparation_provenance_sha256'] = {}, {}
        for path in sorted(project.rglob('*')):
            relative = path.relative_to(project)
            if not path.is_file() or relative.parts[0] in ['out', 'cache']:
                continue
            provenance = relative.parts[0] in ['logs', '.generated'] or str(relative) == 'realization.json'
            target = report['preparation_provenance_sha256'] if provenance else report['input_sha256']
            target[str(relative)] = sha(path)
        entry = [python, repo / 'vtx/vtx.py', '-t', project / 'timeline.json']
        reference = cache_reference = None
        for state in ['cold', 'warm']:
            row = dict(state=state)
            report['states'].append(row)
            save()
            row['all'] = run(state + '-all', entry + ['--events-jsonl', output / (state + '-all.events.jsonl'), 'all'])
            for name in ['qc_report.json', 'receipt.json', 'audio_report.json', 'VERSIONS.json']:
                path = project / 'out' / name
                if path.exists():
                    shutil.copyfile(path, output / (state + '-all-' + name))
            # Distinct snapshot BEFORE strict/warm; never claim two from one file.
            final = project / 'out/final.mp4'
            shutil.copyfile(final, output / (state + '-final.mp4'))
            row['final_sha256'] = sha(final)
            row['strict'] = run(state + '-strict', entry + ['--events-jsonl', output / (state + '-strict.events.jsonl'), 'qc', '--strict'])
            for command in ['all', 'strict']:
                events = read_events((output / (state + '-' + command + '.events.jsonl')).read_bytes())
                if not events['complete'] or events['exit_code'] != 0:
                    raise RuntimeError('incomplete or failed execution events')
            row['events_complete'] = True
            shutil.copyfile(project / 'out/qc_report.json', output / (state + '-strict-qc.json'))
            row['media'] = verify_media(project, output / (state + '-media'), ffmpeg, ffprobe, 3600, env)
            if row['media']['status'] != 'passed':
                raise RuntimeError('full decoded media verification failed')
            pts_path = output / (state + '-exact-pts.json')
            run(state + '-exact-pts', [ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                                      '-show_streams', '-show_entries', 'frame=pts:stream=time_base,duration_ts',
                                      '-of', 'json', '-o', pts_path, final])
            pts = json.loads(pts_path.read_text(encoding='utf-8'))
            tb = Fraction(pts['streams'][0]['time_base'])
            if (len(pts['frames']) != 900
                    or any(frame['pts'] * tb != Fraction(i, 30) for i, frame in enumerate(pts['frames']))
                    or pts['streams'][0]['duration_ts'] * tb != 30):
                raise RuntimeError('exact source PTS/duration mismatch')
            row['exact_pts'] = True
            media = tuple(row['media'][name] for name in ['video_digest', 'audio_digest', 'audio_timeline_digest'])
            if reference is None:
                reference = media
            if media != reference:
                raise RuntimeError('decoded cold/warm output differs')
            certificates = list((project / 'cache').rglob('svg-complete.json'))
            if len(certificates) != 1:
                raise RuntimeError('expected one canonical SVG certificate')
            certificate = certificates[0]
            value = json.loads(certificate.read_text(encoding='utf-8'))
            actual = [sha(certificate.parent / f'f{i:04d}.png') for i in range(900)]
            if actual != value['sha256']:
                raise RuntimeError('full PNG byte identity mismatch')
            cache = {str(path.relative_to(project)): dict(sha256=sha(path), bytes=path.stat().st_size,
                                                         mtime_ns=path.stat().st_mtime_ns)
                     for path in sorted(certificate.parent.glob('f*.png'))}
            if len(cache) != 900:
                raise RuntimeError('expected exactly 900 retained PNGs')
            if cache_reference is None:
                cache_reference = cache
            if cache != cache_reference:
                raise RuntimeError('warm PNG persistence changed')
            (output / (state + '-cache.json')).write_text(json.dumps(cache, indent=2), encoding='utf-8')
            shutil.copyfile(certificate, output / (state + '-svg-complete.json'))
            row['png_sequence_sha256'] = hashlib.sha256(json.dumps(actual).encode()).hexdigest()
            row['png_bytes'] = sum(path.stat().st_size for path in certificate.parent.glob('f*.png'))
            row['disk_logical_bytes'] = {name: sum(path.stat().st_size for path in (project / name).rglob('*')
                                                if path.is_file()) for name in ['cache', 'out']}
            row['gfx_certificate_build'] = dict(raster_seconds=value['raster_seconds'],
                                                batch_count=value['batch_count'],
                                                scope='original cold producer, not warm execution work')
            # core intentionally removes the intermediate WAV. Verify the REAL
            # final decoded PCM clock; do not change retention just for this study.
            timing = row['media']['audio_timing']
            row['decoded_audio'] = dict(samples=timing['sample_frames'], sample_rate=timing['sample_rate'],
                                         channels=timing['channels'], sample_format=timing['sample_format'],
                                         decoded_duration=timing['decoded_duration'],
                                         source_duration=timing['source_duration'],
                                         pcm_sha256=row['media']['audio_digest'])
            if (row['decoded_audio']['samples'] != 1440000 or row['decoded_audio']['sample_rate'] != 48000
                    or row['decoded_audio']['channels'] != 2):
                raise RuntimeError('final decoded audio sample clock mismatch')
            receipt = json.loads((output / (state + '-all-receipt.json')).read_text(encoding='utf-8'))
            row['mixed_audio_receipt'] = receipt['stages']['audio']['post_loudness']
            row['mixed_audio_receipt_scope'] = 'product post-loudness receipt, not an independently retained WAV snapshot'
            if sha(final) != row['final_sha256']:
                raise RuntimeError('final changed during independent verification')
            row['status'] = 'passed'
            save()
        report['status'] = 'passed'
    except BaseException as error:
        report['status'], report['error'] = 'failed', repr(error)
        save()
        raise
    finally:
        save()
    return 0


if __name__ == '__main__':
    sys.exit(main())
