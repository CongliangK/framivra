"""Private generation proof for fixed formal video handoffs.

Media completion certifies bytes and frame count, not the composition code
that produced them. Formal stages need both the current receipt and this proof.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import stat
import subprocess
from time import monotonic

from ..runtime import fstxn

_FILES = {'video': 'video_noaudio.mp4', 'mux': 'final.mp4'}
_SUFFIX = '.formal-video.json'
_EPOCH = 'display-geometry-v2'
_LIMIT = 1024
_GEOMETRY_BUDGET = 300
_PRODUCER = ContextVar('formal_video_producer', default=None)


@contextmanager
def producer_scope(c, stage):
    if stage not in _FILES or not _enabled(c):
        yield
        return
    if not getattr(c, 'dry', False) and not _geometry_ready(c):
        raise RuntimeError('当前视频源缺少有效几何证明，不能发布正式视频')
    token = _PRODUCER.set((c, stage))
    try:
        yield
    finally:
        _PRODUCER.reset(token)


def _enabled(c):
    tl = getattr(c, 'tl', None)
    return (isinstance(tl, dict) and any(isinstance(track, dict) and
            track.get('kind') != 'gfx' and track.get('clips')
            for track in tl.get('video_tracks', ())))


def _geometry_ready(c):
    return getattr(c, '_formal_geometry_input', None) == c.input_fingerprint


def require_geometry(c):
    """Witness the geometry used by the formal compositor, once per operation."""
    if getattr(c, 'dry', False) or not _enabled(c) or _geometry_ready(c):
        return
    from ..media._display_witness import verify_source
    from ..media.encoders import display_video_size
    from .cache_state import verify_input_identity

    verify_input_identity(c)
    deadline = monotonic() + _GEOMETRY_BUDGET
    paths = {}
    try:
        for track in c.tl['video_tracks']:
            if track.get('kind') != 'gfx':
                for clip in track['clips']:
                    paths[c.src_path(clip['src'])] = None
    except (KeyError, TypeError, AttributeError, StopIteration) as error:
        raise RuntimeError('无法确认正式视频源：视频片段须引用已声明的素材，请先运行 vtx validate') from error
    for path in paths:
        try:
            info = c.probe(path)
            if info.get('has_video') is not True:
                raise ValueError('video track source has no video stream')
            verify_source(c, path, *display_video_size(info), deadline=deadline)
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
            raise RuntimeError(f'无法确认正式视频源 {path.name!r} 的解码几何；'
                               '请检查流内旋转、尺寸变化或解码超时') from error
    verify_input_identity(c)
    c._formal_geometry_input = c.input_fingerprint


def proof_name(name):
    return name + _SUFFIX if name in _FILES.values() else None


def _record(c, target, digest):
    return {'epoch': _EPOCH, 'input': c.input_fingerprint,
            'target': target.name, 'sha256': digest}


def publish(c, target, certified, *, write=None):
    """Called under the producer's target lock, after its media certificate."""
    active = _PRODUCER.get()
    if active is None or active[0] is not c:
        return
    logical = c.out / _FILES[active[1]]
    if write is None:
        from ..pipeline.infra import ensure_inside_project
        artifact = ensure_inside_project(logical, c.root, what='formal video output')
    else:
        artifact = logical
    if Path(target) != artifact:
        return
    payload = json.dumps(_record(c, logical, certified['sha256']), sort_keys=True)
    if write is None:
        fstxn.atomic_write(logical.with_name(logical.name + _SUFFIX), payload, root=c.root)
    else:
        write(logical.name + _SUFFIX, payload.encode('utf-8'))


def _read_record(path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > _LIMIT:
        raise ValueError('invalid formal video record')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as error:
        raise ValueError('formal video record disappeared during opening') from error
    with os.fdopen(fd, 'rb') as stream:
        current = os.fstat(stream.fileno())
        if not stat.S_ISREG(current.st_mode) or not os.path.samestat(before, current):
            raise ValueError('formal video record changed during opening')
        raw = stream.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise ValueError('formal video record is too large')
    return json.loads(raw)


def _unaffected_legacy_sources(c):
    """Only after current geometry, receipt and certified legacy bytes."""
    from .cache_state import verify_input_identity

    names = dict.fromkeys(clip['src'] for track in c.tl['video_tracks']
                         if track.get('kind') != 'gfx' for clip in track['clips'])
    try:
        for sid in names:
            info = c.probe(c.src_path(sid))
            if info.get('has_video') is not True:
                return False
            if info.get('quarter_turn', False) is not False:
                return False
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return False
    verify_input_identity(c)
    return True


def missing_prerequisite(c, stage):
    """Return the predecessor requiring rerender, or None for a valid handoff."""
    prerequisite = {'mux': 'video', 'qc': 'mux'}.get(stage)
    if prerequisite is None or not _enabled(c):
        return None
    if not _geometry_ready(c):
        return prerequisite
    from .recovery import _digest, completion_marker

    target = c.out / _FILES[prerequisite]
    proof = target.with_name(target.name + _SUFFIX)
    native = getattr(c, '_windows_svg_media', None)
    absent = object()
    try:
        if native is not None:
            try:
                with native.target_parent(proof, create=False) as (parent, name):
                    record = json.loads(parent.read(name, _LIMIT))
            except FileNotFoundError:
                record = absent
            with native.certified_file(c, target, c.dur) as digest:
                valid = record == _record(c, target, digest)
        else:
            from ..pipeline.infra import ensure_inside_project
            ensure_inside_project(proof, c.root, what='formal video proof')
            try:
                record = _read_record(proof)
            except FileNotFoundError:
                record = absent
            artifact = ensure_inside_project(target, c.root, what='formal video handoff')
            marker = completion_marker(artifact)
            ensure_inside_project(marker, c.root, what='media certificate')
            cert = _read_record(marker)
            digest = cert['sha256']
            if (cert.get('schema') != 1 or cert.get('frames') != c.dur or digest != _digest(artifact)):
                return prerequisite
            valid = record == _record(c, target, digest)
        if valid or record is absent and _unaffected_legacy_sources(c):
            return None
        return prerequisite
    except (OSError, ValueError, TypeError, KeyError):
        return prerequisite
