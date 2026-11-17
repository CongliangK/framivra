"""Bounded Inkscape shell producer; independent static SVG completion evidence.

Large cold clips use at most four isolated shell processes. Timeout/exception
attempts bounded termination/reaping of every owned shell before returning;
failed cleanup is fatal. This is not browser parent-death supervision.
"""
import json
import hashlib
import os
import stat
import subprocess
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..runtime import fstxn
from . import gfx_svg
from .gfx_cli import png_invalid_reason
from ..cache.hygiene import manifest_record, tmp_run


def _start_shell(executable, directory, *, root):
    gfx_svg.private_configuration(directory, root=root)
    return subprocess.Popen([executable, '--shell'], cwd=directory,
                            env=gfx_svg.process_environment(directory),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def _export(proc, indices, width, height, *, count=None, stop=None):
    # Every action operand is generated locally; user paths never enter this language.
    actions = '\n'.join(
        f'file-open:f{i:04d}.svg;export-type:png;export-area-page;'
        f'export-width:{width};export-height:{height};'
        'export-background:#000000;export-background-opacity:0;'
        f'export-filename:f{i:04d}.png;export-do;file-close'
        for i in indices) + '\nquit\n'
    budget = len(indices) if count is None else count
    timeout = max(30, min(3600, budget * 5))
    if stop is None:
        stdout, stderr = proc.communicate(actions, timeout=timeout)
    else:
        deadline = time.monotonic() + timeout
        while True:
            if stop.is_set():
                raise RuntimeError('SVG shell export cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout)
            try:
                stdout, stderr = proc.communicate(actions, timeout=min(0.5, remaining))
                break
            except subprocess.TimeoutExpired:
                # communicate retains its input offset and captured output.
                # This event bounds thread shutdown, not child termination;
                # the parent still has to terminate/reap every owned shell.
                actions = None
    if proc.returncode:
        raise RuntimeError(f'Inkscape failed ({proc.returncode}): {stderr[-1500:]}')


def _stop_shell(proc):
    if proc.poll() is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass  # It exited between poll and signal; still reap it below.
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)


def _close_shell(proc):
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def run_shell(executable, directory, count, width, height, *, root=None, indices=None):
    proc = _start_shell(executable, directory, root=root)
    try:
        _export(proc, list(range(count) if indices is None else indices), width, height, count=count)
    finally:
        try:
            _stop_shell(proc)
        finally:
            _close_shell(proc)


def shell_batches(indices):
    """Bound startup/memory cost; scheduling does not change the SVG grammar."""
    value = os.environ.get('VTX_SVG_WORKERS')
    if value is not None and value not in ('1', '2', '3', '4'):
        raise ValueError('VTX_SVG_WORKERS must be an integer from 1 to 4')
    workers = int(value) if value is not None else min(4, max(1, (os.cpu_count() or 1) // 2))
    workers = min(workers, max(1, len(indices) // 120))
    return [indices[i * len(indices) // workers:(i + 1) * len(indices) // workers]
            for i in range(workers)]


def run_shell_batches(executable, directory, count, width, height, *, root, indices):
    batches = shell_batches(indices)
    if len(batches) == 1:
        run_shell(executable, directory, count, width, height, root=root, indices=indices)
        return 1
    # Each process owns its HOME, Inkscape profile, fontconfig and font cache.
    # Fonts are independent files, never hardlinks into project or sibling state.
    fonts = [(p.name, p.read_bytes()) for p in sorted((directory / 'fonts').glob('*'))]
    jobs = []
    for number, batch in enumerate(batches):
        shard = directory / f'shell-{number}'
        fstxn.mkdir(shard, root=root)
        for name, data in fonts:
            fstxn.atomic_write(shard / 'fonts' / name, data, root=root)
        for i in batch:
            fstxn.replace(directory / f'f{i:04d}.svg', shard / f'f{i:04d}.svg', root=root)
        jobs.append((shard, batch))
    processes, futures = [], []
    stop = threading.Event()
    executor = ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix='svg-shell')
    try:
        # Admission is parent-owned. A launch/submit failure cannot leave an
        # untracked child or let a late task launch after cleanup has started.
        for shard, batch in jobs:
            proc = _start_shell(executable, shard, root=root)
            processes.append(proc)
            futures.append(executor.submit(_export, proc, batch, width, height, stop=stop))
        for future in as_completed(futures):
            future.result()
    finally:
        stop.set()
        cleanup_error = None
        try:
            for proc in processes:
                try:
                    _stop_shell(proc)
                except (OSError, subprocess.TimeoutExpired) as error:
                    # Attempt every sibling even if one cannot be reaped.
                    cleanup_error = cleanup_error or error
        finally:
            executor.shutdown(wait=True)
            for proc in processes:
                _close_shell(proc)
        if cleanup_error is not None:
            raise cleanup_error
    for shard, batch in jobs:
        for i in batch:
            fstxn.replace(shard / f'f{i:04d}.png', directory / f'f{i:04d}.png', root=root)
    return len(jobs)


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
    from ..cache.policy import graphics_root
    cache = graphics_root(c)
    total = warm = 0
    for track in c.tl['video_tracks']:
        if track.get('kind') != 'gfx':
            continue
        for clip in track['clips']:
            total += 1
            record = gfx_svg.resolve(c, clip)
            key = gfx_svg.record_key(record)
            directory = cache / 'gfx_exp' / gfx_svg.NAMESPACE / key
            if c.dry:
                print(f'  [dry-run] SVG gfx {clip["src"]}[{key}]: {clip["dur"]} frames')
                continue
            from .frame_source import PngSequence
            source = PngSequence(directory, str(directory.relative_to(cache)),
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
                    batch_count = run_shell_batches(record['engine']['executable'], staging,
                                                    clip['dur'], c.w, c.h,
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
                    def check_batch(indices):
                        checked = []
                        for i in indices:
                            observed = []
                            reason = png_invalid_reason(staging / f'f{i:04d}.png',
                                                        width=c.w, height=c.h, _fingerprint=observed)
                            if reason:
                                raise ValueError(f'Inkscape output frame {i} invalid: {reason}')
                            if i in reused and observed[0] != copied_fingerprint:
                                raise ValueError('SVG reused PNG changed after materialization')
                            checked.append((i, observed[0]))
                        return checked
                    # Decode/CRC/SHA every PNG as before. Independent streams
                    # share only immutable inputs; at most one batch per shell
                    # runs, and all validation finishes before any publication.
                    hashes = [None] * clip['dur']
                    with ThreadPoolExecutor(max_workers=batch_count) as executor:
                        batches = [range(n, clip['dur'], batch_count) for n in range(batch_count)]
                        for checked in executor.map(check_batch, batches):
                            for i, fingerprint in checked:
                                hashes[i] = fingerprint
                    c.verify_inputs('gfx')
                    if gfx_svg.resolve(c, clip) != record:
                        raise ValueError('SVG identity changed during rendering')
                    for i in range(clip['dur']):
                        fstxn.replace(staging / f'f{i:04d}.png', directory / f'f{i:04d}.png', root=c.root)
                    marker.write_text(json.dumps({'format': 'framivra-svg-completion/v1',
                        'key': key, 'input': record, 'sha256': hashes,
                        'experimental': True, 'batch_count': batch_count, 'raster_seconds': raster_seconds,
                        'export_count': len(indices), 'materialized_count': len(skipped)}, sort_keys=True), encoding='utf-8')
            manifest_record(c, directory, 'gfx', key)
    c.receipt('gfx', True, f'{total} SVG gfx clips; {warm} cache hits; Inkscape static sequence',
              extra={'svg_producer': gfx_svg.PRODUCER})
