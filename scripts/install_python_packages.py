#!/usr/bin/env python3
"""Resolve one trusted pip index, freeze its wheels, acquire by SHA, install offline.

This is not a dependency resolver: pip owns resolution and wheel compatibility.
The persisted report-derived plan is reused until requirements/ABI change or an
explicit update. No global pip configuration or process environment is modified.
"""
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.parse

if __package__:
    from .install_downloads import DownloadError, IntegrityError, acquire, atomic_json, physical
else:
    from install_downloads import DownloadError, IntegrityError, acquire, atomic_json, physical

INDEXES = ('https://pypi.tuna.tsinghua.edu.cn/simple',
           'https://mirrors.aliyun.com/pypi/simple', 'https://pypi.org/simple')
HOSTS = frozenset(('pypi.tuna.tsinghua.edu.cn', 'mirrors.tuna.tsinghua.edu.cn',
                   'mirrors.aliyun.com', 'files.pythonhosted.org'))
MAX_JSON = 4 * 1024 ** 2
MAX_WHEELS = 128
IDENTITY_FIELDS = frozenset(('version', 'implementation', 'cache_tag', 'platform', 'soabi', 'machine', 'executable'))
IDENTITY_CODE = """import json,sys,sysconfig,platform,importlib.metadata as m
print(json.dumps(dict(interpreter=dict(version=sys.version,implementation=sys.implementation.name,
cache_tag=sys.implementation.cache_tag or '',platform=sysconfig.get_platform(),
soabi=sysconfig.get_config_var('SOABI') or '',machine=platform.machine(),executable=sys.executable),pip=m.version('pip'))))"""
VERIFY_CODE = """import importlib.metadata as m,json,sys
expected=json.loads(sys.argv[1]); print(json.dumps({name:m.version(name) for name in expected}))"""


def _json(text, label):
    if not isinstance(text, str) or not text or len(text.encode('utf-8')) > MAX_JSON:
        raise IntegrityError('Missing or oversized ' + label)
    try:
        value = json.loads(text)
    except (ValueError, UnicodeError) as error:
        raise IntegrityError('Invalid JSON in ' + label) from error
    if not isinstance(value, dict):
        raise IntegrityError('Expected an object in ' + label)
    return value


def _read(path, label):
    path = physical(path)
    if not path.is_file() or path.stat().st_size > MAX_JSON:
        raise IntegrityError('Missing or oversized ' + label)
    try:
        return _json(path.read_text(encoding='utf-8'), label)
    except UnicodeError as error:
        raise IntegrityError('Invalid UTF-8 in ' + label) from error


def _requirements(requirements):
    if not isinstance(requirements, (list, tuple)):
        raise IntegrityError('Python requirements must be an explicit list')
    values = list(requirements)
    if not 0 < len(values) <= 64 or any(not isinstance(v, str) or not 0 < len(v) <= 512 or
                                         v.startswith('-') or any(ord(c) < 32 for c in v) for v in values):
        raise IntegrityError('A bounded explicit Python requirements list is required')
    return values


def _environment(environment, index=None):
    env = dict(environment)
    # --isolated excludes user config, but alone still reads global/site config.
    # pip's documented devnull special case disables those files as well.
    env['PIP_CONFIG_FILE'] = os.devnull
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    if index and urllib.parse.urlsplit(index).hostname != 'pypi.org':
        host = urllib.parse.urlsplit(index).hostname
        for key in ('no_proxy', 'NO_PROXY'):
            current = env.get(key, '')
            env[key] = ','.join(x for x in (current, host, 'mirrors.tuna.tsinghua.edu.cn') if x)
    return env


def _pip(python, root):
    return [str(python), '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check',
            '--no-input', '--retries', '1', '--timeout', '60', '--cache-dir', str(root / 'cache' / 'pip')]


def _security_failure(error):
    return re.search(r'certificate.verify.failed|CERTIFICATE_VERIFY_FAILED|SSLError|SSL/TLS|'
                     r'hash.mismatch|DO NOT MATCH THE HASHES|not a supported wheel', str(error), re.I) is not None


def _interpreter(python, environment, run):
    value = _json(run([str(python), '-I', '-c', IDENTITY_CODE], env=_environment(environment), capture=True, timeout=60),
                  'Python interpreter identity')
    identity, version = value.get('interpreter'), value.get('pip')
    if (not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS or
            any(not isinstance(v, str) or len(v) > 4096 for v in identity.values()) or
            not isinstance(version, str) or not re.match(r'^\d+\.\d+', version)):
        raise IntegrityError('Invalid Python interpreter/pip identity')
    return identity, version


def _pip_version(value):
    match = re.match(r'^(\d+)\.(\d+)', value)
    if not match:
        raise IntegrityError('Invalid pip version')
    return tuple(map(int, match.groups()))


def _wheel_url(url):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 32 for c in url) or '\\' in url:
        raise IntegrityError('Invalid wheel URL')
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise IntegrityError('Malformed wheel URL') from error
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or port not in (None, 443) or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise IntegrityError('Wheel must use a trusted credential-free HTTPS package host')
    _, separator, suffix = parsed.path.partition('/packages/')
    parts = urllib.parse.unquote(suffix).split('/')
    if (not separator or not suffix or len(parts) > 12 or
            any(not p or p in ('.', '..') or any(c in p for c in ':\\\x00') or any(ord(c) < 32 for c in p) for p in parts)):
        raise IntegrityError('Invalid package storage path')
    filename = parts[-1]
    # Match the shared downloader's safe basename contract; retain pip's actual
    # wheel filename (a SHA prefix would make it unparsable to pip).
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+\-]{0,195}\.whl', filename):
        raise IntegrityError('Only an explicit binary .whl is allowed')
    if '/' in urllib.parse.unquote(filename):
        raise IntegrityError('Escaped wheel path separator')
    return suffix, filename


def wheel_urls(url, network='cn'):
    suffix, _ = _wheel_url(url)
    official = 'https://files.pythonhosted.org/packages/' + suffix
    if network == 'official':
        return [official]
    if network != 'cn':
        raise ValueError('Unknown Python network policy: ' + str(network))
    return ['https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/' + suffix,
            'https://mirrors.aliyun.com/pypi/packages/' + suffix, official]


def _wheels(values):
    if not isinstance(values, list) or not 0 < len(values) <= MAX_WHEELS:
        raise IntegrityError('Python plan must contain a bounded, nonempty wheel set')
    seen = set()
    for wheel in values:
        if not isinstance(wheel, dict) or set(wheel) != {'name', 'version', 'url', 'sha256', 'filename'}:
            raise IntegrityError('Invalid wheel plan entry')
        name, version, sha = wheel['name'], wheel['version'], wheel['sha256']
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', name) or
                not isinstance(version, str) or not 0 < len(version) <= 128 or any(ord(c) < 33 for c in version) or
                not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha)):
            raise IntegrityError('Invalid Python wheel name/version/SHA256')
        canonical = re.sub(r'[-_.]+', '-', name).lower()
        if canonical in seen:
            raise IntegrityError('Duplicate Python distribution in wheel plan')
        seen.add(canonical)
        _, filename = _wheel_url(wheel['url'])
        if filename != wheel['filename']:
            raise IntegrityError('Wheel filename differs from its locked URL')
    return values


def _plan(value):
    fields = {'schema', 'identity', 'index', 'wheels'}
    # The unreleased v1 had no completion state. Conservatively resume it, never
    # choose newer versions just because its old sidecar publication was torn.
    if value.get('schema') == 'framivra.python-plan/v1' and set(value) == fields:
        value = dict(value, schema='framivra.python-plan/v2', pending=True)
    if (set(value) != fields | {'pending'} or value.get('schema') != 'framivra.python-plan/v2' or
            type(value.get('pending')) is not bool or value.get('index') not in INDEXES):
        raise IntegrityError('Invalid persisted Python plan schema/index/state')
    identity = value.get('identity')
    if (not isinstance(identity, dict) or set(identity) != {'requirements', 'interpreter'} or
            not isinstance(identity.get('interpreter'), dict) or set(identity['interpreter']) != IDENTITY_FIELDS or
            any(not isinstance(v, str) or len(v) > 4096 for v in identity['interpreter'].values())):
        raise IntegrityError('Invalid persisted Python plan identity')
    if not isinstance(identity.get('requirements'), list):
        raise IntegrityError('Invalid persisted Python requirements')
    _requirements(identity['requirements'])
    _wheels(value.get('wheels'))
    return value


def _from_report(value, identity, index):
    if value.get('version') != '1' or not isinstance(value.get('install'), list):
        raise IntegrityError('Unsupported or missing pip JSON installation report')
    entries = value['install']
    if not 0 < len(entries) <= MAX_WHEELS:
        raise IntegrityError('pip report has no wheels or exceeds the wheel-set bound')
    wheels = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise IntegrityError('Invalid pip report entry')
        metadata, download = entry.get('metadata'), entry.get('download_info')
        if not isinstance(metadata, dict) or not isinstance(download, dict):
            raise IntegrityError('pip report entry is missing metadata/download identity')
        url = download.get('url')
        _, filename = _wheel_url(url)
        archive = download.get('archive_info', {})
        hashes = archive.get('hashes', {}) if isinstance(archive, dict) else {}
        sha = hashes.get('sha256') if isinstance(hashes, dict) else None
        if not isinstance(sha, str):
            raise IntegrityError('pip report wheel lacks a SHA256 identity')
        wheels.append(dict(name=metadata.get('name'), version=metadata.get('version'),
                           url=url, sha256=sha.lower(), filename=filename))
    return _plan(dict(schema='framivra.python-plan/v2', identity=identity, index=index, wheels=wheels, pending=True))


def install(python, root, environment, requirements, *, network='cn', update=False, run):
    """Return the persisted, fully installed wheel plan, or fail without ready."""
    if network not in ('cn', 'official'):
        raise ValueError('Unknown Python network policy: ' + str(network))
    root = physical(root)
    cache = physical(root / 'cache')
    plan_path = physical(cache / 'python-plan.json')
    requirements = _requirements(requirements)
    existing = _plan(_read(plan_path, 'persisted Python plan')) if plan_path.exists() else None
    interpreter, pip_version = _interpreter(python, environment, run)
    identity = dict(requirements=requirements, interpreter=interpreter)
    cache.mkdir(parents=True, exist_ok=True)
    indexes = INDEXES if network == 'cn' else INDEXES[-1:]
    if _pip_version(pip_version) < (23, 2):
        # pip 22.2 introduced an experimental report. Use the stable v1 schema.
        errors = []
        for index in indexes:
            try:
                run([*_pip(python, root), 'install', '--upgrade', '--only-binary=:all:', '--index-url', index,
                     'pip>=23.2,<26'], env=_environment(environment, index), capture=True, timeout=None)
                _, pip_version = _interpreter(python, environment, run)
                if _pip_version(pip_version) < (23, 2):
                    raise IntegrityError('pip upgrade returned success without a report-capable pip')
                break
            except IntegrityError:
                raise
            except (RuntimeError, OSError) as error:
                if _security_failure(error):
                    raise IntegrityError('pip bootstrap security failure; not switching indexes') from error
                errors.append(str(error))
        else:
            raise DownloadError('No trusted index could bootstrap a report-capable pip')
    # An explicit update chooses versions once, not again after an interrupted
    # wheel/install/check. A new requirements/ABI identity still needs resolution.
    plan = existing if existing and existing['identity'] == identity and (not update or existing['pending']) else None
    if plan is None:
        with tempfile.TemporaryDirectory(prefix='pip-resolution-', dir=cache) as temporary:
            report = Path(temporary) / 'report.json'
            for index in indexes:
                try:
                    run([*_pip(python, root), 'install', '--dry-run', '--ignore-installed', '--only-binary=:all:',
                         '--index-url', index, '--report', str(report), *requirements],
                        env=_environment(environment, index), capture=True, timeout=None)
                except IntegrityError:
                    raise
                except (RuntimeError, OSError) as error:
                    if _security_failure(error):
                        raise IntegrityError('pip resolution security failure; not switching indexes') from error
                    # A failed attempt's partial report is not acceptance evidence.
                    if report.exists():
                        physical(report).unlink()
                    continue
                # Exit zero alone is not a report. Malformed/sdist/foreign-host or
                # missing-hash output is a hard failure, not another index probe.
                plan = _from_report(_read(report, 'pip installation report'), identity, index)
                break
            else:
                raise DownloadError('No trusted index resolved a complete binary wheel set; source compilation is disabled')
    plan = dict(plan, pending=True)
    atomic_json(plan_path, plan)
    wheels = [acquire(cache / 'downloads', wheel_urls(wheel['url'], network), wheel['sha256'], filename=wheel['filename'])
              for wheel in plan['wheels']]
    run([*_pip(python, root), 'install', '--no-index', '--no-deps', *map(str, wheels)],
        env=_environment(environment), capture=True, timeout=None)
    run([*_pip(python, root), 'check'], env=_environment(environment), capture=True, timeout=120)
    expected = {wheel['name']: wheel['version'] for wheel in plan['wheels']}
    actual = _json(run([str(python), '-I', '-c', VERIFY_CODE, json.dumps(expected)],
                       env=_environment(environment), capture=True, timeout=60), 'installed Python distributions')
    if actual != expected:
        raise IntegrityError('pip returned success but installed distributions differ from the locked wheel plan')
    plan = dict(plan, pending=False)
    atomic_json(plan_path, plan)
    return plan
