"""Interrupted media recovery: certified cache hits and atomic media publication.

Only single-file media outputs owned by this project participate. Pipe/null and
image outputs retain their existing execution paths. A successful encoder plus
full decode certifies a file; timestamps or container headers alone do not.
"""
import hashlib
import json
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


def completion_marker(path):
    """The exact completion-certificate companion of an owned media output."""
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
    """Read-only: require a completed producer's certificate and matching bytes.

    Decodable media alone cannot prove that its producer finished. Uncertified
    caches are rebuilt once; this function never adopts or removes old media.
    """
    path = Path(path)
    try:
        rec = json.loads(completion_marker(path).read_text())
        actual = rec.get('frames')
        count_ok = (frames is None or actual == frames or
                    (allow_short_verified and isinstance(actual, int) and 0 < actual <= frames))
        return rec.get('schema') == 1 and rec.get('sha256') == _digest(path) and count_ok
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def media_output(c, cmd):
    """Recognize an owned single-file output, not arbitrary FFmpeg invocations."""
    from .infra import ensure_inside_project, is_ffmpeg_cmd
    if not is_ffmpeg_cmd(cmd) or not hasattr(c, 'root'):
        return None
    path = Path(str(cmd[-1]))
    if path.suffix.lower() not in FORMATS:
        return None
    return ensure_inside_project(path, c.root, what='media output')


def atomic_media(c, target, producer, *, max_frames=None,
                 before_commit=None, after_commit=None):
    """Publish one project-owned media file only after its producer succeeds.

    producer(staging_path) must raise on failure and specify its output format
    independently of the random .tmp suffix. Its return value is preserved.
    The target lock covers production, full inspection, input-identity checking,
    publication, the completion certificate and both zero-argument callbacks.
    before_commit runs after validation, before replacement, followed by a final
    input-identity check; after_commit runs after the certificate. Callbacks must
    not reacquire this same target lock. Their own side effects are not rolled back.
    An after-commit error leaves published media but propagates, never success.
    Dry runs invoke no producer or callbacks and create no files, returning None.
    """
    from .infra import ensure_inside_project
    target = ensure_inside_project(Path(target), c.root, what='media output')
    if target.suffix.lower() not in FORMATS:
        raise ValueError('Unsupported media output: ' + str(target))
    if c.dry:
        return None

    def publish_marker():
        fstxn.atomic_write(completion_marker(target), json.dumps(rec, sort_keys=True), root=c.root)
        if after_commit is not None:
            after_commit()

    with fstxn.locked_staging(target, after_commit=publish_marker, root=c.root) as temporary:
        current = Path(temporary).resolve()
        prefix = '.' + target.name + '.'
        for orphan in target.parent.iterdir():
            # Names are literal: brackets, * and ? in an owned output filename
            # must never select another target's staging files.
            if not orphan.name.startswith(prefix) or not orphan.name.endswith('.tmp'):
                continue
            # Media staging has one random token after the exact target name.
            # In particular, never claim .<target>.complete.json.<token>.tmp.
            token = orphan.name[len(prefix):-4]
            if orphan != current and token and '.' not in token:
                fstxn.unlink(orphan, root=c.root)
        result = producer(temporary)
        rec = _inspect(temporary, target.suffix.lower())
        if rec is None:
            raise RuntimeError('Media producer produced incomplete media: ' + str(target))
        if max_frames is not None and rec['frames'] is not None and rec['frames'] > max_frames:
            raise RuntimeError('Media producer produced more frames than requested: ' + str(target))
        from .cache_state import verify_input_identity
        verify_input_identity(c)
        if before_commit is not None:
            before_commit()
            # History copies/hashes and ledger locks can make this callback
            # lengthy. Do not certify inputs that changed during that window.
            verify_input_identity(c)
    return result


def atomic_ffmpeg(c, cmd, execute, *, before_commit=None, after_commit=None):
    """FFmpeg argv/error-policy adapter for the single media transaction."""
    target = media_output(c, cmd)
    if target is None or c.dry:
        return execute(cmd)

    def produce(temporary):
        # mkstemp created the destination; -y prevents an overwrite prompt and
        # -f preserves the requested format despite the staging .tmp suffix.
        staged = list(cmd[:-1]) + ['-y', '-f', FORMATS[target.suffix.lower()], str(temporary)]
        result = execute(staged)
        if getattr(result, 'returncode', 0):
            raise RuntimeError('FFmpeg failed (%s); media was not published: %s' %
                               (result.returncode, getattr(result, 'stderr', '')[-1500:]))
        return result

    # Both options are caps: a short source legitimately ends before the limit.
    caps = [int(cmd[cmd.index(option) + 1]) for option in ('-frames:v', '-vframes')
            if option in cmd]
    return atomic_media(c, target, produce, max_frames=min(caps) if caps else None,
                        before_commit=before_commit, after_commit=after_commit)
