"""Internal PNG FrameSource, without eager IO or a second rendering backend.

Intervals are clip-local and half-open. Random seeks and repeated consumers are
independent; no cursor is shared. FFmpeg still reads the original sequence to
EOF (the compositor owns trim, repeat-last and padding). Missing/truncated files
fail before consumption; decoder errors still propagate from FFmpeg. Recovery is
an explicit rerun of gfx, never fabricated frames or a successful receipt.
"""
import hashlib
import json
import os
from contextlib import ExitStack

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .hygiene import gfx_dir
from .gfx_cli import (png_invalid_reason, opaque_rgb_to_rgba, COMPLETE_FILE,
                      inspect_page, invalidate_page, complete_page)
from . import fstxn


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

    @property
    def time_base(self):
        return Fraction(1, self.fps)

    def _interval(self, start, stop):
        if not (0 <= start < stop <= self.frame_count):
            raise ValueError(f"Invalid PNG interval [{start},{stop}) / {self.frame_count}")

    def frame_path(self, frame):
        self._interval(frame, frame + 1)
        return self.directory / f"f{frame:04d}.png"

    def _validated(self, start, stop, *, through_eof=False):
        self._interval(start, stop)
        expected = self.environment_check() if self.environment_check is not None else None
        # stop is a readiness boundary, not an image2 EOF. Partial subtitle
        # previews may have only one frame, or a longer already-rendered tail.
        end = stop
        if through_eof:
            while (self.directory / f"f{end:04d}.png").exists():
                end += 1
        frames = []
        for frame in range(start, end):
            path = self.directory / f"f{frame:04d}.png"
            got, props = [], {}
            reason = png_invalid_reason(path, width=self.width, height=self.height,
                                        _fingerprint=got, _properties=props)
            if reason:
                raise ValueError(f"PNG source not ready: {path}; rerun vtx gfx ({reason})")
            if expected is not None and (frame >= len(expected) or got != [expected[frame]]):
                raise ValueError(f'PNG source digest changed: {path}; rerun vtx gfx')
            frames.append((path, got[0], props))
        return frames

    def require_ready(self, start=0, stop=None):
        stop = self.frame_count if stop is None else stop
        self._validated(start, stop)

    def _stable_input(self, start, stop):
        frames = self._validated(start, stop, through_eof=True)
        formats = {(p['depth'], p['color'], p['transparency']) for _, _, p in frames}
        # RGB+tRNS decodes with alpha, as does RGBA. Do not treat the PNG colour
        # type alone as the decoder format, or silently discard tRNS pixels.
        formats = {(d, 6 if c == 2 and t else c) for d, c, t in formats}
        if len(formats) == 1:
            return self.directory, start  # common case: no writes or conversion
        if formats != {(8, 2), (8, 6)} or any(p['animated'] for _, _, p in frames):
            raise ValueError('Unsupported mixed PNG formats; rerun vtx gfx')
        if any(p['transparency'] for _, _, p in frames):
            raise ValueError('Mixed PNG tRNS conversion is unsupported; rerun vtx gfx')
        if any(p['color'] == 2 and p['unsafe_unknown'] for _, _, p in frames):
            raise ValueError('Unsupported unknown unsafe-to-copy PNG chunk; rerun vtx gfx')
        requested_start = start
        if start:
            # Complete pages share one derivative across random seeks. Missing,
            # invalid or unsupported *unconsumed* prefixes must not expand a
            # preview's readiness requirement; those keep an isolated tail.
            try:
                prefix = self._validated(0, start)
            except ValueError:
                prefix = []
            if len(prefix) == start and all(p['depth'] == 8 and p['color'] in (2, 6)
                    and not p['transparency'] and not p['animated']
                    and (p['color'] == 6 or not p['unsafe_unknown']) for _, _, p in prefix):
                frames = prefix + frames
                start = 0
        provenance = dict(policy='opaque-rgb8-to-rgba8-v1', identity=self.identity,
                          start=start, stop=start + len(frames), width=self.width,
                          height=self.height,
                          source_digest=hashlib.sha256(json.dumps(
                              [sha for _, sha, _ in frames]).encode()).hexdigest())
        key = 'rgba8-' + hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()[:24]
        directory = self.directory.parent / key
        marker = directory / COMPLETE_FILE
        source_directory = fstxn.entry_identity(self.directory.stat())
        # Same page validation/completion and transaction machinery as gfx.
        # The prefix and frame_source provenance distinguish this from browser
        # output; the original page and its certificate are never rewritten.
        with ExitStack() as stack:
            directory_fd, check_directory = stack.enter_context(fstxn.pinned_directory(directory))
            if fstxn.entry_identity(os.fstat(directory_fd)) == source_directory:
                raise ValueError('Canonical PNG directory aliases its source')
            stack.enter_context(fstxn.file_lock(marker, directory_fd=directory_fd))
            check_directory()  # includes changes while waiting for the page lock
            def check_eof():
                try:
                    os.stat(f"f{len(frames):04d}.png", dir_fd=directory_fd, follow_symlinks=False)
                except FileNotFoundError:
                    return
                raise ValueError('Canonical PNG EOF changed; remove derived page and retry')
            def verify_inputs():
                check_directory()
                check_eof()
                current = self._validated(start, stop, through_eof=True)
                if [(p, h) for p, h, _ in current] != [(p, h) for p, h, _ in frames]:
                    raise ValueError('PNG source changed before canonical consumption')
                check_directory()
            check_eof()
            try:
                with fstxn.open_at(directory_fd, COMPLETE_FILE) as stream:
                    record = json.load(stream)
            except (OSError, ValueError):
                record = None
            if isinstance(record, dict) and record.get('frame_source') == provenance:
                bad, needs_marker = inspect_page(directory, len(frames), key, self.width, self.height,
                                                 frame_source=provenance, directory_fd=directory_fd)
                if not bad and not needs_marker:
                    # The source may change while waiting for this page lock or
                    # while validating the derivative. Warm hits need the same
                    # input-token gate as newly published pages.
                    verify_inputs()
                    return directory, requested_start - start
            invalidate_page(directory, directory_fd=directory_fd)
            for i, (path, digest, props) in enumerate(frames):
                check_directory()
                if props['color'] == 2:
                    data = opaque_rgb_to_rgba(path, width=self.width, height=self.height,
                                              expected_digest=digest)
                    fstxn.atomic_write(directory / f'f{i:04d}.png', data,
                                       directory_fd=directory_fd)
                else:
                    # A byte copy, not a hard link: derived writes must never
                    # alter original pixels, alpha, hidden RGB or colour chunks.
                    with fstxn.atomic_writer_at(directory_fd, f'f{i:04d}.png') as dst:
                        copied = hashlib.sha256()
                        with path.open('rb') as src:
                            while block := src.read(65536):
                                dst.write(block)
                                copied.update(block)
                        if copied.hexdigest() != digest:
                            raise ValueError(f'PNG source changed while copying: {path}')
            complete_page(directory, len(frames), key, self.width, self.height,
                          verify_inputs=verify_inputs, frame_source=provenance,
                          directory_fd=directory_fd)
            check_directory()
        return directory, requested_start - start

    def input_args(self, start=0, stop=None, *, options=(), ready=True):
        stop = self.frame_count if stop is None else stop
        self._interval(start, stop)
        directory, first = self._stable_input(start, stop) if ready else (self.directory, start)
        # No input -frames:v/-t derived from stop: preserve the complete actual
        # image2 EOF and compositor-owned trim/repeat. Canonical numbering starts
        # at zero, exactly like image2 PTS regardless of its start_number.
        return ["-framerate", str(self.fps), "-start_number", str(first),
                *options, "-i", str(directory / "f%04d.png")]


def png_source(c, clip):
    """Describe expected output only; safe in the concurrent prepare/gfx wave."""
    directory = gfx_dir(c, clip)
    # Preserve the default leaf identity, but include the selected experimental
    # namespace in every downstream consumer (pass/segment/range/impact).
    relative = directory.relative_to(c.cache)
    identity = str(relative) if relative.parts[0] == "gfx_exp" else directory.name
    def check_environment():
        import json
        from .gfx_environment import certificate
        from .gfx_cli import COMPLETE_FILE
        expected = certificate(c)
        if expected is None:
            return  # historical Electron has its independent producer checks
        try:
            record = json.loads((directory / COMPLETE_FILE).read_text())
        except (OSError, ValueError) as error:
            raise ValueError('gfx environment certificate missing; rerun vtx gfx') from error
        if (not isinstance(record, dict) or type(record.get('schema')) is not int
                or record['schema'] != 1
                or record.get('environment') != expected
                or record.get('mixed_producer')
                or not isinstance(record.get('sha256'), list) or len(record['sha256']) != clip['dur']
                or any(record.get(k) != v for k, v in
                       dict(key=directory.name, frames=clip['dur'], width=c.w, height=c.h).items())):
            raise ValueError('gfx environment certificate changed; rerun vtx gfx')
        return record['sha256']
    return PngSequence(directory, identity, clip["dur"], c.fps, c.w, c.h,
                       environment_check=check_environment)
