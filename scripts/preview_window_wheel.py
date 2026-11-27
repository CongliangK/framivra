#!/usr/bin/python3 -IBS
"""Isolated DSH development launcher; deliberately not part of the wheel.

Set previewWindowExecutable (not vtxExecutable) to a trusted copy of this
executable in a dedicated private Host configuration directory outside all
Session workspaces. Place the approved artifact at kernel.whl and its
independently approved SHA-256 at kernel.sha256 (lowercase hex + LF). This
launcher serves only fixed preview commands without VTX_CONFIG. Optional formal
archive reads require a separate administrator manifest pin.

Offline, --stage-from /absolute/site-packages checks the installed wheel members
and creates a new runtime/ projection from the approved bytes. It never uses
pip, installed RECORD, a venv interpreter, or installed bytecode. --verify checks
the staged artifact without importing it. Normal Host invocations recheck it
before importing anything from the wheel. The optional leading managed pins
argument also requires the trusted Host's expected wheel and projection digests.
The launch directory and its contents
must stay under the trusted operator's control, including against same-UID writes;
this is not isolation from arbitrary code already running as that operator.
"""
from contextlib import ExitStack, redirect_stdout
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import stat
import sys
import time
import zipfile
from email.parser import BytesParser


_SYSTEM_PYTHON = Path('/usr/bin/python3')
_LIMIT = 64 * 1024 * 1024
_NAME = re.compile(r'[A-Za-z0-9_+.-]+')
_DIST = re.compile(r'framivra-([A-Za-z0-9_.+]+)\.dist-info')
_LAUNCHER = 'framivra.preview-window.fixed-wheel/v1'
_MANAGED_IDENTITY = 'framivra.preview-window.managed-pins/v1'
_MANAGED_PINS = re.compile(r'--managed-preview-pins-v1=([0-9a-f]{64}):([0-9a-f]{64})')
_INPUT_EPOCH_ABI = 'framivra.preview-window.inputs/v1'
_WARM_SVG_MEDIA_ABI = 'framivra.preview-window.warm-svg-media/v1'
_PROGRAM_AUDIO_ABI = 'framivra.preview-window.program-audio/v1'
_FIRST_FRAME_ABI = 'framivra.preview-window.first-frame/v1'
_FIRST_FRAME = 'preview-window-first-frame'
_FIRST_FRAME_PROBE = '--first-frame-capabilities'
_FORMAL_ARCHIVE = 'preview-formal-archive'
_FORMAL_ARCHIVE_PROBE = '--formal-archive-capabilities'
_FORMAL_ARCHIVE_ABI = 'framivra.preview-formal-archive/v1'
_FORMAL_ARCHIVE_MEMBER = 'vtx/runtime/preview_formal_archive.py'
_PREVIEW_FLAGS = {
    ('--render', '--include-media', '--prepare-inputs'): 'preview-window',
    ('--read-warm-svg',): 'preview-warm-svg',
    ('--render', '--include-media', '--prepare-inputs', '--read-warm-svg'): 'preview-warm-svg-media',
    ('--render', '--include-media', '--prepare-inputs', '--use-program-audio'): 'preview-program-audio',
    ('--render', '--include-media', '--prepare-inputs', '--read-warm-svg', '--use-program-audio'):
        'preview-warm-svg-program-audio',
}


class ProfileError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise ProfileError(message)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _identity(files):
    return _sha(''.join(f'{name}\0{_sha(data)}\n' for name, data in sorted(files.items())).encode('ascii'))


def _system_python():
    real = _SYSTEM_PYTHON.resolve(strict=True)
    info = real.stat()
    _require(sys.platform == 'linux' and os.geteuid() != 0 and sys.base_prefix == sys.prefix == '/usr'
             and Path(sys.executable).resolve(strict=True) == real and real.parent == _SYSTEM_PYTHON.parent
             and stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o6022
             and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode,
             'use the unprivileged system Python with -IBS')


def _trusted(info):
    return info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022


def _directory(path, *, trusted=False):
    _require(path.is_absolute() and path.resolve(strict=True) == path and path.is_dir(),
             'expected a physical absolute directory')
    if trusted:
        _require(all(_trusted(p.lstat()) for p in (path, *path.parents)),
                 'profile directory has an untrusted owner or writable ancestor')


def _read(path, limit, *, trusted=False):
    _require(path.resolve(strict=True) == path, 'linked wheel or installation member')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit
                 and (not trusted or _trusted(info)), 'wheel or installation member has an unsupported type, owner or size')
        data = stream.read(limit + 1)
        _require(len(data) == info.st_size, 'wheel or installation member size changed')
        return data


def _wheel(raw, pin):
    _require(re.fullmatch(r'[0-9a-f]{64}', pin) is not None and _sha(raw) == pin,
             'wheel SHA-256 does not match the isolated configuration pin')
    files, total = {}, 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        _require(0 < len(members) <= 512, 'unsupported wheel member count')
        for member in members:
            name = member.orig_filename
            parts = name.split('/')
            total += member.file_size
            _require(all(_NAME.fullmatch(part) and part not in ('.', '..') for part in parts)
                     and name not in files and total <= _LIMIT and member.create_system in (0, 3)
                     and stat.S_IFMT(member.external_attr >> 16) in (0, stat.S_IFREG),
                     'unsupported wheel member')
            files[name] = archive.read(member)
    roots = {name.split('/')[0] for name in files}
    dist = roots - {'vtx', 'vt.py'}
    _require(len(dist) == 1 and _DIST.fullmatch(next(iter(dist))) is not None,
             'expected one pure Framivra wheel distribution')
    dist = dist.pop()
    _require({'vt.py', 'vtx/__init__.py', 'vtx/runtime/preview_window_isolation.py',
              'vtx/pluginlib/builtin_registry.json', f'{dist}/RECORD', f'{dist}/METADATA',
              f'{dist}/WHEEL'}.issubset(files), 'wheel is missing required Framivra members')
    metadata = BytesParser().parsebytes(files[f'{dist}/METADATA'], headersonly=True)
    wheel = BytesParser().parsebytes(files[f'{dist}/WHEEL'], headersonly=True)
    version = _DIST.fullmatch(dist)[1]
    _require(metadata.get_all('Name') == ['framivra'] and metadata.get_all('Version') == [version]
             and not metadata.get_all('Requires-Dist') and wheel.get_all('Root-Is-Purelib') == ['true']
             and wheel.get_all('Tag') == ['py3-none-any'], 'expected a dependency-free pure Framivra wheel')
    files.pop(f'{dist}/RECORD')  # pip regenerates this; it is never a trust input or loaded.
    runtime = {name: data for name, data in files.items() if name.startswith('vtx/')}
    _require(all('__pycache__' not in name.split('/') and (name.endswith('.py') or name in {
        'vtx/VERSION', 'vtx/preview.html', 'vtx/timeline.schema.json', 'vtx/pluginlib/builtin_registry.json'
    }) for name in runtime), 'unsupported vtx runtime member')
    if 'vtx/VERSION' in runtime:
        _require(runtime['vtx/VERSION'].strip() == version.encode('ascii'), 'conflicting wheel version')
    # Both parent and worker see the pinned metadata version, with no dist-info
    # lookup (the inner namespace only mounts the package and this VERSION file).
    runtime['VERSION'] = (version + '\n').encode('ascii')
    return files, runtime


def _tree(root, expected, *, installed=False, prefix=''):
    """Exact deployed tree; a pip installation may have ignored __pycache__ dirs."""
    _directory(root)
    _require(installed or _trusted(root.lstat()), 'untrusted projection directory')
    dirs = {parent.as_posix() for name in expected for parent in Path(name).parents} - {'.'}

    def scan(path, base):
        for item in path.iterdir():
            key = base + item.name
            info = item.lstat()
            if stat.S_ISDIR(info.st_mode):
                if installed and item.name == '__pycache__':
                    continue
                _require(key in dirs and (installed or _trusted(info)), 'unexpected installation directory')
                scan(item, key + '/')
            else:
                _require(key in expected and _read(item, len(expected[key]), trusted=not installed) == expected[key],
                         'unexpected or changed installation member')
                seen.add(key)

    seen = set()
    scan(root, prefix)
    _require(seen == set(expected), 'missing installation member')


def _stage(site, target, installed, runtime):
    _tree(site / 'vtx', {k: v for k, v in installed.items() if k.startswith('vtx/')},
          installed=True, prefix='vtx/')
    for name, data in installed.items():
        if not name.startswith('vtx/'):
            _require(_read(site / name, len(data)) == data, 'changed installed wheel metadata or launcher module')
    original_umask = os.umask(0o077)
    try:
        target.mkdir(mode=0o700)  # exclusive; failures never replace a deployment
        for name, data in runtime.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(data)
    finally:
        os.umask(original_umask)


def _managed_request(args):
    # Reserve the prefix everywhere, including the otherwise unused capability
    # placeholder. A misplaced/unknown token must not select the legacy path.
    positions = [i for i, arg in enumerate(args) if arg.startswith('--managed-preview-pins')]
    if not positions:
        return None, args
    _require(positions == [0], 'managed preview pins must appear once as the leading argument')
    match = _MANAGED_PINS.fullmatch(args[0])
    _require(match is not None, 'malformed managed preview pins')
    return match.groups(), args[1:]


def _operation(args, profile):
    if len(args) == 3 and args[0] == '-t' and args[2] == 'capabilities':
        return 'capabilities'  # Host may supply a placeholder; never open it.
    if args == [_FORMAL_ARCHIVE_PROBE]:
        return _FORMAL_ARCHIVE_PROBE
    if len(args) > 3 and args[3] == _FORMAL_ARCHIVE:
        # Do not resolve, stat or open a caller-supplied selector. It must
        # first compare as an exact string against the pinned manifest.
        _require(len(args) == 9 and args[0] == '-t' and args[1].startswith('/')
                 and args[2] == 'timeline' and args[4] == '--frame'
                 and re.fullmatch(r'0|[1-9][0-9]{0,15}', args[5])
                 and re.fullmatch(r'--expected-content-version=sha256:[0-9a-f]{64}', args[6])
                 and args[7] == '--read-approved-archive' and re.fullmatch(r'--nonce=[0-9a-f]{32}', args[8]),
                 'expected only the exact formal archive selectors')
        return _FORMAL_ARCHIVE
    if args == [_FIRST_FRAME_PROBE]:
        return _FIRST_FRAME_PROBE
    first_frame = len(args) == 11 and args[3] == _FIRST_FRAME and re.fullmatch(r'--nonce=[0-9a-f]{32}', args[10])
    if first_frame:
        _require(args[7:10] == ['--render', '--include-media', '--prepare-inputs'],
                 'first-frame transport requires ordinary media flags only')
    op = _PREVIEW_FLAGS.get(tuple(args[7:]))
    op = _FIRST_FRAME if first_frame else op
    _require(op is not None and args[0] == '-t' and args[2:5] == [
        'timeline', _FIRST_FRAME if first_frame else 'preview-window', '--frame']
             and re.fullmatch(r'0|[1-9][0-9]{0,15}', args[5])
             and re.fullmatch(r'--expected-content-version=sha256:[0-9a-f]{64}', args[6]),
             'only the fixed isolated media or SVG warm commands are enabled')
    timeline = Path(args[1])
    _require(timeline.is_absolute(), 'preview timeline must be absolute')
    _require(not any(path == profile or profile in path.parents or path in profile.parents
                     for path in (timeline.parent.resolve(), timeline.resolve().parent)),
             'project overlaps the isolated Host profile')
    return op


def _qualified_command():
    from vtx.cli import timeline_preview_window

    # Compatibility only, after the independent pin and installed projection
    # checks. An older pinned wheel cannot attest this ABI.
    if (getattr(timeline_preview_window, 'FIXED_WHEEL_LAUNCHER', None) == _LAUNCHER
            and getattr(timeline_preview_window, 'INPUT_EPOCH_ABI', None) == _INPUT_EPOCH_ABI):
        return timeline_preview_window.run_preview_window_command
    return None


def _qualified_first_frame_command():
    from vtx.cli import timeline_preview_window

    command = getattr(timeline_preview_window, 'run_first_frame_command', None)
    if (_qualified_command() is not None
            and getattr(timeline_preview_window, 'FIRST_FRAME_ABI', None) == _FIRST_FRAME_ABI
            and callable(command)):
        return command
    return None


def _write_record(data, check):
    # The render command redirects Python's sys.stdout to stderr for diagnostics.
    # Both transport records belong on the launcher's OS stdout, never its log.
    fd = 1
    old = os.get_blocking(fd)
    deadline = time.monotonic() + 5
    data = memoryview(data)
    os.set_blocking(fd, False)
    try:
        while data:
            check()
            if time.monotonic() >= deadline:
                raise ProfileError('preview record reader did not drain its pipe')
            if select.select([], [fd], [], .05)[1]:
                try:
                    data = data[os.write(fd, data):]
                except BlockingIOError:
                    continue
    finally:
        os.set_blocking(fd, old)


def _managed_fonts(profile):
    if profile is None:
        return {}
    from vtx.cli import timeline_preview_window

    # Older fixed wheels keep their exact stock invocation. Only this verified
    # launcher passes the layout to an opted-in wheel, never argv or environment.
    if getattr(timeline_preview_window, 'MANAGED_FONTS_ABI', None) != 'framivra.preview-window.managed-fonts/v1':
        return {}
    return {'managed_font_profile': profile}


def _dispatch_first_frame(op, args, proof, reverify, *, managed_profile=None):
    from vtx.timeline_preview_window import WIRE_LIMIT, supported

    command = _qualified_first_frame_command()
    enabled = command is not None and supported()
    if op == _FIRST_FRAME_PROBE:
        print(json.dumps({'schema': _FIRST_FRAME_ABI, 'available': enabled, **proof,
                          'record_payload_limit_exclusive': WIRE_LIMIT}, ensure_ascii=True))
        return 0
    _require(enabled, 'wheel does not support launcher first-frame qualification')
    from vtx.runtime import preview_window_stream as stream

    nonce = args[10].split('=', 1)[1]
    broken = False

    def emit(kind, result, check):
        nonlocal broken
        data = stream.pack(kind, {'nonce': nonce, 'result': result}, WIRE_LIMIT)
        # A partial record cannot be followed by another header.
        broken = True
        _write_record(data, check)
        broken = False

    def first(record, check):
        emit(stream.FIRST, {**record, **proof}, check)

    normalized = [*args[:3], 'preview-window', *args[4:10]]
    status, result = command(normalized, qualification=proof, nonce=nonce,
                             reverify_profile=reverify, emit=first, **_managed_fonts(managed_profile))
    if broken:
        return 1
    if status == 0:
        reverify()
    emit(stream.FINAL, result, lambda: None)
    return status


def _dispatch_formal_archive(op, args, profile, proof, reverify, runtime):
    probe = op == _FORMAL_ARCHIVE_PROBE
    nonce = None if probe else args[8].split('=', 1)[1]
    phase = 'archive.unavailable'
    emitting = False
    deadline = time.monotonic() + 30

    def check():
        _require(time.monotonic() < deadline, 'formal archive verification deadline exceeded')

    def wire(result):
        return (json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n').encode('ascii')

    capability = {'schema': 'framivra.preview-formal-archive.capabilities/v1', 'available': False, **proof}
    try:
        _require(_FORMAL_ARCHIVE_MEMBER in runtime, 'wheel does not support formal archive qualification')
        from vtx.runtime import preview_formal_archive as archive

        _require(archive.SCHEMA == _FORMAL_ARCHIVE_ABI, 'unsupported formal archive ABI')
        with ExitStack() as stack:
            manifest, manifest_sha, media, bundle = archive.read_bundle(stack, profile, proof, check)
            if probe:
                reverify()
                archive.recheck(bundle, check)
                capability['available'] = True
            else:
                phase = 'archive.selector_mismatch'
                _require(args[1] == manifest['timeline'] and int(args[5]) == manifest['start']
                         and args[6].split('=', 1)[1] == manifest['content_version'],
                         'request does not match the approved archive')
                phase = 'archive.inputs_mismatch'
                inputs, still_local = archive.pin_inputs(stack, manifest, profile, check)
                result = {k: manifest[k] for k in ('content_version', 'start', 'end', 'fps', 'byte_length', 'sha256')}
                data = wire(dict(result, **proof, schema=_FORMAL_ARCHIVE_ABI, ok=True, nonce=nonce,
                                 manifest_sha256=manifest_sha, width=1920, height=1080,
                                 audio_sample_rate=48000, audio_channels=2, mime='video/mp4',
                                 base64=base64.b64encode(media).decode('ascii')))
                phase = 'archive.unavailable'
                _require(len(data) <= archive.WIRE_LIMIT, 'formal archive exceeds its wire limit')
                reverify()
                archive.recheck(bundle, check)
                phase = 'archive.inputs_mismatch'
                still_local()
                archive.recheck(inputs, check)
                phase = 'archive.unavailable'
                for _, guard, _ in bundle:
                    guard()
                check()
                # A reader may hold the pipe open while the project changes.
                # Keep the fds through publication and settle only if still
                # observed unchanged. Never append a second JSON to this wire.
                emitting = True
                _write_record(data, check)
                for _, guard, _ in inputs + bundle:
                    guard()
                check()
                return 0
    except (OSError, ValueError, RuntimeError) as error:
        if emitting:
            raise ProfileError('formal archive did not settle after publication') from error
        if not probe:
            _write_record(wire({'schema': _FORMAL_ARCHIVE_ABI, 'ok': False, 'nonce': nonce, 'code': phase}),
                          lambda: None)
            return 1
    _write_record(wire(capability), lambda: None)
    return 0


def _dispatch(op, args, proof, *, managed=False, managed_profile=None):
    from vtx.cli import timeline_preview_window

    warm_media = getattr(timeline_preview_window, 'WARM_SVG_MEDIA_ABI', None) == _WARM_SVG_MEDIA_ABI
    program_audio = getattr(timeline_preview_window, 'PROGRAM_AUDIO_ABI', None) == _PROGRAM_AUDIO_ABI
    if op == 'capabilities':
        from vtx.timeline import timeline_capabilities

        result = timeline_capabilities()
        # Keep the Host's existing read/v1 probe envelope; only this exact
        # probe is accepted here, not any of the ordinary read subcommands.
        capabilities = result['capabilities']
        for key in ('frame', 'storage'):
            capabilities.pop(key, None)
        window = capabilities.get('preview_window')
        if window is not None:
            if _qualified_command() is None:
                capabilities.pop('preview_window')
            else:
                window.update(proof, input_epoch=True)
                window.pop('managed_identity', None)  # Reserved for this launcher, not the wheel.
                if managed:
                    window['managed_identity'] = _MANAGED_IDENTITY
                if warm_media:
                    window['warm_svg_media'] = True
                if program_audio:
                    window['program_audio'] = True
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    command = _qualified_command()
    if ((op == 'preview-warm-svg'
         and getattr(timeline_preview_window, 'WARM_SVG_SCHEMA', None) != 'framivra.preview-window.warm-svg/v1')
            or (op in ('preview-warm-svg-media', 'preview-warm-svg-program-audio') and not warm_media)
            or (op in ('preview-program-audio', 'preview-warm-svg-program-audio') and not program_audio)):
        command = None
    if command is None:
        print('preview wheel refused: wheel does not support launcher qualification', file=sys.stderr)
        return 2
    return command(args, qualification=proof,
                   **(_managed_fonts(managed_profile) if op == 'preview-window' else {}))


def main():
    try:
        _system_python()
        script = Path(__file__).absolute()
        profile = script.parent
        _directory(profile, trusted=True)
        info = script.lstat()
        _require(stat.S_ISREG(info.st_mode) and _trusted(info) and not profile.stat().st_mode & 0o077,
                 'launcher must be in a private trusted Host configuration directory')
        _require('VTX_CONFIG' not in os.environ, 'the isolated Host profile does not accept VTX_CONFIG')
        expected, args = _managed_request(sys.argv[1:])
        staging = len(args) == 2 and args[0] == '--stage-from'
        verify = args == ['--verify']
        _require(not (staging and expected), 'managed preview pins cannot authorize staging')
        op = None if staging or verify else _operation(args, profile)
        pin_line = _read(profile / 'kernel.sha256', 65, trusted=True).decode('ascii')
        _require(re.fullmatch(r'[0-9a-f]{64}\n', pin_line) is not None, 'expected one fixed wheel SHA-256 line')
        pin = pin_line[:-1]
        if expected:
            _require(pin == expected[0], 'wheel pin differs from trusted managed expectation')
        installed, runtime = _wheel(_read(profile / 'kernel.whl', _LIMIT, trusted=True), pin)
        projection = _identity(runtime)
        if expected:
            _require(projection == expected[1], 'runtime projection differs from trusted managed expectation')
        target = profile / 'runtime'
        if staging:
            site = Path(args[1])
            _directory(site)
            _require(site != profile and site not in profile.parents and profile not in site.parents,
                     'installed site-packages must be separate from the trusted Host profile')
            _stage(site, target, installed, runtime)
        _tree(target, runtime)
        proof = {'launcher': _LAUNCHER, 'wheel_sha256': pin, 'runtime_projection_sha256': projection}
        if staging or verify:
            result = {'ok': True, 'wheel_sha256': pin, 'runtime_projection_sha256': projection}
            if expected:
                result['managed_identity'] = _MANAGED_IDENTITY
            if staging:
                result['installed_member_sha256'] = _identity(installed)
            print(json.dumps(result, sort_keys=True))
            return 0
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        message = str(error) if isinstance(error, ProfileError) else 'cannot read or verify the fixed wheel profile'
        print('preview wheel refused: ' + message, file=sys.stderr)
        return 2

    # The verified projection contains neither the original site-packages, a
    # venv interpreter, generated entry points nor any .pth or bytecode files.
    sys.path.insert(0, str(target))

    def reverify():
        try:
            _tree(target, runtime)
            _require(_read(profile / 'kernel.sha256', 65, trusted=True) == pin_line.encode('ascii')
                     and _sha(_read(profile / 'kernel.whl', _LIMIT, trusted=True)) == pin,
                     'wheel or pin changed during preview')
        except (OSError, ValueError) as error:
            if isinstance(error, ProfileError):
                raise
            raise ProfileError('cannot reverify the fixed wheel profile') from error

    if op in (_FORMAL_ARCHIVE, _FORMAL_ARCHIVE_PROBE):
        # The archive wire uses _write_record, which restores the inherited
        # stdout flags. Do not normalize the caller's shared descriptors.
        try:
            return _dispatch_formal_archive(op, args, profile, proof, reverify, runtime)
        except (OSError, ProfileError):
            print('preview wheel refused: formal archive transport unavailable', file=sys.stderr)
            return 2

    from vtx.runtime.stdio import ensure_blocking_stdio

    ensure_blocking_stdio()  # Same transport preparation as the normal CLI.
    managed_profile = profile if expected is not None else None
    if op in (_FIRST_FRAME, _FIRST_FRAME_PROBE):
        try:
            if op == _FIRST_FRAME_PROBE:
                reverify()
            return _dispatch_first_frame(op, args, proof, reverify, managed_profile=managed_profile)
        except (OSError, ProfileError):
            print('preview wheel refused: first-frame wire or wheel profile unavailable', file=sys.stderr)
            return 2
    if op == 'capabilities':
        return _dispatch(op, args, proof, managed=expected is not None)
    wire = io.StringIO()
    with redirect_stdout(wire):
        status = _dispatch(op, args, proof, managed_profile=managed_profile)
    if status == 0:
        try:
            reverify()
        except (OSError, ValueError) as error:
            message = str(error) if isinstance(error, ProfileError) else 'cannot reverify the fixed wheel profile'
            print('preview wheel refused: ' + message, file=sys.stderr)
            return 2
    sys.stdout.write(wire.getvalue())
    return status


if __name__ == '__main__':
    raise SystemExit(main())
