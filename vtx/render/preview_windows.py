"""Inline-only native Windows composite; no project or delivery writes."""
from contextlib import ExitStack, contextmanager
import inspect
import json
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace

from ..cache.cache_state import InputIdentityError, verify_input_identity
from ..media.encoders import seg_needs_alpha
from ..pipeline.composite import RenderPlan, _clip_alpha_filters, _graph_code_digest, _media_geometry
from ..pipeline.constraints import fps_expression
from ..pipeline.infra import Ctx, filter_threads
from ..pipeline.stages import validate_core
from ..runtime.windows_timeline_store import target_path
from ..timeline_frame import _FrameError, _digest
from ..timeline_structure import structure_errors
from . import gfx_svg as svg, gfx_svg_win_shell as shell
from . import gfx_svg_win_snapshot as snapshot
from .frame_source import PngSequence
from .gfx_environment import _files
from .gfx_svg_win_media import _STANDARD_TIMELINE, require_standard_timeline
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
    standard = version == 'sha256:' + _STANDARD_TIMELINE
    if not standard:
        _validate_generic_scope(reference)
    path = target_path(path)
    portable, scratch, media_bin = roots
    if '%' in scratch:  # FFmpeg image2 treats percent as part of its filename pattern.
        raise _FrameError('environment.unsupported', 'Windows preview scratch must not contain %')
    if not standard:
        return _render_generic(path, version, frame, limit, portable, scratch, media_bin)
    return _render_standard(path, version, frame, limit, portable, scratch, media_bin)


def _render_standard(path, version, frame, limit, portable, scratch, media_bin):
    require_standard_timeline(version[len('sha256:'):])
    with ExitStack() as ownership:
        def factory(c, fonts):
            return ownership.enter_context(owned_runtime(c, portable, scratch, fonts, media_bin=media_bin))

        c = _FrameCtx(path, _svg_runtime_factory=factory, _svg_all=True, _svg_standard=True)
        if 'sha256:' + c.timeline_sha256 != version:
            raise _FrameError('snapshot.changed', 'timeline changed during frame operation')
        clip = c.tl['video_tracks'][0]['clips'][0]
        index = frame - clip['dst_in']
        runtime = c._svg_runtime
        record = svg.resolve(c, clip)
        runtime._require_record(c, clip, record)
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


def _validate_generic_scope(reference):
    from ..pluginlib.hooks import active_plugins

    stored = json.loads(reference['document']['raw'])
    # Same static preview gates as the non-Windows path: refuse before a Ctx
    # can discover runtimes or probe sources. Windows does not admit BGM JSON.
    active = active_plugins(SimpleNamespace(tl=stored))
    if active:
        raise _FrameError('plugins.unsupported', 'visual preview refuses active plugins: ' +
                          ', '.join(p['name'] for p in active))
    errors = structure_errors(stored, preserve_unmanaged=True)
    if errors:
        detail = '; '.join(errors[:16])
        if len(detail) > 1900:
            detail = detail[:1900] + '…'
        if len(errors) > 16:
            detail += ' (+%d more)' % (len(errors) - 16)
        raise _FrameError('render.structure', 'stored structure errors[%d]: %s' % (len(errors), detail))
    try:
        route = svg.route(SimpleNamespace(tl=stored))
    except ValueError as error:
        raise _FrameError('platform.unsupported', str(error)) from error
    if route != 'svg-sequence':
        raise _FrameError('platform.unsupported', 'Windows preview requires static SVG sequence graphics; no HTML')


class _OwnedFrameCtx(_FrameCtx):
    def __init__(self, path, version, factory):
        self._frame_version = version
        super().__init__(path, _svg_runtime_factory=factory, _svg_all=True, _svg_standard=None)

    def _read_timeline_bytes(self):
        data = super()._read_timeline_bytes()
        if 'sha256:' + svg.digest(data) != self._frame_version:
            raise _FrameError('snapshot.changed', 'timeline changed during frame operation')
        return data

    def verify_inputs(self, stage):
        # Ctx's stage failure path attempts a formal receipt; frame never may.
        try:
            verify_input_identity(self)
        except InputIdentityError as error:
            raise _FrameError('inputs.changed', str(error)) from error

    def run(self, cmd, need=True, **kwargs):
        media = self._windows_svg_media
        target = Path(cmd[-1])
        if cmd[0] == 'ffmpeg' and target == media.scratch / 'composite.png':
            return super().run(cmd, need=need, **kwargs)
        if cmd[0] == 'ffprobe':
            allowed = any(target == self.src_path(s['id']) for s in self.tl['sources'])
        else:
            allowed = (cmd[0] == 'ffmpeg' and target.parent == media.scratch
                       and re.fullmatch(r'source-[0-9]+\.png', target.name))
        if not allowed:
            raise _FrameError('write.denied', 'Windows preview permits only owned probes and private PNG output')
        result = media.execute(cmd)
        if result.returncode:
            raise _FrameError('render.process', 'Windows preview source decode/probe failed')
        return result


def _native_layer(c, pins, clip, ti, frame):
    """Use the core clip transform and clock, without a delivery seg encode."""
    media = c._windows_svg_media
    _, x, y, width, height, geometry = _media_geometry(c, clip)
    w, h = (c.w, c.h) if width is None else (width, height)
    if min(w, h) < 2:
        raise _FrameError('render.validation', 'native window scale produces dimensions below 2 pixels')
    path = c.src_path(clip['src'])
    alpha = seg_needs_alpha(clip, c.probe(path))
    index = frame - clip['dst_in']
    filters = [f"trim=start_frame={clip['src_in']}:end_frame={clip['src_out']}", 'setpts=PTS-STARTPTS',
               f'fps={fps_expression(c.fps)}', *geometry, 'format=yuva420p' if alpha else 'format=yuv420p']
    if alpha:
        filters += _clip_alpha_filters(clip, c.spf)
    # The delivery compositor extends a certified short (nonempty) segment
    # with its last decoded frame. Do so after clip-local alpha/fade filters.
    filters += [f'tpad=stop_mode=clone:stop={clip["dur"]}',
                f'trim=start_frame={index}:end_frame={index + 1}', 'setpts=PTS-STARTPTS']
    leaf = f'source-{ti}.png'
    output = media.scratch / leaf
    c.run(['ffmpeg', '-v', 'error', '-nostdin', '-xerror', '-n', '-i', str(path), '-map', '0:v:0',
           '-an', '-vf', ','.join(filters), '-frames:v', '1', '-c:v', 'png', '-threads', '1',
           '-pix_fmt', 'rgba' if alpha else 'rgb24', '-f', 'image2', '-update', '1', str(output)])
    handle, size = pins.enter_context(media.directory.open_read(leaf))
    digest = _png_digest(b''.join(media.directory.chunks(handle, size, limit=_PNG_LIMIT)), w, h)
    # The preparatory graph already applied geometry and alpha on the full clip
    # clock. Its verified singleton is now a native layer placed at this frame.
    layer = dict(cl={'dst_in': frame, 'dur': 1}, ti=ti, gfx=False, seg=output, x=x, y=y, w2=width, h2=height)
    return layer, digest


def _render_generic(path, version, frame, limit, portable, scratch, media_bin):
    with ExitStack() as ownership:
        def factory(c, fonts):
            return ownership.enter_context(owned_runtime(c, portable, scratch, fonts, media_bin=media_bin))

        c = _OwnedFrameCtx(path, version, factory)
        media = c._windows_svg_media
        code_paths = [__file__, inspect.getfile(RenderPlan), inspect.getfile(Ctx),
                      inspect.getfile(type(media)), inspect.getfile(PngSequence),
                      inspect.getfile(seg_needs_alpha), inspect.getfile(validate_core)]
        code = _files(code_paths)
        threads = filter_threads()
        errors = validate_core(c)
        if errors:
            raise _FrameError('render.validation', '; '.join(errors[:16]))
        c.verify_inputs('frame')
        layers, identities, runtimes = [], [], []
        with ExitStack() as pins:
            pins.enter_context(_retain_on_sharing_conflict(media.operation))
            for ti, track in enumerate(c.tl['video_tracks']):
                for ci, clip in enumerate(track['clips']):
                    index = frame - clip['dst_in']
                    if not 0 <= index < clip['dur']:
                        continue
                    if track['kind'] == 'gfx':
                        runtime, record = pins.enter_context(svg._resolved_runtime(c, clip))
                        if not any(runtime is other for other in runtimes):
                            runtimes.append(runtime)
                        directory, digest = pins.enter_context(runtime.sparse_frame(c, clip, record, index))
                        source = PngSequence(directory, digest, clip['dur'], c.fps, c.w, c.h,
                                             environment_check=lambda i=index, d=digest: {i: d})
                        layer = dict(cl=clip, ti=ti, gfx=True, source=source, source_stop=index + 1,
                                     x=clip.get('x', 0), y=clip.get('y', 0), w2=None, h2=None)
                    else:
                        layer, digest = _native_layer(c, pins, clip, ti, frame)
                    layers.append(layer)
                    identities.append({'track': ti, 'clip': ci, 'frame': index, 'png': digest})
            output = media.scratch / 'composite.png'
            plan = RenderPlan(c=c, draft=False, f=1.0, W=c.w, H=c.h, layers=layers, png_output=True)
            # A single graph is sufficient for one frame and doesn't enter the
            # delivery pass cache/certificate flow (even for an empty canvas).
            plan._composite_graph(layers, output, getattr(c, '_windows_subtitles', None) is not None,
                                  a=frame, b=frame + 1)
            with media.directory.open_read(output.name) as (handle, size):
                if size > limit:
                    raise _FrameError('image.limit', 'PNG exceeds requested byte limit')
                data = b''.join(media.directory.chunks(handle, size, limit=limit))
            digest = _png_digest(data, c.w, c.h)
            for runtime in runtimes:
                runtime.verify()
            c.verify_inputs('frame')
            if media.operation.keep or _files(code_paths) != code or filter_threads() != threads:
                raise _FrameError('inputs.changed', 'Windows preview implementation or worker authority changed')
            key = _digest(dict(profile='windows-svg-single-graph/v1', input=c.input_fingerprint,
                               frame=frame, layers=identities, graph=_graph_code_digest(), code=code,
                               threads=threads, media=media.identity))
        result = {'state': 'generated', 'kind': 'current-composite', 'approximate': False, 'path': None,
                  'delivery': 'inline-only', 'mime_type': 'image/png', 'width': c.w, 'height': c.h,
                  'frame': frame, 'fps': {'num': c.frame_rate.numerator, 'den': c.frame_rate.denominator},
                  'content_version': version, 'input_fingerprint': c.input_fingerprint,
                  'identity_semantics': 'Pinned Windows SVG/source bytes and owned native tool identity',
                  'render_key': key, 'cache_hit': False, 'sha256': digest, 'bytes': len(data)}
    return result, data
