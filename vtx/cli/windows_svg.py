"""Select the owned Windows SVG profile for explicitly configured CLI stages."""
from contextlib import contextmanager
import hashlib
import os
import sys


def _is_standard(timeline):
    from ..render.gfx_svg import MAX_MANIFEST_BYTES
    from ..render.gfx_svg_win_media import require_standard_timeline

    # This read only dispatches. Ctx rereads through the anchored reader and
    # checks the exact identity again before accepting any assets or tools.
    try:
        with timeline.open('rb') as stream:
            content = stream.read(MAX_MANIFEST_BYTES + 1)
        require_standard_timeline(hashlib.sha256(content).hexdigest())
    except (OSError, ValueError):
        return False
    return True


def context_options(ownership, command, timeline, *, strict=False, truncated=False):
    if sys.platform != 'win32' or (command == 'qc' and not strict):
        return {}
    portable = os.environ.get('VTX_WINDOWS_SVG_PORTABLE')
    scratch = os.environ.get('VTX_WINDOWS_SVG_SCRATCH')
    media_bin = os.environ.get('VTX_WINDOWS_SVG_MEDIA_BIN')
    if command == 'gfx' and (portable or scratch):
        media_bin = None
    elif command not in ('all', 'validate', 'qc') or not media_bin:
        return {}
    if command in ('validate', 'qc') and not _is_standard(timeline):
        return {}
    if not portable or not scratch:
        raise ValueError('Windows SVG requires explicit portable and private scratch roots')
    if command == 'all' and truncated:
        raise ValueError('Windows SVG all requires full-size CPU rendering without stage truncation')

    from ..render.gfx_svg_win_runtime import owned_runtime

    def factory(c, fonts):
        return ownership.enter_context(owned_runtime(c, portable, scratch, fonts, media_bin=media_bin))

    return {'_svg_all': media_bin is not None, '_svg_runtime_factory': factory}


@contextmanager
def stage_scope(c):
    if getattr(c, '_svg_runtime', None) is None:
        yield
        return

    from ..render.gfx_svg_win_publish import anchored

    # A standalone QC pass must see one delivery generation from its mux
    # prerequisite through report/receipt publication. Movie handles alone
    # cannot prevent another writer from revoking the mux receipt mid-pass.
    with anchored(c.root) as root, root.child('out', create=True) as out:
        with out.lock('windows-svg-stage'):
            yield
