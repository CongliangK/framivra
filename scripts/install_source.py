#!/usr/bin/env python3
"""Managed source deployment (non-AI, CDP). Bootstrap scripts only obtain Python.

Uses system package managers for missing native tools, a private venv for Python
packages, and an explicit Git source channel (main by default, never 'stable').
No model, service, credential, project, or existing developer checkout is changed.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
import importlib.util
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
from types import ModuleType
import uuid
import venv
from urllib.parse import urlsplit

if __package__:
    from . import install_downloads as downloads
    from . import install_native_windows as windows
    from . import install_native_posix as posix
    from . import install_python_packages as python_packages
else:
    import install_downloads as downloads
    import install_native_windows as windows
    import install_native_posix as posix
    import install_python_packages as python_packages

REQUIRED_ENCODERS = posix.REQUIRED_ENCODERS
REQUIRED_FILTERS = posix.REQUIRED_FILTERS
BUNDLE = ('install_source.py', 'install_downloads.py', 'install_native_windows.py',
          'install_native_posix.py', 'install_python_packages.py',
          'install_runtime_fonts.py', 'demo_fonts.py')

SCHEMA = 'framivra.installation/v1'
REPOSITORY = 'https://github.com/CongliangK/framivra.git'
# Release-reviewed records only, keyed by wheel-source Git object identity.
# Empty until independently signed/approved build and runtime pins exist. Nothing in
# a project, install receipt, environment variable or downloaded wheel adds pins.
# A tree can name one flat build record or a helperSha256 with full variants.
_PREVIEW_SUPPLIER_RELEASES = {}
_PREVIEW_SOURCE = ('vt.py', 'vtx', 'pyproject.toml', 'setup.py', 'VERSION', 'MANIFEST.in', 'README.md',
                   'LICENSE', 'plugins/registry.json', 'scripts/preview_window_wheel.py')
PYTHON_PACKAGES = ('setuptools>=68,<81', 'wheel>=0.42,<1', 'numpy>=1.26,<3',
                   'scipy>=1.11,<2', 'soundfile>=0.12,<1', 'soxr>=0.3,<1',
                   'Pillow>=10,<13')
TOOL_NAMES = ('git', 'ffmpeg', 'ffprobe', 'sox')
MAC_FFMPEG_FULL_BIN = Path('/opt/homebrew/opt/ffmpeg-full/bin')
REQUIRED_SOURCE = ('pyproject.toml', 'vtx/cli/commands.py', 'plugins/registry.json',
                   'scripts/install_source.py', 'scripts/install_runtime_fonts.py',
                   'scripts/demo_fonts.py', 'vtx/runtime/managed.py')


class InstallError(RuntimeError):
    pass


class SourceCompatibilityError(InstallError):
    pass


def default_root(system=None, home=None, environment=None):
    system, home = system or sys.platform, Path(home or Path.home())
    env = os.environ if environment is None else environment
    return ((Path(env.get('LOCALAPPDATA') or home / 'AppData' / 'Local') / 'framivra')
            if system == 'win32' else home / '.local' / 'share' / 'framivra')


def physical(path):
    """Existing ancestors must be real directories, not links/junctions."""
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & 0x400):
            raise InstallError('Refusing symlink/junction installation path: ' + str(item))
        if item != path and item.exists() and not item.is_dir():
            raise InstallError('Non-directory ancestor: ' + str(item))
    return path


def command(argv, *, cwd=None, env=None, capture=False, timeout=1800):
    # Production/offline probes retain their bounded wall time. Every native,
    # Git, pip acquisition and font transfer caller explicitly uses timeout=None;
    # those transports impose connection/idle bounds instead of killing progress.
    print('[install] ' + ' '.join(shlex.quote(str(x)) for x in argv), flush=True)
    result = posix.run_process(argv, cwd=cwd, env=env, capture=capture, timeout=timeout)
    if result.returncode:
        raise InstallError('Command failed (%s): %s: %s' % (result.returncode, argv[0],
                           (result.stderr or '')[-16384:]))
    return result.stdout.strip() if capture else ''


def git(*args, cwd=None, executable='git'):
    # No downloaded hooks, Git user filters/templates, or interactive credentials.
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0')
    for key in list(env):
        if key.startswith('GIT_') and key not in ('GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_TERMINAL_PROMPT'):
            env.pop(key)
    return command([executable, '-c', 'core.hooksPath=' + os.devnull,
                    '-c', 'http.lowSpeedLimit=1', '-c', 'http.lowSpeedTime=60', *args], cwd=cwd,
                   env=env, capture=True, timeout=None)


def atomic_json(path, value, *, before_replace=None):
    physical(path)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temp.open('x', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
        temp.chmod(0o600)
        if before_replace is not None:
            before_replace()
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def installation_lock(root):
    # Keep the lock inode. Removing a lock file races with a waiter and can let
    # two installers mutate the same root. Kernel ownership survives stale files.
    with downloads.advisory_lock(physical(root / '.install.lock')):
        yield


def native_browser():
    candidates = [os.environ.get('VTX_CHROME')]
    candidates += [shutil.which(name) for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome', 'msedge')]
    if sys.platform == 'darwin':
        # Current macOS environment identity supports Google Chrome arm64.
        candidates += ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
    elif sys.platform == 'win32':
        for base in (os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'), os.environ.get('LOCALAPPDATA')):
            if base:
                candidates += [str(Path(base) / p) for p in ('Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe')]
    else:
        candidates += ['/opt/google/chrome/chrome', '/usr/lib/chromium/chromium',
                       '/usr/lib64/chromium-browser/chromium-browser', '/usr/lib64/chromium/chromium',
                       '/snap/chromium/current/usr/lib/chromium-browser/chrome']
    for value in candidates:
        if value and Path(value).is_file():
            path = Path(value).resolve()
            if sys.platform == 'linux':
                with path.open('rb') as source:
                    if source.read(4) != b'\x7fELF':
                        continue
            return str(path)
    return None


def native_names(system=None):
    return (*TOOL_NAMES, 'fc-cache', 'fc-list') if (system or sys.platform) == 'linux' else TOOL_NAMES


def tools(root=None):
    result = {name: str(Path(value).resolve()) for name in native_names()
              if (value := shutil.which(name))}
    browser = native_browser()
    if browser:
        result['chrome'] = browser
    if root is not None:
        # Keg-only/private tools are intentionally not installed in global PATH.
        # Receipt paths are candidates, not qualification: capability checks and
        # real production verification still run before publishing ready.
        record = physical(root / 'installation.json')
        if record.exists():
            previous = json_record(record)
            if previous.get('schema') == SCHEMA and previous.get('sourceRoot') == str(root / 'source'):
                candidates = previous.get('tools', {})
                if not isinstance(candidates, dict):
                    raise InstallError('Recorded tools must be a path object')
                for name, value in candidates.items():
                    if name == 'chrome' and os.environ.get('VTX_CHROME'):
                        continue
                    if name in (*native_names(), 'chrome') and isinstance(value, str):
                        path = Path(value)
                        if path.is_absolute() and path.is_file():
                            result[name] = str(path.resolve())
        if sys.platform == 'win32':
            result.update(windows.discover(root))
    return result


def platform_packages(found, system=None, manager=None, needs_venv=False):
    """Pure plan: native packages only; no models or optional experimental backends."""
    system = system or sys.platform
    missing = set((*native_names(system), 'chrome')) - set(found)
    if not missing and not needs_venv and system != 'win32':
        return []
    if system == 'win32':
        ids = {'git': 'Git.MinGit', 'ffmpeg': 'Gyan.FFmpeg', 'ffprobe': 'Gyan.FFmpeg',
               'chrome': 'Google.Chrome', 'sox': 'ChrisBagwell.SoX'}
        return [(['winget', 'install', '--id', package, '-e', '--accept-source-agreements',
                  '--accept-package-agreements', '--disable-interactivity'] if package == 'Google.Chrome'
                 else ['managed-original-download', '--id', package])
                for package in dict.fromkeys(ids[key] for key in sorted(missing))] + [
                    ['conditional-python-runtime', '--id', 'Microsoft.VCRedist.2015+.x64']]
    if system == 'darwin':
        result = [['brew', 'install', '--force-bottle', 'ffmpeg-full' if package == 'ffmpeg' else package]
                  for package in ('git', 'ffmpeg', 'sox')
                  if package in missing or (package == 'ffmpeg' and 'ffprobe' in missing)]
        if 'chrome' in missing:
            result.append(['brew', 'install', '--cask', 'google-chrome'])
        return result
    mapping = {
        'apt-get': ('apt-get', 'install', '-y'),
        'dnf': ('dnf', 'install', '-y'),
        'pacman': ('pacman', '-S', '--needed', '--noconfirm'),
    }
    manager = manager or next((p for p in mapping if shutil.which(p)), None)
    if manager not in mapping:
        raise InstallError('Supported Linux package managers: apt-get, dnf, pacman; install Git, Python venv, FFmpeg, SoX and native Chromium first.')
    packages = {'git': 'git', 'ffmpeg': 'ffmpeg', 'ffprobe': 'ffmpeg', 'sox': 'sox',
                'chrome': 'google-chrome-stable' if manager == 'apt-get' and posix.os_release().get('ID') == 'ubuntu' else 'chromium',
                'fc-cache': 'fontconfig', 'fc-list': 'fontconfig'}
    selected = list(dict.fromkeys(packages[key] for key in sorted(missing)))
    if needs_venv:
        selected.append({'apt-get': 'python3-venv', 'dnf': 'python3-pip', 'pacman': 'python-pip'}[manager])
    if manager == 'apt-get' and 'chrome' in missing:
        selected += ['libegl1', 'libgbm1']
    return [[*mapping[manager], *selected]]


def refresh_windows_path():
    if sys.platform != 'win32':
        return
    import winreg
    values = []
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
                      (winreg.HKEY_CURRENT_USER, r'Environment')):
        try:
            with winreg.OpenKey(hive, key) as handle:
                values.append(os.path.expandvars(winreg.QueryValueEx(handle, 'Path')[0]))
        except FileNotFoundError:
            continue
    os.environ['PATH'] = os.pathsep.join(values + [os.environ.get('PATH', '')])


def prepare_tools(no_system, root=None, network='cn', yes=False):
    found = tools(root)
    incompatible = posix.missing_ffmpeg(found, run=command)
    if sys.platform == 'darwin' and (incompatible or 'ffprobe' not in found):
        # A linked, reduced FFmpeg can shadow an already installed full keg.
        # Resolve the two executables together without asking brew to install/update.
        pair = posix.ffmpeg_pair(MAC_FFMPEG_FULL_BIN)
        if pair and any(found.get(name) != path for name, path in pair.items()):
            if not posix.missing_ffmpeg(pair, run=command):
                try:
                    command([pair['ffprobe'], '-version'], capture=True, timeout=60)
                except (InstallError, OSError, subprocess.SubprocessError):
                    pass  # Leave the pair unselected; native preparation is still required.
                else:
                    found.update(pair)
                    incompatible = []
    if incompatible:
        print('[install] native FFmpeg needs preparation: ' + ', '.join(incompatible), flush=True)
        found.pop('ffmpeg', None)
        found.pop('ffprobe', None)
    root = root or default_root()
    if sys.platform == 'win32':
        # Generic installation confirmation has already happened. The Windows
        # helper does no global-source/upgrade transaction needing another prompt.
        found = windows.prepare(root, found, network=network, no_system=no_system,
                                yes=True, run=command)
    else:
        found = posix.prepare(root, found, network=network, no_system=no_system,
                              yes=yes, needs_venv=importlib.util.find_spec('ensurepip') is None,
                              run=command)
    return found


def verify_native_tools(found):
    missing = posix.missing_ffmpeg(found, run=command)
    if missing:
        raise InstallError('FFmpeg build is missing required capabilities: ' + ', '.join(missing))
    command([found['ffprobe'], '-version'], capture=True, timeout=60)
    command([found['sox'], '--version'], capture=True, timeout=60)
    # libfdk_aac and GPU remain optional. Render/decode/QC are unchanged.


def check_source(source, previous, repository, run_git):
    physical(source)
    physical(source / '.git')
    if not (source / '.git').is_dir():
        raise InstallError('Existing source is not an independent managed Git checkout')
    if not previous or previous.get('sourceRoot') != str(source) or previous.get('repository') != repository:
        raise InstallError('Existing source has no matching installation ownership record; developer clones are not adopted.')
    if run_git('-C', source, 'remote', 'get-url', 'origin') != repository:
        raise InstallError('Source origin differs from the installation repository')
    if run_git('-C', source, 'status', '--porcelain', '--untracked-files=all'):
        raise InstallError('Source has local changes; refusing update. Back up changes; no reset/clean was performed.')
    if run_git('-C', source, 'rev-parse', 'HEAD') != previous.get('commit'):
        raise InstallError('Source has local commits; refusing to overwrite them')


def validate_git_source(source, commit, run_git):
    rows = run_git('-C', source, 'ls-tree', '-r', '-z', commit, '--', *REQUIRED_SOURCE).split('\0')
    files = {}
    for row in rows:
        if row:
            metadata, name = row.split('\t', 1)
            files[name] = metadata.split()[:2]
    for name in REQUIRED_SOURCE:
        if files.get(name) not in (['100644', 'blob'], ['100755', 'blob']):
            raise SourceCompatibilityError('Not a compatible Framivra source (required regular blob): ' + name)


def json_record(path):
    path = physical(path)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 65536:
        raise InstallError('Invalid installation state: ' + str(path))
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise InstallError('Installation state must be a JSON object: ' + str(path))
    return value


def validate_ownership(record, root, repository):
    if record is not None and (record.get('schema') != SCHEMA
            or record.get('sourceRoot') != str(root / 'source')
            or record.get('repository') != repository
            or record.get('status') not in ('preparing', 'ready')):
        raise InstallError('Existing installation receipt has no matching ownership; unrelated environments are not adopted')


def update_source(root, repository, ref, update=False, source_commit=None, *, run_git=None):
    if run_git is None:
        run_git = git
    if not (repository == REPOSITORY or Path(repository).is_absolute()):
        raise InstallError('Only the official HTTPS repository or an explicitly trusted absolute local repository is supported')
    if not re.fullmatch(r'(?:main|v\d+\.\d+\.\d+(?:-rc\.\d+)?|[0-9a-f]{40})', ref):
        raise InstallError('Source ref must be main, a version tag, or a full commit SHA; no implicit stable/latest resolution')
    if source_commit is not None and (ref != 'main' or not re.fullmatch('[0-9a-f]{40}', source_commit)):
        raise InstallError('--source-commit must be a full SHA for the explicit main channel')
    source, record = physical(root / 'source'), physical(root / 'installation.json')
    progress = physical(root / '.source-progress.json')
    previous, pending = json_record(record), json_record(progress)
    validate_ownership(previous, root, repository)
    approved = (pending is not None and pending.get('schema') == 'framivra.source-progress/v1'
                and pending.get('repository') == repository
                and re.fullmatch('[0-9a-f]{40}', pending.get('commit', '')))
    if pending and not approved:
        raise InstallError('Source recovery record has no matching approved identity')
    recovered_checkout = False
    if source.exists():
        # Recover only a clean checkout of the exact previously approved update.
        # A crash after checkout but before receipt publication is not a user
        # local commit. Dirty or unrelated heads remain refusal, never reset.
        if (approved and pending.get('kind') == 'update' and previous
                and pending.get('oldCommit') == previous.get('commit')
                and run_git('-C', source, 'rev-parse', 'HEAD') == pending['commit']):
            previous = dict(previous, commit=pending['commit'], ref=pending['ref'], status='preparing')
            check_source(source, previous, repository, run_git)
            atomic_json(record, previous)
            recovered_checkout = True
        check_source(source, previous, repository, run_git)
        continuing_update = (approved and pending.get('kind') == 'update' and pending.get('ref') == ref
                             and pending.get('oldCommit') == previous.get('commit'))
        if (not update and not continuing_update) or recovered_checkout:
            if ref != previous.get('ref'):
                raise InstallError('Changing source ref requires explicit --update')
            atomic_json(record, dict(previous, status='preparing', sourceApproved=True))
            if approved and pending['commit'] == previous['commit']:
                physical(progress).unlink()
            return previous['commit']
    reuse = approved and pending.get('ref') == ref and (
        (pending.get('kind') == 'clone' and not source.exists()) or
        (pending.get('kind') == 'update' and source.exists()
         and pending.get('oldCommit') == previous.get('commit')))
    if reuse:
        commit = pending['commit']
    elif source_commit is not None:
        commit = source_commit
    elif re.fullmatch('[0-9a-f]{40}', ref):
        commit = ref
    else:
        target = 'refs/heads/main' if ref == 'main' else 'refs/tags/' + ref
        remote = run_git('ls-remote', repository, target, target + '^{}')
        identities = {name: sha for sha, name in (line.split() for line in remote.splitlines())}
        commit = identities.get(target + '^{}', identities.get(target))
    if not commit or not re.fullmatch('[0-9a-f]{40}', commit):
        raise InstallError('Source ref could not be resolved; no fallback to another channel')
    if source.exists():
        atomic_json(record, dict(previous, status='preparing'))
        atomic_json(progress, dict(schema='framivra.source-progress/v1', kind='update',
                    repository=repository, ref=ref, commit=commit, oldCommit=previous['commit']))
        run_git('-C', source, 'fetch', '--depth=1', 'origin', commit)
        try:
            validate_git_source(source, commit, run_git)
        except SourceCompatibilityError:
            # The target was explicitly rejected, not interrupted. Do not keep
            # retrying an incompatible release or silently install the old one.
            physical(progress).unlink()
            raise
        incoming = run_git('-C', source, 'ls-tree', '-r', '--name-only', '-z', commit).split('\0')
        ignored = run_git('-C', source, 'ls-files', '-o', '-i', '--exclude-standard', '-z').split('\0')
        incoming, ignored = [x for x in incoming if x], [x for x in ignored if x]
        if any(a == b or a.startswith(b + '/') or b.startswith(a + '/') for a in incoming for b in ignored):
            raise InstallError('Incoming source conflicts with ignored local files; update refused')
        run_git('-C', source, 'checkout', '--detach', commit)
    else:
        if reuse:
            name = pending.get('stage', '')
            if not re.fullmatch('source-stage-[0-9a-f]{32}', name):
                raise InstallError('Invalid owned source staging path')
            stage = physical(root / name)
        else:
            stage = physical(root / ('source-stage-' + uuid.uuid4().hex))
            atomic_json(progress, dict(schema='framivra.source-progress/v1', kind='clone',
                        repository=repository, ref=ref, commit=commit, stage=stage.name))
        physical(stage / '.git')
        if not (stage / '.git').exists():
            if stage.exists() and any(stage.iterdir()):
                raise InstallError('Owned source staging contains unexpected files; not overwritten')
            run_git('init', stage)
        try:
            origin = run_git('-C', stage, 'remote', 'get-url', 'origin')
        except InstallError:
            origin = ''
        if not origin:
            run_git('-C', stage, 'remote', 'add', 'origin', repository)
        elif origin != repository:
            raise InstallError('Owned source staging origin changed; not adopted')
        if run_git('-C', stage, 'status', '--porcelain', '--untracked-files=all'):
            raise InstallError('Source staging has local changes; not overwritten')
        try:
            run_git('-C', stage, 'cat-file', '-e', commit + '^{commit}')
        except InstallError:
            run_git('-C', stage, 'fetch', '--depth=1', 'origin', commit)
        try:
            validate_git_source(stage, commit, run_git)
        except SourceCompatibilityError:
            physical(progress).unlink()
            raise
        run_git('-C', stage, 'checkout', '--detach', commit)
        if run_git('-C', stage, 'rev-parse', 'HEAD') != commit:
            raise InstallError('Downloaded source commit identity mismatch')
        atomic_json(record, dict(previous or {}, schema=SCHEMA, status='preparing', repository=repository,
                                sourceRoot=str(source), commit=commit, ref=ref, sourceApproved=True))
        stage.rename(physical(source))
    if run_git('-C', source, 'rev-parse', 'HEAD') != commit:
        raise InstallError('Downloaded source commit does not match the approved identity')
    for name in REQUIRED_SOURCE:
        if not (source / name).is_file():
            raise InstallError('Not a compatible Framivra source: ' + name)
    atomic_json(record, dict(previous or {}, schema=SCHEMA, status='preparing', repository=repository,
                            sourceRoot=str(source), commit=commit, ref=ref, sourceApproved=True))
    if progress.exists():
        physical(progress).unlink()
    return commit


def runtime_env(root, found):
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VTX_CONFIG', 'VTX_GFX', 'VTX_CEF_WORKER', 'VTX_GFX_BACKEND'):
        env.pop(key, None)
    binary = root / 'venv' / ('Scripts' if sys.platform == 'win32' else 'bin')
    tool_paths = list(found.values())
    if sys.platform in ('darwin', 'win32'):
        # Git/SoX or a shared shims directory can also contain unqualified
        # FFmpeg. The installer and the ready CLI must both prefer the pair.
        tool_paths = [found[name] for name in ('ffmpeg', 'ffprobe', 'git', 'sox', 'chrome') if name in found]
    if sys.platform == 'win32':
        env.setdefault('NoDefaultCurrentDirectoryInExePath', '1')
    env.update(PATH=os.pathsep.join(dict.fromkeys([str(binary), *(str(Path(x).parent) for x in tool_paths), env.get('PATH', '')])),
               VTX_CHROME=found['chrome'], VTX_GFX='cdp', VTX_NO_NVENC='1',
               VTX_CONFIG=str(root / 'config.json'), FRAMIVRA_VERIFY_ROOT=str(root),
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    return env


def prepare_python(root, source, found, *, network='cn', update=False):
    target = physical(root / 'venv')
    python = target / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    env = runtime_env(root, found)
    repair = not python.is_file()
    if not repair:
        try:
            command([python, '-I', '-c', 'import pip'], env=env, capture=True, timeout=30)
        except InstallError:
            repair = True
    if repair:
        try:
            # Idempotent repair of our partial venv; never clear/delete it.
            # Apple's Xcode/CLT framework Python requires a symlinked interpreter.
            venv.EnvBuilder(with_pip=True, symlinks=(sys.platform == 'darwin')).create(target)
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallError('Python venv/ensurepip unavailable. Install python3-venv (Debian/Ubuntu) or a complete Python distribution and rerun: ' + str(error)) from error
    python_packages.install(python, root, env, PYTHON_PACKAGES, network=network, update=update, run=command)
    record = json_record(root / 'installation.json')
    if record is not None:
        atomic_json(root / 'installation.json', dict(record, pythonDependenciesPrepared=True))
    # The project itself stays offline; user/system pip configuration cannot add
    # indexes, build dependencies or an unrelated editable package.
    editable_env = dict(env, PIP_CONFIG_FILE=os.devnull)
    command([python, '-I', '-m', 'pip', '--isolated', 'install', '--no-index', '--no-deps', '--no-build-isolation', '--editable', source], env=editable_env)
    return python, env


def prepare_configuration(root, source, python):
    target = root / 'config.json'
    if target.exists():
        physical(target)
        value = json.loads(target.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise InstallError('Existing user configuration must be a JSON object')
        return  # Never rewrite existing choices or secrets.
    value = json.loads((source / 'config.example.json').read_text(encoding='utf-8'))
    value = {key: item for key, item in value.items() if not key.startswith('_comment')}
    for key in ('asr', 'tts', 'imgen', 'comfyui', 'denoise'):
        value[key] = {'enabled': False}
    value['bgm'] = {'enabled': True, 'python': str(python), 'runtime': None}
    atomic_json(target, value)


def verify_runtime_tool_paths(root, python, env, tools):
    # A retry may run older owned source. Check its actual ready order as well
    # as the installer's probe order before publishing ready.
    code = '''import json, os, shutil, sys
from pathlib import Path
from vtx.runtime import managed
root, tools = Path(sys.argv[1]), json.loads(sys.argv[2])
legacy = ('git', 'ffmpeg', 'ffprobe', 'sox', 'chrome')
order = getattr(managed, 'tool_search_order', lambda: legacy)()
windows = sys.platform == 'win32'
scripts = 'venv/Scripts' if windows else 'venv/bin'
ready_paths = [str(root / scripts), *(str(Path(tools[name]).parent) for name in order)]
if windows:
    def mismatches(paths):
        shadowed = []
        for name in ('ffmpeg', 'ffprobe', 'git', 'sox'):
            candidates = (Path(d.strip(chr(34))) / (name + '.exe') for d in paths if d)
            selected = next((p for p in candidates if p.is_file()), None)
            if selected is None or selected.resolve() != Path(tools[name]).resolve():
                shadowed.append(name)
        return shadowed
    check = getattr(managed, 'shadowed_windows_tools', None)
    if check is not None:
        bad = sorted(set(check(ready_paths, tools) + check(os.environ['PATH'].split(os.pathsep), tools)))
    else:
        bad = sorted(set(mismatches(ready_paths) + mismatches(os.environ['PATH'].split(os.pathsep))))
else:
    path = os.pathsep.join(ready_paths)
    bad = [name for name in ('ffmpeg', 'ffprobe')
           if not (exe := shutil.which(name, path=path)) or Path(exe).resolve() != Path(tools[name])]
print(json.dumps(bad))
'''
    shadowed = json.loads(command([python, '-I', '-c', code, root, json.dumps(tools)],
                                  env=env, cwd=root, capture=True, timeout=60))
    if shadowed:
        raise InstallError('Managed runtime PATH shadows selected tools: ' + ', '.join(shadowed) +
                           '. Remove the conflicting PATH tools or rerun with --update for an older owned source.')


def verify(root, source, python, env, *, verify_render=False):
    # These are installation probes, not the frozen public-film acceptance gate.
    capabilities = {}
    vtx = root / 'venv' / ('Scripts/vtx.exe' if sys.platform == 'win32' else 'bin/vtx')
    for family, arguments in (('read', ['capabilities']), ('store', ['store', 'capabilities'])):
        value = json.loads(command([vtx, *arguments], env=env, cwd=root, capture=True, timeout=60))
        capabilities[family] = value
    if capabilities['read'].get('schema') != 'framivra.timeline.read/v1':
        raise InstallError('Installed source does not support the sidebar read protocol')
    command([python, '-I', '-c', 'import numpy, scipy, soundfile, soxr, PIL'], env=env, cwd=root)
    command([python, source / 'apps/sync/sync_audio.py', '--help'], env=env, cwd=root)
    music = json.loads(command([python, source / 'apps/bgm/bgm.py', 'render', source / 'apps/bgm/examples/synth.json', '--quiet', '-o', root / 'verification' / uuid.uuid4().hex], env=env, cwd=root, capture=True))
    if music.get('qc_ok') is not True:
        raise InstallError('Non-AI music synthesis/QC failed')
    if not verify_render:
        return capabilities, None
    # Existing independent HTML/subtitle/media fixture + real all/strict QC/decode.
    evidence = root / 'verification' / ('cdp-' + uuid.uuid4().hex)
    ffmpeg = env_tool(env, 'ffmpeg')
    command([python, source / 'tests/runners/run_e2e.py', '--repo', source, '--output', evidence,
             '--ffmpeg', ffmpeg, '--chrome', env['VTX_CHROME'], '--scope', 'capability'], env=env, cwd=root, timeout=3600)
    command([python, source / 'tests/runners/verify_e2e.py', evidence, '--ffmpeg', ffmpeg, '--scope', 'capability'], env=env, cwd=root)
    return capabilities, str(evidence)


def env_tool(env, name):
    if sys.platform == 'win32':
        # CreateProcess searches .exe for bare native command names, not PATHEXT.
        candidates = (Path(d.strip('"')) / (name + '.exe') for d in env['PATH'].split(os.pathsep) if d)
        value = next((str(p) for p in candidates if p.is_file()), None)
    else:
        value = shutil.which(name, path=env['PATH'])
    if not value:
        raise InstallError('Tool not available in the managed environment: ' + name)
    return value


def windows_elevated():
    if sys.platform != 'win32':
        return False
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


class PreviewEnrollmentInterrupted(KeyboardInterrupt):
    pass


@contextmanager
def _preview_cancellation():
    signals = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    cancelled = None

    def mark(signum, _frame):
        nonlocal cancelled
        cancelled = cancelled or signum

    def check():
        if cancelled is not None:
            raise PreviewEnrollmentInterrupted(cancelled)

    previous = {signum: signal.signal(signum, mark) for signum in signals}
    try:
        yield check
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _preview_helper_bytes(path, digest):
    limit = 256 * 1024
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise InstallError('Missing release-reviewed preview helper SHA-256')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= limit:
            raise InstallError('Preview supplier helper is not a bounded physical file')
        raw = stream.read(limit + 1)
    if len(raw) != info.st_size or hashlib.sha256(raw).hexdigest() != digest:
        raise InstallError('Preview supplier helper differs from release-approved bytes')
    return raw


def _preview_build_record(record):
    tools = record.get('buildTools') if type(record) is dict else None
    if (type(tools) is not dict or tools.keys() != {'python', 'pip', 'setuptools', 'wheel'}
            or not all(type(v) is str and re.fullmatch(r'[A-Za-z0-9.+!_-]{1,64}', v) for v in tools.values())
            or record.keys() != {'buildTools', 'wheelFilename', 'wheelBytes', 'wheelSha256',
                                 'launcherSha256', 'runtimeProjectionSha256'}
            or type(record['wheelFilename']) is not str
            or not re.fullmatch(r'framivra-[A-Za-z0-9_.+]+-py3-none-any\.whl', record['wheelFilename'])
            or type(record['wheelBytes']) is not int or not 0 < record['wheelBytes'] <= 64 * 1024**2
            or not all(type(record[k]) is str and re.fullmatch('[0-9a-f]{64}', record[k])
                       for k in ('wheelSha256', 'launcherSha256', 'runtimeProjectionSha256'))):
        raise InstallError('Invalid release-reviewed preview build catalog entry')
    return tuple(sorted(tools.items()))


def optional_preview_supplier(root, source, commit, repository, python, *, check=lambda: None):
    # This hook runs under installation_lock. Unapproved installations never
    # look at the destination, load an optional helper or build a wheel.
    if sys.platform != 'linux' or repository != REPOSITORY or not _PREVIEW_SUPPLIER_RELEASES:
        return None
    tree = git('ls-tree', '-r', commit, '--', *_PREVIEW_SOURCE, cwd=source)
    key = hashlib.sha256((tree + '\n').encode('ascii')).hexdigest()
    if key not in _PREVIEW_SUPPLIER_RELEASES:
        return None
    approved = _PREVIEW_SUPPLIER_RELEASES[key]
    if type(approved) is not dict:
        raise InstallError('Invalid release-reviewed preview build catalog entry')
    variants = None
    if 'variants' in approved:
        variants = approved['variants']
        if approved.keys() != {'helperSha256', 'variants'} or type(variants) not in (list, tuple) or not variants:
            raise InstallError('Invalid release-reviewed preview build variants')
        toolsets = [_preview_build_record(row) for row in variants]
        if len(set(toolsets)) != len(toolsets):
            raise InstallError('Duplicate release-reviewed preview build tool versions')
    else:
        _preview_build_record({k: v for k, v in approved.items() if k != 'helperSha256'})
    helper_name = 'scripts/release/prepare_preview_supplier.py'
    helper_path = physical(source / helper_name)
    raw = _preview_helper_bytes(helper_path, approved.get('helperSha256'))
    check()
    helper = ModuleType('_framivra_preview_supplier')
    helper.__file__ = str(helper_path)
    exec(compile(raw, str(helper_path), 'exec'), helper.__dict__)
    launcher = physical(source / 'scripts/preview_window_wheel.py')
    if variants is not None:
        enroll = getattr(helper, 'enroll_approved_variants', None)
        if not callable(enroll):
            raise InstallError('Release-approved preview helper does not support build variants')
        return enroll(root, launcher_path=launcher, source=source, commit=commit,
                      source_tree_sha256=key, variants=variants, python=python, check=check)

    def prepare_wheel(scratch, child_check):
        return helper.build_approved_wheel(python, scratch, source, commit,
            source_tree_sha256=key, build_tools=approved['buildTools'], wheel_filename=approved['wheelFilename'],
            wheel_bytes=approved['wheelBytes'], wheel_sha256=approved['wheelSha256'], check=child_check)

    return helper.enroll_profile(root, launcher_path=launcher,
        prepare_wheel=prepare_wheel, wheel_filename=approved['wheelFilename'], launcher_sha256=approved['launcherSha256'],
        wheel_sha256=approved['wheelSha256'], projection_sha256=approved['runtimeProjectionSha256'], python=python,
        check=check)


def _register_optional_preview(root, source, commit, repository, python, core):
    if sys.platform != 'linux' or repository != REPOSITORY or not _PREVIEW_SUPPLIER_RELEASES:
        return 0
    registration = root / 'installation.json'
    attempted = None
    try:
        with _preview_cancellation() as check:
            supplier = optional_preview_supplier(root, source, commit, repository, python, check=check)
            check()
            if supplier is not None:
                attempted = dict(core, previewSupplier=supplier)
                atomic_json(registration, attempted, before_replace=check)
    except (PreviewEnrollmentInterrupted, OSError, ValueError, TypeError, KeyError, RuntimeError,
            SyntaxError, subprocess.SubprocessError) as error:
        try:
            observed = json_record(registration)
        except (OSError, ValueError, RuntimeError):
            observed = None
        if observed == core:
            outcome = 'Core Ready; optional ordinary preview supplier not registered'
        elif attempted is not None and observed == attempted:
            outcome = 'Core and supplier Ready registration observed; optional publication completion unconfirmed'
        else:
            outcome = 'Core was Ready before optional enrollment; registration state now unconfirmed'
        print('[install] ' + outcome + ': ' + (str(error) or type(error).__name__), file=sys.stderr)
        if isinstance(error, PreviewEnrollmentInterrupted):
            return 128 + error.args[0]
    return 0


def install(args):
    if sys.platform not in ('linux', 'darwin', 'win32'):
        raise InstallError('Supported systems: Linux, macOS, Windows')
    if sys.platform == 'win32' and (platform.machine().lower() not in ('amd64', 'x86_64') or sys.maxsize <= 2**32):
        raise InstallError('Current Windows browser identity supports native x64 only')
    if sys.platform == 'darwin' and platform.machine().lower() not in ('arm64', 'aarch64'):
        raise InstallError('Current macOS CDP identity supports Apple Silicon arm64 only; Intel macOS is not a verified install target')
    root = physical(args.root or default_root())
    if root in (Path.home(), Path(root.anchor)):
        raise InstallError('Installation root cannot be HOME or a filesystem root')
    print('Source deployment: official Git/HTTPS trust, NOT signed-release provenance.\n'
          'Install root: %s\nChannel: %s (resolved to an exact commit)\nNetwork: %s\n'
          'Downloads: source, PyPI packages, native packages and pinned public fonts.\n'
          'If release-approved for this Linux source, also build the previously pinned ordinary preview wheel locally and run a local synthetic A/V check (separate from --verify-render).\n'
          'Verified cache/partials persist under cache/downloads. No AI models/services.\n'
          'System packages may require administrator approval; native managers display additional side effects.' % (root, args.ref, args.network), flush=True)
    if args.plan:
        print(json.dumps({'root': str(root), 'profile': 'non-ai', 'renderer': 'cdp', 'network': args.network,
                          'verifyRender': args.verify_render,
                          'ordinaryPreviewSupplierConditional': bool(sys.platform == 'linux' and args.repo == REPOSITORY
                                                                     and _PREVIEW_SUPPLIER_RELEASES),
                          'pythonPackages': PYTHON_PACKAGES, 'nativeCommandsConditional': True,
                          'nativeCommands': platform_packages(tools(root), needs_venv=sys.platform == 'linux' and importlib.util.find_spec('ensurepip') is None)}, indent=2))
        return 0
    if windows_elevated():
        raise InstallError('Per-user Windows installation requires a non-administrator PowerShell; '
                           'rerun from an ordinary PowerShell. Existing files and ACLs were not changed.')
    if not args.yes:
        if not sys.stdin.isatty():
            raise InstallError('Non-interactive installation requires explicit --yes (review --plan first)')
        if input('Proceed? [y/N] ').strip().lower() not in ('y', 'yes'):
            raise InstallError('Installation cancelled before writes')
    # A persistent Windows user directory inherits its trusted parent's ACL.
    # Python's Windows 0700 uses OWNER RIGHTS, not a fixed user SID. Temporary
    # directories retain their protected ACLs; never publish them across tokens.
    if sys.platform == 'win32':
        root.mkdir(parents=True, exist_ok=True)
    else:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with installation_lock(root):
        previous = json_record(root / 'installation.json')
        validate_ownership(previous, root, args.repo)
        # A settled channel mismatch needs no preparation. Leave source recovery
        # (including any unexpected progress entry) to the existing Git path.
        if (previous is not None and previous['status'] == 'ready' and not args.update
                and isinstance(previous.get('ref'), str) and previous['ref'] and args.ref != previous['ref']
                and not os.path.lexists(root / '.source-progress.json')):
            raise InstallError('Changing source ref requires explicit --update')
        if previous is None:
            for name in ('source', 'venv', 'bin/vtx'):
                if (root / name).exists():
                    raise InstallError('Refusing existing unmanaged installation component: ' + name)
        if args.restart_update:
            if physical(root / '.source-progress.json').exists():
                raise InstallError('A source checkout is still in progress; resume it without --restart-update')
            if previous is None or not physical(root / 'source').is_dir():
                raise InstallError('--restart-update requires an existing managed source checkout')
        resuming = (not args.restart_update and previous is not None and previous.get('status') == 'preparing'
                    and previous.get('sourceApproved') is True
                    and previous.get('updateRequested') == args.update
                    and previous.get('requestedRef') == args.ref)
        found = prepare_tools(args.no_system, root, args.network, args.yes)
        native_metadata = found.pop('_native_metadata', None)
        verify_native_tools(found)
        source_git = git
        if sys.platform == 'win32':
            selected = Path(found.get('git') or '')
            if not selected.is_absolute() or not selected.is_file() or (os.name == 'nt' and selected.suffix.lower() != '.exe'):
                raise InstallError('Windows source checkout requires a selected absolute Git executable')
            # CreateProcess can prefer the parent directory over PATH even when
            # we pass a different PATH to the child. Bind argv[0] explicitly.
            source_git = partial(git, executable=str(selected))
        # Keep native tools available to subprocesses without persisting PATH.
        os.environ['PATH'] = os.pathsep.join(dict.fromkeys([
            *(str(Path(value).parent) for value in found.values()), os.environ.get('PATH', '')]))
        ownership = dict(previous or dict(schema=SCHEMA, repository=args.repo,
                                          sourceRoot=str(root / 'source'), ref=args.ref))
        ownership.pop('verification', None)
        ownership.pop('previewSupplier', None)
        atomic_json(root / 'installation.json', dict(ownership, status='preparing', sourceApproved=bool(resuming),
                    updateRequested=args.update, requestedRef=args.ref,
                    pythonDependenciesPrepared=bool(resuming and previous.get('pythonDependenciesPrepared'))))
        commit = update_source(root, args.repo, args.ref, args.update and not resuming, args.source_commit,
                               run_git=source_git)
        print('[install] Source commit: ' + commit +
              (' (resuming; use --update --restart-update to select a new target)' if resuming and args.update else ''),
              flush=True)
        source = root / 'source'
        preparing = json_record(root / 'installation.json')
        native_metadata = native_metadata or preparing.get('nativePreparation', {})
        atomic_json(root / 'installation.json', dict(preparing, tools=found, network=args.network,
                    nativePreparation=native_metadata))
        python, env = prepare_python(root, source, found, network=args.network,
                                     update=args.update and not preparing.get('pythonDependenciesPrepared', False))
        if sys.platform == 'win32':
            runtime = windows.ensure_python_runtime(root, python, env, network=args.network,
                                                     no_system=args.no_system, yes=args.yes, run=command)
            native_metadata = dict(native_metadata, pythonRuntime=runtime)
            current = json_record(root / 'installation.json')
            atomic_json(root / 'installation.json', dict(current, nativePreparation=native_metadata))
        prepare_configuration(root, source, python)
        # Use the new, commit-bound installer bundle even when resuming an older
        # owned source. Network fixes do not force an implicit source update.
        font_script = Path(__file__).resolve().with_name('install_runtime_fonts.py')
        font_report = json.loads(command([python, font_script, '--root', root, '--network', args.network], env=env, cwd=root, capture=True, timeout=None))
        font_environment = font_report.get('environment', {})
        expected = {'FONTCONFIG_FILE': str(root / 'fonts/fonts.conf')} if sys.platform == 'linux' else {}
        if font_environment != expected:
            raise InstallError('Font installer returned an unexpected runtime environment')
        env.update(font_environment)
        if sys.platform in ('darwin', 'win32'):
            verify_runtime_tool_paths(root, python, env, found)
        capabilities, evidence = verify(root, source, python, env, verify_render=args.verify_render)
        vtx = root / 'venv' / ('Scripts/vtx.exe' if sys.platform == 'win32' else 'bin/vtx')
        executable = vtx
        if sys.platform != 'win32':
            binary = physical(root / 'bin')
            binary.mkdir(exist_ok=True)
            executable = binary / 'vtx'
            physical(executable)
            executable.write_text('#!/bin/sh\nexec ' + shlex.quote(str(vtx)) + ' "$@"\n', encoding='utf-8')
            executable.chmod(0o700)
        receipt = dict(schema=SCHEMA, status='ready',
                       profile='non-ai', renderer='cdp', repository=args.repo, ref=args.ref,
                       commit=commit, version=(source / 'VERSION').read_text(encoding='utf-8').strip(),
                       sourceRoot=str(source), pythonExecutable=str(python), vtxExecutable=str(executable),
                       configFile=str(root / 'config.json'), tools=found, environment=font_environment,
                       network=args.network, nativePreparation=native_metadata,
                       bootstrapCommit=args.source_commit, capabilities=capabilities,
                       installedAt=datetime.now(timezone.utc).isoformat())
        if evidence is not None:
            receipt['verification'] = evidence
        # The ordinary core is usable before optional enrollment begins. An
        # interruption during supplier qualification therefore leaves Ready
        # core metadata without any unverified or stale supplier assertion.
        atomic_json(root / 'installation.json', receipt)
        preview_status = _register_optional_preview(root, source, commit, args.repo, python, receipt)
        if preview_status:
            return preview_status
    print('[install] CDP render verification: ' + ('passed (' + evidence + ')' if evidence is not None else 'skipped (opt in with --verify-render)'))
    print('\nREADY: non-AI CDP environment. In DSH: scan installed kernels → select the registered installation → save. Use connection diagnostics only if needed.\nVTX: ' + str(executable))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', help='explicit managed root; DSH auto-scans the default root')
    parser.add_argument('--repo', default=REPOSITORY, help='official repository or explicitly trusted absolute local source')
    parser.add_argument('--ref', default='main', help='explicit source channel: main, tag, or full commit')
    parser.add_argument('--source-commit', help='bootstrap-resolved main SHA; existing owned source is reused without --update')
    parser.add_argument('--network', choices=('cn', 'official'), default='cn', help='cn: trusted domestic mirrors first, then same-artifact official fallback')
    parser.add_argument('--yes', action='store_true', help='approve displayed downloads/writes (no AI)')
    parser.add_argument('--plan', action='store_true', help='read-only plan: no network, mkdir, install or execution of candidates')
    parser.add_argument('--no-system', action='store_true', help='use existing native tools; fail if any required tool is missing')
    parser.add_argument('--verify-render', action='store_true', help='run the CDP end-to-end render and QC (off by default; may take several minutes)')
    parser.add_argument('--update', action='store_true', help='explicitly update a clean owned checkout; otherwise reuse pinned source')
    parser.add_argument('--restart-update', action='store_true', help='with --update, select a new target instead of resuming a completed checkout')
    args = parser.parse_args(argv)
    if args.restart_update and not args.update:
        parser.error('--restart-update requires --update')
    resume = 'Rerun without --restart-update to resume.' if args.restart_update else 'Rerun the same command to resume.'
    try:
        return install(args)
    except (InstallError, downloads.DownloadError, posix.NativeInstallError, OSError, ValueError, subprocess.SubprocessError) as error:
        print('[install] FAILED: ' + str(error) + '\n' + resume + ' No failed installation was registered ready.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('[install] Interrupted; verified cache and partial downloads were retained. ' + resume, file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
