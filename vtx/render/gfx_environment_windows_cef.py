"""No-launch identity for the native Windows x64 CEF publish directory.

The launcher uses the fixed executable, a private scratch working directory,
and the native application/System32 DLL search policy. CEF looks for its pak,
locale and subprocess inputs beside the executable, not in the working directory.
This collector binds the on-disk inputs; it does not prove loaded images or pixels.
"""
import ctypes
from pathlib import Path, PureWindowsPath
import re
import stat
import struct

from . import gfx_environment as env

_WORKER = 'vtx_cef_worker.exe'
# The flat Release/Resources payload of the locked Windows64 CEF distribution.
# Any additional files, including other locale packs, are also hashed.
_REQUIRED = frozenset({
    _WORKER, 'libcef.dll', 'chrome_elf.dll', 'd3dcompiler_47.dll', 'dxcompiler.dll', 'dxil.dll',
    'vk_swiftshader.dll', 'vk_swiftshader_icd.json', 'vulkan-1.dll',
    'icudtl.dat', 'v8_context_snapshot.bin', 'resources.pak',
    'chrome_100_percent.pak', 'chrome_200_percent.pak', 'locales/en-us.pak',
})
_MACHINE = 0x8664
_REPARSE_OR_DEVICE = stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DEVICE


def _native_system():
    """Trust the running OS architecture and directories, not env aliases."""
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    architecture = kernel.IsWow64Process2
    architecture.argtypes = [w.HANDLE, ctypes.POINTER(w.USHORT), ctypes.POINTER(w.USHORT)]
    architecture.restype = w.BOOL
    process, native = w.USHORT(), w.USHORT()
    if (struct.calcsize('P') != 8
            or not architecture(w.HANDLE(-1), ctypes.byref(process), ctypes.byref(native))
            or process.value != 0 or native.value != _MACHINE):
        raise env.EnvironmentError('Windows CEF requires a native x64 OS and Python process')

    def directory(name):
        fn = getattr(kernel, name)
        fn.argtypes, fn.restype = [w.LPWSTR, w.UINT], w.UINT
        value = ctypes.create_unicode_buffer(32768)
        if not 0 < fn(value, len(value)) == len(value.value) < len(value):
            raise env.EnvironmentError('unknown Windows CEF system directory')
        path = Path(value.value)
        if not path.is_absolute():
            raise env.EnvironmentError('non-absolute Windows CEF system directory')
        return path.resolve(strict=True)

    windows = directory('GetSystemWindowsDirectoryW')
    system = directory('GetSystemDirectoryW')
    if system != windows / 'System32':
        raise env.EnvironmentError('inconsistent Windows CEF system directories')
    return windows


def _require_local_drive(executable):
    from ctypes import wintypes as w

    drive_type = ctypes.WinDLL('kernel32', use_last_error=True).GetDriveTypeW
    drive_type.argtypes, drive_type.restype = [w.LPCWSTR], w.UINT
    if not re.fullmatch(r'[A-Za-z]:', executable.drive) or drive_type(executable.anchor) != 3:
        raise env.EnvironmentError('Windows CEF publish directory must be on a fixed local drive')


def _amd64_pe(path, size, *, dll):
    """Check a bounded DOS/COFF header and PE32+ image kind without loading it."""
    with path.open('rb') as stream:
        dos = stream.read(64)
        if len(dos) == 64 and dos[:2] == b'MZ':
            offset, = struct.unpack_from('<I', dos, 60)
            if 64 <= offset <= size - 26:
                stream.seek(offset)
                header = stream.read(26)
                if len(header) == 26 and header[:4] == b'PE\0\0':
                    machine, sections, _, _, _, optional, flags = struct.unpack_from('<HHIIIHH', header, 4)
                    if (machine == _MACHINE and 0 < sections <= 96 and flags & 2
                            and bool(flags & 0x2000) == dll and header[24:] == b'\x0b\x02'
                            and optional >= 112 and offset + 24 + optional + 40 * sections <= size):
                        return
    raise env.EnvironmentError(f'Windows CEF requires an AMD64 PE32+ {"DLL" if dll else "executable"}: {path.name}')


def _ordinary(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & _REPARSE_OR_DEVICE:
        raise env.EnvironmentError(f'unsupported Windows CEF reparse or device entry: {path.name}')
    return info


def font_directory(root, *, optional=False):
    """Enumerate the ordinary font tree; absence is allowed only for the user root."""
    try:
        info = _ordinary(root)
    except FileNotFoundError:
        if optional:
            return
        raise
    if not stat.S_ISDIR(info.st_mode):
        raise env.EnvironmentError('not a Windows CEF font directory')
    pending = [root]
    while pending:
        for p in pending.pop().iterdir():
            info = _ordinary(p)
            if stat.S_ISDIR(info.st_mode):
                pending.append(p)
            elif stat.S_ISREG(info.st_mode):
                yield p
            else:
                raise env.EnvironmentError('unknown Windows CEF font entry type')


def _directory_token(info):
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns


def _runtime(executable):
    """Hash all ordinary files and guard the path/membership while scanning."""
    root = executable.parent
    files, folded, directories, tokens = {}, {}, [], []
    pending = [root]
    version = None
    while pending:
        base = pending.pop()
        state = _ordinary(base)
        if not stat.S_ISDIR(state.st_mode) or base.resolve(strict=True) != base:
            raise env.EnvironmentError('unresolved Windows CEF publish directory')
        members = sorted(base.iterdir(), key=lambda p: p.name)
        directories.append((base, _directory_token(state), [p.name for p in members]))
        names = set()
        for p in members:
            name = p.name.casefold()
            if name in names:
                raise env.EnvironmentError('ambiguous Windows CEF publish member name')
            names.add(name)
            info = _ordinary(p)
            if stat.S_ISDIR(info.st_mode):
                pending.append(p)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise env.EnvironmentError('unknown Windows CEF publish member type')
            relative = p.relative_to(root).as_posix()
            key = relative.casefold()
            token = env._file_token(p, info)
            if key in _REQUIRED and not info.st_size:
                raise env.EnvironmentError(f'empty Windows CEF runtime member: {relative}')
            if key in _REQUIRED and p.suffix.lower() in ('.dll', '.exe'):
                _amd64_pe(p, info.st_size, dll=key != _WORKER)
            if key == 'libcef.dll':
                # The built worker need not carry a version resource. Read
                # the deployed CEF DLL's fixed PE version, without loading it.
                version = env._windows_version(p)
            files[relative] = list(env._file_resolved(p, info))
            tokens.append((p, token))
            folded[key] = relative
    if _REQUIRED - folded.keys():
        raise env.EnvironmentError('incomplete Windows CEF publish directory; missing '
                                   + ', '.join(sorted(_REQUIRED - folded.keys())))
    if not isinstance(version, str) or not re.fullmatch(r'[1-9][0-9]*(\.[0-9]+){3}', version):
        raise env.EnvironmentError('unknown Windows CEF runtime version')
    for p, token in tokens:
        if env._file_token(p, _ordinary(p)) != token:
            raise env.EnvironmentError(f'Windows CEF member changed while hashing: {p.relative_to(root)}')
    for base, token, names in reversed(directories):
        if (base.resolve(strict=True) != base or _directory_token(_ordinary(base)) != token
                or sorted(p.name for p in base.iterdir()) != names):
            raise env.EnvironmentError('Windows CEF publish directory changed while hashing')
    return dict(sorted(files.items())), version, files[folded[_WORKER]]


def collect(binary, mode):
    from .gfx_backend import cef_configuration, cef_launch_flags

    if mode != 'a0':
        raise env.EnvironmentError('Windows CEF requires A0; no mode fallback')
    try:
        configuration, launch_flags = cef_configuration(), cef_launch_flags(mode)
    except ValueError as error:
        raise env.EnvironmentError(str(error)) from error
    windows = _native_system()
    path = Path(binary)
    if not path.is_absolute() or path.name.lower() != _WORKER:
        raise env.EnvironmentError('Windows CEF requires the absolute published vtx_cef_worker.exe')
    _require_local_drive(path)
    _ordinary(path)
    executable = path.resolve(strict=True)
    if executable.name.lower() != _WORKER:
        raise env.EnvironmentError('Windows CEF requires the published vtx_cef_worker.exe')
    if path.drive != executable.drive:
        _require_local_drive(executable)
    runtime, version, (worker_path, worker_sha) = _runtime(executable)
    return {'platform_resolver': env.WINDOWS_CEF_RESOLVER, 'version': version, 'architecture': _MACHINE,
            'browser': {worker_path: worker_sha}, 'runtime': {str(executable.parent): runtime},
            'launch_flags': launch_flags, 'cef_configuration': configuration,
            'windows_loader': {'system32': str(windows / 'System32'),
                               'application': str(executable.parent),
                               'cwd': 'owned-scratch', 'path_policy': 'system32-only',
                               'profile': 'owned-localappdata-temp'},
            **env._windows_host(windows, cef=True)}


def complete_certificate_axes(axes):
    """Check this resolver's required wire structure; not a local trust proof."""
    runtime, configuration = axes.get('runtime'), axes.get('cef_configuration')
    loader = axes.get('windows_loader')
    if (axes.get('backend') != 'cef' or axes.get('mode') != 'a0'
            or axes.get('architecture') != _MACHINE
            or not isinstance(loader, dict) or loader.get('cwd') != 'owned-scratch'
            or loader.get('path_policy') != 'system32-only'
            or loader.get('profile') != 'owned-localappdata-temp'
            or not isinstance(loader.get('system32'), str)
            or not isinstance(configuration, dict)
            or any(not isinstance(configuration.get(key), dict) or not configuration[key]
                   for key in ('runtime', 'scheduling'))
            or not isinstance(axes.get('launch_flags'), list) or 'mode=a0' not in axes['launch_flags']
            or not isinstance(runtime, dict) or len(runtime) != 1):
        return False
    root, files = next(iter(runtime.items()))
    if not isinstance(root, str) or not isinstance(files, dict):
        return False
    directory = PureWindowsPath(root)
    system32 = PureWindowsPath(loader['system32'])
    if (not directory.is_absolute() or not re.fullmatch(r'[A-Za-z]:', directory.drive)
            or loader.get('application') != root or not system32.is_absolute()
            or system32.name.casefold() != 'system32'):
        return False
    members = {}
    for name, entry in files.items():
        if (not isinstance(name, str) or not isinstance(entry, list) or len(entry) != 2
                or not all(isinstance(value, str) for value in entry)):
            return False
        relative = PureWindowsPath(name)
        key = name.casefold()
        if (relative.anchor or '..' in relative.parts or not relative.parts
                or relative.as_posix() != name or key in members
                or PureWindowsPath(entry[0]) != directory / relative
                or not re.fullmatch(r'[0-9a-f]{64}', entry[1])):
            return False
        members[key] = entry
    return _REQUIRED <= members.keys() and axes.get('browser') == dict([members[_WORKER]])
