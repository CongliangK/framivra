#!/usr/bin/env python3
"""Install pinned non-AI runtime fonts and their notices, without administrator rights.

Writes managed fonts, the verified resume cache and platform user fonts only;
never edits system fontconfig or replaces a different existing file. Linux callers MUST propagate the returned
FONTCONFIG_FILE to every browser/FFmpeg/fontconfig child. Native macOS/Windows
registration has contract tests, not Linux-hosted physical-platform validation.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape

if __package__:
    from . import demo_fonts, install_downloads
else:
    import demo_fonts
    import install_downloads

FontError = demo_fonts.FontError
MAX_BYTES = demo_fonts.MAX_FONT_BYTES
INTER_COMMIT = '0b58fb370093f9a9f4ff785d94405710b79de67c'
JETBRAINS_COMMIT = '02bb50b082dad9ef8a0f33ac393839202b760223'
INTER_BASE = 'https://raw.githubusercontent.com/google/fonts/' + INTER_COMMIT + '/ofl/inter/'
JETBRAINS_BASE = 'https://raw.githubusercontent.com/JetBrains/JetBrainsMono/' + JETBRAINS_COMMIT + '/'

# Debian package and font pins are imported unchanged from the public Demo.
SOURCES = tuple(dict(s, kind='font', license_file=(
    'Noto-COPYRIGHT.txt' if s['filename'].startswith('Noto') else 'DejaVu-COPYRIGHT.txt'))
    for s in demo_fonts.FONT_SOURCES) + (
    dict(demo_fonts.FONT_SOURCES[0], kind='license', filename='Noto-COPYRIGHT.txt',
         member='./usr/share/doc/fonts-noto-cjk/copyright', size=14716,
         sha256='5bb218343ded506b258d86186d460fc47d9b6b28f0e642cf32676c396b7df763'),
    dict(demo_fonts.FONT_SOURCES[2], kind='license', filename='DejaVu-COPYRIGHT.txt',
         member='./usr/share/doc/fonts-dejavu-core/copyright', size=3859,
         sha256='63d3ba759d12804c5b31a9d5940d855c1820d1f5999e6b0872eb1c7ff045fbc9'),
    dict(filename='InterVariable.ttf', kind='font', version='Google Fonts ' + INTER_COMMIT,
         url=INTER_BASE + 'Inter%5Bopsz,wght%5D.ttf', size=876576,
         sha256='29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031',
         license_file='Inter-OFL.txt'),
    dict(filename='Inter-OFL.txt', kind='license', version=INTER_COMMIT,
         url=INTER_BASE + 'OFL.txt', size=4377,
         sha256='5b9321a4298cfeb6b34354164a1c3afc3db114569984c502b9b35d988fd58c57'),
    dict(filename='JetBrainsMono-Regular.ttf', kind='font', version=JETBRAINS_COMMIT,
         url=JETBRAINS_BASE + 'fonts/ttf/JetBrainsMono-Regular.ttf', size=270224,
         sha256='e6fd0d7e91550b3ed2b735d4312474362c4716edc4fc0577a0f61ed782d5aed1',
         license_file='JetBrainsMono-OFL.txt'),
    dict(filename='JetBrainsMono-Bold.ttf', kind='font', version=JETBRAINS_COMMIT,
         url=JETBRAINS_BASE + 'fonts/ttf/JetBrainsMono-Bold.ttf', size=274096,
         sha256='d22c4f3821d725eb01210d278d95dfcfcaadc34699a06658d47c8a5cc5830ada',
         license_file='JetBrainsMono-OFL.txt'),
    dict(filename='JetBrainsMono-OFL.txt', kind='license', version=JETBRAINS_COMMIT,
         url=JETBRAINS_BASE + 'OFL.txt', size=4399,
         sha256='30f0c136e3c88e422d0791acd97238870f9054a9729bc34cf2ff0d4ed8cac4ad'),
)


def physical(raw):
    path = Path(os.path.expanduser(str(raw)))
    if not str(raw).strip() or '..' in path.parts:
        raise FontError('explicit non-traversing font installation path required')
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            attributes = getattr(item.lstat(), 'st_file_attributes', 0)
        except FileNotFoundError:
            attributes = 0
        # st_file_attributes also covers Windows junctions on Python <3.12,
        # where pathlib has no is_junction method.
        if item.is_symlink() or attributes & 0x400:
            raise FontError('symlink/junction font destination rejected: ' + str(item))
        if item != path and item.exists() and not item.is_dir():
            raise FontError('non-directory font ancestor: ' + str(item))
    return path


def filename(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value):
        raise FontError('unsafe font filename')
    return value


def checked(data, source):
    if not 0 < len(data) <= MAX_BYTES or (
            source.get('size') is not None and len(data) != source['size']) or (
            hashlib.sha256(data).hexdigest() != source['sha256']):
        raise FontError('font/notice size or SHA256 mismatch: ' + source['filename'])
    return data


def existing(path, source):
    path = physical(path)
    if not path.exists():
        return None
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
        raise FontError('not a bounded regular font/notice: ' + str(path))
    return checked(path.read_bytes(), source)


class PinnedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        before, after = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if after.scheme != 'https' or after.netloc != before.netloc:
            raise FontError('off-origin/insecure font redirect rejected')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def candidates(source, network='cn'):
    """Transport choices only; SOURCES and installed manifest keep canonical URLs."""
    if network not in ('cn', 'official'):
        raise FontError('network must be cn or official')
    url = urllib.parse.urlsplit(source['url'])
    if 'package_sha256' in source:
        if not source['url'].startswith('https://deb.debian.org/debian/pool/'):
            raise FontError('unexpected pinned Debian font origin')
        if network == 'cn':
            return [host + url.path for host in ('https://mirrors.tuna.tsinghua.edu.cn',
                                                'https://mirrors.ustc.edu.cn')] + [source['url']]
    elif url.scheme != 'https' or url.netloc != 'raw.githubusercontent.com':
        raise FontError('unexpected pinned font origin')
    return [source['url']]


def download(source, *, cache, network='cn'):
    """Use the common verified Range cache, then recheck bounded bytes locally."""
    package = 'package_sha256' in source
    size = source['package_size'] if package else source['size']
    sha256 = source['package_sha256'] if package else source['sha256']
    limit = demo_fonts.MAX_TAR_BYTES if package else MAX_BYTES
    if not isinstance(size, int) or not 0 < size <= limit:
        raise FontError('pinned font download size is out of bounds')
    urls = candidates(source, network)
    name = (urllib.parse.unquote(urllib.parse.urlsplit(source['url']).path.rsplit('/', 1)[-1])
            if package else source['filename'])
    try:
        path = install_downloads.acquire(cache, urls, sha256, size=size, filename=name)
        path = physical(path)
        if not path.is_file() or path.stat().st_size != size:
            raise FontError('font/package size or SHA256 mismatch: ' + source['filename'])
        with path.open('rb') as stream:
            data = stream.read(size + 1)
    except install_downloads.DownloadError as error:
        raise FontError('pinned font acquisition failed: ' + str(error)) from error
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise FontError('font/package size or SHA256 mismatch: ' + source['filename'])
    return data if package else checked(data, source)


def user_font_directory(system, home, environment):
    if system == 'darwin':
        return physical(home / 'Library' / 'Fonts')
    if system == 'win32':
        local = environment.get('LOCALAPPDATA')
        if not local or not Path(local).is_absolute():
            raise FontError('absolute LOCALAPPDATA required for per-user Windows fonts')
        return physical(Path(local) / 'Microsoft' / 'Windows' / 'Fonts')
    if system == 'linux':
        return None  # Managed fontconfig only; no edits outside installation root.
    raise FontError('unsupported runtime font platform: ' + system)


def local_candidates(source, user_fonts, home):
    name = source['filename']
    if user_fonts:
        yield user_fonts / name
    # Only identical pinned bytes are reusable; local names/versions prove nothing.
    if source['kind'] != 'font':
        return
    for directory in (home / '.local/share/fonts', Path('/usr/share/fonts/opentype/noto'),
                      Path('/usr/share/fonts/noto-cjk'), Path('/usr/share/fonts/truetype/dejavu'),
                      Path('/usr/share/fonts/dejavu')):
        yield directory / name


def acquire(directory, sources, user_fonts, home, *, cache, network='cn'):
    result, missing = {}, []
    for source in sources:
        name = source['filename']
        data = existing(directory / name, source)
        if data is None:
            for candidate in local_candidates(source, user_fonts, home):
                try:
                    data = existing(candidate, source)
                except (FontError, OSError):
                    continue  # A different system font is not an installation target.
                if data is not None:
                    break
        if data is None:
            missing.append(source)
        else:
            result[name] = data
    for url in dict.fromkeys(s['url'] for s in missing if 'package_sha256' in s):
        group = [s for s in missing if s['url'] == url]
        # Reuse bounded ar/xz/tar parsing and both package/member SHA checks.
        package = download(group[0], cache=cache, network=network)
        result.update(demo_fonts._extract(package, group))
    for source in missing:
        name = source['filename']
        if name not in result:
            result[name] = download(source, cache=cache, network=network)
        checked(result[name], source)
    return result


def keep(path, data):
    """Publish a new file, or accept exactly identical existing bytes. Never overwrite."""
    expected = dict(filename=path.name, size=len(data), sha256=hashlib.sha256(data).hexdigest())
    if existing(path, expected) is None:
        physical(path.parent).mkdir(parents=True, exist_ok=True)
        with path.open('xb') as output:
            output.write(data)


def linux_configuration(directory, cache):
    return ('<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
            '<fontconfig>\n  <dir>' + escape(str(directory)) + '</dir>\n'
            '  <cachedir>' + escape(str(cache)) + '</cachedir>\n'
            '  <include ignore_missing="no">/etc/fonts/fonts.conf</include>\n'
            '</fontconfig>\n').encode('utf-8')


def run(argv, environment):
    result = subprocess.run(argv, env=environment, capture_output=True, text=True,
                            encoding='utf-8', errors='strict', timeout=120)
    if result.returncode:
        raise FontError('font registration command failed: %s: %s' % (argv[0], result.stderr[-2000:]))
    return result.stdout


def register_windows(paths):
    import winreg
    from ctypes import wintypes
    key_path = r'Software\Microsoft\Windows NT\CurrentVersion\Fonts'
    values = [(p, 'Framivra ' + p.stem + ' (TrueType)') for p in paths]
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
        for path, name in values:
            try:
                old, kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                continue
            if kind != winreg.REG_SZ or old != str(path):
                raise FontError('existing Windows font registration differs: ' + name)
        gdi, user = ctypes.WinDLL('gdi32', use_last_error=True), ctypes.WinDLL('user32', use_last_error=True)
        add = gdi.AddFontResourceExW
        add.argtypes, add.restype = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID], ctypes.c_int
        for path, name in values:
            if not add(str(path), 0, None):
                raise FontError('Windows font activation failed: ' + str(path))
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(path))
        send = user.SendMessageTimeoutW
        send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                         wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = wintypes.LPARAM
        receipt = ctypes.c_size_t()
        if not send(0xffff, 0x001d, 0, 0, 0x0002, 5000, ctypes.byref(receipt)):
            raise FontError('Windows WM_FONTCHANGE notification failed')


def install(root, *, system=None, home=None, environment=None, network='cn'):
    if network not in ('cn', 'official'):
        raise FontError('network must be cn or official')
    system = system or sys.platform
    home = Path.home() if home is None else Path(home)
    environment = dict(os.environ if environment is None else environment)
    root = physical(root)
    if root.exists() and not root.is_dir():
        raise FontError('font root is not a directory')
    directory, cache = physical(root / 'fonts'), physical(root / 'font-cache')
    if directory.exists() and not directory.is_dir():
        raise FontError('font destination is not a directory')
    user_fonts = user_font_directory(system, home, environment)
    sources = SOURCES
    for source in sources:
        filename(source['filename'])
        if source.get('license_file'):
            filename(source['license_file'])
        # A conflicting user destination is an error, never a local-source miss.
        if user_fonts:
            existing(user_fonts / source['filename'], source)
    blobs = acquire(directory, sources, user_fonts, home,
                    cache=physical(root / 'cache' / 'downloads'), network=network)
    for source in sources:
        keep(directory / source['filename'], blobs[source['filename']])
    child_environment = {}
    if system == 'linux':
        physical(cache).mkdir(parents=True, exist_ok=True)
        config = directory / 'fonts.conf'
        keep(config, linux_configuration(directory, cache))
        child_environment['FONTCONFIG_FILE'] = str(config)
        env = dict(environment, **child_environment)
        run(['fc-cache', '-f', str(directory)], env)
        visible = set(run(['fc-list', '--format=%{file}\n'], env).splitlines())
        absent = [s['filename'] for s in sources if s['kind'] == 'font'
                  and str(directory / s['filename']) not in visible]
        if absent:
            raise FontError('managed fonts not visible through fontconfig: ' + ', '.join(absent))
    else:
        for source in sources:
            keep(user_fonts / source['filename'], blobs[source['filename']])
        if system == 'win32':
            register_windows([user_fonts / s['filename'] for s in sources if s['kind'] == 'font'])
        # macOS watches ~/Library/Fonts. New browser/FFmpeg processes see copies;
        # no system cache deletion or killall/fontd is performed.
    manifest = dict(schema='framivra.runtime-fonts/v1', fonts_directory=str(directory),
                    user_fonts_directory=str(user_fonts) if user_fonts else None,
                    environment=child_environment, sources=list(sources))
    keep(directory / 'sources.json', (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return dict(ok=True, **manifest)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--network', choices=('cn', 'official'), default='cn')
    args = parser.parse_args(argv)
    try:
        report = install(args.root, network=args.network)
    except (FontError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(ok=False, error=str(error)), ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
