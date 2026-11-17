#!/usr/bin/env python3
"""Native Windows tools: locked original packages, private portable directories.

No source registration or global PATH edits. Download/resume/hash policy belongs to
install_downloads; a bad hash must never be turned into another provider.
"""
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import zipfile

if __package__:
    from .install_downloads import DownloadError, IntegrityError, acquire
else:
    from install_downloads import DownloadError, IntegrityError, acquire

PACKAGES = {
    'ffmpeg': {
        'directory': 'ffmpeg-9.0.2',
        'sha256': '759D0A9831C436A0EB331AD36F236C06CB04AAA0005DA46571F0E9D3D9206F6B',
        'urls': ['https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-full_build.zip'],
        'files': {'ffmpeg': 'ffmpeg-9.0.2-full_build/bin/ffmpeg.exe',
                  'ffprobe': 'ffmpeg-9.0.2-full_build/bin/ffprobe.exe'},
    },
    'sox': {
        'directory': 'sox-14.4.2',
        'sha256': '8072CC147CF1A3B3713B8B97D6844BB9389E211AB9E1101E432193FAD6AE6662',
        'urls': ['https://sourceforge.net/projects/sox/files/sox/14.4.2/sox-14.4.2-win32.zip/download'],
        'files': {'sox': 'sox-14.4.2/sox.exe'},
    },
    'git': {
        'directory': 'git-2.56.0.4',
        'sha256': '4961A4A8CC778D8B6E00A5819E5FE4D078B754555AFF7AEDC2D77A5D74C2DE80',
        'urls': ['https://mirrors.huaweicloud.com/git-for-windows/v2.56.0.windows.4/Git-2.56.0.4-64-bit.exe',
                 'https://cdn.npmmirror.com/binaries/git-for-windows/v2.56.0.windows.4/Git-2.56.0.4-64-bit.exe',
                 'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/Git-2.56.0.4-64-bit.exe'],
        'files': {'git': 'cmd/git.exe'},
    },
}


def _physical(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & 0x400):
            raise IntegrityError('Refusing symlink/junction native path: ' + str(item))
        if item != path and item.exists() and not item.is_dir():
            raise IntegrityError('Non-directory native ancestor: ' + str(item))
    return path


def _write_record(path, value):
    path = _physical(path)
    # Interrupted writes must not destroy a prior valid ownership record.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.native-record-',
                                     dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _target(root, package):
    return _physical(Path(root) / 'tools' / 'windows' / package['directory'])


def _receipt(package):
    return {'schema': 'framivra.native/v1', 'archiveSha256': package['sha256'].lower()}


def discover(root):
    """Read completed private installs only; never execute candidates here."""
    found = {}
    for package in PACKAGES.values():
        target = _target(root, package)
        receipt = _physical(target / '.complete.json')
        if not receipt.is_file():
            continue
        try:
            value = json.loads(receipt.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise IntegrityError('Invalid native completion record: ' + str(receipt)) from error
        if value != _receipt(package):
            raise IntegrityError('Native package identity differs: ' + str(target))
        for name, relative in package['files'].items():
            path = _physical(target / relative)
            if not path.is_file():
                raise IntegrityError('Completed native tool is missing: ' + str(path))
            found[name] = str(path)
    return found


def _zip_members(archive):
    """Validate the entire ZIP before extracting any bytes (Windows semantics)."""
    members, seen, total = [], set(), 0
    reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
    entries = archive.infolist()
    if len(entries) > 10000:
        raise IntegrityError('Native archive has too many members')
    for member in entries:
        raw = member.filename.replace('\\', '/')
        path = PurePosixPath(raw)
        parts = raw.rstrip('/').split('/')
        if (not raw or raw.startswith('/') or path.is_absolute() or
                any(p in ('', '.', '..') or ':' in p or p.endswith((' ', '.')) or
                    p.split('.')[0].lower() in reserved for p in parts)):
            raise IntegrityError('Unsafe native ZIP member: ' + member.filename)
        if stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1:
            raise IntegrityError('Linked/encrypted native ZIP member: ' + member.filename)
        key = '/'.join(parts).casefold()
        if key in seen:
            raise IntegrityError('Duplicate native ZIP member: ' + member.filename)
        seen.add(key)
        total += member.file_size
        if member.file_size > 1024 ** 3 or total > 2 * 1024 ** 3:
            raise IntegrityError('Native ZIP expansion limit exceeded')
        members.append((member, parts))
    return members


def unpack(archive, root, package):
    """Publish complete ZIP contents atomically; incomplete staging is never found."""
    target = _target(root, package)
    if target.exists():
        # Only an already completed, matching install can be reused.
        available = discover(root)
        if all(name in available for name in package['files']):
            return {name: available[name] for name in package['files']}
        raise IntegrityError('Refusing existing unowned native directory: ' + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.' + package['directory'] + '-', dir=target.parent))
    try:
        with zipfile.ZipFile(archive) as source:
            members = _zip_members(source)
            for member, parts in members:
                destination = _physical(stage.joinpath(*parts))
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(member) as incoming, destination.open('xb') as outgoing:
                        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        for relative in package['files'].values():
            if not (stage / relative).is_file():
                raise IntegrityError('Native ZIP is missing required file: ' + relative)
        (stage / '.complete.json').write_text(json.dumps(_receipt(package)), encoding='utf-8')
        stage.rename(target)
    except (zipfile.BadZipFile, RuntimeError) as error:
        raise IntegrityError('Invalid native ZIP: ' + str(error)) from error
    finally:
        # stage is a unique directory created by this invocation, not a user tree.
        if stage.exists():
            _physical(stage)
            shutil.rmtree(stage)
    return {name: str(target / relative) for name, relative in package['files'].items()}


def _default_run(argv, *, capture=False, timeout=None, **kwargs):
    result = subprocess.run([str(x) for x in argv], check=True, capture_output=capture,
                            text=True, timeout=timeout, **kwargs)
    return result.stdout.strip() if capture else ''


def _browser():
    for base in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        if os.environ.get(base):
            for relative in ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe'):
                path = Path(os.environ[base]) / relative
                if path.is_file():
                    return str(path.resolve())
    return shutil.which('msedge') or shutil.which('chrome')


def prepare(root, found, *, network='cn', no_system=False, yes=False, run=None):
    """Fill missing tools. Caller retains codec/CDP/full installation acceptance.

    The caller removes incompatible existing tools before calling this function.
    `yes` is the already-approved installation plan, not a bypass for signatures.
    """
    if network not in ('cn', 'official'):
        raise ValueError('Unknown network policy: ' + str(network))
    run = run or _default_run
    # The orchestrator merges discover() before capability checks. Do not put a
    # rejected existing tool back into `found` after that check.
    result = dict(found)
    if 'chrome' not in result:
        browser = _browser()
        if browser:
            result['chrome'] = browser
    missing = {'git', 'ffmpeg', 'ffprobe', 'sox', 'chrome'} - result.keys()
    if not missing:
        return result
    if no_system:
        raise DownloadError('Missing native tools (--no-system): ' + ', '.join(sorted(missing)))
    if not yes:
        raise DownloadError('Native preparation requires approval of the installation plan')
    cache = _physical(Path(root) / 'cache' / 'downloads')
    for name in ('ffmpeg', 'sox', 'git'):
        package = PACKAGES[name]
        if not (set(package['files']) & missing):
            continue
        urls = package['urls'] if network == 'cn' else package['urls'][-1:]
        archive = acquire(cache, urls, package['sha256'], filename=package['directory'] + ('.exe' if name == 'git' else '.zip'))
        if name != 'git':
            # Keep ffmpeg/ffprobe from the same build when either is absent.
            result.update(unpack(archive, root, package))
        else:
            target = _target(root, package)
            marker = _physical(target / '.installing.json')
            if target.exists() and any(target.iterdir()):
                # Interrupted Inno installations are resumable by rerunning the same
                # locked installer in our explicit private destination.
                if not marker.is_file():
                    raise IntegrityError('Refusing unowned Git installation: ' + str(target))
                if json.loads(marker.read_text()) != _receipt(package):
                    raise IntegrityError('Git installation identity differs')
            target.mkdir(parents=True, exist_ok=True)
            _write_record(marker, _receipt(package))
            run([archive, '/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                 '/CURRENTUSER', '/DIR=' + str(target), '/o:PathOption=BashOnly'], timeout=None)
            executable = _physical(target / 'cmd' / 'git.exe')
            if not executable.is_file():
                raise DownloadError('Git installer did not provide cmd/git.exe')
            run([executable, '--version'], capture=True, timeout=60)
            _write_record(target / '.complete.json', _receipt(package))
            result['git'] = str(executable)
    if 'chrome' in missing:
        # The enterprise MSI URL rolls. Let the official current winget manifest
        # select and verify it rather than embedding a permanently stale hash.
        winget = shutil.which('winget')
        if not winget:
            raise DownloadError('No working Edge/Chrome found. Install a native browser or App Installer (winget), then resume.')
        run([winget, 'install', '--id', 'Google.Chrome', '-e', '--source', 'winget',
             '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity'], timeout=None)
        browser = _browser()
        if not browser:
            raise DownloadError('Chrome installation finished but browser was not found; resume after resolving its installation')
        result['chrome'] = browser
    return result
