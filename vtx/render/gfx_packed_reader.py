"""Read an authenticated packed gfx frame without rendering or unpacking.

Storage integrity is not cache eligibility. Reuse the directory consumers'
proof gates with independently resolved CURRENT inputs/backend/environment.
Electron deliberately retains its full-page producer/PNG checks: no O(1) claim.
"""
import re

from ..cache.cache_state import verify_input_identity
from ..cache.hygiene import gfx_cache_ns, gfx_dir
from ..cache.policy import policy_for
from ..cache.session import pack_path
from . import gfx_cli, gfx_environment, gfx_svg
from .gfx_cache_reader import GfxCacheError
from .gfx_pack import PagePack, inspect_pack
from ..runtime import fstxn


def _bare_frame(c, clip, index, directory):
    from .frame_source import png_source
    from .png_artifact import read_png
    source = png_source(c, clip)
    if source.directory != directory:
        raise GfxCacheError('PNG source does not match canonical page')
    source.require_ready(index, index + 1)
    facts, data = read_png(source.frame_path(index), root=c.root, width=c.w, height=c.h,
                         limit=c.w * c.h * 8 + 65536)
    # Bind THESE immutable returned bytes, not only the path previously checked.
    expected = (source.interval_check(index, index + 1) if source.interval_check is not None
                else source.environment_check())
    if facts['sha256'] != expected[index]:
        raise GfxCacheError('PNG bytes changed after source authentication')
    verify_input_identity(c)
    if gfx_dir(c, clip) != directory:
        raise GfxCacheError('PNG inputs/backend changed during read')
    return data


def read_frame(c, clip, index):
    """Return original PNG bytes; no directory creation or certificate adoption.

    ``c`` retains the full normal input pin for read-only verify_input_identity;
    no dry shortcut, formal receipt write or SystemExit adapter is used. Only
    the current canonical pack is considered. If it is entirely absent, existing
    authenticated bare PNGs may supply bytes; corrupt/present packs never fall
    back. Like the existing preview artifact reader, this API requires Linux
    fd-anchored reads. Off mode is a hard refusal.
    """
    if not policy_for(c).reuse_graphics:
        raise GfxCacheError('packed gfx reuse is disabled by cache.mode=off')
    if type(index) is not int or not 0 <= index < clip['dur']:
        raise GfxCacheError('packed gfx frame index outside clip')
    verify_input_identity(c)
    directory = gfx_dir(c, clip)
    archive = pack_path(c, clip)
    if fstxn.cleanup_identity(archive) is None:
        return _bare_frame(c, clip, index, directory)
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
    with PagePack(archive, expected_record=record, root=c.root) as page:
        if backend == 'electron':
            from .gfx_electron_stage import cached_record
            def artifact(name):
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
        data = page.read_frame(index)
        verify_input_identity(c)
        if gfx_dir(c, clip) != directory:
            raise GfxCacheError('packed gfx inputs/backend changed during read')
        if svg:
            if gfx_svg.resolve(c, clip) != inputs:
                raise GfxCacheError('packed SVG inputs changed during read')
        else:
            gfx_environment.recheck(c)
            if (selected() != backend or (gfx_cache_ns() or None) != namespace
                    or gfx_environment.certificate(c) != environment
                    or (backend == 'electron' and (identity(), electron_config()) != settings)):
                raise GfxCacheError('packed gfx backend/environment changed during read')
        return data
