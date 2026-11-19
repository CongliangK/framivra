"""Read authenticated gfx PNGs without rendering or restoring a page.

Storage integrity is not cache eligibility. Reuse the directory consumers'
proof gates with independently resolved CURRENT inputs/backend/environment.
Electron deliberately retains its full-page producer/PNG checks: no O(1) claim.
"""
import re
import stat

from ..cache.cache_state import verify_input_identity
from ..cache.hygiene import gfx_cache_ns, gfx_dir
from ..cache.policy import policy_for
from ..cache.session import pack_path
from . import gfx_cli, gfx_environment, gfx_svg
from .gfx_cache_reader import GfxCacheError
from .gfx_pack import PagePack, inspect_pack
from ..runtime import fstxn


# One second at any allowed rate; cap actual logical output independently of
# compression and frame count. This is a per-call, not a multi-layer, budget.
_MAX_FRAMES = 120
_MAX_BYTES = 256 << 20


def _bare_hashes(source, start, stop):
    return (source.interval_check(start, stop) if source.interval_check is not None
            else source.environment_check())


def _bare_png(c, source, index, *, max_bytes):
    from .png_artifact import read_png
    return read_png(source.frame_path(index), root=c.root, width=c.w, height=c.h,
                    limit=min(max_bytes, c.w * c.h * 8 + 65536))


def _bare_bytes(c, source, index):
    """Authenticate immutable PNG bytes; caller owns the full input boundaries."""
    source.require_ready(index, index + 1)
    facts, data = _bare_png(c, source, index, max_bytes=_MAX_BYTES)
    if facts['sha256'] != _bare_hashes(source, index, index + 1)[index]:
        raise GfxCacheError('PNG bytes changed after source authentication')
    return data


def _cache_version(path):
    """Small namespace + file-version reference, not a producer proof."""
    chain = fstxn.cleanup_identity(path)
    if chain is None or chain[-1][2] != stat.S_IFREG:
        raise GfxCacheError('graphics cache artifact missing, linked or special')
    info = path.lstat()
    if fstxn.entry_identity(info) != chain[-1] or fstxn.cleanup_identity(path) != chain:
        raise GfxCacheError('graphics cache artifact changed during read')
    return chain, info.st_size, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns


def _bare_frames(c, clip, start, stop, directory, archive, check_cancel, max_bytes, keep_recheck):
    from .frame_source import png_source
    from .gfx_backend import selected
    source = png_source(c, clip)
    if source.directory != directory:
        raise GfxCacheError('PNG source does not match canonical page')
    # The same completion gates as require_ready(), but parse/hash each PNG
    # from its anchored immutable snapshot rather than first reopening by path.
    paths = versions = None
    if keep_recheck is not None:
        paths = [directory / 'complete.json', *(source.frame_path(i) for i in range(start, stop))]
        versions = tuple(_cache_version(path) for path in paths)
    expected = _bare_hashes(source, start, stop)
    data, hashes, remaining = [], [], max_bytes
    for i in range(start, stop):
        if check_cancel is not None:
            check_cancel()
        facts, frame = _bare_png(c, source, i, max_bytes=remaining)
        if facts['sha256'] != expected[i]:
            raise GfxCacheError('PNG bytes changed after source authentication')
        data.append(frame)
        hashes.append(facts['sha256'])
        remaining -= len(frame)
    if check_cancel is not None:
        check_cancel()
    current = _bare_hashes(source, start, stop)
    if any(current[i] != digest for i, digest in enumerate(hashes, start)):
        raise GfxCacheError('PNG completion changed during read')
    verify_input_identity(c)
    electron = not gfx_svg.is_svg(clip) and selected() == 'electron'
    def recheck():
        if gfx_dir(c, clip) != directory:
            raise GfxCacheError('PNG inputs/backend changed during read')
        if fstxn.cleanup_identity(archive) is not None:
            raise GfxCacheError('packed gfx appeared during bare read; retry against the pack')
        if versions is not None and any(_cache_version(p) != v for p, v in zip(paths, versions)):
            raise GfxCacheError('bare gfx artifacts changed during consumption')

    recheck()
    if check_cancel is not None:
        check_cancel()
    if keep_recheck is not None:
        def final_recheck():
            # Electron's legacy bare producer proof covers more than the selected
            # PNGs and completion. Do not silently narrow it to just those files.
            if electron:
                current = _bare_hashes(source, start, stop)
                if any(current[i] != value for i, value in enumerate(hashes, start)):
                    raise GfxCacheError('PNG completion changed during consumption')
            recheck()
        keep_recheck(final_recheck)
    return tuple(data)


def read_frame(c, clip, index):
    """Return original PNG bytes using the same gates as an interval read."""
    return read_frames(c, clip, index, index + 1 if type(index) is int else None)[0]


def read_frames(c, clip, start, stop, *, check_cancel=None, max_bytes=None, keep_recheck=None):
    """Return a tuple of original PNGs in clip-local [start, stop) order.

    ``c`` retains the full normal input pin for read-only verify_input_identity;
    no dry shortcut, formal receipt write or SystemExit adapter is used. Only
    the current canonical pack is considered. If it is entirely absent, existing
    authenticated bare PNGs may supply bytes; corrupt/present packs never fall
    back. Linux/macOS anchored reads only; off mode is a hard refusal.
    Requests must be nonempty, at most 120 frames and 256 MiB of returned logical
    PNG bytes (not a peak-memory bound for decompression or producer proofs).
    Optional ``max_bytes`` lowers the per-call budget; ``check_cancel()`` raises
    KeyboardInterrupt to abort cooperatively. A consumer spanning other work can
    take a small ``keep_recheck(recheck)`` reference to the accepted storage and
    producer identity, and call it before its own final full input check. No pack
    handle or decoded PNGs are retained by the reference. Nothing is returned
    until all requested bytes and final checks pass.
    """
    if max_bytes is None:
        max_bytes = _MAX_BYTES
    elif type(max_bytes) is not int or not 0 < max_bytes <= _MAX_BYTES:
        raise GfxCacheError('invalid graphics byte limit')
    if not policy_for(c).reuse_graphics:
        raise GfxCacheError('packed gfx reuse is disabled by cache.mode=off')
    if type(start) is not int or type(stop) is not int or not 0 <= start < stop <= clip['dur']:
        raise GfxCacheError('packed gfx frame interval outside clip')
    if stop - start > _MAX_FRAMES:
        raise GfxCacheError('packed gfx interval exceeds frame limit')
    if check_cancel is not None:
        check_cancel()
    verify_input_identity(c)
    directory = gfx_dir(c, clip)
    archive = pack_path(c, clip)
    if fstxn.cleanup_identity(archive) is None:
        return _bare_frames(c, clip, start, stop, directory, archive, check_cancel, max_bytes, keep_recheck)
    version = _cache_version(archive) if keep_recheck is not None else None
    record = inspect_pack(archive, root=c.root)['record']
    svg = gfx_svg.is_svg(clip)
    environment = inputs = backend = namespace = settings = None
    if svg:
        inputs = gfx_svg.resolve(c, clip)
        gfx_svg.verify_completion(record, inputs, clip['dur'], directory.name)
    else:
        from .gfx_backend import selected, identity, electron_config
        from .gfx_dependencies import policy, resolve, digest
        backend, namespace = selected(), gfx_cache_ns() or None
        strict = policy(c) == 'strict'
        if strict and backend != 'cdp':
            raise GfxCacheError('strict dependencies require CDP; native/CLI caches unsupported')
        environment = gfx_environment.certificate(c)
        gfx_environment.recheck(c)
        if backend == 'electron':
            # Establish TRUSTED resource bounds before PagePack sees an untrusted
            # producer record. Full receipt authentication follows using its bytes.
            if (record.get('key') != directory.name or any(type(record.get(field)) is not int
                    or record[field] != expected for field, expected in
                    (('frames', clip['dur']), ('width', c.w), ('height', c.h)))):
                raise GfxCacheError('packed Electron page does not match current frame contract')
            settings = identity(), electron_config()
        else:
            gfx_cli.verify_page_record(record, clip['dur'], directory.name, c.w, c.h,
                backend_ns=namespace, environment=environment,
                dependency_digest=digest(resolve(c, clip)) if strict else None)
    svg_digest = gfx_svg.digest(gfx_svg.canonical(inputs)) if svg else None

    def recheck():
        if gfx_dir(c, clip) != directory:
            raise GfxCacheError('packed gfx inputs/backend changed during read')
        if svg:
            if gfx_svg.digest(gfx_svg.canonical(gfx_svg.resolve(c, clip))) != svg_digest:
                raise GfxCacheError('packed SVG inputs changed during read')
        else:
            gfx_environment.recheck(c)
            if (selected() != backend or (gfx_cache_ns() or None) != namespace
                    or gfx_environment.certificate(c) != environment
                    or (backend == 'electron' and (identity(), electron_config()) != settings)):
                raise GfxCacheError('packed gfx backend/environment changed during read')
        if version is not None and _cache_version(archive) != version:
            raise GfxCacheError('packed gfx changed during consumption')

    if check_cancel is not None:
        check_cancel()
    with PagePack(archive, expected_record=record, root=c.root) as page:
        if backend == 'electron':
            from .gfx_electron_stage import cached_record
            def artifact(name):
                if check_cancel is not None:
                    check_cancel()
                if name in page.metadata_names:
                    return page.read_metadata(name)
                match = re.fullmatch(r'f([0-9]{4,})\.png', name)
                if match is None or name != f'f{int(match[1]):04d}.png':
                    raise GfxCacheError('unexpected packed producer artifact name')
                return page.read_frame(int(match[1]))
            if cached_record(directory, clip['dur'], directory.name, c.w, c.h,
                    settings[0], namespace, expected_html=c.root / clip['src'],
                    expected_config=settings[1], read_artifact=artifact) is None:
                raise GfxCacheError('packed Electron producer/completion proof invalid; rerun vtx gfx')
        data = page.read_frames(start, stop, max_bytes=max_bytes, check_cancel=check_cancel)
        if check_cancel is not None:
            check_cancel()
        verify_input_identity(c)
        recheck()
    if check_cancel is not None:
        check_cancel()
    if keep_recheck is not None:
        keep_recheck(recheck)
    return data
