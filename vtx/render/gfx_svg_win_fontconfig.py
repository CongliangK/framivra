"""Internal single-file face check; not called by the SVG runtime.

The caller supplies a trusted font digest and an exclusive scratch parent.
The fresh worker trusts Windows, base CPython, this checkout and the local
namespace. Its private Job supervises the process tree, not the loader. The
worker pins the Inkscape import set and checks its loaded file paths; this does
not attest later dynamic loads or already mapped memory. It is not an
untrusted-font sandbox or evidence of selection, shaping or rendering.
"""
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
from types import SimpleNamespace

from ..runtime import windows_browser as native
from ..runtime.windows_cleanup import pinned


# Normal and delay PE imports, recursively, taking only non-OS imports from
# the SHA-256-verified official Inkscape 1.4.4 dcaf3e7 x64 7z release:
# https://inkscape.org/release/inkscape-1.4.4/windows/64-bit/compressed-7z/dl/
# Archive SHA-256 c4dbd64a92628abe7d7316c43f9325396e4d48417866027ee3d993d4f5b54c6a.
# Updating requires reviewing imports and re-pinning from a verified release,
# never deriving trust at runtime. Windows system DLLs/API sets are OS-trusted.
_FONTCONFIG = 'libfontconfig-1.dll'
_DLLS = {
    _FONTCONFIG: (345888, '9fffc784d10b9918214d4a576b690418d8d4b9694d41d642b09ea19dd1daea00'),
    'libbrotlicommon.dll': (144565, 'c62c08223e450dfc2fff33c752cc228505ffd9d755201958ba800d5d30481a2c'),
    'libbrotlidec.dll': (62619, 'e270b6e576594ee8ee6e33ccc54e34a54304616dd6225652e11e49d6f17ef896'),
    'libbz2-1.dll': (78774, '669a4bf9266d5f020e843aa5fd75b93ce8ebde27014d5ac466a8b3cf39d8aba7'),
    'libexpat-1.dll': (199413, 'f573204372a49d147ab0e55e3dd958564620734cd3115b1cef061a82e043a394'),
    'libfreetype-6.dll': (793129, 'c3d1193a07b46107b5f74034574d767b98f0ab18423b11f44e6c1ad452a19466'),
    'libgcc_s_seh-1.dll': (150852, 'a955ab07e75e8773d20821fb94774267f2f5a8773641646c809024b2e298ef31'),
    'libglib-2.0-0.dll': (1554206, 'b910c405a546aeb24031cd122c52e00c25f5510af0ce32932ced9065fb7c16a4'),
    'libgraphite2.dll': (155805, 'd19e2587db996cc44799bc22bd9f7a1fda91427aa4cb63bb581da485acff66e0'),
    'libharfbuzz-0.dll': (1544556, 'ea56edd9516bc802a7f7bbe3c181179e4eac79ab76bbfa684da62e03415fc126'),
    'libiconv-2.dll': (1135869, 'b37d1b4acab5310c4e4f6e2a961d146454ff740d943724a68fa3cebc85deb7cd'),
    'libintl-8.dll': (299024, '8eb731da34fe70fbf36239ca5292f92d5f44004ed4d9d10827421454de8c27a9'),
    'libpcre2-8-0.dll': (718625, '52ccc7e4ec5ad0aecf307c0328826340f5d504aa59f7c4143b9a1b9fff16ccda'),
    'libpng16-16.dll': (257436, '6d5637104c46831b0dda78deb396dd54cda34edc63af62cc3511caf56339a830'),
    'libstdc++-6.dll': (2464009, '81836382ea9c84175fbec2a67dcace716bf3db30706a604c7a7f55ee58cb5b4a'),
    'libwinpthread-1.dll': (63765, '6372a2613b3ac725e24e8a08ac7b081886d53c01728e732f56cefc6551c8409f'),
    'zlib1.dll': (100279, '1dc85208162ee57fe97e892bb5160fe9588903992995f90adc3a3a358dac6fc6'),
}
_SYSTEM32, _DLL_LOAD_DIR = 0x800, 0x100
_MAX_FONT = 32 * 1024 * 1024
_MAX_FACES, _MAX_TEXT, _MAX_RESULT = 64, 256, 8192
_TIMEOUT, _SETTLE_RESERVE = 15, 2
_REPLY = 'result.json'


class _Rejected(ValueError):
    """Only fixed, path-free reasons cross the process boundary."""


def _bind(library, name, result, *args):
    function = getattr(library, name)
    function.restype, function.argtypes = result, args
    return function


def _kernel():
    return C.WinDLL('kernel32.dll', winmode=_SYSTEM32, use_last_error=True)


def _local_path(value):
    path = Path(value)
    is_reserved = getattr(os.path, 'isreserved', lambda part: Path(part).is_reserved())
    if (not path.is_absolute() or not re.fullmatch(r'[A-Za-z]:', path.drive)
            or len(path.parts) > 32 or len(str(path).encode('utf-16-le', 'surrogatepass')) > 480
            or any(part.endswith((' ', '.')) or is_reserved(part)
                   or re.search(r'[<>:"|?*\x00-\x1f\ud800-\udfff]', part) for part in path.parts[1:])
            or _bind(_kernel(), 'GetDriveTypeW', W.UINT, W.LPCWSTR)(path.anchor) != 3):
        raise _Rejected('face check requires ordinary absolute paths on local fixed drives')
    return path


def _pin_dirs(stack, path):
    for directory in (*reversed(path.parents), path):
        stack.enter_context(pinned(directory, directory=True))


def _pin_file(stack, path, limit):
    _, info, _ = stack.enter_context(pinned(path))
    if not stat.S_ISREG(info.st_mode) or info.st_file_attributes & 0x400:
        raise _Rejected('face check input is not a regular non-reparse file')
    if not 0 < info.st_size <= limit:
        raise _Rejected('face check input exceeds its file size bounds')
    return info.st_size


def _verified(path, size, expected, label):
    # Read at most size+1 even if metadata is incorrect. The caller pins the
    # file and ancestors, including across native reads of the private copy.
    with path.open('rb') as stream:
        data = stream.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
        raise _Rejected(f'{label} byte identity mismatch')
    return data


def _query(library, name, index, sample):
    ptr, integer, uint, string = C.c_void_p, C.c_int, C.c_uint, C.c_char_p
    query = _bind(library, 'FcFreeTypeQuery', ptr, string, uint, ptr, C.POINTER(integer))
    destroy = _bind(library, 'FcPatternDestroy', None, ptr)
    get_string = _bind(library, 'FcPatternGetString', integer, ptr, string, integer, C.POINTER(ptr))
    get_index = _bind(library, 'FcPatternGetInteger', integer, ptr, string, integer, C.POINTER(integer))
    get_weight = _bind(library, 'FcPatternGetDouble', integer, ptr, string, integer, C.POINTER(C.c_double))
    get_charset = _bind(library, 'FcPatternGetCharSet', integer, ptr, string, integer, C.POINTER(ptr))
    has_char = _bind(library, 'FcCharSetHasChar', integer, ptr, uint)
    count = integer()
    # Only this exact base face (not a named variation or enumeration). The
    # fixed relative name is in the private CWD and has no encoding ambiguity.
    pattern = query(name.encode('ascii'), index, None, C.byref(count))
    if not pattern:
        raise _Rejected('Fontconfig could not query the requested face')
    try:
        def field(get, key, out):
            if get(pattern, key, 0, C.byref(out)) != 0:  # FcResultMatch
                raise _Rejected('Fontconfig face metadata is incomplete')
            return out.value

        def text(key):
            address = field(get_string, key, ptr())
            chars = C.cast(address, C.POINTER(C.c_ubyte))
            data = bytearray()
            for offset in range(_MAX_TEXT + 1):
                value = chars[offset] if chars else 0
                if not value:
                    if data:
                        return data.decode('utf-8')
                    break
                if offset == _MAX_TEXT:  # at the bound only accept a NUL terminator
                    break
                data.append(value)
            raise _Rejected('Fontconfig face name is missing or exceeds its bound')

        actual = field(get_index, b'index', integer())
        weight = field(get_weight, b'weight', C.c_double())
        charset = field(get_charset, b'charset', ptr())
        if not index < count.value <= _MAX_FACES or actual != index or not math.isfinite(weight) or not charset:
            raise _Rejected('Fontconfig returned an invalid face')
        return {'index': index, 'file_face_count': count.value, 'fontconfig_weight': weight,
                'family': text(b'family'), 'style': text(b'style'),
                'missing_codepoints': [code for code in sorted(set(map(ord, sample))) if not has_char(charset, code)]}
    finally:
        destroy(pattern)


def _check_modules(kernel, directory=None):
    get_module = _bind(kernel, 'GetModuleHandleW', W.HMODULE, W.LPCWSTR)
    get_path = _bind(kernel, 'GetModuleFileNameW', W.DWORD, W.HMODULE, W.LPWSTR, W.DWORD)
    for name in _DLLS:
        module = get_module(name)
        if directory is None:
            if module:
                raise _Rejected('Fontconfig DLL basename was already loaded')
        else:
            buffer = C.create_unicode_buffer(512)
            length = get_path(module, buffer, len(buffer)) if module else 0
            loaded = Path(buffer.value)
            if (not 0 < length < len(buffer) or loaded.parent.parts != directory.parts
                    or loaded.name.lower() != name or not (directory / name).samefile(loaded)):
                raise _Rejected('Fontconfig DLL was not loaded from the verified snapshot')


def _inspect(bundle, font, expected, index, sample):
    # Worker-only. No Fontconfig config, registration, font directory search,
    # caches, or Inkscape execution. The OS and CPython installation are trusted.
    kernel = _kernel()
    _bind(kernel, 'SetErrorMode', W.UINT, W.UINT)(0x0001 | 0x0002)  # no worker error dialogs
    if not _bind(kernel, 'SetDefaultDllDirectories', W.BOOL, W.DWORD)(_SYSTEM32):
        raise _Rejected('restricted Windows DLL search is unavailable')
    directory = Path.cwd()
    bin_dir, font = _local_path(bundle) / 'inkscape' / 'bin', _local_path(font)
    with ExitStack() as stack:
        _pin_dirs(stack, bin_dir)
        _pin_dirs(stack, font.parent)
        stack.enter_context(pinned(directory, directory=True))  # parent pins the ancestors
        inputs = [(bin_dir / name, name, size, sha, 'bundled DLL') for name, (size, sha) in _DLLS.items()]
        font_name = 'font' + font.suffix.lower()
        font_size = _pin_file(stack, font, _MAX_FONT)
        inputs.append((font, font_name, font_size, expected, 'font'))
        for source, name, size, sha, label in inputs:
            if label == 'bundled DLL':
                _pin_file(stack, source, size)
            with (directory / name).open('xb') as stream:
                stream.write(_verified(source, size, sha, label))
            _pin_file(stack, directory / name, size)
            _verified(directory / name, size, sha, label)

        # Do not search the original bin directory or allow other files in the
        # private load directory to stand in for OS imports.
        expected_names = {*_DLLS, font_name}
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name not in expected_names:
                    raise _Rejected('unexpected entry in Fontconfig load directory')
                expected_names.remove(entry.name)
        if expected_names:
            raise _Rejected('incomplete Fontconfig load directory')
        _check_modules(kernel)
        # ctypes.CDLL uses cdecl for the exported Fontconfig functions. winmode
        # is passed to LoadLibraryExW: only the verified snapshot and System32.
        library = C.CDLL(str(directory / _FONTCONFIG), winmode=_DLL_LOAD_DIR | _SYSTEM32)
        _check_modules(kernel, directory)
        version = _bind(library, 'FcGetVersion', C.c_int)()
        if version != 21701:
            raise _Rejected('unexpected pinned Fontconfig version')
        face = _query(library, font_name, index, sample)
        for source, name, size, sha, label in inputs:
            _verified(directory / name, size, sha, label)
            _verified(source, size, sha, label)
        return {'fontconfig_version': version, 'fontconfig_sha256': _DLLS[_FONTCONFIG][1],
                'font_sha256': expected, **face}


def _run_worker(argv, directory, operation):
    system = C.create_unicode_buffer(512)
    n = _bind(_kernel(), 'GetSystemWindowsDirectoryW', W.UINT, W.LPWSTR, W.UINT)(system, len(system))
    if not 0 < n < len(system):
        raise _Rejected('Windows system directory is unavailable')
    # Launch trusted base CPython, not the venv redirector. Keep isolated
    # imports, no site/PYTHONPATH and no bytecode writes in the checkout.
    bootstrap = ('import sys; sys.path.append(sys.argv.pop(1)); '
                 'from vtx.render.gfx_svg_win_fontconfig import _worker; _worker()')
    python = Path(sys._base_executable or '')
    if not python.is_absolute() or python.suffix.lower() != '.exe' or '\0' in str(python):
        raise _Rejected('absolute base Python executable is unavailable')
    args = [str(python), '-IBS', '-c', bootstrap, str(Path(__file__).resolve().parents[2]), *argv]
    # Sorted allowlist; the buffer adds the second NUL for CreateProcessW.
    environment = C.create_unicode_buffer(f'SystemRoot={system.value}\0TEMP={directory}\0TMP={directory}\0')
    deadline = time.monotonic() + _TIMEOUT + _SETTLE_RESERVE
    operation.keep = True  # revoke scratch cleanup authority before launch can run a writer
    job, launched = None, False

    def settle():
        try:
            job._close(deadline)  # exact root handle and Job accounting, not just root exit
        except OSError as error:
            raise _Rejected('Fontconfig face worker Job settlement unconfirmed; scratch retained') from error
        if launched:
            operation.keep = False

    try:
        try:
            job = native._OwnedJob(args)
            job._launch(args, subprocess.DEVNULL, subprocess.DEVNULL, cwd=str(directory), environment=environment)
            launched = True
        except OSError:
            raise _Rejected('Fontconfig face worker launch not confirmed; scratch retained') from None
        if not job._pump(deadline - _SETTLE_RESERVE):
            raise _Rejected('Fontconfig face check timed out')
        if job.proc.returncode != 0:
            raise _Rejected('Fontconfig face worker failed')
    except BaseException as primary:
        if job is not None:
            # Even a clean close cannot prove ownership if launch did not return.
            try:
                settle()
            except BaseException as cleanup:
                raise primary from cleanup
        raise
    settle()


def _worker():
    bundle, font, sha, index, sample = sys.argv[1:]
    try:
        reply = {'face': _inspect(bundle, font, sha, int(index), json.loads(sample))}
    except _Rejected as error:
        reply = {'error': str(error)}
    except (OSError, UnicodeError):
        reply = {'error': 'Fontconfig face check rejected an unreadable or invalid input'}
    with Path(_REPLY).open('xb') as stream:
        stream.write(json.dumps(reply).encode('ascii'))


@contextmanager
def _after(action, message):
    def finish():
        try:
            action()
        except OSError:
            raise _Rejected(message) from None

    try:
        yield
    except BaseException as primary:
        try:
            finish()
        except BaseException as cleanup:
            raise primary from cleanup
        raise
    else:
        finish()


def inspect_face(portable_root, font_path, *, sha256, scratch, index=0, sample=''):
    """Query one explicit TTF/OTF/TTC/OTC face and optional Unicode scalar sample.

    portable_root contains inkscape/bin; scratch is an existing local directory
    controlled exclusively by the caller, where a disposable snapshot is created.
    There is no PATH discovery. Returns first family/style, face index/count,
    native weight, missing sample codepoints and byte identities. No renderer
    or completion integration. ``sha256`` must come from a trusted caller, not
    from rehashing an arbitrary font before running this check.
    """
    if sys.platform != 'win32' or sysconfig.get_platform() != 'win-amd64':
        raise _Rejected('face check requires Windows x64 Python')
    if type(sha256) is not str or not re.fullmatch(r'[0-9a-f]{64}', sha256):
        raise _Rejected('face check requires a trusted font SHA-256')
    if type(index) is not int or not 0 <= index < _MAX_FACES:
        raise _Rejected('font face index is out of bounds')
    if (type(sample) is not str or len(sample) > 128
            or any(0xd800 <= ord(char) <= 0xdfff for char in sample)):
        raise _Rejected('font sample must be at most 128 Unicode scalars')
    bundle, font, scratch = map(_local_path, (portable_root, font_path, scratch))
    if font.suffix.lower() not in {'.ttf', '.otf', '.ttc', '.otc'}:
        raise _Rejected('face check requires a TTF, OTF, TTC or OTC file')
    stack = ExitStack()
    with _after(stack.close, 'Fontconfig face scratch pins could not be released'):
        _pin_dirs(stack, scratch)
        directory = Path(tempfile.mkdtemp(dir=scratch, prefix='vtx-fc-'))
        operation = SimpleNamespace(keep=False)

        def remove():
            if not operation.keep:
                shutil.rmtree(directory)

        with _after(remove, 'Fontconfig face scratch removal failed'):
            _run_worker([str(bundle), str(font), sha256, str(index), json.dumps(sample)], directory, operation)
            try:
                with (directory / _REPLY).open('rb') as stream:
                    data = stream.read(_MAX_RESULT + 1)
            except OSError:
                raise _Rejected('Fontconfig face reply could not be read') from None
            if len(data) > _MAX_RESULT:
                raise _Rejected('Fontconfig face reply exceeds its bound')
            reply = json.loads(data)
            if 'error' in reply:
                raise _Rejected(reply['error'])
            return reply['face']
