#!/usr/bin/env python3
"""Managed source deployment (non-AI, CDP). Bootstrap scripts only obtain Python.

Uses system package managers for missing native tools, a private venv for Python
packages, and an explicit Git source channel (main by default, never 'stable').
No model, service, credential, project, or existing developer checkout is changed.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import uuid
import venv

SCHEMA = 'framivra.installation/v1'
REPOSITORY = 'https://github.com/CongliangK/framivra.git'
PYTHON_PACKAGES = ('setuptools>=68,<81', 'wheel>=0.42,<1', 'numpy>=1.26,<3',
                   'scipy>=1.11,<2', 'soundfile>=0.12,<1', 'soxr>=0.3,<1',
                   'Pillow>=10,<13')
TOOL_NAMES = ('git', 'ffmpeg', 'ffprobe', 'sox')
REQUIRED_SOURCE = ('pyproject.toml', 'vtx/cli/commands.py', 'plugins/registry.json',
                   'scripts/install_source.py', 'scripts/install_runtime_fonts.py',
                   'scripts/demo_fonts.py', 'vtx/runtime/managed.py')


class InstallError(RuntimeError):
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
    print('[install] ' + ' '.join(shlex.quote(str(x)) for x in argv), flush=True)
    result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env,
                            capture_output=capture, text=True, encoding='utf-8',
                            errors='strict', timeout=timeout)
    if result.returncode:
        raise InstallError('Command failed (%s): %s%s' % (result.returncode, argv[0],
                           ': ' + result.stderr[-4000:] if capture else ''))
    return result.stdout.strip() if capture else ''


def git(*args, cwd=None):
    # No downloaded hooks, Git user filters/templates, or interactive credentials.
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0')
    for key in list(env):
        if key.startswith('GIT_') and key not in ('GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_TERMINAL_PROMPT'):
            env.pop(key)
    return command(['git', '-c', 'core.hooksPath=' + os.devnull, *args], cwd=cwd,
                   env=env, capture=True, timeout=300)


def atomic_json(path, value):
    physical(path)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temp.open('x', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
        temp.chmod(0o600)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def installation_lock(root):
    path = root / '.install.lock'
    try:
        handle = path.open('x', encoding='utf-8')
    except FileExistsError as error:
        raise InstallError('Installation already running (or interrupted): inspect ' + str(path)) from error
    try:
        with handle:
            handle.write(str(os.getpid()) + '\n')
        yield
    finally:
        path.unlink()


def native_browser():
    candidates = [os.environ.get('VTX_CHROME')]
    candidates += [shutil.which(name) for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome', 'msedge')]
    if sys.platform == 'darwin':
        # Current macOS environment identity supports Google Chrome arm64.
        candidates = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
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


def tools():
    result = {name: str(Path(value).resolve()) for name in TOOL_NAMES
              if (value := shutil.which(name))}
    browser = native_browser()
    if browser:
        result['chrome'] = browser
    return result


def platform_packages(found, system=None, manager=None):
    """Pure plan: native packages only; no models or optional experimental backends."""
    system = system or sys.platform
    missing = set((*TOOL_NAMES, 'chrome')) - set(found)
    if not missing:
        return []
    if system == 'win32':
        ids = {'git': 'Git.Git', 'ffmpeg': 'Gyan.FFmpeg', 'ffprobe': 'Gyan.FFmpeg',
               'chrome': 'Google.Chrome', 'sox': 'ChrisBagwell.SoX'}
        return [['winget', 'install', '--id', package, '-e', '--accept-source-agreements',
                 '--accept-package-agreements', '--disable-interactivity']
                for package in dict.fromkeys(ids[key] for key in sorted(missing))]
    if system == 'darwin':
        result = [['brew', 'install', package] for package in ('git', 'ffmpeg', 'sox')
                  if package in missing or (package == 'ffmpeg' and 'ffprobe' in missing)]
        if 'chrome' in missing:
            result.append(['brew', 'install', '--cask', 'google-chrome'])
        return result
    mapping = {
        'apt-get': ('apt-get', 'install', '-y', 'git', 'ffmpeg', 'sox', 'chromium', 'python3-venv', 'libegl1', 'libgbm1', 'fontconfig'),
        'dnf': ('dnf', 'install', '-y', 'git', 'ffmpeg', 'sox', 'chromium', 'python3-pip', 'fontconfig'),
        'pacman': ('pacman', '-S', '--needed', '--noconfirm', 'git', 'ffmpeg', 'sox', 'chromium', 'python-pip', 'fontconfig'),
    }
    manager = manager or next((p for p in mapping if shutil.which(p)), None)
    if manager not in mapping:
        raise InstallError('Supported Linux package managers: apt-get, dnf, pacman; install Git, Python venv, FFmpeg, SoX and native Chromium first.')
    return [list(mapping[manager])]


def refresh_windows_path():
    if sys.platform != 'win32':
        return
    import winreg
    values = []
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
                      (winreg.HKEY_CURRENT_USER, r'Environment')):
        with winreg.OpenKey(hive, key) as handle:
            values.append(os.path.expandvars(winreg.QueryValueEx(handle, 'Path')[0]))
    os.environ['PATH'] = os.pathsep.join(values + [os.environ.get('PATH', '')])


def prepare_tools(no_system):
    found = tools()
    plans = platform_packages(found)
    if plans and no_system:
        raise InstallError('Missing native tools (--no-system): ' + ', '.join(sorted(set((*TOOL_NAMES, 'chrome')) - set(found))))
    for plan in plans:
        if not shutil.which(plan[0]):
            raise InstallError('Required package manager not found: ' + plan[0])
        if sys.platform == 'linux' and os.geteuid() != 0:
            plan = ['sudo', *plan]
        command(plan)
    refresh_windows_path()
    found = tools()
    missing = set((*TOOL_NAMES, 'chrome')) - set(found)
    if missing:
        raise InstallError('Packages did not provide working tools: ' + ', '.join(sorted(missing)) + '. Check package availability, repositories and PATH; rerun to resume.')
    return found


def verify_native_tools(found):
    encoders = command([found['ffmpeg'], '-hide_banner', '-encoders'], capture=True, timeout=60)
    filters = command([found['ffmpeg'], '-hide_banner', '-filters'], capture=True, timeout=60)
    required_encoders = ('libx264', 'aac', 'prores_ks', 'ffv1', 'pcm_s16le', 'pcm_f32le', 'flac', 'libmp3lame', 'png', 'mjpeg')
    required_filters = ('ass', 'overlay', 'scale', 'format', 'fps', 'trim', 'setpts', 'concat', 'alphamerge', 'color',
                        'amix', 'aresample', 'volume', 'aformat', 'alimiter', 'sidechaincompress', 'afade', 'atrim',
                        'adelay', 'asetpts', 'apad', 'asplit', 'pan', 'loudnorm', 'ebur128', 'blackdetect',
                        'silencedetect', 'astats', 'psnr', 'ssim')
    missing = [name for text, names in ((encoders, required_encoders), (filters, required_filters))
               for name in names if not re.search(r'\b' + name + r'\b', text)]
    if missing:
        raise InstallError('FFmpeg build is missing required capabilities: ' + ', '.join(missing))
    command([found['ffprobe'], '-version'], capture=True, timeout=60)
    command([found['sox'], '--version'], capture=True, timeout=60)
    # libfdk_aac and GPU are intentionally not hard requirements; AAC+CPU x264
    # are validated by actual render/decode/QC, not an unreviewed binary fallback.


def check_source(source, previous, repository):
    physical(source)
    physical(source / '.git')
    if not (source / '.git').is_dir():
        raise InstallError('Existing source is not an independent managed Git checkout')
    if not previous or previous.get('sourceRoot') != str(source) or previous.get('repository') != repository:
        raise InstallError('Existing source has no matching installation ownership record; developer clones are not adopted.')
    if git('-C', source, 'remote', 'get-url', 'origin') != repository:
        raise InstallError('Source origin differs from the installation repository')
    if git('-C', source, 'status', '--porcelain', '--untracked-files=all'):
        raise InstallError('Source has local changes; refusing update. Back up changes; no reset/clean was performed.')
    if git('-C', source, 'rev-parse', 'HEAD') != previous.get('commit'):
        raise InstallError('Source has local commits; refusing to overwrite them')


def validate_git_source(source, commit):
    rows = git('-C', source, 'ls-tree', '-r', '-z', commit, '--', *REQUIRED_SOURCE).split('\0')
    files = {}
    for row in rows:
        if row:
            metadata, name = row.split('\t', 1)
            files[name] = metadata.split()[:2]
    for name in REQUIRED_SOURCE:
        if files.get(name) not in (['100644', 'blob'], ['100755', 'blob']):
            raise InstallError('Not a compatible Framivra source (required regular blob): ' + name)


def update_source(root, repository, ref, update=False):
    if not (repository == REPOSITORY or Path(repository).is_absolute()):
        raise InstallError('Only the official HTTPS repository or an explicitly trusted absolute local repository is supported')
    if not re.fullmatch(r'(?:main|v\d+\.\d+\.\d+(?:-rc\.\d+)?|[0-9a-f]{40})', ref):
        raise InstallError('Source ref must be main, a version tag, or a full commit SHA; no implicit stable/latest resolution')
    source, record = root / 'source', root / 'installation.json'
    physical(record)
    previous = json.loads(record.read_text(encoding='utf-8')) if record.exists() else None
    if source.exists():
        check_source(source, previous, repository)
        if not update:
            if ref != previous.get('ref'):
                raise InstallError('Changing source ref requires explicit --update')
            atomic_json(record, dict(previous, status='preparing'))
            return previous['commit']
    target = ref if re.fullmatch('[0-9a-f]{40}', ref) else ('refs/heads/main' if ref == 'main' else 'refs/tags/' + ref)
    remote = git('ls-remote', repository, target, target + '^{}') if not re.fullmatch('[0-9a-f]{40}', ref) else ''
    rows = [line.split() for line in remote.splitlines()]
    identities = {name: sha for sha, name in rows}
    commit = ref if not remote else identities.get(target + '^{}', identities.get(target))
    if not commit or not re.fullmatch('[0-9a-f]{40}', commit):
        raise InstallError('Source ref could not be resolved; no fallback to another channel')
    if source.exists():
        atomic_json(record, dict(previous, status='preparing'))
        git('-C', source, 'fetch', '--depth=1', 'origin', commit)
        validate_git_source(source, commit)
        # Ignored files can still collide with newly tracked paths. Never reset.
        incoming = git('-C', source, 'ls-tree', '-r', '--name-only', '-z', commit).split('\0')
        ignored = git('-C', source, 'ls-files', '-o', '-i', '--exclude-standard', '-z').split('\0')
        incoming, ignored = [x for x in incoming if x], [x for x in ignored if x]
        if any(a == b or a.startswith(b + '/') or b.startswith(a + '/') for a in incoming for b in ignored):
            raise InstallError('Incoming source conflicts with ignored local files; update refused')
        git('-C', source, 'checkout', '--detach', commit)
        atomic_json(record, dict(schema=SCHEMA, status='preparing', repository=repository,
                                sourceRoot=str(source), commit=commit, ref=ref))
    else:
        # A network/checkout failure must not strand an unowned source/.git.
        # Retain failed uniquely-owned stages for diagnosis, never delete/adopt
        # an arbitrary existing checkout. A retry can create a fresh stage.
        stage = physical(root / ('source-stage-' + uuid.uuid4().hex))
        git('init', stage)
        git('-C', stage, 'remote', 'add', 'origin', repository)
        git('-C', stage, 'fetch', '--depth=1', 'origin', commit)
        validate_git_source(stage, commit)
        git('-C', stage, 'checkout', '--detach', 'FETCH_HEAD')
        if git('-C', stage, 'rev-parse', 'HEAD') != commit:
            raise InstallError('Downloaded source commit identity mismatch')
        for name in REQUIRED_SOURCE:
            if not (stage / name).is_file():
                raise InstallError('Not a compatible Framivra source: ' + name)
        # Ownership precedes publication; interruption cannot create an unowned
        # source. Preparing receipts are never accepted by DSH discovery.
        atomic_json(record, dict(schema=SCHEMA, status='preparing', repository=repository,
                                sourceRoot=str(source), commit=commit, ref=ref))
        stage.rename(source)
    actual = git('-C', source, 'rev-parse', 'HEAD')
    if actual != commit:
        raise InstallError('Downloaded source commit does not match the approved identity')
    for name in REQUIRED_SOURCE:
        if not (source / name).is_file():
            raise InstallError('Not a compatible Framivra source: ' + name)
    # Ownership is recorded even if later dependency preparation fails; not ready.
    atomic_json(record, dict(schema=SCHEMA, status='preparing', repository=repository,
                            sourceRoot=str(source), commit=commit, ref=ref))
    return commit


def runtime_env(root, found):
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VTX_CONFIG', 'VTX_GFX', 'VTX_CEF_WORKER', 'VTX_GFX_BACKEND'):
        env.pop(key, None)
    binary = root / 'venv' / ('Scripts' if sys.platform == 'win32' else 'bin')
    env.update(PATH=os.pathsep.join(dict.fromkeys([str(binary), *(str(Path(x).parent) for x in found.values()), env.get('PATH', '')])),
               VTX_CHROME=found['chrome'], VTX_GFX='cdp', VTX_NO_NVENC='1',
               VTX_CONFIG=str(root / 'config.json'), FRAMIVRA_VERIFY_ROOT=str(root),
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    return env


def prepare_python(root, source, found):
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
            venv.EnvBuilder(with_pip=True, symlinks=False).create(target)
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallError('Python venv/ensurepip unavailable. Install python3-venv (Debian/Ubuntu) or a complete Python distribution and rerun: ' + str(error)) from error
    command([python, '-I', '-m', 'pip', '--isolated', 'install', '--index-url', 'https://pypi.org/simple', '--disable-pip-version-check', *PYTHON_PACKAGES], env=env)
    command([python, '-I', '-m', 'pip', '--isolated', 'install', '--no-index', '--no-deps', '--no-build-isolation', '--editable', source], env=env)
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


def verify(root, source, python, env):
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
    # Existing independent HTML/subtitle/media fixture + real all/strict QC/decode.
    evidence = root / 'verification' / ('cdp-' + uuid.uuid4().hex)
    command([python, source / 'tests/runners/run_e2e.py', '--repo', source, '--output', evidence,
             '--ffmpeg', env_tool(env, 'ffmpeg'), '--chrome', env['VTX_CHROME'], '--scope', 'capability'], env=env, cwd=root, timeout=3600)
    command([python, source / 'tests/runners/verify_e2e.py', evidence, '--ffmpeg', env_tool(env, 'ffmpeg'), '--scope', 'capability'], env=env, cwd=root)
    return capabilities, str(evidence)


def env_tool(env, name):
    value = shutil.which(name, path=env['PATH'])
    if not value:
        raise InstallError('Tool not available in the managed environment: ' + name)
    return value


def install(args):
    if sys.platform not in ('linux', 'darwin', 'win32'):
        raise InstallError('Supported systems: Linux, macOS, Windows')
    if sys.platform == 'win32' and platform.machine().lower() not in ('amd64', 'x86_64'):
        raise InstallError('Current Windows browser identity supports native x64 only')
    if sys.platform == 'darwin' and platform.machine().lower() not in ('arm64', 'aarch64'):
        raise InstallError('Current macOS CDP identity supports Apple Silicon arm64 only; Intel macOS is not a verified install target')
    root = physical(args.root or default_root())
    if root in (Path.home(), Path(root.anchor)):
        raise InstallError('Installation root cannot be HOME or a filesystem root')
    print('Source deployment: official Git/HTTPS trust, NOT signed-release provenance.\n'
          'Install root: %s\nChannel: %s (resolved to an exact commit)\n'
          'Downloads: source, PyPI packages, native packages and pinned public fonts.\n'
          'No AI models/services. System package installation may request administrator approval.' % (root, args.ref), flush=True)
    if args.plan:
        print(json.dumps({'root': str(root), 'profile': 'non-ai', 'renderer': 'cdp',
                          'pythonPackages': PYTHON_PACKAGES, 'nativeCommands': platform_packages(tools())}, indent=2))
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            raise InstallError('Non-interactive installation requires explicit --yes (review --plan first)')
        if input('Proceed? [y/N] ').strip().lower() not in ('y', 'yes'):
            raise InstallError('Installation cancelled before writes')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with installation_lock(root):
        if not (root / 'installation.json').exists():
            for name in ('source', 'venv', 'bin/vtx'):
                if (root / name).exists():
                    raise InstallError('Refusing existing unmanaged installation component: ' + name)
        found = prepare_tools(args.no_system)
        verify_native_tools(found)
        commit = update_source(root, args.repo, args.ref, args.update)
        source = root / 'source'
        python, env = prepare_python(root, source, found)
        prepare_configuration(root, source, python)
        font_report = json.loads(command([python, source / 'scripts/install_runtime_fonts.py', '--root', root], env=env, cwd=root, capture=True))
        font_environment = font_report.get('environment', {})
        expected = {'FONTCONFIG_FILE': str(root / 'fonts/fonts.conf')} if sys.platform == 'linux' else {}
        if font_environment != expected:
            raise InstallError('Font installer returned an unexpected runtime environment')
        env.update(font_environment)
        capabilities, evidence = verify(root, source, python, env)
        vtx = root / 'venv' / ('Scripts/vtx.exe' if sys.platform == 'win32' else 'bin/vtx')
        executable = vtx
        if sys.platform != 'win32':
            binary = physical(root / 'bin')
            binary.mkdir(exist_ok=True)
            executable = binary / 'vtx'
            physical(executable)
            executable.write_text('#!/bin/sh\nexec ' + shlex.quote(str(vtx)) + ' "$@"\n', encoding='utf-8')
            executable.chmod(0o700)
        atomic_json(root / 'installation.json', dict(schema=SCHEMA, status='ready',
                    profile='non-ai', renderer='cdp', repository=args.repo, ref=args.ref,
                    commit=commit, version=(source / 'VERSION').read_text(encoding='utf-8').strip(),
                    sourceRoot=str(source), pythonExecutable=str(python), vtxExecutable=str(executable),
                    configFile=str(root / 'config.json'), tools=found, environment=font_environment,
                    capabilities=capabilities,
                    verification=evidence, installedAt=datetime.now(timezone.utc).isoformat()))
    print('\nREADY: non-AI CDP environment. In DSH: scan installed kernels → select registered installation → save → check connection.\nVTX: ' + str(executable))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', help='explicit managed root; DSH auto-scans the default root')
    parser.add_argument('--repo', default=REPOSITORY, help='official repository or explicitly trusted absolute local source')
    parser.add_argument('--ref', default='main', help='explicit source channel: main, tag, or full commit')
    parser.add_argument('--yes', action='store_true', help='approve displayed downloads/writes (no AI)')
    parser.add_argument('--plan', action='store_true', help='read-only plan: no network, mkdir, install or execution of candidates')
    parser.add_argument('--no-system', action='store_true', help='use existing native tools; fail if any required tool is missing')
    parser.add_argument('--update', action='store_true', help='explicitly update a clean owned checkout; otherwise reuse pinned source')
    args = parser.parse_args(argv)
    try:
        return install(args)
    except (InstallError, OSError, ValueError, subprocess.SubprocessError) as error:
        print('[install] FAILED: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
