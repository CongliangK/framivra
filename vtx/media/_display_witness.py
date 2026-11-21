"""Private, same-decoder witness for the dimensions entering a clip filter.

The caller owns the input and selects the appropriate executor. A successful
witness describes uninterrupted decoding, not an arbitrary seek.
"""
from subprocess import CompletedProcess, DEVNULL, PIPE, Popen, TimeoutExpired
from threading import Condition, Thread
from time import monotonic


_CAPTURE_LIMIT = 16 * 1024


def capture_witness(argv, *, deadline, limit=_CAPTURE_LIMIT):
    """Bound capture of ordinary local renderer output and reap the process."""
    if limit < 1 or deadline <= monotonic():
        raise TimeoutError('decoded video geometry exhausted its operation budget')
    streams = (bytearray(), bytearray())
    ready = Condition()
    finished = 0
    failed = []

    def drain(pipe, data):
        nonlocal finished
        try:
            while True:
                block = pipe.read(4096)
                if not block:
                    break
                with ready:
                    if sum(map(len, streams)) + len(block) > limit:
                        raise ValueError('decoded video geometry exceeded its output budget')
                    data.extend(block)
        except (OSError, ValueError) as error:
            with ready:
                failed.append(error)
        finally:
            with ready:
                finished += 1
                ready.notify_all()

    child = Popen(argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, bufsize=0)
    readers = []
    try:
        for pipe, data in zip((child.stdout, child.stderr), streams):
            reader = Thread(target=drain, args=(pipe, data), daemon=True)
            reader.start()
            readers.append(reader)
        with ready:
            while finished < len(readers) and not failed:
                left = deadline - monotonic()
                if left <= 0:
                    raise TimeoutError('decoded video geometry exceeded its time budget')
                ready.wait(left)
            if failed:
                raise failed[0]
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError('decoded video geometry exceeded its time budget')
        try:
            child.wait(timeout=remaining)
        except TimeoutExpired:
            raise TimeoutError('decoded video geometry exceeded its time budget') from None
        if monotonic() >= deadline:
            raise TimeoutError('decoded video geometry exceeded its time budget')
        return CompletedProcess(argv, child.returncode, *map(bytes, streams))
    finally:
        try:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        finally:
            for pipe in (child.stdout, child.stderr):
                pipe.close()
            for reader in readers:
                reader.join(timeout=0.1)


def dimension_guard(width, height, *, shrink=False):
    """Reject a mismatch before a crop or scale can hide it."""
    if any(type(value) is not int or value < 1 for value in (width, height)):
        raise ValueError('invalid expected decoded video dimensions')
    right_width = '1' if shrink else 'iw'
    right_height = '1' if shrink else 'ih'
    check = f"if(eq(iw,{width})*eq(ih,{height}),{right_width},0)"
    return f"crop=w='{check}':h={right_height}:x=0:y=0:exact=1"


def witness_argv(path, width, height):
    # Do not limit packets, frames or time within the media. A changed frame
    # reinitializes the filter, and must encounter the same assertion again.
    return ['ffmpeg', '-v', 'error', '-nostdin', '-nostats', '-xerror',
            '-stats_period', '3600', '-progress', 'pipe:1', '-reinit_filter', '1',
            '-i', str(path), '-map', '0:v:0', '-an', '-vf', dimension_guard(width, height, shrink=True),
            # FFmpeg 6's default encoder tick can round different MKV timestamps
            # to the same frame number. The null sink needs no nominal FPS grid.
            '-fps_mode', 'passthrough', '-enc_time_base', '1:1000000',
            '-c:v', 'wrapped_avframe', '-f', 'null', '-']


def verify_source(c, path, width, height, *, deadline):
    argv = witness_argv(path, width, height)
    owned = getattr(c, '_windows_svg_media', None)
    result = (owned.execute(argv, text=False, capture_limit=_CAPTURE_LIMIT, deadline=deadline)
              if owned is not None else capture_witness(argv, deadline=deadline))
    return witnessed_frames(result)


def witnessed_frames(result):
    """Return the number of checked frames, only for a positive clean ending."""
    if result.returncode or result.stderr or not isinstance(result.stdout, bytes):
        raise ValueError('decoded video geometry could not be confirmed')
    if not result.stdout.endswith(b'\n'):
        raise ValueError('incomplete decoded geometry progress')
    fields = {}
    ended = False
    try:
        for row in result.stdout.decode('ascii').splitlines():
            key, sep, value = row.partition('=')
            if not sep or not key or ended or key in fields:
                raise ValueError('invalid decoded geometry progress')
            if key != 'progress':
                fields[key] = value
                continue
            if value == 'end':
                frames = fields.get('frame', '')
                if not frames.isdecimal() or int(frames) <= 0:
                    raise ValueError('decoded geometry progress has no frames')
                ended = True
            elif value != 'continue':
                raise ValueError('invalid decoded geometry progress')
            fields.clear()
    except UnicodeError as error:
        raise ValueError('invalid decoded geometry progress') from error
    if not ended:
        raise ValueError('incomplete decoded geometry progress')
    return int(frames)
