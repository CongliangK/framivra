"""Internal CLI candidate; not a public render API or a delivery receipt."""
import base64
from contextlib import ExitStack, contextmanager
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace
import wave

from .runtime import fstxn, preview_window_isolation as isolation
from .timeline import _error, _load
from .timeline_reference import _reference_loaded, _wire_size

SCHEMA = 'framivra.timeline.preview-window/v1'
WARM_SVG_SCHEMA = 'framivra.preview-window.warm-svg/v1'
MEDIA_LIMIT = 5 * 1024 * 1024
WIRE_LIMIT = 8 * 1024 * 1024
_WORK_FILE_LIMIT = 32 * 1024 * 1024
_WORK_FILE_COUNT = 24
_WORK_MEDIA_LIMIT = _WORK_FILE_LIMIT * _WORK_FILE_COUNT
_VERSION = re.compile(r'sha256:[0-9a-f]{64}')
_SOURCE_FORMATS = 'mov,matroska,wav,mp3,flac,ogg,aac,avi,aiff,mpegts'
_TOOLS = ('ffmpeg', 'ffprobe', 'ldd', 'fc-list', 'fc-conflist')
_UNSAFE_ENV = {'FFREPORT', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE',
               'BASH_ENV', 'ENV', 'GCONV_PATH', 'LOCPATH', 'MALLOC_TRACE', 'SSLKEYLOGFILE',
               'FRAMIVRA_VERIFY_ROOT'}


class WindowError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def error_result(code, message):
    result = {'schema': SCHEMA, 'ok': False, 'diagnostics': []}
    _error(result, code, '', message)
    return result


def supported():
    if (sys.platform != 'linux' or not fstxn.directory_write_capability()
            or fstxn.write_capability() != 'fd-anchored'
            or os.rmdir not in os.supports_dir_fd or os.listdir not in os.supports_fd
            or not isolation.available()):
        return False
    from .pipeline import range as range_pipeline
    return callable(getattr(range_pipeline, 'render_1080p_window', None))


def _safe_environment(font_config=None):
    present = sorted(k for k in os.environ if k in _UNSAFE_ENV or k.startswith(
        ('LD_', 'DYLD_', 'FONTCONFIG_', 'FC_', 'GLIBC_TUNABLES', 'BASH_FUNC_')))
    if font_config is not None and os.environ.get('FONTCONFIG_FILE') == font_config:
        present.remove('FONTCONFIG_FILE')
    if os.environ.get('VTX_GFX', 'cdp') != 'cdp':
        present.append('VTX_GFX')
    if present:
        raise WindowError('environment.unsupported', 'unsupported environment: ' + ', '.join(present))
    # Downstream kernel calls also invoke these tools directly. Accept only the
    # system tools, not PATH wrappers or an experimental environment's copies.
    for name in _TOOLS:
        selected = shutil.which(name)
        system = Path('/usr/bin') / name
        if not selected or Path(selected).resolve(strict=True) != system.resolve(strict=True):
            raise WindowError('environment.unsupported', 'system tool required: ' + name)


def _plugins(stored):
    from .pluginlib.hooks import active_plugins, _scope_items
    from .pluginlib.registry import key_map
    active = active_plugins(SimpleNamespace(tl=stored))
    declared = {info['plugin'] for key, info in key_map().items()
                if any(isinstance(item, dict) and key in item
                       for item in _scope_items(stored, info['path']))}
    rejected = declared | {p['name'] for p in active}
    if rejected:
        raise WindowError('plugins.unsupported', 'preview window refuses declared/active plugins: '
                          + ', '.join(sorted(rejected)))


def _preflight(stored, start, font_config=None, *, allow_svg=False, svg_read_only=False):
    from .pipeline.constraints import parse_fps
    from .timeline_structure import structure_errors
    errors = structure_errors(stored, preserve_unmanaged=True)
    if errors:
        raise WindowError('render.structure', f'{len(errors)} stored structure errors: '
                          + '; '.join(errors[:16])[:1800])
    _plugins(stored)
    project = stored['project']
    if (project['width'], project['height']) != (1920, 1080):
        raise WindowError('render.resolution', 'preview window requires a 1920x1080 project')
    if svg_read_only and 'subtitles_src' in stored:
        raise WindowError('subtitles.unsupported', 'standalone warm SVG read requires inline subtitles')
    rate = parse_fps(project['fps'])
    end = min(project['duration_frames'], start + (rate.numerator + rate.denominator - 1) // rate.denominator)
    from .runtime import preview_window_svg as svg
    gfx = svg.clips(stored)
    if allow_svg:
        try:
            clip = svg.selection(stored, start, end)
        except (ValueError, isolation.IsolationError) as error:
            raise WindowError('gfx.unsupported', str(error)) from error
        if clip is None:
            raise WindowError('gfx.unsupported', 'warm read requires an intersecting SVG clip')
        if os.environ.get('VTX_INKSCAPE') or os.environ.get('VTX_GFX_CACHE_NS'):
            raise WindowError('environment.unsupported', 'SVG window refuses renderer and namespace overrides')
    elif gfx:
        if any(clip['dst_in'] < end and start < clip['dst_in'] + clip['dur'] for clip in gfx):
            raise WindowError('gfx.unsupported', 'preview window intersects a GFX clip')
        raise WindowError('gfx.unsupported', 'preview window cannot verify off-window graphics yet')
    _safe_environment(font_config)
    return end, rate


@contextmanager
def _request(root, root_identity, result, *, epoch=False):
    """Create exclusively; prove removal of the original inode, not a pathname."""
    from .runtime.proc_supervisor import WORKER_LEDGER
    from .render.png_artifact import project_identity

    # Qualified workers have already verified a fresh independent tmpfs.
    # Keep the *actual* Fontconfig cache path stable across those namespaces,
    # not a redacted path in the certificate. Creation is still exclusive.
    directory = root / 'cache' / 'preview' / ('window_input_v1' if epoch else 'window_' + secrets.token_hex(16))
    # Reject linked ancestors before pinned_parent's write-path normalization.
    fstxn.cleanup_identity(directory)
    if project_identity(root) != root_identity:
        raise WindowError('snapshot.changed', 'project root identity changed before request creation')
    with fstxn.pinned_parent(directory, root=root) as (parent, name, canonical):
        parent_identity = fstxn.cleanup_identity(directory.parent)
        if (canonical != directory or parent_identity[:len(root_identity)] != root_identity
                or parent_identity[-1] != fstxn.entry_identity(os.fstat(parent))):
            raise WindowError('write.denied', 'preview namespace identity changed')
        os.mkdir(name, mode=0o700, dir_fd=parent)  # no exist_ok, even on a token collision
        fd = identity = None
        failed = False
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise WindowError('write.denied', 'request directory is not privately owned')
            identity = parent_identity + (fstxn.entry_identity(info),)
            if fstxn.cleanup_identity(directory) != identity:
                raise WindowError('write.denied', 'preview request namespace changed')
            yield directory, identity
        except BaseException:
            failed = True
            raise
        finally:
            try:
                if fd is None or fstxn.cleanup_identity(directory) != identity:
                    raise WindowError('render.unsettled', 'request identity lost; cleanup not proven')

                def inaccessible(error):
                    raise error

                if any(WORKER_LEDGER in dirs or WORKER_LEDGER in files
                       for _, dirs, files in os.walk(directory, onerror=inaccessible)):
                    raise WindowError('render.unsettled', 'worker recovery ledger remains; request retained')
                fstxn.remove_pinned(directory, identity, preserve=lambda p: p.name == WORKER_LEDGER,
                                    retain_if_present=(directory, WORKER_LEDGER))
                if os.fstat(fd).st_nlink != 0:
                    raise WindowError('render.unsettled', 'request removal not proven')
            except (OSError, ValueError, RuntimeError) as error:
                if not failed:
                    raise WindowError('render.unsettled', str(error)) from error
                # Do not let a cleanup exception replace a producer failure.
                _error(result, 'render.unsettled', '', str(error)[:1000])
            finally:
                if fd is not None:
                    os.close(fd)


def _admit(c, start, end, *, program_sources=0):
    """Conservative cold-work gate, not aggregate filesystem quota/reservation."""
    from .pipeline.composite import DEFAULT_SEGMENTS
    from .pipeline.range import AUDIO_MARGIN_FRAMES

    layers = [(track['kind'], clip) for track in c.tl['video_tracks'] for clip in track['clips']
              if clip['dst_in'] < end and start < clip['dst_in'] + clip['dur']]
    clips = [clip for kind, clip in layers if kind != 'gfx']
    sounds = set() if program_sources else {
        ev['src'] for track in c.tl['audio_tracks'] for ev in track['events']
        if ev['in'] < end + AUDIO_MARGIN_FRAMES and start - AUDIO_MARGIN_FRAMES < ev['out']}
    # Each program source copy + extraction, plus full mix. The normal +3
    # already includes the window PCM.
    audio_files = 2 * program_sources + 1 if program_sources else len(sounds)
    segments = int(os.environ.get('VTX_VIDEO_SEGMENTS', str(DEFAULT_SEGMENTS)))
    # Full-clip decode bounds, candidate-local pass count, and estimated PCM
    # bytes. A video-native source can have nonmatching A/V lengths: actual
    # FFmpeg writes also have a per-file OS cap, not just this estimate.
    estimates = [c.sample_at(c.probe(c.src_path(sid))['nb_frames']) * 4 for sid in sounds]
    if (not 1 <= segments <= _WORK_FILE_COUNT or sum(cl['src_out'] for cl in clips) > 3600
            or len(clips) + min(segments, end - start) * max(1, len(layers)) + audio_files + 3 > _WORK_FILE_COUNT
            or any(estimate > _WORK_FILE_LIMIT for estimate in estimates)):
        raise WindowError('work.limit', 'cold preview work exceeds the candidate resource budget')


def _expected_pcm(data, samples):
    with wave.open(io.BytesIO(data), 'rb') as wav:
        if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth(), wav.getnframes()) != (2, 48000, 2, samples):
            raise WindowError('media.audio_clock', 'mixed PCM does not match exact window sample endpoints')
        if len(wav.readframes(samples)) != samples * 4:
            raise WindowError('media.audio_clock', 'mixed PCM is truncated')


def _probe_facts(info, rate, frames, samples, *, verify_dts=False):
    streams = info['streams']
    video = [s for s in streams if s.get('codec_type') == 'video']
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    if len(streams) != 2 or len(video) != 1 or len(audio) != 1:
        raise WindowError('media.streams', 'expected exactly one video and one audio stream')
    video, audio = video[0], audio[0]
    dims = (video['width'], video['height'])
    sound = (int(audio['sample_rate']), audio['channels'])
    if video['codec_name'] != 'h264' or dims != (1920, 1080) or audio['codec_name'] != 'aac' or sound != (48000, 2):
        raise WindowError('media.streams', 'expected 1080p H.264 and 48 kHz stereo AAC')
    vt, at = Fraction(video['time_base']), Fraction(audio['time_base'])
    if (int(video['start_pts']) != 0 or int(video['duration_ts']) * vt != frames / rate
            or Fraction(video['avg_frame_rate']) != rate):
        raise WindowError('media.video_clock', 'video stream does not span the exact frame interval')
    if at != Fraction(1, sound[0]) or int(audio['start_pts']) != 0 or int(audio['duration_ts']) != samples:
        raise WindowError('media.audio_clock', 'AAC presentation does not match the mixed sample window: '
                          f'time_base={at}, start={audio["start_pts"]}, samples={audio["duration_ts"]}, expected={samples}')
    # Reordered H.264 (B frames) is allowed; use presentation, not decode order.
    packets = info['packets']
    vp = [p for p in packets if p['stream_index'] == video['index']]
    ap = [p for p in packets if p['stream_index'] == audio['index']]
    if len(vp) + len(ap) != len(packets) or len(vp) != frames:
        raise WindowError('media.video_clock', 'packet counts do not match the certified video window')
    if (sorted(int(p['pts']) * vt for p in vp) != [i / rate for i in range(frames)]
            or any(int(p['duration']) * vt != 1 / rate for p in vp)):
        raise WindowError('media.video_clock', 'video packets are not on the exact frame clock')
    if verify_dts:
        # Decode order is per video stream, not the interleaved A/V packet index.
        # B-frame reordering can start DTS below zero without shifting PTS.
        step = 1 / (rate * vt)
        d0 = int(vp[0]['dts'])
        if (step.denominator != 1 or d0 > 0 or d0 % step
                or any(int(p['dts']) != d0 + i * step or int(p['dts']) > int(p['pts'])
                       for i, p in enumerate(vp))):
            raise WindowError('media.video_clock', 'video packets do not have a continuous decode clock')
    cursor = 0
    for i, packet in enumerate(ap):
        pts, duration = int(packet['pts']), int(packet['duration'])
        if i == 0 and pts < 0:
            priming = [s for s in packet.get('side_data_list', []) if s.get('side_data_type') == 'Skip Samples']
            if len(priming) != 1 or int(priming[0]['skip_samples']) != -pts or duration != -pts:
                raise WindowError('media.audio_clock', 'AAC priming does not end at the zero presentation origin')
            continue
        if pts != cursor or not 0 < duration <= 1024:
            raise WindowError('media.audio_clock', 'AAC packet presentation has a gap or overlap')
        cursor += duration
    # AAC's decoded frame size includes padding; it is NOT a sample-clock proof.
    if cursor != samples:
        raise WindowError('media.audio_clock', 'AAC packet presentation does not match the mixed sample window')
    return {'width': dims[0], 'height': dims[1], 'audio_sample_rate': sound[0], 'audio_channels': sound[1]}


def _context(path, check_cancelled, workspace_env, read_timeline, read_subtitles=None, *, epoch_sources=(),
             program_copies=None):
    from .cache.recovery import atomic_ffmpeg
    from .pipeline.infra import Ctx, is_ffmpeg_cmd
    from .cache.cache_state import verify_input_identity

    copies = program_copies or {}
    targets = set(copies.values())
    pcm_inputs = {str(path): str(copy) for path, copy in copies.items()}
    if read_subtitles is not None:
        subtitles = read_subtitles()
        initial_digest = hashlib.sha256(subtitles).hexdigest()

        def subtitles_digest():
            nonlocal initial_digest
            # Ctx's initial receipt and parser consume one snapshot. Each
            # later verification, starting just after parse, reads the same FD.
            if initial_digest is not None:
                digest, initial_digest = initial_digest, None
                return digest
            return hashlib.sha256(read_subtitles()).hexdigest()

    class WindowCtx(Ctx):
        # Guard every internal hook lookup from the very first fingerprint. The
        # real Ctx constructor and validate_core still run in full.
        _core_only_preview = True
        _program_audio_verified = bool(copies)
        write_root = None
        if read_subtitles is not None:
            _read_subtitles_bytes = staticmethod(lambda: subtitles)
            _subtitles_input_digest = staticmethod(subtitles_digest)

        def receipt(self, *args, **kwargs):
            raise WindowError('write.denied', 'window preview cannot write delivery receipts')

        def _read_timeline_bytes(self):
            return read_timeline()

        def verify_inputs(self, stage):
            check_cancelled()
            verify_input_identity(self)

        def probe(self, path):
            if not Path(path).is_file():
                raise WindowError('inputs.unsupported', 'media input must be a regular file')
            return super().probe(copies.get(Path(path), path))

        def _preview_media_target(self, target):
            if (self.write_root is None or self.write_root not in target.parents or target.resolve() != target):
                raise WindowError('write.denied', 'renderer output escaped private preview workspace')
            targets.add(target)
            if len(targets) > _WORK_FILE_COUNT:
                raise WindowError('work.limit', 'too many preview media outputs')

        @staticmethod
        def _preview_window_tasks(c, tasks, what):
            # Serial and cancellable; use exactly the core-produced argv.
            for _label, cmd, nvenc in tasks:
                if nvenc:
                    raise WindowError("environment.unsupported", "window preview is CPU-only")
                c.run(cmd)

        @staticmethod
        def _preview_ffmpeg_args(cmd):
            if not epoch_sources:
                return cmd
            # Actual decode of project inputs must use the same ordinary
            # demuxer set as the probe, not e.g. concat/playlist indirection.
            guarded = []
            for i, arg in enumerate(cmd):
                if arg == '-i' and cmd[i + 1] in pcm_inputs:
                    guarded.extend(('-protocol_whitelist', 'file', '-format_whitelist', 'wav', '-f', 'wav'))
                elif arg == '-i' and cmd[i + 1] in epoch_sources:
                    guarded.extend(('-protocol_whitelist', 'file', '-format_whitelist', _SOURCE_FORMATS))
                guarded.append(pcm_inputs.get(arg, arg) if i and cmd[i - 1] == '-i' else arg)
            return guarded

        def run(self, cmd, need=True, **kwargs):
            check_cancelled()
            if is_ffmpeg_cmd(cmd):
                if cmd[-1] not in ('-', os.devnull):
                    self._preview_media_target(Path(cmd[-1]))
                cmd = self._preview_ffmpeg_args(cmd)
                relay = getattr(self, '_program_seg_relay', None)
                if need and relay is not None:
                    committed = relay.capture(self, cmd)
                    if committed is not None:
                        if kwargs.get('after_commit') is not None:
                            raise WindowError('media.invalid', 'segment relay requires an owned completion callback')
                        kwargs['after_commit'] = committed
            elif cmd[0] == 'ffprobe':
                cmd = [cmd[0], '-protocol_whitelist', 'file', '-format_whitelist', _SOURCE_FORMATS, *cmd[1:]]
            execute = lambda args: _process(args, check_cancelled, env=workspace_env)
            value = (atomic_ffmpeg(self, cmd, execute, inspect_execute=execute, **kwargs)
                     if need else execute(cmd))
            check_cancelled()
            if need and value.returncode:
                raise WindowError('render.process', 'renderer/probe failed: ' + value.stderr[-1500:])
            return value

    return WindowCtx(path)


def _process(argv, check_cancelled, *, env=None, data=None, timeout=None):
    """Own/reap the direct command; no detached FFmpeg outside Host supervision."""
    import time

    check_cancelled()
    deadline = time.monotonic() + timeout if timeout is not None else None
    with subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as child:
        try:
            while True:
                try:
                    output, error = child.communicate(data, timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    data = None  # communicate remembers the pending stdin bytes
                    check_cancelled()
                    if deadline is not None and time.monotonic() >= deadline:
                        raise WindowError('environment.unavailable', 'environment probe timed out')
        except BaseException:
            child.kill()
            child.communicate()
            raise
    return subprocess.CompletedProcess(argv, child.returncode, output.decode('utf-8', 'replace'),
                                       error.decode('utf-8', 'replace'))


def _project_files(path, stored, *, svg=False, program_audio=False):
    from .cache.cache_state import all_declared_dependencies

    root = path.parent
    total, files = 0, {path: [('timeline',)]}

    def no_derived_input(candidate):
        if program_audio and any(directory == candidate or directory in candidate.parents
                                 for directory in (root / 'out', root / 'cache')):
            raise WindowError('audio.program_unsupported', 'program audio refuses declared output/cache inputs')

    declared = [(s['path'], ('source', s['id'], s['path'])) for s in stored['sources']]
    declared.extend((name, ('dependency', name))
                    for name in all_declared_dependencies(SimpleNamespace(tl=stored)))
    if svg:
        from .runtime.preview_window_svg import declarations
        declared.extend(declarations(root, stored))
    for name, role in declared:
        candidate = root / name
        no_derived_input(candidate)
        if svg and (candidate == root / 'cache' or root / 'cache' in candidate.parents):
            raise WindowError('inputs.unsupported', 'SVG warm inputs cannot declare files in the cache tree')
        if (root not in candidate.parents or '..' in candidate.parts
                or candidate.resolve(strict=True) != candidate or not candidate.is_file()):
            raise WindowError('inputs.unsupported', 'declared inputs must be regular project files')
        if candidate not in files or files[candidate] == [('timeline',)]:
            total += candidate.stat().st_size
            if total > _WORK_MEDIA_LIMIT:
                raise WindowError('work.limit', 'declared files exceed the candidate input budget')
        files.setdefault(candidate, []).append(role)

    caption = None
    if src := stored.get('subtitles_src'):
        relative = Path(src)
        if ('\0' in src or relative.is_absolute() or '..' in relative.parts
                or relative.suffix.lower() not in ('.srt', '.json')):
            raise WindowError('subtitles.unsupported', 'external subtitles require a project-relative .srt or .json')
        caption = root / relative
        no_derived_input(caption)
        cache = root / 'cache'
        if svg and (caption == cache or cache in caption.parents):
            raise WindowError('inputs.unsupported', 'SVG warm inputs cannot declare files in the cache tree')
        scratch = cache / 'preview'
        if caption == scratch or scratch in caption.parents or caption in scratch.parents:
            raise WindowError('subtitles.unsupported', 'external subtitles overlap the isolated scratch mount')
        if caption in files:
            raise WindowError('subtitles.unsupported', 'external subtitles cannot share another input role')
        files[caption] = [('subtitles_src', src)]
    return files, caption, min(WIRE_LIMIT, _WORK_MEDIA_LIMIT - total)


def _caption_reader(fd, guard, check_cancelled, *, limit=WIRE_LIMIT):
    """Read only the pinned regular file; never reopen its pathname for bytes."""
    def read_subtitles():
        guard()
        size = os.fstat(fd).st_size
        if size > limit:
            raise WindowError('work.limit', 'external subtitles exceed the candidate byte limit')
        data = bytearray()
        while len(data) <= size:
            check_cancelled()
            chunk = os.pread(fd, min(65536, size + 1 - len(data)), len(data))
            if not chunk:
                break
            data.extend(chunk)
        guard()
        if len(data) != size:
            raise WindowError('snapshot.changed', 'external subtitles changed during read')
        return bytes(data)

    return read_subtitles


def _timeline_reader(path, root_identity, version, check_cancelled):
    from .render.png_artifact import read_artifact
    from .timeline_frame import _hash

    def read_timeline():
        check_cancelled()
        payload = read_artifact(path, root=path.parent, limit=WIRE_LIMIT, root_identity=root_identity)
        if 'sha256:' + _hash(payload) != version:
            raise WindowError('snapshot.changed', 'timeline changed during preview window')
        return payload

    return read_timeline


def _dispatch(path, reference, root_identity, start, check_cancelled, qualification=None, *, svg_mode=None,
              program_audio=False, first_frame=None):
    from .cache.cache_state import receipt_fingerprint, verify_input_identity
    from .timeline_structure import strict_loads

    stored = strict_loads(reference['document']['raw'].encode('utf-8'))
    version = reference['content_version']
    allow_svg = svg_mode is not None
    if allow_svg and qualification is None:
        raise WindowError('gfx.unsupported', 'warm SVG read requires an explicitly qualified fixed wheel')
    if program_audio and (qualification is None or svg_mode == 'svg-read'):
        raise WindowError('audio.program_unsupported', 'program audio requires qualified whole-program media')
    end, rate = _preflight(stored, start, isolation.managed_font(), allow_svg=allow_svg,
                           svg_read_only=svg_mode == 'svg-read')
    if first_frame is not None:
        if allow_svg or program_audio or qualification is None:
            raise WindowError('input_epoch.unavailable', 'first frame requires the qualified ordinary window supervisor')
        from .pipeline.composite import DEFAULT_SEGMENTS, choose_cutpoints
        from .pipeline.range import select_segments

        def position(env):
            count = int(env.get('VTX_VIDEO_SEGMENTS', str(DEFAULT_SEGMENTS)))
            if not 1 <= count <= _WORK_FILE_COUNT:
                raise WindowError('work.limit', 'invalid preview segment count')
            bounds = [(clip['dst_in'], clip['dst_in'] + clip['dur'])
                      for track in stored['video_tracks'] for clip in track['clips']]
            duration = stored['project']['duration_frames']
            cuts = choose_cutpoints(bounds, duration, count)
            (_, segment_end), *_ = select_segments([0, *cuts, duration], start, end)
            return end, segment_end, rate

        nonce, reverify_profile, emit = first_frame
        first_frame = (nonce, position, reverify_profile, emit)
    program_paths = ()
    if program_audio:
        from .pipeline import preview_program_audio as program

        program_paths = tuple(program.inputs(stored, path.parent, rate).values())
    read_timeline = _timeline_reader(path, root_identity, version, check_cancelled)
    files, caption, caption_limit = _project_files(path, stored, svg=allow_svg, program_audio=program_audio)
    pin = None
    if not allow_svg:
        pin = SimpleNamespace(tl=stored, root=path.parent, tl_path=path, _core_only_preview=True,
                              _read_timeline_bytes=read_timeline)

    def read_caption(fd, guard):
        data = _caption_reader(fd, guard, check_cancelled, limit=caption_limit)()
        return len(data), hashlib.sha256(data).hexdigest()

    def pin_inputs(caption_digest, project_digest):
        # The callback always reads through the same bindings, also on recheck.
        pin._project_input_digest = project_digest
        if caption is not None:
            pin._subtitles_input_digest = lambda: caption_digest
        pin.input_fingerprint = receipt_fingerprint(pin)
        return pin.input_fingerprint

    def recheck(caption_digest):
        _plugins(stored)
        if pin is not None:
            if caption is not None:
                pin._subtitles_input_digest = lambda: caption_digest
            verify_input_identity(pin)
        elif _project_files(path, stored, svg=True)[0] != files:
            raise WindowError('snapshot.changed', 'declared SVG inputs changed during projection')
        read_timeline()

    with ExitStack() as stack:
        result = isolation.execute(stack, path, files, start, version, pin_inputs if pin is not None else None,
                                   _WORK_MEDIA_LIMIT, WIRE_LIMIT, check_cancelled,
                                   root_identity=root_identity, qualification=qualification, recheck=recheck,
                                   svg_mode=svg_mode, caption=caption, read_caption=read_caption,
                                   program_paths=program_paths,
                                   **({"first_frame": first_frame} if first_frame is not None else {}))
    schema = WARM_SVG_SCHEMA if svg_mode == 'svg-read' else SCHEMA
    if svg_mode == 'svg-media' and not result['ok'] and result.get('schema') == WARM_SVG_SCHEMA:
        # The shared input-only planner uses the warm-read envelope on failure.
        return {'schema': SCHEMA, 'ok': False, 'diagnostics': result['diagnostics']}
    if (result.get('schema') != schema or result['ok'] and
            (result.get('content_version') != version or result.get('start') != start
             or (svg_mode == 'svg-media' and result.get('end') != end))):
        raise isolation.IsolationError('isolated result identity rejected')
    return result


def _svg_projection(path, reference, root_identity, start, check, expected_inputs, *, mode, relative):
    from .cache.cache_state import verify_input_identity
    from .runtime import preview_window_svg as svg
    from .render import gfx_packed_reader as reader
    from .timeline_structure import strict_loads

    root = path.parent
    isolation.check_mount(root / 'cache' / 'preview', _WORK_MEDIA_LIMIT)
    svg.check_tools()
    stored = strict_loads(reference['document']['raw'].encode('utf-8'))
    # The planner gets a digest only for explicitly authorized warm media.
    # The digest-only path always uses the old no-external-subtitles contract.
    plan_caption = mode == 'svg-plan' and expected_inputs != '-'
    end, rate = _preflight(stored, start, allow_svg=True, svg_read_only=not plan_caption)
    _, caption, caption_limit = _project_files(path, stored, svg=True)
    read_timeline = _timeline_reader(path, root_identity, reference['content_version'], check)
    if mode == 'svg-plan':
        if plan_caption != (caption is not None):
            raise WindowError('snapshot.changed', 'planned external subtitles do not match the projection')
        with ExitStack() as stack:
            caption_digest = None
            if caption is not None:
                binding = isolation._pin_binding(stack, caption, root, single_link=True, root_identity=root_identity)
                read_subtitles = _caption_reader(*binding, check, limit=caption_limit)

                def caption_digest():
                    digest = hashlib.sha256(read_subtitles()).hexdigest()
                    if digest != expected_inputs:
                        raise WindowError('snapshot.changed', 'planned external subtitles changed during projection')
                    return digest

            return {'ok': True, **svg.plan(root, stored, path, read_timeline, start, end,
                                          caption_digest=caption_digest)}
    clip = svg.selection(stored, start, end)
    pack = svg.pack_selector(root, relative)
    c = SimpleNamespace(tl=stored, root=root, tl_path=path, cache=root / 'cache', fps=rate,
                        w=1920, h=1080, _core_only_preview=True, _read_timeline_bytes=read_timeline,
                        input_fingerprint=expected_inputs)
    offset = int(clip['dst_in'])
    first, last = max(start, offset) - offset, min(end - offset, clip['dur'])
    rechecks = []
    frames = reader.read_frames(c, clip, first, last, check_cancel=check,
                                keep_recheck=rechecks.append, expected_svg_pack=pack)
    digests = [hashlib.sha256(frame).hexdigest() for frame in frames]
    for recheck in rechecks:
        recheck()
    verify_input_identity(c)
    read_timeline()
    check()
    return {'schema': WARM_SVG_SCHEMA, 'ok': True, 'content_version': reference['content_version'],
            'start': start, 'end': end, 'clip_start': first, 'clip_stop': last, 'png_sha256': digests}


def _program_windows(stored, start, rate, count, *, part_seconds=1):
    frames = 30 * part_seconds
    stop = start + frames * count
    if rate != Fraction(30, 1) or start < 0 or stop > stored['project']['duration_frames']:
        duration = 'one-second' if part_seconds == 1 else f'{part_seconds}-second'
        raise WindowError('render.range', f'program run requires complete adjacent 30/1 {duration} windows')
    return tuple((a, a + frames) for a in range(start, stop, frames))


def _program_projection(stack, path, root_identity, files, version, check):
    """Rehash every projected input on every checkpoint, not just Ctx media stats."""
    if len(files) > isolation._PROJECT_FILE_COUNT:
        raise WindowError('work.limit', 'too many separately anchored project files')
    bindings = [(item, roles, *isolation._pin_binding(stack, item, path.parent,
                 single_link=True, root_identity=root_identity)) for item, roles in sorted(files.items())]

    def observe():
        total, observed = 0, []
        for item, roles, fd, guard in bindings:
            limit = WIRE_LIMIT if item == path else _WORK_MEDIA_LIMIT - total
            size, digest = isolation.binding_digest(fd, guard, limit, check)
            if item == path:
                if 'sha256:' + digest != version:
                    raise WindowError('snapshot.changed', 'projected timeline changed during program run')
            else:
                total += size
            observed.append((str(item), roles, size, digest))
        # Recheck early descriptors again after the last read.
        for _, _, _, guard in bindings:
            guard()
        return isolation.identity_digest('framivra.preview-window.program-run-projection/v1', observed)

    return observe


def _verified_media(output, workspace, request, identity, start, end, rate, check, *, limit, verify_dts=False):
    from .cache.recovery import completion_marker
    from .pipeline.constraints import frame_sample
    from .render.png_artifact import read_artifact
    from .timeline_frame import _hash

    if output.parent != workspace or output.suffix != '.mp4':
        raise WindowError('media.invalid', 'producer did not return its private MP4 artifact')

    def artifact(path, limit):
        return read_artifact(path, root=request, root_identity=identity, limit=limit)

    data = artifact(output, limit)
    digest = _hash(data)
    if (len(data) < 16 or data[4:8] != b'ftyp' or data[8:12] == b'qt  '
            or json.loads(artifact(completion_marker(output), 4096)) !=
            {'schema': 1, 'sha256': digest, 'frames': end - start}):
        raise WindowError('media.invalid', 'MP4 bytes do not match the decoded media certificate')
    samples = frame_sample(end, rate) - frame_sample(start, rate)
    pcm = artifact(workspace / 'work' / 'preview' / output.with_suffix('.wav').name,
                   samples * 4 + 4096)
    _expected_pcm(pcm, samples)
    # Probe the exact anchored bytes, never a second, pathname-based read of
    # the renderer's result. Faststart allows nonseekable input.
    packet_fields = 'stream_index,pts,duration' + (',dts' if verify_dts else '')
    probe = _process(['ffprobe', '-v', 'error', '-protocol_whitelist', 'pipe', '-f', 'mov',
                     '-show_streams', '-show_packets', '-show_entries',
                     'stream=index,codec_type,codec_name,width,height,sample_rate,channels,time_base,start_pts,duration_ts,avg_frame_rate:'
                     f'packet={packet_fields}:packet_side_data=side_data_type,skip_samples',
                     '-of', 'json', '-i', 'pipe:0'], check, data=data)
    if probe.returncode or probe.stderr.strip():
        raise WindowError('media.invalid', 'ffprobe rejected the anchored MP4 payload')
    facts = _probe_facts(json.loads(probe.stdout), rate, end - start, samples, verify_dts=verify_dts)
    return data, {'start': start, 'end': end, 'fps': {'num': rate.numerator, 'den': rate.denominator},
                  **facts, 'mime': 'video/mp4', 'byte_length': len(data), 'sha256': digest}


def _render(path, reference, root_identity, start, result, check_cancelled, expected_inputs, *, epoch,
            svg_pack=None, program_manifest=None, operator_run=None, operator_part_seconds=None,
            first_frame_ready=None):
    from .cache.cache_state import verify_input_identity
    from .pipeline import composite, range as range_pipeline, stages
    from .render import frame_environment
    from .render.gfx_environment import _files, _libraries
    from .timeline_structure import strict_loads

    version = reference['content_version']
    stored = strict_loads(reference['document']['raw'].encode('utf-8'))
    root = path.parent
    scratch = root / 'cache' / 'preview'
    isolation.check_mount(scratch, _WORK_MEDIA_LIMIT)
    if epoch and os.listdir(scratch):
        raise WindowError('write.denied', 'verified input proof requires empty isolated scratch')
    if svg_pack is not None:
        from .runtime import preview_window_svg as svg

        svg.check_tools()
        svg_pack = svg.pack_selector(root, svg_pack)
    end, rate = _preflight(stored, start, os.environ.get('FONTCONFIG_FILE'), allow_svg=svg_pack is not None)
    program_audio = program_manifest is not None
    windows = ((start, end),)
    span_run = operator_part_seconds is not None
    if first_frame_ready is not None and (not epoch or program_audio or svg_pack is not None or operator_run is not None):
        raise WindowError('input_epoch.unavailable', 'first frame requires a verified isolated ordinary window')
    if operator_run is not None:
        if not (epoch and program_audio and svg_pack is None):
            raise WindowError('audio.program_unsupported', 'operator run requires isolated ordinary program media')
        count, emit = operator_run
        windows = _program_windows(stored, start, rate, count,
                                   part_seconds=operator_part_seconds if span_run else 1)
    reuse_segments = operator_run is not None and len(windows) > 1
    if program_audio:
        from .pipeline import preview_program_audio as program

        if not epoch or not _VERSION.fullmatch(program_manifest):
            raise WindowError('audio.program_unsupported', 'missing isolated program source proof')
        program_paths = program.inputs(stored, root, rate)
        # Pre-admit every actual experimental part before staging or mixing the
        # program. Retain the legacy first-window check and later ACK sequencing.
        admission_windows = windows if span_run else ((start, end),)
        if span_run:
            check_cancelled()
        for fa, fb in admission_windows:
            _admit(SimpleNamespace(tl=stored), fa, fb, program_sources=len(program_paths))
    projected_files, caption, caption_limit = _project_files(
        path, stored, svg=svg_pack is not None, program_audio=program_audio)
    read_timeline = _timeline_reader(path, root_identity, version, check_cancelled)
    read_timeline()
    with ExitStack() as lifecycle:
        program_pcm = None
        if operator_run is not None:
            project_epoch = _program_projection(lifecycle, path, root_identity, projected_files,
                                                version, check_cancelled)
            project_baseline = project_epoch()
            program_pcm = program._RunPCM()
            lifecycle.callback(program_pcm.clear)
        read_subtitles = None
        if caption is not None:
            binding = isolation._pin_binding(lifecycle, caption, root, single_link=True, root_identity=root_identity)
            read_subtitles = _caption_reader(*binding, check_cancelled, limit=caption_limit)
        old_env = {}

        @lifecycle.callback
        def restore_environment():
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        with _request(root, root_identity, result, epoch=epoch) as (request, identity):
            # Worker-local font-cache and CPU paths also cover direct kernel children.
            overrides = {'XDG_CACHE_HOME': str(request / 'font-cache'), 'VTX_SEG_NVENC': '0',
                         'PATH': '/usr/bin:/bin'}
            old_env.update({key: os.environ.get(key) for key in overrides})
            os.environ.update(overrides)

            files = [__file__, composite.__file__, range_pipeline.__file__, stages.__file__]
            if reuse_segments:
                from .pipeline import _window_seg_relay

                files.append(_window_seg_relay.__file__)
            if program_audio:
                files.append(program.__file__)
            if read_subtitles is not None:
                from .media import subtitles_src
                files.append(subtitles_src.__file__)

            def environment():
                from .runtime.preview_window_epoch import EnvironmentWitness
                witness = EnvironmentWitness(check_cancelled, _WORK_MEDIA_LIMIT) if epoch else None

                def environment_command(argv):
                    probe = _process(argv, check_cancelled, timeout=30)
                    if probe.returncode or probe.stderr.strip():
                        raise WindowError('environment.unavailable', f'{argv[0]} reported an environment error')
                    if witness is not None:
                        witness.observe(argv, probe.stdout)
                    return probe.stdout

                certificate = (frame_environment.collect('/usr/bin/ffmpeg', command=environment_command),
                               _files(['/usr/bin/ffprobe']), _libraries('/usr/bin/ffprobe', command=environment_command))
                if witness is not None:
                    environment_command(['ldd', '/usr/bin/fc-conflist'])
                    environment_command(['ldd', sys.executable])
                    return witness.snapshot(certificate, files, dict(os.environ))
                return certificate

            tool_identity = environment()
            code_identity = _files(files)
            workspace_env = os.environ.copy()
            read_timeline()
            epoch_sources = {str(root / s['path']) for s in stored['sources']} if epoch else ()

            def limit_work_file():
                # An inherited per-file ceiling, not a reservation of scratch space.
                import resource
                old_file_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
                soft, hard = old_file_limit
                if soft == resource.RLIM_INFINITY or soft > _WORK_FILE_LIMIT:
                    resource.setrlimit(resource.RLIMIT_FSIZE, (_WORK_FILE_LIMIT, hard))
                    lifecycle.callback(resource.setrlimit, resource.RLIMIT_FSIZE, old_file_limit)

            copies = program_sources = None
            if program_audio:
                limit_work_file()
                copies, pcm = program.stage(program_paths, program_manifest, root=root, root_identity=root_identity,
                                            directory=request / 'program-inputs', check=check_cancelled)
                program_sources = {sid: (source, pcm[source][1]) for sid, source in program_paths.items()}
            c = _context(path, check_cancelled, workspace_env, read_timeline, read_subtitles,
                         epoch_sources=epoch_sources, program_copies=copies)
            if program_audio:
                if operator_run is not None and any(c.src_path(sid) != source for sid, source in program_paths.items()):
                    raise WindowError('snapshot.changed', 'program sources differ from the isolated Ctx mapping')
                program.probe(c, copies, pcm)
            if (c.input_fingerprint != expected_inputs or 'sha256:' + c.timeline_sha256 != version or c.frame_rate != rate
                    or c.dur != stored['project']['duration_frames'] or (c.w, c.h) != (1920, 1080)):
                raise WindowError('snapshot.changed', 'preview Ctx is not the referenced project')
            errors = stages.validate_core(c)
            if errors:
                raise WindowError('render.validation', f'{len(errors)} validation errors: '
                                  + '; '.join(errors[:16])[:1800])

            def current():
                read_timeline()  # includes current project's no-follow identity
                _plugins(stored)
                verify_input_identity(c)  # full kernel pin, including off-window inputs
                read_timeline()

            current()
            if not program_audio:
                _admit(c, start, end)
                limit_work_file()

            def checkpoint():
                if (operator_run is not None and dict(os.environ) != workspace_env
                        or environment() != tool_identity or _files(files) != code_identity):
                    raise WindowError('inputs.changed', 'preview toolchain or implementation changed')
                if operator_run is not None and project_epoch() != project_baseline:
                    raise WindowError('inputs.changed', 'projected input bytes changed during program run')
                current()

            segment_relay = None
            if reuse_segments:
                segment_relay = _window_seg_relay._RunSegmentRelay(c, check_cancelled)
                c._program_seg_relay = segment_relay
                lifecycle.callback(segment_relay.clear)
            remaining_media = MEDIA_LIMIT
            for index, (fa, fb) in enumerate(windows):
                workspace = request / ('render' if operator_run is None else f'render-{index}')
                if operator_run is None:
                    fstxn.mkdir(workspace, root=root)
                else:
                    if remaining_media == 0:
                        raise WindowError('output.limit', 'program run exhausted its media byte budget')
                    if index and not span_run:
                        _admit(SimpleNamespace(tl=stored), fa, fb, program_sources=len(program_paths))
                    # Neither the previous window's post-check nor its ACK
                    # substitutes for a fresh pre-production observation.
                    checkpoint()
                    with fstxn.pinned_parent(workspace, create=False, root=root) as (parent, name, canonical):
                        if canonical != workspace or fstxn.entry_identity(os.fstat(parent)) != identity[-1]:
                            raise WindowError('write.denied', 'workspace parent is not the owned request')
                        os.mkdir(name, mode=0o700, dir_fd=parent)
                workspace_identity = fstxn.cleanup_identity(workspace)
                if not workspace_identity or workspace_identity[:-1] != identity:
                    raise WindowError('write.denied', 'workspace escaped the owned request')
                c.write_root = workspace  # The same WindowCtx keeps its job-wide target set.
                if segment_relay is not None:
                    following = windows[index + 1] if index + 1 < len(windows) else None
                    segment_relay.begin_window(workspace, workspace_identity, (fa, fb), following)
                if first_frame_ready is not None:
                    def observe(stage, facts=None):
                        if stage == 'first_frame_verified':
                            checkpoint()
                            first_frame_ready(facts, tool_identity[0])

                    output = range_pipeline._render_native_window(
                        c, fa, fb, workspace=workspace, check_cancel=check_cancelled,
                        expected_svg_pack=None, mux_concat=epoch, observe=observe)
                elif svg_pack is None and not program_audio:
                    output = range_pipeline.render_1080p_window(c, fa, fb, workspace=workspace, _mux_concat=epoch)
                else:
                    pcm_options = {'program_pcm': program_pcm} if program_pcm is not None else {}
                    if span_run:
                        check_cancelled()  # Recheck the admitted segmentation before actual production.
                        pcm_options['program_part_seconds'] = operator_part_seconds
                    output = range_pipeline._render_native_window(
                        c, fa, fb, workspace=workspace, check_cancel=check_cancelled,
                        expected_svg_pack=svg_pack, program_sources=program_sources, mux_concat=epoch, **pcm_options)
                current()
                clock_options = {'verify_dts': True} if span_run else {}
                data, media = _verified_media(output, workspace, request, identity, fa, fb, rate,
                                              check_cancelled, limit=remaining_media, **clock_options)
                checkpoint()
                if segment_relay is not None:
                    segment_relay.settle_window()
                if operator_run is not None:
                    remaining_media -= len(data)
                    check_cancelled()
                    if emit(dict(media, data=data, content_version=version, program_audio=True,
                                 _input_environment=tool_identity[0], _project_inputs=project_baseline)) is not None:
                        raise WindowError('render.cancelled', 'program run emitter did not acknowledge media')
                    del data, media
                    check_cancelled()
            if operator_run is not None:
                # The last ACK may have blocked just like the earlier ones,
                # but there is no next pre-check to catch changes while waiting.
                checkpoint()
        current()  # Normal return requires pinned request cleanup and this final check.
    if operator_run is not None:
        return None
    success = {'schema': SCHEMA, 'ok': True, 'content_version': version, **media,
               'base64': base64.b64encode(data).decode('ascii')}
    if program_audio:
        success['program_audio'] = True
    if epoch:
        success['_input_environment'] = tool_identity[0]
    return success


def _render_program_run(path, *, start, count, expected_content_version, expected_inputs, program_manifest,
                        check_allowed, emit):
    """Private operator-only worker entry; not a CLI mode or security boundary.

    The caller owns the single, isolated PID-1 worker, the explicit grant for
    the complete span and whole-program PCM, the parent-original input recheck,
    and process-tree settlement. `check_allowed()` must return literal True
    while that same grant is live, or deny/raise. `emit(verified_media)` blocks
    until ACK (None) or raises; it must recheck the parent originals and grant
    before publishing. The mapping contains bounded MP4 `data` bytes (5 MiB
    over the run), the window facts, and the two local projection witnesses;
    it is not a public input epoch, delivery receipt, or settlement proof.
    No next window starts before ACK. None is returned only after local cleanup
    and the final input check; on failure an exception stops further emission.
    """
    return _run_operator_program(path, start, count, expected_content_version, expected_inputs,
                                 program_manifest, check_allowed, emit)


def _render_program_span_run(path, *, start, count, part_seconds, expected_content_version, expected_inputs,
                             program_manifest, check_allowed, emit):
    """Experimental private run of 1–3 complete 1/2/4-second, 1080p 30/1 windows.

    The same caller duties and ACK contract as `_render_program_run` apply.
    The explicit grant is `check_allowed(start, stop, part_seconds, count) is True`,
    where [start, stop) is the entire batch in absolute project frames. The caller
    must bind the grant to this project's input proof and enforce its 8 MiB total
    wire budget; the worker bounds cumulative MP4 bytes to 5 MiB before emission.
    Emitted `start`/`end` are the actual half-open part endpoints in that grant.
    """
    if type(part_seconds) is not int or part_seconds not in (1, 2, 4):
        raise WindowError('render.arguments', 'program span run requires 1, 2 or 4 integer seconds per window')
    return _run_operator_program(path, start, count, expected_content_version, expected_inputs,
                                 program_manifest, check_allowed, emit, part_seconds=part_seconds)


def _run_operator_program(path, start, count, expected_content_version, expected_inputs, program_manifest,
                          check_allowed, emit, *, part_seconds=None):
    from .render.png_artifact import project_identity, read_artifact

    if (type(start) is not int or type(count) is not int or not 1 <= count <= 3
            or not isinstance(expected_content_version, str) or not _VERSION.fullmatch(expected_content_version)
            or not isinstance(expected_inputs, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_inputs)
            or not isinstance(program_manifest, str) or not _VERSION.fullmatch(program_manifest)
            or not callable(check_allowed) or not callable(emit)):
        raise WindowError('render.arguments', 'program run requires 1–3 windows, input proofs and private callbacks')

    emitter = emit
    span_options = {}
    grant = ()
    if part_seconds is not None:
        # Both _admit and the real compositor read this setting from the worker.
        # Do not let a grant callback move the plan onto a different segment count.
        segment_setting = os.environ.get('VTX_VIDEO_SEGMENTS')
        if start < 0:
            raise WindowError('render.range', 'program span run requires a nonnegative start frame')
        frames = 30 * part_seconds
        stop = start + frames * count
        grant = (start, stop, part_seconds, count)
        next_frame = start
        span_options['operator_part_seconds'] = part_seconds

        def emitter(media):
            nonlocal next_frame
            check()
            if (next_frame >= stop or type(media.get('start')) is not int or type(media.get('end')) is not int
                    or (media['start'], media['end']) != (next_frame, next_frame + frames)):
                raise WindowError('media.invalid', 'emitted part endpoints do not match the authorized program span')
            ack = emit(media)
            if ack is None:
                next_frame += frames
            check()
            return ack

    def segment_current():
        if part_seconds is not None and os.environ.get('VTX_VIDEO_SEGMENTS') != segment_setting:
            raise WindowError('inputs.changed', 'preview segment count setting changed during program span run')

    def check():
        segment_current()
        allowed = check_allowed(*grant)
        segment_current()
        if allowed is not True:
            raise WindowError('render.cancelled', 'program run approval is not live')

    check()
    path = Path(os.path.abspath(Path(path).expanduser()))
    scratch = path.parent / 'cache' / 'preview'
    isolation.check_mount(scratch, _WORK_MEDIA_LIMIT)
    identity = project_identity(path.parent)
    loaded = _load(path, 'reference', raw=True, max_bytes=WIRE_LIMIT, read_payload=lambda:
                   read_artifact(path, root=path.parent, root_identity=identity, limit=WIRE_LIMIT))
    reference = _reference_loaded(path, loaded, global_frame=start,
                                  expected_content_version=expected_content_version)
    if not reference['ok']:
        raise WindowError('snapshot.changed', 'program run requires the referenced stored timeline and start frame')
    result = {'diagnostics': []}
    try:
        success = _render(path, reference, identity, start, result, check, expected_inputs, epoch=True,
                          program_manifest=program_manifest, operator_run=(count, emitter), **span_options)
        if part_seconds is not None:
            check()
            if next_frame != stop:
                raise WindowError('media.invalid', 'program span did not emit all authorized parts')
        return success
    except BaseException as primary:
        # Pinned cleanup errors are recorded separately when a producer or
        # emitter already failed. Keep the primary and signal unproven cleanup.
        if result['diagnostics']:
            primary._vtx_isolation_unsettled = True
        raise


def preview_window(path, *, start, expected_content_version, check_cancelled, _expected_inputs=None,
                   _qualification=None, _epoch=False, _svg_mode=None, _relative=None,
                   _program_audio=False, _program_manifest=None,
                   _first_frame=None, _first_frame_ready=None):
    if type(start) is not int or not isinstance(expected_content_version, str) or not _VERSION.fullmatch(expected_content_version):
        return error_result('cli.arguments', 'integer --frame and lowercase sha256: --expected-content-version required')
    if _expected_inputs is None and not supported():
        return error_result('platform.unsupported', 'preview window requires Linux fd-anchored writes, Bubblewrap and the native producer')
    result = {'schema': WARM_SVG_SCHEMA if _svg_mode in ('svg-plan', 'svg-read') else SCHEMA,
              'ok': False, 'diagnostics': []}
    try:
        path = Path(os.path.abspath(Path(path).expanduser()))
        from .render.png_artifact import project_identity, read_artifact
        identity = project_identity(path.parent)
        loaded = _load(path, 'reference', raw=True, max_bytes=WIRE_LIMIT, read_payload=lambda:
                       read_artifact(path, root=path.parent, root_identity=identity, limit=WIRE_LIMIT))
        reference = _reference_loaded(path, loaded, global_frame=start,
                                      expected_content_version=expected_content_version)
        if not reference['ok']:
            result['diagnostics'] = reference['diagnostics']
        else:
            if _expected_inputs is None:
                success = _dispatch(path, reference, identity, start, check_cancelled, _qualification,
                                    svg_mode=_svg_mode, program_audio=_program_audio,
                                    **({"first_frame": _first_frame} if _first_frame is not None else {}))
            elif _svg_mode in ('svg-plan', 'svg-read'):
                success = _svg_projection(path, reference, identity, start, check_cancelled,
                                          _expected_inputs, mode=_svg_mode, relative=_relative)
            else:
                success = _render(path, reference, identity, start, result, check_cancelled,
                                  _expected_inputs, epoch=_epoch,
                                  svg_pack=_relative if _svg_mode == 'svg-media' else None,
                                  program_manifest=_program_manifest,
                                  **({"first_frame_ready": _first_frame_ready} if _first_frame_ready is not None else {}))
            if _wire_size(success, WIRE_LIMIT - 2) >= WIRE_LIMIT - 1:
                return error_result('output.limit', 'preview window exceeds JSON wire limit')
            return success
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, OverflowError, ZeroDivisionError,
            EOFError, wave.Error, SystemExit, KeyboardInterrupt) as error:
        from .pipeline.preview_program_audio import ProgramAudioError
        from .pluginlib.hooks import _CoreOnlyPreviewError
        from .render.png_artifact import ArtifactError
        primary = error if not isinstance(error, SystemExit) or error.__cause__ is None else error.__cause__
        code = (primary.code if isinstance(primary, (WindowError, ArtifactError, _CoreOnlyPreviewError, ProgramAudioError))
                else 'render.cancelled' if isinstance(primary, KeyboardInterrupt)
                else 'isolation.unavailable' if isinstance(primary, isolation.IsolationError) else 'render.failed')
        message = str(primary)[:2000] or 'preview window cancelled; no success returned'
        result['diagnostics'][:0] = error_result(code, message)['diagnostics']
        if getattr(primary, '_vtx_isolation_unsettled', False):
            _error(result, 'render.unsettled', '', 'isolated process-tree exit requires Host confirmation')
    if _wire_size(result, WIRE_LIMIT - 1) >= WIRE_LIMIT:
        return error_result('output.limit', 'preview diagnostics exceed JSON wire limit')
    return result
