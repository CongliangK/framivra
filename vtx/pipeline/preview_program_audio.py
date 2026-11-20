"""Bounded whole-program PCM for the qualified Linux window worker only."""
import hashlib
import io
import json
from pathlib import Path
import struct
import wave

from .constraints import AUDIO_SAMPLE_RATE, frame_sample
from ..render.png_artifact import ArtifactError, read_artifact
from ..runtime import fstxn, preview_window_isolation as isolation

_MAX_SAMPLES = AUDIO_SAMPLE_RATE * 301 // 10
_MAX_HEADER = 4096
_MAX_SOURCE_BYTES = _MAX_SAMPLES * 6 + _MAX_HEADER
_MAX_SOURCES = 4
_MAX_TRACKS = 8
_MAX_EVENTS = 16
_PCM_GUID = bytes.fromhex('0100000000001000800000aa00389b71')


class ProgramAudioError(ValueError):
    code = 'audio.program_unsupported'


def inputs(stored, root, rate):
    """Select only event-referenced sources, including all off-window events."""
    tracks = stored['audio_tracks']
    count = sum(len(track['events']) for track in tracks)
    if (stored['project']['duration_frames'] * rate.denominator * AUDIO_SAMPLE_RATE > _MAX_SAMPLES * rate.numerator
            or len(tracks) > _MAX_TRACKS or not 0 < count <= _MAX_EVENTS):
        raise ProgramAudioError('program audio requires at most 30.1 seconds, 8 tracks and 1–16 events')
    for track in tracks:
        keys = track.get('duck_by', [])
        if 'duck_params' in track or len(keys) > _MAX_TRACKS - 1 or len(keys) != len(set(keys)):
            raise ProgramAudioError('program audio only admits default duck parameters and bounded unique keys')
    events = [ev for track in tracks for ev in track['events']]
    if any(ev.get('loop') for ev in events):
        raise ProgramAudioError('program audio does not admit looped events')
    ids = {ev['src'] for ev in events}
    paths = {s['id']: root / s['path'] for s in stored['sources'] if s['id'] in ids}
    if len(paths) > _MAX_SOURCES or any(p.suffix.lower() != '.wav' for p in paths.values()):
        raise ProgramAudioError('program audio admits at most four ordinary PCM WAV sources')
    return paths


def manifest(paths, observations):
    """Bind the private copies to the same bytes the parent puts in the epoch."""
    return isolation.identity_digest('framivra.preview-window.program-audio-source-bytes/v1',
                                     [(str(p), *observations[str(p)]) for p in sorted(set(paths))])


def _pcm(data):
    """Strict single-data RIFF subset, not a general WAV demuxer."""
    def require(ok):
        if not ok:
            raise ProgramAudioError('program audio requires bounded RIFF 48 kHz stereo PCM16/24')

    def chunk(offset):
        require(offset + 8 <= len(data))
        return data[offset:offset + 4], struct.unpack_from('<I', data, offset + 4)[0], offset + 8

    require(44 <= len(data) <= _MAX_SOURCE_BYTES and data[:4] == b'RIFF'
            and data[8:12] == b'WAVE' and struct.unpack_from('<I', data, 4)[0] == len(data) - 8)
    kind, size, pos = chunk(12)
    require(kind == b'fmt ' and size in (16, 40) and pos + size <= len(data))
    tag, channels, rate, byte_rate, align, bits = struct.unpack_from('<HHIIHH', data, pos)
    if size == 40:
        extra, valid, mask = struct.unpack_from('<HHI', data, pos + 16)
        require(tag == 0xfffe and extra == 22 and valid == bits and mask in (0, 3)
                and data[pos + 24:pos + 40] == _PCM_GUID)
    else:
        require(tag == 1)
    require(channels == 2 and rate == AUDIO_SAMPLE_RATE and bits in (16, 24)
            and align == bits // 4 and byte_rate == rate * align)
    kind, size, pos = chunk(pos + size)
    if kind == b'LIST':
        # The common FFmpeg INFO encoder tag, not wavl or arbitrary nested chunks.
        end = pos + size
        require(size >= 14 and size % 2 == 0 and end + 8 <= min(len(data), _MAX_HEADER)
                and data[pos:pos + 8] == b'INFOISFT')
        text_size = struct.unpack_from('<I', data, pos + 8)[0]
        require(text_size >= 2 and size == 12 + text_size + text_size % 2
                and not any(data[pos + 11 + text_size:end])
                and all(32 <= b <= 126 for b in data[pos + 12:pos + 11 + text_size]))
        kind, size, pos = chunk(end)
    require(kind == b'data' and pos <= _MAX_HEADER and pos + size == len(data)
            and 0 < size <= _MAX_SAMPLES * align and size % align == 0)
    return bits, size // align


def stage(paths, expected, *, root, root_identity, directory, check):
    """Snapshot once before *any* media probe, with no FFmpeg access to the original PCM."""
    copies, observed, facts = {}, {}, {}
    fstxn.mkdir(directory, root=root)
    for i, path in enumerate(sorted(set(paths.values()))):
        check()
        try:
            data = read_artifact(path, root=root, root_identity=root_identity, limit=_MAX_SOURCE_BYTES)
        except ArtifactError as error:
            raise ProgramAudioError('program WAV is not a bounded, anchored file') from error
        facts[path] = _pcm(data)
        observed[str(path)] = len(data), hashlib.sha256(data).hexdigest()
        copy = directory / f'{i}.wav'
        fstxn.atomic_write(copy, data, root=root)
        copies[path] = copy
    if manifest(paths.values(), observed) != expected:
        raise ProgramAudioError('program WAV bytes differ from the fixed-wheel input proof')
    return copies, facts


def probe(c, copies, facts):
    for original, path in copies.items():
        bits, samples = facts[original]
        result = c.run(['ffprobe', '-v', 'error', '-f', 'wav', '-read_intervals', '%+#1',
                        '-show_streams', '-show_format', '-show_packets', '-show_entries',
                        'format=format_name:stream=index,codec_type,codec_name,sample_fmt,sample_rate,channels,'
                        'channel_layout,time_base,start_pts,duration_ts:packet=pts,stream_index',
                        '-of', 'json', str(path)], need=False)
        try:
            info = json.loads(result.stdout)
            audio, = info['streams']
            packet, = info['packets']
            expected = {'index': 0, 'codec_type': 'audio', 'codec_name': f'pcm_s{bits}le',
                        'sample_fmt': 's16' if bits == 16 else 's32', 'sample_rate': str(AUDIO_SAMPLE_RATE),
                        'channels': 2, 'time_base': f'1/{AUDIO_SAMPLE_RATE}', 'duration_ts': samples}
            if (not result.returncode and not result.stderr and info['format']['format_name'] == 'wav'
                    and all(audio.get(k) == v for k, v in expected.items())
                    and audio.get('channel_layout') in (None, 'stereo') and audio.get('start_pts') in (None, 0)
                    and packet == {'pts': 0, 'stream_index': 0}):
                continue
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        raise ProgramAudioError('program WAV stream or sample count differs from its RIFF extent')


def _checked_wav(path, samples):
    with path.open('rb') as stream:
        data = stream.read(samples * 4 + _MAX_HEADER + 1)
    if _pcm(data) != (16, samples):
        raise ProgramAudioError('program audio requires exact complete PCM16 sample endpoints')
    return data


def _cut(data, total, start, end):
    with wave.open(io.BytesIO(data), 'rb') as source:
        if ((source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getnframes())
                != (2, 2, AUDIO_SAMPLE_RATE, total) or not 0 <= start < end <= total):
            raise ProgramAudioError('program PCM has an unsupported sample clock')
        source.setpos(start)
        window = source.readframes(end - start)
    if len(window) != (end - start) * 4:
        raise ProgramAudioError('program PCM window is incomplete')
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((2, 2, AUDIO_SAMPLE_RATE, 0, 'NONE', 'not compressed'))
        wav.writeframes(window)
    return output.getvalue()


def _mix(c, whole, sources, check):
    from .stages import mix_audio_wav, source_audio_extraction

    owners = {}
    for sid, (source, count) in sorted(sources.items()):
        check()
        prepared, cmd = source_audio_extraction(c, sid)
        if owners.setdefault(prepared, source) != source:
            raise ProgramAudioError('distinct bound WAV sources share a private extraction target')
        if cmd is not None:
            c.run(cmd)
        # Repeated source aliases can reuse only a completed file in this request.
        _checked_wav(prepared, count)
    try:
        mix_audio_wav(c, whole)
    except SystemExit as error:
        raise ProgramAudioError('whole-program loudness measurement did not complete') from error
    check()
    return _checked_wav(whole, frame_sample(c.dur, c.frame_rate))


def _write_window(c, wav, fa, fb, data):
    total = frame_sample(c.dur, c.frame_rate)
    window = _cut(data, total, frame_sample(fa, c.frame_rate), frame_sample(fb, c.frame_rate))
    del data
    c._preview_media_target(wav)
    fstxn.atomic_write(wav, window, root=c.root)


def render(c, wav, fa, fb, sources, check):
    whole = Path(wav).with_name('program-audio.wav')
    _write_window(c, wav, fa, fb, _mix(c, whole, sources, check))


class _RunPCM:
    """Owned by one private run; only the checked immutable bytes cross windows."""

    def __init__(self):
        self._data = None

    def clear(self):
        self._data = None

    def render(self, c, wav, fa, fb, sources, check):
        check()
        mixed = self._data is None
        if mixed:
            from ..cache.recovery import completion_marker

            whole = Path(wav).with_name('program-audio.wav')
            marker = completion_marker(whole)
            data = _mix(c, whole, sources, check)
            if json.loads(read_artifact(marker, root=c.root, limit=4096)) != {
                    'schema': 1, 'sha256': hashlib.sha256(data).hexdigest(), 'frames': None}:
                raise ProgramAudioError('whole-program PCM differs from its fresh mix certificate')
            # Do not carry a usable whole-program artifact into the next window.
            # Removing it does NOT refund its job-wide output target.
            fstxn.unlink(whole, root=c.root)
            fstxn.unlink(marker, root=c.root)
            self._data = data
        _write_window(c, wav, fa, fb, self._data)
        return mixed
