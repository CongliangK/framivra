"""Inline-only Windows composite for the locked portable-v2 timeline."""
from contextlib import ExitStack, contextmanager
import inspect
import os
from pathlib import Path
import time

from ..pipeline.composite import RenderPlan, _graph_code_digest
from ..pipeline.infra import Ctx, filter_threads
from ..runtime.windows_timeline_store import target_path
from ..timeline_frame import _FrameError, _digest
from . import gfx_svg as svg, gfx_svg_win_shell as shell
from . import gfx_svg_win_snapshot as snapshot
from .frame_source import PngSequence
from .gfx_environment import _files
from .gfx_svg_win_media import require_standard_timeline
from .gfx_svg_win_runtime import _PNG_LIMIT, _png_digest, owned_runtime


class _FrameCtx(Ctx):
    _core_only_preview = True

    def receipt(self, *args, **kwargs):
        raise RuntimeError('frame preview cannot write delivery receipts')

    def run(self, cmd, need=True, **kwargs):
        media = self._windows_svg_media
        if cmd[0] != 'ffmpeg' or Path(cmd[-1]) != media.scratch / 'composite.png':
            raise _FrameError('write.denied', 'Windows preview permits only its private compositor output')
        result = media.execute(cmd)
        if result.returncode:
            raise _FrameError('render.process', 'Windows preview compositor failed')
        return result


@contextmanager
def _retain_on_sharing_conflict(operation):
    try:
        yield
    except OSError as error:
        if getattr(error, 'winerror', None) in (32, 33):
            operation.keep = True
        raise


def render(path, reference, frame, limit, *, prepare_gfx, trust_html):
    roots = [os.environ.get(name) for name in ('VTX_WINDOWS_SVG_PORTABLE', 'VTX_WINDOWS_SVG_SCRATCH',
                                                'VTX_WINDOWS_SVG_MEDIA_BIN')]
    if not all(roots):
        raise _FrameError('platform.unsupported', 'Windows frame requires the configured portable-v2 native runtime')
    if not prepare_gfx or trust_html:
        raise _FrameError('platform.unsupported', 'Windows frame requires inline sparse SVG preparation; no HTML')
    version = reference['content_version']
    require_standard_timeline(version[len('sha256:'):])
    path = target_path(path)
    portable, scratch, media_bin = roots
    if '%' in scratch:  # FFmpeg image2 treats percent as part of its filename pattern.
        raise _FrameError('environment.unsupported', 'Windows preview scratch must not contain %')
    with ExitStack() as ownership:
        def factory(c, fonts):
            return ownership.enter_context(owned_runtime(c, portable, scratch, fonts, media_bin=media_bin))

        c = _FrameCtx(path, _svg_runtime_factory=factory, _svg_all=True)
        if 'sha256:' + c.timeline_sha256 != version:
            raise _FrameError('snapshot.changed', 'timeline changed during frame operation')
        clip = c.tl['video_tracks'][0]['clips'][0]
        index = frame - clip['dst_in']
        runtime = c._svg_runtime
        record = svg.resolve(c, clip)
        runtime._require_record(c, record)
        c.verify_inputs('frame')
        code_paths = [__file__, inspect.getfile(RenderPlan), inspect.getfile(Ctx),
                      inspect.getfile(type(c._windows_svg_media)), inspect.getfile(PngSequence)]
        code = _files(code_paths)
        threads = filter_threads()

        operation, _, cwd, _, _ = runtime.context
        source_name = record['frames'][index]
        source_bytes = svg.read_input(c, source_name, limit=svg.MAX_SVG_BYTES)
        if svg.digest(source_bytes) != record['files'][source_name]:
            raise _FrameError('inputs.changed', 'selected Windows SVG frame changed before export')
        try:
            shell.fontconfig._empty_catalog_cache(cwd)
        except BaseException:
            operation.keep = True
            raise
        _, directory, names = shell._export_bound_sources(
            runtime.context, [(index, source_bytes)], c.w, c.h, deadline=time.monotonic() + 45,
            capture_limit=8 * 1024 * 1024, held_images=runtime.held_images)
        shell._require_outputs(directory, names)
        png = directory / names[0]
        media = c._windows_svg_media
        with _retain_on_sharing_conflict(operation), snapshot._pin_entry(png) as (handle, info, _, is_directory):
            source_hash = _png_digest(shell._read_pinned(handle, info, is_directory, _PNG_LIMIT), c.w, c.h)
            source = PngSequence(directory, source_hash, clip['dur'], c.fps, c.w, c.h,
                                 environment_check=lambda: {index: source_hash})
            layer = dict(cl=clip, ti=0, gfx=True, source=source, source_stop=index+1, x=0, y=0, w2=None, h2=None)
            plan = RenderPlan(c=c, draft=False, f=1.0, W=c.w, H=c.h, layers=[layer], png_output=True)
            plan.composite([layer], media.scratch / 'composite.png', False, a=frame, b=frame+1)
        with _retain_on_sharing_conflict(media.operation), media.directory.open_read('composite.png') as (_, size):
            if size > limit:
                raise _FrameError('image.limit', 'PNG exceeds requested byte limit')
            data = media.directory.read('composite.png', limit)
        digest = _png_digest(data, c.w, c.h)
        runtime.verify()
        c.verify_inputs('frame')
        if media.operation.keep or _files(code_paths) != code or filter_threads() != threads:
            raise _FrameError('inputs.changed', 'Windows preview implementation or worker authority changed')
        key = _digest(dict(profile='windows-portable-v2-composite/v1', input=c.input_fingerprint,
                           frame=frame, png=source_hash, graph=_graph_code_digest(), code=code, threads=threads))
        result = {'state': 'generated', 'kind': 'current-composite', 'approximate': False, 'path': None,
                  'delivery': 'inline-only', 'mime_type': 'image/png', 'width': c.w, 'height': c.h,
                  'frame': frame, 'fps': {'num': c.frame_rate.numerator, 'den': c.frame_rate.denominator},
                  'content_version': version, 'input_fingerprint': c.input_fingerprint,
                  'identity_semantics': 'locked portable-v2 inputs and native tool identity',
                  'render_key': key, 'cache_hit': False, 'sha256': digest, 'bytes': len(data)}
    return result, data  # Cleanup errors cannot authorize an inline reply.
