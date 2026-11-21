"""Private, same-decoder witness for the dimensions entering a clip filter.

The caller owns the input and bounds the subprocess and its captured output.
A successful witness describes uninterrupted decoding, not an arbitrary seek.
"""


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
            '-fps_mode', 'passthrough', '-c:v', 'wrapped_avframe', '-f', 'null', '-']


def witnessed_frames(result):
    """Return the number of checked frames, only for a positive clean ending."""
    if result.returncode or result.stderr or not isinstance(result.stdout, bytes):
        raise ValueError('decoded video geometry could not be confirmed')
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
