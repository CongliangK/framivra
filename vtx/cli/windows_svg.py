"""Select the owned Windows SVG profile for explicitly configured CLI stages."""
from contextlib import contextmanager
import json
import os
import sys


def _has_svg(timeline):
    from ..render.gfx_svg import MAX_MANIFEST_BYTES, is_svg

    # Dispatch only. Ctx rereads and checks the actual route through the anchored
    # reader. If this dispatch misses an SVG route, the default runtime rejects.
    try:
        with timeline.open('rb') as stream:
            content = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(content) > MAX_MANIFEST_BYTES:
            return False
        doc = json.loads(content.decode('utf-8'))
        return any(is_svg(clip) for track in doc.get('video_tracks', ())
                   if track.get('kind') == 'gfx' for clip in track.get('clips', ()))
    except (OSError, ValueError, AttributeError, TypeError, RecursionError):
        return False


def context_options(ownership, command, timeline, *, strict=False, truncated=False, window=False, report=False):
    if (sys.platform != 'win32' or window or (command == 'qc' and not strict)
            or command not in ('all', 'validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc')
            or not _has_svg(timeline)):
        return {}
    portable = os.environ.get('VTX_WINDOWS_SVG_PORTABLE')
    scratch = os.environ.get('VTX_WINDOWS_SVG_SCRATCH')
    media_bin = os.environ.get('VTX_WINDOWS_SVG_MEDIA_BIN') or None
    if not portable or not scratch:
        raise ValueError('Windows SVG requires explicit portable and private scratch roots')
    if command != 'gfx' and not media_bin:
        raise ValueError('Windows SVG timeline stages require VTX_WINDOWS_SVG_MEDIA_BIN')
    if truncated:
        raise ValueError('Windows SVG delivery requires full-size CPU rendering without frame truncation')
    if report:
        raise ValueError('Windows SVG delivery does not support audio reports')

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
