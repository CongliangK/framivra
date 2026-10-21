"""30-page generated-PNG CPU consumer reconciliation, NOT HTML acceptance.

Prepopulate valid deterministic PNGs only for new gfx identities, then use real
CLI gfx validation and video consumers. Track every PNG's size/mtime and every
video product; compare exact path sets against saved input-impact snapshots.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    from tests.gfx._png import write_png
    from vtx.hygiene import gfx_dir
    from vtx.impact import capture, compare
    env = dict(os.environ, VTX_SEG_NVENC='0', VTX_NO_NVENC='1',
               VTX_VIDEO_SEGMENTS='8', VTX_VIDEO_WORKERS='2')
    tl = dict(version=2, project=dict(fps=30, width=64, height=64,
        duration_frames=2700, cache_dependencies=['global.bin']), sources=[],
        subtitles=[], audio_tracks=[], video_tracks=[dict(name='pages', kind='gfx', clips=[])])
    (root / 'global.bin').write_bytes(b'g' * 262144)
    (root / 'local.bin').write_bytes(b'l' * 262144)
    for i in range(30):
        (root / f'page{i}.html').write_text(f'<p>generated PNG fixture {i}</p>')
        cl = dict(src=f'page{i}.html', dst_in=90 * i, dur=90)
        if i == 7:
            cl['cache_dependencies'] = ['local.bin']
        tl['video_tracks'][0]['clips'].append(cl)
    c = SimpleNamespace(tl=tl, root=root, cache=root / 'cache', fps=30, w=64, h=64)

    def render(label):
        (root / 'timeline.json').write_text(json.dumps(tl))
        # Explicit synthetic producer seam, never presented as browser execution.
        for cl in tl['video_tracks'][0]['clips']:
            directory = gfx_dir(c, cl)
            if directory.exists():
                continue
            directory.mkdir(parents=True)
            seed = int(hashlib.sha256(directory.name.encode()).hexdigest()[:6], 16)
            for frame in range(cl['dur']):
                write_png(directory / f'f{frame:04d}.png', 64, 64, (seed + frame) % 200)
        for stage in ('gfx', 'video'):
            command = [sys.executable, str(repo / 'vtx/vtx.py'), '-t', str(root / 'timeline.json'), stage]
            started = time.monotonic()
            result = subprocess.run(command, capture_output=True, env=env)
            (root / f'{label}-{stage}.log').write_bytes(result.stdout + result.stderr)
            commands.append(dict(argv=command, exit_code=result.returncode,
                                 seconds=time.monotonic() - started))
            if result.returncode:
                raise RuntimeError(f'{label}-{stage}: exit {result.returncode}')

    def products():
        files = {p.relative_to(root).as_posix(): [p.stat().st_size, p.stat().st_mtime_ns]
                 for folder, pattern in [('gfx', '*/*.png'), ('vseg', '*.mp4'), ('vseg_qc', '*.mp4'), ('vseg_pass', '*.mkv')]
                 for p in (root / 'cache' / folder).glob(pattern)}
        paths = {str(Path(name).parent) if name.startswith('cache/gfx/') else name for name in files}
        return files, paths

    commands, results = [], []
    render('cold')
    before = capture(tl, root)
    initial_files, initial_paths = products()
    render('noop')
    assert products() == (initial_files, initial_paths), 'warm no-op rewrote products'

    def check(label):
        nonlocal before, initial_files, initial_paths
        after = capture(tl, root)
        predicted = compare(before, after)
        render(label)
        current, paths = products()
        new = sorted(paths - initial_paths)
        rewritten = sorted(p for p in current.keys() & initial_files.keys() if current[p] != initial_files[p])
        passed = new == predicted['new_artifacts'] and not rewritten
        results.append(dict(case=label, passed=passed, predicted=predicted,
                            actual_new=new, actual_rewritten=rewritten))
        (root / 'reconciliation.json').write_text(json.dumps(dict(
            coverage='generated PNG producer + real gfx validator and CPU video consumer; NOT browser HTML',
            environment={k: env[k] for k in ('VTX_SEG_NVENC', 'VTX_NO_NVENC', 'VTX_VIDEO_SEGMENTS', 'VTX_VIDEO_WORKERS')},
            commands=commands, results=results), indent=2))
        assert passed, f'{label}: predicted/actual mismatch'
        before, initial_files, initial_paths = after, current, paths

    def middle_edit(name):
        path = root / name
        st = path.stat(); data = bytearray(path.read_bytes()); data[131072] ^= 1
        path.write_bytes(data); os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))

    (root / 'page7.html').write_text('<p>changed page eight</p>')
    check('page-eight')
    middle_edit('local.bin')
    check('local-middle-restored-mtime')
    middle_edit('global.bin')
    check('global-middle-restored-mtime')
    print(json.dumps(dict(passed=True, scenes=30, frames=2700, cases=len(results),
                         html_render_acceptance='NOT RUN'), indent=2))


if __name__ == '__main__':
    main()
