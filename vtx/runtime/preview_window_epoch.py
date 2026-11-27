"""Private environment witness for the qualified Linux window worker only."""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import sys

from .preview_window_isolation import (
    _DISTRIBUTION_FONTS, _MANAGED_FONTS, _MANAGED_FONT_PINS, IsolationError, _pin_binding, _token,
    binding_digest, identity_digest,
)


# ldd emits process-random mapped addresses. Keep the ordered loader names and
# absolute resolutions, not the addresses; reject lines we cannot interpret.
_LDD = re.compile(r'\s*(?:(\S+)\s+=>\s+)?(/\S+)\s+\(0x[0-9a-fA-F]+\)\s*')
_VDSO = re.compile(r'\s*(linux-(?:vdso|gate)\.so\.1)\s+\(0x[0-9a-fA-F]+\)\s*')
_ACTIVE_CONFIG = re.compile(r'^\+ (.*?): ', re.M)
_ENV_FILE_LIMIT = 256 * 1024 * 1024
_PROVIDER_FILES = ('/usr/bin/ffmpeg', '/usr/bin/ffprobe', '/usr/bin/fc-list', '/usr/bin/fc-conflist',
                   '/usr/bin/ldd', '/etc/fonts/fonts.conf')


class EnvironmentWitness:
    def __init__(self, check, limit, *, font_config=None):
        self.check = check
        self.limit = limit
        self.paths = set()
        self.inventories = {}
        managed_config = str(_MANAGED_FONTS / 'fonts.conf')
        self.managed_config = managed_config if font_config == managed_config else None

    def observe(self, argv, stdout):
        tool = argv[0]
        if tool == 'ldd':
            entries = []
            for line in stdout.splitlines():
                match = _LDD.fullmatch(line)
                if match:
                    name, path = match.groups()
                    self.paths.add(path)
                    entries.append((name, path))
                elif vdso := _VDSO.fullmatch(line):
                    entries.append((vdso[1], None))
                else:
                    raise IsolationError('unknown dynamic library inventory')
            if not any(path is not None for _, path in entries):
                raise IsolationError('empty dynamic library inventory')
            self.inventories['ldd:' + str(argv[1])] = entries
        elif tool == 'fc-list':
            names = sorted(set(stdout.splitlines()))
            if self.managed_config is not None:
                # Only the fixed worker's own fc-list can show which mounted
                # leaves Fontconfig actually recognized. Check before the
                # certificate collector opens the reported font paths.
                expected = {str(_MANAGED_FONTS / leaf) for leaf in _MANAGED_FONT_PINS}
                listed = set(names)
                if not expected <= listed:
                    raise IsolationError('managed preview font inventory is missing an approved font')
                for name in listed - expected:
                    path = Path(name)
                    if _DISTRIBUTION_FONTS not in path.parents or '..' in path.parts:
                        raise IsolationError('managed preview font inventory contains a non-distribution font')
                    if _DISTRIBUTION_FONTS not in path.resolve(strict=True).parents:
                        raise IsolationError('managed preview distribution font resolves outside its provider')
            self.paths.update(names)
            self.inventories[tool] = names
        elif tool == 'fc-conflist':
            self.paths.update(_ACTIVE_CONFIG.findall(stdout))
            self.inventories[tool] = stdout  # Preserve order, active and inactive config.

    def snapshot(self, certificate, implementation, environment):
        self.check()
        uname = os.uname()
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if (not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot)
                or len(self.paths) > 8192):
            raise IsolationError('unknown host or provider inventory')
        paths = self.paths | set(map(str, implementation)) | set(_PROVIDER_FILES) | {sys.executable}
        if self.managed_config is not None:
            if 'fc-list' not in self.inventories or environment.get('FONTCONFIG_FILE') != self.managed_config:
                raise IsolationError('managed preview lacks its observed font configuration')
            paths.add(self.managed_config)
        observed, tokens, total = [], [], 0
        for name in sorted(paths):
            self.check()
            path = Path(name)
            if not path.is_absolute() or '..' in path.parts:
                raise IsolationError('provider inventory requires absolute paths')
            target = path.resolve(strict=True)
            alias = _token(path.lstat())
            with ExitStack() as stack:
                fd, guard = _pin_binding(stack, target, target.parent)
                token = _token(os.fstat(fd))
                size, sha = binding_digest(fd, guard, min(_ENV_FILE_LIMIT, self.limit - total), self.check)
                if path.resolve(strict=True) != target or _token(path.lstat()) != alias:
                    raise IsolationError('provider path changed during full-byte verification')
            total += size
            observed.append((name, str(target), size, sha))
            tokens.append((alias, token))
        return (identity_digest('framivra.preview-window.environment/v1', boot, list(uname),
                                os.cpu_count(), sorted(os.sched_getaffinity(0)), sys.version,
                                environment, certificate, self.inventories, observed), tokens)
