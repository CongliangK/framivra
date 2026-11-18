"""Internal Windows acceptance of fixed fixtures and one pinned canonical demo frame.

The verified vendor copy, staged fonts and isolated profile belong to one
operation. A Job controls our process tree, not the OS loader or arbitrary SVG;
this is neither a public renderer nor a security sandbox.
"""
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import time
from xml.sax.saxutils import escape

from ..runtime import windows_browser as native
from . import gfx_svg_win_input as win_input
from .gfx_cli import _png_stream_invalid_reason
from .gfx_svg_stage import _FONT_ERROR
from .gfx_svg_win_snapshot import _local_path, _pin_dir, _pin_dirs, _pin_entry, _snapshot_operation, _windows_api


_WIDTH, _HEIGHT = 64, 48
_RGBA = bytes((19, 87, 155, 255))
_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48">'
        b'<rect width="64" height="48" fill="#13579b"/></svg>')
_TEXT_SIZE = (300, 160)
# Fixed sample and decoded RGBA from the f6b19f8 native Windows audit of Inkscape 1.4.4.
# Its Fontconfig trace matched Noto SC Regular/Bold at index 2 and DejaVu Book/Bold at index 0.
_TEXT_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="160" viewBox="0 0 300 160">'
             '<text x="8" y="37" font-family="Noto Sans CJK SC" font-size="29">中渲染</text>'
             '<text x="8" y="73" font-family="Noto Sans CJK SC" font-weight="700" font-size="29">中渲染</text>'
             '<text x="8" y="109" font-family="DejaVu Sans" font-size="29">Framivra 123</text>'
             '<text x="8" y="145" font-family="DejaVu Sans" font-weight="700" font-size="29">Framivra 123</text>'
             '</svg>').encode('utf-8')
_TEXT_RGBA_SHA256 = '47b8a9149b9fbc45797fcd95ae0d265f89d9f174f9c314dbf9625f6108de63df'
# portable-v2 arrangement_read (4.5 s), checked by the frozen template generator/checker.
# Reference: two independent Inkscape 1.4.4 x64 CLI exports, reopened and decoded by FFmpeg.
_STANDARD_STEM, _STANDARD_SIZE = 'f0135', (1920, 1080)
_STANDARD_SVG_BYTES = 3419
_STANDARD_SVG_SHA256 = '7f6ef5e87080ad55248052ebd5857d2fd1be84aca2ad7f28a2edc06cdcc6cc86'
_STANDARD_RGBA_SHA256 = '3b245999b54791dcc7be240418965d8c91445a758bfc9450afbe5227c70edfd5'
_DEMO = Path(__file__).resolve().parents[2] / 'templates/demo-timeline-svg'
_FONT_NAMES = ('NotoSansCJK-Regular.ttc', 'NotoSansCJK-Bold.ttc', 'DejaVuSans.ttf', 'DejaVuSans-Bold.ttf')
_FONT_LIMIT = 24 * 1024 * 1024  # Per file; four fixed inputs, no font directory scan.
_EXPORT = ('file-open:{stem}.svg;export-type:png;export-area-page;export-width:{width};export-height:{height};'
           'export-background:#000000;export-background-opacity:0;export-filename:{png};'
           'export-do;file-close\n')
_LIMIT = 64 * 1024  # independently bounds each PNG and both diagnostic streams for a single frame
_TIMEOUT, _SETTLE_RESERVE = 30, 5
_SEQUENCE_FRAMES = 180
_SEQUENCE_CAPTURE_LIMIT = 256 * 1024  # combined streams for the 180-command shell only
_SEQUENCE_TIMEOUT, _VERIFY_RESERVE = 90, 10  # after snapshot verification, including the Job reserves
_MARKER = bytes((245, 218, 91, 255))


def _dos_path(handle):
    query = _windows_api('GetFinalPathNameByHandleW', W.DWORD, W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD)
    buffer = C.create_unicode_buffer(32768)
    size = query(handle, buffer, len(buffer), 0)  # normalized FILE_NAME_NORMALIZED / VOLUME_NAME_DOS
    path = buffer.value
    if not 4 < size < len(buffer) or not path.startswith('\\\\?\\') or not path.isascii() or ';' in path:
        raise ValueError('Windows SVG requires normalized ASCII DOS paths without semicolons')
    return _local_path(path[4:])


def _environment(cwd, bin_dir):
    getter = _windows_api('GetSystemWindowsDirectoryW', W.UINT, W.LPWSTR, W.UINT)
    buffer = C.create_unicode_buffer(512)
    if not 0 < getter(buffer, len(buffer)) < len(buffer):
        raise OSError('Windows SVG cannot locate the system Windows directory')
    with _pin_dir(_local_path(buffer.value)) as (handle, _):
        system = _dos_path(handle)
    private = {'TEMP': 'temp', 'TMP': 'temp', 'HOME': 'home', 'USERPROFILE': 'home',
               'APPDATA': 'config', 'LOCALAPPDATA': 'data', 'XDG_CONFIG_HOME': 'config',
               'XDG_DATA_HOME': 'data', 'XDG_CACHE_HOME': 'cache', 'INKSCAPE_PROFILE_DIR': 'profile',
               'FONTCONFIG_PATH': '.', 'FONTCONFIG_FILE': 'fonts.conf'}
    for name in set(private.values()) - {'fonts.conf', '.'} | {'fonts', 'font-cache'}:
        (cwd / name).mkdir()
    (cwd / 'fonts.conf').write_text(
        '<fontconfig><dir>' + escape((cwd / 'fonts').as_posix()) + '</dir><cachedir>'
        + escape((cwd / 'font-cache').as_posix()) + '</cachedir></fontconfig>', encoding='utf-8')
    (cwd / 'profile/preferences.xml').write_text(
        '<inkscape version="1"><group id="options"><group id="font" use_fontsdir_system="0" '
        'use_fontsdir_user="0" custom_fontdirs=""/></group></inkscape>', encoding='utf-8')
    env = {key: str(cwd / name) for key, name in private.items()}
    env.update(SystemRoot=str(system), WINDIR=str(system), SYSTEMDRIVE=system.drive,
               PATH=os.pathsep.join((str(bin_dir), str(system / 'System32'))),
               LANG='C', LC_ALL='C', LANGUAGE='en', TZ='UTC', NO_AT_BRIDGE='1', OMP_NUM_THREADS='1',
               PANGOCAIRO_BACKEND='fc', GIO_USE_VFS='local', GIO_USE_PROXY_RESOLVER='dummy')
    return C.create_unicode_buffer('\0'.join(f'{k}={env[k]}' for k in sorted(env, key=str.casefold)) + '\0')


def _read_pinned(handle, info, directory, limit):
    # The non-reparse handle is opened sharing only reads, excluding writers and rename.
    if directory or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= limit:
        raise ValueError('Windows SVG expected a bounded, single-link regular file')
    buffer, count = C.create_string_buffer(info.st_size + 1), W.DWORD()
    native._check(native._api().ReadFile(handle, buffer, len(buffer), C.byref(count), None), 'ReadFile')
    if count.value != info.st_size:
        raise ValueError('Windows SVG file size changed or was short-read')
    return buffer.raw[:count.value]


def _locked_font_digests():
    def read(relative):
        with _pin_entry(_DEMO / relative) as (handle, info, _, directory):
            return _read_pinned(handle, info, directory, 16 * 1024)

    baseline = json.loads(read('benchmark/portable-v2.json'))
    lock = read('fonts.lock.json')
    if hashlib.sha256(lock).hexdigest() != baseline['runtime_files_sha256']['fonts.lock.json']:
        raise ValueError('Windows SVG demo font lock byte identity mismatch')
    fonts = {row['path']: row['sha256'] for row in json.loads(lock)['fonts']}
    return tuple(fonts['assets/fonts/' + name] for name in _FONT_NAMES)


def _stage_locked_fonts(source, destination, pins):
    digests = _locked_font_digests()
    source = _local_path(source)
    pins.enter_context(_pin_dir(destination))
    with ExitStack() as chain:
        _pin_dirs(chain, source)
        for i, (name, expected) in enumerate(zip(_FONT_NAMES, digests)):
            with _pin_entry(source / name) as (handle, info, _, directory):
                data = _read_pinned(handle, info, directory, _FONT_LIMIT)
            _stage_font(data, expected, name, destination / f'font{i}{Path(name).suffix}', pins)


def _stage_font(data, expected, name, target, pins):
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f'Windows SVG locked demo font byte identity mismatch: {name}')
    with target.open('xb') as stream:
        stream.write(data)
    handle, info, _, directory = pins.enter_context(_pin_entry(target))
    if hashlib.sha256(_read_pinned(handle, info, directory, len(data))).hexdigest() != expected:
        raise ValueError('Windows SVG staged font byte identity mismatch')


def _settle(operation, job, deadline):
    try:
        job._close(deadline)
    except OSError as error:
        raise native.JobSettlementError('Windows SVG Job settlement unconfirmed; operation retained') from error
    except KeyboardInterrupt as cancel:
        if native.job_cleanup_unconfirmed(cancel):
            raise
        raise cancel from native.JobSettlementError('Windows SVG Job settlement interrupted; operation retained')
    operation.keep = False


def _run(operation, executable, cwd, environment, stdin, *, deadline=None):
    argv = [str(executable), '--shell']
    if deadline is not None and time.monotonic() >= deadline - _SETTLE_RESERVE:
        raise TimeoutError('Windows SVG sequence did not stage in time')
    job = native._OwnedJob(argv, capture_limit=_LIMIT if deadline is None else _SEQUENCE_CAPTURE_LIMIT)
    if deadline is None:
        deadline = time.monotonic() + _TIMEOUT
    operation.keep = True  # revoke cleanup authority before CreateProcessW can run a writer
    try:
        job._launch(argv, subprocess.PIPE, subprocess.PIPE, stdin=stdin,
                    cwd=str(cwd), environment=environment)
        if not job._pump(deadline - _SETTLE_RESERVE):
            raise TimeoutError('Windows SVG shell did not exit and close both outputs in time')
    except BaseException as primary:
        try:
            _settle(operation, job, deadline)
        except native.JobSettlementError as cleanup:
            raise primary from cleanup
        except KeyboardInterrupt as cleanup:
            if isinstance(primary, KeyboardInterrupt):
                raise primary from cleanup.__cause__
            raise
        raise
    _settle(operation, job, deadline)
    if job.proc.returncode != 0:
        raise ValueError(f'Windows SVG shell failed ({job.proc.returncode})')
    _, stderr = job._output()
    if _FONT_ERROR.search(stderr.decode('utf-8', 'replace')):
        raise ValueError('Windows SVG export failed: severe Fontconfig or Pango diagnostic')


def _accept_frame(portable_root, *, scratch):
    """Return validated PNG bytes for the font-free geometry fixture."""
    return _accept_fixed(portable_root, scratch, font_dir=None)


def _accept_text_frame(portable_root, *, scratch, font_dir):
    """Accept the fixed text fixture with the four demo-lock files in font_dir.

    No caller SVG, font declarations, digests or output path. The source
    directory is read only and is not part of the shell's search paths.
    """
    return _accept_fixed(portable_root, scratch, font_dir=Path(font_dir))


@contextmanager
def _fixed_shell(portable_root, scratch):
    with _snapshot_operation(portable_root, scratch=scratch) as operation, ExitStack() as pins:
        inputs = operation.inputs
        pins.enter_context(_pin_dir(inputs.vendor))
        pins.enter_context(_pin_dir(inputs.executable.parent))
        entry, _, _, _ = pins.enter_context(_pin_entry(inputs.executable))
        cwd_handle, _ = pins.enter_context(_pin_dir(inputs.cwd))
        executable, cwd = _dos_path(entry), _dos_path(cwd_handle)
        yield operation, executable, cwd, _environment(cwd, executable.parent), pins


@contextmanager
def _fixed_stdin(cwd, actions):
    plan = cwd / 'actions.txt'
    plan.write_bytes(actions)
    with _pin_entry(plan) as (handle, info, _, directory):
        if _read_pinned(handle, info, directory, len(actions)) != actions:
            raise ValueError('Windows SVG fixed actions changed')
        rewind = _windows_api('SetFilePointerEx', W.BOOL, W.HANDLE, C.c_longlong, C.c_void_p, W.DWORD)
        native._check(rewind(handle, 0, None, 0), 'SetFilePointerEx (stdin)')
        yield handle


def _read_png(operation, path):
    try:
        with _pin_entry(path) as (handle, info, _, directory):
            return _read_pinned(handle, info, directory, _LIMIT)
    except OSError as error:
        if getattr(error, 'winerror', None) in (32, 33):  # sharing or lock violation
            operation.keep = True
        raise


def _png_pixels(data, width, height, open_image, *, static_only=False):
    if _png_stream_invalid_reason(io.BytesIO(data), len(data), width=width, height=height):
        raise ValueError(f'Windows SVG shell did not export a valid {width}x{height} PNG')
    if static_only:
        # The gate above checked every chunk boundary, CRC, and exact EOF at IEND.
        offset = 8
        while offset < len(data):
            if data[offset + 4:offset + 8] in (b'acTL', b'fcTL', b'fdAT'):
                raise ValueError('Windows SVG canonical frame requires a static PNG')
            offset += 12 + int.from_bytes(data[offset:offset + 4], 'big')
    with open_image(io.BytesIO(data)) as image:
        return image.convert('RGBA').tobytes()


def _accept_fixed(portable_root, scratch, *, font_dir):
    from PIL import Image

    text = font_dir is not None
    width, height = _TEXT_SIZE if text else (_WIDTH, _HEIGHT)
    actions = (_EXPORT.format(stem='f0000', width=width, height=height, png='f0000.png') + 'quit\n').encode('ascii')
    with _fixed_shell(portable_root, scratch) as (operation, executable, cwd, environment, pins):
        if text:
            _stage_locked_fonts(font_dir, cwd / 'fonts', pins)
        (cwd / 'f0000.svg').write_bytes(_TEXT_SVG if text else _SVG)
        with _fixed_stdin(cwd, actions) as stdin:
            _run(operation, executable, cwd, environment, stdin)
        data = _read_png(operation, cwd / 'f0000.png')
        rgba = _png_pixels(data, width, height, Image.open)
        if text:
            if hashlib.sha256(rgba).hexdigest() != _TEXT_RGBA_SHA256:
                raise ValueError('Windows SVG locked text fixture pixels differ')
        elif rgba != _RGBA * (_WIDTH * _HEIGHT):
            raise ValueError('Windows SVG fixture pixels differ')
        return data


def _accept_standard_frame(portable_root, *, scratch, project_root):
    """Accept only portable-v2 f0135 from a caller-authorized stable NTFS root.

    Use only the fixed SVG and font leaves, not declarations from the project.
    This returns one verified PNG, not evidence about the rest of the project.
    """
    from PIL import Image

    project_root = os.fspath(project_root)  # preserve the original root spelling for anchored validation
    filename = _STANDARD_STEM + '.svg'
    svg = win_input.read(project_root, 'gfx/frames-svg/' + filename,
                         limit=_STANDARD_SVG_BYTES, expected_path=None)
    if len(svg) != _STANDARD_SVG_BYTES or hashlib.sha256(svg).hexdigest() != _STANDARD_SVG_SHA256:
        raise ValueError('Windows SVG canonical frame byte identity mismatch')
    width, height = _STANDARD_SIZE
    png = _STANDARD_STEM + '.png'
    actions = (_EXPORT.format(stem=_STANDARD_STEM, width=width, height=height, png=png) + 'quit\n').encode('ascii')
    digests = _locked_font_digests()
    with _fixed_shell(portable_root, scratch) as (operation, executable, cwd, environment, pins):
        fonts = cwd / 'fonts'
        pins.enter_context(_pin_dir(fonts))
        for i, (name, expected) in enumerate(zip(_FONT_NAMES, digests)):
            data = win_input.read(project_root, 'assets/fonts/' + name, limit=_FONT_LIMIT, expected_path=None)
            _stage_font(data, expected, name, fonts / f'font{i}{Path(name).suffix}', pins)
        source = cwd / filename
        with source.open('xb') as stream:
            stream.write(svg)
        handle, info, _, directory = pins.enter_context(_pin_entry(source))
        if _read_pinned(handle, info, directory, _STANDARD_SVG_BYTES) != svg:
            raise ValueError('Windows SVG staged canonical frame changed')
        with _fixed_stdin(cwd, actions) as stdin:
            _run(operation, executable, cwd, environment, stdin)
        data = _read_png(operation, cwd / png)
        rgba = _png_pixels(data, width, height, Image.open, static_only=True)
        if hashlib.sha256(rgba).hexdigest() != _STANDARD_RGBA_SHA256:
            raise ValueError('Windows SVG canonical frame pixels differ')
        return data


def _sequence_marker(index):
    phase, column = divmod(index, 30)  # six rows, thirty positions per row
    return bytes((32 + index, 64 + phase * 16, 155, 255)), column * 2, 2 + phase * 7


def _sequence_svg(index):
    color, x, y = _sequence_marker(index)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="{_HEIGHT}">'
            f'<rect width="{_WIDTH}" height="{_HEIGHT}" fill="#{color[:3].hex()}"/>'
            f'<rect x="{x}" y="{y}" width="5" height="6" fill="#{_MARKER[:3].hex()}"/></svg>').encode('ascii')


def _sequence_pixels(index):
    color, x, y = _sequence_marker(index)
    base = color * _WIDTH
    marked = color * x + _MARKER * 5 + color * (_WIDTH - x - 5)
    return base * y + marked * 6 + base * (_HEIGHT - y - 6)


def _accept_sequence(portable_root, *, scratch):
    """Run one owned shell for 180 fixed 64x48 frames; return bytes in index order.

    The tuple is returned only on exact names and per-index full pixel identity.
    Nothing is published; this does not accept caller-provided render inputs.
    """
    from PIL import Image

    names = tuple(f'f{i:04d}' for i in range(_SEQUENCE_FRAMES))
    outputs = tuple(name + '.png' for name in names)
    # Exactly 180 fixed-length action lines and one quit; read back that length only.
    actions = (''.join(_EXPORT.format(stem=name, width=_WIDTH, height=_HEIGHT, png='frames/' + png)
                       for name, png in zip(names, outputs)) + 'quit\n').encode('ascii')
    with _fixed_shell(portable_root, scratch) as (operation, executable, cwd, environment, pins):
        deadline = time.monotonic() + _SEQUENCE_TIMEOUT
        frames = cwd / 'frames'
        frames.mkdir()
        pins.enter_context(_pin_dir(frames))
        for i, name in enumerate(names):
            (cwd / (name + '.svg')).write_bytes(_sequence_svg(i))
        with _fixed_stdin(cwd, actions) as stdin:
            _run(operation, executable, cwd, environment, stdin, deadline=deadline - _VERIFY_RESERVE)

        missing = set(outputs)
        with os.scandir(frames) as entries:
            for entry in entries:
                if entry.name not in missing:
                    raise ValueError('Windows SVG sequence has an unexpected output entry')
                missing.remove(entry.name)
        if missing:
            raise ValueError('Windows SVG sequence is missing output frames')

        accepted = []
        for i, name in enumerate(outputs):
            if time.monotonic() >= deadline:
                raise TimeoutError('Windows SVG sequence verification deadline expired')
            data = _read_png(operation, frames / name)
            if _png_pixels(data, _WIDTH, _HEIGHT, Image.open) != _sequence_pixels(i):
                raise ValueError(f'Windows SVG sequence frame {i} pixels differ')
            accepted.append(data)
        if time.monotonic() >= deadline:
            raise TimeoutError('Windows SVG sequence verification deadline expired')
        return tuple(accepted)
