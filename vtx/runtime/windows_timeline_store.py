"""NTFS filesystem adapter for the timeline store's existing journal protocol."""
from contextlib import ExitStack, contextmanager
import ntpath
import os
from pathlib import Path
import secrets

from ..render.gfx_svg_win_input import WindowsPathAlias, _ordinary
from ..render.gfx_svg_win_publish import anchored_relative


# Keep the exact byte histories bounded without imposing the smaller IPC limit
# on in-process snapshots. A sidecar can contain both an exact-import encoding
# and its canonical request/result metadata.
MAX_BYTES = 32 * 1024 * 1024


def target_path(value):
    raw = os.path.expanduser(os.fspath(value)).replace('/', '\\')
    drive, tail = ntpath.splitdrive(raw)
    if drive:
        if len(drive) != 2 or drive[1] != ':' or not tail.startswith('\\'):
            raise ValueError('timeline store requires a local absolute or ordinary relative path')
        parts = tail[1:].split('\\')
    else:
        parts = tail.split('\\')
    if not all(map(_ordinary, parts)):
        raise ValueError('timeline store refuses nonordinary Windows path components')
    path = Path(raw)
    if not drive:
        path = Path.cwd() / path
    # Drive letters are DOS volume aliases, not filesystem entry spellings.
    return Path(path.drive.upper() + str(path)[len(path.drive):])


class StoreDirectory:
    def __init__(self, target, root):
        self.target = target
        self.root = root

    @contextmanager
    def directory(self, path):
        parent = self.root
        with ExitStack() as pins:
            for part in path.relative_to(self.target.parent).parts:
                parent = pins.enter_context(parent.child(part, create=False))
            yield parent

    def read(self, path):
        with self.directory(path.parent) as parent:
            return parent.read(path.name, MAX_BYTES, share=5 if path == self.target else 1)

    def require(self, path, *, directory):
        if directory:
            with self.directory(path):
                pass
        else:
            with self.directory(path.parent) as parent, parent.open_read(path.name, share=5 if path == self.target else 1):
                pass
        return True

    def entries(self, path):
        with self.directory(path) as directory:
            return [path / name for name in directory.entries()]

    def publish(self, path, payload, *, replace=True, before_replace=None):
        self.check_size(payload)
        with self.directory(path.parent) as parent:
            parent.publish(path.name, payload, replace=replace,
                           before_replace=before_replace, temp_prefix='.publish-',
                           while_readers=replace and path == self.target)

    @contextmanager
    def lock(self, path, name):
        with self.directory(path) as parent, parent.lock(name):
            yield

    @staticmethod
    def check_size(payload):
        if payload is not None and len(payload) > MAX_BYTES:
            raise ValueError('Windows timeline store document or history record exceeds its byte limit')

    def publish_tree(self, path, members, *, prefix, checkpoint=None):
        """Publish a whole initially absent directory with its exclusive bytes."""
        for _, payload in members:
            self.check_size(payload)
        with self.directory(path.parent) as parent:
            with parent.new_directory(prefix + secrets.token_hex(12)) as staging:
                if checkpoint is not None:
                    checkpoint('init_mkdir')
                for name, payload in members:
                    staging.write_new(name, payload)
                if checkpoint is not None:
                    checkpoint('init_manifest_synced')
                parent.publish_directory(staging, path.name)


@contextmanager
def scope(value):
    target = target_path(value)
    with anchored_relative(target.parent) as root:
        yield target, StoreDirectory(target, root)
