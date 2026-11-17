"""Select v2 dependency slots and statically confine their file targets."""

import os
import stat
from pathlib import Path


class UnsafeTrackRef(Exception):
    """A track reference is anchored, outside the project, or not a file."""


def scan_ref_file(project, ref):
    """Return (resolved path, exists) for a statically safe track reference.

    This is not an atomic open: concurrent replacement and hardlinks remain
    outside this check's protection. Missing files are never opened.
    """
    path = os.path
    separators = tuple(s for s in (path.sep, path.altsep) if s)
    if path.splitdrive(ref)[0] or ref.startswith(separators) or path.isabs(ref):
        raise UnsafeTrackRef(f"anchored track ref: {ref!r}")
    base = str(Path(project).parent.resolve(strict=True))
    joined = path.join(str(Path(project).parent), ref)
    # Match _read_ref_track's normpath-before-realpath and open semantics.
    candidate = path.normpath(joined)
    try:
        resolved = str(Path(candidate).resolve(strict=True))
        st = os.stat(resolved)
    except FileNotFoundError:
        resolved = path.realpath(candidate)
        if not resolved.startswith(base + path.sep):
            raise UnsafeTrackRef(f"track ref outside project: {ref!r}")
        return Path(resolved), False
    if not resolved.startswith(base + path.sep):
        raise UnsafeTrackRef(f"track ref outside project: {ref!r}")
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeTrackRef(f"track ref is not a regular file: {ref!r}")
    return Path(resolved), True


def track_ref_slots(project):
    """Return (track index, original ref) for v2 pure nonblank string slots.

    This is a shape filter, not a path-safety check or a project validator.
    """
    if not isinstance(project, dict):
        return []
    meta, tracks = project.get("meta"), project.get("tracks")
    if not isinstance(meta, dict) or str(meta.get("spec_version")) != "2" or not isinstance(tracks, list):
        return []
    return [(i, track["$ref"]) for i, track in enumerate(tracks)
            if isinstance(track, dict) and set(track) == {"$ref"}
            and isinstance(track["$ref"], str) and track["$ref"].strip()]
