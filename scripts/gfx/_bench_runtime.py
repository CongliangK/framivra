"""Execution and decoded-media evidence for the opt-in CEF benchmark.

This module does not select a backend, relax QC, or certify a renderer. Resource
figures are sampled observations, not an all-descendant lifetime accounting API.
Only the process group created for a command may be signalled. Nested sessions
(e.g. CEF workers) still depend on the product's own supervision.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from fractions import Fraction


_TERMINATE_GRACE_SECONDS = 2.0
_PS_TIMEOUT_SECONDS = 2.0
_REAP_TIMEOUT_SECONDS = 5.0
_RESOURCE_LIMITATIONS = [
    'RSS is a sampled sum of visible descendant RSS; shared pages are counted repeatedly.',
    'Sampling can miss short-lived children and memory peaks; this is not peak physical memory.',
    'CPU is the sum of maximum observed per-PID CPU time, a lower bound, not total CPU accounting.',
    'ps CPU time precision varies by platform; short CPU bursts may round down to zero.',
    'Reparented processes, detached sessions and descendants absent from a snapshot may be missed.',
    'No GPU utilization, GPU memory, energy, or privileged counters are measured.',
]


def _positive_finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + ' must be a finite positive number')
    return float(value)


def _private_file(path):
    """Create a new private artifact; never append to or reuse stale evidence."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return os.fdopen(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')


def _cpu_seconds(value):
    """Parse Linux [[days-]hours:]minutes:seconds and macOS minutes:seconds.cs."""
    days = 0
    if '-' in value:
        day_text, value = value.split('-', 1)
        days = int(day_text)
    parts = value.split(':')
    if len(parts) == 2:
        hours, minutes, seconds = 0, int(parts[0]), float(parts[1])
    elif len(parts) == 3:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
    else:
        raise ValueError('unsupported ps CPU time')
    if min(days, hours, minutes, seconds) < 0 or not math.isfinite(seconds) or seconds >= 60:
        raise ValueError('invalid ps CPU time')
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _ps_snapshot():
    """Read only PID, PPID, RSS and CPU time. Never persist the global process list."""
    if sys.platform not in ('darwin', 'linux'):
        raise RuntimeError('ps sampling is supported only on macOS and Linux')
    executable = next((p for p in ('/bin/ps', '/usr/bin/ps') if os.path.isfile(p)), None)
    if executable is None:
        raise RuntimeError('ps executable is unavailable')
    result = subprocess.run(
        [executable, '-axo', 'pid=,ppid=,rss=,time='], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=_PS_TIMEOUT_SECONDS, check=False,
        env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C', 'LANG': 'C'})
    if result.returncode:
        # Do not include command lines or a global process dump in diagnostics.
        raise RuntimeError('ps exited with status ' + str(result.returncode))
    rows = {}
    for line in result.stdout.decode('ascii', 'replace').splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        try:
            rss = int(fields[2]) * 1024  # Both BSD and procps ps report RSS in KiB.
            if rss < 0:
                rss = None
        except ValueError:
            rss = None
        try:
            cpu = _cpu_seconds(fields[3])
        except (ValueError, OverflowError):
            cpu = None
        rows[pid] = (ppid, rss, cpu)
    return rows


class _ResourceSampler:
    def __init__(self, pid, interval):
        self.pid, self.interval = pid, interval
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.samples = 0
        self.peak_rss = None
        self.peak_processes = None
        self.cpu = {}
        self.errors = []
        self.thread = threading.Thread(target=self._run, name='benchmark-ps', daemon=True)

    def _error(self, reason):
        with self.lock:
            if reason not in self.errors:
                self.errors.append(reason)

    def _sample(self):
        rows = _ps_snapshot()
        if self.pid not in rows:
            return  # A fast process may exit before its first snapshot.
        children = {}
        for pid, row in rows.items():
            children.setdefault(row[0], []).append(pid)
        selected, pending = set(), [self.pid]
        while pending:
            pid = pending.pop()
            if pid in selected:
                continue
            selected.add(pid)
            pending.extend(children.get(pid, ()))
        rss_values = [rows[pid][1] for pid in selected]
        with self.lock:
            self.samples += 1
            self.peak_processes = max(self.peak_processes or 0, len(selected))
            if all(value is not None for value in rss_values):
                total = sum(rss_values)
                self.peak_rss = max(self.peak_rss or 0, total)
            elif 'incomplete RSS snapshot' not in self.errors:
                self.errors.append('incomplete RSS snapshot')
            for pid in selected:
                cpu = rows[pid][2]
                if cpu is not None:
                    self.cpu[pid] = max(cpu, self.cpu.get(pid, 0))
                elif 'incomplete CPU snapshot' not in self.errors:
                    self.errors.append('incomplete CPU snapshot')

    def _run(self):
        while not self.stop.is_set():
            try:
                self._sample()
            except Exception as error:
                self._error(type(error).__name__ + ': ' + str(error))
            self.stop.wait(self.interval)

    def result(self):
        with self.lock:
            available = self.peak_rss is not None or bool(self.cpu)
            return {
                'status': ('partial' if self.errors else 'sampled') if available else 'unavailable',
                'method': 'ps-descendant-tree', 'samples': self.samples,
                'sample_interval_seconds': self.interval,
                'peak_tree_rss_bytes': self.peak_rss,
                'cpu_seconds_lower_bound': sum(self.cpu.values()) if self.cpu else None,
                'peak_sampled_processes': self.peak_processes,
                'reasons': {
                    'rss': None if self.peak_rss is not None else 'no complete RSS snapshot of the live command tree',
                    'cpu': None if self.cpu else 'no CPU snapshot of the live command tree',
                },
                'errors': list(self.errors), 'limitations': list(_RESOURCE_LIMITATIONS),
            }


def _terminate_owned_group(process, cleanup):
    """Keep the leader unreaped through escalation so its PGID cannot be reused."""
    if process.returncode is not None:
        # Popen.wait may briefly wait/reap while handling KeyboardInterrupt. A
        # recycled numeric PGID is never authority to signal another process.
        cleanup['errors'].append('leader already reaped; process-group cleanup was not attempted')
        cleanup['leader_reaped'] = True
        return process.returncode
    for sig, key in ((signal.SIGTERM, 'term_sent'), (signal.SIGKILL, 'kill_sent')):
        try:
            os.killpg(process.pid, sig)
            cleanup[key] = True
        except ProcessLookupError:
            pass
        except OSError as error:
            cleanup['errors'].append(type(error).__name__ + ': ' + str(error))
        if sig == signal.SIGTERM:
            # Do NOT poll()/wait() here: a dead-but-unreaped leader reserves its PID.
            time.sleep(_TERMINATE_GRACE_SECONDS)
    try:
        code = process.wait(timeout=_REAP_TIMEOUT_SECONDS)
        cleanup['leader_reaped'] = True
        return code
    except subprocess.TimeoutExpired:
        cleanup['leader_reaped'] = False
        cleanup['errors'].append('owned command leader did not exit/reap after bounded escalation; cleanup is incomplete')
        return None


def run_command(argv, cwd, env, log_path, timeout, sample_interval=1.0):
    """Run one command, with direct file output and honest optional ps metrics.

    Wall time starts immediately before Popen and ends immediately after wait;
    log setup and sampler join are outside it. Timeout/grace are inside wall time.
    Spawn failures have no elapsed_seconds or exit_code. Metrics failures never
    replace the command's status. Invalid arguments fail before starting a child.
    """
    entered = time.perf_counter()
    if os.name != 'posix':
        raise ValueError('run_command requires POSIX command-owned process groups')
    timeout = _positive_finite(timeout, 'timeout')
    sample_interval = _positive_finite(sample_interval, 'sample_interval')
    if not 0.01 <= sample_interval <= 60:
        raise ValueError('sample_interval must be between 0.01 and 60 seconds')
    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError('argv must be a nonempty sequence, not a shell command')
    argv = [os.fspath(part) for part in argv]
    if any(not isinstance(part, str) or '\0' in part for part in argv):
        raise ValueError('argv must contain text without NUL bytes')
    cleanup = {'term_sent': False, 'kill_sent': False, 'leader_reaped': None, 'errors': [],
               'scope': 'only the command-owned process group; detached descendants require product supervision'}
    empty_sampler = _ResourceSampler(-1, sample_interval)
    result = {
        'exit_code': None, 'elapsed_seconds': None, 'timed_out': False,
        'started_utc': None, 'error': None, 'log_path': str(log_path),
        'resources': empty_sampler.result(), 'cleanup': cleanup,
        'overhead_seconds': {'setup': None, 'sampler_join': 0.0, 'after_wait': 0.0},
    }
    process = sampler = None
    waited_at = None
    try:
        with _private_file(log_path) as log:
            log.write(('[benchmark command] ' + json.dumps(argv, ensure_ascii=False) + '\n').encode('utf-8'))
            log.flush()
            result['started_utc'] = datetime.now(timezone.utc).isoformat()
            started = time.perf_counter()
            result['overhead_seconds']['setup'] = started - entered
            try:
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=(os.name == 'posix'))
            except (OSError, ValueError, TypeError) as error:
                result['error'] = type(error).__name__ + ': ' + str(error)
                return result
            sampler = _ResourceSampler(process.pid, sample_interval)
            try:
                sampler.thread.start()
            except RuntimeError as error:
                sampler._error('sampler could not start: ' + str(error))
            try:
                result['exit_code'] = process.wait(timeout=timeout)
                cleanup['leader_reaped'] = True
            except subprocess.TimeoutExpired:
                result['timed_out'] = True
                result['exit_code'] = _terminate_owned_group(process, cleanup)
                if result['exit_code'] is None:
                    result['error'] = '; '.join(cleanup['errors'])
            except BaseException:
                _terminate_owned_group(process, cleanup)
                raise
            finally:
                waited_at = time.perf_counter()
                result['elapsed_seconds'] = waited_at - started
                sampler.stop.set()
    except (OSError, ValueError) as error:
        # A log/open failure must not masquerade as a successful command.
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        if sampler is not None:
            joined_at = time.perf_counter()
            if sampler.thread.ident is not None:
                sampler.thread.join(_PS_TIMEOUT_SECONDS + 1)
                if sampler.thread.is_alive():
                    sampler._error('sampler did not finish before the bounded join')
            result['overhead_seconds']['sampler_join'] = time.perf_counter() - joined_at
            result['resources'] = sampler.result()
        if waited_at is not None:
            result['overhead_seconds']['after_wait'] = time.perf_counter() - waited_at
    return result


def _json_write_private(path, value):
    with _private_file(path) as handle:
        handle.write((json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8'))


def _fraction(value):
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None


def _file_identity(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _normalize_framemd5(path, video, expected_frames, expected_fps):
    """Preserve exact decoded native pixels and timestamps, not MP4 packet bytes."""
    time_base, dimensions = None, None
    records = []
    with path.open('r', encoding='ascii') as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith('#tb 0:'):
                if time_base is not None:
                    raise ValueError('duplicate video time base')
                time_base = _fraction(line.split(':', 1)[1].strip())
            elif line.startswith('#dimensions 0:'):
                dimensions = line.split(':', 1)[1].strip()
            elif not line.startswith('#'):
                fields = [field.strip() for field in line.split(',')]
                if len(fields) != 6 or fields[0] != '0' or not re.fullmatch('[0-9a-fA-F]{32}', fields[5]):
                    raise ValueError('invalid decoded video frame record')
                dts, pts, duration, size = map(int, fields[1:5])
                if duration <= 0 or size <= 0:
                    raise ValueError('invalid decoded video frame duration/size')
                records.append((dts, pts, duration, size, fields[5].lower()))
                if len(records) > expected_frames:
                    raise ValueError('decoded video has extra frames')
    if time_base is None or time_base <= 0:
        raise ValueError('missing decoded video time base')
    if dimensions != '%sx%s' % (video['width'], video['height']):
        raise ValueError('decoded video dimensions differ from ffprobe')
    if len(records) != expected_frames:
        raise ValueError('decoded video frame count differs from the expected workload')
    normalized = []
    for index, (dts, pts, duration, size, digest) in enumerate(records):
        timestamp, frame_duration = pts * time_base, duration * time_base
        if timestamp != Fraction(index, 1) / expected_fps or frame_duration != 1 / expected_fps:
            raise ValueError('decoded video has a noncanonical PTS/duration sequence')
        if dts != pts:
            raise ValueError('decoded raw video DTS/PTS mismatch')
        normalized.append({'pts': str(timestamp), 'duration': str(frame_duration),
                           'size': size, 'md5': digest})
    payload = {
        'schema': 'vtx.cef_benchmark.decoded_video/1',
        'width': video['width'], 'height': video['height'], 'pix_fmt': video['pix_fmt'],
        'sample_aspect_ratio': video.get('sample_aspect_ratio'),
        'color_range': video.get('color_range'), 'color_space': video.get('color_space'),
        'color_transfer': video.get('color_transfer'), 'color_primaries': video.get('color_primaries'),
        'frames': normalized,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                       allow_nan=False).encode('utf-8')).hexdigest()
    return payload, digest


def _normalize_audio_framemd5(path, audio, expected_frames, expected_fps):
    """Validate the full decoded PCM timeline; a PCM hash alone ignores PTS gaps."""
    time_base = None
    samples = 0
    expected_samples = Fraction(expected_frames * 48000, 1) / expected_fps
    with path.open('r', encoding='ascii') as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith('#tb 0:'):
                if time_base is not None:
                    raise ValueError('duplicate decoded audio time base')
                time_base = _fraction(line.split(':', 1)[1].strip())
                if time_base is None or time_base <= 0:
                    raise ValueError('invalid decoded audio time base')
            elif not line.startswith('#'):
                fields = [field.strip() for field in line.split(',')]
                if (time_base is None or len(fields) != 6 or fields[0] != '0'
                        or not re.fullmatch('[0-9a-fA-F]{32}', fields[5])):
                    raise ValueError('invalid decoded audio frame record')
                dts, pts, duration, size = map(int, fields[1:5])
                if size <= 0 or size % 4 or duration <= 0:
                    raise ValueError('invalid stereo s16le audio frame size/duration')
                frame_samples = size // 4
                if (dts != pts or pts * time_base != Fraction(samples, 48000)
                        or duration * time_base != Fraction(frame_samples, 48000)):
                    raise ValueError('decoded audio PTS/duration contains a gap, overlap or offset')
                samples += frame_samples
                # Existing full-media verification permits at most one AAC frame
                # of padding; do not invent a broader timing tolerance here.
                if samples > expected_samples + 1024:
                    raise ValueError('decoded audio exceeds existing 1024-sample AAC padding allowance')
    source_base, source_ticks = _fraction(audio.get('time_base')), _fraction(audio.get('duration_ts'))
    if not samples or time_base is None or source_base is None or source_base <= 0 or source_ticks is None:
        raise ValueError('missing complete decoded audio duration/timeline')
    source_duration = source_base * source_ticks
    expected_duration = Fraction(expected_frames, 1) / expected_fps
    if (abs(samples - expected_samples) > 1024 or source_duration <= 0
            or abs(source_duration - expected_duration) > Fraction(1024, 48000)):
        raise ValueError('audio duration exceeds existing 1024-sample AAC padding allowance')
    # Independent of decoder packet/frame splitting. The raw artifact retains
    # every frame, while these facts identify the verified continuous timeline.
    payload = {'schema': 'vtx.cef_benchmark.decoded_audio_timeline/1',
               'sample_rate': 48000, 'channels': 2, 'sample_format': 's16le',
               'start': '0', 'sample_frames': samples,
               'decoded_duration': str(Fraction(samples, 48000)),
               'source_duration': str(source_duration)}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                       allow_nan=False).encode('utf-8')).hexdigest()
    return payload, digest


def verify_media(project, evidence_dir, ffmpeg, ffprobe, timeout, env):
    """Verify the fixed canonical 900-frame 1080p30 output, independently of QC."""
    return _verify_media(project, evidence_dir, ffmpeg, ffprobe, timeout, env)


def _verify_media(project, evidence_dir, ffmpeg, ffprobe, timeout, env, *,
                  expected_width=1920, expected_height=1080, expected_frames=900,
                  expected_fps=Fraction(30, 1)):
    """Private geometry overrides exist solely for small mechanism tests."""
    timeout = _positive_finite(timeout, 'timeout')
    expected_fps = Fraction(expected_fps)
    if (any(type(value) is not int or value <= 0 for value in
            (expected_width, expected_height, expected_frames)) or expected_fps <= 0):
        raise ValueError('invalid expected media workload')
    project, evidence_dir = Path(project).resolve(), Path(evidence_dir).resolve()
    final = project / 'out' / 'final.mp4'
    names = ('final_exists', 'ffprobe_full_stream', 'video_stream', 'audio_stream',
             'video_geometry', 'video_frame_count', 'video_frame_rate', 'video_start_time',
             'audio_format', 'audio_start_time', 'video_full_decode', 'video_timestamps',
             'video_decoded_frame_count', 'audio_full_decode', 'audio_nonempty', 'audio_timestamps',
             'audio_duration', 'source_unchanged')
    report = {'status': 'unknown', 'video_digest': None, 'audio_digest': None,
              'audio_timeline_digest': None, 'checks': dict.fromkeys(names), 'error': None, 'evidence': {},
              'commands': {}, 'video': {}, 'audio': {},
              'limitations': ['Decoded equality is not strict QC, native CEF evidence, or perceptual quality certification.']}
    checks = report['checks']

    def check(name, passed, message):
        checks[name] = bool(passed)
        if not passed:
            raise ValueError(message)

    def command(name, argv):
        log = evidence_dir / (name + '.log')
        result = run_command(argv, project, env, log, timeout)
        report['commands'][name] = result
        report['evidence'][name + '_log'] = str(log)
        if result['exit_code'] is None or result['timed_out']:
            raise RuntimeError(name + ' was not completed: ' + (result['error'] or 'timeout'))
        check(name + '_full_stream' if name == 'ffprobe' else name + '_full_decode',
              result['exit_code'] == 0 and result['error'] is None,
              name + ' failed with exit status ' + str(result['exit_code']))

    try:
        check('final_exists', final.is_file() and not final.is_symlink(),
              'required physical out/final.mp4 is missing')
        before = _file_identity(final)
        evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        probe_path = evidence_dir / 'ffprobe.json'
        with _private_file(probe_path):
            pass
        report['evidence']['ffprobe'] = str(probe_path)
        fields = ('index,codec_type,width,height,pix_fmt,sample_aspect_ratio,color_range,color_space,'
                  'color_transfer,color_primaries,r_frame_rate,avg_frame_rate,nb_read_frames,'
                  'time_base,start_pts,duration_ts,sample_rate,channels')
        command('ffprobe', [os.fspath(ffprobe), '-v', 'error', '-count_frames', '-show_entries',
                           'stream=' + fields, '-of', 'json', '-o', str(probe_path), str(final)])
        probe = json.loads(probe_path.read_text(encoding='utf-8'))
        streams = probe.get('streams', [])
        videos = [row for row in streams if row.get('codec_type') == 'video']
        audios = [row for row in streams if row.get('codec_type') == 'audio']
        check('video_stream', len(videos) == 1, 'expected exactly one video stream')
        check('audio_stream', len(audios) == 1, 'expected exactly one audio stream')
        video, audio = videos[0], audios[0]
        report['video'], report['audio'] = video, audio
        check('video_geometry', video.get('width') == expected_width and video.get('height') == expected_height
              and isinstance(video.get('pix_fmt'), str) and bool(video['pix_fmt']), 'video geometry/pixel format mismatch')
        check('video_frame_count', str(video.get('nb_read_frames')) == str(expected_frames), 'full ffprobe frame count mismatch')
        # r_frame_rate 必须精确（排除 29.97 等真实错帧率，NTSC 的 r=30000/1001）；
        # avg_frame_rate 由容器时长导出，帧级 pts 在 1/15360 时基上的舍入可累积
        # ~1ms 量级偏差（900 帧实测 30.0016），故用相对容差 2e-3。真实缺帧由
        # video_frame_count（精确相等）与解码 pts 归一化检查拦截。
        avg_fps = _fraction(video.get('avg_frame_rate'))
        check('video_frame_rate', _fraction(video.get('r_frame_rate')) == expected_fps
              and avg_fps is not None
              and abs(float(avg_fps) - float(expected_fps)) <= 2e-3 * float(expected_fps),
              'video frame rate mismatch')
        check('video_start_time', _fraction(video.get('start_pts')) == 0, 'video must start at time zero')
        check('audio_format', str(audio.get('sample_rate')) == '48000' and audio.get('channels') == 2,
              'expected 48 kHz stereo audio before PCM normalization')
        check('audio_start_time', _fraction(audio.get('start_pts')) == 0, 'audio must start at time zero')
        video_path = evidence_dir / 'video.framemd5'
        with _private_file(video_path):
            pass
        report['evidence']['video_framemd5'] = str(video_path)
        command('video', [os.fspath(ffmpeg), '-v', 'error', '-nostdin', '-y', '-xerror',
                          '-err_detect', 'explode', '-copyts', '-i', str(final), '-map', '0:v:0',
                          '-an', '-sn', '-dn', '-c:v', 'rawvideo', '-threads', '1',
                          '-fps_mode', 'passthrough', '-f', 'framemd5', str(video_path)])
        try:
            normalized, digest = _normalize_framemd5(video_path, video, expected_frames, expected_fps)
        except (ValueError, UnicodeError) as error:
            checks['video_timestamps'] = checks['video_decoded_frame_count'] = False
            raise ValueError(str(error)) from error
        checks['video_timestamps'] = checks['video_decoded_frame_count'] = True
        normalized_path = evidence_dir / 'video.normalized.json'
        _json_write_private(normalized_path, normalized)
        report['evidence']['video_normalized'] = str(normalized_path)
        report['video_digest'] = digest
        audio_path = evidence_dir / 'audio.sha256'
        with _private_file(audio_path):
            pass
        report['evidence']['audio_sha256'] = str(audio_path)
        audio_frames_path = evidence_dir / 'audio.framemd5'
        with _private_file(audio_frames_path):
            pass
        report['evidence']['audio_framemd5'] = str(audio_frames_path)
        command('audio', [os.fspath(ffmpeg), '-v', 'error', '-nostdin', '-y', '-xerror',
                          '-err_detect', 'explode', '-copyts', '-i', str(final), '-map', '0:a:0', '-vn', '-sn', '-dn',
                          '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', '-f', 'hash',
                          '-hash', 'sha256', str(audio_path),
                          '-map', '0:a:0', '-vn', '-sn', '-dn', '-ar', '48000', '-ac', '2',
                          '-c:a', 'pcm_s16le', '-f', 'framemd5', str(audio_frames_path)])
        match = re.fullmatch(r'SHA256=([0-9a-fA-F]{64})\s*', audio_path.read_text(encoding='ascii'))
        check('audio_nonempty', match is not None and match.group(1).lower() != hashlib.sha256(b'').hexdigest(),
              'missing, invalid or empty decoded PCM SHA256')
        report['audio_digest'] = match.group(1).lower()
        try:
            audio_normalized, timeline_digest = _normalize_audio_framemd5(
                audio_frames_path, audio, expected_frames, expected_fps)
        except (ValueError, UnicodeError) as error:
            checks['audio_timestamps'] = checks['audio_duration'] = False
            raise ValueError(str(error)) from error
        checks['audio_timestamps'] = checks['audio_duration'] = True
        audio_normalized_path = evidence_dir / 'audio.normalized.json'
        _json_write_private(audio_normalized_path, audio_normalized)
        report['evidence']['audio_normalized'] = str(audio_normalized_path)
        report['audio_timeline_digest'] = timeline_digest
        report['audio_timing'] = audio_normalized
        check('source_unchanged', _file_identity(final) == before, 'final.mp4 changed during verification')
        report['status'] = 'passed'
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError) as error:
        report['status'], report['error'] = 'failed', type(error).__name__ + ': ' + str(error)
    except (OSError, RuntimeError) as error:
        report['status'], report['error'] = 'unknown', type(error).__name__ + ': ' + str(error)
    return report
