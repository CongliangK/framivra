"""Opt-in real Chrome/FFmpeg 30-page impact acceptance (never PNG prepopulation).

Run only on a normally sandboxed Chrome host. Static spans use the production
renderer optimization: Chrome produces each page's pixels and the renderer
materializes every frame of the explicit 48- or 90-frame page profile. This is correctness evidence, not a speed benchmark.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tests.impact_inventory import POLICY, observe, delta, validate_sidecars, is_pass_sidecar


def profile(frames_per_page):
    if frames_per_page not in (48, 90):
        raise ValueError("frames per page must be 48 or 90")
    return dict(frames_per_page=frames_per_page, pages=30,
                total_frames=30 * frames_per_page, static_copied=frames_per_page - 1,
                subtitle=[640 * frames_per_page // 90, 670 * frames_per_page // 90])


def fixture(root, frames_per_page=90):
    """Self-contained consumed dependencies; effective rules lie beyond 128 KiB."""
    shape = profile(frames_per_page)
    padding = '/*' + 'x' * 131072 + '*/\n'
    (root / 'global.css').write_text(padding + '#global{background:#224466}\n' + padding)
    (root / 'local.css').write_text(padding + '#local{background:#668822}\n' + padding)
    clips = []
    for i in range(30):
        local = '<link rel="stylesheet" href="local.css">' if i == 7 else ''
        (root / f'page{i}.html').write_text(
            '<!doctype html><meta charset="utf-8"><style>'
            'html,body{margin:0;width:1920px;height:1080px;background:#ffffff}'
            'div{position:absolute;width:200px;height:200px;top:100px}'
            '#global{left:100px}#local{left:400px;background:#999999}'
            '#page{left:700px;background:#aa2244}</style>'
            '<link rel="stylesheet" href="global.css">' + local +
            f'<h1>Page {i + 1}</h1><div id="global"></div><div id="local"></div>'
            f'<div id="page"></div><script>window.__vtx_static_spans=[[0,{frames_per_page}]];</script>')
        clip = dict(src=f'page{i}.html', dst_in=frames_per_page * i, dur=frames_per_page)
        if i == 7:
            clip['cache_dependencies'] = ['local.css']
        clips.append(clip)
    return dict(version=2, project=dict(fps=30.0, width=1920, height=1080,
        duration_frames=shape['total_frames'], cache_dependencies=['global.css']), sources=[],
        audio_tracks=[], subtitles=[dict(zip(('in', 'out'), shape['subtitle']), text='Issue 52 page eight')],
        video_tracks=[dict(name='pages', kind='gfx', clips=clips)])


def middle_edit(path, old, new):
    stat = path.stat()
    data = path.read_bytes()
    assert len(old) == len(new) and data.count(old) == 1
    position = data.index(old)
    assert 65536 < position < len(data) - 65536
    path.write_bytes(data.replace(old, new))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size and path.stat().st_mtime_ns == stat.st_mtime_ns


def partial_inventory(root, max_entries=512, seconds=1.0):
    """Bounded, non-hashing failure observation; never acceptance/recovery proof."""
    started = time.monotonic()
    result = dict(partial=True, entries=[], scanned_entries=0, truncated=False, errors=[])
    for folder in ('cache/vseg', 'cache/vseg_qc', 'cache/vseg_pass', 'out'):
        directory = root / folder
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if result['scanned_entries'] >= max_entries or time.monotonic() - started >= seconds:
                        result['truncated'] = True
                        return result
                    result['scanned_entries'] += 1
                    if entry.is_file(follow_symlinks=False):
                        stat = entry.stat(follow_symlinks=False)
                        result['entries'].append(dict(path=folder + '/' + entry.name,
                            size=stat.st_size, mtime_ns=stat.st_mtime_ns))
        except OSError as error:
            result['errors'].append(str(error))
    return result


def main():
    import resource  # Hosted Linux runner only; fixture unit tests remain portable.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frames-per-page', type=int, choices=(48, 90), default=90,
                        help='48: correctness profile; 90: retained long profile')
    args = parser.parse_args()
    shape = profile(args.frames_per_page)
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    evidence = root / 'evidence'
    evidence.mkdir()
    from PIL import Image
    from vtx.gfx_cli import png_invalid_reason
    from vtx.hygiene import gfx_dir
    from vtx.impact import capture, compare
    from vtx.infra import Ctx
    env = dict(os.environ, VTX_SEG_NVENC='0', VTX_NO_NVENC='1',
               VTX_VIDEO_SEGMENTS='8', VTX_GFX='cdp',
               VTX_GFX_PAGES='2', VTX_GFX_TABS='1', VTX_GFX_STATIC='1',
               VTX_GFX_NAVFREE='0', VTX_GFX_SPLIT_FRAMES='0')
    # Do not inherit experimental cache routing or hidden browser wrappers.
    env.pop('VTX_GFX_CACHE_NS', None)
    # Checked at subprocess boundaries; Python work may exceed this budget.
    # The hosted workflow's 65-minute job ceiling is the hard outer bound.
    deadline = time.monotonic() + 3000
    report = dict(passed=False, coverage='real Chrome HTML + CPU composition',
                  commands=[], cases=[], profile=shape, inventory_policy=POLICY,
                  resource_scope='65-minute hosted job ceiling; 50-minute budget checked at subprocess boundaries; disk/RSS observations are not quotas; production chooses CPU concurrency',
                  environment={k: env[k] for k in ('VTX_SEG_NVENC', 'VTX_NO_NVENC',
                      'VTX_VIDEO_SEGMENTS', 'VTX_GFX', 'VTX_GFX_PAGES',
                      'VTX_GFX_TABS', 'VTX_GFX_STATIC', 'VTX_GFX_NAVFREE', 'VTX_GFX_SPLIT_FRAMES')})
    def save():
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        report['resources'] = dict(disk_free_bytes=shutil.disk_usage(root).free,
            child_maxrss_kib=usage.ru_maxrss, child_user_seconds=usage.ru_utime,
            child_system_seconds=usage.ru_stime, parent_maxrss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        (evidence / 'report.json').write_text(json.dumps(report, indent=2))
    def run(argv, label, limit=900):
        command = list(map(str, argv))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('acceptance 50-minute subprocess-boundary budget exhausted')
        start = time.monotonic()
        with (evidence / f'{label}.log').open('wb') as log:
            try:
                proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                        env=env, start_new_session=True)
                proc.wait(timeout=min(limit, remaining))
            except subprocess.TimeoutExpired:
                report['failure_partial_inventory'] = partial_inventory(root)
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                report['commands'].append(dict(argv=command, timed_out=True,
                    seconds=time.monotonic() - start))
                save()
                raise
        report['commands'].append(dict(argv=command, exit_code=proc.returncode,
                                       seconds=time.monotonic() - start,
                                       disk_free_bytes=shutil.disk_usage(root).free,
                                       child_maxrss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss))
        save()
        if proc.returncode:
            raise RuntimeError(f'{label}: exit {proc.returncode}')
        return (evidence / f'{label}.log').read_text()
    try:
        chrome = shutil.which('google-chrome') or shutil.which('google-chrome-stable')
        if not chrome:
            raise RuntimeError('normal installed Google Chrome is required')
        env['VTX_CHROME'] = chrome
        report['environment']['VTX_CHROME'] = chrome
        report['identity'] = {key: run(['git', '-C', REPO, *cmd], 'identity-' + key, 10).strip()
            for key, cmd in [('head', ['rev-parse', 'HEAD']), ('tree', ['rev-parse', 'HEAD^{tree}']),
                             ('status', ['status', '--porcelain'])]}
        if report['identity']['status']:
            raise RuntimeError('acceptance requires a clean frozen checkout')
        for name, argv in [('python', [sys.executable, '--version']), ('chrome', [chrome, '--version']),
                           ('ffmpeg', ['ffmpeg', '-version']), ('ffprobe', ['ffprobe', '-version'])]:
            run(argv, 'version-' + name, 15)
        # Startup failure is fatal; never change sandbox flags or OS security.
        run([chrome, '--headless', '--disable-gpu', '--no-first-run',
             '--user-data-dir=' + str(root / 'preflight-profile'), '--dump-dom', 'about:blank'],
            'chrome-sandbox-preflight', 45)
        tl = fixture(root, args.frames_per_page)
        expected = dict(global_color=(34, 68, 102), local_color=(102, 136, 34), page_color=(170, 34, 68))
        timeline = root / 'timeline.json'
        def render(label):
            timeline.write_text(json.dumps(tl))
            for stage in ('gfx', 'video'):
                run([sys.executable, REPO / 'vtx/vtx.py', '-t', timeline, '--events-jsonl',
                     evidence / f'{label}-{stage}-events.jsonl', stage], label + '-' + stage)
                shutil.copy2(root / 'out' / 'receipt.json', evidence / f'{label}-{stage}-receipt.json')
            if label != 'warm':
                detail = json.loads((evidence / f'{label}-gfx-receipt.json').read_text())['stages']['gfx']['detail']
                expected_pages = 30 if label in ('cold', 'global-middle-restored-mtime') else 1
                assert detail.count(f"static_copied×{shape['static_copied']}") == expected_pages, detail
                assert detail.count('span×1') == expected_pages, detail
        verified_frame_counts = {}
        def inventory(label):
            observation = observe(root)
            (evidence / f'{label}-all-categories.json').write_text(json.dumps(observation, indent=2))
            def probe(path):
                raw = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames',
                           '-show_entries', 'stream=nb_read_frames', '-of', 'json', path],
                          f'{label}-frames-{path.stem}', 120)
                return json.loads(raw)['streams'][0]['nb_read_frames']
            observation['sidecar_checks'] = validate_sidecars(root, observation['products'], probe, verified_frame_counts)
            (evidence / f'{label}-all-categories.json').write_text(json.dumps(observation, indent=2))
            return observation['products']
        def validate(label, current, previous):
            # Every new PNG fully decodes, every new media product fully decodes.
            for index, name in enumerate(sorted(current.keys() - previous.keys())):
                path = root / name
                if is_pass_sidecar(name):
                    continue  # Integrity and associated decoded frame count checked in inventory().
                if path.suffix == '.png':
                    error = png_invalid_reason(path, width=1920, height=1080)
                    if error:
                        raise AssertionError(f'{name}: {error}')
                else:
                    run(['ffmpeg', '-v', 'error', '-xerror', '-threads', '1', '-i', path,
                         '-map', '0:v:0', '-f', 'null', '-'], f'{label}-decode-{index}', 120)
            # Semantic pixel checks prove linked CSS is consumed, not merely hashed.
            context = Ctx(timeline)
            observed = []
            for i, clip in enumerate(tl['video_tracks'][0]['clips']):
                directory = gfx_dir(context, clip)
                frames = sorted(directory.glob('f*.png'))
                assert len(frames) == shape['frames_per_page'], (i, len(frames))
                for frame in (frames[0], frames[-1]):
                    with Image.open(frame) as im:
                        im = im.convert('RGB')
                        colors = [im.getpixel((x, 150)) for x in (150, 450, 750)]
                    want = [expected['global_color'], expected['local_color'] if i == 7 else (153,153,153),
                            expected['page_color'] if i == 7 else (170,34,68)]
                    assert colors == want, (label, i, colors, want)
                observed.append(dict(page=i + 1, colors=colors, directory=str(directory.relative_to(root))))
                if i in (0, 7):
                    shutil.copy2(frames[0], evidence / f'{label}-page-{i+1}.png')
            (evidence / f'{label}-pixels.json').write_text(json.dumps(observed, indent=2))
            # Both completed main and QC outputs must exist and decode fully.
            outputs = [root / 'out' / 'video_noaudio.mp4', root / 'cache' / 'qc_clean.mp4']
            assert all(p.is_file() for p in outputs), 'missing main or QC composition output'
            for i, path in enumerate(outputs):
                run(['ffmpeg', '-v', 'error', '-xerror', '-threads', '1', '-i', path,
                     '-map', '0:v:0', '-f', 'null', '-'], f'{label}-output-{i}', 120)
                probe = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                    '-show_entries', 'stream=width,height,nb_frames,r_frame_rate', '-of', 'json', path],
                    f'{label}-probe-{i}', 30))['streams'][0]
                assert (probe['width'], probe['height'], int(probe['nb_frames'])) == (1920,1080,shape['total_frames']), probe
                assert probe['r_frame_rate'] == '30/1', probe
        render('cold')
        before = capture(tl, root, nseg=8)
        previous = inventory('cold')
        cold_context = Ctx(timeline)
        cold_frames = {gfx_dir(cold_context, cl).relative_to(root).as_posix(): cl['dur']
                       for cl in tl['video_tracks'][0]['clips']}
        cold_paths = sorted({node['artifact'] for node in before['nodes'].values() if node['artifact']})
        report['cold_delta'] = delta({}, previous, cold_paths, cold_frames)
        save()
        assert report['cold_delta']['passed'], report['cold_delta']
        assert len([p for p in previous if p.endswith('.png')]) == shape['total_frames']
        assert any(p.startswith('cache/vseg_qc/') for p in previous)
        validate('cold', previous, {})
        (evidence / 'cold-inventory.json').write_text(json.dumps(previous, indent=2))
        (evidence / 'cold-snapshot.json').write_text(json.dumps(before, indent=2))
        render('warm')
        assert inventory('warm') == previous, 'warm rebuild added, removed, or rewrote cached products'
        report['warm_noop'] = True
        def check(label):
            nonlocal before, previous
            after = capture(tl, root, nseg=8)
            prediction = compare(before, after)
            render(label)
            current = inventory(label)
            context = Ctx(timeline)
            gfx_frames = {gfx_dir(context, cl).relative_to(root).as_posix(): cl['dur']
                          for cl in tl['video_tracks'][0]['clips']}
            result = dict(case=label, prediction=prediction,
                          **delta(previous, current, prediction['new_artifacts'], gfx_frames))
            report['cases'].append(result)
            save()
            assert result['passed'], result
            added = result['actual_new']
            gfx_added = [p for p in added if p.startswith('cache/gfx/')]
            assert len(gfx_added) == (30 if label.startswith('global') else 1), gfx_added
            validate(label, current, previous)
            (evidence / f'{label}-inventory.json').write_text(json.dumps(current, indent=2))
            (evidence / f'{label}-snapshot.json').write_text(json.dumps(after, indent=2))
            result['passed'] = True
            before, previous = after, current
            save()
        page = root / 'page7.html'
        page.write_text(page.read_text().replace('#aa2244', '#22aa44'))
        expected['page_color'] = (34,170,68)
        check('page-eight')
        middle_edit(root / 'local.css', b'#668822', b'#882266')
        expected['local_color'] = (136,34,102)
        check('local-middle-restored-mtime')
        middle_edit(root / 'global.css', b'#224466', b'#664422')
        expected['global_color'] = (102,68,34)
        check('global-middle-restored-mtime')
        report['passed'] = True
    except Exception as error:
        if 'failure_partial_inventory' not in report:
            report['failure_partial_inventory'] = partial_inventory(root)
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        save()
    print(json.dumps(dict(passed=True, pages=30, frames=shape['total_frames'], resolution=[1920,1080])))


if __name__ == '__main__':
    main()
