"""Conservative, host-scoped gfx input identity (#48).

This binds inputs, not cross-machine pixel equivalence. In particular a matching
manifest must never promote environment_identity to passed in Golden evidence.
Unknown/unreadable inputs fail closed. Collection starts no browser and writes
nothing (including on dry runs). POSIX digests are memoized by
inode/size/mtime/ctime; Windows always hashes bytes because even ChangeTime can
collide on rapid same-size writes. Membership is re-read at every recheck.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import threading
import contextlib
import io
import importlib.metadata
import sys
import ctypes
import plistlib
import struct
import uuid
import stat
from concurrent.futures import ThreadPoolExecutor

SCHEMA = 'vtx.gfx-environment/1'
CHROME_VISUAL_FLAGS = ('--headless', '--disable-gpu', '--hide-scrollbars',
                       '--force-device-scale-factor=1', '--force-color-profile=srgb',
                       '--font-render-hinting=none')
_lock = threading.RLock()
_hashes = {}
_ROOT = Path(__file__).resolve().parents[1]
# CDP PNG compression/scheduling are provenance only. Experimental CEF also
# binds its explicit runtime/scheduling configuration (see gfx_backend).
PIXEL_ENV = ('LANG', 'LANGUAGE', 'LC_ALL', 'LC_CTYPE', 'LC_NUMERIC', 'TZ',
             'FONTCONFIG_FILE', 'FONTCONFIG_PATH', 'FONTCONFIG_SYSROOT',
             'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_DATA_DIRS', 'XDG_CACHE_HOME', 'HOME',
             'DISPLAY', 'WAYLAND_DISPLAY', 'XDG_SESSION_TYPE',
             'LD_LIBRARY_PATH', 'LD_PRELOAD', 'LIBGL_DRIVERS_PATH',
             'LIBGL_ALWAYS_SOFTWARE', 'MESA_LOADER_DRIVER_OVERRIDE',
             'GALLIUM_DRIVER', '__GLX_VENDOR_LIBRARY_NAME',
             'VK_ICD_FILENAMES', 'VK_DRIVER_FILES', 'DRI_PRIME',
             'VTX_GFX_NAVFREE', 'VTX_GFX_STATIC')

_CORETEXT = '/System/Library/Frameworks/CoreText.framework'
_COREFOUNDATION = '/System/Library/Frameworks/CoreFoundation.framework'


def _darwin_bundle(root):
    """Hash one app, including framework links, without traversing outside it."""
    root = Path(root).resolve(strict=True)
    files, links = {}, {}
    for base, dirs, names in os.walk(root, onerror=lambda e: _raise(e)):
        for name in sorted(dirs + names):
            p = Path(base) / name
            target = p.resolve(strict=True)
            if not target.is_relative_to(root):
                raise EnvironmentError(f'Darwin bundle link escapes distribution: {p}')
            relative = str(p.relative_to(root))
            link = p.is_symlink()
            link_text = os.readlink(p) if link else None
            if link:
                links[relative] = [link_text, str(target.relative_to(root))]
            target_stat = target.stat()
            if stat.S_ISREG(target_stat.st_mode):
                # `target` is already the canonical strict resolution above.
                # Reuse it and its stat instead of resolving/statting it again
                # in _file; the helper still hashes bytes and checks the file
                # token both before and after the read. Check the lexical entry
                # after hashing: regular files need only lstat, while links are
                # re-resolved and compared with their captured link text.
                files[relative] = list(_file_resolved(
                    target, target_stat, verify_entry=(p, link_text, target_stat)))
            elif not stat.S_ISDIR(target_stat.st_mode):
                raise EnvironmentError(f'unknown Darwin bundle member: {p}')
    if not files:
        raise EnvironmentError(f'empty Darwin app distribution: {root}')
    return {'files': files, 'links': links}


def _darwin_native(path):
    """Require a native arm64 Mach-O slice; Rosetta/other CPUs are not supported."""
    with Path(path).open('rb') as f:
        header = f.read(8)
        if len(header) != 8:
            raise EnvironmentError(f'truncated Darwin renderer: {path}')
        if header[:4] == b'\xcf\xfa\xed\xfe':
            cpus = [struct.unpack('<I', header[4:])[0]]
        elif header[:4] in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
            count = struct.unpack('>I', header[4:])[0]
            width = 20 if header[:4] == b'\xca\xfe\xba\xbe' else 32
            if not 1 <= count <= 16:
                raise EnvironmentError('unknown Darwin universal binary inventory')
            records = f.read(count * width)
            if len(records) != count * width:
                raise EnvironmentError('truncated Darwin universal binary inventory')
            cpus = [struct.unpack_from('>I', records, i * width)[0] for i in range(count)]
        else:
            raise EnvironmentError(f'native Mach-O renderer required: {path}')
    if 0x100000c not in cpus:
        raise EnvironmentError(f'native arm64 renderer required: {path}')
    return sorted(cpus)


def _darwin_load_commands(path):
    """Read native Chrome Mach-O dependency names without installing otool."""
    with Path(path).open('rb') as f:
        first = f.read(8)
        if first[:4] not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
            return None
        offset = 0
        if first[:4] != b'\xcf\xfa\xed\xfe':
            _darwin_native(path)
            count = struct.unpack('>I', first[4:])[0]
            width = 20 if first[:4] == b'\xca\xfe\xba\xbe' else 32
            records = f.read(count * width)
            selected = [i * width for i in range(count)
                        if struct.unpack_from('>I', records, i * width)[0] == 0x100000c]
            if len(selected) != 1:
                raise EnvironmentError('ambiguous native Darwin binary slices')
            offset = struct.unpack_from('>I' if width == 20 else '>Q', records, selected[0] + 8)[0]
        f.seek(offset)
        header = f.read(32)
        if (len(header) != 32 or header[:4] != b'\xcf\xfa\xed\xfe'
                or struct.unpack_from('<I', header, 4)[0] not in (0x100000c, 0x1000007)):
            raise EnvironmentError(f'unsupported Darwin binary slice: {path}')
        count, size = struct.unpack_from('<II', header, 16)
        if count > 65536 or size > 16 * 1024 * 1024 or offset + 32 + size > Path(path).stat().st_size:
            raise EnvironmentError('invalid Darwin load command inventory')
        commands = f.read(size)
    deps, rpaths, at = [], [], 0
    for _ in range(count):
        if at + 8 > len(commands):
            raise EnvironmentError('truncated Darwin load commands')
        kind, length = struct.unpack_from('<II', commands, at)
        if length < 8 or at + length > len(commands):
            raise EnvironmentError('invalid Darwin load command length')
        if kind == 0x27:  # LC_DYLD_ENVIRONMENT can add an otherwise unbound loader override.
            raise EnvironmentError('unsupported embedded Darwin loader environment')
        if kind in (0xc, 0x80000018, 0x8000001f, 0x20, 0x80000023, 0x8000001c, 0xe):
            if length < 12:
                raise EnvironmentError('truncated Darwin dependency command')
            start = struct.unpack_from('<I', commands, at + 8)[0]
            if start < 12 or start >= length or b'\0' not in commands[at + start:at + length]:
                raise EnvironmentError('invalid Darwin dependency name')
            value = commands[at + start:at + length].split(b'\0', 1)[0].decode('utf-8')
            if kind == 0xe:  # LC_LOAD_DYLINKER: the file is separately bound below.
                if value != '/usr/lib/dyld':
                    raise EnvironmentError('unsupported Darwin dynamic linker')
            else:
                (rpaths if kind == 0x8000001c else deps).append(value)
        at += length
    if at != size:
        raise EnvironmentError('mismatched Darwin load command inventory')
    # Intel-only Widevine is an inactive distribution resource on arm64.
    # Validate its structure and retain all bytes in the bundle hash, but do
    # not pretend its dependency list is native. Referenced targets must pass
    # _darwin_native separately.
    if struct.unpack_from('<I', header, 4)[0] == 0x1000007:
        return None
    return deps, rpaths, struct.unpack_from('<I', header, 12)[0]


def _darwin_dependencies(root, runtime):
    lib = ctypes.CDLL(None)
    contains = lib._dyld_shared_cache_contains_path
    contains.argtypes, contains.restype = [ctypes.c_char_p], ctypes.c_bool
    out = {}
    for path in sorted({v[0] for v in runtime['files'].values()}):
        commands = _darwin_load_commands(path)
        if commands is None:
            continue
        deps, rpaths, filetype = commands
        # MH_EXECUTE helpers outside .app wrappers use their own directory.
        # Dylibs retain the containing app executable context.
        executable_dir = (Path(path).parent if filetype == 2 else
                          next((p / 'Contents/MacOS' for p in Path(path).parents
                                if p.suffix == '.app'), root / 'Contents/MacOS'))

        def expand(value):
            for prefix, directory in (('@loader_path/', Path(path).parent),
                                      ('@executable_path/', executable_dir)):
                if value == prefix[:-1]:
                    return directory
                if value.startswith(prefix):
                    return directory / value[len(prefix):]
            if value.startswith('/'):
                return Path(value)
            raise EnvironmentError(f'unresolved Darwin library path: {value}')

        search = [expand(r).resolve() for r in rpaths]
        if any(not p.is_relative_to(root) for p in search):
            raise EnvironmentError(f'external Darwin library search path: {path}')
        resolved = {}
        for dep in deps:
            if dep.startswith(('/usr/lib/', '/System/Library/')) and contains(os.fsencode(dep)):
                resolved[dep] = 'system-cache'
                continue
            choices = ([r / dep[len('@rpath/'):] for r in search]
                       if dep.startswith('@rpath/') else [expand(dep)])
            matches = [p.resolve(strict=True) for p in choices if p.is_file()]
            if not matches or any(not p.is_relative_to(root) for p in matches):
                raise EnvironmentError(f'unresolved/external Darwin dependency: {path}: {dep}')
            for match in matches:
                _darwin_native(match)
            resolved[dep] = sorted(set(map(str, matches)))
        out[path] = resolved
    if not out:
        raise EnvironmentError('empty Darwin native dependency inventory')
    return out


def _darwin_dyld():
    """Ask the running native process for its actual system cache, not a guess."""
    lib = ctypes.CDLL(None)
    path_fn = lib.dyld_shared_cache_file_path
    path_fn.argtypes, path_fn.restype = [], ctypes.c_char_p
    uuid_fn = lib._dyld_get_shared_cache_uuid
    uuid_fn.argtypes, uuid_fn.restype = [ctypes.c_void_p], ctypes.c_bool
    value = (ctypes.c_ubyte * 16)()
    path = path_fn()
    if not path or not uuid_fn(value) or not any(value):
        raise EnvironmentError('unknown active Darwin system cache')
    for name in ('dyld_shared_cache_some_image_overridden',
                 'dyld_has_inserted_or_interposing_libraries'):
        check = getattr(lib, name)
        check.argtypes, check.restype = [], ctypes.c_bool
        if check():
            raise EnvironmentError('unsupported Darwin library override')
    return Path(os.fsdecode(path)), bytes(value)


def _darwin_cache(path, loaded_uuid):
    """Hash actual modern dyld cache/subcache bytes, checking their UUID closure.

    Layout: Apple's dyld_cache_header and dyld_subcache_entry. Older layouts
    without named subcaches are deliberately unsupported. No SDK is required.
    """
    path = Path(path).resolve(strict=True)
    with path.open('rb') as f:
        header = f.read(464)
        if (len(header) != 464 or not header.startswith(b'dyld_v1')
                or b'arm64' not in header[:16]
                or struct.unpack_from('<I', header, 16)[0] < 464
                or header[88:104] != loaded_uuid):
            raise EnvironmentError('unknown/mismatched Darwin dyld cache header')
        offset, count = struct.unpack_from('<II', header, 392)
        if count > 128 or (count and (offset < 464 or offset + count * 56 > path.stat().st_size)):
            raise EnvironmentError('invalid Darwin dyld subcache inventory')
        f.seek(offset)
        records = f.read(count * 56)
    if len(records) != count * 56:
        raise EnvironmentError('truncated Darwin dyld subcache inventory')
    paths, seen = [path], set()
    for i in range(count):
        record = records[i * 56:(i + 1) * 56]
        suffix = record[24:].split(b'\0', 1)[0].decode('ascii')
        if not re.fullmatch(r'\.[A-Za-z0-9_.]+', suffix) or suffix in seen:
            raise EnvironmentError('unknown/duplicate Darwin dyld subcache suffix')
        seen.add(suffix)
        member = path.with_name(path.name + suffix)
        with member.open('rb') as f:
            subheader = f.read(104)
        if (len(subheader) != 104 or subheader[:16] != header[:16]
                or subheader[88:104] != record[:16]):
            raise EnvironmentError(f'mismatched Darwin dyld subcache: {member}')
        paths.append(member)
    return {'uuid': str(uuid.UUID(bytes=loaded_uuid)), 'files': _darwin_cache_files(paths)}


def _darwin_cache_files(paths):
    """Hash a validated Darwin cache closure with a small bounded worker pool."""
    ordered = sorted(set(map(str, paths)))
    if len(ordered) < 2:
        return _files(ordered)

    # Keep _files' path normalization and result ordering, while allowing the
    # large independent cache members to be read and hashed concurrently.
    with ThreadPoolExecutor(max_workers=min(4, len(ordered))) as executor:
        pending = [executor.submit(_file, path) for path in ordered]
        results = [future.result() for future in pending]
    return dict(sorted(results))


def _collect_darwin_renderer(backend, executable, root, architecture, mode):
    """Collect renderer inputs independent of the active system cache bytes."""
    from .gfx_backend import cef_configuration, cef_launch_flags
    runtime = _darwin_bundle(root)
    if not any('/Frameworks/' in p for p in runtime['files']):
        raise EnvironmentError('Darwin Chrome framework distribution is missing')
    if backend == 'cef' and not any('Chromium Embedded Framework.framework/' in p for p in runtime['files']):
        raise EnvironmentError('Darwin CEF framework distribution is missing')
    dependencies = _darwin_dependencies(root, runtime)
    application_domains = _darwin_application_domains(root, dependencies)
    boot = _command(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']).strip()
    if not re.fullmatch(r'[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', boot):
        raise EnvironmentError('unknown Darwin host/boot identity')
    version = _command([str(executable), '--sanity' if backend == 'cef' else '--version']).strip()
    if not version:
        raise EnvironmentError('empty actual Darwin renderer version')
    system = _files(['/System/Library/CoreServices/SystemVersion.plist', '/usr/lib/dyld'] +
                    ([Path('/etc/localtime')] if Path('/etc/localtime').exists() else []))
    browser = _files([executable])
    return {'browser': browser, 'version': version, 'runtime': {str(root): runtime},
            'architecture': architecture, 'dependencies': dependencies,
            'application_domains': application_domains,
            'host_boot': {'boot_session_uuid': boot, 'hostname': platform.node()},
            'os': list(platform.uname()), 'system_locale': system,
            'tools': _files(['/usr/sbin/sysctl']),
            'environment': {k: os.environ.get(k) for k in sorted(set(PIXEL_ENV) |
                             {k for k in os.environ if k.startswith(('LC_', 'DYLD_', '__XPC_DYLD_', '__CF_'))})},
            'launch_flags': (list(CHROME_VISUAL_FLAGS) if backend == 'cdp' else
                             cef_launch_flags(mode) + ['--use-mock-keychain']),
            **({'cef_configuration': cef_configuration()} if backend == 'cef' else {}),
            'protocol': {'device_scale': 1, 'color': 'srgb',
                         'alpha': 'native-or-white-black-solve', 'class': 'full-html'},
            'python_runtime': _python_runtime(),
            'implementation': _files([_ROOT / p for p in (
                'render/gfx_environment.py', 'render/gfx_backend.py', 'render/gfx_render.py', 'render/gfx_cli.py', 'render/browser_paths.py',
                'render/gfx_cef.py', 'render/cef_client.py')])}


def _darwin_font_inventory():
    """Use CoreText's available URLs and faces, not Linux fontconfig on macOS."""
    cf = ctypes.CDLL(_COREFOUNDATION + '/CoreFoundation')
    ct = ctypes.CDLL(_CORETEXT + '/CoreText')
    for name in ('CTFontManagerCopyAvailableFontURLs', 'CTFontManagerCopyAvailablePostScriptNames'):
        fn = getattr(ct, name)
        fn.argtypes, fn.restype = [], ctypes.c_void_p
    cf.CFArrayGetCount.argtypes, cf.CFArrayGetCount.restype = [ctypes.c_void_p], ctypes.c_long
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFURLGetFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                                   ctypes.c_void_p, ctypes.c_long]
    cf.CFURLGetFileSystemRepresentation.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long,
                                     ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes, cf.CFRelease.restype = [ctypes.c_void_p], None

    def values(fn, urls):
        array = fn()
        if not array:
            raise EnvironmentError('unknown CoreText font inventory')
        out = []
        try:
            count = cf.CFArrayGetCount(array)
            if not 0 < count <= 100000:
                raise EnvironmentError('unknown/empty CoreText font inventory')
            for i in range(count):
                value = cf.CFArrayGetValueAtIndex(array, i)
                buf = ctypes.create_string_buffer(32768)
                ok = (cf.CFURLGetFileSystemRepresentation(value, True, buf, len(buf)) if urls
                      else cf.CFStringGetCString(value, buf, len(buf), 0x08000100))
                if not ok or not buf.value:
                    raise EnvironmentError('unreadable CoreText font inventory entry')
                out.append(os.fsdecode(buf.value) if urls else buf.value.decode('utf-8'))
        finally:
            cf.CFRelease(array)
        return sorted(set(out))

    return (values(ct.CTFontManagerCopyAvailableFontURLs, True),
            values(ct.CTFontManagerCopyAvailablePostScriptNames, False))


def _darwin_application_domains(root, dependencies):
    """NSUserDefaults application domains come from actual app/helper metadata."""
    apps = {root}
    for binary in dependencies:
        apps.update(p for p in Path(binary).parents
                    if p.suffix == '.app' and p.is_relative_to(root))
    domains = set()
    for app in apps:
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        domain = info.get('CFBundleIdentifier')
        if not isinstance(domain, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', domain):
            raise EnvironmentError(f'unknown Darwin application preference domain: {app}')
        domains.add(domain)
    return sorted(domains)


def _darwin_font_catalog():
    """Collect CoreText inputs independent of application preference domains."""
    paths, names = _darwin_font_inventory()
    if not paths or not names or any(not Path(p).is_absolute() for p in paths):
        raise EnvironmentError('unknown/empty CoreText font inventory')
    resources = _tree(Path(_CORETEXT) / 'Resources')
    return {'files': _files(paths), 'tools': _files([sys.executable, _ROOT / 'render/gfx_environment.py']),
            'urls': paths, 'postscript_names': names, 'resources': resources}


def _darwin_fonts(libraries, application_domains, catalog=None):
    if catalog is None:
        catalog = _darwin_font_catalog()
    paths, names = catalog['urls'], catalog['postscript_names']
    resources = catalog['resources']
    # NSUserDefaults searches the application domain before the global domain.
    # Hash only the named preference files, never Chrome profiles or raw values.
    preferences = []
    domains = sorted(set(application_domains) | {'.GlobalPreferences', 'com.apple.CoreText', 'com.apple.ATS'})
    names_by_domain = {domain + '.plist' for domain in domains}
    by_host = re.compile(r'(?:' + '|'.join(map(re.escape, domains)) + r')\.[A-Za-z0-9-]+\.plist')
    for root in (Path('/Library/Preferences'), Path.home() / 'Library/Preferences'):
        try:
            entries = list(root.iterdir())
        except FileNotFoundError:
            continue
        boundary = root.resolve(strict=True)
        selected = []
        for p in entries:
            if p.name in names_by_domain:
                selected.append(p)
            elif p.name == 'ByHost':
                if not p.resolve(strict=True).is_relative_to(boundary):
                    raise EnvironmentError(f'Darwin ByHost directory escapes preferences: {p}')
                if p.is_symlink():
                    raise EnvironmentError(f'unsupported Darwin ByHost directory symlink: {p}')
                selected.extend(q for q in p.iterdir() if by_host.fullmatch(q.name))
        if any(not p.resolve(strict=True).is_relative_to(boundary) for p in selected):
            raise EnvironmentError('Darwin preference file escapes its directory')
        # Resolved-target hashes alone cannot bind a symlink's preference domain.
        if any(p.is_symlink() for p in selected):
            raise EnvironmentError('unsupported Darwin preference file symlink')
        preferences.extend(selected)
    configs = {value[0]: value[1] for value in resources.values()}
    configs.update(_files(preferences))
    return {'files': catalog['files'], 'configs': configs,
            'inventory': {'urls': paths, 'postscript_names': names, 'resources': resources,
                          'application_domains': application_domains},
            'tools': catalog['tools'], 'libraries': libraries}


def _collect_darwin(backend, binary, mode):
    if backend not in ('cdp', 'cef') or (backend == 'cdp' and mode is not None) or platform.machine() != 'arm64':
        raise EnvironmentError('Darwin resolver requires native arm64 CDP')
    if (any(os.environ[k] for k in os.environ if k.startswith(('DYLD_', '__XPC_DYLD_')))
            or any(os.environ.get(k) for k in ('LD_LIBRARY_PATH', 'LD_PRELOAD',
                       'LIBGL_DRIVERS_PATH', 'VK_ICD_FILENAMES', 'VK_DRIVER_FILES', 'CFFIXED_USER_HOME'))):
        raise EnvironmentError('unsupported Darwin DYLD environment override')
    executable = Path(shutil.which(str(binary)) or binary).resolve(strict=True)
    if executable.parent.name != 'MacOS' or executable.parent.parent.name != 'Contents':
        raise EnvironmentError('Darwin resolver requires a Chrome app bundle executable')
    root = executable.parent.parent.parent
    if root.suffix != '.app':
        raise EnvironmentError('Darwin resolver requires a Chrome app bundle')
    info = plistlib.loads((root / 'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleExecutable') != executable.name
            or (backend == 'cdp' and info.get('CFBundleIdentifier') != 'com.google.Chrome')):
        raise EnvironmentError('Darwin resolver supports the Google Chrome app bundle only')
    architecture = _darwin_native(executable)
    cache_path, cache_uuid = _darwin_dyld()
    # Cache bytes, renderer inputs, and CoreText inputs are independent identity axes.
    with ThreadPoolExecutor(max_workers=3) as executor:
        cache_future = executor.submit(_darwin_cache, cache_path, cache_uuid)
        renderer_future = executor.submit(_collect_darwin_renderer, backend, executable, root,
                                         architecture, mode)
        font_future = executor.submit(_darwin_font_catalog)
        cache = cache_future.result()
        renderer = renderer_future.result()
        font_catalog = font_future.result()
    libraries = cache['files']
    fonts = _darwin_fonts(libraries, renderer['application_domains'], font_catalog)
    return {'schema': SCHEMA, 'status': 'input-bound', 'pixel_equivalence': 'unproven',
            'backend': backend, 'mode': mode, 'platform_resolver': 'darwin-arm64-' + backend + '/1',
            'browser': renderer['browser'], 'version': renderer['version'],
            'runtime': renderer['runtime'], 'libraries': libraries,
            'system_cache': cache, 'architecture': renderer['architecture'],
            'dependencies': renderer['dependencies'], 'fonts': fonts,
            'host_boot': renderer['host_boot'], 'os': renderer['os'],
            'system_locale': renderer['system_locale'], 'tools': renderer['tools'],
            'environment': renderer['environment'], 'launch_flags': renderer['launch_flags'],
            **({'cef_configuration': renderer['cef_configuration']} if backend == 'cef' else {}),
            'protocol': renderer['protocol'], 'python_runtime': renderer['python_runtime'],
            'implementation': renderer['implementation']}


def _windows_registry(root, name):
    """Inventory names/types and hash values; never publish raw preferences."""
    import winreg
    result = {}
    def visit(key, prefix):
        children, values, _ = winreg.QueryInfoKey(key)
        for i in range(values):
            n, value, kind = winreg.EnumValue(key, i)
            if isinstance(value, bytes):
                value = value.hex()
            result[prefix + '/' + n] = [kind, digest(value)]
        for i in range(children):
            child = winreg.EnumKey(key, i)
            with winreg.OpenKey(key, child) as sub:
                visit(sub, prefix + '/' + child)
    try:
        with winreg.OpenKey(root, name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            visit(key, name)
    except FileNotFoundError:
        return {'absent': digest(name)}
    return result or {'empty': digest(name)}


def _windows_version(executable):
    from ctypes import wintypes
    api = ctypes.WinDLL('version', use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    unused = wintypes.DWORD()
    size = api.GetFileVersionInfoSizeW(str(executable), ctypes.byref(unused))
    if not size:
        raise EnvironmentError('unknown Windows renderer PE version')
    data = ctypes.create_string_buffer(size)
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if (not api.GetFileVersionInfoW(str(executable), 0, size, data)
            or not api.VerQueryValueW(data, chr(92), ctypes.byref(pointer), ctypes.byref(length))
            or length.value < 52):
        raise EnvironmentError('unreadable Windows renderer PE version')
    info = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    if info[0] != 0xFEEF04BD:
        raise EnvironmentError('invalid Windows renderer PE version')
    return '.'.join(str(v) for v in (info[2] >> 16, info[2] & 65535, info[3] >> 16, info[3] & 65535))


def _windows_boot():
    # SystemTimeOfDayInformation begins with the boot FILETIME. Uptime-derived
    # timestamps drift between collections and cannot identify one boot.
    data = ctypes.create_string_buffer(48)
    size = ctypes.c_ulong()
    query = ctypes.WinDLL('ntdll').NtQuerySystemInformation
    query.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    if query(3, data, len(data), ctypes.byref(size)) != 0:
        raise EnvironmentError('unknown Windows boot identity')
    boot = struct.unpack_from('<q', data.raw)[0]
    if boot <= 0:
        raise EnvironmentError('invalid Windows boot identity')
    return str(boot)


def _collect_windows(backend, binary, mode):
    import winreg
    if (backend not in ('cdp', 'cli') or mode is not None
            or platform.machine().upper() not in ('AMD64', 'X86_64') or struct.calcsize('P') != 8):
        raise EnvironmentError('Windows resolver requires native x64 Chrome/Edge CDP or CLI')
    if any(os.environ.get(k) for k in ('FONTCONFIG_FILE', 'FONTCONFIG_PATH', 'FONTCONFIG_SYSROOT',
                                      'LD_PRELOAD', 'LD_LIBRARY_PATH', 'VK_ICD_FILENAMES', 'VK_DRIVER_FILES')):
        raise EnvironmentError('unsupported Windows renderer environment override')
    executable = Path(shutil.which(str(binary)) or binary).resolve(strict=True)
    if executable.name.lower() not in ('chrome.exe', 'msedge.exe') or executable.parent.name != 'Application':
        raise EnvironmentError('Windows resolver requires a Chrome/Edge Application distribution')
    with executable.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise EnvironmentError('Windows renderer is not a native PE executable')
        stream.seek(60)
        offset = struct.unpack('<I', stream.read(4))[0]
        stream.seek(offset)
        if stream.read(6) != b'PE\0\0d\x86':
            raise EnvironmentError('Windows renderer requires AMD64 PE architecture')
    version = _windows_version(executable)
    version_root = executable.parent / version
    if not version_root.is_dir() or not (version_root / ('chrome.dll' if executable.name.lower() == 'chrome.exe' else 'msedge.dll')).is_file():
        raise EnvironmentError('Windows renderer version/distribution mismatch')
    windows = Path(os.environ['SystemRoot']).resolve(strict=True)
    system = windows / 'System32'
    # Include delay-loaded DLLs and graphics driver packages, not just imports.
    libraries = _files(p for p in system.iterdir() if p.is_file() and p.suffix.lower() in ('.dll', '.exe', '.nls'))
    drivers = []
    driver_root = system / 'DriverStore/FileRepository'
    for base, dirs, names in os.walk(driver_root, onerror=lambda e: _raise(e)):
        if any((Path(base) / d).is_symlink() for d in dirs):
            raise EnvironmentError('unknown Windows driver directory link')
        drivers.extend(Path(base) / n for n in names if Path(n).suffix.lower() in ('.dll', '.sys', '.exe', '.json', '.inf'))
    font_paths = []
    font_roots = [windows / 'Fonts', Path(os.environ['LOCALAPPDATA']) / 'Microsoft/Windows/Fonts']
    for root in font_roots:
        if root.exists():
            font_paths.extend(p for p in root.rglob('*') if p.is_file())
    registry = {}
    for label, root in (('machine', winreg.HKEY_LOCAL_MACHINE), ('user', winreg.HKEY_CURRENT_USER)):
        for suffix in ('Fonts', 'FontSubstitutes', 'FontLink', 'FontMapper', 'Font Drivers'):
            name = 'SOFTWARE/Microsoft/Windows NT/CurrentVersion/' + suffix
            name = name.replace('/', chr(92))
            registry[label + '/' + suffix] = _windows_registry(root, name)
            if suffix == 'Fonts':
                try:
                    with winreg.OpenKey(root, name) as key:
                        for i in range(winreg.QueryInfoKey(key)[1]):
                            _, value, kind = winreg.EnumValue(key, i)
                            if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
                                raise EnvironmentError('unknown Windows font registry entry')
                            p = Path(os.path.expandvars(value))
                            font_paths.append(p if p.is_absolute() else windows / 'Fonts' / p)
                except FileNotFoundError:
                    pass
    if not font_paths:
        raise EnvironmentError('empty Windows font inventory')
    for name in ('Control Panel/Desktop', 'Control Panel/Colors', 'Control Panel/International'):
        registry[name] = _windows_registry(winreg.HKEY_CURRENT_USER, name.replace('/', chr(92)))
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\Microsoft\\Cryptography', 0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        machine, _ = winreg.QueryValueEx(key, 'MachineGuid')
    return {'schema': SCHEMA, 'status': 'input-bound', 'pixel_equivalence': 'unproven',
            'backend': backend, 'mode': mode, 'platform_resolver': 'windows-x64/1',
            'browser': _files([executable]), 'version': version,
            'runtime': {str(executable.parent): (_windows_chrome_runtime(executable.parent)
                        if executable.name.lower() == 'chrome.exe' else _tree(executable.parent))},
            'libraries': libraries, 'graphics_libraries': _files(drivers),
            'fonts': {'files': _files(font_paths), 'configs': {'registry': digest(registry)},
                      'tools': _files([sys.executable, _ROOT / 'render/gfx_environment.py']), 'libraries': libraries},
            'registry': registry,
            'host_boot': {'machine_guid': digest(machine), 'boot_filetime': _windows_boot()},
            'os': list(platform.uname()), 'environment': {k: os.environ.get(k) for k in
                sorted(set(PIXEL_ENV) | {'SystemRoot', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'PATH'})},
            'launch_flags': list(CHROME_VISUAL_FLAGS),
            'protocol': {'device_scale': 1, 'color': 'srgb', 'alpha': 'native-or-white-black-solve', 'class': 'full-html'},
            'python_runtime': _python_runtime(),
            'implementation': _files([_ROOT / p for p in ('render/gfx_environment.py', 'render/gfx_backend.py',
                'render/gfx_render.py', 'render/gfx_cli.py', 'render/gfx_cef.py', 'render/cef_client.py', 'runtime/windows_browser.py',
                'render/browser_paths.py')])}


class EnvironmentError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _command(args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30,
                           encoding='utf-8', errors='strict')
        if r.returncode:
            raise EnvironmentError(f'{args[0]} exited {r.returncode}')
        return r.stdout
    except (OSError, subprocess.SubprocessError, UnicodeError) as e:
        raise EnvironmentError(f'gfx environment unknown: {args[0]}: {e}') from e


def _file_token(path, st):
    token = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if os.name == 'nt':
        # CPython's Windows st_ctime is creation time, not inode change time.
        # FILE_BASIC_INFO.ChangeTime adds a read-before/after change check.
        # It is not a content identity: rapid writes can share the same token.
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = ctypes.create_string_buffer(40)
            if not kernel.GetFileInformationByHandleEx(handle, 0, info, len(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            token += (struct.unpack_from('<q', info.raw, 24)[0],)
        finally:
            kernel.CloseHandle(handle)
    return token


def _file(path):
    p = Path(path).resolve(strict=True)
    st = p.stat()
    return _file_resolved(p, st)


def _file_resolved(p, st, *, verify_entry=None):
    """Hash a canonical regular file, optionally checking its bundle alias."""
    p = Path(p)
    if not stat.S_ISREG(st.st_mode):
        raise EnvironmentError(f'not a regular environment input: {p}')
    token = _file_token(p, st)

    def check_entry():
        if verify_entry is None:
            return
        entry, expected_link, entry_stat = verify_entry
        entry = Path(entry)
        if expected_link is None:
            current = entry.lstat()
            if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != (
                    entry_stat.st_dev, entry_stat.st_ino):
                raise EnvironmentError(f'Darwin bundle member changed while hashing: {entry}')
        elif (entry.resolve(strict=True) != p or os.readlink(entry) != expected_link):
            raise EnvironmentError(f'Darwin bundle link changed while hashing: {entry}')

    if os.name != 'nt':
        with _lock:
            old = _hashes.get(str(p))
            if old and old[0] == token:
                check_entry()
                return str(p), old[1]
    from ..cache.cache_state import file_digest
    try:
        content_hash = file_digest(p)
    except ValueError as error:
        # Keep environment failures in this resolver's fail-closed error domain.
        raise EnvironmentError(str(error)) from error
    after = p.stat()
    if token != _file_token(p, after):
        raise EnvironmentError(f'environment input changed while hashing: {p}')
    check_entry()
    if os.name != 'nt':
        with _lock:
            _hashes[str(p)] = token, content_hash
    return str(p), content_hash


def _files(paths):
    return dict(sorted(_file(p) for p in sorted(set(map(str, paths)))))


def _tree(root):
    """Include relative names as well as target hashes (resource lookup uses names)."""
    root = Path(root).resolve(strict=True)
    out = {}
    for base, dirs, names in os.walk(root, onerror=lambda e: _raise(e)):
        if any((Path(base) / d).is_symlink() for d in dirs):
            raise EnvironmentError(f'unknown runtime directory symlink closure: {base}')
        for name in names:
            p = Path(base) / name
            target, sha = _file(p)
            out[str(p.relative_to(root))] = [target, sha]
    if not out:
        raise EnvironmentError(f'empty runtime distribution: {root}')
    return out


def _windows_chrome_runtime(root):
    """Bind Chrome resources, excluding only its atomic installer histograms.

    Chromium ChromeMetricsServiceClient registers DIR_EXE/SetupMetrics as an
    atomic histogram directory. FileMetricsProvider consumes/deletes its PMAs
    (or deletes the directory when reporting is disabled); these are telemetry,
    not renderer resources. Unknown members remain bound; links and nested
    directories in this exceptional location are unsupported, not ignored.
    """
    root = Path(root).resolve(strict=True)
    out = {}

    def visit(directory, telemetry=False):
        try:
            entries = list(directory.iterdir())
        except FileNotFoundError:
            if telemetry:
                return  # Chrome may delete this exact telemetry directory.
            raise
        for p in entries:
            setup = directory == root and p.name.casefold() == 'setupmetrics'
            pma = telemetry and p.suffix == '.pma'
            try:
                info = p.lstat()
            except FileNotFoundError:
                if setup or pma:
                    continue
                raise
            reparse = bool(getattr(info, 'st_file_attributes', 0) & 0x400)
            if (setup or telemetry) and (p.is_symlink() or reparse):
                raise EnvironmentError('unsupported Chrome installer telemetry link')
            if stat.S_ISDIR(info.st_mode):
                if telemetry:
                    raise EnvironmentError('unsupported Chrome installer telemetry subdirectory')
                if p.is_symlink() or reparse:
                    raise EnvironmentError('unknown Windows runtime directory link')
                visit(p, setup)
            elif pma and stat.S_ISREG(info.st_mode):
                continue
            else:
                target, sha = _file(p)
                out[str(p.relative_to(root))] = [target, sha]
    visit(root)
    if not out:
        raise EnvironmentError(f'empty runtime distribution: {root}')
    return out


def _raise(error):
    raise error


def _libraries(binary):
    text = _command(['ldd', str(binary)])
    if 'not found' in text:
        raise EnvironmentError(f'unresolved renderer library: {text}')
    paths = re.findall(r'(?:=>\s+)?(/[^\s]+)\s+\(', text)
    if not paths:
        raise EnvironmentError(f'unknown dynamic library closure: {binary}')
    return _files(paths)


def _fonts():
    # All installed families, not just the three template fonts: arbitrary HTML
    # can consume any family, fallback glyph or fontconfig alias.
    names = _command(['fc-list', '--format=%{file}\n']).splitlines()
    if not names or any(not n.startswith('/') for n in names):
        raise EnvironmentError('unknown/empty fontconfig font inventory')
    configs = _command(['fc-conflist'])
    active = re.findall(r'^\+ (.*?): ', configs, re.M)
    if not active:
        raise EnvironmentError('unknown fontconfig active configuration')
    # fonts.conf can control matching without appearing in fc-conflist.
    config = os.environ.get('FONTCONFIG_FILE', '/etc/fonts/fonts.conf')
    if not Path(config).is_absolute():
        config = str(Path(os.environ.get('FONTCONFIG_PATH', '/etc/fonts')) / config)
    return {'files': _files(names), 'configs': _files(active + [config]),
            'inventory': configs, 'tools': _files([shutil.which('fc-list'),
                                                 shutil.which('fc-conflist')]),
            'libraries': _libraries(shutil.which('fc-list'))}


def _python_runtime():
    packages = {}
    for name in ('numpy', 'Pillow'):
        try:
            package = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = {'status': 'absent'}
            continue
        files = package.files
        if files is None:
            raise EnvironmentError(f'unknown installed renderer package inventory: {name}')
        paths = [package.locate_file(f) for f in files
                 if str(f).endswith(('.py', '.so', '.pyd', '.dll', '.dylib'))]
        if not paths:
            raise EnvironmentError(f'empty installed renderer package inventory: {name}')
        packages[name] = {'version': package.version, 'files': _files(paths)}
    return {'version': sys.version, 'executable': _files([sys.executable]), 'packages': packages}


def collect(backend, binary, mode=None):
    """Return auditable axes, refusing platforms without a complete resolver.

    Linux distribution files, host/boot boundary, system loader/font libraries,
    graphics driver files and all fontconfig-visible fonts are conservative key
    inputs. A host/boot boundary forbids portable-cache equality claims. Runtime
    observations remain separate evidence; no launch flag proves a GPU backend.
    """
    from .gfx_backend import cef_configuration, cef_launch_flags
    try:
        if platform.system() == 'Windows':
            return _collect_windows(backend, binary, mode)
        if platform.system() == 'Darwin':
            return _collect_darwin(backend, binary, mode)
        if platform.system() != 'Linux':
            raise EnvironmentError('gfx environment unknown: platform resolver requires Linux')
        executable = Path(shutil.which(str(binary)) or binary).resolve(strict=True)
        # Chrome's launcher is a shell script beside the actual ELF executable.
        native = executable.parent / 'chrome'
        if backend == 'cef' or not native.is_file():
            native = executable
        with native.open('rb') as stream:
            magic = stream.read(4)
        if magic != b'\x7fELF':
            raise EnvironmentError(f'gfx environment unknown: native ELF renderer required: {native}')
        libraries = _libraries(native)
        version = _command([str(native), '--sanity' if backend == 'cef' else '--version']).strip()
        if not version:
            raise EnvironmentError('gfx environment unknown: empty actual renderer version')
        roots = {native.parent}
        if backend == 'cef':
            cef = [Path(p) for p in libraries if Path(p).name == 'libcef.so']
            if len(cef) != 1:
                raise EnvironmentError('gfx environment unknown: CEF runtime libcef.so missing')
            roots.add(cef[0].parent)
        # Graphics libraries may be dlopen'ed, outside the ldd closure.
        graphics = []
        for root in (Path('/usr/lib64'), Path('/usr/lib/x86_64-linux-gnu')):
            if root.is_dir():
                for pattern in ('libGL*.so*', 'libEGL*.so*', 'libgbm.so*', 'libvulkan*.so*',
                                'libnvidia*.so*', 'dri/*.so', 'gbm/*.so', 'vdpau/*.so'):
                    graphics.extend(p for p in root.glob(pattern) if p.is_file())
        drivers = {str(p): p.read_text() for p in (
            Path('/proc/driver/nvidia/version'), Path('/sys/module/amdgpu/version')) if p.is_file()}
        overrides = {}
        for var in ('LD_LIBRARY_PATH', 'LIBGL_DRIVERS_PATH'):
            if os.environ.get(var):
                for path in os.environ[var].split(':'):
                    if not path or not Path(path).is_absolute():
                        raise EnvironmentError(f'unknown relative {var} search path')
                    overrides[path] = _tree(path)
        extra_files = []
        for var in ('LD_PRELOAD', 'VK_ICD_FILENAMES', 'VK_DRIVER_FILES'):
            if os.environ.get(var):
                for path in re.split(r'[:\s]+', os.environ[var]):
                    if not Path(path).is_absolute():
                        raise EnvironmentError(f'unknown relative {var} input')
                    extra_files.append(path)
        if Path('/etc/ld.so.preload').exists():
            # System preloads can name unresolved libraries outside ldd.
            raise EnvironmentError('unknown system loader preload; resolver refuses reuse')
        return {'schema': SCHEMA, 'status': 'input-bound',
                'pixel_equivalence': 'unproven',
                'backend': backend, 'mode': mode,
                'browser': _files([executable, native]), 'version': version,
                'runtime': {str(p): _tree(p) for p in sorted(roots)},
                'libraries': libraries, 'graphics_libraries': _files(graphics),
                'override_directories': overrides, 'override_files': _files(extra_files),
                'fonts': _fonts(), 'driver': drivers,
                'host_boot': _files(['/etc/machine-id', '/proc/sys/kernel/random/boot_id',
                                     '/etc/ld.so.cache']),
                'os': list(platform.uname()),
                'environment': {k: os.environ.get(k) for k in sorted(set(PIXEL_ENV) |
                                 {k for k in os.environ if k.startswith('LC_')})},
                'system_locale': _files(p for p in ('/etc/localtime', '/etc/locale.conf')
                                        if Path(p).exists()),
                'launch_flags': list(CHROME_VISUAL_FLAGS) if backend != 'cef' else
                    cef_launch_flags(mode),
                **({'cef_configuration': cef_configuration()} if backend == 'cef' else {}),
                'protocol': {'device_scale': 1, 'color': 'srgb',
                             'alpha': 'native-or-white-black-solve', 'class': 'full-html'},
                'python_runtime': _python_runtime(),
                'implementation': _files([_ROOT / p for p in (
                    'render/gfx_environment.py', 'render/gfx_backend.py', 'render/gfx_render.py', 'render/gfx_cli.py', 'render/browser_paths.py',
                    'render/gfx_cef.py', 'render/cef_client.py')])}
    except (OSError, RuntimeError, TypeError, AttributeError, plistlib.InvalidFileException,
            UnicodeError, struct.error) as e:
        raise EnvironmentError(f'gfx environment unknown; cache reuse refused: {e}') from e


def resolve(c):
    """Pin one environment to a context; recheck() never adopts new inputs."""
    from .gfx_backend import selected, cef_config, CEF_CONFIGURATION_ENV
    from ..media.encoders import find_chrome
    backend = selected()
    if backend == 'electron':
        return None  # frozen historical identity owned by gfx_backend
    selector = (backend, os.environ.get('VTX_CHROME'), os.environ.get('VTX_CEF_WORKER'),
                os.environ.get('VTX_CEF_MODE'), tuple(os.environ.get(k) for k in PIXEL_ENV))
    if backend == 'cef':
        selector += (tuple(os.environ.get(k) for k in CEF_CONFIGURATION_ENV),)
    if platform.system() == 'Darwin':
        selector += (tuple(sorted((k, v) for k, v in os.environ.items()
                     if k.startswith(('DYLD_', '__XPC_DYLD_', '__CF_')) or k == 'CFFIXED_USER_HOME')),)
    pinned = getattr(c, '_gfx_environment', None)
    if pinned and pinned[0] == selector:
        return pinned[1]
    binary, mode = None, None
    try:
        # Loading/validating a timeline remains possible without a renderer.
        # Unknown is diagnostic data, never a cache eligibility certificate.
        with contextlib.redirect_stdout(io.StringIO()):
            binary, mode = cef_config() if backend == 'cef' else (find_chrome(), None)
        record = collect(backend, binary, mode)
    except (EnvironmentError, OSError, SystemExit) as error:
        record = {'schema': SCHEMA, 'status': 'unknown', 'backend': backend,
                  'selector': selector, 'error': str(error), 'pixel_equivalence': 'unproven'}
    c._gfx_environment = (selector, record, binary, mode)
    return record


def _changed_inputs(before, after):
    """Bounded diagnostic labels only; never include input values or host paths.

    Runtime member names are relative to their distribution. Other axes can
    contain user preferences, environment values and absolute paths, so report
    only their fixed top-level label.
    """
    axes = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    labels = []
    for axis in axes:
        if axis == 'host_boot':
            # Known resolver field names are safe; Linux keys are host paths.
            old, new = before.get(axis, {}), after.get(axis, {})
            fields = ('machine_guid', 'boot_filetime', 'boot_session_uuid')
            changed = [key for key in fields if old.get(key) != new.get(key)]
            labels.extend('host_boot/' + key for key in changed)
            if {k: v for k, v in old.items() if k not in fields} != {
                    k: v for k, v in new.items() if k not in fields} or not changed:
                labels.append(axis)
            continue
        if axis != 'runtime':
            labels.append(axis)
            continue
        old, new = before.get(axis, {}), after.get(axis, {})
        for root in sorted(set(old) | set(new)):
            a, b = old.get(root, {}), new.get(root, {})
            for member in sorted(set(a) | set(b)):
                if a.get(member) != b.get(member):
                    # Both supported runtime layouts use relative member keys.
                    # Reject absolute names rather than leaking a host path.
                    if Path(member).is_absolute() or re.match(r'^[A-Za-z]:', member):
                        labels.append('runtime')
                    else:
                        labels.append('runtime/' + member)
        if not any(label.startswith('runtime') for label in labels):
            labels.append('runtime')
    return json.dumps(labels[:20] + (['...'] if len(labels) > 20 else []), ensure_ascii=True)


def recheck(c):
    pinned = getattr(c, '_gfx_environment', None)
    if pinned is None:
        return
    if pinned[1]['status'] != 'input-bound':
        return  # metadata-only contexts; certificate() refuses this path
    current = resolve(c)
    if current == pinned[1]:
        current = collect(pinned[1]['backend'], pinned[2], pinned[3])
    if current != pinned[1]:
        raise EnvironmentError('gfx environment changed during invocation; changed inputs: '
                               + _changed_inputs(pinned[1], current)
                               + '; restart with current inputs')


def identity(c):
    record = resolve(c)
    return '' if record is None else SCHEMA + '|' + digest(record)


def certificate(c):
    record = resolve(c)
    if record is not None and record.get('status') != 'input-bound':
        raise EnvironmentError('gfx environment unknown; rendering/cache reuse refused: '
                               + record.get('error', 'incomplete resolver'))
    if record is None:
        return None
    axes = dict(record, viewport={'width': c.w, 'height': c.h}, fps=c.fps)
    return {'schema': SCHEMA, 'sha256': digest(axes), 'status': record['status'], 'axes': axes}


def verify_certificate(record):
    """Offline wire validation for #62. Input-bound never means pixel-exact."""
    if (not isinstance(record, dict) or record.get('schema') != SCHEMA
            or record.get('status') != 'input-bound'
            or not isinstance(record.get('axes'), dict)
            or record['axes'].get('status') != 'input-bound'
            or record['axes'].get('schema') != SCHEMA
            or record.get('sha256') != digest(record['axes'])):
        raise EnvironmentError('unknown/mismatched gfx input environment certificate')
    axes = record['axes']
    if axes.get('platform_resolver') in ('darwin-arm64-cdp/1', 'darwin-arm64-cef/1'):
        cache = axes.get('system_cache')
        host = axes.get('host_boot')
        architecture = axes.get('architecture')
        if (axes.get('platform_resolver') != 'darwin-arm64-' + str(axes.get('backend')) + '/1'
                or axes.get('backend') not in ('cdp', 'cef') or not isinstance(cache, dict)
                or not isinstance(cache.get('uuid'), str)
                or not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', cache['uuid'])
                or not isinstance(cache.get('files'), dict) or not cache['files']
                or cache.get('files') != axes.get('libraries')
                or not isinstance(host, dict) or not host.get('boot_session_uuid')
                or not isinstance(architecture, list) or 0x100000c not in architecture
                or not isinstance(axes.get('dependencies'), dict) or not axes['dependencies']):
            raise EnvironmentError('incomplete Darwin gfx input environment certificate')
    if axes.get('platform_resolver') == 'windows-x64/1':
        host = axes.get('host_boot')
        if (axes.get('backend') not in ('cdp', 'cli') or axes.get('mode') is not None
                or not isinstance(host, dict) or not host.get('machine_guid')
                or not str(host.get('boot_filetime', '')).isdigit()
                or not isinstance(axes.get('registry'), dict) or not axes['registry']
                or not isinstance(axes.get('graphics_libraries'), dict) or not axes['graphics_libraries']):
            raise EnvironmentError('incomplete Windows gfx input environment certificate')
    if (axes.get('backend') not in ('cdp', 'cli', 'cef')
            or axes.get('pixel_equivalence') != 'unproven'
            or not isinstance(axes.get('fonts'), dict)
            or any(not isinstance(axes.get(field), dict) or not axes[field]
                   for field in ('browser', 'runtime', 'libraries', 'host_boot', 'implementation'))
            or any(not isinstance(axes['fonts'].get(field), dict) or not axes['fonts'][field]
                   for field in ('files', 'configs', 'tools', 'libraries'))
            or not axes.get('version')):
        raise EnvironmentError('incomplete gfx input environment certificate')
    return record['sha256']
