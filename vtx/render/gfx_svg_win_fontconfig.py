"""Internal face and fixed-config catalog checks for trusted acceptance; not a renderer.

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
from xml.sax.saxutils import escape

from ..runtime import windows_browser as native
from ..runtime.windows_cleanup import pinned
from .gfx_svg_win_input import _StandardInfo
from .gfx_svg_win_snapshot import _AttributeTagInfo, _handle_info, _pin_dir, _pin_entry


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


_CATALOG_LEAVES = ('font0.ttc', 'font1.ttc', 'font2.ttf', 'font3.ttf')
_CATALOG_COUNTS = (10, 10, 1, 1)
_CATALOG_FAMILIES = tuple('Noto Sans ' + mono + 'CJK ' + region
                          for mono in ('', 'Mono ') for region in ('JP', 'KR', 'SC', 'TC', 'HK'))
_CATALOG_ROWS = frozenset(
    (slot, index, family, (b'Regular', b'Bold')[slot], (80., 200.)[slot])
    for slot in (0, 1) for index, family in enumerate(f.encode('ascii') for f in _CATALOG_FAMILIES)
) | {(2, 0, b'DejaVu Sans', b'Book', 80.), (3, 0, b'DejaVu Sans', b'Bold', 200.)}


def _expected_catalog_config(cwd):
    # Independent allowlist, not an XML validator or the shell's config writer.
    return ('<fontconfig><dir>' + escape((cwd / 'fonts').as_posix()) + '</dir><cachedir>'
            + escape((cwd / 'font-cache').as_posix()) + '</cachedir></fontconfig>').encode('ascii')


def _check_catalog_config(path):
    expected = _expected_catalog_config(path.parent)
    with path.open('rb') as stream:
        if stream.read(len(expected) + 1) != expected:
            raise _Rejected('Fontconfig fixed configuration byte mismatch')


def _catalog_cache_name(cwd):
    # The pinned build's unsalted directory-key convention, not a trust digest.
    key = hashlib.md5((cwd / 'fonts').as_posix().encode('ascii'), usedforsecurity=False).hexdigest()
    return key + '-le64.cache-9'


def _require_empty_catalog_cache(cwd):
    with os.scandir(cwd / 'font-cache') as entries:
        if next(entries, None) is not None:
            raise _Rejected('Fontconfig catalog cache is not empty')


def _empty_catalog_cache(cwd):
    # The caller holds the cache directory pinned and proved the worker settled.
    # No recursive cleanup and no read or reuse of cache contents.
    cache = cwd / 'font-cache'
    name = _catalog_cache_name(cwd)
    with os.scandir(cache) as entries:
        entry = next(entries, None)
        if entry is None:
            return
        if entry.name not in (name, name + '.NEW') or next(entries, None) is not None:
            raise _Rejected('Fontconfig probe left unexpected cache entries')
    with pinned(cache / entry.name, delete=True) as (handle, _, kernel):
        tag = _handle_info(handle, kernel, 9, _AttributeTagInfo())
        info = _handle_info(handle, kernel, 1, _StandardInfo())
        forbidden = stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DIRECTORY | stat.FILE_ATTRIBUTE_DEVICE
        if (tag.tag or tag.attributes & forbidden or info.directory or info.delete_pending
                or info.links != 1 or not 0 <= info.size <= 256 * 1024):
            raise _Rejected('Fontconfig probe left an invalid cache file')
        discard = W.BOOL(True)  # FileDispositionInfo: delete this held file on close
        if not kernel.SetFileInformationByHandle(handle, 4, C.byref(discard), C.sizeof(discard)):
            raise _Rejected('Fontconfig probe cache file could not be removed')
    with os.scandir(cache) as entries:
        if next(entries, None) is not None:
            raise _Rejected('Fontconfig probe cache is not empty after cleanup')


def _catalog_string(address):
    chars = C.cast(address, C.POINTER(C.c_ubyte))
    for n in range(_MAX_TEXT + 1):
        if not chars or not chars[n]:
            if n:
                return C.string_at(address, n)
            break
    raise _Rejected('Fontconfig catalog string is missing or exceeds its bound')


def _catalog(library, cwd):
    ptr, integer, string = C.c_void_p, C.c_int, C.c_char_p

    class FontSet(C.Structure):  # public FcFontSet ABI, borrowed from config
        _fields_ = [('nfont', integer), ('sfont', integer), ('fonts', C.POINTER(ptr))]

    def bind(name, result, *args):
        return _bind(library, name, result, *args)

    def encoded(path):
        return path.as_posix().encode('ascii')

    destroy = bind('FcConfigDestroy', None, ptr)
    config = bind('FcConfigCreate', ptr)()
    if not config:
        raise _Rejected('Fontconfig could not allocate a catalog configuration')
    fonts, cfg, cache = cwd / 'fonts', cwd / 'fonts.conf', cwd / 'font-cache'
    try:
        if not bind('FcConfigParseAndLoad', integer, ptr, string, integer)(config, encoded(cfg), 1):
            raise _Rejected('Fontconfig could not parse the fixed configuration')
        get_next = bind('FcStrListNext', ptr, ptr)
        done = bind('FcStrListDone', None, ptr)
        for suffix, expected in (('ConfigFiles', cfg), ('FontDirs', fonts), ('CacheDirs', cache)):
            paths = bind('FcConfigGet' + suffix, ptr, ptr)(config)
            if not paths:
                raise _Rejected('Fontconfig catalog configuration list unavailable')
            try:
                if _catalog_string(get_next(paths)) != encoded(expected) or get_next(paths):
                    raise _Rejected('Fontconfig catalog configuration roots differ')
            finally:
                done(paths)
        # FcSetSystem (0) names the configured set; no OS/global config init,
        # no AppFont registrations and only the above verified private roots.
        if not bind('FcConfigBuildFonts', integer, ptr)(config):
            raise _Rejected('Fontconfig could not build the fixed catalog')
        result = bind('FcConfigGetFonts', C.POINTER(FontSet), ptr, integer)(config, 0)
        if not result:
            raise _Rejected('Fontconfig catalog is unavailable')
        fs = result.contents
        if fs.nfont != len(_CATALOG_ROWS) or fs.sfont < fs.nfont or not fs.fonts:
            raise _Rejected('Fontconfig catalog face count differs')
        slots = {encoded(fonts / leaf): i for i, leaf in enumerate(_CATALOG_LEAVES)}
        get_s = bind('FcPatternGetString', integer, ptr, string, integer, C.POINTER(ptr))
        get_i = bind('FcPatternGetInteger', integer, ptr, string, integer, C.POINTER(integer))
        get_w = bind('FcPatternGetDouble', integer, ptr, string, integer, C.POINTER(C.c_double))
        remaining = set(_CATALOG_ROWS)
        for n in range(fs.nfont):
            pattern = fs.fonts[n]

            def field(get, key, out):
                if not pattern or get(pattern, key, 0, C.byref(out)) != 0:
                    raise _Rejected('Fontconfig catalog face metadata is incomplete')
                return out.value

            # This build emits mixed slash spellings. Only convert separators;
            # never open a returned path or collapse dots, case or aliases.
            path = _catalog_string(field(get_s, b'file', ptr())).replace(b'\\', b'/')
            if path not in slots:
                raise _Rejected('Fontconfig catalog path is outside the staged set')
            row = (slots[path], field(get_i, b'index', integer()),
                   _catalog_string(field(get_s, b'family', ptr())),
                   _catalog_string(field(get_s, b'style', ptr())), field(get_w, b'weight', C.c_double()))
            if row not in remaining:
                raise _Rejected('Fontconfig catalog face inventory differs')
            remaining.remove(row)
        return _CATALOG_COUNTS
    finally:
        destroy(config)


def _catalog_inputs(stack, cwd, digests):
    stack.enter_context(_pin_dir(cwd))
    fonts, cache, cfg = cwd / 'fonts', cwd / 'font-cache', cwd / 'fonts.conf'
    for path in (fonts, cache):
        stack.enter_context(_pin_dir(path))
    _, info, _, is_dir = stack.enter_context(_pin_entry(cfg))
    if is_dir or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise _Rejected('Fontconfig fixed configuration is not a single-link file')
    _check_catalog_config(cfg)
    with os.scandir(cache) as entries:
        if next(entries, None) is not None:
            raise _Rejected('Fontconfig catalog cache is not initially empty')
    required = set(_CATALOG_LEAVES)
    with os.scandir(fonts) as entries:
        for entry in entries:
            if entry.name not in required:
                raise _Rejected('Fontconfig staged catalog has an unexpected entry')
            required.remove(entry.name)
    if required:
        raise _Rejected('Fontconfig staged catalog is incomplete')
    verified = []
    for leaf, sha in zip(_CATALOG_LEAVES, digests):
        path = fonts / leaf
        _, info, _, is_dir = stack.enter_context(_pin_entry(path))
        if is_dir or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= _MAX_FONT:
            raise _Rejected('Fontconfig catalog input is not a bounded single-link font')
        _verified(path, info.st_size, sha, 'font')
        verified.append((path, info.st_size, sha))
    return verified


def _inspect(bundle, font, expected, index, sample, *, catalog=None):
    # Worker-only; standalone face mode does not parse config or scan dirs.
    # The OS and CPython installation are trusted.
    kernel = _kernel()
    _bind(kernel, 'SetErrorMode', W.UINT, W.UINT)(0x0001 | 0x0002)  # no worker error dialogs
    if not _bind(kernel, 'SetDefaultDllDirectories', W.BOOL, W.DWORD)(_SYSTEM32):
        raise _Rejected('restricted Windows DLL search is unavailable')
    directory = Path.cwd()
    bin_dir = _local_path(bundle) / 'inkscape' / 'bin'
    with ExitStack() as stack:
        _pin_dirs(stack, bin_dir)
        stack.enter_context(pinned(directory, directory=True))  # parent pins the ancestors
        inputs = [(bin_dir / name, name, size, sha, 'bundled DLL') for name, (size, sha) in _DLLS.items()]
        if catalog is None:
            font = _local_path(font)
            _pin_dirs(stack, font.parent)
            font_name = 'font' + font.suffix.lower()
            font_size = _pin_file(stack, font, _MAX_FONT)
            inputs.append((font, font_name, font_size, expected, 'font'))
        else:
            cwd = _local_path(bundle) / 'shell'
            staged = _catalog_inputs(stack, cwd, catalog)
        for source, name, size, sha, label in inputs:
            if label == 'bundled DLL':
                _pin_file(stack, source, size)
            with (directory / name).open('xb') as stream:
                stream.write(_verified(source, size, sha, label))
            _pin_file(stack, directory / name, size)
            _verified(directory / name, size, sha, label)

        # Do not search the original bin directory or allow other files in the
        # private load directory to stand in for OS imports.
        expected_names = set(_DLLS)
        if catalog is None:
            expected_names.add(font_name)
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
        if catalog is None:
            face = _query(library, font_name, index, sample)
        else:
            counts = _catalog(library, cwd)
            _check_catalog_config(cwd / 'fonts.conf')
            for path, size, sha in staged:
                _verified(path, size, sha, 'font')
        for source, name, size, sha, label in inputs:
            _verified(directory / name, size, sha, label)
            _verified(source, size, sha, label)
        identity = {'fontconfig_version': version, 'fontconfig_sha256': _DLLS[_FONTCONFIG][1]}
        if catalog is not None:
            return {**identity, 'faces_by_file': counts}
        return {**identity, 'font_sha256': expected, **face}


def _run_worker(argv, directory, operation, *, deadline=None, kind='face'):
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
    if deadline is None:
        deadline = time.monotonic() + _TIMEOUT + _SETTLE_RESERVE
    elif time.monotonic() >= deadline - _SETTLE_RESERVE:
        raise TimeoutError(f'Fontconfig {kind} check deadline expired before launch')
    operation.keep = True  # revoke scratch cleanup authority before launch can run a writer
    job, launched = None, False

    def settle():
        try:
            job._close(deadline)  # exact root handle and Job accounting, not just root exit
        except OSError as error:
            raise _Rejected(f'Fontconfig {kind} worker Job settlement unconfirmed; scratch retained') from error
        if launched:
            operation.keep = False

    try:
        try:
            job = native._OwnedJob(args)
            job._launch(args, subprocess.DEVNULL, subprocess.DEVNULL, cwd=str(directory), environment=environment)
            launched = True
        except OSError:
            raise _Rejected(f'Fontconfig {kind} worker launch not confirmed; scratch retained') from None
        if not job._pump(deadline - _SETTLE_RESERVE):
            raise _Rejected(f'Fontconfig {kind} check timed out')
        if job.proc.returncode != 0:
            raise _Rejected(f'Fontconfig {kind} worker failed')
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
    args = sys.argv[1:]
    kind = 'catalog' if args[0] == 'catalog' else 'face'
    try:
        if kind == 'catalog':
            _, bundle, *digests = args
            if _local_path(bundle) != Path.cwd().parent or len(digests) != len(_CATALOG_LEAVES):
                raise _Rejected('Fontconfig catalog requires the fixed private staging layout')
            result = _inspect(bundle, None, None, None, None, catalog=digests)
        else:
            bundle, font, sha, index, sample = args
            result = _inspect(bundle, font, sha, int(index), json.loads(sample))
        reply = {kind: result}
    except _Rejected as error:
        reply = {'error': str(error)}
    except (OSError, UnicodeError, ValueError):
        reply = {'error': f'Fontconfig {kind} check rejected an unreadable or invalid input'}
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
    return _inspect_face(portable_root, font_path, sha256=sha256, scratch=scratch, index=index, sample=sample)


def _inspect_face(portable_root, font_path, *, sha256, scratch, index, sample='', operation=None, deadline=None):
    """The private raster caller shares its cleanup authority and absolute deadline."""
    if operation is None:
        operation = SimpleNamespace(keep=False)
    elif operation.keep:
        raise _Rejected('Fontconfig face check requires confirmed prior Job settlement')
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
    argv = [str(bundle), str(font), sha256, str(index), json.dumps(sample)]
    return _worker_reply(argv, scratch, operation, deadline, 'face')


def _inspect_catalog(bundle, digests, *, operation, deadline):
    # Only the private standard operation can supply the fixed sibling layout;
    # no font/config/root selectors or standalone catalog API.
    if operation.keep:
        raise _Rejected('Fontconfig catalog check requires confirmed prior Job settlement')
    if len(digests) != len(_CATALOG_LEAVES) or any(not re.fullmatch(r'[0-9a-f]{64}', d) for d in digests):
        raise _Rejected('Fontconfig catalog requires four trusted font digests')
    bundle = _local_path(bundle)
    if (bundle / 'shell' != operation.inputs.cwd or bundle / 'inkscape' != operation.inputs.vendor
            or not str(bundle).isascii()):
        raise _Rejected('Fontconfig catalog requires the fixed private staging layout')
    reply = _worker_reply(['catalog', str(bundle), *digests], bundle, operation, deadline, 'catalog')
    expected = {'fontconfig_version': 21701, 'fontconfig_sha256': _DLLS[_FONTCONFIG][1],
                'faces_by_file': list(_CATALOG_COUNTS)}
    if reply != expected:
        raise _Rejected('Fontconfig catalog reply differs from the fixed inventory')
    return _CATALOG_COUNTS


def _worker_reply(argv, scratch, operation, deadline, kind):
    stack = ExitStack()
    with _after(stack.close, f'Fontconfig {kind} scratch pins could not be released'):
        _pin_dirs(stack, scratch)
        directory = Path(tempfile.mkdtemp(dir=scratch, prefix='vtx-fc-'))

        def remove():
            if not operation.keep:
                shutil.rmtree(directory)

        with _after(remove, f'Fontconfig {kind} scratch removal failed'):
            _run_worker(argv, directory, operation, deadline=deadline, kind=kind)
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f'Fontconfig {kind} check deadline expired before reply')
            try:
                with (directory / _REPLY).open('rb') as stream:
                    data = stream.read(_MAX_RESULT + 1)
            except OSError:
                raise _Rejected(f'Fontconfig {kind} reply could not be read') from None
            if len(data) > _MAX_RESULT:
                raise _Rejected(f'Fontconfig {kind} reply exceeds its bound')
            reply = json.loads(data)
            if 'error' in reply:
                raise _Rejected(reply['error'])
            return reply[kind]
