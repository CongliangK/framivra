"""Installer-only HTTPS acquisition: fixed identities, persistent Range cache.

Not a release signature scheme. Callers supply a hash from a trusted, explicit
source. Completed objects are checked on every use; interrupted bytes survive
process death. There is an idle/socket timeout, not a large-file wall deadline.
"""
import contextlib
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class DownloadError(RuntimeError):
    pass


class IntegrityError(DownloadError):
    pass


DOMESTIC_HOSTS = frozenset((
    'mirrors.tuna.tsinghua.edu.cn', 'pypi.tuna.tsinghua.edu.cn',
    'mirrors.bfsu.edu.cn', 'mirrors.ustc.edu.cn', 'mirrors.nju.edu.cn',
    'mirrors.aliyun.com', 'repo.huaweicloud.com', 'mirrors.huaweicloud.com',
    'mirrors.cloud.tencent.com', 'mirrors.sjtug.sjtu.edu.cn',
    'mirror.sjtu.edu.cn', 'registry.npmmirror.com', 'cdn.npmmirror.com',
))
# Redirects are for known upstream delivery domains, not arbitrary GitHub proxies.
DELIVERY_HOSTS = frozenset((
    *DOMESTIC_HOSTS, 'release-assets.githubusercontent.com',
    'objects.githubusercontent.com', 'downloads.sourceforge.net',
    'cdn-fastly.deb.debian.org',
))
MAX_BYTES = 2 * 1024 ** 3


def physical(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & 0x400):
            raise DownloadError('Installer cache must not follow symlinks/junctions: ' + str(item))
    return path


def digest(path):
    value = hashlib.sha256()
    with physical(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


@contextlib.contextmanager
def advisory_lock(path):
    """Kernel-owned lock: process death releases it, no stale PID deletion race."""
    path = physical(path)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise DownloadError('Another installation/download is running; retry after it exits: ' + str(path)) from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or '\\' in url
            or any(ord(x) < 32 for x in url)):
        raise IntegrityError('Only credential-free HTTPS download URLs are allowed')
    return parsed


def identity(url):
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        previous, target = validate_url(request.full_url), validate_url(newurl)
        trusted = (target.hostname == previous.hostname or target.hostname in DELIVERY_HOSTS
                   or (previous.hostname in ('sourceforge.net', 'downloads.sourceforge.net')
                       and target.hostname.endswith('.dl.sourceforge.net')))
        if not trusted:
            raise IntegrityError('Unexpected cross-origin download redirect: ' + str(target.hostname))
        return super().redirect_request(request, fp, code, message, headers, newurl)


def open_response(url, headers=None, timeout=60):
    parsed = validate_url(url)
    handlers = [HTTPSRedirects(), urllib.request.HTTPSHandler(context=ssl.create_default_context())]
    if parsed.hostname in DOMESTIC_HOSTS:
        # Per-request only. Existing overseas fallback continues to inherit the
        # user's proxy; do not edit process/global no_proxy or system settings.
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={'User-Agent': 'Framivra-source-installer',
                                                   'Accept-Encoding': 'identity', **(headers or {})})
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.URLError as error:
        if isinstance(error.reason, (ssl.SSLCertVerificationError, ssl.CertificateError)):
            raise IntegrityError('TLS certificate verification failed: ' + str(parsed.hostname)) from error
        raise


def read_json(urls, *, maximum=2_000_000, timeout=30):
    """Bounded trusted HTTPS metadata. Never executes or permanently caches it."""
    errors = []
    for url in urls:
        validate_url(url)
        try:
            with open_response(url, timeout=timeout) as response:
                raw = response.read(maximum + 1)
                if len(raw) > maximum:
                    raise IntegrityError('Download metadata exceeds size bound')
                result = json.loads(raw.decode('utf-8'))
                if not isinstance(result, dict):
                    raise DownloadError('Expected a JSON metadata object')
                return result, response.geturl()
        except IntegrityError:
            raise
        except (OSError, ValueError, urllib.error.URLError) as error:
            errors.append(type(error).__name__ + ': ' + str(urllib.parse.urlsplit(url).hostname))
    raise DownloadError('No metadata source available: ' + '; '.join(errors))


def atomic_json(path, data):
    path = physical(path)
    temp = physical(path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp'))
    with temp.open('x', encoding='utf-8') as output:
        json.dump(data, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp, path)


def event(cache, **fields):
    path = physical(cache / 'downloads.jsonl')
    with path.open('a', encoding='utf-8') as output:
        output.write(json.dumps(dict(at=time.time(), **fields), sort_keys=True) + '\n')


def acquire(cache, urls, sha256, *, size=None, filename=None):
    if not re.fullmatch('[0-9a-fA-F]{64}', sha256 or ''):
        raise IntegrityError('Download needs a trusted SHA256 identity')
    sha256 = sha256.lower()
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_BYTES):
        raise IntegrityError('Invalid expected download size')
    filename = filename or (sha256 + '.blob')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+\-]{0,199}', filename) or filename in ('.', '..'):
        raise DownloadError('Unsafe download filename')
    if not urls or len(urls) > 8:
        raise DownloadError('A bounded list of explicit download sources is required')
    for url in urls:
        validate_url(url)
    cache = physical(cache)
    cache.mkdir(parents=True, exist_ok=True)
    directory = physical(cache / sha256)
    directory.mkdir(exist_ok=True)
    complete, part = physical(directory / filename), physical(directory / (filename + '.part'))
    state = physical(directory / (filename + '.json'))
    with advisory_lock(directory / '.lock'):
        if complete.exists():
            if (not complete.is_file() or (size is not None and complete.stat().st_size != size)
                    or digest(complete) != sha256):
                raise IntegrityError('Corrupt completed download cache; do not use: ' + str(complete))
            event(cache, artifact=filename, sha256=sha256, result='cache-hit', bytes=complete.stat().st_size)
            print('[download] verified cache: ' + filename, file=sys.stderr, flush=True)
            return complete
        if part.exists() and not part.is_file():
            raise DownloadError('Partial download is not a regular file')
        if part.exists() and part.stat().st_size > (size or MAX_BYTES):
            raise IntegrityError('Partial cache exceeds trusted download size: ' + str(part))
        errors = []
        for url in urls:
            host = urllib.parse.urlsplit(url).hostname
            finished = False
            for attempt in range(2):
                offset = part.stat().st_size if part.exists() else 0
                if size is not None and offset == size:
                    finished = True
                    break
                saved = {}
                if state.exists():
                    if not state.is_file() or state.stat().st_size > 16_384:
                        raise DownloadError('Invalid partial download metadata')
                    try:
                        saved = json.loads(state.read_text(encoding='utf-8'))
                    except (ValueError, UnicodeError):
                        saved = {}
                headers = {}
                if offset:
                    headers['Range'] = 'bytes=%d-' % offset
                    if isinstance(saved, dict) and saved.get('url') == identity(url) and saved.get('validator'):
                        headers['If-Range'] = saved['validator']
                print('[download] %s: %s, resume=%s bytes' % (filename, host, offset), file=sys.stderr, flush=True)
                event(cache, artifact=filename, sha256=sha256, host=host, result='request', resume_offset=offset)
                try:
                    with open_response(url, headers) as response:
                        status = response.getcode()
                        encoding = response.headers.get('Content-Encoding', 'identity')
                        if encoding not in ('identity', ''):
                            raise IntegrityError('Unexpected download content encoding')
                        length = response.headers.get('Content-Length')
                        length = int(length) if length is not None else None
                        total = size
                        mode = 'ab' if offset else 'wb'
                        if status == 206:
                            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                            if not match:
                                raise IntegrityError('Missing or malformed HTTP Content-Range')
                            start, end, declared = map(int, match.groups())
                            if start != offset or not start <= end < declared or (length is not None and length != end - start + 1):
                                raise IntegrityError('HTTP range does not match the saved offset')
                            if total is not None and total != declared:
                                raise IntegrityError('HTTP range total differs from trusted size')
                            total = declared
                        elif status == 200:
                            if offset:
                                print('[download] server cannot resume; restarting this artifact only', file=sys.stderr, flush=True)
                                event(cache, artifact=filename, sha256=sha256, host=host, result='range-reset')
                            offset, mode = 0, 'wb'
                            if length is not None:
                                if total is not None and total != length:
                                    raise IntegrityError('Content length differs from trusted size')
                                total = length
                        else:
                            raise DownloadError('Unexpected download HTTP status: ' + str(status))
                        if total is not None and not 0 < total <= MAX_BYTES:
                            raise IntegrityError('Download exceeds size bound')
                        validator = response.headers.get('ETag', '')
                        if validator.startswith('W/'):
                            validator = ''
                        validator = validator or response.headers.get('Last-Modified', '')
                        if '\r' in validator or '\n' in validator:
                            raise IntegrityError('Invalid response validator')
                        if status == 206 and headers.get('If-Range') and validator != headers['If-Range']:
                            raise IntegrityError('Partial response validator changed; refusing to concatenate versions')
                        received, progress_at = 0, time.monotonic()
                        # For a full response, truncate the old prefix before publishing
                        # its replacement validator. A killed process must not leave old
                        # bytes paired with the new representation's resume state.
                        with part.open(mode) as output:
                            atomic_json(state, dict(url=identity(url), validator=validator, sha256=sha256, total=total))
                            while True:
                                block = response.read(1024 * 1024)
                                if not block:
                                    break
                                if offset + received + len(block) > (total or MAX_BYTES):
                                    raise IntegrityError('Download body exceeds declared size')
                                output.write(block)
                                output.flush()
                                received += len(block)
                                if time.monotonic() - progress_at >= 15:
                                    print('[download] %s: %.1f MB' % (filename, (offset + received) / 1e6), file=sys.stderr, flush=True)
                                    progress_at = time.monotonic()
                            os.fsync(output.fileno())
                        actual = part.stat().st_size
                        if total is not None and actual != total:
                            raise DownloadError('Transfer interrupted; saved %d/%d bytes' % (actual, total))
                        if not actual:
                            raise DownloadError('Empty download response')
                    finished = True
                    break
                except IntegrityError:
                    event(cache, artifact=filename, sha256=sha256, host=host, result='integrity-error')
                    raise
                except urllib.error.HTTPError as error:
                    if error.code == 416:
                        total_match = re.fullmatch(r'bytes \*/(\d+)', error.headers.get('Content-Range', ''))
                        if total_match and int(total_match[1]) == offset and (size is None or size == offset):
                            finished = True
                            break
                    errors.append('%s: HTTP%s' % (host, error.code))
                    event(cache, artifact=filename, sha256=sha256, host=host, result='http-error', status=error.code)
                    if error.code not in (408, 429, 500, 502, 503, 504):
                        break
                    if error.code == 429:
                        # Do not hammer the server or sleep through a long server
                        # cooldown. Switch candidate; a later run may retry it.
                        break
                except (DownloadError, OSError, urllib.error.URLError, http.client.HTTPException) as error:
                    errors.append('%s: %s' % (host, type(error).__name__))
                    event(cache, artifact=filename, sha256=sha256, host=host, result='interrupted', reason=type(error).__name__)
                    print('[download] interrupted; retained partial bytes for retry', file=sys.stderr, flush=True)
            # A completed HTTP response/416 is not acceptance. Always check its
            # hash here, including after another source/attempt failed.
            if finished and part.exists():
                if digest(part) != sha256:
                    event(cache, artifact=filename, sha256=sha256, host=host, result='hash-mismatch')
                    raise IntegrityError('SHA256 mismatch; retained partial for diagnosis: ' + str(part))
                os.replace(physical(part), physical(complete))
                event(cache, artifact=filename, sha256=sha256, host=host, result='verified', bytes=complete.stat().st_size)
                return complete
        # A successful size-unknown transfer after an earlier candidate failed is
        # still recognized by its hash; do not infer completion from EOF alone.
        if part.exists() and part.stat().st_size and digest(part) == sha256:
            if size is not None and part.stat().st_size != size:
                raise IntegrityError('Verified content length differs from trusted size')
            os.replace(physical(part), physical(complete))
            event(cache, artifact=filename, sha256=sha256, result='verified', bytes=complete.stat().st_size)
            return complete
        raise DownloadError('Download incomplete; rerun the same command to resume %s: %s' % (filename, '; '.join(errors)))
