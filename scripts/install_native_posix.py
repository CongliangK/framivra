#!/usr/bin/env python3
"""Native POSIX preparation; temporary mirrors, original package trust, no source build.

System package installation is an explicitly confirmed transaction. Repository
configuration is copied into temporary files, never edited under /etc. Vendor
package maintainer scripts (notably Chrome) retain their normal side effects.
"""
import codecs
import configparser
import glob
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit

REQUIRED_ENCODERS = ('libx264', 'aac', 'prores_ks', 'ffv1', 'pcm_s16le',
                     'pcm_f32le', 'flac', 'libmp3lame', 'png', 'mjpeg')
REQUIRED_FILTERS = ('ass', 'overlay', 'scale', 'format', 'fps', 'trim', 'setpts',
                    'concat', 'alphamerge', 'color', 'amix', 'aresample', 'volume',
                    'aformat', 'alimiter', 'sidechaincompress', 'afade', 'atrim',
                    'adelay', 'asetpts', 'apad', 'asplit', 'pan', 'loudnorm',
                    'ebur128', 'blackdetect', 'silencedetect', 'astats', 'psnr', 'ssim')
TOOLS = ('git', 'ffmpeg', 'ffprobe', 'sox')
MIRROR_HOSTS = ('mirrors.tuna.tsinghua.edu.cn', 'mirrors.ustc.edu.cn',
                'mirrors.bfsu.edu.cn')
GOOGLE_KEY_SHA256 = '54dea5f6c2a26091578cf52a999cebc6b64df478d37ad4dce96376b711e3b27c'
GOOGLE_KEY_FINGERPRINT = 'EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796'


class NativeInstallError(RuntimeError):
    pass


def run_process(argv, *, cwd=None, env=None, capture=False, timeout=None):
    """Retain bounded stderr without capturing interactive stdin/stdout."""
    argv = [str(x) for x in argv]
    if capture:
        return subprocess.run(argv, cwd=cwd, env=env, timeout=timeout, capture_output=True,
                              text=True, encoding='utf-8', errors='replace')
    process = subprocess.Popen(argv, cwd=cwd, env=env, stderr=subprocess.PIPE, bufsize=0)
    tail = bytearray()
    stopped = threading.Event()
    descriptor = process.stderr.fileno()

    def read_stderr():
        decode = codecs.getincrementaldecoder('utf-8')(errors='replace')
        tee = True
        while not stopped.is_set():
            try:
                block = os.read(descriptor, 8192)
            except OSError:
                break
            if not block or stopped.is_set():
                break
            tail.extend(block)
            del tail[:-16384]
            if tee:
                try:
                    sys.stderr.write(decode.decode(block))
                    sys.stderr.flush()
                except (OSError, ValueError):
                    tee = False  # Keep draining if the progress sink closed.
        if tee and not stopped.is_set():
            try:
                sys.stderr.write(decode.decode(b'', final=True))
                sys.stderr.flush()
            except (OSError, ValueError):
                pass

    reader = threading.Thread(target=read_stderr, name='installer-stderr', daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        reader.join(timeout=2)
        stopped.set()
        process.stderr.close()
    return subprocess.CompletedProcess(argv, process.returncode, stdout=None,
                                       stderr=bytes(tail).decode('utf-8', errors='replace'))


def _locale_environment(env=None):
    return dict(os.environ if env is None else env, LC_ALL='C', LANG='C')


def _command(argv, *, cwd=None, env=None, capture=False, timeout=None):
    print('[native] ' + shlex.join([str(x) for x in argv]), flush=True)
    result = run_process(argv, cwd=cwd, env=_locale_environment(env), timeout=timeout, capture=capture)
    if result.returncode:
        raise NativeInstallError('Command failed: ' + str(argv[0]) + ': ' + (result.stderr or '')[-16384:])
    return result.stdout.strip() if capture else ''


def missing_ffmpeg(found, run=None):
    """Same complete capability inventory used by final installer verification."""
    if 'ffmpeg' not in found:
        return ['ffmpeg']
    run = run or _command
    try:
        encoders = run([found['ffmpeg'], '-hide_banner', '-encoders'], capture=True, timeout=60)
        filters = run([found['ffmpeg'], '-hide_banner', '-filters'], capture=True, timeout=60)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return ['unusable ffmpeg']
    return [name for text, names in ((encoders, REQUIRED_ENCODERS), (filters, REQUIRED_FILTERS))
            for name in names if not re.search(r'\b' + name + r'\b', text)]


def _confirm(message, yes):
    print('[native plan] ' + message, flush=True)
    if not yes:
        try:
            answer = input('Continue with this system transaction? [y/N] ')
        except EOFError as error:
            raise NativeInstallError('Native installation needs confirmation or --yes') from error
        if answer.strip().lower() not in ('y', 'yes'):
            raise NativeInstallError('Native installation declined; no transaction started')


def _network_environment(network):
    env = dict(os.environ)
    if network == 'cn':
        bypass = ','.join(filter(None, [env.get('no_proxy', env.get('NO_PROXY', '')),
                                       ','.join(MIRROR_HOSTS)]))
        env['no_proxy'] = env['NO_PROXY'] = bypass
    return env


def _privileged(argv):
    if os.geteuid() == 0:
        return argv
    # Only propagate network/diagnostic locale, not caller code/configuration.
    keys = 'http_proxy,https_proxy,HTTP_PROXY,HTTPS_PROXY,all_proxy,ALL_PROXY,no_proxy,NO_PROXY,LC_ALL,LANG'
    return ['sudo', '--preserve-env=' + keys, *argv]


def browser_candidates():
    return [os.environ.get('VTX_CHROME'),
            *[shutil.which(n) for n in ('google-chrome', 'google-chrome-stable',
                                      'chromium', 'chromium-browser', 'chrome', 'msedge')],
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/opt/google/chrome/chrome', '/usr/lib/chromium/chromium',
            '/usr/lib64/chromium-browser/chromium-browser', '/usr/lib64/chromium/chromium']


def discover(root, found=None):
    """Read-only discovery; preserve explicitly selected tools and VTX_CHROME."""
    result = dict(found or {})
    names = (*TOOLS, 'fc-cache', 'fc-list') if sys.platform == 'linux' else TOOLS
    for name in names:
        if name not in result and (value := shutil.which(name)):
            result[name] = str(Path(value).resolve())
    if 'chrome' not in result:
        for value in browser_candidates():
            if not value or not Path(value).is_file():
                continue
            path = Path(value).resolve()
            if sys.platform == 'linux':
                with path.open('rb') as source:
                    if source.read(4) != b'\x7fELF':
                        continue
            result['chrome'] = str(path)
            break
    return result


def os_release(path='/etc/os-release'):
    result = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            parsed = shlex.split(value)
            result[key] = parsed[0] if parsed else ''
    return result


def apt_mirror_text(text, distro, mirror=None):
    """Rewrite known vendor origins only; keep suite/components/signed-by/options."""
    def replace(match):
        value = match.group(0)
        parsed = urlsplit(value)
        host, path = parsed.hostname or '', parsed.path.rstrip('/')
        known = host in MIRROR_HOSTS or host in ('deb.debian.org', 'security.debian.org',
                                                'archive.ubuntu.com', 'security.ubuntu.com',
                                                'ports.ubuntu.com') or host.endswith('.archive.ubuntu.com')
        if not known:
            return value
        if distro == 'debian' and path in ('/debian', '/debian-security'):
            official = ('https://security.debian.org' if path == '/debian-security'
                        else 'https://deb.debian.org')
        elif distro == 'ubuntu' and path in ('/ubuntu', '/ubuntu-ports'):
            # Keep the independent security channel official (mirror lag matters).
            if host == 'security.ubuntu.com':
                return value
            official = ('https://ports.ubuntu.com' if path == '/ubuntu-ports'
                        else 'https://archive.ubuntu.com')
        else:
            return value
        return (mirror or official) + path + ('/' if parsed.path.endswith('/') else '')
    return re.sub(r'https?://[^\s\]#]+', replace, text)


def apt_sources(destination, distro, mirror=None, etc=Path('/etc/apt')):
    """Materialise legacy and deb822 sources without mutating the originals."""
    destination = Path(destination)
    destination.mkdir()
    main = etc / 'sources.list'
    inputs = ([main] if main.is_file() else []) + sorted((etc / 'sources.list.d').glob('*'))
    count = 0
    for source in inputs:
        if source.is_file() and (source.name == 'sources.list' or source.suffix in ('.list', '.sources')):
            suffix = '.sources' if source.suffix == '.sources' else '.list'
            text = apt_mirror_text(source.read_text(encoding='utf-8'), distro, mirror)
            (destination / ('source-%d' % count + suffix)).write_text(text, encoding='utf-8')
            count += 1
    if not count:
        raise NativeInstallError('No apt sources found; configure the correct distribution first')
    return ['-o', 'Dir::Etc::sourcelist=/dev/null', '-o', 'Dir::Etc::sourceparts=' + str(destination),
            '-o', 'Acquire::http::Timeout=30', '-o', 'Acquire::https::Timeout=30',
            '-o', 'Acquire::Retries=2', '-o', 'APT::Update::Error-Mode=any',
            *[item for host in MIRROR_HOSTS for item in
              ('-o', 'Acquire::https::Proxy::' + host + '=DIRECT')]]


def dnf_repositories(destination, mirror=False, source=Path('/etc/yum.repos.d')):
    destination = Path(destination)
    destination.mkdir()
    for filename in sorted(source.glob('*.repo')):
        config = configparser.ConfigParser(interpolation=None)
        config.read(filename, encoding='utf-8')
        for name in config.sections():
            if name not in ('fedora', 'updates'):
                continue
            base = config[name].get('baseurl', '')
            metalink = config[name].get('metalink', '')
            known = ('mirrors.fedoraproject.org/' in metalink or
                     any(('https://' + host + '/fedora/') in base for host in MIRROR_HOSTS))
            if not known:
                continue  # Never reclassify an unknown user/provider repo as Fedora.
            if mirror:
                path = ('releases/$releasever/Everything/$basearch/os/' if name == 'fedora'
                        else 'updates/$releasever/Everything/$basearch/')
                for option in ('metalink', 'mirrorlist'):
                    config.remove_option(name, option)
                config[name]['baseurl'] = 'https://mirrors.ustc.edu.cn/fedora/' + path
                config[name]['proxy'] = '_none_'
            elif not metalink:
                config.remove_option(name, 'baseurl')
                config.remove_option(name, 'proxy')
                repo = 'fedora-$releasever' if name == 'fedora' else 'updates-released-f$releasever'
                config[name]['metalink'] = 'https://mirrors.fedoraproject.org/metalink?repo=' + repo + '&arch=$basearch'
        with (destination / filename.name).open('w', encoding='utf-8') as output:
            config.write(output)
    return ['--setopt=reposdir=' + str(destination), '--setopt=timeout=30',
            '--setopt=minrate=1000', '--setopt=retries=2']


def rpmfusion_repository(destination, key, mirror=False):
    """Only free, not nonfree/tainted. Trust comes from Fedora's signed key package."""
    base = ('https://mirrors.ustc.edu.cn/rpmfusion/free/fedora' if mirror
            else 'https://download1.rpmfusion.org/free/fedora')
    text = ''
    for suffix, path in (('', 'releases/$releasever/Everything/$basearch/os/'),
                         ('-updates', 'updates/$releasever/$basearch/')):
        text += ('[framivra-rpmfusion-free%s]\nname=RPM Fusion free (temporary Framivra transaction)\n'
                 'baseurl=%s/%s\nenabled=1\ngpgcheck=1\nsslverify=1\ngpgkey=file://%s\n'
                 'skip_if_unavailable=False\n%s\n' %
                 (suffix, base, path, key, 'proxy=_none_' if mirror else ''))
    (Path(destination) / 'framivra-rpmfusion-free.repo').write_text(text, encoding='utf-8')


def pacman_configuration(path=Path('/etc/pacman.conf'), *, mirror=False, seen=None):
    """Expand Include while retaining SigLevel, architecture and custom repositories."""
    path = Path(path).resolve()
    seen = set() if seen is None else seen
    if path in seen:
        raise NativeInstallError('Recursive pacman Include: ' + str(path))
    seen = seen | {path}
    lines = []
    section = None
    for line in path.read_text(encoding='utf-8').splitlines():
        if re.match(r'^\s*\[.*\]\s*$', line):
            section = line.strip()[1:-1]
        if re.match(r'^\s*DisableDownloadTimeout\s*$', line):
            lines.append('# Framivra transaction retains libalpm download timeouts')
            continue
        if re.match(r'^\s*XferCommand\s*=', line):
            raise NativeInstallError('Custom pacman XferCommand has unverified timeout/resume semantics; use native libalpm or provide tools')
        include = re.match(r'^\s*Include\s*=\s*(.*?)\s*$', line)
        if include:
            pattern = include.group(1)
            if not Path(pattern).is_absolute():
                pattern = str(path.parent / pattern)
            matches = sorted(glob.glob(pattern))
            if not matches:
                raise NativeInstallError('Missing pacman Include: ' + pattern)
            # Includes (e.g. mirrorlist) inherit the enclosing repository.
            for match in matches:
                content = pacman_configuration(Path(match), mirror=False, seen=seen)
                if mirror and section in ('core', 'extra', 'multilib'):
                    content = re.sub(r'(?m)^\s*Server\s*=.*$',
                                     'Server = https://mirrors.ustc.edu.cn/archlinux/$repo/os/$arch', content)
                lines.append(content)
        elif mirror and section in ('core', 'extra', 'multilib') and re.match(r'^\s*Server\s*=', line):
            lines.append('Server = https://mirrors.ustc.edu.cn/archlinux/$repo/os/$arch')
        else:
            lines.append(line)
    return '\n'.join(lines) + '\n'


def _retry(stages, operation):
    for index, stage in enumerate(stages):
        try:
            return operation(stage)
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            # Never bypass trust errors by choosing another source after bad content.
            if re.search(r'(?i)signature|hash sum mismatch|sha256|sha512|checksum|GPG|NO_PUBKEY|public key|certificate|SSL|TLS|clearsigned|not signed', str(error)):
                raise
            if index == len(stages) - 1:
                raise
            print('[native] Mirror transaction failed; retrying original trusted source.', flush=True)


def _apt(root, found, needs_venv, network, yes, run, metadata):
    distro = os_release().get('ID')
    if distro not in ('debian', 'ubuntu'):
        raise NativeInstallError('Automatic apt mirror selection requires Debian or Ubuntu ID')
    chrome = 'chrome' not in found and distro == 'ubuntu'
    if chrome and platform.machine().lower() not in ('x86_64', 'amd64'):
        raise NativeInstallError('Ubuntu native Google Chrome requires amd64; supply a qualified native browser')
    mapping = {'git': 'git', 'ffmpeg': 'ffmpeg', 'ffprobe': 'ffmpeg', 'sox': 'sox',
               'fc-cache': 'fontconfig', 'fc-list': 'fontconfig'}
    packages = list(dict.fromkeys(v for k, v in mapping.items() if k not in found))
    if needs_venv:
        packages.append('python3-venv')
    if 'chrome' not in found:
        packages += ['libegl1', 'libgbm1']
        if not chrome:
            packages.append('chromium')
        elif not shutil.which('gpg'):
            packages.append('gpg')
    _confirm('apt packages: ' + ', '.join(packages) + '. Uses temporary source copies; '
             'apt package caches and installed packages change.', yes)
    metadata['transactions'].append('apt: ' + ', '.join(packages))
    with tempfile.TemporaryDirectory(prefix='framivra-apt-') as temporary:
        temp = Path(temporary)
        temp.chmod(0o755)
        stages = [True, False] if network == 'cn' else [False]
        def install(mirrored):
            sources = temp / ('cn' if mirrored else 'official')
            options = apt_sources(sources, distro, 'https://mirrors.tuna.tsinghua.edu.cn' if mirrored else None)
            run(_privileged(['apt-get', *options, 'update']), timeout=None)
            if packages:
                run(_privileged(['apt-get', *options, 'install', '-y', *packages]), timeout=None)
            return options, sources
        options, sources = _retry(stages, install)
        if chrome:
            _confirm('Install Google Chrome amd64 from its official signed apt repository, not Snap. '
                     'The standard Google package may persistently register its own Google update '
                     'repository under /etc and modify system package state. Existing sources are not rewritten.', yes)
            if __package__:
                from .install_downloads import acquire
            else:
                from install_downloads import acquire
            key = acquire(Path(root) / 'cache' / 'downloads',
                          ['https://dl.google.com/linux/linux_signing_key.pub'], GOOGLE_KEY_SHA256,
                          filename='google-linux-signing-key.asc')
            homedir = temp / 'gnupg'
            homedir.mkdir(mode=0o700)
            listing = run(['gpg', '--batch', '--homedir', str(homedir), '--with-colons',
                           '--show-keys', str(key)], capture=True, timeout=30)
            if not any(line.startswith('fpr:') and line.split(':')[9] == GOOGLE_KEY_FINGERPRINT
                       for line in listing.splitlines()):
                raise NativeInstallError('Google signing key fingerprint does not match official Linux repositories key')
            # Place the scoped key in a world-readable temporary path for apt's sandbox.
            scoped = temp / 'google.asc'
            shutil.copyfile(key, scoped)
            scoped.chmod(0o644)
            (sources / 'google-chrome.list').write_text(
                'deb [arch=amd64 signed-by=%s] https://dl.google.com/linux/chrome/deb/ stable main\n' % scoped,
                encoding='utf-8')
            run(_privileged(['apt-get', *options, 'update']), timeout=None)
            run(_privileged(['apt-get', *options, 'install', '-y', 'google-chrome-stable']), timeout=None)
            metadata['transactions'].append('Google Chrome official signed apt amd64')
            metadata['system_side_effects'].append('Google Chrome package may register its persistent Google update repository')


def _dnf(root, found, needs_venv, network, yes, run, metadata):
    release = os_release()
    version = release.get('VERSION_ID', '')
    if release.get('ID') != 'fedora' or not version.isdigit():
        raise NativeInstallError('Automatic dnf preparation currently requires a numbered Fedora release')
    full = 'ffmpeg' not in found or 'ffprobe' not in found
    mapping = {'git': 'git', 'sox': 'sox', 'chrome': 'chromium',
               'fc-cache': 'fontconfig', 'fc-list': 'fontconfig'}
    packages = list(dict.fromkeys(v for k, v in mapping.items() if k not in found))
    if needs_venv:
        packages.append('python3-pip')
    if full:
        packages.append('distribution-gpg-keys')
    _confirm('Fedora packages: ' + ', '.join(packages) + '. Native package caches and RPM database change.', yes)
    metadata['transactions'].append('dnf: ' + ', '.join(packages))
    with tempfile.TemporaryDirectory(prefix='framivra-dnf-') as temporary:
        stages = [True, False] if network == 'cn' else [False]
        def install(mirrored):
            repos = Path(temporary) / ('cn' if mirrored else 'official')
            options = dnf_repositories(repos, mirrored)
            if packages:
                run(_privileged(['dnf', *options, 'install', '-y', *packages]), timeout=None)
            return repos, options
        repos, options = _retry(stages, install)
        if full:
            _confirm('Enable only RPM Fusion free in a temporary repository configuration. '
                     'DNF may import its key from Fedora-signed distribution-gpg-keys and replace '
                     'ffmpeg-free/libraries with full ffmpeg using --allowerasing; review the DNF transaction. '
                     'No permanent RPM Fusion repo is registered, no nonfree/tainted is enabled.', yes)
            key = Path('/usr/share/distribution-gpg-keys/rpmfusion') / ('RPM-GPG-KEY-rpmfusion-free-fedora-' + version)
            if not key.is_file():
                raise NativeInstallError('Fedora distribution-gpg-keys does not supply the matching RPM Fusion free key')
            try:
                run(['rpm', '-q', 'ffmpeg-free'], capture=True, timeout=30)
                replacing = True
            except (RuntimeError, subprocess.SubprocessError):
                replacing = False
            def full_install(mirrored):
                # Reuse Fedora sources from the already successful stage, only select
                # the additional free repo's transport; package signatures stay required.
                rpmfusion_repository(repos, key, mirrored)
                consent = ['-y'] if yes else []
                command = (['swap', *consent, 'ffmpeg-free', 'ffmpeg', '--allowerasing'] if replacing
                           else ['install', *consent, 'ffmpeg'])
                run(_privileged(['dnf', *options, *command]), timeout=None)
            _retry(stages, full_install)
            metadata['transactions'].append('RPM Fusion free: ' + ('swap ffmpeg-free -> ffmpeg (--allowerasing)' if replacing else 'install ffmpeg'))
            metadata['system_side_effects'].append('RPM Fusion free signing key may be imported into the RPM key database')


def _pacman(root, found, needs_venv, network, yes, run, metadata):
    mapping = {'git': 'git', 'ffmpeg': 'ffmpeg', 'ffprobe': 'ffmpeg', 'sox': 'sox',
               'chrome': 'chromium', 'fc-cache': 'fontconfig', 'fc-list': 'fontconfig'}
    packages = list(dict.fromkeys(v for k, v in mapping.items() if k not in found))
    if needs_venv:
        packages.append('python-pip')
    # -Syu, never -Sy + install: Arch partial upgrades are unsupported.
    _confirm('pacman -Syu --needed: ' + ', '.join(packages) + '. This synchronises and upgrades '
             'the Arch system to avoid a partial upgrade. Package signatures and custom repo settings remain.', yes)
    metadata['transactions'].append('pacman system upgrade and install: ' + ', '.join(packages))
    metadata['system_side_effects'].append('Arch full system upgrade (-Syu), not just missing tools')
    with tempfile.TemporaryDirectory(prefix='framivra-pacman-') as temporary:
        def install(mirrored):
            config = Path(temporary) / ('cn.conf' if mirrored else 'original.conf')
            config.write_text(pacman_configuration(mirror=mirrored), encoding='utf-8')
            # DisableDownloadTimeout is NOT used: libalpm's own low-speed timeout
            # bounds stalled transfers, while partial archives remain in its cache.
            consent = ['--noconfirm'] if yes else []
            run(_privileged(['pacman', '--config', str(config), '-Syu', '--needed',
                             *consent, *packages]), env=_network_environment(network), timeout=None)
        _retry([True, False] if network == 'cn' else [False], install)


def _brew_environment(network):
    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE='1', HOMEBREW_CURL_RETRIES='2')
    env.pop('HOMEBREW_NO_INSTALL_FROM_API', None)
    # Do not update/repoint existing brew/core/cask Git remotes or use experimental OCI.
    for key in ('HOMEBREW_ARTIFACT_DOMAIN', 'HOMEBREW_ARTIFACT_DOMAIN_NO_FALLBACK'):
        env.pop(key, None)
    if network == 'cn':
        env['HOMEBREW_API_DOMAIN'] = 'https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api'
        env['HOMEBREW_BOTTLE_DOMAIN'] = 'https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles'
        bypass = ','.join(filter(None, [env.get('no_proxy', env.get('NO_PROXY', '')), MIRROR_HOSTS[0]]))
        env['no_proxy'] = env['NO_PROXY'] = bypass
    else:
        env.pop('HOMEBREW_API_DOMAIN', None)
        env.pop('HOMEBREW_BOTTLE_DOMAIN', None)
    return env


def _brew(root, found, needs_venv, network, yes, run, metadata):
    if not shutil.which('brew'):
        raise NativeInstallError('Homebrew is required; rerun the bootstrap to prepare it first')
    full = 'ffmpeg' not in found or 'ffprobe' not in found
    packages = [name for name in ('git', 'sox') if name not in found]
    if full:
        packages.append('ffmpeg-full')
    _confirm('Homebrew bottles only: ' + ', '.join(packages) +
             (', Google Chrome cask (official Google DMG)' if 'chrome' not in found else '') +
             '. ffmpeg-full is keg-only and includes whisper.cpp/ggml/tesseract libraries, not models. '
             'No brew remote changes, force-linking, or automatic source build.', yes)
    if needs_venv:
        raise NativeInstallError('Current Python lacks ensurepip; rerun using the Homebrew Python interpreter')
    if packages and (platform.machine().lower() not in ('arm64', 'aarch64') or
                     int((platform.mac_ver()[0] or '0').split('.')[0]) < 15):
        raise NativeInstallError('Bottles-only preparation requires supported Apple Silicon macOS >=15; no automatic source build')
    env = _brew_environment(network)
    result = dict(found)
    with tempfile.TemporaryDirectory(prefix='framivra-brew-') as temporary:
        curlrc = Path(temporary) / 'curlrc'
        curlrc.write_text('connect-timeout = 15\nspeed-limit = 1000\nspeed-time = 30\n', encoding='utf-8')
        env['HOMEBREW_CURLRC'] = str(curlrc)
        if packages:
            # Let brew resolve its dependency graph. Make every dependency an
            # explicit --force-bottle target too, so a missing dependency bottle
            # cannot silently fall back to compilation on machines with CLT.
            dependencies = run(['brew', 'deps', '--formula', '--union', *packages],
                               env=env, capture=True, timeout=None).splitlines()
            targets = list(dict.fromkeys([*dependencies, *packages]))
            run(['brew', 'install', '--force-bottle', *targets], env=env, timeout=None)
        if full:
            prefix = run(['brew', '--prefix', 'ffmpeg-full'], env=env, capture=True, timeout=30)
            for name in ('ffmpeg', 'ffprobe'):
                executable = Path(prefix) / 'bin' / name
                if not executable.is_file() or not os.access(executable, os.X_OK):
                    raise NativeInstallError('Homebrew ffmpeg-full did not provide executable ' + name)
                result[name] = str(executable.resolve())
        if 'chrome' not in found:
            run(['brew', 'install', '--cask', 'google-chrome'], env=env, timeout=None)
    metadata['transactions'].append('brew bottles: ' + ', '.join(packages))
    if full:
        metadata['system_side_effects'].append('ffmpeg-full installs AI-related compiled libraries, not models; selected keg-only binaries explicitly')
    return result


def prepare(root, found, *, network='cn', no_system=False, yes=False,
            needs_venv=False, run=None):
    """Prepare missing tools, returning tool paths plus reserved _native_metadata.

    The caller removes incompatible FFmpeg/ffprobe before invoking this function,
    and MUST run its unchanged complete capability/render acceptance afterwards.
    no_system performs no package operation, download, key import or file write.
    """
    if network not in ('cn', 'official'):
        raise NativeInstallError('network must be cn or official')
    names = (*TOOLS, 'chrome', 'fc-cache', 'fc-list') if sys.platform == 'linux' else (*TOOLS, 'chrome')
    missing = set(names) - set(found)
    if not missing and not needs_venv:
        return dict(found)
    if no_system:
        raise NativeInstallError('Missing or incompatible native tools (--no-system): ' +
                                 ', '.join(sorted(missing)) + ('; Python ensurepip' if needs_venv else ''))
    base_run = run or _command
    def run(argv, **options):
        # Security-error classification must not depend on the host's language.
        options['env'] = _locale_environment(options.get('env'))
        return base_run(argv, **options)
    metadata = {'network': network, 'transactions': [], 'system_side_effects': []}
    if sys.platform == 'darwin':
        metadata['manager'] = 'brew'
        found = _brew(root, found, needs_venv, network, yes, run, metadata)
    elif sys.platform == 'linux':
        choices = (('apt-get', _apt), ('dnf', _dnf), ('pacman', _pacman))
        manager, handler = next(((name, fn) for name, fn in choices if shutil.which(name)), (None, None))
        if handler is None:
            raise NativeInstallError('Supported native POSIX managers: apt-get, dnf, pacman, Homebrew')
        metadata['manager'] = manager
        handler(root, found, needs_venv, network, yes, run, metadata)
    else:
        raise NativeInstallError('POSIX native helper supports Linux and macOS only')
    result = discover(root, found)
    missing = set(names) - set(result)
    if missing:
        raise NativeInstallError('Packages did not provide native tools: ' + ', '.join(sorted(missing)))
    result['_native_metadata'] = metadata
    return result
