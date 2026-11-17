"""Persistent pip plans with fixture reports; no PyPI or native-install claim."""
import copy
import hashlib
import http.server
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse
import urllib.request

from scripts import install_downloads as downloads
from scripts import install_python_packages as packages


class PythonPackages(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='framivra-pip-plan-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = dict(os.environ, HTTPS_PROXY='http://proxy.invalid:7890', PIP_EXTRA_INDEX_URL='https://untrusted.invalid/simple')
        self.requirements = ['direct>=1,<3']
        self.identity = {name: 'fixture-' + name for name in packages.IDENTITY_FIELDS}
        self.pip = '25.3'
        self.calls = []
        self.reports = []
        self.report = dict(version='1', install=[self.entry('direct', '1.0'), self.entry('transitive', '2.0')])
        self.installed = True

    def entry(self, name, version, body=b'fixture wheel'):
        return dict(metadata=dict(name=name, version=version),
                    download_info=dict(url='https://files.pythonhosted.org/packages/ab/cd/' + name + '-' + version + '-py3-none-any.whl',
                                       archive_info=dict(hashes=dict(sha256=hashlib.sha256(body).hexdigest()))))

    def pip_run(self, argv, **options):
        argv = list(map(str, argv))
        self.calls.append((argv, options))
        if packages.IDENTITY_CODE in argv:
            return json.dumps(dict(interpreter=self.identity, pip=self.pip))
        if packages.VERIFY_CODE in argv:
            return argv[-1] if self.installed else '{}'
        if '--report' in argv:
            self.reports.append(argv)
            Path(argv[argv.index('--report') + 1]).write_text(json.dumps(self.report), encoding='utf-8')
        return ''

    def acquire(self, cache, urls, sha256, *, filename):
        path = self.root / 'fixture-wheels' / filename
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'fixture wheel')
        return path

    def install(self, **kwargs):
        return packages.install('fixture-python', self.root, self.environment, self.requirements, run=self.pip_run, **kwargs)

    def test_resume_uses_the_persisted_transitive_plan_then_update_reresolves(self):
        with mock.patch.object(packages, 'acquire', side_effect=RuntimeError('interrupted before wheel completion')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.install(update=True)
        path = self.root / 'cache/python-plan.json'
        saved = json.loads(path.read_text())
        self.assertTrue(saved['pending'])
        legacy = dict(saved, schema='framivra.python-plan/v1')
        legacy.pop('pending')
        path.write_text(json.dumps(legacy))
        self.assertEqual(len(self.reports), 1)
        self.report['install'][0] = self.entry('direct', '2.0')
        with mock.patch.object(packages, 'acquire', side_effect=self.acquire):
            resumed = self.install(update=True)
            self.assertFalse(resumed['pending'])
            self.assertEqual(len(self.reports), 1)
            self.assertEqual(resumed['wheels'][0]['version'], '1.0')
            self.assertEqual(json.loads(path.read_text()), dict(saved, pending=False))
            updated = self.install(update=True)
        self.assertEqual(len(self.reports), 2)
        self.assertEqual(updated['wheels'][0]['version'], '2.0')
        self.assertEqual([x['name'] for x in updated['wheels']], ['direct', 'transitive'])

    def test_requirements_or_interpreter_abi_change_resolves_a_new_plan(self):
        with mock.patch.object(packages, 'acquire', side_effect=RuntimeError('interrupted old ABI')):
            with self.assertRaises(RuntimeError):
                self.install(update=True)
        self.identity = dict(self.identity, soabi='new-soabi')
        publish = packages.atomic_json
        def interrupt_after_publication(path, value):
            publish(path, value)
            raise RuntimeError('interrupted after new plan publication')
        with mock.patch.object(packages, 'atomic_json', side_effect=interrupt_after_publication):
            with self.assertRaisesRegex(RuntimeError, 'after new plan publication'):
                self.install(update=True)
        self.assertEqual(len(self.reports), 2)
        with mock.patch.object(packages, 'acquire', side_effect=self.acquire):
            self.install(update=True)
            self.assertEqual(len(self.reports), 2)
            self.requirements = ['direct>=1,<4']
            self.install(update=True)
        self.assertEqual(len(self.reports), 3)

    def test_all_wheels_install_offline_and_global_configs_cannot_add_indexes(self):
        original = dict(self.environment)
        with mock.patch.object(packages, 'acquire', side_effect=self.acquire):
            plan = self.install()
        self.assertEqual(self.environment, original)
        self.assertEqual(len(plan['wheels']), 2)
        resolve, options = next((a, e) for a, e in self.calls if '--report' in a)
        self.assertIn('--isolated', resolve)
        self.assertIn('--ignore-installed', resolve)
        self.assertIn('--only-binary=:all:', resolve)
        self.assertNotIn('--extra-index-url', resolve)
        self.assertEqual(options['env']['PIP_CONFIG_FILE'], os.devnull)
        self.assertIn('pypi.tuna.tsinghua.edu.cn', options['env']['NO_PROXY'])
        self.assertEqual(options['env']['HTTPS_PROXY'], original['HTTPS_PROXY'])
        local, options = next((a, e) for a, e in self.calls if '--no-index' in a)
        self.assertIn('--no-deps', local)
        self.assertIsNone(options['timeout'])
        self.assertEqual(sum(a.endswith('.whl') for a in local), 2)
        self.assertFalse(any(a.startswith('https:') for a in local))
        self.assertTrue(any('check' in a for a, _ in self.calls))

    def test_invalid_report_and_sdist_never_download_or_install(self):
        cases = []
        cases.append({})
        missing_hash = copy.deepcopy(self.report)
        missing_hash['install'][0]['download_info']['archive_info'] = {}
        cases.append(missing_hash)
        source = copy.deepcopy(self.report)
        source['install'][0]['download_info']['url'] = 'https://files.pythonhosted.org/packages/a/source.tar.gz'
        cases.append(source)
        foreign = copy.deepcopy(self.report)
        foreign['install'][0]['download_info']['url'] = 'https://untrusted.invalid/packages/a/direct-1-py3-none-any.whl'
        cases.append(foreign)
        for report in cases:
            with self.subTest(report=report):
                self.report = report
                self.calls.clear()
                with mock.patch.object(packages, 'acquire') as acquire:
                    with self.assertRaises(packages.IntegrityError):
                        self.install()
                acquire.assert_not_called()
                self.assertFalse(any('--no-index' in a for a, _ in self.calls))
                self.assertFalse((self.root / 'cache/python-plan.json').exists())

    def test_zero_without_report_or_html_is_not_resolution_success(self):
        for body in (None, '<html>not a report</html>'):
            def run(argv, **kwargs):
                if '--report' in argv:
                    if body is not None:
                        Path(argv[argv.index('--report') + 1]).write_text(body)
                    return ''
                return self.pip_run(argv, **kwargs)
            with self.subTest(body=body), mock.patch.object(packages, 'acquire') as acquire:
                with self.assertRaises(packages.IntegrityError):
                    packages.install('python', self.root, self.environment, self.requirements, run=run)
                acquire.assert_not_called()

    def test_corrupt_persisted_plan_fails_even_when_update_is_requested(self):
        path = self.root / 'cache/python-plan.json'
        path.parent.mkdir()
        path.write_text('{torn record')
        with mock.patch.object(packages, 'acquire') as acquire:
            with self.assertRaises(packages.IntegrityError):
                self.install(update=True)
        acquire.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_hash_failure_hard_stops_before_any_local_install(self):
        with mock.patch.object(packages, 'acquire', side_effect=packages.IntegrityError('SHA mismatch')) as acquire:
            with self.assertRaises(packages.IntegrityError):
                self.install()
        acquire.assert_called_once()
        self.assertFalse(any('--no-index' in a for a, _ in self.calls))

    def test_zero_install_and_check_do_not_prove_distributions_exist(self):
        self.installed = False
        with mock.patch.object(packages, 'acquire', side_effect=self.acquire):
            with self.assertRaisesRegex(packages.IntegrityError, 'installed distributions differ'):
                self.install()

    def test_single_index_failure_falls_back_but_tls_failure_does_not(self):
        def run(argv, **kwargs):
            if '--report' in argv and packages.INDEXES[0] in argv:
                raise RuntimeError('index unavailable')
            return self.pip_run(argv, **kwargs)
        with mock.patch.object(packages, 'acquire', side_effect=self.acquire):
            plan = packages.install('python', self.root, self.environment, self.requirements, run=run)
        self.assertEqual(plan['index'], packages.INDEXES[1])
        def insecure(argv, **kwargs):
            if '--report' in argv:
                raise RuntimeError('SSLError: CERTIFICATE_VERIFY_FAILED')
            return self.pip_run(argv, **kwargs)
        with mock.patch.object(packages, 'acquire') as acquire:
            with self.assertRaises(packages.IntegrityError):
                packages.install('python', self.root, self.environment, self.requirements, update=True, run=insecure)
        acquire.assert_not_called()

    def test_old_pip_bootstrap_is_verified_not_assumed_from_exit_zero(self):
        self.pip = '21.3.1'
        def upgrade(argv, **kwargs):
            if '--upgrade' in argv:
                self.pip = '23.2'
            return self.pip_run(argv, **kwargs)
        with mock.patch.object(packages, 'acquire', side_effect=self.acquire):
            packages.install('python', self.root, self.environment, self.requirements, run=upgrade)
        command = next(a for a, _ in self.calls if '--upgrade' in a)
        self.assertIn('pip>=23.2,<26', command)
        self.assertIn('--only-binary=:all:', command)
        self.pip = '21.3.1'
        with mock.patch.object(packages, 'acquire') as acquire:
            with self.assertRaisesRegex(packages.IntegrityError, 'without a report-capable pip'):
                self.install()
        acquire.assert_not_called()

    def test_exact_wheel_falls_back_to_official_with_same_sha_over_real_loopback(self):
        payload = b'fixture wheel'
        self.report['install'] = [self.entry('direct', '1.0', payload)]
        observed = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append(self.path)
                if self.path != '/files.pythonhosted.org':
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def open_response(url, headers=None, timeout=60):
            host = urllib.parse.urlsplit(url).hostname
            return urllib.request.urlopen('http://127.0.0.1:%s/%s' % (server.server_address[1], host), timeout=3)
        with mock.patch.object(downloads, 'open_response', side_effect=open_response):
            self.install()
        self.assertEqual(observed, ['/mirrors.tuna.tsinghua.edu.cn', '/mirrors.aliyun.com', '/files.pythonhosted.org'])
        sha = hashlib.sha256(payload).hexdigest()
        self.assertEqual(next((self.root / 'cache/downloads' / sha).glob('*.whl')).read_bytes(), payload)


if __name__ == '__main__':
    unittest.main()
