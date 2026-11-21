#!/usr/bin/env python3
"""Prepare and qualify an ordinary fixed-wheel candidate offline (maintainers only).

A candidate statement records observations, not release approval. Qualification
requires independent expected wheel/launcher hashes from the reviewing caller;
this tool never reads approval from the statement, a project or an install.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

_HEX = re.compile(r'[0-9a-f]{64}')
_COMMIT = re.compile(r'[0-9a-f]{40}')
_WHEEL_LIMIT = 64 * 1024**2
_MEDIA_LIMIT = 5 * 1024**2
_WIRE_LIMIT = 8 * 1024**2
_LAUNCHER = 'framivra.preview-window.fixed-wheel/v1'
_WINDOW = 'framivra.timeline.preview-window/v1'
_INPUTS = ('vt.py', 'vtx', 'pyproject.toml', 'setup.py', 'VERSION', 'MANIFEST.in', 'README.md', 'LICENSE',
           'plugins/registry.json', 'scripts/preview_window_wheel.py')
_SYSTEM_PYTHON = '/usr/bin/python3'
_SYSTEM_FFMPEG = '/usr/bin/ffmpeg'


class SupplierError(ValueError):
    pass


def _require(ok, message):
    if not ok:
        raise SupplierError(message)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _object(pairs):
    value = dict(pairs)
    _require(len(value) == len(pairs), 'duplicate qualification field')
    return value


def _json(raw):
    _require(raw.count(b'\n') == 1 and raw.endswith(b'\n'), 'expected one complete JSON line')
    value = json.loads(raw, object_pairs_hook=_object)
    _require(type(value) is dict, 'expected a qualification JSON object')
    return value


def _env(home):
    return {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LC_ALL': 'C.UTF-8', 'TZ': 'UTC',
            'PIP_CONFIG_FILE': os.devnull, 'PIP_NO_INDEX': '1', 'SOURCE_DATE_EPOCH': '315532800',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}


def _run(args, *, cwd, env, timeout=90, limit=32 * 1024):
    # Read at most the accepted output plus one byte; deadline and ownership
    # hold across both pipe EOF and process settlement.
    deadline = time.monotonic() + timeout
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          cwd=cwd, env=env, umask=0o077, start_new_session=True) as proc:
        data = bytearray()
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(proc.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    _require(remaining > 0 and ready.select(remaining), 'qualification command timed out')
                    chunk = os.read(proc.stdout.fileno(), min(65536, limit + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    _require(len(data) <= limit, 'qualification command exceeded its output limit')
            code = proc.wait(timeout=max(0, deadline - time.monotonic()))
            if code:
                # A launched wheel may return ordinary vtx diagnostics. Keep
                # only bounded known-shaped codes, never rendered data or log text.
                codes = []
                if data[:1] == b'{' and len(data) <= 32 * 1024:
                    try:
                        record = _json(bytes(data))
                        codes = [row['code'] for row in record.get('diagnostics', [])[:8]
                                 if type(row) is dict and type(row.get('code')) is str
                                 and re.fullmatch(r'[a-z][a-z0-9_.-]{0,63}', row['code'])]
                    except (ValueError, TypeError, RecursionError):
                        pass
                detail = ': ' + ', '.join(codes) if codes else ''
                raise SupplierError(f'{Path(args[0]).name} failed ({code})' + detail)
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                return bytes(data)
            raise SupplierError('qualification process group did not settle')
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise


def _read(path, limit):
    _require(path.resolve(strict=True) == path, 'linked artifact path')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= limit,
                 'unsupported artifact file type or size')
        data = stream.read(limit + 1)
        _require(len(data) == info.st_size, 'artifact size changed')
        return data


def _write(path, data, mode=0o400):
    with path.open('xb') as output:
        output.write(data)
    path.chmod(mode)


def _source_archive(source, commit, env):
    def git(*args, limit=32 * 1024):
        return _run(['/usr/bin/git', *args], cwd=source, env=env, limit=limit)

    _require(_COMMIT.fullmatch(commit) is not None and git('rev-parse', 'HEAD').strip() == commit.encode('ascii')
             and not git('status', '--porcelain'), 'expected a clean checkout at the exact requested commit')
    listing = git('ls-tree', '-rz', commit, '--', *_INPUTS, limit=1024**2)
    expected = {}
    for line in listing.split(b'\0')[:-1]:
        meta, name = line.split(b'\t', 1)
        mode, kind, oid = meta.split(b' ')
        path = name.decode('ascii')
        _require(kind == b'blob' and mode in (b'100644', b'100755') and path not in expected,
                 'expected only regular core source blobs')
        expected[path] = oid.decode('ascii')
    _require(set(_INPUTS) - {'vtx'} <= expected.keys(), 'immutable commit lacks a required build input')
    raw = git('archive', '--format=tar', commit, '--', *_INPUTS, limit=_WHEEL_LIMIT)
    members = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for entry in archive:
            if entry.isdir():
                continue
            _require(entry.isfile() and entry.name in expected and entry.name not in members
                     and 0 <= entry.size <= _WHEEL_LIMIT, 'unexpected source archive member')
            stream = archive.extractfile(entry)
            data = stream.read()
            oid = hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()
            _require(oid == expected[entry.name], 'archived source bytes differ from exact commit')
            members[entry.name] = data
    _require(members.keys() == expected.keys(), 'incomplete archived wheel source')
    return members


def _build(python, root, members, env):
    source, wheels = root / 'src', root / 'wheels'
    source.mkdir(parents=True)
    wheels.mkdir()
    for name, data in members.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, data)
    _run([python, '-IB', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'wheel', '--no-index',
          '--no-deps', '--no-build-isolation', '--wheel-dir', wheels, source], cwd=root, env=env)
    paths = tuple(wheels.iterdir())
    _require(len(paths) == 1 and re.fullmatch(r'framivra-[A-Za-z0-9_.+]+-py3-none-any\.whl', paths[0].name),
             'expected one pure Framivra kernel wheel')
    return paths[0].name, _read(paths[0], _WHEEL_LIMIT)


def build_candidate(source, commit, output, *, python):
    """Emit unapproved candidate bytes, not a pin or managed install receipt."""
    _require(not output.is_relative_to(source) and not source.is_relative_to(output),
             'candidate output must be outside the source checkout')
    with tempfile.TemporaryDirectory(prefix='.framivra-build-', dir=output.parent) as tmp:
        root = Path(tmp)
        env = _env(root)
        members = _source_archive(source, commit, env)
        tools = _json(_run([python, '-IB', '-c', 'import importlib.metadata as m,json,sys; '
                           'print(json.dumps(dict(python=sys.version.split()[0], '
                           'pip=m.version("pip"),setuptools=m.version("setuptools"),wheel=m.version("wheel"))))'],
                          cwd=root, env=env))
        filename, wheel = _build(python, root / 'one', members, env)
        _require(_build(python, root / 'two', members, env) == (filename, wheel), 'offline wheel build was not reproducible')
        launcher = members['scripts/preview_window_wheel.py']
        proposal = {'schema': 'framivra.preview-supplier-candidate/v1', 'commit': commit, 'wheelFilename': filename,
                    'launcherSha256': _sha(launcher), 'wheelSha256': _sha(wheel), 'buildTools': tools}
        output.mkdir(mode=0o700)  # Exclusive: no existing distribution or install is replaced.
        _write(output / 'vtx-preview', launcher, 0o500)
        _write(output / 'kernel.whl', wheel)
        _write(output / 'candidate.json', (json.dumps(proposal, sort_keys=True) + '\n').encode('ascii'))
    return proposal


def _receipt(wire, pins, version):
    record = _json(wire)
    expected = {'schema': _WINDOW, 'ok': True, 'content_version': version, 'launcher': _LAUNCHER,
                'wheel_sha256': pins['wheel_sha256'], 'runtime_projection_sha256': pins['runtime_projection_sha256'],
                'start': 0, 'end': 30, 'fps': {'num': 30, 'den': 1}, 'width': 1920, 'height': 1080,
                'mime': 'video/mp4', 'audio_sample_rate': 48000, 'audio_channels': 2}
    _require(all(json.dumps(record.get(k), sort_keys=True) == json.dumps(value, sort_keys=True)
                 for k, value in expected.items())
             and type(record.get('input_epoch')) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', record['input_epoch']),
             'ordinary window identity or exact clock did not qualify')
    size, encoded = record.get('byte_length'), record.get('base64')
    _require(type(size) is int and 0 < size <= _MEDIA_LIMIT and type(encoded) is str
             and len(encoded) == 4 * ((size + 2) // 3), 'ordinary window media envelope did not qualify')
    media = base64.b64decode(encoded, validate=True)
    _require(len(media) == size and _sha(media) == record.get('sha256') and media[4:8] == b'ftyp'
             and base64.b64encode(media).decode('ascii') == encoded,
             'ordinary window media bytes did not qualify')
    return media


def qualify_candidate(artifacts, *, wheel_filename, launcher_sha256, wheel_sha256, work_parent, python):
    """Return observed enrollment metadata after a clean real ordinary probe.

    Inputs here are maintainer/release-verifier facts, never read from the
    candidate statement. A normal end-user installer must source these facts
    from its separately approved release catalog; none is bundled here.
    """
    _require(sys.platform == 'linux' and os.geteuid() != 0, 'ordinary fixed wheel requires unprivileged Linux')
    _require(all(_HEX.fullmatch(digest) for digest in (launcher_sha256, wheel_sha256))
             and re.fullmatch(r'framivra-[A-Za-z0-9_.+]+-py3-none-any\.whl', wheel_filename),
             'expected independently approved launcher/wheel identities')
    launcher, wheel = _read(artifacts / 'vtx-preview', 256 * 1024), _read(artifacts / 'kernel.whl', _WHEEL_LIMIT)
    _require((_sha(launcher), _sha(wheel)) == (launcher_sha256, wheel_sha256),
             'candidate does not match independently approved launcher and wheel')
    with tempfile.TemporaryDirectory(prefix='framivra-ordinary-', dir=work_parent) as tmp:
        root = Path(tmp)
        home, profile, project = (root / name for name in ('home', 'preview', 'fixture'))
        for directory in (home, profile, project):
            directory.mkdir(mode=0o700)
        env = _env(home)
        distribution = root / wheel_filename
        _write(distribution, wheel)
        program = profile / 'vtx-preview'
        _write(program, launcher, 0o500)
        _write(profile / 'kernel.whl', wheel)
        _write(profile / 'kernel.sha256', (wheel_sha256 + '\n').encode('ascii'))
        site = root / 'site'
        _run([python, '-IB', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install', '--no-index',
              '--no-deps', '--no-compile', '--target', site, distribution], cwd=root, env=env)

        def call(*args, limit=32 * 1024):
            return _json(_run([_SYSTEM_PYTHON, '-IBS', program, *args], cwd=root, env=env, limit=limit))

        staged = call('--stage-from', site)
        verified = call('--verify')
        _require(set(verified) == {'ok', 'wheel_sha256', 'runtime_projection_sha256'}
                 and verified['ok'] is True and verified['wheel_sha256'] == wheel_sha256
                 and type(verified['runtime_projection_sha256']) is str
                 and _HEX.fullmatch(verified['runtime_projection_sha256'])
                 and set(staged) == verified.keys() | {'installed_member_sha256'}
                 and all(staged[key] == value for key, value in verified.items()),
                 'staging and fixed-wheel verification did not match')
        capabilities = call('-t', 'unused', 'capabilities')
        _require(capabilities.get('schema') == 'framivra.timeline.read/v1', 'unexpected launcher capability schema')
        cap = capabilities['capabilities']['preview_window']
        _require(cap['schema'] == _WINDOW and cap['launcher'] == _LAUNCHER
                 and cap['inline_media'] is cap['prepare_inputs'] is cap['input_epoch'] is True
                 and all(cap[key] == verified[key] for key in ('wheel_sha256', 'runtime_projection_sha256')),
                 'ordinary fixed-wheel capability identity did not match')
        source = project / 'probe.mp4'
        _run([_SYSTEM_FFMPEG, '-v', 'error', '-nostdin', '-f', 'lavfi', '-i', 'color=c=teal:s=1920x1080:r=30:d=1',
              '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1', '-c:v', 'libx264',
              '-threads:v', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ac', '2', source], cwd=root, env=env)
        timeline = project / 'timeline.json'
        document = {'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 30},
                    'sources': [{'id': 'fixture', 'path': source.name}], 'subtitles': [],
                    'video_tracks': [{'name': 'picture', 'kind': 'aroll', 'clips': [
                        {'src': 'fixture', 'dst_in': 0, 'dur': 30, 'src_in': 0, 'src_out': 30}]}],
                    'audio_tracks': [{'name': 'voice', 'events': [{'src': 'fixture', 'in': 0, 'out': 30, 'volume': 1}]}]}
        raw = (json.dumps(document, sort_keys=True) + '\n').encode('ascii')
        _write(timeline, raw)
        version = 'sha256:' + _sha(raw)
        wire = _run([_SYSTEM_PYTHON, '-IBS', program, '-t', timeline, 'timeline', 'preview-window', '--frame', '0',
                     '--expected-content-version=' + version, '--render', '--include-media', '--prepare-inputs'],
                    cwd=root, env=env, limit=_WIRE_LIMIT)
        media = root / 'decoded-probe.mp4'
        _write(media, _receipt(wire, verified, version))
        _run([_SYSTEM_FFMPEG, '-v', 'error', '-xerror', '-nostdin', '-protocol_whitelist', 'file', '-f', 'mov',
              '-i', media, '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], cwd=root, env=env)
        _require(_read(timeline, 1024 * 1024) == raw and _read(program, 256 * 1024) == launcher
                 and call('--verify') == verified, 'qualification input or profile changed')
        return {'schema': 'framivra.preview-supplier/v1', 'abi': _LAUNCHER, 'launcherSha256': launcher_sha256,
                'wheelSha256': wheel_sha256, 'runtimeProjectionSha256': verified['runtime_projection_sha256']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    build = sub.add_parser('build')
    build.add_argument('--source', required=True, type=Path)
    build.add_argument('--commit', required=True)
    build.add_argument('--output', required=True, type=Path)
    verify = sub.add_parser('qualify')
    verify.add_argument('--artifacts', required=True, type=Path)
    verify.add_argument('--work-parent', required=True, type=Path)
    verify.add_argument('--wheel-filename', required=True)
    verify.add_argument('--launcher-sha256', required=True)
    verify.add_argument('--wheel-sha256', required=True)
    for command in (build, verify):
        command.add_argument('--python', default=sys.executable, type=Path,
                             help='maintainer-approved local interpreter with offline pip/setuptools/wheel')
    args = parser.parse_args(argv)
    try:
        if args.operation == 'build':
            result = build_candidate(args.source.resolve(strict=True), args.commit, args.output.absolute(),
                                     python=args.python.absolute())
        else:
            result = qualify_candidate(args.artifacts.resolve(strict=True), wheel_filename=args.wheel_filename,
                                       launcher_sha256=args.launcher_sha256, wheel_sha256=args.wheel_sha256,
                                       work_parent=args.work_parent.resolve(strict=True), python=args.python.absolute())
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f'preview supplier refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
