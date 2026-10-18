#!/usr/bin/env python3
"""Build the locked macOS arm64 CEF distribution; no system installation."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent

def run(*args):
    subprocess.run(args, check=True)

def main():
    if os.uname().machine != 'arm64':
        raise SystemExit('macOS CEF lock currently supports arm64 only')
    lock = json.loads((ROOT / 'cef_lock.macosarm64.json').read_text())
    cache = Path(os.environ.get('CEF_CACHE_DIR', Path.home() / '.cache/cef'))
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / lock['archive']
    def verified(path):
        if not path.is_file() or path.stat().st_size != lock['size_bytes']:
            return False
        data = path.read_bytes()
        return (hashlib.sha256(data).hexdigest() == lock['sha256'] and
                hashlib.sha1(data).hexdigest() == lock['sha1_official_index'])
    if not verified(archive):
        partial = archive.with_suffix(archive.suffix + '.part')
        run('curl', '-fL', '--retry', '3', '-o', str(partial), lock['url'])
        if not verified(partial):
            raise SystemExit('CEF archive size/digest mismatch')
        partial.replace(archive)
    cef = ROOT / lock['archive'].removesuffix('.tar.bz2')
    framework = cef / 'Release/Chromium Embedded Framework.framework'
    if not framework.is_dir():
        # Official archive is digest-pinned; still reject paths escaping its root.
        with tarfile.open(archive, 'r:bz2') as tf:
            for member in tf.getmembers():
                if Path(member.name).is_absolute() or '..' in Path(member.name).parts:
                    raise SystemExit('Unsafe archive path')
            tf.extractall(ROOT)
    build = ROOT / 'build'
    run('cmake', '-S', str(ROOT), '-B', str(build), '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DUSE_SANDBOX=OFF', '-DCEF_ROOT=' + str(cef))
    run('cmake', '--build', str(build), '--parallel', str(os.cpu_count() or 2))
    binary = build / 'bin'
    app = binary / 'vtx_cef_worker.app'
    frameworks = app / 'Contents/Frameworks'
    frameworks.mkdir(parents=True, exist_ok=True)
    dest = frameworks / framework.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(framework, dest, symlinks=True)
    main_exe = app / 'Contents/MacOS/vtx_cef_worker'
    for suffix in ('', ' (Alerts)', ' (GPU)', ' (Plugin)', ' (Renderer)'):
        name = 'vtx_cef_worker Helper' + suffix
        helper = frameworks / (name + '.app') / 'Contents'
        (helper / 'MacOS').mkdir(parents=True, exist_ok=True)
        shutil.copy2(main_exe, helper / 'MacOS' / name)
        with (helper / 'Info.plist').open('wb') as fh:
            plistlib.dump({'CFBundleExecutable': name, 'CFBundleName': name,
                          'CFBundleIdentifier': 'org.framivra.cef-worker.helper' +
                          suffix.lower().replace(' ', '').replace('(', '.').replace(')', ''),
                          'CFBundlePackageType': 'APPL', 'LSUIElement': True}, fh)
    # Local ad-hoc signatures are required for arm64 executable integrity. No
    # entitlements, quarantine changes, or security-policy changes are applied.
    run('codesign', '--force', '--deep', '--sign', '-', str(app))
    entry = binary / 'vtx_cef_worker'
    if entry.is_symlink():
        entry.unlink()
    entry.write_text('#!/bin/sh\nexec "$(dirname "$0")/vtx_cef_worker.app/Contents/MacOS/vtx_cef_worker" "$@"\n')
    entry.chmod(0o755)
    run('codesign', '--verify', '--deep', '--strict', str(app))
    run(str(main_exe), '--sanity')
    print('CEF macOS worker:', main_exe)

if __name__ == '__main__':
    main()
