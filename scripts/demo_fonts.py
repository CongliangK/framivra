"""Pinned public Demo fonts; imported by demo.py, never a second prepare CLI.

Only fetch_fonts is write-state. Debian packages are fixed-version public inputs,
not system installation instructions. Both package AND extracted font bytes are
pinned; a same-name/version substitute must fail. No third-party Python modules,
GitHub requests, system font discovery, or global installation are used.
"""
import hashlib
import io
import os
from pathlib import Path
import stat
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NOTO_URL = ('https://deb.debian.org/debian/pool/main/f/fonts-noto-cjk/'
            'fonts-noto-cjk_20220127+repack1-1_all.deb')
DEJAVU_URL = ('https://deb.debian.org/debian/pool/main/f/fonts-dejavu/'
              'fonts-dejavu-core_2.37-8_all.deb')
# Real downloads verified against the unchanged template lock. Debian's older
# 2.37-6 and Ubuntu's 2.37 builds are NOT byte-identical substitutes.
FONT_SOURCES = (
    dict(filename='NotoSansCJK-Regular.ttc', version='2.004', url=NOTO_URL,
         package_sha256='4a2515eb6db3978b897fef9709ed0d2b1f4c6c4df4d83d6c4ef65f71f1b1f502',
         package_size=56547048, member='./usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
         sha256='b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a'),
    dict(filename='NotoSansCJK-Bold.ttc', version='2.004', url=NOTO_URL,
         package_sha256='4a2515eb6db3978b897fef9709ed0d2b1f4c6c4df4d83d6c4ef65f71f1b1f502',
         package_size=56547048, member='./usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
         sha256='faa5f3656a78b2e2d450d27fe8382c778bc2b6bb5ea29c986664a6a435056ceb'),
    dict(filename='DejaVuSans.ttf', version='2.37 (Debian 2.37-8)', url=DEJAVU_URL,
         package_sha256='86635b3d25b3655fc11cb3ecc3af59f0bf19643b02b94f2de48bd10253cdba12',
         package_size=840460, member='./usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
         sha256='57f73e11f51999432bf7ab22ce55b6f945d5eca1bf824404cfa9ec2e3718c84e'),
    dict(filename='DejaVuSans-Bold.ttf', version='2.37 (Debian 2.37-8)', url=DEJAVU_URL,
         package_sha256='86635b3d25b3655fc11cb3ecc3af59f0bf19643b02b94f2de48bd10253cdba12',
         package_size=840460, member='./usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
         sha256='a4c5bc453ca281d90ea079e596da7ae0dfeb5777497c29ec254e76d97ff6f890'),
)
MAX_FONT_BYTES = 24 * 1024 * 1024
MAX_TAR_BYTES = 160 * 1024 * 1024


class FontError(Exception):
    """Explicit source, integrity, or destination failure; no font fallback."""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _physical(path):
    return not any(p.is_symlink() for p in (path, *path.parents))


def diagnose_fonts(directory=None):
    """Read-only: no network, mkdir, writes, or implicit system discovery."""
    base = Path(directory).absolute() if directory is not None else None
    rows = []
    for source in FONT_SOURCES:
        row = dict(source, status='MISSING')
        if base is not None:
            path = base / source['filename']
            if not _physical(path):
                row.update(status='FAIL', error='symlink rejected')
            elif path.exists():
                try:
                    if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > MAX_FONT_BYTES:
                        raise FontError('not a bounded regular font file')
                    actual = _digest(path.read_bytes())
                    row.update(actual_sha256=actual, status='PASS' if actual == source['sha256'] else 'FAIL')
                    if row['status'] == 'FAIL':
                        row['error'] = 'font SHA256 mismatch'
                except (OSError, FontError) as error:
                    row.update(status='FAIL', error=str(error))
        rows.append(row)
    status = 'FAIL' if any(r['status'] == 'FAIL' for r in rows) else (
        'PASS' if all(r['status'] == 'PASS' for r in rows) else 'MISSING')
    return dict(read_only=True, status=status, fonts=rows)


def _new_output(raw):
    if raw is None or not str(raw).strip():
        raise FontError('explicit new font output directory required')
    path = Path(os.path.abspath(os.path.expanduser(str(raw))))
    if not _physical(path) or path.exists():
        raise FontError('font output exists or contains a symlink: ' + str(path))
    if not path.parent.is_dir():
        raise FontError('font output parent must already exist')
    home = Path.home().resolve()
    cache = Path(os.environ.get('XDG_CACHE_HOME', home / '.cache')).resolve()
    forbidden = (ROOT, home, cache, Path('/usr'), Path('/Library/Fonts'),
                 Path('/System'), Path('/Windows'))
    if any(path == p or p in path.parents for p in forbidden):
        raise FontError('font output is inside repo, HOME, cache, or system directory')
    return path


class _DebianRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject off-origin/insecure redirects BEFORE urllib sends the next request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith('https://deb.debian.org/'):
            raise FontError('unexpected font source redirect: ' + newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener():
    # Per-download opener only: never mutate urllib's process-global opener.
    return urllib.request.build_opener(_DebianRedirectHandler())


def _download(source):
    request = urllib.request.Request(source['url'], headers={'User-Agent': 'Framivra-demo-fonts/1'})
    try:
        with _build_opener().open(request, timeout=60) as response:
            # Defense in depth; the redirect handler already checks next hops.
            final = response.geturl()
            if not final.startswith('https://deb.debian.org/'):
                raise FontError('unexpected font source redirect: ' + final)
            data = response.read(source['package_size'] + 1)
    except (OSError, ValueError) as error:
        raise FontError('font download failed: %s: %s' % (source['url'], error)) from error
    if len(data) != source['package_size'] or _digest(data) != source['package_sha256']:
        raise FontError('package size/SHA256 mismatch: ' + source['url'])
    return data


def _data_tar(package):
    """Parse only bounded simple Unix ar records, never invoke package tools."""
    if package[:8] != b'!<arch>\n':
        raise FontError('bad Debian ar signature')
    pos, result = 8, None
    while pos < len(package):
        header = package[pos:pos + 60]
        if len(header) != 60 or header[58:] != b'`\n':
            raise FontError('bad Debian ar header')
        try:
            size = int(header[48:58])
            name = header[:16].decode('ascii').strip().rstrip('/')
        except (ValueError, UnicodeError) as error:
            raise FontError('bad Debian ar record') from error
        start = pos + 60
        if size < 0 or start + size > len(package):
            raise FontError('truncated Debian ar record')
        if name == 'data.tar.xz':
            if result is not None:
                raise FontError('duplicate Debian data archive')
            result = package[start:start + size]
        pos = start + size + size % 2
    if result is None:
        raise FontError('missing Debian data.tar.xz')
    return result


def _extract(package, sources):
    # Decompress incrementally, with a ceiling BEFORE constructing a tar reader.
    import lzma
    try:
        decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
        raw = decoder.decompress(_data_tar(package), max_length=MAX_TAR_BYTES + 1)
        if len(raw) > MAX_TAR_BYTES or not decoder.eof or decoder.unused_data:
            raise FontError('oversized or incomplete Debian data archive')
        wanted = {s['member']: s for s in sources}
        found = {}
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            for member in archive:
                if member.name not in wanted:
                    continue  # Never extract arbitrary paths, links, scripts, or metadata.
                source = wanted[member.name]
                name = source['filename']
                if name in found or not member.isfile() or not 0 < member.size <= MAX_FONT_BYTES:
                    raise FontError('invalid/duplicate font archive member: ' + name)
                with archive.extractfile(member) as handle:
                    data = handle.read(MAX_FONT_BYTES + 1)
                if len(data) != member.size or _digest(data) != source['sha256']:
                    raise FontError('font SHA256 mismatch: ' + name)
                found[name] = data
        if len(found) != len(wanted):
            raise FontError('missing required font archive member')
        return found
    except (lzma.LZMAError, tarfile.TarError, EOFError, OSError, ValueError) as error:
        raise FontError('invalid font package: ' + str(error)) from error


def fetch_fonts(output):
    """Explicit write-state: fetch/verify all inputs, then create ONLY new output.

    Failure before publication leaves no destination. A publication I/O failure
    may leave a partial directory, reports failure, and never overwrites/reuses it.
    No archive files or installation commands are written or executed.
    """
    destination = _new_output(output)
    fonts = {}
    try:
        for url in dict.fromkeys(s['url'] for s in FONT_SOURCES):
            sources = [s for s in FONT_SOURCES if s['url'] == url]
            fonts.update(_extract(_download(sources[0]), sources))
        # Recheck after network activity, and use exclusive creation for both
        # directory and files. Existing directories are never accepted as output.
        _new_output(destination)
        destination.mkdir(mode=0o700)
        for name, data in fonts.items():
            with (destination / name).open('xb') as handle:
                handle.write(data)
    except OSError as error:
        raise FontError('font fetch/publication failed: ' + str(error)) from error
    return dict(read_only=False, status='PASS', output=str(destination),
                fonts=[dict(filename=s['filename'], sha256=s['sha256'],
                            size=len(fonts[s['filename']])) for s in FONT_SOURCES])
