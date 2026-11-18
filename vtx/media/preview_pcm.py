"""Direct native-preview input for the small RIFF subset emitted by PCM extraction."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import struct

from ..pipeline.constraints import AUDIO_SAMPLE_RATE

_PCM_FMT = b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, 1, 2, AUDIO_SAMPLE_RATE,
                                    AUDIO_SAMPLE_RATE * 4, 4, 16)


def _identity(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


@dataclass(frozen=True)
class DirectPCM:
    path: Path
    identity: tuple

    def verify(self):
        if _identity(self.path.lstat()) != self.identity:
            raise ValueError(f'direct PCM source changed during native preview: {self.path.name}')


def _pcm_bytes(stream, file_size):
    # Deliberately not a RIFF walker: only PCM fmt16, optional ordinary ISFT
    # encoder text, then terminal data. No masks, BWF, wavl, cue, RF64, etc.
    head = stream.read(36)
    if head[:4] != b'RIFF' or head[8:] != _PCM_FMT:
        return None
    if struct.unpack('<I', head[4:8])[0] != file_size - 8:
        # Includes ordinary FFmpeg pipe WAV with unknown RIFF/data lengths.
        # Extraction may decode it; direct input lacks a proven extent.
        return None
    chunk = stream.read(8)
    if chunk[:4] == b'LIST' and len(chunk) == 8:
        size = struct.unpack('<I', chunk[4:])[0]
        if not 14 <= size <= 128 or size % 2:
            return None
        info = stream.read(size)
        if len(info) != size or info[:8] != b'INFOISFT':
            return None
        n = struct.unpack('<I', info[8:12])[0]
        if n < 2 or size != 12 + n + n % 2 or any(info[11 + n:]):
            return None
        if not all(32 <= b <= 126 for b in info[12:11 + n]):
            return None
        chunk = stream.read(8)
    if chunk[:4] != b'data' or len(chunk) != 8:
        return None
    length = struct.unpack('<I', chunk[4:])[0]
    end = stream.tell() + length
    return length if length and length % 4 == 0 and end == file_size else None


def qualify(c, path):
    """Check known layout, FFmpeg's actual stream/clock and operation identity."""
    path = Path(path)
    if (path.suffix.lower() != '.wav' or path.absolute() != path.resolve()
            or c.root.resolve() not in path.absolute().parents):
        return None
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
                 | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError('direct PCM requires a regular file')
        length = _pcm_bytes(stream, st.st_size)
    source = DirectPCM(path, _identity(st))
    source.verify()
    if length is None:
        return None
    probe = c.run(['ffprobe', '-v', 'error', '-read_intervals', '%+#1',
                   '-show_streams', '-show_packets', '-show_format', '-show_entries',
                   'format=format_name:stream=codec_name,codec_type,sample_fmt,sample_rate,channels,'
                   'channel_layout,time_base,start_pts,duration_ts:packet=pts,stream_index',
                   '-of', 'json', str(path)], need=False)
    source.verify()
    if probe.returncode < 0:
        raise RuntimeError(f'direct PCM ffprobe interrupted ({probe.returncode}): {probe.stderr[-400:]}')
    if probe.returncode or probe.stderr:
        return None
    try:
        info = json.loads(probe.stdout)
        if info['format']['format_name'] != 'wav':
            return None
        audio, = info['streams']
        packet, = info['packets']
        if packet['pts'] != 0 or packet['stream_index'] != 0 or not isinstance(audio, dict):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    expected = {'codec_name': 'pcm_s16le', 'codec_type': 'audio', 'sample_fmt': 's16',
                'sample_rate': str(AUDIO_SAMPLE_RATE), 'channels': 2,
                'time_base': f'1/{AUDIO_SAMPLE_RATE}', 'duration_ts': length // 4}
    if (any(audio.get(key) != value for key, value in expected.items())
            or audio.get('channel_layout') not in (None, 'stereo')
            or audio.get('start_pts') not in (None, 0)):
        return None
    return source
