"""Bounded Inkscape shell producer; independent static SVG completion evidence.

One shell process per cold clip. Timeout/exception closes that process. This is
not the browser worker's IPC/parent-death supervision protocol.
"""
import json
import subprocess
import time
from pathlib import Path

from . import fstxn, gfx_svg
from .gfx_cli import png_invalid_reason
from .hygiene import manifest_record, tmp_run


def run_shell(executable, directory, count, width, height, *, root=None):
    gfx_svg.private_configuration(directory, root=root)
    env = gfx_svg.process_environment(directory)
    # Pin transparent background RGB as well as alpha; Inkscape defaults to white.
    # The stage source digest in resolve() also invalidates pre-change PNG caches.
    # Every action operand is generated locally; user paths never enter this language.
    actions = '\n'.join(
        f'file-open:f{i:04d}.svg;export-type:png;export-area-page;'
        f'export-width:{width};export-height:{height};'
        'export-background:#000000;export-background-opacity:0;'
        f'export-filename:f{i:04d}.png;export-do;file-close'
        for i in range(count)) + '\nquit\n'
    proc = subprocess.Popen([executable, '--shell'], cwd=directory, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = proc.communicate(actions, timeout=max(30, min(3600, count * 5)))
        if proc.returncode:
            raise RuntimeError(f'Inkscape failed ({proc.returncode}): {stderr[-1500:]}')
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


def render_stage(c):
    total = warm = 0
    for track in c.tl['video_tracks']:
        if track.get('kind') != 'gfx':
            continue
        for clip in track['clips']:
            total += 1
            record = gfx_svg.resolve(c, clip)
            key = gfx_svg.record_key(record)
            directory = c.cache / 'gfx_exp' / gfx_svg.NAMESPACE / key
            if c.dry:
                print(f'  [dry-run] SVG gfx {clip["src"]}[{key}]: {clip["dur"]} frames')
                continue
            from .frame_source import PngSequence
            source = PngSequence(directory, str(directory.relative_to(c.cache)),
                                 clip['dur'], c.fps, c.w, c.h,
                                 environment_check=lambda: gfx_svg.completion(
                                     c, clip, directory, expected_record=record))
            try:
                source.require_ready()
                warm += 1
                continue
            except (OSError, ValueError):
                pass
            # Serialize this key's publication. Failure never leaves a valid marker.
            with fstxn.locked_write(directory / gfx_svg.COMPLETE_FILE, root=c.root) as marker:
                fstxn.unlink(directory / gfx_svg.COMPLETE_FILE, root=c.root)
                with tmp_run(c, 'svg-inkscape', unique=True) as staging:
                    staging = staging.resolve()
                    for i, name in enumerate(record['frames']):
                        data = gfx_svg.read_input(c, name, limit=gfx_svg.MAX_SVG_BYTES)
                        if gfx_svg.digest(data) != record['files'][name]:
                            raise ValueError('SVG input changed before rendering')
                        fstxn.atomic_write(staging / f'f{i:04d}.svg', data, root=c.root)
                    for i, name in enumerate(record['fonts']):
                        data = gfx_svg.read_input(c, name)
                        if gfx_svg.digest(data) != record['files'][name]:
                            raise ValueError('SVG font changed before rendering')
                        fstxn.atomic_write(staging / 'fonts' / f'font{i}{Path(name).suffix.lower()}', data, root=c.root)
                    started = time.monotonic()
                    run_shell(record['engine']['executable'], staging, clip['dur'], c.w, c.h, root=c.root)
                    raster_seconds = time.monotonic() - started
                    hashes = []
                    for i in range(clip['dur']):
                        path = staging / f'f{i:04d}.png'
                        observed = []
                        reason = png_invalid_reason(path, width=c.w, height=c.h, _fingerprint=observed)
                        if reason:
                            raise ValueError(f'Inkscape output frame {i} invalid: {reason}')
                        hashes.append(observed[0])
                    c.verify_inputs('gfx')
                    if gfx_svg.resolve(c, clip) != record:
                        raise ValueError('SVG identity changed during rendering')
                    for i in range(clip['dur']):
                        fstxn.replace(staging / f'f{i:04d}.png', directory / f'f{i:04d}.png', root=c.root)
                    marker.write_text(json.dumps({'format': 'framivra-svg-completion/v1',
                        'key': key, 'input': record, 'sha256': hashes,
                        'experimental': True, 'batch_count': 1, 'raster_seconds': raster_seconds}, sort_keys=True), encoding='utf-8')
            manifest_record(c, directory, 'gfx', key)
    c.receipt('gfx', True, f'{total} SVG gfx clips; {warm} cache hits; Inkscape static sequence',
              extra={'svg_producer': gfx_svg.PRODUCER})
