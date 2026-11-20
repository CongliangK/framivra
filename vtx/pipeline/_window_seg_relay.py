"""One in-memory clip pre-encode slot owned by the private operator run."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from ..cache.recovery import completion_marker, media_output
from ..runtime import fstxn

_MAX_BYTES = 256 * 1024  # media and original marker together
_MAX_MARKER = 512
_SHA = re.compile('[0-9a-f]{64}')


def _file_version(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_size <= 0):
        raise ValueError('segment relay requires nonempty single-link private files')
    return fstxn.entry_identity(info), info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _leaf_version(parent, name):
    return _file_version(os.stat(name, dir_fd=parent, follow_symlinks=False))


@contextmanager
def _read_leaf(parent, name):
    version = _leaf_version(parent, name)
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
    with os.fdopen(fd, 'rb') as stream:
        def guard():
            if (_file_version(os.fstat(stream.fileno())) != version
                    or _leaf_version(parent, name) != version):
                raise ValueError('segment relay artifact changed during transfer')

        def read():
            data = stream.read(version[2] + 1)
            if len(data) != version[2]:
                raise ValueError('segment relay artifact length changed during read')
            return data

        guard()
        yield read, version[2]
        guard()


def _require_new(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError('segment relay target already exists')


def _write_new(parent, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        version = _file_version(os.fstat(stream.fileno()))
        if version[2] != len(data) or version != _leaf_version(parent, name):
            raise ValueError('segment relay destination changed during write')
        return version


def _completion(marker, frames):
    rec = json.loads(marker)
    if (not isinstance(rec, dict) or set(rec) != {'schema', 'frames', 'sha256'}
            or type(rec['schema']) is not int or rec['schema'] != 1
            or type(rec['frames']) is not int or not 0 < rec['frames'] <= frames
            or not isinstance(rec['sha256'], str) or not _SHA.fullmatch(rec['sha256'])
            or json.dumps(rec, sort_keys=True).encode('ascii') != marker):
        raise ValueError('segment relay requires the original producer completion marker')
    return rec


def _clock_and_pin(c):
    return c.frame_rate, c.fps, c.input_fingerprint


class _RunSegmentRelay:
    """No external input or return value is a source of relay bytes."""

    def __init__(self, c, check):
        self._baseline = _clock_and_pin(c)
        self._check = check
        self.clear()

    def clear(self):
        self._entry = self._window = self._stage = None
        self._pending = {}

    def begin_window(self, workspace, identity, window, following):
        if self._window is not None:
            raise ValueError('segment relay window did not settle')
        self._window = workspace, identity, window, following

    def wants_next(self, cl):
        following = self._window[3]
        return following is not None and cl['dst_in'] < following[1] and following[0] < cl['dst_in'] + cl['dur']

    def _guard_stage(self):
        self._check()
        c, folder, identity = self._stage
        if _clock_and_pin(c) != self._baseline or fstxn.cleanup_identity(folder) != identity:
            raise ValueError('segment relay clock, input fingerprint or stage identity changed')

    @contextmanager
    def _target(self, target):
        self._guard_stage()
        c, folder, identity = self._stage
        with fstxn.pinned_parent(target, create=False, root=c.root) as (parent, name, canonical):
            if (canonical != target or target.parent != folder
                    or fstxn.entry_identity(os.fstat(parent)) != identity[-1]):
                raise ValueError('segment relay target parent changed')
            yield parent, name, completion_marker(target).name
            self._guard_stage()

    def prepare(self, c, tasks, producers):
        """Import only after prepare_layers has checked every producer collision."""
        workspace, workspace_id, window, _ = self._window
        folder = c.cache / 'seg'
        identity = fstxn.cleanup_identity(folder)
        if (self._stage is not None or c.range_window != window or c.cache != workspace / 'work'
                or not identity or identity[:len(workspace_id)] != workspace_id or identity[-1][2] != stat.S_IFDIR):
            raise ValueError('unexpected segment relay stage')
        self._stage = c, folder, identity
        self._guard_stage()
        hit = False
        cold = []
        for task in tasks:
            _, cmd, _ = task
            guarded = c._preview_ffmpeg_args(cmd)
            target = media_output(c, guarded)
            if target != Path(cmd[-1]):
                raise ValueError('unexpected segment relay output target')
            producer = producers[target]
            spec = tuple(guarded[:-1]), producer['frames'], producer['eligible']
            with self._target(target) as (parent, name, marker_name):
                _require_new(parent, name)
                _require_new(parent, marker_name)
                if self._entry is not None and name == self._entry[0]:
                    _, expected, data, marker = self._entry
                    if spec != expected:
                        raise ValueError('conflicting segment relay producer specification or eligibility')
                    rec = _completion(marker, spec[1])
                    if rec['frames'] != spec[1] or rec['sha256'] != hashlib.sha256(data).hexdigest():
                        raise ValueError('segment relay hit bytes or frame count changed')
                    c._preview_media_target(target)  # same job-wide budget as Ctx.run
                    mv = _write_new(parent, name, data)
                    cv = _write_new(parent, marker_name, marker)
                    if _leaf_version(parent, name) != mv or _leaf_version(parent, marker_name) != cv:
                        raise ValueError('segment relay published files changed')
                    hit = True
                    if not producer.get('relay_next'):
                        self._entry = None
                    continue
            cold.append(task)
            if producer['eligible'] and producer.get('relay_next'):
                self._pending[target] = spec
        if not hit:
            self._entry = None
        return cold

    def capture(self, c, cmd):
        """Get a callback for the trusted atomic_ffmpeg path, not its return value."""
        if self._stage is None or c is not self._stage[0] or self._entry is not None:
            return None
        target = Path(cmd[-1])
        spec = self._pending.pop(target, None)
        if spec is None:
            return None
        if tuple(cmd[:-1]) != spec[0]:
            raise ValueError('segment relay encoding command changed')
        with self._target(target) as (parent, name, marker_name):
            _require_new(parent, name)
            _require_new(parent, marker_name)

        def committed():
            with self._target(target) as (parent, name, marker_name):
                with _read_leaf(parent, name) as (read_media, size), _read_leaf(parent, marker_name) as (read_marker, msize):
                    if msize > _MAX_MARKER:
                        raise ValueError('segment relay producer marker is oversized')
                    marker = read_marker()
                    rec = _completion(marker, spec[1])
                    if size + msize > _MAX_BYTES or rec['frames'] != spec[1]:
                        return  # valid ordinary production, not relayable
                    data = read_media()
                    if rec['sha256'] != hashlib.sha256(data).hexdigest():
                        raise ValueError('segment relay producer payload digest mismatch')
            self._entry = name, spec, data, marker

        return committed

    def settle_window(self):
        if self._stage is None:
            self.clear()  # a result that did not reach the actual stage grants no authority
        else:
            self._guard_stage()
            self._window = self._stage = None
            self._pending.clear()
