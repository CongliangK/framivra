"""Write an isolated 20s HTML study, then measure complete cold/warm vtx all pairs.

Not the SVG main benchmark. New output directory only; no cache deletion.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import time
import wave

import psutil

REPO = Path(__file__).resolve().parents[2]
PAGE = 'templates/tech-talk/assets/gfx-templates/chart_curve.html'
FRAMES = 600
FIXED_ENV = dict(VTX_GFX='cdp', VTX_GFX_PAGES='7', VTX_GFX_SPLIT_FRAMES='450',
                 VTX_GFX_SPLIT_MAX='3', VTX_GFX_NAVFREE='0', VTX_GFX_STATIC='1',
                 VTX_VIDEO_SEGMENTS='1', VTX_IO_WORKERS='2', VTX_PIPELINE='1',
                 VTX_NO_NVENC='1', VTX_SEG_NVENC='0', VTX_FILTER_THREADS='1')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def inventory(project):
    return {str(p.relative_to(project)): {'sha256': sha(p), 'bytes': p.stat().st_size,
            'mtime_ns': p.stat().st_mtime_ns}
            for p in sorted((project / 'cache').rglob('*'))
            if p.is_file() and p.suffix in ('.png', '.wav', '.mp4', '.vtxp')
            and 'tmp' not in p.relative_to(project / 'cache').parts}


def command(argv, directory, env):
    """Preserve every command, output, exit and sampled process-tree lower bounds."""
    directory.mkdir()
    save(directory / 'command.json', {'argv': list(map(str, argv)),
         'environment': {k: v for k, v in env.items() if k.startswith('VTX_')},
         'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    started = time.monotonic()
    samples, maxima, peak = [], {}, 0
    with (directory / 'stdout.log').open('wb') as out, (directory / 'stderr.log').open('wb') as err:
        proc = subprocess.Popen(list(map(str, argv)), cwd=REPO, env=env,
                                stdout=out, stderr=err, start_new_session=True)
        parent = psutil.Process(proc.pid)
        while True:
            rss = 0
            try:
                tree = [parent, *parent.children(recursive=True)]
            except psutil.Error:
                tree = []
            for child in tree:
                try:
                    key = f'{child.pid}:{child.create_time()}'
                    mem = child.memory_info().rss
                    cpu = child.cpu_times()
                    io = child.io_counters()
                    rss += mem
                    maxima[key] = {'cpu_s': cpu.user + cpu.system,
                                   'read_bytes': io.read_bytes, 'write_bytes': io.write_bytes}
                except psutil.Error:
                    continue
            peak = max(peak, rss)
            samples.append({'offset_s': time.monotonic() - started, 'tree_rss_bytes': rss})
            try:
                code = proc.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
    result = {'exit_code': code, 'wall_s': time.monotonic() - started,
              'sampled_peak_tree_rss_bytes': peak, 'sampled_processes': len(maxima),
              'sampled_cpu_s': sum(x['cpu_s'] for x in maxima.values()),
              'sampled_read_bytes': sum(x['read_bytes'] for x in maxima.values()),
              'sampled_write_bytes': sum(x['write_bytes'] for x in maxima.values()),
              'resource_boundary': '100ms process-tree samples; lower bounds, short-lived children may be missed; RSS sums include shared pages',
              'loadavg_before_after_end_only': list(os.getloadavg())}
    save(directory / 'resources.json', {'samples': samples, 'process_last_observations': maxima})
    save(directory / 'result.json', result)
    print(directory.name, result, flush=True)
    return result


def prepare(project):
    project.mkdir()
    (project / 'gfx').mkdir()
    shutil.copyfile(REPO / PAGE, project / 'gfx/chart_curve.html')
    # Deterministic public calibration tone, not a narration or content-quality claim.
    with wave.open(str(project / 'tone.wav'), 'wb') as stream:
        stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        second = b''.join(struct.pack('<hh', *([round(2200 * math.sin(2 * math.pi * 220 * i / 48000))] * 2))
                          for i in range(48000))
        stream.writeframes(second * 20)
    save(project / 'timeline.json', {'version': 2,
         'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': FRAMES},
         'sources': [{'id': 'tone', 'path': 'tone.wav'}],
         'video_tracks': [{'name': 'tech-talk-chart', 'kind': 'gfx', 'clips': [
             {'src': 'gfx/chart_curve.html', 'dst_in': 0, 'dur': FRAMES}]}],
         'audio_tracks': [{'name': 'calibration-tone', 'events': [
             {'src': 'tone', 'in': 0, 'out': FRAMES, 'volume': 1}]}], 'subtitles': []})
    return {str(p.relative_to(project)): sha(p) for p in sorted(project.rglob('*')) if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--ffmpeg', required=True, type=Path)
    parser.add_argument('--chrome', required=True, type=Path)
    parser.add_argument('--tmpdir', required=True, type=Path,
                        help='existing short physical directory for Chrome Unix sockets')
    parser.add_argument('--tabs', type=int, default=4)
    parser.add_argument('--pairs', type=int, default=3)
    args = parser.parse_args()
    if args.tabs < 1 or args.pairs < 1:
        parser.error('tabs and pairs must be positive')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    ffmpeg, chrome = args.ffmpeg.resolve(), args.chrome.resolve()
    env = {k: v for k, v in os.environ.items() if not k.startswith('VTX_')}
    env.update(FIXED_ENV, VTX_GFX_TABS=str(args.tabs), VTX_CHROME=str(chrome),
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', TMPDIR=str(args.tmpdir.resolve(strict=True)),
               PATH=str(ffmpeg.parent) + os.pathsep + os.environ['PATH'])
    project_input = root / 'input'
    inputs = prepare(project_input)
    versions = {}
    for name, cmd in {'python': [sys.executable, '--version'], 'chrome': [chrome, '--version'],
                      'ffmpeg': [ffmpeg, '-version'], 'ffprobe': [ffmpeg.parent / 'ffprobe', '-version'],
                      'fonts': ['fc-list', '--format', '%{file}\n']}.items():
        data = subprocess.check_output(list(map(str, cmd)), text=True)
        versions[name] = data
    fonts = {p: sha(p) for p in sorted(set(versions['fonts'].splitlines())) if Path(p).is_file()}
    identity = {'source_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
                'source_tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=REPO, text=True).strip(),
                'git_status': subprocess.check_output(['git', 'status', '--short'], cwd=REPO, text=True),
                'platform': platform.platform(), 'cpu': platform.processor(), 'cpus': os.cpu_count(),
                'memory_bytes': psutil.virtual_memory().total, 'versions': versions, 'fonts_sha256': fonts,
                'tools_sha256': {str(p): sha(p) for p in (ffmpeg, ffmpeg.parent / 'ffprobe', chrome, Path(sys.executable).resolve())},
                'inputs': inputs, 'page_source': PAGE, 'environment': {k: v for k, v in env.items() if k.startswith('VTX_')},
                'source_sha256': {str(p.relative_to(REPO)): sha(p) for base in ('vtx', 'experiments/perf_html_workflow')
                                  for p in sorted((REPO / base).rglob('*.py'))},
                'tmpdir': env['TMPDIR'],
                'cache_definition': 'cold=new project cache/out; warm=same project immediately rerun; OS page cache not cleared',
                'unobserved': {k: {'value': None, 'reason': reason} for k, reason in {
                    'browser_png_encoder_s': 'CDP capture includes raster, encode, transport and JSON; no browser-internal timer',
                    'downstream_png_read_only_s': 'video stage combines PNG read/decode, composition, encode and output write',
                    'downstream_output_write_only_s': 'no separate FFmpeg read/write timers',
                    'exact_process_tree_peak': 'sampling only; not an OS-enforced process-tree peak',
                    'gpu_vram_bytes': 'not sampled; CPU FFmpeg and existing CDP visual flags fixed',
                    'poll_sleep_only_s': 'included in navigate wall, not separately timed',
                    'os_cold': 'no system-wide cache flushing on shared workstation',
                    'svg_main_benchmark': 'UNRUN; independent HTML workload, not the primary SVG benchmark'}.items()}}
    identity['tracked_runtime_sha256'] = {name: sha(REPO / name) for name in
        subprocess.check_output(['git', 'ls-files', '-z', 'vtx', 'plugins', 'VERSION', 'config.example.json'],
                                cwd=REPO, text=True).split('\0') if name and (REPO / name).is_file()}
    save(root / 'identity.json', identity)
    successes = True
    for pair in range(1, args.pairs + 1):
        project = root / f'pair-{pair}'
        shutil.copytree(project_input, project)
        cold_inventory = None
        for state in ('cold', 'warm'):
            log = root / f'{pair}-{state}'
            result = command([sys.executable, '-m', 'experiments.perf_html_workflow.observe',
                              root / f'{pair}-{state}-observation.json', '-t', project / 'timeline.json',
                              '--events-jsonl', root / f'{pair}-{state}-events.jsonl', 'all'], log, env)
            successes &= result['exit_code'] == 0
            for filename in ('all.events.jsonl', 'qc_report.json', 'receipt.json', 'audio_report.json'):
                source = project / 'out' / filename
                if source.exists():
                    shutil.copyfile(source, log / filename)
            from vtx.render.gfx_pack import inspect_pack
            certificates = {str(p.relative_to(project)): json.loads(p.read_text())
                            for p in (project / 'cache/gfx').rglob('*.json')}
            certificates.update({str(p.relative_to(project)): inspect_pack(p, root=project)['record']
                                 for p in (project / 'cache/gfx_packs').rglob('*.vtxp')})
            save(log / 'gfx-certificates.json', certificates)
            current = inventory(project)
            save(log / 'cache.json', current)
            save(log / 'outputs.json', {p.name: {'sha256': sha(p), 'bytes': p.stat().st_size}
                 for p in (project / 'out').glob('*') if p.is_file() and p.suffix in ('.mp4', '.wav')})
            final = project / 'out/final.mp4'
            if final.exists():
                shutil.copyfile(final, log / 'final.mp4')
            observation = json.loads((root / f'{pair}-{state}-observation.json').read_text())
            rendered = [r['stats'] for r in observation['records']
                        if r['operation'] == 'render_page' and r['status'] == 'ok']
            cdp_valid = (observation['exit_code'] == 0 and (bool(rendered) or state == 'warm')
                         and not any(r['status'] != 'ok' for r in observation['records'])
                         and sum(r['rendered'] for r in rendered) == (FRAMES if state == 'cold' else 0)
                         and not any(r['failed'] for r in rendered))
            save(log / 'cdp-qualification.json', {'valid': cdp_valid,
                 'reason': 'Requires complete expected CDP frame count, zero observed failures and successful exit; failed attempts retained but excluded from qualification'})
            successes &= cdp_valid
            if state == 'cold':
                cold_inventory = current
            else:
                unchanged = bool(cold_inventory) and all(current.get(k) == v for k, v in cold_inventory.items())
                save(log / 'cache-reuse.json', {'files_before': len(cold_inventory),
                     'all_existing_unchanged': unchanged})
                successes &= unchanged
            if result['exit_code']:
                # Keep the failed attempt intact; do not silently retry into its cache.
                break
        if not successes:
            break
    save(root / 'run-result.json', {'success': bool(successes), 'requested_pairs': args.pairs})
    return 0 if successes else 1


if __name__ == '__main__':
    sys.exit(main())
