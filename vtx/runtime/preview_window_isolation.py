"""Local Linux candidate profile: one Bubblewrap namespace per preview request.

No host render scratch, no home/project tree mount, no unisolated fallback.
Only fixed portable-v2 input leaves may use private host tmpfs staging.
The byte ceiling is for tmpfs pages, not inodes, process memory or concurrency.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import selectors
import stat
import subprocess
import sys
import time

from . import fstxn

_BWRAP = Path('/usr/bin/bwrap')
_PROJECT_FILE_COUNT = 128
_STDERR_LIMIT = 16 * 1024
_EPOCH_FILESYSTEMS = frozenset(('ext4', 'xfs', 'btrfs', 'tmpfs', 'overlay'))
_BOOT = ('import sys;sys.path.insert(0,sys.argv.pop(1));'
         'from vtx.runtime.preview_window_isolation import check_entry_fds;check_entry_fds();'
         'from vtx.cli.timeline_preview_window import _isolated_worker;'
         'sys.exit(_isolated_worker(sys.argv[1:]))')


class IsolationError(RuntimeError):
    pass


def available():
    try:
        info = _BWRAP.lstat()
    except OSError:
        return False
    return (sys.platform == 'linux' and stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and not info.st_mode & 0o6022 and bool(info.st_mode & 0o111))


def _token(info):
    return (*fstxn.entry_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _pin_binding(stack, path, root, *, directory=False, single_link=False, root_identity=None):
    kind = stat.S_IFDIR if directory else stat.S_IFREG
    identity = fstxn.cleanup_identity(path)
    if identity is None or identity[-1][2] != kind:
        raise IsolationError('binding requires its expected canonical type (missing or wrong file type)')
    if root_identity is not None and identity[:len(root_identity)] != root_identity:
        raise IsolationError('binding project root identity changed')
    with fstxn.pinned_parent(path, create=False, root=root) as (parent, name, canonical):
        if canonical != path or fstxn.entry_identity(os.fstat(parent)) != identity[-2]:
            raise IsolationError('binding requires its expected canonical type')
        flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else os.O_NONBLOCK)
        fd = os.open(name, flags, dir_fd=parent)
    stack.callback(os.close, fd)
    info = os.fstat(fd)
    if (fstxn.entry_identity(info) != identity[-1] or (single_link and info.st_nlink != 1)
            or fstxn.cleanup_identity(path) != identity):
        raise IsolationError('binding file changed or has multiple links')
    token = _token(info)

    def current():
        if fstxn.cleanup_identity(path) != identity or _token(path.lstat()) != token or _token(os.fstat(fd)) != token:
            raise IsolationError('binding input changed during execution')

    return fd, current


def identity_digest(*fields):
    payload = json.dumps(fields, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                         allow_nan=False).encode('ascii')
    return 'sha256:' + hashlib.sha256(payload).hexdigest()


def _binding_blocks(fd, guard, limit, check):
    check()
    guard()
    size = os.fstat(fd).st_size
    if not 0 <= size <= limit:
        raise IsolationError('input exceeds the full-byte verification limit')
    offset = 0
    while offset < size:
        check()
        block = os.pread(fd, min(1024 * 1024, size - offset), offset)
        if not block:
            raise IsolationError('binding input changed during full-byte verification')
        offset += len(block)
        yield block
    guard()


def binding_digest(fd, guard, limit, check):
    """Hash the full original descriptor without changing the bind FD offset."""
    h, size = hashlib.sha256(), 0
    for block in _binding_blocks(fd, guard, limit, check):
        h.update(block)
        size += len(block)
    return size, h.hexdigest()


def binding_read(fd, guard, limit, check):
    """Read the full original descriptor into memory, with a small caller-supplied limit."""
    return b''.join(_binding_blocks(fd, guard, limit, check))


def _scan_tree(path, check):
    # Only selected provider trees; never scan the project or the user's home.
    # Borrowed directory mounts must not expose host IPC files or special nodes.
    def unreadable(error):
        raise error

    if path.resolve(strict=True) != path or not path.is_dir():
        raise IsolationError('provider directory is not canonical')
    for root, dirs, files in os.walk(path, onerror=unreadable, followlinks=False):
        check()
        for name in dirs + files:
            info = (Path(root) / name).lstat()
            if stat.S_ISLNK(info.st_mode):
                raise IsolationError('unsupported symbolic link in provider directory')
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise IsolationError('special entry in provider directory')


def _managed_environment(source):
    from .managed import configure_environment

    env = {}
    return env if configure_environment(source=source, environment=env) else {}


def managed_font():
    return _managed_environment(Path(__file__).resolve().parents[2]).get('FONTCONFIG_FILE')


def _private_directory(path):
    if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_dir():
        raise IsolationError('HOME/XDG root must be a canonical absolute directory')
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise IsolationError('HOME/XDG root must be caller-owned, not group/world-writable')
    return path


def _mount_view():
    real = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        before, after = line.split(' - ', 1)
        fields = before.split()
        name = fields[4].replace(r'\040', ' ').replace(r'\011', '\t').replace(r'\012', '\n').replace(r'\134', '\\')
        real[name] = (fields[5].split(','), after.split()[0])
    return real


def _project_mounts(files):
    # Use exactly one fresh mountinfo view per observation. Walk outward from
    # each file, so even a mount on a single leaf overrides its parent. Never
    # reuse this index across the before/after checks of a request.
    mounts = {}
    for name, (_, kind) in _mount_view().items():
        root = Path(name)
        mounts.setdefault(root, (root, kind))
    found = {}
    for path in files:
        root = path
        while root not in mounts and root != root.parent:
            root = root.parent
        provider = mounts.get(root)
        if provider is None or provider[1] not in _EPOCH_FILESYSTEMS:
            raise IsolationError('input proof requires a supported local project filesystem')
        found[path] = provider
    return found


def _profile(check, *, svg=False):
    from ..pluginlib.config import config_path

    if os.geteuid() == 0 or Path(sys.base_prefix) != Path('/usr'):
        raise IsolationError('requires unprivileged system Python or its verified managed venv')
    source = Path(__file__).resolve().parents[2]
    home = _private_directory(Path.home())
    xdg = {}
    for key, default in (('XDG_CONFIG_HOME', home / '.config'), ('XDG_DATA_HOME', home / '.local/share')):
        xdg[key] = _private_directory(Path(os.environ[key])) if os.environ.get(key) else default
        if xdg[key].resolve() != xdg[key]:
            raise IsolationError('linked Fontconfig lookup roots are unsupported')
    # Do not inspect user config content or mount a HOME/XDG directory. Default
    # system and recognized installed Fontconfig entry files remain same-source.
    if any(p.exists() or p.is_symlink() for p in (home / '.fonts.conf', home / '.fonts.conf.d',
                                                 xdg['XDG_CONFIG_HOME'] / 'fontconfig', Path('/etc/fonts/local.conf'))):
        raise IsolationError('custom Fontconfig configuration is outside this first profile')
    managed = _managed_environment(source)
    verified = bool(managed)
    python = Path(sys.executable)
    approved_font = managed.get('FONTCONFIG_FILE') if verified else None
    if os.environ.get('FONTCONFIG_FILE') != approved_font:
        raise IsolationError('Fontconfig override requires the existing verified managed profile')
    original_config = config_path(source)
    expected_config = Path(managed['VTX_CONFIG']) if verified else source / 'config.local.json'
    if original_config != expected_config:
        raise IsolationError('independent config path is outside this first profile')
    # Do not hide configured external declarations from the static plugin gate.
    if original_config.is_file():
        with original_config.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise IsolationError('kernel configuration exceeds the profile limit')
        config = json.loads(raw)
        if not isinstance(config, dict) or config.get('external_apps') or config.get('external_plugins'):
            raise IsolationError('external component roots are outside this first profile')
    profile_files = []
    if verified:
        prefix = Path(sys.prefix)
        if python.parent != prefix / 'bin':
            raise IsolationError('interpreter differs from the managed venv')
        link = python.resolve(strict=True)
        if link != python and not link.is_relative_to('/usr/bin'):
            raise IsolationError('unsupported managed Python link target')
        profile_files.extend([(link, python), (prefix / 'pyvenv.cfg', prefix / 'pyvenv.cfg')])
        # Read the same managed fonts.conf bytes in situ; writes to its old
        # external cache directory cannot traverse the read-only namespace.
    elif not python.is_relative_to('/usr/bin') or not python.resolve(strict=True).is_relative_to('/usr/bin'):
        raise IsolationError('unverified independent Python is not supported')
    if original_config.is_file():
        profile_files.append((original_config, original_config))
    # System roots here are trusted distribution providers, not a scan of the
    # host package database. Refuse runtime submounts under these fixed roots.
    system = [Path(p) for p in ('/usr/lib', '/usr/lib64', '/etc/fonts',
                               '/usr/share/fonts', '/usr/share/fontconfig') if Path(p).exists()]
    mounted = _mount_view()
    for path in system:
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or any(path in Path(mount).parents for mount in mounted)):
            raise IsolationError('fixed distribution subtree is not an approved system provider')
    trees = [(path, True) for path in system]
    binaries = ('bwrap', 'setpriv', 'bash', 'ffmpeg', 'ffprobe', 'ldd', 'fc-list', 'fc-conflist')
    svg_binaries = ('inkscape', 'fc-scan') if svg else ()
    binaries += svg_binaries
    for path in [*(Path('/usr/bin') / name for name in binaries), *([python] if not verified else [])]:
        real = path.resolve(strict=True)
        info = real.stat()
        if (not real.is_relative_to('/usr/bin') or not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0 or info.st_mode & 0o6022):
            raise IsolationError('executable is not a trusted system provider')
        if path.name in svg_binaries and real != path:
            raise IsolationError('SVG identity requires the fixed system executable path')
        profile_files.append((real, path))
    for name in ('/etc/ld.so.cache', '/etc/localtime'):
        path = Path(name)
        if path.exists() and (target := path.resolve(strict=True)) == path:
            profile_files.append((target, path))
    trees.append((source / 'vtx', False))
    extra_trees = (source / 'plugins',)
    if not svg:
        extra_trees += (Path('/usr/local/share/fonts'), home / '.fonts', xdg['XDG_DATA_HOME'] / 'fonts')
    for path in extra_trees:
        if path.exists() or path.is_symlink():
            trees.append((path, False))
    if verified:
        trees.append((Path(approved_font).parent, False))
    for name in ('VERSION', 'pyproject.toml'):
        path = source / name
        if path.is_file():
            profile_files.append((path, path))
    for path, is_system in trees:
        if not is_system:
            _scan_tree(path, check)
    env = {key: value for key, value in os.environ.items()
           if key in ('LANG', 'LANGUAGE', 'VTX_VIDEO_SEGMENTS', 'VTX_FILTER_THREADS', 'VTX_PASS_CACHE')
           or key.startswith('LC_')}
    env.update(PATH='/usr/bin:/bin', HOME=str(home), VTX_GFX='cdp', VTX_CONFIG=str(original_config))
    env.update({key: str(path) for key, path in xdg.items()})
    if approved_font is not None:
        env['FONTCONFIG_FILE'] = approved_font
    return source, python, trees, profile_files, env


def _read_worker(argv, descriptors, check, limit, *, keep_on_signal=False, framed=None):
    """Capture only enclosed pipes. On success Popen.wait includes PID-1 exit."""
    child = subprocess.Popen(argv, pass_fds=descriptors, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    try:
        output, errors = bytearray(), bytearray()
        try:
            with selectors.DefaultSelector() as sel:
                sel.register(child.stdout, selectors.EVENT_READ, (output, limit - 1))
                sel.register(child.stderr, selectors.EVENT_READ, (errors, _STDERR_LIMIT))
                while sel.get_map():
                    check()
                    for key, _ in sel.select(.05):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            sel.unregister(key.fileobj)
                            continue
                        buffer, cap = key.data
                        if buffer is output and framed is not None:
                            framed.feed(chunk)
                            continue
                        room = cap - len(buffer)
                        if buffer is output and len(chunk) > room:
                            raise IsolationError('isolated JSON exceeds the wire limit')
                        buffer.extend(chunk[:room])
            while child.poll() is None:
                check()
                time.sleep(.025)
            check()
        except BaseException as primary:
            status = child.poll()
            if status is None or (keep_on_signal and status < 0):
                # A running or externally signalled monitor does not prove
                # namespace PID-1 exit. The Host must retain range admission.
                primary._vtx_isolation_unsettled = True
            if status is None:
                try:
                    child.kill()
                    child.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass  # Retain the original failure, never publish output.
            raise
        return child.returncode, framed.finish() if framed is not None else bytes(output), bytes(errors)
    finally:
        child.stdout.close()
        child.stderr.close()


def execute(stack, path, files, start, version, prepare_fingerprint, bytes_limit, wire_limit, check,
            *, root_identity, qualification=None, recheck=None, svg_mode=None, caption=None, read_caption=None,
            program_paths=(), first_frame=None):
    if first_frame is not None and (qualification is None or recheck is None or svg_mode is not None or program_paths):
        raise IsolationError('first frame requires the qualified ordinary window supervisor')
    if program_paths and (qualification is None or svg_mode == 'svg-read'):
        raise IsolationError('program audio requires an explicitly qualified fixed wheel media mode')
    if svg_mode is not None and (svg_mode not in ('svg-read', 'svg-media') or qualification is None):
        raise IsolationError('SVG warm projection requires a fixed wheel mode')
    if svg_mode == 'svg-read' and caption is not None:
        raise IsolationError('standalone warm SVG read does not admit external subtitles')
    if not available():
        raise IsolationError('trusted Bubblewrap is unavailable')
    frame_paths = ()
    if svg_mode is not None:
        from . import preview_window_portable_svg as portable

        if version == portable.TIMELINE_VERSION:
            frame_paths = portable.frames(path, files)
    projected_files = set(frame_paths)
    direct_files = [p for p in files if p not in projected_files]
    if len(direct_files) + bool(frame_paths) > _PROJECT_FILE_COUNT:
        raise IsolationError('too many separately anchored project files')
    source, python, trees, profile_files, env = _profile(check, svg=svg_mode is not None)
    if qualification is not None and (env.get('FONTCONFIG_FILE') is not None
            or any(dest == source / 'config.local.json' for _, dest in profile_files)):
        raise IsolationError('input proof requires the fixed wheel profile without independent configuration')
    root = path.parent
    mount_paths = (*files, root) if frame_paths else files
    project_mounts = _project_mounts(mount_paths) if qualification is not None else None
    scratch = root / 'cache' / 'preview'
    planned = [p for p, _ in trees] + [dest for _, dest in profile_files] + [Path('/proc'), Path('/dev')]
    if any(p == root or root in p.parents or p in root.parents for p in planned):
        raise IsolationError('project overlaps a fixed runtime mount')
    if any(p == scratch or scratch in p.parents or p in scratch.parents for p in files):
        raise IsolationError('project input overlaps the isolated scratch mount')
    argv = [str(_BWRAP), '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
            '--disable-userns', '--cap-drop', 'ALL', '--die-with-parent', '--as-pid-1', '--clearenv',
            '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--remount-ro', '/proc', '--dev-bind', '/dev/null', '/dev/null',
            '--dev-bind', '/dev/urandom', '/dev/urandom']
    if svg_mode is not None:
        # The unchanged production identity commands create a temporary private
        # Fontconfig/Inkscape profile. Keep even that work inside tmpfs.
        env['TMPDIR'] = str(scratch)
    for key, val in sorted(env.items()):
        argv.extend(('--setenv', key, val))
    fds, guards, inputs = [], [], {}

    def bind(src, dest, *, directory=False, project_file=False):
        fd, guard = _pin_binding(stack, src, root if project_file else src.parent,
                                 directory=directory, single_link=project_file,
                                 root_identity=root_identity if project_file else None)
        fds.append(fd)
        guards.append(guard)
        if not directory and (qualification is not None or dest == caption):
            inputs[dest] = (src, fd, guard, files[dest] if project_file else ('provider',))
        argv.extend(('--ro-bind-fd', str(fd), str(dest)))
        return fd

    for tree, _ in trees:
        bind(tree, tree, directory=True)
    for src, dest in profile_files:
        bind(src, dest)
    project_fds = {item: bind(item, item, project_file=True) for item in direct_files}
    if caption is not None:
        # Single-link checks do not detect two names for one file via a mount.
        caption_identity = fstxn.entry_identity(os.fstat(project_fds[caption]))
        if any(fstxn.entry_identity(os.fstat(fd)) == caption_identity
               for item, fd in project_fds.items() if item != caption):
            raise IsolationError('external subtitles cannot share another input role')

    def observe_caption():
        if caption is not None:
            _, fd, guard, _ = inputs[caption]
            return read_caption(fd, guard)
        return None, None

    def full_inputs(caption_observation):
        observed, total = [], 0
        for dest, (src, fd, guard, roles) in sorted(inputs.items()):
            project_file = dest in files
            limit = bytes_limit - total if project_file and dest != path else bytes_limit
            if dest == caption:
                # The same bound read supplies the receipt and the epoch.
                size, sha = caption_observation
                if size > limit:
                    raise IsolationError('input exceeds the full-byte verification limit')
            else:
                size, sha = binding_digest(fd, guard, limit, check)
            if project_file and dest != path:
                total += size
            if dest == path and 'sha256:' + sha != version:
                raise IsolationError('timeline changed before isolated execution')
            observed.append((str(src), str(dest), roles, size, sha))
        return observed, total

    def run(mode, fingerprint, *extra, framed=None):
        command = [*argv, '--size', str(bytes_limit), '--tmpfs', str(scratch), '--remount-ro', '/',
                   '--chdir', str(root), '--', '/usr/bin/setpriv', '--no-new-privs',
                   str(python), '-I', '-B', '-S', '-c', _BOOT,
                   str(source), str(path), str(start), version, fingerprint, mode, *extra]
        for guard in guards:
            guard()
        worker_options = {'keep_on_signal': True} if frame_paths else {}
        if framed is not None:
            worker_options['framed'] = framed
        status, output, errors = _read_worker(command, tuple(fds), check, wire_limit, **worker_options)
        if frame_paths and status < 0:
            failure = IsolationError('isolated worker did not return a settled result')
            failure._vtx_isolation_unsettled = True
            raise failure
        if errors and framed is None:
            sys.stderr.write(errors.decode('utf-8', errors='replace'))
        for guard in guards:
            guard()
        if status not in (0, 1) or not output:
            raise IsolationError('isolated worker did not return a settled result')
        result = json.loads(output) if framed is None else output
        if not isinstance(result, dict) or result.get('ok') is not (status == 0):
            raise IsolationError('isolated worker result disagrees with process status')
        return status, result

    if program_paths:
        from ..pipeline import preview_program_audio as program

        if any(os.fstat(project_fds[path]).st_size > program._MAX_SOURCE_BYTES for path in program_paths):
            raise program.ProgramAudioError('program WAV exceeds its per-source byte limit')
    caption_observation = observe_caption()
    pinned, total = full_inputs(caption_observation) if qualification is not None else (None, 0)
    program_args = ()
    if program_paths:
        program_args = (program.manifest(program_paths, {dest: (size, sha) for _, dest, _, size, sha in pinned}),)
    frame_identities, frame_observations, verify_frames = set(), [], None
    if frame_paths:
        portable.verify_manifest(root, pinned)
        other_identities = {fstxn.entry_identity(os.fstat(fd)) for fd in fds}
        staged, verify_stage, verify_frames, frame_identities, frame_observations = stack.enter_context(
            portable.snapshot(root, root_identity, frame_paths, files, project_mounts,
                              other_identities, bytes_limit - total, check))
        if _project_mounts(mount_paths) != project_mounts:
            raise IsolationError('portable-v2 project mounts changed during frame projection')
        fds.append(staged)
        guards.append(verify_stage)
        argv.extend(('--ro-bind-fd', str(staged), str(frame_paths[0].parent)))

    def verify_project(observation):
        if full_inputs(observation)[0] != pinned or _project_mounts(mount_paths) != project_mounts:
            raise IsolationError('binding input changed during isolated execution')
        if verify_frames is not None:
            verify_frames()
            if _project_mounts(mount_paths) != project_mounts:
                raise IsolationError('portable-v2 project mounts changed during frame verification')

    if svg_mode is not None:
        from .preview_window_svg import PACK_LIMIT, pack_selector

        status, result = run('svg-plan', caption_observation[1] or '-')
        if status:
            if verify_frames is not None:
                verify_project(observe_caption())
            return result
        if (set(result) != {'ok', 'fingerprint', 'pack'}
                or not isinstance(result['fingerprint'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', result['fingerprint'])):
            raise IsolationError('invalid isolated SVG input plan')
        relative = result['pack']
        pack = pack_selector(root, relative)
        if pack in files:
            raise IsolationError('project declares its warm pack as a source or dependency')
        pack_mount = _project_mounts((pack,))
        fd, guard = _pin_binding(stack, pack, root, single_link=True, root_identity=root_identity)
        pack_identity = fstxn.entry_identity(os.fstat(fd))
        if (pack_identity in frame_identities
                or any(fstxn.entry_identity(os.fstat(bound_fd)) == pack_identity for bound_fd in fds)):
            raise IsolationError('SVG warm pack cannot share another input role')
        pack_observed = binding_digest(fd, guard, PACK_LIMIT, check)
        fds.append(fd)
        guards.append(guard)
        argv.extend(('--ro-bind-fd', str(fd), str(pack)))
        fingerprint = result['fingerprint']
        mode = 'svg-media-program-audio' if program_args else svg_mode
        status, result = run(mode, fingerprint, relative, *program_args)
    else:
        mode = 'program-audio' if program_args else 'input-epoch' if qualification is not None else 'unqualified'
        fingerprint = prepare_fingerprint(caption_observation[1])
        if first_frame is None:
            status, result = run(mode, fingerprint, *program_args)
        else:
            from . import preview_window_stream as stream

            nonce, expected_position, reverify_profile, emit = first_frame
            if not stream.NONCE.fullmatch(nonce):
                raise IsolationError('invalid first-frame nonce')
            end, segment_end, rate = expected_position(env)
            first, first_environment = None, None

            def first_record(record):
                nonlocal first, first_environment
                check()
                for guard in guards:
                    guard()
                verified = stream.verify_first(record, start=start, end=end, segment_end=segment_end, rate=rate,
                                               version=version, fingerprint=fingerprint, limit=wire_limit)
                reverify_profile()
                observation = observe_caption()
                recheck(observation[1])
                verify_project(observation)
                for guard in guards:
                    guard()
                check()
                emit(verified, check)
                first, first_environment = verified, record['environment']

            framed = stream.Decoder(wire_limit, nonce, first_record)
            status, result = run('first-frame:' + nonce, fingerprint, framed=framed)
            if status:
                raise IsolationError('isolated window did not settle successfully')
            stream.verify_final(result, first, first_environment)

    if ('input_epoch' in result or 'warm' in result
            or ('_input_environment' in result and (svg_mode == 'svg-read' or pinned is None or status != 0))):
        raise IsolationError('unexpected isolated input proof')
    if program_args and status == 0:
        if result.get('program_audio') is not True:
            raise IsolationError('isolated result is missing its program audio proof')
    elif 'program_audio' in result:
        raise IsolationError('unexpected isolated program audio proof')
    observed_environment = result.pop('_input_environment', None)
    final_caption = observe_caption()
    if recheck is not None:
        recheck(final_caption[1])
    verify_full = pinned is not None and (status == 0 or svg_mode is not None)
    if verify_full:
        verify_project(final_caption)
    if svg_mode is not None and (binding_digest(fd, guard, PACK_LIMIT, check) != pack_observed
                                 or _project_mounts((pack,)) != pack_mount):
        raise IsolationError('SVG warm pack changed during isolated consumption')
    if verify_full:
        for binding_guard in guards:
            binding_guard()
    elif caption is not None:
        inputs[caption][2]()  # Keep the caption anchored through the final receipt check.
    # Only successful, settled media can carry an epoch. The warm-read proof
    # remains a separate PNG digest response, never a media authorization.
    if pinned is not None and status == 0 and svg_mode != 'svg-read':
        if not isinstance(observed_environment, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', observed_environment):
            raise IsolationError('isolated environment could not be verified')
        epoch_inputs = sorted(pinned + frame_observations) if frame_paths else pinned
        fields = (qualification, epoch_inputs, [str(p) for p, _ in trees], observed_environment)
        if svg_mode == 'svg-media':
            # The rechecked planner fingerprint includes the SVG tool closure;
            # pack_observed is the entire original bound descriptor, not just
            # a digest of the staged PNGs or of the requested interval.
            fields += (('svg-pack/v1', relative, *pack_observed, fingerprint),)
            result['warm'] = True
        domain = ('framivra.preview-window.inputs/program-audio/v1' if program_args
                  else 'framivra.preview-window.inputs/v1')
        result['input_epoch'] = identity_digest(domain, *fields)
    return result


def check_entry_fds():
    """Check inheritance at bootstrap, before kernel imports open their own files."""
    for entry in os.listdir('/proc/self/fd'):
        fd = int(entry)
        if fd <= 2:
            continue
        try:
            os.fstat(fd)
        except OSError as error:
            import errno
            if error.errno == errno.EBADF:  # listdir's own already-closed fd
                continue
            raise
        raise IsolationError('worker inherited a descriptor outside stdio')


def check_mount(scratch, bytes_limit):
    """Validate the live isolation, not ambient markers or advertised flags."""
    if os.getpid() != 1 or os.geteuid() == 0:
        raise IsolationError('worker is not unprivileged namespace PID 1')
    real = _mount_view()
    devices = {'/dev/null', '/dev/urandom'}  # the two explicit nonpersistent device nodes
    if ({p for p, (opts, _) in real.items() if 'rw' in opts} - devices != {str(scratch)}
            or real.get(str(scratch), (None, None))[1] != 'tmpfs'):
        raise IsolationError('worker mount view is not read-only outside its scratch')
    info = os.statvfs(scratch)
    if info.f_blocks * info.f_frsize != bytes_limit:
        raise IsolationError('worker scratch capacity differs from the requested byte ceiling')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    if status.get('NoNewPrivs', '').strip() != '1' or int(status.get('CapEff', '1'), 16):
        raise IsolationError('worker has unexpected privilege state')
