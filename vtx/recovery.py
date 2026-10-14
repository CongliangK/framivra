"""Interrupted media recovery: verified cache hits and atomic FFmpeg publication.

Only single-file media outputs owned by this project participate. Pipe/null and
image outputs retain their existing execution paths. A successful encoder plus
full decode certifies a file; timestamps or container headers alone do not.
"""
import hashlib
import json
import math
import subprocess
import wave
from pathlib import Path

from . import fstxn

FORMATS = {'.wav': 'wav', '.mp4': 'mp4', '.mov': 'mov', '.mkv': 'matroska'}


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _marker(path):
    return Path(str(path) + '.complete.json')


def _inspect(path, kind=None):
    """Return certified metadata, or None for missing/truncated/undecodable media."""
    path = Path(path)
    try:
        if not path.is_file() or not path.stat().st_size:
            return None
        if (kind or path.suffix) == '.wav':
            with wave.open(str(path), 'rb') as stream:
                samples = stream.getnframes()
                if not samples or stream.getnchannels() != 2 or stream.getframerate() != 48000:
                    return None
                expected = samples * stream.getnchannels() * stream.getsampwidth()
                actual = 0
                while True:
                    data = stream.readframes(32768)
                    if not data:
                        break
                    actual += len(data)
                if actual != expected:
                    return None
            frames = None
        else:
            probe = subprocess.run(
                ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames',
                 '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0', str(path)],
                capture_output=True, text=True)
            count = probe.stdout.strip()
            if probe.returncode or probe.stderr.strip() or not count.isdigit() or int(count) <= 0:
                return None
            decoded = subprocess.run(
                ['ffmpeg', '-v', 'error', '-xerror', '-i', str(path),
                 '-map', '0', '-f', 'null', '-'], capture_output=True, text=True)
            if decoded.returncode or decoded.stderr.strip():
                return None
            frames = int(count)
        return {'schema': 1, 'sha256': _digest(path), 'frames': frames}
    except (OSError, ValueError, EOFError, wave.Error):
        return None


def media_cache_valid(path, frames=None, allow_short_verified=False):
    """Read-only: trust a successful decode's digest, otherwise verify legacy data."""
    path = Path(path)
    try:
        rec = json.loads(_marker(path).read_text())
        actual = rec.get('frames')
        count_ok = (frames is None or actual == frames or
                    (allow_short_verified and isinstance(actual, int) and 0 < actual <= frames))
        return rec.get('schema') == 1 and rec.get('sha256') == _digest(path) and count_ok
    except (OSError, ValueError, TypeError, AttributeError):
        if _marker(path).exists():
            return False  # a damaged/mismatched certificate is not legacy media
    rec = _inspect(path)
    return rec is not None and (frames is None or rec['frames'] == frames)


def audio_cache_valid(c, path, sid):
    """Legacy extraction needs source duration, not merely a finalized short WAV.

    A new successful transaction's digest certifies full command completion.
    Older files with unknown/inexact source duration are conservatively rebuilt
    once; their new record then permits warm reuse without source probing.
    """
    path = Path(path)
    if not media_cache_valid(path):
        return False
    if _marker(path).exists():
        return True
    try:
        with wave.open(str(path), 'rb') as audio:
            actual = audio.getnframes()
        source = c.src_path(sid)
        try:
            with wave.open(str(source), 'rb') as audio:
                expected = round(audio.getnframes() * 48000 / audio.getframerate())
        except (wave.Error, EOFError):
            probe = subprocess.run(
                ['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                 '-show_entries', 'stream=duration:format=duration', '-of', 'json', str(source)],
                capture_output=True, text=True)
            if probe.returncode:
                return False
            info = json.loads(probe.stdout)
            streams = info.get('streams', [])
            duration = float(streams[0].get('duration') or info.get('format', {}).get('duration'))
            if not math.isfinite(duration) or duration <= 0:
                return False
            expected = round(duration * 48000)
        return abs(actual - expected) <= 1
    except (OSError, ValueError, TypeError, KeyError, IndexError, wave.Error, EOFError):
        return False


def media_output(c, cmd):
    """Recognize an owned single-file output, not arbitrary FFmpeg invocations."""
    if not cmd or not str(cmd[0]).endswith('ffmpeg') or not hasattr(c, 'root'):
        return None
    path = Path(str(cmd[-1]))
    if path.suffix.lower() not in FORMATS:
        return None
    from .infra import ensure_inside_project
    return ensure_inside_project(path, c.root, what='media output')


def atomic_ffmpeg(c, cmd, execute):
    """execute(argv) preserves caller error policy; failed encodes never publish.

    Same-target thread/flock exclusion also lets us retire orphan .tmp files
    safely after a killed writer. A crash between media and marker publication
    leaves valid legacy media, which must be decoded again before reuse.
    """
    target = media_output(c, cmd)
    if target is None or c.dry:
        return execute(cmd)
    # ffmpeg 经 argv 接收输出路径（子进程写者）——locked_staging 给同目录
    # 随机名真实路径（/proc 锚定路径会被子进程拒开），提交前 inode 对账。
    with fstxn.locked_staging(target) as temporary:
        current = Path(temporary).resolve()
        for orphan in target.parent.glob('.' + target.name + '.*.tmp'):
            if orphan != current:
                fstxn.unlink(orphan)
        # Keep the original argv and output format; only replace the destination.
        # -y: the mkstemp staging file already exists, so a caller that omitted
        # -y would otherwise hit FFmpeg's interactive overwrite prompt.
        staged = list(cmd[:-1]) + ['-y', '-f', FORMATS[target.suffix.lower()], str(temporary)]
        result = execute(staged)
        if getattr(result, 'returncode', 0):
            raise RuntimeError('FFmpeg failed (%s); media was not published: %s' %
                               (result.returncode, getattr(result, 'stderr', '')[-1500:]))
        rec = _inspect(temporary, target.suffix.lower())
        if rec is None:
            raise RuntimeError('FFmpeg produced incomplete media: ' + str(target))
        for option in ('-frames:v', '-vframes'):
            # -frames:v is a cap: a short source legitimately ends before it.
            if option in cmd and rec['frames'] is not None and rec['frames'] > int(cmd[cmd.index(option) + 1]):
                raise RuntimeError('FFmpeg produced more frames than requested: ' + str(target))
        from .cache_state import verify_input_identity
        verify_input_identity(c)
    fstxn.atomic_write(_marker(target), json.dumps(rec, sort_keys=True))
    return result
