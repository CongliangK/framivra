#!/usr/bin/env python3
"""Native Windows tools: locked original packages, private portable directories.

No source registration or global PATH edits. Download/resume/hash policy belongs to
install_downloads; a bad hash must never be turned into another provider.
"""
import base64
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
        'directory': 'mingit-2.56.0.4-x64',
        'size': 39846926,
        'sha256': 'e3bf78351fe777fba47ddee5950082f8a98bb0f02604fdc7ba3ee2cb68ffa877',
        'urls': ['https://mirrors.huaweicloud.com/git-for-windows/v2.56.0.windows.4/MinGit-2.56.0.4-64-bit.zip',
                 'https://cdn.npmmirror.com/binaries/git-for-windows/v2.56.0.windows.4/MinGit-2.56.0.4-64-bit.zip',
                 'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/MinGit-2.56.0.4-64-bit.zip'],
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
        archive = acquire(cache, urls, package['sha256'], size=package.get('size'),
                          filename=package['directory'] + '.zip')
        # Keep every companion/helper/DLL and license from the original archive.
        # In particular MinGit never runs the legacy, potentially elevated Inno
        # installer; its separate destination leaves old Git trees untouched.
        result.update(unpack(archive, root, package))
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


VC_RUNTIME = {
    'version': '14.44.35211.0',
    'size': 25635768,
    'sha256': 'cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b',
    'url': 'https://download.visualstudio.microsoft.com/download/pr/e184d144-a0ee-448c-97bb-96407b707977/CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B/VC_redist.x64.exe',
}
CRT_DLLS = ('MSVCP140.dll', 'VCRUNTIME140.dll', 'VCRUNTIME140_1.dll')
PYTHON_RUNTIME_PROBE = r'''import ctypes, importlib, json
result = {'ok': False}
try:
    for name in ('numpy', 'scipy', 'soundfile', 'soxr', 'PIL'):
        importlib.import_module(name)
    import numpy, soxr
    audio = soxr.resample(numpy.ones(32), 48000, 44100)
    assert audio.ndim == 1 and 0 < audio.size <= 32 and numpy.isfinite(audio).all(), 'soxr resampling failed'
    result = {'ok': True}
except Exception as error:
    result.update(error_type=type(error).__name__, error=str(error))
    if type(error) is ImportError:
        result['dlls'] = {}
        for name in ('MSVCP140.dll', 'VCRUNTIME140.dll', 'VCRUNTIME140_1.dll'):
            try:
                ctypes.WinDLL(name)
                result['dlls'][name] = {'ok': True}
            except OSError as failure:
                code = getattr(failure, 'winerror', None)
                result['dlls'][name] = {'ok': False, 'winerror': code,
                    'error_type': type(failure).__name__, 'error': str(failure),
                    'wrapped126': isinstance(failure, FileNotFoundError) and code is None}
print(json.dumps(result, ensure_ascii=True))
'''
# This fixed script never interpolates a filesystem path or runs Python elevated.
# Signature/hash/UAC failures exit the wrapper; only the vendor exit is JSON data.
VC_RUNTIME_INSTALL = r'''$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
try {
    $file = $env:FRAMIVRA_VCREDIST_FILE
    $expected = $env:FRAMIVRA_VCREDIST_SHA256
    if (-not $file -or -not $expected) { throw 'Missing runtime identity' }
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
    if ($hash -ine $expected) { throw 'Microsoft runtime SHA256 mismatch' }
    $signature = Get-AuthenticodeSignature -LiteralPath $file
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -cnotmatch '(?:^|,\s*)CN=Microsoft Corporation(?:,|$)') {
        throw 'Microsoft runtime Authenticode signature/publisher is not valid'
    }
    $process = Start-Process -FilePath $file -Verb RunAs -ArgumentList @('/install', '/passive', '/norestart') -PassThru
    if ($null -eq $process) { throw 'No Microsoft runtime installer process' }
    $handle = $process.Handle
    $process.WaitForExit()
    $code = $process.ExitCode
    if ($null -eq $code) { throw 'Missing Microsoft runtime installer exit status' }
    [pscustomobject]@{exitcode = [int]$code} | ConvertTo-Json -Compress
    exit 0
} catch {
    [Console]::Error.WriteLine('Microsoft runtime trust/UAC/launcher failure: ' + $_.Exception.Message)
    exit 1
}
'''


def _runtime_json(text):
    if not isinstance(text, str) or len(text) > 32768:
        raise IntegrityError('Invalid Python runtime probe/installer response')
    try:
        value = json.loads(text)
    except ValueError as error:
        raise IntegrityError('Invalid Python runtime probe/installer JSON') from error
    if not isinstance(value, dict):
        raise IntegrityError('Invalid Python runtime probe/installer response')
    return value


def _runtime_probe(python, environment, run):
    value = _runtime_json(run([str(python), '-I', '-c', PYTHON_RUNTIME_PROBE],
                              env=environment, capture=True, timeout=120))
    if value.get('ok') is True:
        return value
    if (value.get('ok') is not False or not isinstance(value.get('error_type'), str)
            or not isinstance(value.get('error'), str)):
        raise IntegrityError('Incomplete Python runtime probe response')
    return value


def _runtime_error(probe):
    return probe['error_type'] + ': ' + probe['error']


def ensure_python_runtime(root, python, environment, *, network='cn', no_system=False,
                          yes=False, run=None):
    """Repair only an observed missing MSVC CRT, not an arbitrary import failure.

    Native Windows acceptance remains UNRUN until exercised on that platform.
    This returns preparation metadata, never an installation-ready declaration.
    """
    if network not in ('cn', 'official'):
        raise ValueError('Unknown network policy: ' + str(network))
    run = run or _default_run
    env = dict(environment, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    before = _runtime_probe(python, env, run)
    if before['ok']:
        return {'status': 'available', 'installationAttempted': False}
    problem = _runtime_error(before)
    dlls = before.get('dlls')
    if before['error_type'] != 'ImportError' or not isinstance(dlls, dict) or set(dlls) != set(CRT_DLLS):
        raise DownloadError('Python runtime probe failed; no confirmed missing CRT: ' + problem)
    missing = []
    for name in CRT_DLLS:
        state = dlls[name]
        if not isinstance(state, dict):
            raise IntegrityError('Invalid CRT probe response')
        if state.get('ok') is True:
            continue
        # ctypes maps ERROR_MOD_NOT_FOUND to FileNotFoundError without setting
        # winerror on current CPython. Only these three fixed DLL probes qualify.
        missing_code = type(state.get('winerror')) is int and state['winerror'] == 126
        wrapped_missing = (state.get('error_type') == 'FileNotFoundError'
                           and state.get('winerror') is None and state.get('wrapped126') is True)
        if state.get('ok') is not False or not (missing_code or wrapped_missing):
            raise DownloadError('Python runtime probe failed; blocked CRT load error for %s: %s; %s' % (name, state, problem))
        missing.append(name)
    if not missing:
        raise DownloadError('Python import failed with all known CRTs loadable; no runtime installation: ' + problem)
    if no_system:
        raise DownloadError('Missing Microsoft VC++ runtime (--no-system prohibits download/installation): %s; %s' % (', '.join(missing), problem))
    print('[install] Missing CRT: %s. Prepare Microsoft VC++ %s x64 shared SYSTEM runtime; '
          'may update other applications, requires normal UAC/admin approval and may require a restart. '
          'No automatic restart, DLL copying or PATH changes.' % (', '.join(missing), VC_RUNTIME['version']), flush=True)
    if not yes:
        try:
            consent = input('Install this Microsoft system runtime? [y/N] ').strip().lower()
        except EOFError as error:
            raise DownloadError('Microsoft system runtime requires explicit confirmation/--yes') from error
        if consent not in ('y', 'yes'):
            raise DownloadError('Microsoft system runtime installation cancelled before download')
    powershell = shutil.which('powershell.exe', path=env.get('PATH'))
    if not powershell:
        raise DownloadError('Windows PowerShell is required to verify and elevate only the Microsoft runtime installer')
    cache = _physical(Path(root) / 'cache' / 'downloads')
    executable = acquire(cache, [VC_RUNTIME['url']], VC_RUNTIME['sha256'],
                         size=VC_RUNTIME['size'], filename='VC_redist.x64.exe')
    installer_env = dict(env, FRAMIVRA_VCREDIST_FILE=str(_physical(executable)),
                         FRAMIVRA_VCREDIST_SHA256=VC_RUNTIME['sha256'])
    encoded = base64.b64encode(VC_RUNTIME_INSTALL.encode('utf-16le')).decode('ascii')
    # A nonzero wrapper (trust/UAC/launch failure) propagates immediately: a later
    # successful import must not turn a bad signature or refused UAC into success.
    result = _runtime_json(run([powershell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                              env=installer_env, capture=True, timeout=None))
    code = result.get('exitcode')
    if type(code) is not int:
        raise IntegrityError('Missing Microsoft runtime native exit code')
    after = _runtime_probe(python, env, run)  # Fresh process even after native failure.
    unsigned = code & 0xffffffff
    if unsigned in (3010, 1641):
        raise DownloadError('Microsoft runtime exit %s requires a user-managed restart; no automatic restart or ready registration. Import probe: %s' % (code, 'passed' if after['ok'] else _runtime_error(after)))
    if unsigned in (1223, 1602, 0x800704c7, 0x80070642):
        raise DownloadError('Microsoft runtime installation was cancelled (exit %s); import probe: %s' % (code, 'passed' if after['ok'] else _runtime_error(after)))
    if not after['ok']:
        raise DownloadError('Python runtime still unavailable after Microsoft installer exit %s: %s; original: %s' % (code, _runtime_error(after), problem))
    if unsigned not in (0, 1638, 0x80070666):
        raise DownloadError('Microsoft runtime installer failed with exit %s despite a passing import probe; inspect vendor TEMP logs' % code)
    return {'status': 'available', 'installationAttempted': True,
            'packageVersion': VC_RUNTIME['version'], 'packageSha256': VC_RUNTIME['sha256'],
            'installerExitCode': code, 'rebootRequired': False, 'missingCrt': missing, 'crtProbe': dlls,
            'systemSideEffects': ['Microsoft shared system VC++ runtime installation/update; normal UAC approval']}
