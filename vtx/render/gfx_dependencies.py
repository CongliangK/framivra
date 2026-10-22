"""Versioned, fail-closed HTML dependency identity.

Strict v1 accepts inert HTML, local raster images and a bounded CSS grammar.
It deliberately rejects JavaScript, SVG, embeddings, base tags, srcset, data URLs,
CSS escapes, custom properties, image-set and unknown CSS functions/at-rules.
Declarations remain timeline-root-relative, references document/CSS-relative.
"""
import hashlib
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

VERSION = 1


class DependencyError(ValueError):
    def __init__(self, reason, resource=None, origin=None, detail=None):
        self.reason, self.resource, self.origin, self.detail = reason, resource, origin, detail
        super().__init__(f'{reason}: {resource or ""} ({origin or "resource"})' +
                         (f': {detail}' if detail else ''))


def read_regular_bytes(path):
    """Read scanner/delivery bytes without a FIFO race after a prior hash."""
    import stat
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise DependencyError('content input is not a regular file', str(path))
        blocks = []
        for block in iter(lambda: os.read(fd, 1024 * 1024), b''):
            blocks.append(block)
        return b''.join(blocks)
    finally:
        os.close(fd)


def policy(c):
    from ..cache.cache_state import _get
    project = _get(c, c.tl, 'project', '$', {})
    value = _get(c, project, 'gfx_dependency_policy', '$.project', 'legacy')
    if value not in ('legacy', 'strict'):
        raise DependencyError('invalid gfx_dependency_policy', value)
    return value


def logical_local_url(url, base):
    """Preserve the requested URL base; canonical disk paths are not redirects."""
    try:
        value = urlsplit(urljoin(base, url))
    except ValueError as error:
        # Only URL parser failures are normalized here. Producer/file I/O errors
        # must not be swallowed as legacy parser uncertainty.
        raise DependencyError('malformed resource URL', url, base, str(error)) from error
    if value.scheme != 'file' or value.netloc or not value.path or '\\' in value.path:
        raise DependencyError('nonlocal or ambiguous URL', url)
    if re.search(r'%(?![0-9A-Fa-f]{2})', value.path):
        raise DependencyError('malformed URL escape', url)
    return urlunsplit((value.scheme, value.netloc, value.path, value.query, ''))


def local_path(url, base):
    logical = logical_local_url(url, base)
    path = unquote(urlsplit(logical).path, errors='strict')
    if '\0' in path or '\\' in path:
        raise DependencyError('invalid URL path', url)
    return Path(path).resolve()


class Scanner(HTMLParser):
    TAGS = set('html head body title meta style link div span p br hr h1 h2 h3 h4 h5 h6 ul ol li b i em strong small section article header footer main img table tbody thead tr td th'.split())
    VOID = {'meta', 'link', 'img', 'br', 'hr'}
    RAW_TEXT = {'style', 'title'}
    COMMON = set('id class style title lang dir'.split())
    ATTRS = {'meta': {'charset'}, 'link': {'rel', 'href', 'type'},
             'img': {'src', 'alt', 'width', 'height'}, 'td': {'colspan', 'rowspan'},
             'th': {'colspan', 'rowspan'}}
    FUNCTIONS = set('rgb rgba hsl hsla calc min max clamp linear-gradient radial-gradient repeating-linear-gradient repeating-radial-gradient translate translatex translatey scale rotate matrix cubic-bezier steps'.split())

    def __init__(self, html, declared):
        super().__init__(convert_charrefs=True)
        self.base = html.as_uri()
        self.declared = declared
        self.references = set()
        self.requests = set()
        self.stack = []
        self.css_seen = set()
        self.in_style = False

    def resource(self, url, base, kind):
        logical = logical_local_url(url, base)
        path = local_path(logical, base)
        if str(path) not in self.declared:
            raise DependencyError('undeclared dependency', url, base)
        suffixes = {'css': {'.css'}, 'image': {'.png', '.jpg', '.jpeg', '.webp', '.gif'},
                    'css-url': {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.woff', '.woff2', '.ttf', '.otf'}}
        if path.suffix.lower() not in suffixes[kind]:
            raise DependencyError('unsupported resource type', url, base)
        self.references.add(str(path))
        self.requests.add((logical, str(path), kind))
        if len(self.requests) > 1024:
            raise DependencyError('static resource budget exceeded', base)
        if kind == 'css' and logical not in self.css_seen:
            if len(self.css_seen) >= 128:
                raise DependencyError('stylesheet traversal budget exceeded', logical)
            self.css_seen.add(logical)
            data = read_regular_bytes(path)
            if hashlib.sha256(data).hexdigest() != self.declared[str(path)]:
                raise DependencyError('CSS changed during dependency resolution', str(path))
            self.css(data.decode('utf-8'), logical)

    def css(self, value, base):
        # No escape or comment ambiguity: comments are unsupported rather than
        # stripped by a regex which might change strings/token boundaries.
        if any(ord(c) < 32 and c not in '\n\r\t' for c in value) or any(x in value for x in ('\\', '/*', '*/', '--', '<', '>')):
            raise DependencyError('unsupported CSS syntax', base)
        token = re.compile(r'''\s+|"[^"\n\r]*"|'[^'\n\r]*'|[A-Za-z_-][A-Za-z0-9_-]*|[0-9.]+|[^\s]''')
        tokens = [m.group() for m in token.finditer(value) if not m.group().isspace()]
        # The fallback token alternative must not silently consume a broken
        # string. Boundaries are validated before resource discovery/proof.
        brackets = []
        closing = {')': '(', '}': '{', ']': '['}
        for item in tokens:
            if item in ('"', "'"):
                raise DependencyError('unterminated CSS string', base)
            if item in ('(', '{', '['):
                brackets.append(item)
            elif item in closing:
                if not brackets or brackets.pop() != closing[item]:
                    raise DependencyError('unbalanced CSS delimiter', base)
        if brackets:
            raise DependencyError('unbalanced CSS delimiter', base)
        i = 0
        while i < len(tokens):
            t = tokens[i].lower()
            if t == '@':
                if i + 1 >= len(tokens):
                    raise DependencyError('malformed CSS at-rule', base)
                name = tokens[i + 1].lower()
                if name == 'import':
                    if i + 3 >= len(tokens) or tokens[i + 2][:1] not in ('"', "'") or tokens[i + 3] != ';':
                        raise DependencyError('only quoted CSS import supported', base)
                    self.resource(tokens[i + 2][1:-1], base, 'css')
                    i += 4
                    continue
                if name not in ('font-face', 'media', 'supports'):
                    raise DependencyError('unsupported CSS at-rule', name, base)
            if i + 1 < len(tokens) and tokens[i + 1] == '(':
                if t == 'url':
                    if i + 3 >= len(tokens) or tokens[i + 2][:1] not in ('"', "'") or tokens[i + 3] != ')':
                        raise DependencyError('only quoted CSS url supported', base)
                    self.resource(tokens[i + 2][1:-1], base, 'css-url')
                    i += 4
                    continue
                if t not in self.FUNCTIONS and t not in ('format',):
                    raise DependencyError('unsupported CSS function', t, base)
            if t in ('behavior', '-moz-binding'):
                raise DependencyError('active CSS forbidden', t, base)
            i += 1

    def handle_startendtag(self, tag, attrs):
        # HTML ignores the self-closing flag for non-void elements; the stdlib
        # default instead calls start+end, which can hide raw-text resources.
        if tag not in self.VOID:
            raise DependencyError('self-closing non-void HTML unsupported', tag, self.base)
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag, attrs):
        if self.stack and self.stack[-1] in self.RAW_TEXT:
            raise DependencyError('nested HTML in raw-text element unsupported', tag, self.base)
        if tag not in self.TAGS:
            raise DependencyError('unsupported HTML tag', tag, self.base)
        names = [k for k, _ in attrs]
        if len(names) != len(set(names)):
            raise DependencyError('duplicate HTML attribute', tag, self.base)
        values = dict(attrs)
        for name, value in attrs:
            if name not in self.COMMON | self.ATTRS.get(tag, set()) or value is None:
                raise DependencyError('unsupported HTML attribute', name, self.base)
            if name == 'style':
                self.css(value, self.base)
        if tag == 'meta' and values.get('charset', '').lower() not in ('utf-8', 'utf8'):
            raise DependencyError('only UTF-8 meta charset supported', tag, self.base)
        if tag == 'link':
            if values.get('rel', '').lower() != 'stylesheet' or not values.get('href'):
                raise DependencyError('only stylesheet links supported', tag, self.base)
            self.resource(values['href'], self.base, 'css')
        if tag == 'img' and values.get('src'):
            self.resource(values['src'], self.base, 'image')
        if tag not in self.VOID:
            self.stack.append(tag)
        if tag == 'style':
            self.in_style = True

    def handle_endtag(self, tag):
        if tag not in self.TAGS:
            raise DependencyError('unsupported HTML closing tag', tag, self.base)
        if tag in self.VOID or not self.stack or self.stack[-1] != tag:
            raise DependencyError('unbalanced or implicit HTML closure unsupported', tag, self.base)
        self.stack.pop()
        if tag == 'style':
            self.in_style = False

    def handle_comment(self, data):
        raise DependencyError('HTML comments unsupported in strict grammar', self.base)

    def handle_decl(self, decl):
        if decl.lower() != 'doctype html':
            raise DependencyError('unsupported HTML declaration', decl, self.base)

    def handle_data(self, data):
        if not self.in_style and '<' in data:
            raise DependencyError('ambiguous HTML text', data, self.base)
        if self.in_style:
            self.css(data, self.base)

    def unknown_decl(self, data):
        raise DependencyError('unsupported HTML declaration', data, self.base)

    def handle_pi(self, data):
        raise DependencyError('unsupported processing instruction', data, self.base)


def resolve(c, clip, *, html_path=None, origin='$'):
    # Keep native producer operations in cache_state, so its operation-level
    # diagnostic annotations survive integration. Do not attribute file I/O to
    # a broad synthetic diagnostic operation here.
    from ..cache.cache_state import resolved_declarations, _field, _path
    selected = policy(c)
    if selected == 'strict' and os.name != 'posix':
        raise DependencyError('strict v1 currently requires POSIX file URI semantics')
    html = (_path(c, _field(c, clip, 'src', origin), origin + '.src')
            if html_path is None else html_path).resolve()
    entries = resolved_declarations(c, clip, origin=origin)
    html_bytes = read_regular_bytes(html)
    record = {'schema': VERSION, 'policy': selected, 'html_uri': html.as_uri(),
              'html_sha256': hashlib.sha256(html_bytes).hexdigest(), 'dependencies': entries,
              'closure': 'unproven', 'reasons': ['legacy_dynamic_closure_unproven']}
    scanner = Scanner(html, {e['path']: e['sha256'] for e in entries})
    try:
        scanner.feed(html_bytes.decode('utf-8'))
        if scanner.rawdata or scanner.in_style or scanner.stack:
            raise DependencyError('incomplete or ambiguous HTML', html.as_uri())
        scanner.close()
    except (DependencyError, UnicodeError) as error:
        if selected == 'strict':
            raise
        record['static_error'] = str(error)
        record['legacy_key_safe'] = False
    else:
        record['references'] = sorted(scanner.references)
        record['resource_requests'] = [dict(url=url, path=path, kind=kind)
                                       for url, path, kind in sorted(scanner.requests)]
        record['legacy_key_safe'] = not scanner.references
        if selected == 'strict':
            record.update(closure='static-subset-v1', reasons=[])
    return record


def identity(record):
    return json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()


def digest(record):
    return hashlib.sha256(identity(record)).hexdigest()


class RequestPolicy:
    """CDP Fetch policy. Paused local requests are fulfilled from pinned bytes.

    A successful command enabling Fetch is not evidence of file interception.
    Main-document and every scanned resource interception are required before
    capture. Unsupported Chrome implementations therefore fail closed.
    """
    def __init__(self, record):
        self.record = record
        self.expected = {e['path']: e['sha256'] for e in record['dependencies']}
        main = local_path(record['html_uri'], record['html_uri'])
        self.expected[str(main)] = record['html_sha256']
        self.observed = set()
        self.violation = None

    def response(self, params):
        import base64
        import mimetypes
        url = params['request']['url']
        request_id = params['requestId']
        try:
            path = local_path(url, self.record['html_uri'])
            kind = params.get('resourceType')
            if kind not in ('Document', 'Stylesheet', 'Image', 'Font'):
                raise DependencyError('unsupported runtime resource type', kind, url)
            if kind == 'Document' and path.as_uri() != self.record['html_uri']:
                raise DependencyError('unexpected document navigation', url)
            if str(path) not in self.expected:
                raise DependencyError('undeclared runtime request', url)
            # file_digest supplies the regular-file/nonblocking defense before
            # reading; bytes are checked again and are what Chrome receives.
            from ..cache.cache_state import file_digest
            if file_digest(path) != self.expected[str(path)]:
                raise DependencyError('dependency changed before delivery', url)
            data = read_regular_bytes(path)
            if hashlib.sha256(data).hexdigest() != self.expected[str(path)]:
                raise DependencyError('dependency changed during delivery', url)
            self.observed.add(str(path))
            mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
            return 'Fetch.fulfillRequest', {'requestId': request_id, 'responseCode': 200,
                'responseHeaders': [{'name': 'Content-Type', 'value': mime}],
                'body': base64.b64encode(data).decode('ascii')}
        except (OSError, ValueError) as error:
            self.violation = error
            return 'Fetch.failRequest', {'requestId': request_id, 'errorReason': 'BlockedByClient'}

    def check(self, require_complete=False):
        if self.violation is not None:
            raise DependencyError('strict runtime request rejected', str(self.violation))
        if require_complete:
            required = set(self.record.get('references', []))
            required.add(str(local_path(self.record['html_uri'], self.record['html_uri'])))
            missing = required - self.observed
            if missing:
                raise DependencyError('file interception coverage unproven', sorted(missing))
