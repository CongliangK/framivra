"""Blender-local gfx delivery dependencies, never a reusable cache or producer."""
import hashlib
from pathlib import Path
import secrets
import sys

from vtx.cache.cache_state import verify_input_identity
from vtx.cache.hygiene import gfx_key
from vtx.cache.policy import policy_for
from vtx.render.gfx_packed_reader import read_frame
from vtx.render.gfx_pack import _rename_new
from vtx.render.png_artifact import read_png
from vtx.runtime import fstxn


def materialize_frames(c, clip, blend_path):
    """Authenticate ALL frames, then publish independent original PNG assets.

    On Linux fd-anchored, even aggressive bare PNGs are copied so switching to
    core cannot break an existing .blend. Other platforms retain the old read-only
    canonical bare-frame dependency. Existing deliveries are never replaced.
    Electron's getter repeats its full-page receipt gate; no speedup is claimed.
    """
    if not policy_for(c).reuse_graphics:
        raise ValueError('Blender gfx export is unsupported in cache.mode=off: '
                         'old gfx is forbidden and private fresh gfx is not available to this script')
    verify_input_identity(c)
    if sys.platform != 'linux' or fstxn.write_capability() != 'fd-anchored':
        # These platforms keep bare gfx in core. Preserve the existing read-only
        # consumer; never invent a weaker output/publication backend here.
        from vtx.render.gfx_cache_reader import resolve_frames
        files = resolve_frames(c, clip)
        verify_input_identity(c)
        return files
    key = gfx_key(c, clip)
    first = read_frame(c, clip, 0)  # Missing/invalid inputs create no output assets.
    output = Path(blend_path).absolute()
    directory = output.with_suffix('.assets') / 'gfx' / key
    fstxn.mkdir(directory.parent, root=output.parent)
    with fstxn.file_lock(directory, root=output.parent):
        stage = directory.parent / ('.' + key + '.' + secrets.token_hex(8) + '.tmp')
        fstxn.mkdir(stage, root=output.parent)
        identity = fstxn.cleanup_identity(stage)
        hashes = []
        try:
            for index in range(clip['dur']):
                data = first if index == 0 else read_frame(c, clip, index)
                hashes.append(hashlib.sha256(data).hexdigest())
                fstxn.atomic_write(stage / f'f{index:04d}.png', data, root=output.parent)
            verify_input_identity(c)
            if gfx_key(c, clip) != key:
                raise ValueError('gfx identity changed during Blender export')
            files = [directory / f'f{i:04d}.png' for i in range(clip['dur'])]
            with fstxn.pinned_parent(directory, create=False, root=output.parent) as (parent, name, _):
                if directory.exists() or directory.is_symlink():
                    # Existing DELIVERY bytes are not cache authority: every
                    # producer frame above was freshly authenticated first.
                    delivered_identity = fstxn.cleanup_identity(directory)
                else:
                    if fstxn.cleanup_identity(stage) != identity:
                        raise ValueError('Blender gfx staging identity changed')
                    _rename_new(parent, stage.name, name)
                    delivered_identity = identity
            for path, expected in zip(files, hashes):
                facts, _ = read_png(path, root=output.parent, width=c.w, height=c.h,
                                    limit=c.w * c.h * 8 + 65536)
                if facts['sha256'] != expected:
                    raise ValueError('Blender gfx assets differ; choose a new .blend output name')
            verify_input_identity(c)
            if fstxn.cleanup_identity(directory) != delivered_identity:
                raise ValueError('Blender gfx delivery directory changed')
            return files
        finally:
            fstxn.remove_pinned(stage, identity, preserve=lambda _path: False)
