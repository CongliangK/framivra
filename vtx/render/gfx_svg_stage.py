"""Bounded Inkscape shell producer; independent static SVG completion evidence.

One shell process per cold clip. Timeout/exception closes that process. This is
not the browser worker's IPC/parent-death supervision protocol.
"""
import json
import hashlib
import os
import stat
import subprocess
import time
from pathlib import Path

from ..runtime import fstxn
from . import gfx_svg
from .gfx_cli import png_invalid_reason
from ..cache.hygiene import manifest_record, tmp_run


def run_shell(executable, directory, count, width, height, *, root=None, indices=None):
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
        for i in (range(count) if indices is None else indices)) + '\nquit\n'
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


def reuse_group(record):
    """Only one repeated group, inside one proven closed clip identity."""
    proof = record.get('export_reuse', {})
    if record['fonts'] or proof.get('proof') != 'font-free-rects/v1':
        return []
    allowed = set(proof.get('frames', []))
    groups = {}
    for i, name in enumerate(record['frames']):
        if name in allowed:
            groups.setdefault(record['files'][name], []).append(i)
    return next((group for group in groups.values() if len(group) > 1), [])


def copy_png(source, destination, fingerprint, size, *, root):
    """Bounded independent copy, pinned regular input and validated byte identity."""
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('SVG representative PNG is not a regular file')
    fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) |
                 getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as src:
        opened = os.fstat(src.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_size != size or
                (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
            raise ValueError('SVG representative PNG changed before copy')
        with fstxn.locked_write(destination, root=root) as temporary:
            digest = hashlib.sha256()
            remaining = size
            with temporary.open('wb') as dst:
                while remaining:
                    block = src.read(min(64 * 1024, remaining))
                    if not block:
                        raise ValueError('SVG representative PNG truncated during copy')
                    remaining -= len(block)
                    digest.update(block)
                    dst.write(block)
            if src.read(1) or digest.hexdigest() != fingerprint:
                raise ValueError('SVG representative PNG changed during copy')


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
                    group = reuse_group(record)
                    members = set(group)
                    representative = None
                    for i, name in enumerate(record['frames']):
                        data = gfx_svg.read_input(c, name, limit=gfx_svg.MAX_SVG_BYTES)
                        if gfx_svg.digest(data) != record['files'][name]:
                            raise ValueError('SVG input changed before rendering')
                        if i in members:
                            if representative is None:
                                representative = data
                            elif data != representative:
                                group = []  # Hash equality alone is not byte equality.
                        fstxn.atomic_write(staging / f'f{i:04d}.svg', data, root=c.root)
                    representative = None
                    for i, name in enumerate(record['fonts']):
                        data = gfx_svg.read_input(c, name)
                        if gfx_svg.digest(data) != record['files'][name]:
                            raise ValueError('SVG font changed before rendering')
                        fstxn.atomic_write(staging / 'fonts' / f'font{i}{Path(name).suffix.lower()}', data, root=c.root)
                    started = time.monotonic()
                    reused = set(group)  # Rebuild after any byte-inequality fallback.
                    skipped = set(group[1:])
                    indices = [i for i in range(clip['dur']) if i not in skipped]
                    run_shell(record['engine']['executable'], staging, clip['dur'], c.w, c.h,
                              root=c.root, indices=indices)
                    copied_fingerprint = None
                    if skipped:
                        source = staging / f'f{group[0]:04d}.png'
                        observed, sizes = [], []
                        reason = png_invalid_reason(source, width=c.w, height=c.h,
                                                    _fingerprint=observed, _verified_size=sizes)
                        if reason:
                            raise ValueError(f'Inkscape representative invalid: {reason}')
                        copied_fingerprint = observed[0]
                        for i in group[1:]:
                            copy_png(source, staging / f'f{i:04d}.png', copied_fingerprint, sizes[0], root=c.root)
                    raster_seconds = time.monotonic() - started
                    hashes = []
                    for i in range(clip['dur']):
                        path = staging / f'f{i:04d}.png'
                        observed = []
                        reason = png_invalid_reason(path, width=c.w, height=c.h, _fingerprint=observed)
                        if reason:
                            raise ValueError(f'Inkscape output frame {i} invalid: {reason}')
                        if i in reused and observed[0] != copied_fingerprint:
                            raise ValueError('SVG reused PNG changed after materialization')
                        hashes.append(observed[0])
                    c.verify_inputs('gfx')
                    if gfx_svg.resolve(c, clip) != record:
                        raise ValueError('SVG identity changed during rendering')
                    for i in range(clip['dur']):
                        fstxn.replace(staging / f'f{i:04d}.png', directory / f'f{i:04d}.png', root=c.root)
                    marker.write_text(json.dumps({'format': 'framivra-svg-completion/v1',
                        'key': key, 'input': record, 'sha256': hashes,
                        'experimental': True, 'batch_count': 1, 'raster_seconds': raster_seconds,
                        'export_count': len(indices), 'materialized_count': len(skipped)}, sort_keys=True), encoding='utf-8')
            manifest_record(c, directory, 'gfx', key)
    c.receipt('gfx', True, f'{total} SVG gfx clips; {warm} cache hits; Inkscape static sequence',
              extra={'svg_producer': gfx_svg.PRODUCER})
