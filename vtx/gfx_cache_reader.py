"""Read-only gfx consumer for bridges: production routing and PNG integrity.

No rendering, certification, marker publication or directory discovery. CEF and
experimental namespaces require an existing exact completion record. The default
CDP/CLI route accepts unmarked PNGs only at the current canonical directory;
its derived legacy stem requires an exact current completion certificate.
"""
from pathlib import Path

from . import gfx_cli
from .hygiene import gfx_cache_ns


class GfxCacheError(ValueError):
    """The selected gfx cache cannot safely supply the requested strip."""


def resolve_frames(c, clip):
    """Return exactly f0000..f(dur-1) from this context's selected gfx directory.

    Accepts the renderer context or a read-only view with root/cache/tl/fps/w/h;
    backend selection, worker identity, dependencies and namespace use the same
    production functions as rendering. Never searches other namespaces/keys.
    The input cache is not modified, including legacy caches without a marker.
    """
    from .frame_source import png_source
    from .gfx_dependencies import resolve, digest, policy
    from .gfx_backend import selected
    if policy(c) == 'strict' and selected() != 'cdp':
        raise GfxCacheError('strict dependencies require CDP; native/CLI caches unsupported')
    source = png_source(c, clip)
    namespace = gfx_cache_ns()
    key = source.directory.name
    directory = source.directory
    legacy = False
    # Only one explicitly derived old location, never discovery. An existing
    # canonical entry (including a broken symlink/non-directory) must fail on
    # its own merits, not be concealed by otherwise valid historical frames.
    if (not namespace and selected() in ('cdp', 'cli')
            and not directory.exists() and not directory.is_symlink()):
        candidate = c.cache / Path(clip['src']).stem
        if candidate.is_dir():
            directory, legacy = candidate, True
    dependency_digest = digest(resolve(c, clip)) if policy(c) == 'strict' else None
    n = source.frame_count
    marker = directory / gfx_cli.COMPLETE_FILE
    from .gfx_backend import identity
    from .gfx_environment import certificate, recheck
    environment = certificate(c)
    recheck(c)
    if selected() == 'electron':
        from .gfx_electron_stage import cached_runs
        from .gfx_backend import electron_config
        if cached_runs(directory, n, key, c.w, c.h, identity(), namespace,
                       expected_html=c.root / clip['src'], expected_config=electron_config()) is None:
            raise GfxCacheError(f'Electron gfx 完成记录/身份/producer 回执无效: {directory}')
    elif environment is not None or legacy or namespace or marker.exists() or dependency_digest is not None:
        # inspect_page's second result also detects absent/malformed records,
        # unsupported schemas, missing hashes and mismatched backend_ns. Unlike
        # a renderer, a read-only consumer must not recertify any of these.
        bad, needs_publication = gfx_cli.inspect_page(
            directory, n, key, c.w, c.h, backend_ns=namespace or None,
            dependency_digest=dependency_digest, environment=environment)
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
