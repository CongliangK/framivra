"""Explicit, static SVG-sequence input and isolated Inkscape cache contract.

This is not HTML dependency evidence. Generators are hashed, never executed.
Fonts are explicit files, used in a process-local isolated fontconfig directory.
"""
import contextlib
import hashlib
import io
import math
import os
import stat
import sys
import ctypes
import platform
import plistlib
import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

FORMAT = 'framivra-svg-sequence/v1'
NAMESPACE = 'svg_inkscape_v1'
COMPLETE_FILE = 'svg-complete.json'
PRODUCER = 'framivra-svg-inkscape/v1'
MAX_SVG_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_SVG_NODES = 20000
MAX_TEXT_CHARS = 200000
MAX_FRAMES = 18000
SVG_NS = 'http://www.w3.org/2000/svg'
_COMMON = {'id', 'transform', 'opacity', 'fill', 'fill-opacity', 'stroke',
           'stroke-width', 'stroke-opacity', 'stroke-linecap', 'stroke-linejoin',
           'stroke-dasharray', 'clip-path', 'data-role'}
_ATTRIBUTES = {
    'svg': {'width', 'height', 'viewBox', 'role'}, 'g': set(), 'defs': set(),
    'title': set(), 'desc': set(), 'clipPath': {'clipPathUnits'},
    'rect': {'x', 'y', 'width', 'height', 'rx', 'ry'},
    'circle': {'cx', 'cy', 'r'}, 'line': {'x1', 'y1', 'x2', 'y2'}, 'path': {'d'},
    'text': {'x', 'y', 'font-family', 'font-size', 'font-weight', 'text-anchor'},
}
_NUMBER = r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
_NUMBER_RE = re.compile(_NUMBER)
_ID = r'[A-Za-z_][A-Za-z0-9_.-]{0,127}'



def is_svg(clip):
    return clip.get('format', 'html') == 'svg-sequence'


def route(c):
    formats = set()
    for track in c.tl.get('video_tracks', []):
        for clip in track.get('clips', []):
            value = clip.get('format', 'html')
            if track.get('kind') != 'gfx':
                if 'format' in clip:
                    raise ValueError('clip.format is only supported on gfx tracks')
                continue
            if value not in ('html', 'svg-sequence'):
                raise ValueError(f'unsupported gfx format: {value!r}')
            formats.add(value)
    if len(formats) > 1:
        raise ValueError('mixed HTML/SVG gfx timelines are unsupported')
    if formats == {'svg-sequence'}:
        if c.tl.get('project', {}).get('gfx_dependency_policy', 'legacy') != 'legacy':
            raise ValueError('HTML strict dependency policy is unsupported for SVG-sequence')
        return 'svg-sequence'
    return 'html'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def input_path(c, name):
    if not isinstance(name, str) or not name or any(x in name for x in ('\0', '\\')):
        raise ValueError('SVG inputs require nonempty project-relative paths')
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('SVG inputs require project-relative paths without parent traversal')
    root = Path(c.root).resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError('SVG input must resolve inside the project root')
    return path


def read_input(c, name, *, limit=None):
    """Bound the read itself, rather than allocating first and checking later."""
    limit = MAX_INPUT_BYTES if limit is None else limit
    if limit < 0:
        raise ValueError('SVG input exceeds byte budget')
    path = input_path(c, name)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('SVG input must be a regular file')
        if info.st_size > limit:
            raise ValueError('SVG input exceeds byte budget')
        blocks, total = [], 0
        while True:
            block = os.read(fd, min(1024 * 1024, limit - total + 1))
            if not block:
                return b''.join(blocks)
            total += len(block)
            if total > limit:
                raise ValueError('SVG input exceeds byte budget')
            blocks.append(block)
    finally:
        os.close(fd)


def _number(value, *, minimum=-1000000, maximum=1000000):
    if not _NUMBER_RE.fullmatch(value):
        raise ValueError('SVG numeric attribute must be a finite unitless number')
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError('SVG numeric attribute outside supported range')
    return number


def _numbers(value):
    pieces = re.split(r'[\s,]+', value.strip())
    return [_number(piece) for piece in pieces]


def _transform(value):
    position = 0
    for match in re.finditer(r'(translate|scale)\s*\(([^()]*)\)', value):
        if value[position:match.start()].strip():
            raise ValueError('unsupported SVG transform')
        values = _numbers(match.group(2))
        if len(values) not in (1, 2):
            raise ValueError('SVG translate/scale requires one or two numbers')
        position = match.end()
    if not position or value[position:].strip():
        raise ValueError('unsupported SVG transform')


def _path_data(value):
    # Current producer needs straight, closed shapes only. Curves/arcs are not
    # claimed until they have a concrete consumer and validation contract.
    tokens = re.findall(r'[MmLlZz]|' + _NUMBER, value)
    if re.sub(r'[MmLlZz]|' + _NUMBER + r'|[\s,]+', '', value) or not tokens or tokens[0] not in ('M', 'm'):
        raise ValueError('SVG path supports only finite M/L/Z geometry')
    index = 0
    while index < len(tokens):
        command = tokens[index]; index += 1
        if command not in 'MmLlZz':
            raise ValueError('invalid SVG path command')
        values = []
        while index < len(tokens) and tokens[index] not in 'MmLlZz':
            values.append(_number(tokens[index])); index += 1
        if (command in 'Zz' and values) or (command in 'MmLl' and (not values or len(values) % 2)):
            raise ValueError('invalid SVG path coordinate count')


def _attribute(tag, attr, value, *, families):
    if attr not in _COMMON | _ATTRIBUTES[tag]:
        raise ValueError(f'unsupported SVG attribute: {tag}.{attr}')
    if attr in ('id', 'data-role'):
        valid = bool(re.fullmatch(_ID, value))
    elif attr == 'transform':
        _transform(value); return
    elif attr == 'd':
        _path_data(value); return
    elif attr == 'viewBox':
        valid = len(_numbers(value)) == 4
    elif attr in ('x', 'y', 'cx', 'cy', 'x1', 'y1', 'x2', 'y2'):
        _number(value); return
    elif attr in ('width', 'height', 'rx', 'ry', 'r', 'stroke-width', 'font-size'):
        if tag == 'svg' and value.endswith('px'):
            value = value[:-2]
        _number(value, minimum=0); return
    elif attr in ('opacity', 'fill-opacity', 'stroke-opacity'):
        _number(value, minimum=0, maximum=1); return
    elif attr in ('fill', 'stroke'):
        valid = value in {'none', 'black', 'white', 'red', 'green', 'blue', 'transparent'} or bool(re.fullmatch(r'#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?', value))
    elif attr == 'stroke-dasharray':
        values = _numbers(value)
        valid = 1 <= len(values) <= 128 and all(v >= 0 for v in values) and any(values)
    elif attr == 'clip-path':
        valid = bool(re.fullmatch(r'url\(#' + _ID + r'\)', value))
    elif attr == 'font-family':
        valid = value in families
    else:
        choices = {'stroke-linecap': {'butt', 'round', 'square'},
                   'stroke-linejoin': {'miter', 'round', 'bevel'},
                   'font-weight': {'normal', 'bold', *map(str, range(100, 901, 100))},
                   'text-anchor': {'start', 'middle', 'end'},
                   'clipPathUnits': {'userSpaceOnUse'}, 'role': {'img'}}
        valid = value in choices[attr]
    if not valid:
        raise ValueError(f'unsupported SVG attribute value: {tag}.{attr}')


def inspect_svg(data, width, height, *, fonts, families=()):
    if len(data) > MAX_SVG_BYTES:
        raise ValueError('SVG frame exceeds byte budget')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError('SVG frames must use UTF-8') from error
    declaration = re.match(r'<\?xml\s[^?]*\?>', text.lstrip('\ufeff'))
    encoding = re.search(r'''\bencoding\s*=\s*["']([^"']+)["']''', declaration.group(0), re.I) if declaration else None
    if '\0' in text or (encoding and encoding.group(1).lower() != 'utf-8'):
        raise ValueError('SVG frames must use UTF-8 XML encoding')
    if re.search(br'<\?(?!xml\s)', data, re.I):
        raise ValueError('SVG processing instructions are unsupported')
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', data, re.I):
        raise ValueError('SVG document types/entities are unsupported')
    try:
        parser = ET.iterparse(io.BytesIO(data), events=('start', 'start-ns'))
        nodes = []
        for event, item in parser:
            if event == 'start-ns':
                if item[1] != SVG_NS:
                    raise ValueError('unsupported SVG namespace declaration')
            else:
                nodes.append(item)
                if len(nodes) > MAX_SVG_NODES:
                    raise ValueError('SVG node budget exceeded')
        root = parser.root
    except ET.ParseError as error:
        raise ValueError(f'invalid SVG XML: {error}') from error
    if root.tag != '{' + SVG_NS + '}svg':
        raise ValueError('SVG root must use the SVG namespace')
    if _numbers(root.attrib.get('viewBox', '')) != [0, 0, width, height]:
        raise ValueError('SVG viewBox must exactly match the manifest canvas')
    for dimension, expected in [('width', width), ('height', height)]:
        if root.get(dimension) not in (str(expected), f'{expected}px'):
            raise ValueError('SVG width/height must exactly match the manifest canvas')
    if sum(len(e.text or '') + len(e.tail or '') for e in nodes) > MAX_TEXT_CHARS:
        raise ValueError('SVG text budget exceeded')
    for element in nodes:
        if not element.tag.startswith('{' + SVG_NS + '}'):
            raise ValueError('SVG elements must use the SVG namespace')
        name = element.tag[len(SVG_NS) + 2:]
        if name not in _ATTRIBUTES:
            raise ValueError(f'unsupported static SVG element: {name}')
        if name == 'svg' and element is not root:
            raise ValueError('nested SVG canvases are unsupported')
        if name == 'text' and not fonts:
            raise ValueError('SVG text requires explicit manifest.fonts files')
        if name == 'text' and 'font-family' not in element.attrib:
            raise ValueError('SVG text requires an explicit declared font-family')
        for attr, value in element.attrib.items():
            _attribute(name, attr, value, families=families)


# Explicit environment for both introspection and raster subprocesses. No
# inherited locale, loader, font, GTK, or Pango overrides can affect pixels.
_PROCESS_ENV = {'PATH': os.defpath, 'LANG': 'C', 'LC_ALL': 'C',
                'LANGUAGE': 'en', 'TZ': 'UTC', 'GIO_USE_VFS': 'local',
                'GIO_USE_PROXY_RESOLVER': 'dummy', 'NO_AT_BRIDGE': '1',
                'OMP_NUM_THREADS': '1', 'PANGOCAIRO_BACKEND': 'fc'}


def process_environment(directory=None):
    env = dict(_PROCESS_ENV)
    if directory is not None:
        directory = Path(directory)
        env.update(HOME=str(directory / 'home'),
                   XDG_CONFIG_HOME=str(directory / 'config'),
                   XDG_CACHE_HOME=str(directory / 'cache'),
                   XDG_DATA_HOME=str(directory / 'data'),
                   INKSCAPE_PROFILE_DIR=str(directory / 'inkscape-profile'),
                   FONTCONFIG_FILE=str(directory / 'fonts.conf'),
                   FONTCONFIG_PATH=str(directory))
    return env


def private_configuration(directory, *, root=None):
    """Configure only declared fonts, including Inkscape's extra font folders."""
    from . import fstxn
    directory = Path(directory)
    for name in ('home', 'config', 'cache', 'data', 'inkscape-profile', 'fonts', 'font-cache'):
        fstxn.mkdir(directory / name, root=root)
    fstxn.atomic_write(directory / 'fonts.conf', '<?xml version="1.0"?><fontconfig><dir>' +
                       escape(str(directory / 'fonts')) + '</dir><cachedir>' +
                       escape(str(directory / 'font-cache')) + '</cachedir></fontconfig>', root=root)
    # Inkscape otherwise adds its distribution/user font dirs on top of FcConfig.
    fstxn.atomic_write(directory / 'inkscape-profile/preferences.xml',
                      '<inkscape version="1"><group id="options"><group id="font" '
                      'use_fontsdir_system="0" use_fontsdir_user="0" '
                      'custom_fontdirs=""/></group></inkscape>', root=root)


def runtime_libraries(executable):
    """Actual platform dependency closure and loader; never an OS version seal."""
    from .cache_state import file_digest
    if sys.platform == 'darwin':
        return _darwin_runtime(executable)
    if sys.platform != 'linux':
        raise ValueError('SVG-sequence runtime identity supports Linux ELF and macOS arm64 only')
    ldd = shutil.which('ldd', path=os.defpath)
    if ldd is None:
        raise ValueError('SVG-sequence runtime identity requires system ldd')
    result = subprocess.run([ldd, executable], capture_output=True, text=True,
                            timeout=15, env=process_environment())
    if result.returncode:
        raise ValueError('unable to identify SVG runtime shared libraries')
    libraries = {}
    loader_found = False
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith('linux-vdso.so.'):
            continue  # Kernel virtual mapping, not a graphics library file.
        match = re.fullmatch(r'([^ ]+) => (/.+) \(0x[0-9a-fA-F]+\)', line)
        if match:
            name, path = match.groups()
        else:
            match = re.fullmatch(r'(/.+) \(0x[0-9a-fA-F]+\)', line)
            if not match:
                raise ValueError('unresolved SVG runtime library dependency')
            path = match.group(1)
            name = Path(path).name
            loader_found = True
        path = str(Path(path).resolve())
        if name in libraries:
            raise ValueError('ambiguous SVG runtime library dependency')
        libraries[name] = {'path': path, 'sha256': file_digest(path)}
    if not loader_found or not libraries:
        raise ValueError('SVG runtime dynamic loader was not identified')
    return dict(sorted(libraries.items()))


def _darwin_runtime(executable):
    """Bind native dependencies and actual dyld cache, including dlopen resources.

    Inkscape requires a self-contained app. The standalone fontconfig inspector
    may use an external installed library closure (e.g. Homebrew). Unknown or
    ambiguous runpaths fail closed instead of substituting an OS version seal.
    """
    from . import gfx_environment as ge
    if platform.machine() != 'arm64':
        raise ValueError('SVG-sequence macOS requires native arm64')
    executable = Path(executable).resolve(strict=True)
    ge._darwin_native(executable)
    bundle = next((p for p in executable.parents if p.suffix == '.app'), None)
    distribution = ge._darwin_bundle(bundle) if bundle else None
    lib = ctypes.CDLL(None)
    contains = lib._dyld_shared_cache_contains_path
    contains.argtypes, contains.restype = [ctypes.c_char_p], ctypes.c_bool
    files, dependencies, visited = {}, {}, set()

    def expand(value, loader, entry):
        for prefix, base in (('@loader_path', loader.parent), ('@executable_path', entry.parent)):
            if value == prefix or value.startswith(prefix + '/'):
                return base / value[len(prefix):].lstrip('/')
        if value.startswith('/'):
            return Path(value)
        raise ValueError(f'unresolved SVG Darwin library path: {value}')

    def walk(path, entry, inherited=()):
        path = path.resolve(strict=True)
        commands = ge._darwin_load_commands(path)
        if commands is None:
            raise ValueError(f'non-native SVG Darwin dependency: {path}')
        ge._darwin_native(path)
        deps, rpaths, filetype = commands
        if filetype == 2:
            entry, inherited = path, ()
        search = tuple(dict.fromkeys([str(expand(r, path, entry).resolve()) for r in rpaths] + list(inherited)))
        state = (str(path), str(entry), search)
        if state in visited:
            return
        if len(visited) >= 4096:
            raise ValueError('SVG Darwin dependency inventory exceeds budget')
        visited.add(state)
        files[str(path)] = ge._file(path)[1]
        resolved = {}
        for dep in deps:
            if dep.startswith(('/usr/lib/', '/System/Library/')) and contains(os.fsencode(dep)):
                resolved[dep] = 'system-cache'
                continue
            choices = ([Path(r) / dep[len('@rpath/'):] for r in search]
                       if dep.startswith('@rpath/') else [expand(dep, path, entry)])
            matches = {p.resolve(strict=True) for p in choices if p.is_file()}
            if len(matches) != 1:
                raise ValueError(f'unresolved/ambiguous SVG Darwin dependency: {path}: {dep}')
            target = matches.pop()
            if bundle and not target.is_relative_to(bundle):
                raise ValueError(f'external SVG Darwin app dependency: {target}')
            resolved[dep] = str(target)
            walk(target, entry, search)
        previous = dependencies.get(str(path))
        if previous is not None and previous != resolved:
            raise ValueError(f'context-dependent SVG Darwin dependency: {path}')
        dependencies[str(path)] = resolved

    walk(executable, executable)
    if distribution:
        # Modules loaded by GIO/Pango/GTK need identity even when absent from
        # LC_LOAD_DYLIB. Python extensions/standalone tools are hashed as app
        # resources; static SVG PNG export does not execute those programs.
        commands = ge._darwin_load_commands(executable)
        inherited = tuple(str(expand(r, executable, executable).resolve()) for r in commands[1])
        module_roots = tuple(bundle / 'Contents/Resources/lib' / name for name in
                             ('gio/modules', 'gdk-pixbuf-2.0', 'gtk-3.0', 'pango'))
        for value in distribution['files'].values():
            path = Path(value[0])
            if (any(path.is_relative_to(root) for root in module_roots)
                    and ge._darwin_load_commands(path) is not None):
                walk(path, executable, inherited)
    return {'resolver': 'darwin-arm64-svg/1', 'files': dict(sorted(files.items())),
            'dependencies': dict(sorted(dependencies.items())),
            'distribution': distribution, 'system_cache': ge._darwin_cache(*ge._darwin_dyld()),
            'loader': {'path': '/usr/lib/dyld', 'sha256': ge._file('/usr/lib/dyld')[1]}}


def inkscape_executable():
    """Explicit override, PATH, then macOS app; never execute a launcher shim."""
    name = os.environ.get('VTX_INKSCAPE') or shutil.which('inkscape')
    if not name and sys.platform == 'darwin':
        for root in (Path('/Applications'), Path.home() / 'Applications'):
            candidate = root / 'Inkscape.app/Contents/MacOS/inkscape'
            if candidate.is_file():
                name = str(candidate)
                break
    if not name:
        raise ValueError('SVG-sequence requires installed Inkscape (PATH or VTX_INKSCAPE)')
    executable = Path(name).resolve(strict=True)
    if sys.platform == 'darwin':
        from .gfx_environment import _darwin_native
        _darwin_native(executable)
        if executable.parent.name != 'MacOS' or executable.parent.parent.name != 'Contents':
            raise ValueError('SVG macOS requires a real Inkscape app Mach-O executable; launcher scripts unsupported')
        bundle = executable.parent.parent.parent
        info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
        if (bundle.suffix != '.app' or info.get('CFBundleIdentifier') != 'org.inkscape.Inkscape'
                or info.get('CFBundleExecutable') != executable.name
                or not (bundle / 'Contents/Resources/share/inkscape').is_dir()):
            raise ValueError('SVG macOS requires the self-contained Inkscape app distribution')
    return str(executable)


def _inspect_command(argv):
    # macOS Inkscape rewrites XDG paths beneath HOME. Introspection must never
    # see the user's profile or create state there, even on a dry-run load.
    with tempfile.TemporaryDirectory(prefix='vtx-svg-inspect-') as private:
        private_configuration(private)
        return subprocess.run(argv, capture_output=True, text=True, timeout=15,
                              env=process_environment(private))


@lru_cache(maxsize=8)
def _version(executable, sha256, runtime_digest):
    result = _inspect_command([executable, '--version'])
    if result.returncode or not result.stdout.strip().startswith('Inkscape '):
        raise ValueError('unable to identify installed Inkscape')
    return result.stdout.strip()


def engine_identity():
    from .cache_state import file_digest
    executable = inkscape_executable()
    binary_hash = file_digest(executable)
    libraries = runtime_libraries(executable)
    return {'name': 'inkscape', 'executable': executable, 'sha256': binary_hash,
            'version': _version(executable, binary_hash, digest(canonical(libraries))),
            'libraries': libraries, 'process_environment': dict(_PROCESS_ENV),
            'private_configuration': 'fresh-home-profile-fontconfig/v1'}


@lru_cache(maxsize=32)
def _font_faces(executable, path, sha256, runtime_digest):
    from .cache_state import file_digest
    result = _inspect_command([executable, '-f', '%{family}\t%{style}\t%{index}\n', path])
    if result.returncode or not result.stdout.strip() or file_digest(path) != sha256:
        raise ValueError('declared SVG font could not be identified or changed during inspection')
    faces = []
    for line in result.stdout.splitlines():
        family, style, index = line.split('\t')
        faces.append({'family': family, 'style': style, 'index': int(index)})
    return faces


@lru_cache(maxsize=8)
def _fontconfig_version(executable, sha256, runtime_digest):
    result = _inspect_command([executable, '--version'])
    if result.returncode:
        raise ValueError('unable to identify fontconfig')
    return (result.stdout + result.stderr).strip()


def fontconfig_identity():
    from .cache_state import file_digest
    executable = shutil.which('fc-scan')
    if executable is None:
        raise ValueError('SVG-sequence requires fontconfig fc-scan on PATH')
    executable = str(Path(executable).resolve())
    sha256 = file_digest(executable)
    libraries = runtime_libraries(executable)
    return {'executable': executable, 'sha256': sha256, 'libraries': libraries,
            'version': _fontconfig_version(executable, sha256, digest(canonical(libraries)))}


def resolve(c, clip):
    """Re-read every declared input; returned record carries full file hashes."""
    if route(c) != 'svg-sequence' or not is_svg(clip):
        raise ValueError('SVG sequence resolver requires an explicit SVG-only gfx timeline')
    raw = read_input(c, clip['src'], limit=MAX_MANIFEST_BYTES)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('SVG manifest exceeds 8 MiB input limit')
    manifest = json.loads(raw.decode('utf-8'))
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT:
        raise ValueError(f'SVG manifest.format must be {FORMAT}')
    for field, value in [('fps', c.fps), ('width', c.w), ('height', c.h), ('frame_count', clip['dur'])]:
        if type(manifest.get(field)) is not int or manifest[field] != value:
            raise ValueError(f'SVG manifest.{field} must equal {value}')
    if type(clip['dur']) is not int or not 1 <= clip['dur'] <= MAX_FRAMES:
        raise ValueError('SVG clip duration must be an integer in [1, 18000]')
    frames = manifest.get('frames')
    if not isinstance(frames, list) or len(frames) != clip['dur']:
        raise ValueError('SVG manifest.frames length must equal clip.dur')
    fonts = manifest.get('fonts', [])
    if not isinstance(fonts, list):
        raise ValueError('SVG manifest.fonts must be a list of project-relative files')
    files = {clip['src']: digest(raw)}
    total_bytes = len(raw)
    def add(name, *, limit=None):
        nonlocal total_bytes
        remaining = MAX_INPUT_BYTES if name in files else MAX_INPUT_BYTES - total_bytes
        data = read_input(c, name, limit=min(remaining, MAX_INPUT_BYTES if limit is None else limit))
        if name not in files:
            total_bytes += len(data)
        if total_bytes > MAX_INPUT_BYTES:
            raise ValueError('SVG declared inputs exceed 256 MiB budget')
        value = digest(data)
        if name in files and files[name] != value:
            raise ValueError('SVG input changed while resolving identity')
        files[name] = value
        return data
    fontconfig = fontconfig_identity()
    font_faces = {}
    for font in fonts:
        if not isinstance(font, str) or Path(font).suffix.lower() not in ('.ttf', '.otf', '.ttc', '.otc'):
            raise ValueError('SVG fonts must be explicit TTF/OTF/TTC/OTC files')
        add(font)
        font_faces[font] = _font_faces(fontconfig['executable'], str(input_path(c, font)), files[font], digest(canonical(fontconfig)))
    if 'generator' in manifest:
        add(manifest['generator'])
    from .cache_state import declared_dependencies
    for name in declared_dependencies(c, clip):
        # Existing adapter may canonicalize clip-local unions to absolute names.
        path = Path(name)
        relative = str(path.relative_to(Path(c.root).resolve())) if path.is_absolute() else name
        add(relative)
    families = {family.strip() for faces in font_faces.values() for face in faces
                for family in face['family'].split(',')}
    seen = set()
    for index, frame in enumerate(frames):
        if not isinstance(frame, str):
            raise ValueError('SVG manifest.frames must contain file paths')
        if frame not in seen:
            try:
                inspect_svg(add(frame, limit=MAX_SVG_BYTES), c.w, c.h, fonts=fonts, families=families)
            except (ValueError, OSError) as error:
                raise ValueError(f'SVG frame {index} ({frame}): {error}') from error
            seen.add(frame)
    code_names = ['gfx_svg.py', 'gfx_svg_stage.py']
    if sys.platform == 'darwin':
        code_names.append('gfx_environment.py')
    code = {name: digest(Path(__file__).with_name(name).read_bytes()) for name in code_names}
    return {'producer': PRODUCER, 'manifest': clip['src'], 'files': files,
            'frames': frames, 'fonts': fonts, 'font_faces': font_faces,
            'fontconfig': fontconfig, 'fps': c.fps, 'width': c.w,
            'height': c.h, 'frame_count': clip['dur'], 'engine': engine_identity(),
            'implementation': code,
            'parameters': {'export': 'png', 'area': 'page', 'background_opacity': 0,
                           'fonts': 'isolated-fontconfig', 'frame_index': 'clip-local'}}


def record_key(record):
    return digest(canonical(record))[:24]


def key(c, clip):
    return record_key(resolve(c, clip))


def completion(c, clip, directory, *, expected_record=None):
    """Read-only independent completion record; no HTML proof or adoption."""
    # A producer may reuse the record freshly resolved at this stage boundary.
    # External consumers always re-resolve; no context-global identity cache.
    record = resolve(c, clip) if expected_record is None else expected_record
    expected_key = record_key(record)
    try:
        value = json.loads((directory / COMPLETE_FILE).read_bytes())
    except (OSError, ValueError) as error:
        raise ValueError('SVG completion record missing; rerun vtx gfx') from error
    hashes = value.get('sha256') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get('format') != 'framivra-svg-completion/v1'
            or value.get('key') != expected_key or directory.name != expected_key
            or value.get('input') != record or not isinstance(hashes, list)
            or len(hashes) != clip['dur']
            or any(not isinstance(x, str) or not re.fullmatch('[0-9a-f]{64}', x) for x in hashes)):
        raise ValueError('SVG completion record does not match inputs; rerun vtx gfx')
    return hashes


class SequenceSnapshot:
    """One consumer operation's input snapshot, never a live global cache.

    Input reuse is valid only while the exact explicit consumer scope is active.
    Its caller checks full inputs before/after the operation. Outside that scope,
    including an escaped instance, every interval rechecks the complete identity.
    """
    def __init__(self, c, clip, directory, record, *, scope=None):
        self.c, self.clip, self.directory, self.record = c, clip, directory, record
        self.scope = scope
        self.hashes = self.marker_stat = None

    def _marker_stat(self):
        try:
            value = (self.directory / COMPLETE_FILE).stat()
        except OSError as error:
            raise ValueError('SVG completion record missing; rerun vtx gfx') from error
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)

    def check_interval(self, start, stop):
        if self.scope is None or getattr(self.c, '_svg_consumer_sources', None) is not self.scope:
            return completion(self.c, self.clip, self.directory)
        before = self._marker_stat()
        if self.hashes is None:
            self.hashes = completion(self.c, self.clip, self.directory, expected_record=self.record)
            after = self._marker_stat()
            if before != after:
                raise ValueError('SVG completion changed while pinning the consumer snapshot')
            self.marker_stat = after
        elif before != self.marker_stat:
            raise ValueError('SVG completion changed during consumption; rerun vtx gfx')
        for name in set(self.record['frames'][start:stop]):
            data = read_input(self.c, name, limit=MAX_SVG_BYTES)
            if digest(data) != self.record['files'][name]:
                raise ValueError('SVG requested frame changed during consumption; rerun vtx gfx')
        return self.hashes


@contextlib.contextmanager
def consumer_scope(c):
    """Operation-local reuse only; caller must verify all inputs before/after."""
    if getattr(c, '_svg_consumer_sources', None) is not None:
        raise ValueError('nested SVG consumer scopes are unsupported')
    c._svg_consumer_sources = {}
    try:
        yield
    finally:
        del c._svg_consumer_sources
