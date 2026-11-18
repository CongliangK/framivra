"""Internal CLI candidate; not a public render API or a delivery receipt."""
import base64
from contextlib import ExitStack, contextmanager
from fractions import Fraction
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

from .runtime import fstxn
from .timeline import _error, _load
from .timeline_reference import _reference_loaded, _wire_size

SCHEMA = 'framivra.timeline.preview-window/v1'
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
            or os.rmdir not in os.supports_dir_fd or os.listdir not in os.supports_fd):
        return False
    from .pipeline import range as range_pipeline
    return callable(getattr(range_pipeline, 'render_1080p_window', None))


def _safe_environment():
    present = sorted(k for k in os.environ if k in _UNSAFE_ENV or k.startswith(
        ('LD_', 'DYLD_', 'FONTCONFIG_', 'FC_', 'GLIBC_TUNABLES', 'BASH_FUNC_')))
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


def _preflight(stored, start):
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
    if 'subtitles_src' in stored:
        raise WindowError('subtitles.unsupported', 'preview window currently requires inline subtitles')
    rate = parse_fps(project['fps'])
    end = min(project['duration_frames'], start + (rate.numerator + rate.denominator - 1) // rate.denominator)
    if any(clip['dst_in'] < end and start < clip['dst_in'] + clip['dur']
           for track in stored['video_tracks'] if track['kind'] == 'gfx' for clip in track['clips']):
        raise WindowError('gfx.unsupported', 'preview window intersects a GFX clip')
    if any(track['kind'] == 'gfx' and track['clips'] for track in stored['video_tracks']):
        # The full input pin can initialize graphics-tool identity outside the
        # window; this candidate has no authorization for that process family.
        raise WindowError('gfx.unsupported', 'preview window cannot verify off-window graphics yet')
    _safe_environment()
    return end, rate


@contextmanager
def _request(root, root_identity, result):
    """Create exclusively; prove removal of the original inode, not a pathname."""
    from .runtime.proc_supervisor import WORKER_LEDGER
    from .render.png_artifact import project_identity

    directory = root / 'cache' / 'preview' / ('window_' + secrets.token_hex(16))
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


def _admit(c, start, end):
    """Conservative cold-work gate, not aggregate filesystem quota/reservation."""
    from .pipeline.composite import DEFAULT_SEGMENTS
    from .pipeline.range import AUDIO_MARGIN_FRAMES

    clips = [clip for track in c.tl['video_tracks'] for clip in track['clips']
             if clip['dst_in'] < end and start < clip['dst_in'] + clip['dur']]
    sounds = {ev['src'] for track in c.tl['audio_tracks'] for ev in track['events']
              if ev['in'] < end + AUDIO_MARGIN_FRAMES and start - AUDIO_MARGIN_FRAMES < ev['out']}
    segments = int(os.environ.get('VTX_VIDEO_SEGMENTS', str(DEFAULT_SEGMENTS)))
    # Full-clip decode bounds, candidate-local pass count, and estimated PCM
    # bytes. A video-native source can have nonmatching A/V lengths: actual
    # FFmpeg writes also have a per-file OS cap, not just this estimate.
    estimates = [c.sample_at(c.probe(c.src_path(sid))['nb_frames']) * 4 for sid in sounds]
    if (not 1 <= segments <= _WORK_FILE_COUNT or sum(cl['src_out'] for cl in clips) > 3600
            or len(clips) + min(segments, end - start) * max(1, len(clips)) + len(sounds) + 3 > _WORK_FILE_COUNT
            or any(estimate > _WORK_FILE_LIMIT for estimate in estimates)
            or shutil.disk_usage(c.root).free < _WORK_MEDIA_LIMIT):
        raise WindowError('work.limit', 'cold preview work exceeds the candidate resource budget')


def _expected_pcm(data, samples):
    with wave.open(io.BytesIO(data), 'rb') as wav:
        if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth(), wav.getnframes()) != (2, 48000, 2, samples):
            raise WindowError('media.audio_clock', 'mixed PCM does not match exact window sample endpoints')
        if len(wav.readframes(samples)) != samples * 4:
            raise WindowError('media.audio_clock', 'mixed PCM is truncated')


def _probe_facts(info, rate, frames, samples):
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


def _context(path, check_cancelled, workspace_env, read_timeline):
    from .cache.recovery import atomic_ffmpeg
    from .pipeline.infra import Ctx, is_ffmpeg_cmd
    from .cache.cache_state import verify_input_identity

    targets = set()

    class WindowCtx(Ctx):
        # Guard every internal hook lookup from the very first fingerprint. The
        # real Ctx constructor and validate_core still run in full.
        _core_only_preview = True
        write_root = None

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
            return super().probe(path)

        @staticmethod
        def _preview_window_tasks(c, tasks, what):
            # Serial and cancellable; use exactly the core-produced argv.
            for _label, cmd, nvenc in tasks:
                if nvenc:
                    raise WindowError("environment.unsupported", "window preview is CPU-only")
                c.run(cmd)

        def run(self, cmd, need=True, **kwargs):
            check_cancelled()
            if is_ffmpeg_cmd(cmd) and cmd[-1] not in ('-', os.devnull):
                target = Path(cmd[-1])
                if (self.write_root is None or self.write_root not in target.parents
                        or target.resolve() != target):
                    raise WindowError('write.denied', 'renderer output escaped private preview workspace')
                targets.add(target)
                if len(targets) > _WORK_FILE_COUNT:
                    raise WindowError('work.limit', 'too many preview media outputs')
            if cmd[0] == 'ffprobe':
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


def _render(path, reference, root_identity, start, result, check_cancelled):
    from .cache.cache_state import verify_input_identity
    from .cache.recovery import completion_marker
    from .pipeline import composite, range as range_pipeline, stages
    from .pipeline.constraints import frame_sample
    from .render import frame_environment
    from .render.gfx_environment import _files, _libraries
    from .render.png_artifact import read_artifact
    from .timeline_frame import _hash
    from .timeline_structure import strict_loads

    version = reference['content_version']
    stored = strict_loads(reference['document']['raw'].encode('utf-8'))
    end, rate = _preflight(stored, start)
    root = path.parent
    from .cache.cache_state import all_declared_dependencies
    input_size = 0
    for name in [*(source['path'] for source in stored['sources']),
                 *all_declared_dependencies(SimpleNamespace(tl=stored))]:
        candidate = root / name
        if (root not in candidate.parents or '..' in candidate.parts
                or candidate.resolve(strict=True) != candidate or not candidate.is_file()):
            raise WindowError('inputs.unsupported', 'sources and dependencies must be regular project files')
        input_size += candidate.stat().st_size
        if input_size > _WORK_MEDIA_LIMIT:
            raise WindowError('work.limit', 'declared source files exceed the candidate input budget')

    def read_timeline():
        check_cancelled()
        payload = read_artifact(path, root=root, limit=WIRE_LIMIT, root_identity=root_identity)
        if 'sha256:' + _hash(payload) != version:
            raise WindowError('snapshot.changed', 'timeline changed during preview window')
        return payload

    read_timeline()
    with ExitStack() as lifecycle:
        old_env = {}

        @lifecycle.callback
        def restore_environment():
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        with _request(root, root_identity, result) as (request, identity):
            # Scope font-cache writes from the default Fontconfig config and force
            # the CPU path. This is CLI-only and also covers direct kernel children.
            overrides = {'XDG_CACHE_HOME': str(request / 'font-cache'), 'VTX_SEG_NVENC': '0',
                         'PATH': '/usr/bin:/bin'}
            old_env.update({key: os.environ.get(key) for key in overrides})
            os.environ.update(overrides)

            def environment_command(argv):
                probe = _process(argv, check_cancelled, timeout=30)
                if probe.returncode:
                    raise WindowError('environment.unavailable', f'{argv[0]} exited {probe.returncode}')
                return probe.stdout

            def environment():
                return (frame_environment.collect('/usr/bin/ffmpeg', command=environment_command),
                        _files(['/usr/bin/ffprobe']), _libraries('/usr/bin/ffprobe', command=environment_command))

            tool_identity = environment()
            files = [__file__, composite.__file__, range_pipeline.__file__, stages.__file__]
            code_identity = _files(files)
            read_timeline()
            c = _context(path, check_cancelled, os.environ.copy(), read_timeline)
            if ('sha256:' + c.timeline_sha256 != version or c.frame_rate != rate
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
            _admit(c, start, end)
            # Linux's inherited file-size limit bounds each encoder output. It
            # does not reserve bytes or cover every metadata/font cache write.
            import resource
            old_file_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
            soft, hard = old_file_limit
            if soft == resource.RLIM_INFINITY or soft > _WORK_FILE_LIMIT:
                resource.setrlimit(resource.RLIMIT_FSIZE, (_WORK_FILE_LIMIT, hard))
                lifecycle.callback(resource.setrlimit, resource.RLIMIT_FSIZE, old_file_limit)
            workspace = request / 'render'
            fstxn.mkdir(workspace, root=root)
            if fstxn.cleanup_identity(workspace)[:-1] != identity:
                raise WindowError('write.denied', 'workspace escaped the owned request')
            c.write_root = workspace
            output = range_pipeline.render_1080p_window(c, start, end, workspace=workspace)
            current()
            if output.parent != workspace or output.suffix != '.mp4':
                raise WindowError('media.invalid', 'producer did not return its private MP4 artifact')

            def artifact(path, limit):
                return read_artifact(path, root=request, root_identity=identity, limit=limit)

            data = artifact(output, MEDIA_LIMIT)
            digest = _hash(data)
            if (len(data) < 16 or data[4:8] != b'ftyp' or data[8:12] == b'qt  '
                    or json.loads(artifact(completion_marker(output), 4096)) !=
                    {'schema': 1, 'sha256': digest, 'frames': end - start}):
                raise WindowError('media.invalid', 'MP4 bytes do not match the decoded media certificate')
            samples = frame_sample(end, rate) - frame_sample(start, rate)
            pcm = artifact(workspace / 'work' / 'preview' / output.with_suffix('.wav').name,
                           samples * 4 + 4096)
            _expected_pcm(pcm, samples)
            # Probe the exact anchored bytes, never a second, pathname-based
            # read of the renderer's result. Faststart allows nonseekable input.
            probe = _process(['ffprobe', '-v', 'error', '-protocol_whitelist', 'pipe', '-f', 'mov',
                             '-show_streams', '-show_packets', '-show_entries',
                             'stream=index,codec_type,codec_name,width,height,sample_rate,channels,time_base,start_pts,duration_ts,avg_frame_rate:'
                             'packet=stream_index,pts,duration:packet_side_data=side_data_type,skip_samples',
                             '-of', 'json', '-i', 'pipe:0'], check_cancelled, data=data)
            if probe.returncode or probe.stderr.strip():
                raise WindowError('media.invalid', 'ffprobe rejected the anchored MP4 payload')
            facts = _probe_facts(json.loads(probe.stdout), rate, end - start, samples)
            if environment() != tool_identity or _files(files) != code_identity:
                raise WindowError('inputs.changed', 'preview toolchain or implementation changed')
            current()
        current()  # No reply until pinned cleanup and a final input check succeed.
    return {'schema': SCHEMA, 'ok': True, 'content_version': version, 'start': start, 'end': end,
            'fps': {'num': rate.numerator, 'den': rate.denominator}, **facts, 'mime': 'video/mp4',
            'byte_length': len(data), 'sha256': digest, 'base64': base64.b64encode(data).decode('ascii')}


def preview_window(path, *, start, expected_content_version, check_cancelled):
    if type(start) is not int or not isinstance(expected_content_version, str) or not _VERSION.fullmatch(expected_content_version):
        return error_result('cli.arguments', 'integer --frame and lowercase sha256: --expected-content-version required')
    if not supported():
        return error_result('platform.unsupported', 'preview window requires Linux fd-anchored writes and the native producer')
    result = {'schema': SCHEMA, 'ok': False, 'diagnostics': []}
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
            success = _render(path, reference, identity, start, result, check_cancelled)
            if _wire_size(success, WIRE_LIMIT - 2) >= WIRE_LIMIT - 1:
                return error_result('output.limit', 'preview window exceeds JSON wire limit')
            return success
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, OverflowError, ZeroDivisionError,
            EOFError, wave.Error, SystemExit, KeyboardInterrupt) as error:
        from .pluginlib.hooks import _CoreOnlyPreviewError
        from .render.png_artifact import ArtifactError
        primary = error if not isinstance(error, SystemExit) or error.__cause__ is None else error.__cause__
        code = (primary.code if isinstance(primary, (WindowError, ArtifactError, _CoreOnlyPreviewError))
                else 'render.cancelled' if isinstance(primary, KeyboardInterrupt) else 'render.failed')
        message = str(primary)[:2000] or 'preview window cancelled; no success returned'
        result['diagnostics'][:0] = error_result(code, message)['diagnostics']
    if _wire_size(result, WIRE_LIMIT - 1) >= WIRE_LIMIT:
        return error_result('output.limit', 'preview diagnostics exceed JSON wire limit')
    return result
