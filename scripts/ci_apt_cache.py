#!/usr/bin/env python3
"""Optional, untrusted .deb download cache for disposable Ubuntu CI runners.

Only ``prepare`` is read-only with respect to the system. ``install`` performs
the same mandatory APT installation on hits and misses. Cache entries never
contain package lists, dpkg state, installed files, or executable cache hooks.
The workflow must use an exact image-scoped key, without restore-key prefixes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import stat
import subprocess
import sys
import tempfile


SCHEMA = 'v1'
PACKAGES = ('ffmpeg', 'fonts-noto-cjk')
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z')
_DEB = re.compile(r'[A-Za-z0-9][A-Za-z0-9.+:~%_-]*\.deb\Z')


def cache_key(env, distro, arch, packages=PACKAGES, schema=SCHEMA):
    """Missing actual image identity disables caching; never guess a version."""
    identity = (env.get('ImageOS', ''), env.get('ImageVersion', ''),
                distro.get('ID', ''), distro.get('VERSION_ID', ''), arch, schema)
    if not all(_IDENTIFIER.fullmatch(value) for value in identity):
        return None
    # Hash structured identity too: separator-containing image identifiers must
    # never alias another image's key through ambiguous string concatenation.
    declaration = hashlib.sha256(json.dumps([identity, list(packages)],
                                            separators=(',', ':'))
                                 .encode('utf-8')).hexdigest()
    key = 'framivra-apt-' + '-'.join(identity) + '-' + declaration
    return key if len(key) <= 512 else None


def read_distro(path=Path('/etc/os-release')):
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        name, separator, value = line.partition('=')
        if separator and name in ('ID', 'VERSION_ID'):
            parsed = shlex.split(value)
            if len(parsed) == 1:
                result[name] = parsed[0]
    return result


def configuration(env):
    raw = env.get('RUNNER_TEMP', '')
    root = Path(raw)
    if not raw or not root.is_absolute() or not root.is_dir():
        raise ValueError('RUNNER_TEMP must name an existing absolute directory')
    # Only this fixed child may be restored. Resolve the trusted runner parent,
    # never the restored child (which could itself be a symlink).
    root = root.resolve()
    try:
        key = cache_key(env, read_distro(), platform.machine())
    except (OSError, ValueError) as exc:
        warning(exc)
        key = None
    return root, root / ('framivra-apt-archives-' + SCHEMA), key


def output(values, env):
    text = ''.join('{}={}\n'.format(name, value) for name, value in values.items())
    if any('\n' in str(value) or '\r' in str(value) for value in values.values()):
        raise ValueError('unsafe workflow output')
    if env.get('GITHUB_OUTPUT'):
        with open(env['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write(text)
    print(text, end='')


def warning(message):
    print('APT cache ignored: ' + str(message), file=sys.stderr)


def prepare(env):
    _, cache, key = configuration(env)
    enabled = key is not None
    if enabled:
        try:
            cache.mkdir(mode=0o700, exist_ok=True)
            if cache.is_symlink() or not cache.is_dir():
                raise ValueError('archive cache is not a real directory')
        except (OSError, ValueError) as exc:
            warning(exc)
            enabled = False
    if key is None:
        warning('actual image/distro/architecture identity is unavailable')
    output({'path': str(cache), 'key': key or '',
            'cacheable': str(enabled).lower()}, env)


def parse_plan(text):
    """Read APT's current signed-index archive plan, requiring strong hashes.

    APT defaults --print-uris to MD5 for compatibility. Our caller explicitly
    sets Acquire::ForceHash=SHA256; an unexpected format disables reuse.
    """
    records = {}
    for line in text.splitlines():
        if not line.startswith("'"):
            continue
        fields = shlex.split(line)
        if len(fields) != 4:
            raise ValueError('malformed APT archive record')
        _, name, size, digest = fields
        if (not _DEB.fullmatch(name) or not size.isdecimal() or int(size) <= 0
                or not re.fullmatch(r'SHA256:[0-9a-fA-F]{64}', digest)
                or name in records):
            raise ValueError('unsafe/duplicate archive name or absent SHA256')
        records[name] = (int(size), digest.split(':', 1)[1].lower())
    return records


def copy_verified(source, target, expected):
    """Hash exactly the bytes copied; never parse or execute an untrusted deb."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('archive must be a regular, singly-linked file')
        if info.st_size != expected[0]:
            raise ValueError('archive size differs from current APT metadata')
        digest = hashlib.sha256()
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            with target.open('xb') as dest:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    dest.write(chunk)
        if target.stat().st_size != expected[0] or digest.hexdigest() != expected[1]:
            raise ValueError('archive SHA256 differs from current APT metadata')
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        if fd is not None:
            os.close(fd)


def stage_cache(cache, archives, plan):
    if not cache.exists() and not cache.is_symlink():
        return 0
    if cache.is_symlink() or not cache.is_dir():
        raise ValueError('archive cache is not a real directory')
    entries = list(cache.iterdir())
    # All-or-nothing: stale versions, unexpected files, symlinks and directories
    # invalidate reuse, even if other entries happen to have valid checksums.
    for entry in entries:
        info = entry.lstat()
        if (entry.name not in plan or not _DEB.fullmatch(entry.name)
                or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise ValueError('unexpected or stale cache entry: ' + entry.name)
    for entry in entries:
        copy_verified(entry, archives / entry.name, plan[entry.name])
    return len(entries)


def export_cache(cache, archives, plan, root):
    """Publish a fresh .deb-only directory; never walk the restored directory."""
    exported = Path(tempfile.mkdtemp(prefix='framivra-apt-export-', dir=root))
    count = 0
    for name, expected in plan.items():
        source = archives / name
        if source.exists() or source.is_symlink():
            copy_verified(source, exported / name, expected)
            count += 1
    if not count:
        return False
    if cache.exists() or cache.is_symlink():
        retired = Path(tempfile.mkdtemp(prefix='framivra-apt-retired-', dir=root))
        retired.rmdir()
        cache.rename(retired)
    exported.rename(cache)
    return True


def apt_command(lists, archives, *arguments):
    options = (
        'Dir::State::lists=' + str(lists),
        'Dir::Cache::archives=' + str(archives),
        'Dir::Cache::pkgcache=', 'Dir::Cache::srcpkgcache=',
        'Acquire::AllowInsecureRepositories=false',
        'Acquire::AllowDowngradeToInsecureRepositories=false',
        'Acquire::AllowWeakRepositories=false',
        'Acquire::Check-Valid-Until=true',
        'APT::Get::AllowUnauthenticated=false',
        'APT::Update::Error-Mode=any',
        'Acquire::ForceHash=SHA256',
    )
    command = ['sudo', 'env', 'DEBIAN_FRONTEND=noninteractive', 'LC_ALL=C', 'apt-get']
    for option in options:
        command.extend(('-o', option))
    return command + list(arguments)


def install(env):
    root, cache, key = configuration(env)
    output({'cacheable': 'false'}, env)
    # This private workspace is never restored/saved. Runner cleanup removes
    # root/_apt-owned lists/partial/lock files after the job; do not cache them.
    work = Path(tempfile.mkdtemp(prefix='framivra-apt-work-', dir=root))
    work.chmod(0o755)
    lists, archives = work / 'lists', work / 'archives'
    lists.mkdir(mode=0o755)
    archives.mkdir(mode=0o755)

    # Fresh signed indices, with ANY update failure fatal. Never fall back to
    # yesterday's indices or let a cache error hide a real update/install error.
    subprocess.run(apt_command(lists, archives, 'update'), check=True)
    cacheable = False
    if key is not None:
        try:
            result = subprocess.run(
                apt_command(lists, archives, '--print-uris', '--yes',
                            '--download-only', 'install', *PACKAGES),
                check=True, capture_output=True, text=True)
            plan = parse_plan(result.stdout)
            try:
                reused = stage_cache(cache, archives, plan)
                print('APT cache: verified {} current archive(s)'.format(reused))
            except (OSError, ValueError) as exc:
                warning(exc)
                # A previous entry may already have been copied. Abandon the
                # whole private directory so no partial validation is reused.
                archives = work / 'clean-archives'
                archives.mkdir(mode=0o755)
            subprocess.run(
                apt_command(lists, archives, '--yes', '--download-only',
                            'install', *PACKAGES), check=True)
            cacheable = export_cache(cache, archives, plan, root)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            warning(exc)
            # Download/cache preparation is optional. Mandatory ordinary APT
            # installation below starts with a new, empty archive directory.
            archives = work / 'fallback-archives'
            archives.mkdir(mode=0o755)
            cacheable = False

    # Always install, even on exact cache hits. No extracted state is reused;
    # normal APT authentication/dependency/maintainer-script behavior applies.
    subprocess.run(apt_command(lists, archives, '--yes', 'install', *PACKAGES),
                   check=True)
    status = subprocess.run(
        ['dpkg-query', '--show', '--showformat=${db:Status-Status}\n', *PACKAGES],
        check=True, capture_output=True, text=True)
    if status.stdout.splitlines() != ['installed'] * len(PACKAGES):
        raise ValueError('mandatory media packages are not fully installed')
    output({'cacheable': str(cacheable).lower()}, env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'install'))
    args = parser.parse_args(argv)
    try:
        (prepare if args.command == 'prepare' else install)(os.environ)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print('APT prerequisite failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
