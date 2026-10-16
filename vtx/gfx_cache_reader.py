"""Read-only gfx consumer for bridges: production routing and PNG integrity.

No rendering, certification, marker publication or directory discovery. CEF and
experimental namespaces require an existing exact completion record. Only the
default CDP/CLI route retains legacy unmarked/stem-directory compatibility.
"""
from pathlib import Path

from . import gfx_cli
from .hygiene import gfx_cache_ns, gfx_dir, gfx_key


class GfxCacheError(ValueError):
    """The selected gfx cache cannot safely supply the requested strip."""


def resolve_frames(c, clip):
    """Return exactly f0000..f(dur-1) from this context's selected gfx directory.

    Accepts the renderer context or a read-only view with root/cache/tl/fps/w/h;
    backend selection, worker identity, dependencies and namespace use the same
    production functions as rendering. Never searches other namespaces/keys.
    The input cache is not modified, including legacy caches without a marker.
    """
    namespace = gfx_cache_ns()
    key = gfx_key(c, clip)
    directory = gfx_dir(c, clip)
    if not namespace and not directory.is_dir():
        directory = c.cache / Path(clip['src']).stem
    n = clip['dur']
    marker = directory / gfx_cli.COMPLETE_FILE
    if namespace or marker.exists():
        # inspect_page's second result also detects absent/malformed records,
        # unsupported schemas, missing hashes and mismatched backend_ns. Unlike
        # a renderer, a read-only consumer must not recertify any of these.
        bad, needs_publication = gfx_cli.inspect_page(
            directory, n, key, c.w, c.h, backend_ns=namespace or None)
        if bad:
            raise GfxCacheError(f'gfx 缓存无效 {directory}: {bad[:8]}')
        if needs_publication:
            raise GfxCacheError(f'gfx complete.json 缺失或与当前页合同/namespace 不符: {directory}')
    else:
        # Default CDP/CLI compatibility still validates exact frame numbers and
        # real PNG bytes. A glob/count cannot establish contiguous coverage.
        bad = gfx_cli.verify_frames(directory, range(n), width=c.w, height=c.h)
        if bad:
            raise GfxCacheError(f'gfx 缓存无效 {directory}: {bad[:8]}')
    return [directory / f'f{i:04d}.png' for i in range(n)]
