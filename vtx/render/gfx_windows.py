"""Windows CDP loose-page production and validation on native NTFS."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import threading

from ..cache.hygiene import TMP, manifest_record
from ..runtime import events, fstxn
from . import gfx_cli
from .gfx_svg_win_publish import anchored_relative

_LIMIT = 32 * 1024 * 1024  # per PNG or completion record


def _read(page, name):
    with page.open_read(name) as (handle, size):
        if not 0 < size <= _LIMIT:
            return b''  # An ordinary but invalid cache file is repairable.
        return b''.join(page.chunks(handle, size, limit=_LIMIT))


def _frame(page, name, width, height):
    data = _read(page, name)
    hashes = []
    reason = gfx_cli._png_stream_invalid_reason(io.BytesIO(data), len(data), width=width,
                                                height=height, _fingerprint=hashes)
    return reason, hashes[0] if hashes else None, data


def _current(page, n, key, width, height, namespace, environment):
    try:
        payload = _read(page, gfx_cli.COMPLETE_FILE)
    except FileNotFoundError:
        return False
    try:
        record = json.loads(payload)
        gfx_cli.verify_page_record(record, n, key, width, height, backend_ns=namespace,
                                   environment=environment)
    except (ValueError, RecursionError):
        return False
    for index, expected in enumerate(record['sha256']):
        try:
            reason, sha, _ = _frame(page, f'f{index:04d}.png', width, height)
        except FileNotFoundError:
            return False
        if reason or sha != expected:
            return False
    return True


def require_interval(c, directory, count, start, stop, *, namespace, environment):
    """Verify the requested loose PNGs in the exact NTFS page, without writes.

    This is a point-in-time integrity check, not a handle handoff to FFmpeg.
    """
    with ExitStack() as readers:
        page = readers.enter_context(anchored_relative(c.root))
        for name in directory.relative_to(c.root).parts:
            page = readers.enter_context(page.child(name, create=False))
        payload = _read(page, gfx_cli.COMPLETE_FILE)
        try:
            record = json.loads(payload)
        except (ValueError, RecursionError) as error:
            raise ValueError('invalid Windows CDP completion record') from error
        gfx_cli.verify_page_record(record, count, directory.name, c.w, c.h,
                                   backend_ns=namespace, environment=environment)
        for index in range(start, stop):
            reason, sha, _ = _frame(page, f'f{index:04d}.png', c.w, c.h)
            if reason or sha != record['sha256'][index]:
                raise ValueError(f'Windows CDP frame {index:04d} failed integrity check')


def _check_cancel(cancel):
    if cancel.is_set():
        raise gfx_cli.GfxCliError('Windows CDP rendering cancelled')


def _produce(c, page, scratch_parent, scratch_root, html, count, key, namespace, environment, render, cancel):
    name = 'gfx-cdp-' + secrets.token_hex(16)
    scratch = scratch_root / name
    identity = None
    try:
        _check_cancel(cancel)
        with scratch_parent.new_directory(name, movable=False) as stage:
            identity = fstxn.cleanup_identity(scratch)
            if identity is None:
                raise gfx_cli.GfxCliError('Windows gfx scratch identity unavailable')
            via = render(html, scratch, count)
            c.verify_inputs('gfx')
            _check_cancel(cancel)
            # Never certify a partial page or mix frames from multiple producers.
            page.remove(gfx_cli.COMPLETE_FILE)
            page.remove(gfx_cli.ENV_PENDING_FILE)
            digests = []
            for index in range(count):
                _check_cancel(cancel)
                frame = f'f{index:04d}.png'
                reason, sha, data = _frame(stage, frame, c.w, c.h)
                if reason:
                    raise gfx_cli.GfxCliError(f'{frame}: {reason}')
                page.publish(frame, data, while_readers=True, temp_prefix='.frame-')
                digests.append(sha)
    finally:
        # The browser never receives this path. Only drained Python workers
        # can write here; an unsettled browser's own profile is kept separately.
        if identity is not None:
            fstxn.remove_pinned(scratch, identity, preserve=lambda _: False)
    if fstxn.cleanup_identity(scratch) is not None:
        raise gfx_cli.GfxCliError('Windows gfx scratch cleanup incomplete')
    record = dict(gfx_cli._page_metadata(count, key, c.w, c.h, namespace),
                  sha256=digests, environment=environment)
    payload = json.dumps(record, sort_keys=True).encode('utf-8')
    if len(payload) > _LIMIT:
        raise gfx_cli.GfxCliError('Windows gfx completion record exceeds its byte limit')

    def before_commit():
        c.verify_inputs('gfx')
        _check_cancel(cancel)

    with ExitStack() as readers:
        for index, expected in enumerate(digests):
            _check_cancel(cancel)
            handle, size = readers.enter_context(page.open_read(f'f{index:04d}.png'))
            digest = hashlib.sha256()
            for chunk in page.chunks(handle, size, limit=_LIMIT):
                digest.update(chunk)
            if digest.hexdigest() != expected:
                raise gfx_cli.GfxCliError('Windows gfx frame changed before page completion')
        page.publish(gfx_cli.COMPLETE_FILE, payload, replace=False, temp_prefix='.page-',
                     before_replace=before_commit)
    return via


def render_stage(c, namespace, environment, *, directory_for, cdp_via):
    from .gfx_render import render_page
    clips = [clip for track in c.tl['video_tracks'] if track.get('kind') == 'gfx' for clip in track['clips']]
    binary, = environment['axes']['browser']
    jobs = {}
    for clip in clips:
        directory = directory_for(c, clip)
        if directory not in jobs:
            task = events.task(c, 'gfx', kind='gfx_block', metadata={
                'page_id': str(events.uuid.uuid4()), 'backend': 'cdp', 'frame_range': [0, clip['dur']]})
            jobs[directory] = clip, task
    if c.dry:
        for directory, (clip, _) in jobs.items():
            count = clip['dur']
            try:
                with ExitStack() as pins:
                    page = pins.enter_context(anchored_relative(c.root))
                    for name in directory.relative_to(c.root).parts:
                        page = pins.enter_context(page.child(name, create=False))
                    current = _current(page, count, directory.name, c.w, c.h, namespace, environment)
            except FileNotFoundError:
                current = False
            title = Path(clip['src']).stem
            if current:
                print(f'✓ {title}[{directory.name}]: {count} 帧已缓存')
            else:
                print(f'  [dry-run] gfx {title}[{directory.name}]: 将渲染 {count} 帧（CDP {c.w}×{c.h}）')
        return

    cancel = getattr(c, '_windows_cdp_cancel', None) or threading.Event()
    scratch_root = c.cache / TMP

    def operation(directory, clip, task):
        with task.scope():
            return render_one(directory, clip, task)

    def render_one(directory, clip, task):
        _check_cancel(cancel)
        key = directory.name
        title, count = Path(clip['src']).stem, clip['dur']
        with ExitStack() as pins:
            project = pins.enter_context(anchored_relative(c.root))
            page = project
            for name in directory.relative_to(c.root).parts:
                page = pins.enter_context(page.child(name, create=True))
            pins.enter_context(page.lock(gfx_cli.COMPLETE_FILE, check_cancel=lambda: _check_cancel(cancel)))
            _check_cancel(cancel)
            if _current(page, count, key, c.w, c.h, namespace, environment):
                c.verify_inputs('gfx')
                _check_cancel(cancel)
                events.cache_hit(c, 'inspect_page', frames=[0, count])
                print(f'✓ {title}[{key}]: {count} 帧已缓存')
                return None

            def render(html, scratch, n):
                stats = task.attempt(render_page, html, scratch, n, chrome_path=binary,
                                     width=c.w, height=c.h, ctx=c, cancel=cancel)
                if stats['failed']:
                    raise gfx_cli.GfxCliError(f'Windows CDP failed on {len(stats["failed"])} frame(s); no CLI fallback')
                return cdp_via(stats)

            scratch_parent = project
            for name in scratch_root.relative_to(c.root).parts:
                scratch_parent = pins.enter_context(scratch_parent.child(name, create=True))
            via = _produce(c, page, scratch_parent, scratch_root, (c.root / clip['src']).resolve(),
                           count, key, namespace, environment, render, cancel)
            print(f'✓ {title}[{key}]: 渲染 {count} 帧（含 alpha，{via}）')
            manifest_record(c, directory, 'gfx', key)
            return f'{title}: {via}'

    workers = max(1, int(os.environ.get('VTX_GFX_PAGES', '7')))
    with ThreadPoolExecutor(min(workers, len(jobs))) as pool:
        futures = []
        try:
            for directory, (clip, task) in jobs.items():
                futures.append(pool.submit(operation, directory, clip, task))
            pending = set(futures)
            while pending:
                # An infinite lock wait can hide Ctrl-C on pre-3.14 Windows.
                done, pending = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()
        except BaseException:
            cancel.set()
            for future in futures:
                future.cancel()
            raise
    _check_cancel(cancel)
    notes = [note for future in futures if (note := future.result())]
    c.receipt('gfx', True, f'{len(clips)} gfx clips' + ('｜' + '; '.join(notes) if notes else ''))
