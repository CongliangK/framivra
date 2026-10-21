"""Internal PNG FrameSource, without eager IO or a second rendering backend.

Intervals are clip-local and half-open. Random seeks and repeated consumers are
independent; no cursor is shared. FFmpeg still reads the original sequence to
EOF (the compositor owns trim, repeat-last and padding). Missing/truncated files
fail before consumption; decoder errors still propagate from FFmpeg. Recovery is
an explicit rerun of gfx, never fabricated frames or a successful receipt.
"""
import contextlib
import hashlib
import threading
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

from .hygiene import gfx_cache_ns, gfx_dir
from .gfx_cli import png_invalid_reason


class _PngConsumerScope:
    """Successful PNG facts owned only by one explicit video operation."""
    def __init__(self):
        self.active = True
        self.lock = threading.Lock()
        self.bindings = []

    def invalidate(self):
        with self.lock:
            self.active = False
            for binding in self.bindings:
                binding.facts.clear()


class _PngConsumerFacts:
    def __init__(self, scope):
        self.scope = scope
        self.source = None
        self.lock = threading.Lock()
        self.facts = {}
        scope.bindings.append(self)

    def bound_to(self, source):
        return self.source is source and self.scope.active

    def get(self, source, frame):
        with self.scope.lock:
            return self.facts.get(frame) if self.bound_to(source) else None

    def remember(self, source, frame, fingerprint, size):
        with self.scope.lock:
            if self.bound_to(source):
                self.facts[frame] = (fingerprint, size)


def _current_png_fingerprint(path, verified_size):
    """Read every current byte, bounded by the previously parsed size + 1."""
    if not path.is_file():
        raise ValueError(f'PNG source not ready: {path}; rerun vtx gfx (missing)')
    digest = hashlib.sha256()
    count = 0
    try:
        with open(path, 'rb') as stream:
            while count <= verified_size:
                data = stream.read(min(65536, verified_size + 1 - count))
                if not data:
                    break
                digest.update(data)
                count += len(data)
    except OSError as error:
        raise ValueError(f'PNG source not ready: {path}; rerun vtx gfx ({error})') from error
    return digest.hexdigest(), count


@contextlib.contextmanager
def png_consumer_scope(layers):
    """Bind fresh source views; preparation and escaped views gain no trust.

    This does not open an SVG input snapshot scope. All existing input,
    environment and completion checks still run on every consumer call.
    """
    from .gfx_svg import SequenceSnapshot
    scope = _PngConsumerScope()
    sources = {}
    scoped_layers = []
    try:
        for layer in layers:
            view = dict(layer)
            if layer['gfx']:
                source = layer['source']
                if id(source) not in sources:
                    binding = _PngConsumerFacts(scope)
                    changes = {'_png_facts': binding}
                    snapshot = getattr(source.interval_check, '__self__', None)
                    if (type(snapshot) is SequenceSnapshot
                            and getattr(source.interval_check, '__func__', None)
                            is SequenceSnapshot.check_interval
                            and snapshot.scope is not None):
                        # A caller may hand in a preview-scoped source. Video
                        # facts never inherit that scope's narrower input gate.
                        snapshot = SequenceSnapshot(snapshot.c, snapshot.clip,
                                                    snapshot.directory, snapshot.record)
                        changes['interval_check'] = snapshot.check_interval
                    bound = replace(source, **changes)
                    binding.source = bound
                    sources[id(source)] = bound
                view['source'] = sources[id(source)]
            scoped_layers.append(view)
        yield scoped_layers
    finally:
        scope.invalidate()


@dataclass(frozen=True)
class PngSequence:
    directory: Path
    identity: str
    frame_count: int
    fps: int
    width: int
    height: int
    storage_kind: str = "png_sequence"
    pixel_format: str = "unknown"
    alpha: str = "unknown"
    color_space: str = "unknown"
    environment_check: object = field(default=None, repr=False, compare=False)
    interval_check: object = field(default=None, repr=False, compare=False)
    _png_facts: object = field(default=None, repr=False, compare=False)

    @property
    def time_base(self):
        return Fraction(1, self.fps)

    def _interval(self, start, stop):
        if not (0 <= start < stop <= self.frame_count):
            raise ValueError(f"Invalid PNG interval [{start},{stop}) / {self.frame_count}")

    def frame_path(self, frame):
        self._interval(frame, frame + 1)
        return self.directory / f"f{frame:04d}.png"

    def require_ready(self, start=0, stop=None):
        stop = self.frame_count if stop is None else stop
        facts = self._png_facts
        if facts is not None and facts.bound_to(self):
            # Segment workers may request overlapping intervals. Only the first
            # successful consumer parses a frame; later ones still read it all.
            try:
                with facts.lock:
                    self._require_ready(start, stop, facts)
            except BaseException:
                facts.scope.invalidate()
                raise
        else:
            self._require_ready(start, stop)

    def _require_ready(self, start, stop, facts=None):
        self._interval(start, stop)
        expected = (self.interval_check(start, stop) if self.interval_check is not None
                    else self.environment_check() if self.environment_check is not None else None)
        for frame in range(start, stop):
            path = self.frame_path(frame)
            known = facts.get(self, frame) if facts is not None else None
            got, size = [], []
            if known is None:
                options = {'_verified_size': size} if facts is not None else {}
                reason = png_invalid_reason(path, width=self.width, height=self.height,
                                            _fingerprint=got, **options)
                if reason:
                    raise ValueError(f"PNG source not ready: {path}; rerun vtx gfx ({reason})")
            else:
                fingerprint, length = _current_png_fingerprint(path, known[1])
                if (fingerprint, length) != known:
                    raise ValueError(f'PNG source digest changed: {path}; rerun vtx gfx')
                got.append(fingerprint)
            if expected is not None and (not got or got[0] != expected[frame]):
                raise ValueError(f'PNG source digest changed: {path}; rerun vtx gfx')
            if facts is not None and known is None and got and size:
                facts.remember(self, frame, got[0], size[0])

    def input_args(self, start=0, stop=None, *, options=(), ready=True):
        stop = self.frame_count if stop is None else stop
        if ready:
            self.require_ready(start, stop)
        else:
            self._interval(start, stop)
        # Deliberately no input -frames:v/-t derived from stop: legacy compositor
        # controls output bounds and EOF/repeat. Preserve its optional draft -t.
        return ["-reinit_filter", "0", "-framerate", str(self.fps), "-start_number", str(start),
                *options, "-i", str(self.directory / "f%04d.png")]


def png_source(c, clip):
    """Describe expected output only; safe in the concurrent prepare/gfx wave."""
    from . import gfx_svg
    if gfx_svg.is_svg(clip):
        scope = getattr(c, '_svg_consumer_sources', None)
        scope_key = gfx_svg.canonical(clip)
        if scope is not None and scope_key in scope:
            return scope[scope_key]
        record = gfx_svg.resolve(c, clip)
        directory = c.cache / 'gfx_exp' / gfx_svg.NAMESPACE / gfx_svg.record_key(record)
        snapshot = gfx_svg.SequenceSnapshot(c, clip, directory, record, scope=scope)
        source = PngSequence(directory, str(directory.relative_to(c.cache)), clip['dur'],
                             c.fps, c.w, c.h, interval_check=snapshot.check_interval)
        if scope is not None:
            scope[scope_key] = source
        return source
    directory = gfx_dir(c, clip)
    # Preserve the default leaf identity and HTML environment behavior.
    relative = directory.relative_to(c.cache)
    identity = str(relative) if relative.parts[0] == "gfx_exp" else directory.name
    def check_environment():
        from .gfx_environment import certificate
        from .gfx_cli import require_page_record
        expected = certificate(c)
        if expected is None:
            # Electron has no HTML environment certificate. Authenticate its
            # independent producer contract lazily, never during preparation,
            # then bind the following per-frame byte checks to that record.
            from .gfx_cache_reader import require_electron_record
            return require_electron_record(c, clip, directory)['sha256']
        from .gfx_backend import selected
        from .gfx_dependencies import resolve, digest, policy
        strict = policy(c) == 'strict'
        if strict and selected() != 'cdp':
            raise ValueError('strict dependencies require CDP; native/CLI caches unsupported')
        return require_page_record(
            directory, clip['dur'], directory.name, c.w, c.h,
            backend_ns=gfx_cache_ns() or None, environment=expected,
            dependency_digest=digest(resolve(c, clip)) if strict else None)['sha256']
    return PngSequence(directory, identity, clip["dur"], c.fps, c.w, c.h,
                       environment_check=check_environment)
