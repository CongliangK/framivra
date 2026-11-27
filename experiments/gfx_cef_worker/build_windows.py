#!/usr/bin/env python3
"""Build the locked Windows x64 CEF worker with a native MSVC-ABI toolchain."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
CEF_LOCK = ROOT / 'cef_lock.windows64.json'


def run(*args, **kwargs):
    command = [str(arg) for arg in args]
    print('+', subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, check=True, **kwargs)


def verified(path, lock):
    if not path.is_file() or path.stat().st_size != lock['size_bytes']:
        return False
    digests = {'sha256': hashlib.sha256()}
    if 'sha1_official_index' in lock:
        digests['sha1_official_index'] = hashlib.sha1()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(2**20), b''):
            for digest in digests.values():
                digest.update(chunk)
    return all(lock[name] == digest.hexdigest() for name, digest in digests.items())


def entries(root):
    """List a cached tree without traversing reparse points (including junctions)."""
    todo = [root]
    while todo:
        path = todo.pop()
        info = path.lstat()
        if getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f'Reparse point in dependency cache: {path}')
        if stat.S_ISDIR(info.st_mode):
            todo.extend(path.iterdir())
        elif not stat.S_ISREG(info.st_mode):
            raise ValueError(f'Unexpected entry in dependency cache: {path}')
        yield path


def archive_tree(archive, root, *, verify):
    top = archive.name.removesuffix('.tar.bz2').removesuffix('.tar.gz')
    remaining = set(entries(root)) if verify else None
    with tarfile.open(archive, 'r|*') as source:
        for member in source:
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != top or any(
                    p == '..' or '\\' in p or ':' in p for p in parts)):
                raise ValueError(f'Invalid archive path: {member.name}')
            dest = root.joinpath(*parts[1:])
            if member.isdir():
                if verify and not dest.is_dir():
                    raise ValueError(f'Missing cache directory: {dest}')
                if not verify:
                    dest.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                with source.extractfile(member) as contents:
                    if verify:
                        with dest.open('rb') as cached:
                            for chunk in iter(lambda: contents.read(2**20), b''):
                                if chunk != cached.read(len(chunk)):
                                    raise ValueError(f'Dependency cache content mismatch: {dest}')
                            if cached.read(1):
                                raise ValueError(f'Dependency cache size mismatch: {dest}')
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with dest.open('xb') as out:
                            shutil.copyfileobj(contents, out, length=2**20)
            else:
                raise ValueError(f'Invalid archive entry type: {member.name}')
            if verify:
                # Some archives rely on implicit parent directories.
                remaining.discard(dest)
                for parent in dest.parents:
                    if parent == root.parent:
                        break
                    remaining.discard(parent)
    if remaining:
        raise ValueError(f'Unexpected file in dependency cache: {next(iter(remaining))}')


def prepare(cache, lock):
    archive = cache / lock['archive']
    if not verified(archive, lock):
        print('Download:', lock['url'], flush=True)
        with tempfile.TemporaryDirectory(prefix='.download-', dir=cache) as tmp:
            incoming = Path(tmp) / archive.name
            with urlopen(lock['url'], timeout=90) as response, incoming.open('wb') as out:
                shutil.copyfileobj(response, out, length=2**20)
            if not verified(incoming, lock):
                raise ValueError(f'Archive size or digest mismatch: {archive.name}')
            incoming.replace(archive)
    print(f'Verified SHA-256 {lock["sha256"]}: {archive}', flush=True)

    # Reuse only a fully extracted, content-addressed dependency directory.
    # Strip the archive's long versioned top-level path to fit MAX_PATH even
    # on Windows systems that have not enabled long paths in policy.
    extracted = cache / lock['sha256']
    if extracted.exists():
        # Never compile or publish an edited cache tree merely because the
        # original tarball still passes its lock. Keep the user's cache intact.
        archive_tree(archive, extracted, verify=True)
    else:
        with tempfile.TemporaryDirectory(prefix='.extract-', dir=cache) as tmp:
            stage = Path(tmp)
            archive_tree(archive, stage, verify=False)
            try:
                stage.rename(extracted)
            except FileExistsError:
                archive_tree(archive, extracted, verify=True)
    return extracted


def publish(build, cef, lock):
    output = build / 'bin'
    previous = build / '.bin-previous'
    if previous.exists():
        raise ValueError(f'Previous publication was interrupted; recover {previous} first')
    with tempfile.TemporaryDirectory(prefix='.bin-', dir=build) as tmp:
        stage = Path(tmp)
        for name in ('vtx_cef_worker', 'pixel_ops_selftest', 'frame_sync_selftest',
                     'js_checkpoint_selftest'):
            exe = f'{name}.exe'
            shutil.copyfile(build / 'obj' / 'bin' / exe, stage / exe)
        for section, files in lock['runtime'].items():
            for name in files:
                shutil.copyfile(cef / section / name, stage / name)
        locales = cef / 'Resources' / 'locales'
        if not (locales / 'en-US.pak').is_file():
            raise ValueError('Missing required Windows CEF en-US locale')
        shutil.copytree(locales, stage / 'locales')
        # Check loader/worker startup from an unrelated CWD, before replacing a
        # known-good publication. The runtime path is always app-relative.
        run(stage / 'vtx_cef_worker.exe', '--sanity', cwd=build, timeout=60)
        # Replace the owned bin directory as a unit so no stale runtime remains.
        if output.exists():
            if not output.is_dir() or not (output / 'vtx_cef_worker.exe').is_file():
                raise ValueError(f'Refusing to replace non-worker output {output}')
            output.rename(previous)
        try:
            stage.rename(output)
        except OSError:
            if previous.exists():
                previous.rename(output)
            raise
    if previous.exists():
        shutil.rmtree(previous)
    return output / 'vtx_cef_worker.exe'


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Run from an x64 Visual Studio developer shell, or supply a native '
        'clang-cl/MSVC-ABI CMake toolchain with --toolchain. No compiler, SDK, '
        'or global PATH changes are performed by this script.'))
    parser.add_argument('--cache-dir', type=Path, help='dependency cache (or CEF_CACHE_DIR)')
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build' / 'windows64',
                        help='dedicated build directory; bin/ is replaced on success')
    parser.add_argument('--cmake', default='cmake', help='CMake executable')
    parser.add_argument('--ninja', default='ninja', help='Ninja executable')
    parser.add_argument('--toolchain', type=Path, help='optional CMake toolchain file')
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 1, 4))
    options = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        parser.error('This lock requires a native Windows x64 host.')
    if options.jobs < 1:
        parser.error('--jobs must be positive')
    if options.toolchain and not options.toolchain.is_file():
        parser.error('--toolchain must name an existing file')
    cmake = shutil.which(options.cmake)
    ninja = shutil.which(options.ninja)
    if not (cmake and ninja):
        parser.error('CMake or Ninja not found; provide --cmake and --ninja or use a developer shell')
    cache = (options.cache_dir or Path(os.environ.get('CEF_CACHE_DIR') or (
        Path(os.environ['LOCALAPPDATA']) / 'framivra' / 'cef-cache'))).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    build = options.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=True)
    import msvcrt
    with (build / '.build.lock').open('a+b') as guard:
        guard.seek(0)
        try:
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise ValueError(f'Cannot lock this build directory: {build}') from exc
        build_worker(options, cmake, ninja, cache, build)


def build_worker(options, cmake, ninja, cache, build):
    lock = json.loads(CEF_LOCK.read_text(encoding='utf-8'))
    cef = prepare(cache, lock)
    zlib = prepare(cache, lock['zlib'])

    obj = build / 'obj'
    configure = ['-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_TRY_COMPILE_CONFIGURATION=Release',
                 '-DPROJECT_ARCH=x86_64', '-DUSE_SANDBOX=OFF', '-DUSE_ATL=OFF',
                 f'-DCEF_ROOT={cef.as_posix()}', f'-DVTX_ZLIB_ROOT={zlib.as_posix()}',
                 f'-DCMAKE_MAKE_PROGRAM={Path(ninja).resolve().as_posix()}']
    if options.toolchain:
        configure.append(f'-DCMAKE_TOOLCHAIN_FILE={options.toolchain.resolve().as_posix()}')
    run(cmake, '-S', ROOT, '-B', obj, '-G', 'Ninja', *configure)
    run(cmake, '--build', obj, '--parallel', options.jobs)
    print('CEF Windows x64 worker:', publish(build, cef, lock), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as exc:
        raise SystemExit(f'Windows CEF build failed: {exc}') from None
