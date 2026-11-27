#!/usr/bin/env python3
"""Prepare and qualify an ordinary fixed-wheel candidate offline (maintainers only).

A candidate statement records observations, not release approval. Qualification
requires independent expected wheel/launcher hashes from the reviewing caller;
this tool never reads approval from the statement, a project or an install.

The normal installer can enable this only after a maintainer has reviewed the
exact source/build/artifact identities in the separately shipped installer
catalog. It builds locally without a published wheel. No user steps are added.
The first installer transaction supports
fresh install and exact-match retry only; a different existing profile is
preserved and needs a future automatic upgrade transaction.
"""
import argparse
import base64
from contextlib import contextmanager
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import signal
import threading
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
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0',
            'GIT_ALLOW_PROTOCOL': '', 'GIT_NO_LAZY_FETCH': '1'}


@contextmanager
def _group_reaper():
    # Like the core supervisor, treat child exit and descendant settlement as
    # separate facts. The ordinary bwrap path uses --die-with-parent and no
    # --new-session; pinning the wheel restricts this to that owned group.
    control = ctypes.CDLL(None, use_errno=True).prctl
    prior = ctypes.c_int()
    _require(control(37, ctypes.byref(prior), 0, 0, 0) == 0, 'cannot read child reaper policy')  # PR_GET_CHILD_SUBREAPER
    _require(control(36, 1, 0, 0, 0) == 0, 'cannot own qualification descendants')  # PR_SET_CHILD_SUBREAPER
    try:
        yield
    finally:
        _require(control(36, prior.value, 0, 0, 0) == 0, 'cannot restore child reaper policy')


def _group_empty(pgid, *, wait=False):
    while True:
        try:
            child, _ = os.waitpid(-pgid, 0 if wait else os.WNOHANG)
        except ChildProcessError:
            return True
        if not child:
            return False


def _stop_owned(proc):
    if proc.returncode is None:
        # Give the qualified launcher the same cooperative TERM path as Host
        # cancellation, keeping its pid unreaped until hard cleanup if needed.
        try:
            os.kill(proc.pid, signal.SIGTERM)
            deadline = time.monotonic() + .5
            while time.monotonic() < deadline:
                if os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
                    break
                time.sleep(.02)
        except ProcessLookupError:
            pass
    if proc.returncode is None or not _group_empty(proc.pid):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()
    # The installer lock is still held. Do not return and release it while
    # an adopted group member is alive, even after a timeout or second TERM.
    _group_empty(proc.pid, wait=True)


def _noop():
    pass


def _run(args, *, cwd, env, timeout=90, limit=32 * 1024, check=_noop):
    # Caller is the isolated qualification supervisor for installation, not
    # the installer. Read at most the cap and poll its cancellation checkpoint.
    check()
    deadline = time.monotonic() + timeout
    with _group_reaper(), subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, cwd=cwd, env=env, umask=0o077, start_new_session=True) as proc:
        data = bytearray()
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(proc.stdout, selectors.EVENT_READ)
                while True:
                    check()
                    remaining = deadline - time.monotonic()
                    _require(remaining > 0, 'qualification command timed out')
                    if not ready.select(min(.05, remaining)):
                        continue
                    chunk = os.read(proc.stdout.fileno(), min(65536, limit + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    _require(len(data) <= limit, 'qualification command exceeded its output limit')
            while proc.poll() is None:
                check()
                _require(time.monotonic() < deadline, 'qualification command timed out')
                time.sleep(.02)
            check()
            _require(_group_empty(proc.pid), 'qualification process group did not settle')
            code = proc.returncode
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
            return bytes(data)
        except BaseException:
            _stop_owned(proc)
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


def _source_archive(source, commit, env, *, expected_tree=None, check=_noop):
    def git(*args, limit=32 * 1024):
        return _run(['/usr/bin/git', *args], cwd=source, env=env, limit=limit, check=check)

    _require(_COMMIT.fullmatch(commit) is not None and git('rev-parse', 'HEAD').strip() == commit.encode('ascii')
             and not git('status', '--porcelain'), 'expected a clean checkout at the exact requested commit')
    listing = git('ls-tree', '-rz', commit, '--', *_INPUTS, limit=1024**2)
    if expected_tree is not None:
        _require(_sha(listing.replace(b'\0', b'\n')) == expected_tree, 'approved Git source tree differs')
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


def _build(python, root, members, env, *, check=_noop):
    source, wheels = root / 'src', root / 'wheels'
    source.mkdir(parents=True)
    wheels.mkdir()
    for name, data in members.items():
        check()
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, data)
    _run([python, '-IB', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'wheel', '--no-index',
          '--no-deps', '--no-build-isolation', '--no-cache-dir', '--wheel-dir', wheels, source],
         cwd=root, env=env, check=check)
    paths = tuple(wheels.iterdir())
    _require(len(paths) == 1 and re.fullmatch(r'framivra-[A-Za-z0-9_.+]+-py3-none-any\.whl', paths[0].name),
             'expected one pure Framivra kernel wheel')
    return paths[0].name, _read(paths[0], _WHEEL_LIMIT)


def _build_tools(python, root, env, *, check=_noop):
    return _json(_run([python, '-IB', '-c', 'import importlib.metadata as m,json,sys; '
                     'print(json.dumps(dict(python=sys.version.split()[0], '
                     'pip=m.version("pip"),setuptools=m.version("setuptools"),wheel=m.version("wheel"))))'],
                      cwd=root, env=env, check=check))


def build_approved_wheel(python, scratch, source, commit, *, source_tree_sha256, build_tools,
                         wheel_filename, wheel_bytes, wheel_sha256, check):
    """Consumer of a maintainer's prior inputs/outputs, not a catalog generator."""
    env = _env(scratch)
    members = _source_archive(source, commit, env, expected_tree=source_tree_sha256, check=check)
    _require(_build_tools(python, scratch, env, check=check) == build_tools, 'approved preview build tool versions differ')
    filename, wheel = _build(python, scratch / 'build', members, env, check=check)
    _require(filename == wheel_filename and len(wheel) == wheel_bytes and _sha(wheel) == wheel_sha256,
             'offline wheel differs from independently approved bytes')
    return wheel


def build_candidate(source, commit, output, *, python):
    """Emit unapproved candidate bytes, not a pin or managed install receipt."""
    _require(not output.is_relative_to(source) and not source.is_relative_to(output),
             'candidate output must be outside the source checkout')
    with tempfile.TemporaryDirectory(prefix='.framivra-build-', dir=output.parent) as tmp:
        root = Path(tmp)
        env = _env(root)
        members = _source_archive(source, commit, env)
        tools = _build_tools(python, root, env)
        filename, wheel = _build(python, root / 'one', members, env)
        _require(_build(python, root / 'two', members, env) == (filename, wheel), 'offline wheel build was not reproducible')
        launcher = members['scripts/preview_window_wheel.py']
        identity = _run(['/usr/bin/git', 'ls-tree', '-r', commit, '--', *_INPUTS], cwd=source, env=env, limit=1024**2)
        proposal = {'schema': 'framivra.preview-supplier-candidate/v1', 'commit': commit,
                    'sourceTreeSha256': _sha(identity), 'wheelFilename': filename,
                    'launcherSha256': _sha(launcher), 'wheelSha256': _sha(wheel),
                    'wheelBytes': len(wheel), 'buildTools': tools}
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


def _require_unprivileged_linux():
    _require(sys.platform == 'linux' and os.geteuid() != 0, 'ordinary fixed wheel requires unprivileged Linux')


def _verify_pins(launcher_sha256, wheel_sha256, wheel_filename):
    _require_unprivileged_linux()
    _require(all(type(digest) is str and _HEX.fullmatch(digest) for digest in (launcher_sha256, wheel_sha256))
             and type(wheel_filename) is str
             and re.fullmatch(r'framivra-[A-Za-z0-9_.+]+-py3-none-any\.whl', wheel_filename),
             'expected independently approved launcher/wheel identities')


def _verified_profile(profile, launcher_sha256, wheel_sha256, *, staged=True):
    info = profile.lstat()
    _require(profile.resolve(strict=True) == profile and stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
             and not info.st_mode & 0o077, 'preview profile conflicts with an unowned or nonprivate directory')
    exact = {'vtx-preview', 'kernel.whl', 'kernel.sha256', 'runtime'}
    if not staged:
        exact.remove('runtime')
    _require({item.name for item in profile.iterdir()} == exact, 'preview profile conflicts with unexpected entries')
    launcher = profile / 'vtx-preview'
    _require(stat.S_IMODE(launcher.lstat().st_mode) == 0o500
             and _sha(_read(launcher, 256 * 1024)) == launcher_sha256
             and _sha(_read(profile / 'kernel.whl', _WHEEL_LIMIT)) == wheel_sha256
             and _read(profile / 'kernel.sha256', 65) == (wheel_sha256 + '\n').encode('ascii'),
             'preview profile conflicts with approved artifact bytes')


def _qualify_profile(root, profile, *, wheel_filename, launcher_sha256, wheel_sha256, python,
                     fresh=None, expected_projection=None, check=_noop):
    def run(args, **kw):
        return _run(args, check=check, **kw)

    home, project = root / 'home', root / 'fixture'
    for directory in (home, project):
        directory.mkdir(mode=0o700)
    env = _env(home)
    site = None
    if fresh is not None:
        launcher, wheel = fresh
        _require((_sha(launcher), _sha(wheel)) == (launcher_sha256, wheel_sha256),
                 'candidate does not match independently approved launcher and wheel')
        profile.mkdir(mode=0o700)
        distribution = root / wheel_filename
        _write(distribution, wheel)
        _write(profile / 'vtx-preview', launcher, 0o500)
        _write(profile / 'kernel.whl', wheel)
        _write(profile / 'kernel.sha256', (wheel_sha256 + '\n').encode('ascii'))
        _verified_profile(profile, launcher_sha256, wheel_sha256, staged=False)
        site = root / 'site'
        run([python, '-IB', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install', '--no-index',
              '--no-deps', '--no-compile', '--target', site, distribution], cwd=root, env=env)
    else:
        _verified_profile(profile, launcher_sha256, wheel_sha256)
    program = profile / 'vtx-preview'

    managed = ([f'--managed-preview-pins-v1={wheel_sha256}:{expected_projection}']
               if expected_projection is not None else [])
    identity_key = {'managed_identity'} if managed else set()
    identity = 'framivra.preview-window.managed-pins/v1'

    def call(*args, limit=32 * 1024):
        prefix = [] if args[0] == '--stage-from' else managed
        return _json(run([_SYSTEM_PYTHON, '-IBS', program, *prefix, *args], cwd=root, env=env, limit=limit))

    staged = call('--stage-from', site) if site is not None else None
    verified = call('--verify')
    _require(set(verified) == {'ok', 'wheel_sha256', 'runtime_projection_sha256'} | identity_key
             and (not managed or verified['managed_identity'] == identity)
             and verified['ok'] is True and verified['wheel_sha256'] == wheel_sha256
             and type(verified['runtime_projection_sha256']) is str
             and _HEX.fullmatch(verified['runtime_projection_sha256'])
             and (expected_projection is None or verified['runtime_projection_sha256'] == expected_projection)
             and (staged is None or set(staged) == (verified.keys() - identity_key) | {'installed_member_sha256'}
                  and all(staged[key] == verified[key] for key in verified.keys() - identity_key)),
             'staging and fixed-wheel verification did not match')
    capabilities = call('-t', 'unused', 'capabilities')
    _require(capabilities.get('schema') == 'framivra.timeline.read/v1', 'unexpected launcher capability schema')
    cap = capabilities['capabilities']['preview_window']
    _require(cap['schema'] == _WINDOW and cap['launcher'] == _LAUNCHER
             and cap['inline_media'] is cap['prepare_inputs'] is cap['input_epoch'] is True
             and (not managed or cap.get('managed_identity') == identity)
             and all(cap[key] == verified[key] for key in ('wheel_sha256', 'runtime_projection_sha256')),
             'ordinary fixed-wheel capability identity did not match')
    source = project / 'probe.mp4'
    run([_SYSTEM_FFMPEG, '-v', 'error', '-nostdin', '-f', 'lavfi', '-i', 'color=c=teal:s=1920x1080:r=30:d=1',
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
    wire = run([_SYSTEM_PYTHON, '-IBS', program, *managed, '-t', timeline, 'timeline', 'preview-window', '--frame', '0',
                 '--expected-content-version=' + version, '--render', '--include-media', '--prepare-inputs'],
                cwd=root, env=env, limit=_WIRE_LIMIT)
    media = root / 'decoded-probe.mp4'
    _write(media, _receipt(wire, verified, version))
    run([_SYSTEM_FFMPEG, '-v', 'error', '-xerror', '-nostdin', '-protocol_whitelist', 'file', '-f', 'mov',
          '-i', media, '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], cwd=root, env=env)
    _require(_read(timeline, 1024 * 1024) == raw and call('--verify') == verified,
             'qualification input or profile changed')
    _verified_profile(profile, launcher_sha256, wheel_sha256)
    check()
    return {'schema': 'framivra.preview-supplier/v1', 'abi': _LAUNCHER, 'launcherSha256': launcher_sha256,
            'wheelSha256': wheel_sha256, 'runtimeProjectionSha256': verified['runtime_projection_sha256']}


def qualify_candidate(artifacts, *, wheel_filename, launcher_sha256, wheel_sha256, work_parent, python):
    """Observe a qualification; do not publish the disposable profile."""
    _verify_pins(launcher_sha256, wheel_sha256, wheel_filename)
    launcher, wheel = _read(artifacts / 'vtx-preview', 256 * 1024), _read(artifacts / 'kernel.whl', _WHEEL_LIMIT)
    _require((_sha(launcher), _sha(wheel)) == (launcher_sha256, wheel_sha256),
             'candidate does not match independently approved launcher and wheel')
    with tempfile.TemporaryDirectory(prefix='framivra-ordinary-', dir=work_parent) as tmp:
        root = Path(tmp)
        return _qualify_profile(root, root / 'preview', wheel_filename=wheel_filename,
                                launcher_sha256=launcher_sha256, wheel_sha256=wheel_sha256,
                                python=python, fresh=(launcher, wheel))


def _supervise(probe, check):
    """Run the probe in an isolated single-thread supervisor; never promote there."""
    _require(threading.active_count() == 1 and threading.current_thread() is threading.main_thread(),
             'qualification supervisor requires the installer main thread')
    signals = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    parent = os.getpid()
    read_fd, write_fd = os.pipe()
    child = None
    try:
        check()
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
        try:
            child = os.fork()
            if child == 0:
                os.close(read_fd)
                cancelled = False

                def mark(_signum, _frame):
                    nonlocal cancelled
                    cancelled = True

                def checkpoint():
                    _require(not cancelled and os.getppid() == parent, 'qualification supervisor cancelled')

                try:
                    for signum in signals:
                        signal.signal(signum, mark)
                    signal.pthread_sigmask(signal.SIG_SETMASK, mask - set(signals))
                    os.setsid()
                    result = probe(checkpoint)
                    checkpoint()
                    payload = {'ok': True, 'receipt': result}
                except BaseException as error:
                    payload = {'ok': False, 'reason': str(error)[:1000]}
                try:
                    os.write(write_fd, (json.dumps(payload) + '\n').encode('ascii'))
                finally:
                    os._exit(0)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        os.close(write_fd)
        write_fd = None
        data = bytearray()
        with selectors.DefaultSelector() as ready:
            ready.register(read_fd, selectors.EVENT_READ)
            while True:
                check()
                if not ready.select(.05):
                    continue
                chunk = os.read(read_fd, 8193 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
                _require(len(data) <= 8192, 'qualification supervisor result exceeded limit')
        _, status = os.waitpid(child, 0)
        child = None
        check()
        _require(status == 0, 'qualification supervisor did not settle successfully')
        result = _json(bytes(data))
        _require(result.get('ok') is True, result.get('reason', 'qualification supervisor failed'))
        return result['receipt']
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if child not in (None, 0):
            try:
                os.kill(child, signal.SIGTERM)
            except ProcessLookupError:
                pass
            os.waitpid(child, 0)


def _publish_new(staged, target):
    # Do not use rename(): on POSIX it can replace an existing empty directory.
    # The tested ordinary profile is Linux-only, and refusal is safe everywhere.
    import ctypes

    rename = getattr(ctypes.CDLL(None, use_errno=True), 'renameat2', None)
    _require(rename is not None, 'cannot atomically publish a new preview profile')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(staged), -100, os.fsencode(target), 1):  # AT_FDCWD, RENAME_NOREPLACE
        error = ctypes.get_errno()
        raise OSError(error, 'cannot publish preview profile without replacement')


def enroll_profile(root, *, launcher_path, prepare_wheel, wheel_filename, launcher_sha256, wheel_sha256,
                   projection_sha256, python, check=_noop):
    """Installer-only operation, under the existing install lock and approved catalog.

    Publish exactly the staged and qualified directory; an existing directory is
    accepted only after independent exact-pin and ordinary render requalification.
    The caller must publish the Ready registration only on successful return.
    This is not an updater and touches neither an old profile nor old receipt.
    """
    _verify_enrollment_pins(wheel_filename, launcher_sha256, wheel_sha256, projection_sha256)
    pins = (wheel_filename, launcher_sha256, wheel_sha256, projection_sha256)
    return _enroll_profile(root, launcher_path=launcher_path, python=python, check=check,
                           select=lambda _scratch, _check: (pins, prepare_wheel))


def enroll_approved_variants(root, *, launcher_path, source, commit, source_tree_sha256, variants, python, check=_noop):
    """Choose one complete catalog record inside the existing qualification supervisor."""
    _require_unprivileged_linux()

    def select(scratch, child_check):
        tools = _build_tools(python, scratch, _env(scratch), check=child_check)
        matches = [row for row in variants if row['buildTools'] == tools]
        _require(len(matches) == 1, 'no unique release-reviewed preview build tool match')
        row, = matches
        pins = tuple(row[key] for key in ('wheelFilename', 'launcherSha256', 'wheelSha256', 'runtimeProjectionSha256'))
        _verify_enrollment_pins(*pins)

        def prepare_wheel(scratch, child_check):
            return build_approved_wheel(python, scratch, source, commit,
                source_tree_sha256=source_tree_sha256, build_tools=row['buildTools'], wheel_filename=row['wheelFilename'],
                wheel_bytes=row['wheelBytes'], wheel_sha256=row['wheelSha256'], check=child_check)

        return pins, prepare_wheel

    return _enroll_profile(root, launcher_path=launcher_path, python=python, check=check, select=select)


def _verify_enrollment_pins(wheel_filename, launcher_sha256, wheel_sha256, projection_sha256):
    _verify_pins(launcher_sha256, wheel_sha256, wheel_filename)
    _require(type(projection_sha256) is str and _HEX.fullmatch(projection_sha256), 'expected approved projection identity')


def _enroll_profile(root, *, launcher_path, python, check, select):
    info = root.lstat()
    _require(root.resolve(strict=True) == root and stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
             and not info.st_mode & 0o022, 'preview managed root is not private-owner controlled')
    target = root / 'preview'
    adopt = os.path.lexists(target)
    with tempfile.TemporaryDirectory(prefix='.preview-enrollment-', dir=root) as tmp:
        scratch = Path(tmp)
        profile = target if adopt else scratch / 'preview'
        def qualify(child_check):
            pins, prepare_wheel = select(scratch, child_check)
            wheel_filename, launcher_sha256, wheel_sha256, projection_sha256 = pins
            fresh = None
            if not adopt:
                launcher = _read(launcher_path, 256 * 1024)
                _require(_sha(launcher) == launcher_sha256, 'candidate does not match independently approved launcher')
                fresh = launcher, prepare_wheel(scratch, child_check)
            return _qualify_profile(scratch, profile, wheel_filename=wheel_filename,
                launcher_sha256=launcher_sha256, wheel_sha256=wheel_sha256, python=python,
                fresh=fresh, expected_projection=projection_sha256, check=child_check)

        receipt = _supervise(qualify, check)
        check()
        if not adopt:
            before = profile.stat()
            _publish_new(profile, target)
            after = target.lstat()
            _require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino), 'qualified preview profile was replaced')
        return receipt


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
