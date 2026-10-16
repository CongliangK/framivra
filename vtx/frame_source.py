"""Internal PNG FrameSource, without eager IO or a second rendering backend.

Intervals are clip-local and half-open. Random seeks and repeated consumers are
independent; no cursor is shared. FFmpeg still reads the original sequence to
EOF (the compositor owns trim, repeat-last and padding). Missing/truncated files
fail before consumption; decoder errors still propagate from FFmpeg. Recovery is
an explicit rerun of gfx, never fabricated frames or a successful receipt.
"""
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .hygiene import gfx_dir
from .gfx_cli import png_invalid_reason


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
        self._interval(start, stop)
        for frame in range(start, stop):
            path = self.frame_path(frame)
            reason = png_invalid_reason(path, width=self.width, height=self.height)
            if reason:
                raise ValueError(f"PNG source not ready: {path}; rerun vtx gfx ({reason})")

    def input_args(self, start=0, stop=None, *, options=(), ready=True):
        stop = self.frame_count if stop is None else stop
        self._interval(start, stop)
        if ready:
            self.require_ready(start, stop)
        # Deliberately no input -frames:v/-t derived from stop: legacy compositor
        # controls output bounds and EOF/repeat. Preserve its optional draft -t.
        return ["-framerate", str(self.fps), "-start_number", str(start),
                *options, "-i", str(self.directory / "f%04d.png")]


def png_source(c, clip):
    """Describe expected output only; safe in the concurrent prepare/gfx wave."""
    directory = gfx_dir(c, clip)
    return PngSequence(directory, directory.name, clip["dur"], c.fps, c.w, c.h)
