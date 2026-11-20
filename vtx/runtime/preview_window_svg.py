"""Private SVG-only declaration and canonical pack selection for Linux windows."""
import re
from pathlib import Path
from types import SimpleNamespace

from .preview_window_isolation import IsolationError


PACK_LIMIT = 256 << 20


def clips(stored):
    return [clip for track in stored['video_tracks'] if track['kind'] == 'gfx'
            for clip in track['clips']]


def check_tools():
    """Refuse launcher scripts before the production inspector invokes ldd."""
    from ..render import gfx_svg
    import shutil

    for tool, selected in (('inkscape', gfx_svg.inkscape_executable()), ('fc-scan', shutil.which('fc-scan'))):
        path = Path('/usr/bin') / tool
        if selected != str(path) or path.resolve(strict=True) != path:
            raise IsolationError('SVG identity requires the fixed system executable')
        with path.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                raise IsolationError('SVG identity refuses an executable launcher script')


def declarations(root, stored):
    """Enumerate manifest members without resolving any producer or tool identity."""
    from ..render import gfx_svg
    from ..render.png_artifact import read_artifact
    from ..timeline_structure import strict_loads

    def member(name):
        if (not isinstance(name, str) or not name or '\0' in name or '\\' in name
                or Path(name).is_absolute() or '..' in Path(name).parts or not Path(name).parts):
            raise IsolationError('SVG window requires project-relative manifest members')
        return root / name

    for clip in clips(stored):
        manifest = member(clip['src'])
        raw = read_artifact(manifest, root=root, limit=gfx_svg.MAX_MANIFEST_BYTES)
        value = strict_loads(raw)
        if not isinstance(value, dict) or value.get('format') != gfx_svg.FORMAT:
            raise IsolationError('SVG window requires the explicit SVG sequence manifest')
        frames, fonts = value.get('frames'), value.get('fonts', [])
        if (not isinstance(frames, list) or len(frames) != clip['dur']
                or len(frames) > gfx_svg.MAX_FRAMES or not isinstance(fonts, list)):
            raise IsolationError('invalid SVG window manifest member list')
        yield str(manifest.relative_to(root)), ('svg-manifest', clip['src'])
        for role, entries in (('svg-frame', frames), ('svg-font', fonts),
                              ('svg-generator', [value['generator']] if 'generator' in value else [])):
            for name in dict.fromkeys(str(member(n).relative_to(root)) for n in entries):
                yield name, (role, clip['src'], name)


def selection(stored, start, end):
    from ..render import gfx_svg

    gfx = clips(stored)
    if gfx and gfx_svg.route(SimpleNamespace(tl=stored)) != 'svg-sequence':
        raise IsolationError('preview window requires an SVG-only graphics timeline')
    hit = [clip for clip in gfx if clip['dst_in'] < end and start < clip['dst_in'] + clip['dur']]
    # Deliberately one clip, not a search over caches or alternative producers.
    if len(hit) > 1:
        raise IsolationError('preview window admits only one intersecting SVG clip')
    return hit[0] if hit else None


def plan(root, stored, timeline, read_timeline, start, end, *, caption_digest=None):
    """Called only after checking the live namespace. Never reads a cache pack."""
    from ..cache.cache_state import receipt_fingerprint
    from ..cache.policy import policy_for
    from ..cache.session import pack_path
    from ..pipeline.constraints import parse_fps

    project = stored['project']
    ctx = SimpleNamespace(tl=stored, root=root, tl_path=timeline, cache=root / 'cache',
                          w=project['width'], h=project['height'], fps=parse_fps(project['fps']),
                          _core_only_preview=True, _read_timeline_bytes=read_timeline)
    if caption_digest is not None:
        ctx._subtitles_input_digest = caption_digest
    clip = selection(stored, start, end)
    fingerprint = receipt_fingerprint(ctx)
    if clip is not None and not policy_for(ctx).reuse_graphics:
        raise IsolationError('SVG warm pack reuse is disabled')
    pack = str(pack_path(ctx, clip).relative_to(root)) if clip is not None else None
    if caption_digest is not None:
        caption_digest()  # Recheck the same bound input after deriving the pack key.
    read_timeline()
    return {'fingerprint': fingerprint, 'pack': pack}


def pack_selector(root, relative):
    """Validate the worker's typed selection before opening any host cache file."""
    from ..cache.hygiene import GFX_EXP
    from ..render.gfx_svg import NAMESPACE

    prefix = f'cache/gfx_packs/{GFX_EXP}/{NAMESPACE}/'
    if type(relative) is not str or not re.fullmatch(re.escape(prefix) + r'[0-9a-f]{24}\.vtxp', relative):
        raise IsolationError('invalid isolated SVG pack selection')
    return root / relative
