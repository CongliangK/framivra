"""Read-only gfx consumer for bridges: production routing and PNG integrity.

No rendering, certification, marker publication or directory discovery. HTML
caches require exact completion records with the selected backend/environment
identity at the canonical production location; unmarked PNGs are not accepted.
"""
from . import gfx_cli
from ..hygiene import gfx_cache_ns


class GfxCacheError(ValueError):
    """The selected gfx cache cannot safely supply the requested strip."""


def require_electron_record(c, clip, directory):
    """Read-only authentication shared by bridges and lazy PNG consumers."""
    from .gfx_backend import electron_config, identity, selected
    from .gfx_dependencies import policy
    from .gfx_electron_stage import cached_record
    if selected() != 'electron':
        raise GfxCacheError('Electron completion requires the selected Electron backend')
    if policy(c) == 'strict':
        raise GfxCacheError('strict dependencies require CDP; native/CLI caches unsupported')
    record = cached_record(directory, clip['dur'], directory.name, c.w, c.h,
                           identity(), gfx_cache_ns(), expected_html=c.root / clip['src'],
                           expected_config=electron_config())
    if record is None:
        raise GfxCacheError(f'Electron gfx 完成记录/身份/producer 回执无效: {directory}; rerun vtx gfx')
    return record


def resolve_frames(c, clip):
    """Return exactly f0000..f(dur-1) from this context's selected gfx directory.

    Accepts the renderer context or a read-only view with root/cache/tl/fps/w/h;
    backend selection, worker identity, dependencies and namespace use the same
    production functions as rendering. Never searches other namespaces/keys.
    The input cache is not modified; obsolete stem directories are not read.
    """
    from .frame_source import png_source
    from . import gfx_svg
    if gfx_svg.is_svg(clip):
        source = png_source(c, clip)
        source.require_ready()
        return [source.frame_path(i) for i in range(source.frame_count)]
    from .gfx_dependencies import resolve, digest, policy
    from .gfx_backend import selected
    if policy(c) == 'strict' and selected() != 'cdp':
        raise GfxCacheError('strict dependencies require CDP; native/CLI caches unsupported')
    source = png_source(c, clip)
    namespace = gfx_cache_ns()
    key = source.directory.name
    directory = source.directory
    # The canonical entry (including a broken symlink/non-directory) must fail on
    # its own merits, not be concealed by otherwise valid historical frames.
    dependency_digest = digest(resolve(c, clip)) if policy(c) == 'strict' else None
    n = source.frame_count
    from .gfx_environment import certificate, recheck
    environment = certificate(c)
    recheck(c)
    if selected() == 'electron':
        require_electron_record(c, clip, directory)
    else:
        # For non-Electron backends certificate() returns a bound environment
        # or raises; no uncertified PNG-only path is reachable.
        # inspect_page's second result also detects absent/malformed records,
        # unsupported schemas, missing hashes and mismatched backend_ns. Unlike
        # a renderer, a read-only consumer must not recertify any of these.
        bad, needs_publication = gfx_cli.inspect_page(
            directory, n, key, c.w, c.h, backend_ns=namespace or None,
            dependency_digest=dependency_digest, environment=environment)
        if bad:
            raise GfxCacheError(f'gfx 缓存无效 {directory}: {bad[:8]}; rerun vtx gfx')
        if needs_publication:
            raise GfxCacheError(f'gfx complete.json 缺失或与当前页合同/namespace 不符: {directory}; rerun vtx gfx')
    return [directory / f'f{i:04d}.png' for i in range(n)]
