"""Media execution/publication for the owned Windows SVG composition lane."""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from types import SimpleNamespace

from . import gfx_svg_win_shell as shell
from .gfx_svg_win_input import relative_parts
from .gfx_svg_win_publish import anchored
from .gfx_svg_win_snapshot import _local_path


_TOOL_LIMIT = 256 * 1024 * 1024
_MEDIA_LIMIT = 512 * 1024 * 1024
_SOURCE_LIMIT = 4 * 1024 * 1024 * 1024
_MAX_SOURCES = 256
# Force a single-file demuxer; a playlist renamed as a .mp4 must not be
# auto-probed as a playlist. MOV external data references stay disabled.
_SOURCE_FORMATS = {
    '.wav': 'wav', '.flac': 'flac', '.mp3': 'mp3', '.aac': 'aac',
    '.ogg': 'ogg', '.opus': 'ogg', '.m4a': 'mov', '.mp4': 'mov',
    '.m4v': 'mov', '.mov': 'mov', '.mkv': 'matroska', '.webm': 'matroska',
    '.avi': 'avi',
}
_MARKER_LIMIT = 16 * 1024
_PRIVATE_OUTPUT = ContextVar('windows_svg_private_output', default=None)
_AUDIO_GRAPH = ContextVar('windows_svg_audio_graph', default=None)
_STANDARD_TIMELINE = 'a7008c2e5b2833da13021c3a1ccb46a09068ca1775b6724328563d2d37f7d105'
# Raw LF bytes of the frozen input lock (the Git blob and source archives).
_STANDARD_INPUTS = 'b4a687a8cdaf3166b0f74f30015ef9c24ff8d1a27cdd9f491e5e4b78ea3d1bb0'
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


def bind_project_inputs(pins, c):
    """Fingerprint and hold exact original source names before any media probe."""
    sources = c.tl.get('sources', [])
    if not isinstance(sources, list) or len(sources) > _MAX_SOURCES:
        raise ValueError(f'Windows SVG supports at most {_MAX_SOURCES} local media declarations')
    rows = {}
    for source in sources:
        name = source['path']
        parts = relative_parts(name)
        if parts[0].lower() in ('cache', 'out') or Path(name).suffix.lower() not in _SOURCE_FORMATS:
            raise ValueError(f'Windows SVG requires a local, self-contained media source: {name!r}')
        rows[name] = parts
    if not rows:
        return {}
    root = pins.enter_context(anchored(c.root))
    directories = {(): root}
    result = {}
    for name, parts in rows.items():
        parent = root
        for i, part in enumerate(parts[:-1]):
            branch = tuple(parts[:i + 1])
            if branch not in directories:
                directories[branch] = pins.enter_context(parent.child(part, create=False))
            parent = directories[branch]
        handle, size = pins.enter_context(parent.open_read(parts[-1]))
        result[name] = WindowsSvgMedia._digest(parent.chunks(handle, size, limit=_SOURCE_LIMIT))
    return result


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
        self._env = {'SystemRoot': str(system), 'WINDIR': str(system), 'SYSTEMDRIVE': system.drive,
                     'PATH': str(system / 'System32'), 'HOME': str(scratch),
                     'TEMP': str(scratch), 'TMP': str(scratch), 'LANG': 'C', 'TZ': 'UTC'}
        self.environment = self.process_environment()
        self.directory = pins.enter_context(anchored(scratch))

    def process_environment(self, **extra):
        env = {**self._env, **extra}
        return ctypes.create_unicode_buffer('\0'.join(
            f'{name}={env[name]}' for name in sorted(env, key=str.casefold)) + '\0')

    @staticmethod
    def _digest(chunks):
        h = hashlib.sha256()
        for part in chunks:
            h.update(part)
        return h.hexdigest()

    @contextmanager
    def command_inputs(self, cmd):
        # Declared sources are already held for the entire operation. Derived
        # project media must have a current certificate and a new read pin.
        # Private sequences and concat lists are held by their callers.
        argv = [self.tools[cmd[0]], *map(str, cmd[1:])]
        with ExitStack() as pins:
            if self.ctx is not None:
                sources = getattr(self.ctx, '_svg_source_digests', None)
                locked_audio = getattr(self.ctx, '_svg_audio_digests', {})
                if cmd[0] == 'ffprobe':
                    inputs = ((len(cmd) - 1, len(cmd) - 1),)
                else:
                    inputs = ((i, i + 1) for i, option in enumerate(cmd[:-1]) if option == '-i')
                insertions = []
                for option, index in inputs:
                    item = os.fspath(cmd[index])
                    path = Path(item)
                    if path.is_absolute() and path.is_relative_to(self.project):
                        rel = path.relative_to(self.project).as_posix()
                        if sources is not None and rel in sources:
                            format_name = _SOURCE_FORMATS[path.suffix.lower()]
                            options = ['-protocol_whitelist', 'file,pipe', '-f', format_name]
                            if format_name == 'mov':
                                options += ['-enable_drefs', '0']
                            insertions.append((option, options))
                        elif rel not in locked_audio:
                            pins.enter_context(self.certified_file(self.ctx, path))
                    elif sources is not None:
                        private = path.is_absolute() and path.is_relative_to(self.scratch.parent.parent)
                        lavfi = cmd[0] == 'ffmpeg' and list(cmd[option-2:option]) == ['-f', 'lavfi']
                        if not (private or lavfi):
                            raise ValueError('Windows SVG media input is outside the owned input scope')
                for option, options in reversed(insertions):
                    argv[option:option] = options
            yield argv

    @contextmanager
    def audio_graph_scope(self):
        """Allow file-backed transport only for the kernel's generated mix graph."""
        token = _AUDIO_GRAPH.set(self)
        try:
            yield
        finally:
            _AUDIO_GRAPH.reset(token)

    def execute(self, cmd, *, text=True, capture_limit=16 * 1024 * 1024, deadline=None):
        if not 0 < capture_limit <= 64 * 1024 * 1024:
            raise ValueError('Windows SVG media capture exceeds its byte budget')
        if self.operation.keep:
            raise ValueError('Windows SVG media operation has an unconfirmed writer')
        # A sibling worker's successful close must never clear another worker's
        # unresolved ownership. Only propagate the sticky failure back upward.
        state = SimpleNamespace(keep=False)
        try:
            with self.command_inputs(cmd) as argv, ExitStack() as graph_inputs:
                if cmd[0] == 'ffmpeg' and _AUDIO_GRAPH.get() is self and '-filter_complex' in argv:
                    # Size the final argv: absolute tool, authorized input
                    # options, and the actual staged WAV are already present.
                    from ..pipeline.stages import _windows_audio_argument_units
                    if _windows_audio_argument_units(argv) > 32767:
                        index = argv.index('-filter_complex')
                        graph = argv[index + 1].encode('utf-8')
                        path = graph_inputs.enter_context(self.private_input(graph, '.graph'))
                        argv[index:index + 2] = ['-/filter_complex', str(path)]
                        if _windows_audio_argument_units(argv) > 32767:
                            graph_inputs.close()  # No child was launched; remove the private graph now.
                            raise RuntimeError('Windows SVG 混音输入参数仍超过进程命令行长度上限；'
                                               '请减少单次混音的音频事件或缩短工程路径。')
                from .gfx_svg_win_subtitles import job_environment
                now = time.monotonic()
                if deadline is None:
                    deadline = now + 600
                elif deadline <= now + shell._SETTLE_RESERVE:
                    raise TimeoutError('Windows SVG media exhausted its time budget before launch')
                job, _ = shell._run_owned(state, argv, self.scratch, job_environment(self, cmd), None,
                                          deadline=deadline, capture_limit=capture_limit, phase='media tool')
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
    def private_input(self, data, suffix):
        leaf = 'sample-' + secrets.token_hex(12) + suffix
        self.directory.write_new(leaf, data)
        with self.directory.open_read(leaf) as (handle, size):
            if b''.join(self.directory.chunks(handle, size, limit=len(data))) != data:
                raise ValueError('Windows SVG private sample differs from the certified bytes')
            yield self.scratch / leaf
        self.directory.remove(leaf)

    @contextmanager
    def private_reference(self):
        """Permit exactly one private QC reference output, never a project writer."""
        directory = Path(tempfile.mkdtemp(prefix='reference-', dir=self.scratch))
        with anchored(directory):
            token = _PRIVATE_OUTPUT.set((self, directory / 'reference.mp4'))
            try:
                yield directory
            finally:
                _PRIVATE_OUTPUT.reset(token)

    @contextmanager
    def qc_scope(self, c, *, standalone=False):
        from ..pipeline.composite import needs_qc_clean

        paths = [c.out / 'final.mp4']
        if needs_qc_clean(c):
            paths.append(c.cache / 'qc_clean.mp4')
        with ExitStack() as pins:
            hashes = {}
            for path in paths:
                sha = pins.enter_context(self.certified_file(c, path, c.dur))
                # A separate strict QC invocation has no in-process publication.
                # The all route must still match both publications from this run.
                if not standalone and sha != self.published.get(path):
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
        from ..cache._formal_video import publish as publish_formal_video

        suffix = target.suffix.lower()
        if _PRIVATE_OUTPUT.get() == (self, target):
            if before_commit is not None or after_commit is not None or target.exists():
                raise ValueError('Windows SVG private reference must be a fresh uncommitted output')
            result = producer(target)
            if self.operation.keep:
                raise ValueError('Windows SVG reference writer settlement unconfirmed')
            with anchored(target.parent) as parent, parent.open_read(target.name) as (_, size):
                if not 0 < size <= _MEDIA_LIMIT:
                    raise ValueError('Windows SVG private reference exceeds its byte budget or is empty')
                inspected = _inspect(target, suffix, execute=self.execute)
                if inspected is None or inspected['frames'] != 1:
                    raise RuntimeError('Windows SVG private reference requires one decoded frame')
                verify_input_identity(c)
            return result
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
            publish_formal_video(c, target, rec, write=parent.publish)
            self.directory.remove(leaf)
            return result
