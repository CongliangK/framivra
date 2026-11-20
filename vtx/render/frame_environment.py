"""Read-only environment identity for the single-frame ASS compositor.

Darwin deliberately supports only native arm64 and a sole CoreText libass
provider. In particular, never initialize fontconfig or render a provider probe:
those operations can populate font caches during otherwise zero-write preflight.
The browser's app-contained dependency resolver cannot describe Homebrew FFmpeg.
"""
import ctypes
import os
from pathlib import Path
import platform
import sys

from . import gfx_environment as env


def _dependencies(binary, contains):
    """Resolve external Mach-O images with each loader's inherited rpath stack.

    First-existing rpath wins, as in dyld. Recollection rechecks even earlier
    missing candidates. Conflicting resolutions of one image fail closed rather
    than guessing dyld's first-loaded-image ordering.
    """
    executable = Path(binary).resolve(strict=True)
    files, dependencies, resolutions, seen = {}, {}, {}, set()
    pending = [(executable, ())]
    while pending:
        image, inherited = pending.pop()
        image = image.resolve(strict=True)
        state = (str(image), inherited)
        if state in seen:
            continue
        seen.add(state)
        if len(seen) > 4096:
            raise env.EnvironmentError('Darwin dependency context limit exceeded')
        env._darwin_native(image)
        commands = env._darwin_load_commands(image)
        if commands is None:
            raise env.EnvironmentError('unknown native Darwin dependency')
        names, rpaths, kind = commands
        if kind not in (2, 6, 8) or (image == executable and kind != 2):
            raise env.EnvironmentError('unsupported Darwin executable/library type')
        files[str(image)] = env._file(image)[1]

        def expand(value):
            for prefix, directory in (('@loader_path', image.parent),
                                      ('@executable_path', executable.parent)):
                if value == prefix or value.startswith(prefix + '/'):
                    return directory / value[len(prefix):].lstrip('/')
            if value.startswith('/'):
                return Path(value)
            raise env.EnvironmentError('unresolved Darwin dependency path: ' + value)

        search = tuple(dict.fromkeys([str(expand(p).resolve()) for p in rpaths] + list(inherited)))
        resolved = {}
        for name in names:
            if name.startswith(('/usr/lib/', '/System/Library/')) and contains(os.fsencode(name)):
                resolved[name] = 'system-cache'
                continue
            choices = ([Path(p) / name[len('@rpath/'):] for p in search]
                       if name.startswith('@rpath/') else [expand(name)])
            match = next((p.resolve(strict=True) for p in choices if p.is_file()), None)
            if match is None:
                raise env.EnvironmentError('unresolved Darwin dependency: ' + name)
            resolved[name] = str(match)
            pending.append((match, search))
        if str(image) in resolutions and resolutions[str(image)] != resolved:
            raise env.EnvironmentError('ambiguous Darwin loader context: ' + str(image))
        resolutions[str(image)] = resolved
        dependencies[str(image)] = {'loads': resolved, 'rpaths': list(search)}
    return {'files': dict(sorted(files.items())), 'dependencies': dict(sorted(dependencies.items()))}


def _coretext_provider(libraries):
    """Query libass's compiled provider list; do NOT initialize any provider.

    ass_library_init allocates the library handle; the provider-list API only
    returns the compiled enum list. No renderer, ass_set_fonts, FcInit or cache
    update is involved. Requiring one provider avoids autodetect-order guesses.
    """
    paths = [p for p in libraries['files'] if Path(p).name.startswith('libass.')
             and Path(p).suffix == '.dylib']
    if len(paths) != 1:
        raise env.EnvironmentError('one dynamically linked libass is required')
    lib = ctypes.CDLL(paths[0])
    if any(not hasattr(lib, name) for name in ('ass_library_init', 'ass_library_done',
                                               'ass_get_available_font_providers')):
        raise env.EnvironmentError('unknown libass font provider API')
    lib.ass_library_init.argtypes, lib.ass_library_init.restype = [], ctypes.c_void_p
    lib.ass_library_done.argtypes, lib.ass_library_done.restype = [ctypes.c_void_p], None
    pointer = ctypes.POINTER(ctypes.c_int)()
    size = ctypes.c_size_t()
    lib.ass_get_available_font_providers.argtypes = [ctypes.c_void_p,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_int)), ctypes.POINTER(ctypes.c_size_t)]
    lib.ass_get_available_font_providers.restype = None
    handle = lib.ass_library_init()
    if not handle:
        raise env.EnvironmentError('libass library allocation failed')
    try:
        lib.ass_get_available_font_providers(handle, ctypes.byref(pointer), ctypes.byref(size))
        # libass includes NONE and AUTODETECT in its public available list.
        if size.value > 16 or not pointer:
            raise env.EnvironmentError('unknown libass font provider inventory')
        providers = [pointer[i] for i in range(size.value)]
    finally:
        if pointer:
            free = ctypes.CDLL(None).free
            free.argtypes, free.restype = [ctypes.c_void_p], None
            free(pointer)
        lib.ass_library_done(handle)
    if providers != [0, 1, 2]:
        raise env.EnvironmentError('Darwin frame requires sole CoreText libass provider')
    return {'library': env._file(paths[0]), 'available': providers, 'selected': 'coretext'}


def _darwin(binary):
    if platform.machine() != 'arm64':
        raise env.EnvironmentError('Darwin frame requires native arm64')
    if (any(v for k, v in os.environ.items() if k.startswith(('DYLD_', '__XPC_DYLD_')))
            or any(os.environ.get(k) for k in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'LD_AUDIT',
                'CFFIXED_USER_HOME', 'FONTCONFIG_FILE', 'FONTCONFIG_PATH', 'FONTCONFIG_SYSROOT'))):
        raise env.EnvironmentError('unsupported Darwin frame environment override')
    executable = Path(binary).resolve(strict=True)
    if any(p.suffix.lower() == '.app' for p in (*Path(binary).absolute().parents, *executable.parents)):
        raise env.EnvironmentError('Darwin frame app-wrapped executables are unsupported')
    # A command-line tool has no supported bundle identifier. Bind both the
    # invocation and canonical process names if a renamed symlink is used;
    # do not guess application domains from arbitrary app metadata.
    application_domains = sorted({Path(binary).name, executable.name})
    cache_path, cache_uuid = env._darwin_dyld()
    lib = ctypes.CDLL(None)
    contains = lib._dyld_shared_cache_contains_path
    contains.argtypes, contains.restype = [ctypes.c_char_p], ctypes.c_bool
    libraries = _dependencies(binary, contains)
    provider = _coretext_provider(libraries)
    cache = env._darwin_cache(cache_path, cache_uuid)
    fonts = env._darwin_fonts(cache['files'], application_domains)
    fonts['provider'] = provider
    return {'ffmpeg': env._file(binary), 'libraries': libraries, 'system_cache': cache,
            'fonts': fonts, 'platform': sys.platform, 'os': list(platform.uname()),
            'system': env._files(['/System/Library/CoreServices/SystemVersion.plist', '/usr/lib/dyld'])}


def collect(binary, *, command=None):
    """Keep the existing Linux dependency/font collector behavior intact."""
    from ..pipeline.infra import filter_threads
    if sys.platform == 'darwin':
        result = _darwin(binary)
    else:
        options = {} if command is None else {'command': command}
        result = {'ffmpeg': env._file(binary), 'libraries': env._libraries(binary, **options),
                  'fonts': env._fonts(**options), 'platform': sys.platform}
    keys = set(env.PIXEL_ENV)
    if sys.platform == 'darwin':
        keys.update(k for k in os.environ if k.startswith(('LC_', '__CF_')))
        keys.update(('PATH', 'CFFIXED_USER_HOME'))
    result.update(filter_threads=filter_threads(),
        implementation=env._files([__file__, env.__file__]),
        environment={k: os.environ.get(k) for k in sorted(keys)
                     if k not in ('DISPLAY', 'WAYLAND_DISPLAY')})
    return result
