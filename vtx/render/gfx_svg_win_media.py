"""Media execution/publication for the owned Windows SVG composition lane."""
from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import time
from types import SimpleNamespace

from . import gfx_svg_win_shell as shell
from .gfx_svg_win_input import relative_parts
from .gfx_svg_win_publish import anchored
from .gfx_svg_win_snapshot import _local_path


_TOOL_LIMIT = 256 * 1024 * 1024
_MEDIA_LIMIT = 512 * 1024 * 1024
_MARKER_LIMIT = 16 * 1024
_STANDARD_TIMELINE = 'a7008c2e5b2833da13021c3a1ccb46a09068ca1775b6724328563d2d37f7d105'
_STANDARD_INPUTS = 'ee88e5d4be9fb74ade319eda4fa868866de6e5849be338147aec7b1c9ba0f8fe'
# Gyan 9.0.2 full static build exercised by the Windows reference run.
_STANDARD_TOOLS = {
    'ffmpeg': '589e50b766d251afdf181dd664d40bd94407e200b019989fd7468c7d118a28d0',
    'ffprobe': '02264dfa4f56baaa31e443ece8aa16d397f6b008200e7fd184d22b9009e75fd3',
}


def require_standard_timeline(sha256):
    if sha256 != _STANDARD_TIMELINE:
        raise ValueError('Windows SVG all requires the exact portable-v2 timeline')


def bind_standard_audio(pins, project):
    """Hash the two independent input WAVs and deny write/delete until all settles."""
    lock = shell._read_demo_lock('benchmark/portable-v2-input-lock.json')
    if hashlib.sha256(lock).hexdigest() != _STANDARD_INPUTS:
        raise ValueError('Windows SVG portable-v2 input lock mismatch')
    parent = pins.enter_context(anchored(project))
    for part in ('assets', 'audio'):
        parent = pins.enter_context(parent.child(part, create=False))
    digests = {}
    for name, facts in json.loads(lock)['audio'].items():
        handle, size = pins.enter_context(parent.open_read(name.rsplit('/', 1)[-1]))
        if size != facts['size'] or WindowsSvgMedia._digest(
                parent.chunks(handle, size, limit=facts['size'])) != facts['sha256']:
            raise ValueError('Windows SVG portable-v2 audio byte identity mismatch')
        digests[name] = facts['sha256']
    return digests


class WindowsSvgMedia:
    """Explicitly pinned tools, private Job writers, and handle-relative commit."""
    def __init__(self, pins, project, scratch, operation, tool_dir, expected, *, ctx=None):
        self.project, self.scratch, self.operation = project, scratch, operation
        self.ctx = ctx
        self.published = {}
        tool_dir = _local_path(tool_dir)
        tools = pins.enter_context(anchored(tool_dir))
        self.tools = {}
        for name in ('ffmpeg', 'ffprobe'):
            leaf = name + '.exe'
            handle, size = pins.enter_context(tools.open_read(leaf))
            if self._digest(tools.chunks(handle, size, limit=_TOOL_LIMIT)) != expected[name]:
                raise ValueError(f'Windows SVG {name} byte identity mismatch')
            self.tools[name] = str(tool_dir / leaf)
        self.identity = {'profile': 'windows-svg-media/v1', 'sha256': dict(expected)}
        system = shell._system_windows_path()
        env = {'SystemRoot': str(system), 'WINDIR': str(system), 'SYSTEMDRIVE': system.drive,
               'PATH': str(system / 'System32'), 'HOME': str(scratch),
               'TEMP': str(scratch), 'TMP': str(scratch), 'LANG': 'C', 'TZ': 'UTC'}
        self.environment = ctypes.create_unicode_buffer('\0'.join(
            f'{name}={env[name]}' for name in sorted(env, key=str.casefold)) + '\0')
        self.directory = pins.enter_context(anchored(scratch))

    @staticmethod
    def _digest(chunks):
        h = hashlib.sha256()
        for part in chunks:
            h.update(part)
        return h.hexdigest()

    @contextmanager
    def command_inputs(self, cmd):
        # Private PNG/concat paths have their own scopes. Hold direct project
        # media through the child run, not merely while hashing it in Python.
        with ExitStack() as pins:
            if self.ctx is not None:
                if cmd[0] == 'ffprobe':
                    inputs = (cmd[-1],)
                else:
                    inputs = (cmd[i+1] for i, option in enumerate(cmd[:-1]) if option == '-i')
                for item in inputs:
                    path = Path(item)
                    if path.is_absolute() and path.is_relative_to(self.project):
                        rel = path.relative_to(self.project).as_posix()
                        if rel not in self.ctx._svg_audio_digests:
                            pins.enter_context(self.certified_file(self.ctx, path))
            yield

    def execute(self, cmd, *, text=True):
        if self.operation.keep:
            raise ValueError('Windows SVG media operation has an unconfirmed writer')
        argv = [self.tools[cmd[0]], *map(str, cmd[1:])]
        # A sibling worker's successful close must never clear another worker's
        # unresolved ownership. Only propagate the sticky failure back upward.
        state = SimpleNamespace(keep=False)
        try:
            with self.command_inputs(cmd):
                job, _ = shell._run_owned(state, argv, self.scratch, self.environment, None,
                                          deadline=time.monotonic() + 600, capture_limit=16 * 1024 * 1024,
                                          phase='media tool')
        finally:
            if state.keep:
                self.operation.keep = True
        stdout, stderr = job._output()
        if text:
            stdout, stderr = (stream.decode('utf-8', 'replace') for stream in (stdout, stderr))
        return subprocess.CompletedProcess(cmd, job.proc.returncode, stdout, stderr)

    @contextmanager
    def target_parent(self, target, *, create):
        relative = Path(target).relative_to(self.project).as_posix()
        parts = relative_parts(relative)
        if len(parts) < 2 or parts[0] not in ('cache', 'out'):
            raise ValueError('Windows SVG media target must be under cache or out')
        with ExitStack() as pins:
            parent = pins.enter_context(anchored(self.project))
            for part in parts[:-1]:
                parent = pins.enter_context(parent.child(part, create=create))
            yield parent, parts[-1]

    def rotate_history(self, target, keep):
        # The caller already holds final's writer lock. Copy instead of linking
        # so the old generation does not lose single-leaf publication eligibility.
        with self.target_parent(target, create=False) as (parent, name), ExitStack() as pins:
            try:
                handle, size = pins.enter_context(parent.open_read(name))
            except FileNotFoundError:
                return
            folder = pins.enter_context(parent.child('history', create=True))
            from ..cache.hygiene import _md5_file
            stamp = time.strftime('%Y%m%d-%H%M%S')
            leaf = f'{target.stem}_{stamp}_{_md5_file(target)[:8]}{target.suffix}'
            folder.publish_chunks(leaf, parent.chunks(handle, size, limit=_MEDIA_LIMIT))
            old = sorted(p.name for p in (target.parent / 'history').iterdir()
                         if p.name.startswith(target.stem + '_') and p.suffix == target.suffix)
            for leaf in old[:max(0, len(old) - max(keep, 0))]:
                folder.remove(leaf)

    def write_aux(self, target, data):
        with self.target_parent(target, create=True) as (parent, name):
            parent.publish(name, data.encode('utf-8') if isinstance(data, str) else data)

    @contextmanager
    def concat_input(self, c, paths, manifest):
        leaf = 'concat-' + secrets.token_hex(12) + '.txt'
        target = self.scratch / leaf
        data = manifest.encode('utf-8')
        with ExitStack() as pins:
            for path, frames in paths:
                pins.enter_context(self.certified_file(c, path, frames))
            with target.open('xb') as stream:
                stream.write(data)
            handle, size = pins.enter_context(self.directory.open_read(leaf))
            if size != len(data) or b''.join(self.directory.chunks(handle, size, limit=len(data))) != data:
                raise ValueError('Windows SVG private concat manifest changed')
            yield target
        self.directory.remove(leaf)

    @contextmanager
    def certified_file(self, c, target, frames=None, *, allow_short=False):
        from ..cache.recovery import matches_frame_count

        with self.target_parent(target, create=False) as (parent, name):
            rec = json.loads(parent.read(name + '.complete.json', _MARKER_LIMIT))
            if (not isinstance(rec, dict) or rec.get('schema') != 3
                    or rec.get('target') != Path(target).relative_to(self.project).as_posix()
                    or rec.get('input') != c.input_fingerprint or rec.get('tool') != self.identity
                    or not matches_frame_count(rec.get('frames'), frames, allow_short)):
                raise ValueError('Windows SVG media certificate does not match this operation')
            with parent.open_read(name) as (handle, size):
                if rec.get('sha256') != self._digest(parent.chunks(handle, size, limit=_MEDIA_LIMIT)):
                    raise ValueError('Windows SVG media bytes do not match the certificate')
                yield rec['sha256']

    @contextmanager
    def qc_scope(self, c):
        paths = (c.out / 'final.mp4', c.cache / 'qc_clean.mp4')
        with ExitStack() as pins:
            hashes = {}
            for path in paths:
                sha = pins.enter_context(self.certified_file(c, path, c.dur))
                if sha != self.published.get(path):
                    raise ValueError('Windows SVG QC input differs from the movie published in this run')
                hashes[path.name] = sha
            self.qc_identity = hashes
            try:
                yield
            finally:
                self.qc_identity = None

    def update_ledger(self, path, key, mutate, dump):
        with self.target_parent(path, create=True) as (parent, name), parent.lock(name):
            try:
                old = json.loads(parent.read(name, 8 * 1024 * 1024))
            except FileNotFoundError:
                old = {key: []}
            if not isinstance(old, dict) or not isinstance(old.get(key), list):
                raise ValueError(f'Windows SVG ledger {name} has an invalid shape')
            new = mutate(old)
            if new is not None:
                parent.publish(name, dump(new).encode('utf-8'))
            return new

    def ensure_dir(self, path):
        with self.target_parent(path / 'unused', create=True):
            pass

    def valid(self, c, target, frames=None, *, allow_short=False):
        try:
            with self.certified_file(c, target, frames, allow_short=allow_short):
                return True
        except (FileNotFoundError, ValueError):
            return False

    def publish(self, c, target, producer, *, max_frames, before_commit, after_commit):
        from ..cache.cache_state import verify_input_identity
        from ..cache.recovery import _inspect

        suffix = target.suffix.lower()
        with self.target_parent(target, create=True) as (parent, name), parent.lock(name):
            parent._check_existing(name)
            parent._check_existing(name + '.complete.json')
            # FFmpeg may write only a fresh private file, never a project path.
            # The operation owner removes the enclosing scratch only after Job settlement.
            leaf = 'encode-' + secrets.token_hex(12) + suffix
            staged = self.scratch / leaf
            result = producer(staged)
            if self.operation.keep:
                raise ValueError('Windows SVG media writer settlement unconfirmed')
            with self.directory.open_read(leaf) as (handle, size):
                if not 0 < size <= _MEDIA_LIMIT:
                    raise ValueError('Windows SVG media output exceeds its byte budget or is empty')
                rec = _inspect(staged, suffix, execute=self.execute)
                if rec is None or (max_frames is not None and rec['frames'] is not None and rec['frames'] > max_frames):
                    raise RuntimeError('Windows SVG media output failed full inspection')
                verify_input_identity(c)
                if before_commit is not None:
                    before_commit()
                    verify_input_identity(c)

                def verified_chunks():
                    h = hashlib.sha256()
                    for part in self.directory.chunks(handle, size, limit=_MEDIA_LIMIT):
                        h.update(part)
                        yield part
                    if h.hexdigest() != rec['sha256']:
                        raise ValueError('Windows SVG media bytes differ from inspected output')

                parent.publish_chunks(name, verified_chunks())
                rec.update(schema=3, input=c.input_fingerprint, tool=self.identity,
                           target=target.relative_to(self.project).as_posix())
                parent.publish(name + '.complete.json', json.dumps(rec, sort_keys=True).encode('utf-8'))
                self.published[target] = rec['sha256']
            if after_commit is not None:
                after_commit()
            self.directory.remove(leaf)
            return result
