"""Conservative, host-scoped gfx input identity (#48).

This binds inputs, not cross-machine pixel equivalence. In particular a matching
manifest must never promote environment_identity to passed in Golden evidence.
Unknown/unreadable inputs fail closed. Collection starts no browser and writes
nothing (including on dry runs). Digests are memoized by inode/size/mtime/ctime;
membership and metadata are re-read at every recheck.
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

SCHEMA = 'vtx.gfx-environment/1'
CHROME_VISUAL_FLAGS = ('--headless', '--disable-gpu', '--hide-scrollbars',
                       '--force-device-scale-factor=1', '--force-color-profile=srgb',
                       '--font-render-hinting=none')
_lock = threading.RLock()
_hashes = {}
_ROOT = Path(__file__).resolve().parent
# PNG compression and scheduling are deliberately provenance only.
PIXEL_ENV = ('LANG', 'LANGUAGE', 'LC_ALL', 'LC_CTYPE', 'LC_NUMERIC', 'TZ',
             'FONTCONFIG_FILE', 'FONTCONFIG_PATH', 'FONTCONFIG_SYSROOT',
             'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_DATA_DIRS', 'XDG_CACHE_HOME', 'HOME',
             'DISPLAY', 'WAYLAND_DISPLAY', 'XDG_SESSION_TYPE',
             'LD_LIBRARY_PATH', 'LD_PRELOAD', 'LIBGL_DRIVERS_PATH',
             'LIBGL_ALWAYS_SOFTWARE', 'MESA_LOADER_DRIVER_OVERRIDE',
             'GALLIUM_DRIVER', '__GLX_VENDOR_LIBRARY_NAME',
             'VK_ICD_FILENAMES', 'VK_DRIVER_FILES', 'DRI_PRIME',
             'VTX_GFX_NAVFREE', 'VTX_GFX_STATIC')


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


def _file(path):
    p = Path(path).resolve(strict=True)
    st = p.stat()
    if not p.is_file():
        raise EnvironmentError(f'not a regular environment input: {p}')
    token = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    with _lock:
        old = _hashes.get(str(p))
        if old and old[0] == token:
            return str(p), old[1]
    h = hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    after = p.stat()
    if token != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise EnvironmentError(f'environment input changed while hashing: {p}')
    with _lock:
        _hashes[str(p)] = token, h.hexdigest()
    return str(p), h.hexdigest()


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
    try:
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
                    ['mode=' + str(mode), 'sync-mode=n', 'sync-canary=exact'],
                'protocol': {'device_scale': 1, 'color': 'srgb',
                             'alpha': 'native-or-white-black-solve', 'class': 'full-html'},
                'python_runtime': _python_runtime(),
                'implementation': _files([_ROOT / p for p in (
                    'gfx_environment.py', 'gfx_backend.py', 'gfx_render.py', 'gfx_cli.py',
                    'gfx_cef.py', 'cef_client.py')])}
    except (OSError, RuntimeError, TypeError) as e:
        raise EnvironmentError(f'gfx environment unknown; cache reuse refused: {e}') from e


def resolve(c):
    """Pin one environment to a context; recheck() never adopts new inputs."""
    from .gfx_backend import selected, cef_config
    from .encoders import find_chrome
    backend = selected()
    if backend == 'electron':
        return None  # frozen historical identity owned by gfx_backend
    selector = (backend, os.environ.get('VTX_CHROME'), os.environ.get('VTX_CEF_WORKER'),
                os.environ.get('VTX_CEF_MODE'), tuple(os.environ.get(k) for k in PIXEL_ENV))
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


def recheck(c):
    pinned = getattr(c, '_gfx_environment', None)
    if pinned is None:
        return
    if pinned[1]['status'] != 'input-bound':
        return  # metadata-only contexts; certificate() refuses this path
    if resolve(c) != pinned[1] or collect(pinned[1]['backend'], pinned[2], pinned[3]) != pinned[1]:
        raise EnvironmentError('gfx environment changed during invocation; restart with current inputs')


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
