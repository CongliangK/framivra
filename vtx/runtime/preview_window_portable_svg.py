"""Fixed portable-v2 SVG input projection, private to the Linux warm-window path."""
from contextlib import ExitStack, contextmanager
import hashlib
import os
from pathlib import Path
import stat
import sys

from . import fstxn
from .preview_window_isolation import IsolationError, _mount_view, _pin_binding, _token

# Timeline: templates/demo-timeline-svg/benchmark/portable-v2.json. Manifest and
# ordered SVG byte fold: the existing fixed reference in gfx_svg_win_shell.py.
TIMELINE_VERSION = 'sha256:a7008c2e5b2833da13021c3a1ccb46a09068ca1775b6724328563d2d37f7d105'
_MANIFEST = 'gfx/demo.frames.json'
MANIFEST_BYTES = (29140, 'e500ae49644014da611621ad5ce1c9d5e3e30f90cd6f7ee4cdb06a376235e14d')
_FRAME_DIR = 'gfx/frames-svg'
_FRAME_COUNT, _FRAME_LIMIT, _TOTAL = 900, 5 * 1024, 2326452
_FRAME_SHA = '70ddd6d89d21e9051c4479364cc69003c6612c9da3b3c7caaaa11f25b77c79bc'
_SHM = Path('/dev/shm')
_SLOT_PREFIX = 'framivra-preview-portable-v2-uid-'
_DIR_FLAGS = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)


def frames(path, files):
    root = path.parent
    manifest = root / _MANIFEST
    fixed = tuple(root / _FRAME_DIR / f'f{i:04d}.svg' for i in range(_FRAME_COUNT))
    actual = {p for p, roles in files.items() if any(r[0] == 'svg-frame' for r in roles)}
    if (path.name != 'timeline.json' or files.get(path) != [('timeline',)]
            or files.get(manifest) != [('svg-manifest', _MANIFEST)] or actual != set(fixed)
            or any(files[p] != [('svg-frame', _MANIFEST, p.relative_to(root).as_posix())] for p in fixed)):
        raise IsolationError('portable-v2 requires exactly the fixed SVG input roles')
    directory = fixed[0].parent
    if any(p == directory or p in directory.parents or directory in p.parents for p in files.keys() - actual):
        raise IsolationError('portable-v2 frame projection overlaps another input role')
    return fixed


def verify_manifest(root, observed):
    if not any(dest == str(root / _MANIFEST) and (size, sha) == MANIFEST_BYTES
               for _, dest, _, size, sha in observed):
        raise IsolationError('portable-v2 manifest does not match the frozen bytes')


def _read_at(parent, name, device, limit):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (not stat.S_ISREG(info.st_mode) or info.st_dev != device or info.st_nlink != 1
            or not 0 < info.st_size <= limit):
        raise IsolationError('portable-v2 frame has an unsupported filesystem, type, links or size')
    token = _token(info)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        if _token(os.fstat(fd)) != token:
            raise IsolationError('portable-v2 frame changed before anchored read')
        data = os.pread(fd, info.st_size + 1, 0)
        if (len(data) != info.st_size or _token(os.fstat(fd)) != token
                or _token(os.stat(name, dir_fd=parent, follow_symlinks=False)) != token):
            raise IsolationError('portable-v2 frame changed during anchored read')
        return token, data
    finally:
        os.close(fd)


@contextmanager
def _stage(root, project_device, other_identities):
    # A root-owned sticky tmpfs, not TMPDIR, the project, or the launcher
    # profile. Same-UID code is in the trusted operator boundary of the launcher.
    if (root == _SHM or root in _SHM.parents or _SHM in root.parents
            or _mount_view().get(str(_SHM), (None, None))[1] != 'tmpfs'):
        raise IsolationError('portable-v2 requires a separate system staging tmpfs')
    with fstxn.pinned_parent(_SHM, root=_SHM.parent, create=False) as (parent, leaf, canonical):
        if canonical != _SHM:
            raise IsolationError('portable-v2 staging tmpfs is not canonical')
        shm = os.open(leaf, _DIR_FLAGS, dir_fd=parent)
    with ExitStack() as owned:
        owned.callback(os.close, shm)
        info = os.fstat(shm)
        if (info.st_uid != 0 or not info.st_mode & stat.S_ISVTX or info.st_dev == project_device
                or any(device == info.st_dev for device, _, _ in other_identities)
                or fstxn.entry_identity(_SHM.lstat()) != fstxn.entry_identity(info)):
            raise IsolationError('portable-v2 staging tmpfs is not a distinct trusted provider')
        name = f'{_SLOT_PREFIX}{os.geteuid()}'
        try:
            os.mkdir(name, 0o700, dir_fd=shm)
        except FileExistsError:
            # An uncatchable exit cannot accumulate slots. Never open, reuse
            # or remove an entry left by a different invocation or owner.
            raise IsolationError('portable-v2 staging slot is occupied') from None
        fd = os.open(name, _DIR_FLAGS, dir_fd=shm)
        owned.callback(os.close, fd)
        identity = fstxn.entry_identity(os.fstat(fd))
        parent_identity = fstxn.cleanup_identity(_SHM)
        entries = {}

        def current(mode):
            if (parent_identity is None or parent_identity[-1] != fstxn.entry_identity(info)
                    or fstxn.cleanup_identity(_SHM / name) != (*parent_identity, identity)
                    or stat.S_IMODE(os.fstat(fd).st_mode) != mode):
                raise IsolationError('portable-v2 staging directory changed')

        def put(leaf, data):
            out = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=fd)
            try:
                entries[leaf] = fstxn.entry_identity(os.fstat(out))
                if os.write(out, data) != len(data):
                    raise IsolationError('portable-v2 staging write incomplete')
                os.fchmod(out, 0o400)
            finally:
                os.close(out)

        failure = None
        try:
            if os.fstat(fd).st_uid != os.geteuid() or identity[0] != info.st_dev:
                raise IsolationError('portable-v2 staging directory is not privately owned')
            current(0o700)
            yield fd, current, put, entries
        except BaseException as error:
            failure = error
            raise
        finally:
            # The namespace may still map the staged pages. Keep the occupied
            # slot until independently settled; reopening would defeat the cap.
            if not getattr(failure, '_vtx_isolation_unsettled', False):
                try:
                    # Only delete the private directory and the exact inodes we created.
                    if fstxn.entry_identity(os.stat(name, dir_fd=shm, follow_symlinks=False)) != identity:
                        raise IsolationError('portable-v2 staging cleanup identity lost')
                    os.fchmod(fd, 0o700)
                    for leaf, expected in entries.items():
                        if fstxn.entry_identity(os.stat(leaf, dir_fd=fd, follow_symlinks=False)) != expected:
                            raise IsolationError('portable-v2 staging cleanup identity lost')
                        os.unlink(leaf, dir_fd=fd)
                    os.rmdir(name, dir_fd=shm)
                except (OSError, IsolationError):
                    if failure is None:
                        raise IsolationError('portable-v2 staging cleanup not proven') from None
                    print('portable-v2 staging cleanup not proven', file=sys.stderr)


@contextmanager
def snapshot(root, root_identity, paths, files, mounts, other_identities, limit, check):
    directory = paths[0].parent
    device = root_identity[-1][0]
    if any(mounts[p] != mounts[root] for p in paths):
        raise IsolationError('portable-v2 frames must be on the project filesystem')
    if _TOTAL > limit:
        raise IsolationError('portable-v2 inputs exceed the full-byte verification limit')
    with ExitStack() as held:
        original, guard = _pin_binding(held, directory, root, directory=True, root_identity=root_identity)
        if os.fstat(original).st_dev != device:
            raise IsolationError('portable-v2 frames must be on the project filesystem')
        staged, current, put, entries = held.enter_context(_stage(root, device, other_identities))

        def observe(callback=None):
            guard()
            fold = hashlib.sha256(b'framivra:portable-v2:svg:v1\0')
            tokens, digests, identities, remaining = [], [], set(), _TOTAL
            for i, p in enumerate(paths):
                check()
                token, data = _read_at(original, p.name, device, min(remaining, _FRAME_LIMIT))
                inode = token[:3]
                if inode in other_identities or inode in identities:
                    raise IsolationError('portable-v2 frame cannot share another input role')
                identities.add(inode)
                tokens.append(token)
                remaining -= len(data)
                fold.update(i.to_bytes(4, 'big') + len(data).to_bytes(8, 'big') + data)
                digests.append((len(data), hashlib.sha256(data).hexdigest()))
                if callback is not None:
                    callback(p.name, data)
            guard()
            if remaining or fold.hexdigest() != _FRAME_SHA:
                raise IsolationError('portable-v2 SVG frames do not match the frozen bytes')
            return tokens, digests, identities

        tokens, digests, identities = observe(put)
        os.fchmod(staged, 0o500)
        staged_device = os.fstat(staged).st_dev
        names = {p.name for p in paths}
        inventory = None

        def verify_stage():
            nonlocal inventory
            current(0o500)
            if set(os.listdir(staged)) != names:
                raise IsolationError('portable-v2 staged frame inventory changed')
            found = []
            for p, (size, sha) in zip(paths, digests):
                check()
                token, data = _read_at(staged, p.name, staged_device, size)
                if (token[:3] != entries[p.name] or len(data) != size or hashlib.sha256(data).hexdigest() != sha
                        or stat.S_IMODE(os.stat(p.name, dir_fd=staged, follow_symlinks=False).st_mode) != 0o400):
                    raise IsolationError('portable-v2 staged frames changed')
                found.append(token)
            if (inventory is not None and found != inventory) or set(os.listdir(staged)) != names:
                raise IsolationError('portable-v2 staged frame inventory changed')
            current(0o500)
            inventory = found

        def verify_original():
            if observe() != (tokens, digests, identities):
                raise IsolationError('portable-v2 original frame identity changed')

        observed = [(str(p), str(p), files[p], *digest) for p, digest in zip(paths, digests)]
        yield staged, verify_stage, verify_original, identities, observed
