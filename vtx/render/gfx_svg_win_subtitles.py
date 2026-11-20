"""Plain-text captions for the ordinary owned Windows SVG compositor.

The locked demo and POSIX ASS paths do not enter this profile. Only generated
filter syntax and private, pinned UTF-8 text/font files reach FFmpeg.
"""
from bisect import bisect_left
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct
import unicodedata

from ..pipeline.constraints import fps_expression
from .gfx_svg_win_input import read, relative_parts


_SOURCE_LIMIT = 512 * 1024
_FONT_LIMIT = 32 * 1024 * 1024
_MAX_CUES = 256
_MAX_TEXT = 4096
_FONTS = ('arialbd.ttf', 'msyhbd.ttc')
_PROFILE = 'windows-svg-plain-captions/v1'
_NO_FONTCONFIG = b'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd"><fontconfig></fontconfig>\n'
_CAPTION_JOB = ContextVar('windows_svg_caption_job', default=None)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _integer(value):
    return type(value) is int or (type(value) is float and value.is_integer())


def _cues(subtitles, duration):
    if not isinstance(subtitles, list) or len(subtitles) > _MAX_CUES:
        raise ValueError(f'Windows SVG requires a subtitle list of at most {_MAX_CUES} cues')
    result = []
    for i, cue in enumerate(subtitles):
        at = f'Windows SVG subtitle cue {i + 1}'
        if not isinstance(cue, dict):
            raise ValueError(f'{at} must be an object')
        start, stop, text = (cue.get(key) for key in ('in', 'out', 'text'))
        if not (_integer(start) and _integer(stop) and 0 <= start < stop <= duration):
            raise ValueError(f'{at} requires an integer half-open interval inside the timeline')
        if not isinstance(text, str) or len(text) > _MAX_TEXT:
            raise ValueError(f'{at} requires bounded plain-text content')
        if re.search(r'\{[^}]*\\|\\[nh]', text):
            raise ValueError(f'{at} does not support ASS styling or escape codes other than \\N')
        text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\\N', '\n')
        lines = text.split('\n')
        if (not 1 <= len(lines) <= 4 or any(len(line) > 120 or not any(
                unicodedata.category(ch)[0] in 'LNPS' for ch in line) for line in lines)
                or any(ch != '\n' and (unicodedata.category(ch) in ('Cc', 'Cf', 'Cs', 'Zl', 'Zp')
                        or ch in '\u115f\u1160\u2800\u3164\uffa0') for ch in text)):
            raise ValueError(f'{at} requires 1–4 visible lines of at most 120 characters without control characters')
        result.append((int(start), int(stop), text))
    result.sort()
    if any(left[1] > right[0] for left, right in zip(result, result[1:])):
        raise ValueError('Windows SVG subtitle cues must not overlap')
    return tuple(result)


def _missing_glyphs(data, chars):
    """Check the first face's Windows Unicode cmap, preferring full Unicode."""
    def unpack(fmt, offset):
        return struct.unpack_from('>' + fmt, data, offset)

    try:
        face = unpack('I', 12)[0] if data.startswith(b'ttcf') else 0
        tables = unpack('H', face + 4)[0]
        cmap, size = next((offset, size) for tag, _, offset, size in
                          (unpack('4sIII', face + 12 + 16 * i) for i in range(tables))
                          if tag == b'cmap')
        entries = {encoding: cmap + offset for platform, encoding, offset in
                   (unpack('HHI', cmap + 4 + 8 * i) for i in range(unpack('H', cmap + 2)[0]))
                   if platform == 3 and offset < size}
        start = entries[10] if 10 in entries else entries[1]
        kind = unpack('H', start)[0]
        if kind == 12:
            count = unpack('I', start + 12)[0]
            if start + 16 + count * 12 > cmap + size:
                raise ValueError
            groups = [unpack('III', start + 16 + 12 * i) for i in range(count)]
            last = [end for _, end, _ in groups]

            def has(cp):
                i = bisect_left(last, cp)
                return i < count and groups[i][0] <= cp and groups[i][2] + cp - groups[i][0] != 0
        elif kind == 4:
            count = unpack('H', start + 6)[0] // 2
            ends = unpack(f'{count}H', start + 14)
            base = start + 16 + 2 * count
            starts = unpack(f'{count}H', base)
            delta = unpack(f'{count}h', base + 2 * count)
            offsets = unpack(f'{count}H', base + 4 * count)

            def has(cp):
                i = bisect_left(ends, cp)
                if i == count or cp < starts[i]:
                    return False
                index = cp
                if offsets[i]:
                    address = base + 4 * count + 2 * i + offsets[i] + 2 * (cp - starts[i])
                    if address + 2 > cmap + size:
                        raise ValueError
                    index = unpack('H', address)[0]
                    if not index:
                        return False
                return (index + delta[i]) % 65536 != 0
        else:
            raise ValueError
        return {ch for ch in chars if not has(ord(ch))}
    except (struct.error, IndexError, KeyError, StopIteration, ValueError) as error:
        raise ValueError('Windows SVG subtitle font has no supported Unicode mapping') from error


def _system_font(leaf):
    """Read a fixed OS font, allowing the ordinary Windows servicing hard link."""
    import ctypes as C
    from ctypes import wintypes as W
    from .gfx_svg_win_shell import _system_windows_path
    from .gfx_svg_win_input import _final_path, _info, _open_child, _root_parts, _volume_root
    from .gfx_svg_win_system import _facts

    if leaf not in _FONTS:
        raise ValueError('Windows SVG subtitle font must be from the fixed system set')
    system = _system_windows_path()
    drive, parents = _root_parts(system)
    path = system / 'Fonts' / leaf
    with _volume_root(drive) as (kernel, nt, handle, prefix), ExitStack() as pins:
        device = prefix.rstrip('\\')
        for part in (*parents, 'Fonts'):
            handle = _open_child(pins, kernel, nt, handle, part)
            _info(kernel, handle, directory=True)
            prefix += part
            if _final_path(kernel, handle) != prefix:
                raise ValueError('Windows SVG subtitle font directory name differs')
            prefix += '\\'
        handle = _open_child(pins, kernel, nt, handle, leaf)
        facts = _facts(kernel, handle, path, device, directory=False)
        size, links = facts[3:5]
        if not 0 < size <= _FONT_LIMIT or links not in (1, 2):
            raise ValueError('Windows SVG subtitle font has an unsupported size or link count')
        data, buffer, count = bytearray(), C.create_string_buffer(64 * 1024), W.DWORD()
        while True:
            amount = min(size - len(data) + 1, len(buffer))
            if not kernel.ReadFile(handle, buffer, amount, C.byref(count), None):
                raise C.WinError(C.get_last_error())
            if not count.value:
                break
            data.extend(buffer[:count.value])
            if len(data) > size:
                raise ValueError('Windows SVG subtitle font grew during read')
        if len(data) != size or _facts(kernel, handle, path, device, directory=False) != facts:
            raise ValueError('Windows SVG subtitle font changed during read')
        return bytes(data)


def _font(cues):
    chars = {ch for _, _, text in cues for ch in text if ch != '\n'}
    for leaf in _FONTS:
        try:
            data = _system_font(leaf)
        except FileNotFoundError:
            continue
        if not _missing_glyphs(data, chars):
            return leaf, data
    raise ValueError('Windows SVG subtitle text is not covered by the installed Arial Bold or Microsoft YaHei Bold font')


@dataclass(frozen=True)
class Captions:
    cues: tuple
    font: str
    font_bytes: bytes
    font_sha256: str

    def identity(self):
        # Reload the small implementation input, just like the audio producer.
        return {'profile': _PROFILE, 'font': self.font, 'sha256': self.font_sha256,
                'implementation': _sha(Path(__file__).read_bytes())}

    def window(self, a, b):
        return [(max(start, a) - a, min(stop, b) - a, text) for start, stop, text in self.cues
                if start < b and stop > a]


def prepare(c):
    """Qualify inputs before producer discovery, and parse exactly the hashed bytes."""
    from ..media.subtitles_src import resolve_subtitles_src

    src = c.tl.get('subtitles_src')
    if src not in ('', None) and not c.tl.get('subtitles'):
        parts = relative_parts(src)
        if parts[0].casefold() in ('cache', 'out') or Path(src).suffix.lower() not in ('.srt', '.json'):
            raise ValueError('Windows SVG requires a project-relative .srt or .json subtitle source outside cache/out')

        def current_bytes():
            return read(c.root, src, limit=_SOURCE_LIMIT, expected_path=c.root.joinpath(*parts))

        try:
            raw = current_bytes()
        except OSError as error:
            raise ValueError(f'Windows SVG cannot read subtitles_src {src!r}: {error}') from error
        expected = _sha(raw)

        def checked_digest():
            if _sha(current_bytes()) != expected:
                raise ValueError('Windows SVG subtitle source changed after parsing')
            return expected

        c._read_subtitles_bytes = lambda: raw
        c._subtitles_input_digest = checked_digest
    resolve_subtitles_src(c)
    if c.subtitles_errors:
        raise ValueError('; '.join(c.subtitles_errors))
    # An absent array is permitted in existing external-file timelines.
    cues = _cues(c.tl.get('subtitles', []), c.dur)
    c._windows_subtitles_prepared = True
    if cues and c._svg_all:
        name, data = _font(cues)
        c._windows_subtitles = Captions(cues, name, data, _sha(data))


def job_environment(media, cmd):
    active = _CAPTION_JOB.get()
    if active is not None and active[0] is media and cmd[0] == 'ffmpeg' and '-/filter_complex' in cmd:
        return active[1]
    return media.environment


def _draw_filter(start, stop, text, font_name, text_name, height):
    size = max(5, min(58, round(height * 58 / 1080)))
    # Conservative width guard: size down authored long lines rather than let
    # drawtext cut the right edge. CJK uses full ems in both admitted fonts.
    length = max(len(line) for line in text.split('\n'))
    margin = max(2, round(height * 60 / 1080))
    outline = max(1, round(height * 3 / 1080))
    shadow = max(1, round(height * 2 / 1080))
    return (f"drawtext=fontfile={font_name}:textfile={text_name}:expansion=none:reload=0:"
            f"fontcolor=0xF8FBFF:bordercolor=black:borderw={outline}:shadowcolor=black@0.5:"
            f"shadowx={shadow}:shadowy={shadow}:fontsize='max(1,min({size},(w-{2 * margin + 2 * outline})/{length + 1}))':"
            f"text_align=C:x=(w-tw)/2:y=h-th-{margin}:fix_bounds=1:enable='between(n,{start},{stop - 1})'")


def run(c, argv, *, a, b, label):
    """Append captions after the last layer and launch with private FFmpeg inputs."""
    media = c._windows_svg_media
    profile = c._windows_subtitles
    cues = profile.window(a, b)
    if not cues or c.dry:
        return c.run(argv)
    index = argv.index('-filter_complex')
    graph, terminator = argv[index + 1], f'[{label}]null[vout]'
    if not graph.endswith(terminator):
        raise ValueError('Windows SVG caption graph must terminate at the compositor output')
    with ExitStack() as files:
        font = files.enter_context(media.private_input(profile.font_bytes, Path(profile.font).suffix))
        config = files.enter_context(media.private_input(_NO_FONTCONFIG, '.conf'))
        filters = []
        for start, stop, text in cues:
            staged = files.enter_context(media.private_input(text.encode('utf-8'), '.txt'))
            filters.append(_draw_filter(start, stop, text, font.name, staged.name, c.h))
        # Repair a framesync PTS hole before counting frames, not at the
        # output muxer. Otherwise output CFR would duplicate already-painted
        # pixels or shift a one-frame cue to the wrong global frame.
        clock = f'fps=fps={fps_expression(c.fps)}:start_time=0'
        rendered = graph[:-len(terminator)] + f'[{label}]' + ','.join([clock, *filters]) + '[vout]'
        script = files.enter_context(media.private_input(rendered.encode('utf-8'), '.graph'))
        environment = media.process_environment(FONTCONFIG_FILE=config.name, FONTCONFIG_PATH='.')
        token = _CAPTION_JOB.set((media, environment))
        try:
            return c.run([*argv[:index], '-/filter_complex', script.name, *argv[index + 2:]])
        finally:
            _CAPTION_JOB.reset(token)
