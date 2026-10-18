"""Offline GitHub transaction and third-party installer tests; never live publish."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]

FAKE_GH = r'''#!/usr/bin/env python3
import base64, json, os
from pathlib import Path
import sys
state_path = Path(os.environ['MOCK_GH_STATE'])
s = json.loads(state_path.read_text())
a = sys.argv[1:]
s['calls'].append(a)
def save(): state_path.write_text(json.dumps(s))
def fail(message):
    save(); print(message, file=sys.stderr); sys.exit(1)
if a[0] == 'api':
    endpoint = a[1]
    if endpoint == 'graphql':
        if os.environ.get('MOCK_API_FAILURE'): fail('network unavailable (HTTP 503)')
        release = {'databaseId': s['release']['id']} if s['release'] else None
        repo = {'release': release}
        if os.environ.get('MOCK_LOOKUP_NO_REPO'): repo = None
        if os.environ.get('MOCK_LOOKUP_INVALID_ID'): repo = {'release': {'databaseId': 0}}
        print(json.dumps({'data': {'repository': repo}}))
    elif '--method' in a and a[a.index('--method') + 1] == 'PATCH':
        if os.environ.get('MOCK_FAIL_PUBLISH'): fail('injected publish failure')
        if len(s['release']['assets']) != 4 or 'draft=false' not in a or 'make_latest=true' not in a:
            fail('incomplete publish')
        s['release']['draft'] = False
        s['latest'] = s['release'].copy()
        print(json.dumps(s['release']))
    elif '/commits/' in endpoint:
        print(json.dumps({'sha': os.environ.get('MOCK_REMOTE_COMMIT', s['commit'])}))
    elif endpoint.endswith('/releases/latest'):
        print(json.dumps(s['latest']))
    elif '/releases/tags/' in endpoint:
        # Real REST cannot look up a draft's pending tag; expose that limitation.
        if s['release'] is None or s['release']['draft']: fail('release missing (HTTP 404)')
        print(json.dumps(s['release']))
    elif endpoint.endswith('/releases/2'):
        if s['release'] is None: fail('release missing (HTTP 404)')
        print(json.dumps(s['release']))
    else: fail('unknown api')
elif a[:2] == ['release', 'create']:
    if '--draft' not in a or '--verify-tag' not in a: fail('must create verified draft')
    s['release'] = {'id': 2, 'draft': True, 'prerelease': False,
                    'tag_name': a[2], 'assets': []}
elif a[:2] == ['release', 'upload']:
    path = Path(a[3])
    if not s['release']['draft']: fail('cannot mutate published assets')
    if os.environ.get('MOCK_FAIL_UPLOAD') == path.name: fail('injected upload failure')
    s['bytes'][path.name] = base64.b64encode(path.read_bytes()).decode()
    s['release']['assets'].append({'name': path.name, 'state': 'uploaded'})
elif a[:2] == ['release', 'download']:
    name = a[a.index('--pattern') + 1]
    data = base64.b64decode(s['bytes'][name])
    if os.environ.get('MOCK_CORRUPT_DOWNLOAD') == name: data += b'corrupt'
    if os.environ.get('MOCK_MISSING_DOWNLOAD') == name:
        pass
    else:
        (Path(a[a.index('--dir') + 1]) / name).write_bytes(data)
else: fail('unknown command')
save()
'''

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os
from pathlib import Path
import sys
args = sys.argv[1:]
urls = [arg for arg in args if arg.startswith('https://')]
if len(urls) != 1: sys.exit(91)
url = urls[0]
with Path(os.environ['MOCK_CURL_LOG']).open('a') as stream:
    stream.write(json.dumps(args) + '\n')
# Fail closed: local fixtures, no network fallback.
if not url.startswith(os.environ.get('MOCK_EXPECT_BASE', 'https://github.com/CongliangK/framivra/releases/')):
    sys.exit(92)
name = url.rsplit('/', 1)[-1]
path = Path(os.environ['MOCK_CURL_FIXTURES']) / name
if not path.is_file(): sys.exit(93)
data = path.read_bytes()
if '-o' in args: Path(args[args.index('-o') + 1]).write_bytes(data)
else: sys.stdout.buffer.write(data)
'''


class GithubRelease(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='framivra-release-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        (self.repo / 'scripts').mkdir()
        (self.repo / 'vtx').mkdir()
        for name in ('release.sh', 'release_github.py', 'install.sh'):
            shutil.copyfile(REPO / 'scripts' / name, self.repo / 'scripts' / name)
        (self.repo / 'VERSION').write_text('2.0.1\n')
        (self.repo / 'pyproject.toml').write_text('[project]\nname="framivra"\nversion = "2.0.1"\n')
        (self.repo / 'vtx/__init__.py').write_text('_FALLBACK_VERSION = "2.0.1"\n')
        (self.repo / 'vt.py').write_text('print("inert fixture")\n')
        self.env = {'PATH': os.environ.get('PATH', os.defpath),
                    'LANG': 'C.UTF-8', 'PYTHONIOENCODING': 'utf-8'}
        self.env['HOME'] = str(self.root / 'home')
        Path(self.env['HOME']).mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env['PATH'] = str(self.bin) + os.pathsep + self.env['PATH']
        (self.bin / 'gh').write_text(FAKE_GH)
        (self.bin / 'gh').chmod(0o755)
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'fixture')
        self.git('remote', 'add', 'origin', 'https://github.com/example/framivra.git')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.git('tag', '-a', 'v2.0.1', '-m', 'fixture')
        self.commit = self.git('rev-parse', 'HEAD').stdout.strip()
        self.state_path = self.root / 'state.json'
        self.old = {'id': 1, 'draft': False, 'tag_name': 'v1.9.0',
                    'prerelease': False, 'assets': [{'name': 'old.tar.gz', 'state': 'uploaded'}]}
        self.state_path.write_text(json.dumps({'commit': self.commit, 'calls': [],
                                              'latest': self.old, 'release': None,
                                              'old_bytes': 'old-complete-assets', 'bytes': {}}))
        self.env['MOCK_GH_STATE'] = str(self.state_path)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, env=self.env,
                              text=True, capture_output=True, check=True)

    def release(self, *args, **env):
        # Transaction fixtures isolate freeze/provenance; their strict real gates
        # have dedicated negative tests in test_release_freeze.py.
        harness = ("import importlib.util,sys,types; sys.dont_write_bytecode=True; "
                   "spec=importlib.util.spec_from_file_location('release',sys.argv[1]); "
                   "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m); "
                   "m.verify_freeze_evidence=lambda *a: None; "
                   "sys.modules['verify_release']=types.SimpleNamespace(verify_artifact=lambda *a,**kw: 1); "
                   "sys.exit(m.main(sys.argv[2:]))")
        return subprocess.run([sys.executable, '-c', harness, str(self.repo / 'scripts/release_github.py'),
                               'v2.0.1', *args], cwd=self.repo, env={**self.env, **env},
                              text=True, capture_output=True, timeout=30)

    def state(self):
        return json.loads(self.state_path.read_text())

    def assert_old_intact(self):
        state = self.state()
        self.assertEqual(state['latest'], self.old)
        self.assertEqual(state['old_bytes'], 'old-complete-assets')
        self.assertFalse(any('PATCH' in call for call in state['calls']))

    def test_publish_all_verified_assets_then_idempotent_retry(self):
        result = self.release()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.state()
        self.assertFalse(state['release']['draft'])
        self.assertEqual(state['latest']['id'], 2)
        expected = {'video-tools-2.0.1.tar.gz', 'install.sh', 'releases.json', 'SHA256SUMS'}
        self.assertEqual(set(state['bytes']), expected)
        calls = state['calls']
        publish = next(i for i, call in enumerate(calls) if 'PATCH' in call)
        self.assertEqual(sum(call[:2] == ['release', 'download'] for call in calls[:publish]), 4)
        data = base64.b64decode(state['bytes']['video-tools-2.0.1.tar.gz'])
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            self.assertEqual(archive.extractfile('VERSION').read(), b'2.0.1\n')
            frozen = archive.extractfile('scripts/install.sh').read()
        self.assertEqual(frozen, base64.b64decode(state['bytes']['install.sh']))
        metadata = json.loads(base64.b64decode(state['bytes']['releases.json']))
        self.assertEqual(metadata['releases'][0]['commit'], self.commit)
        result = self.release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum('PATCH' in call for call in self.state()['calls']), 1)

    def test_origin_verified_publication_ignores_ambient_host_override(self):
        result = self.release(GH_HOST='unrelated.invalid', GH_REPO='elsewhere/other')
        self.assertEqual(result.returncode, 0, result.stderr)
        for call in self.state()['calls']:
            if call[0] == 'api':
                self.assertEqual(call[call.index('--hostname') + 1], 'github.com')
            elif call[0] == 'release':
                self.assertEqual(call[call.index('--repo') + 1], 'github.com/example/framivra')

    def test_each_upload_failure_keeps_previous_published_release(self):
        for name in ('video-tools-2.0.1.tar.gz', 'install.sh', 'releases.json', 'SHA256SUMS'):
            with self.subTest(name=name):
                state = {'commit': self.commit, 'calls': [], 'latest': self.old,
                         'release': None, 'old_bytes': 'old-complete-assets', 'bytes': {}}
                self.state_path.write_text(json.dumps(state))
                self.assertNotEqual(self.release(MOCK_FAIL_UPLOAD=name).returncode, 0)
                self.assert_old_intact()
                self.assertTrue(self.state()['release']['draft'])

    def test_matching_partial_draft_can_resume_without_clobber(self):
        self.assertNotEqual(self.release(MOCK_FAIL_UPLOAD='install.sh').returncode, 0)
        first = self.state()['bytes']['video-tools-2.0.1.tar.gz']
        result = self.release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()['bytes']['video-tools-2.0.1.tar.gz'], first)
        uploads = [call[3] for call in self.state()['calls'] if call[:2] == ['release', 'upload']]
        self.assertEqual(sum(Path(path).name == 'video-tools-2.0.1.tar.gz' for path in uploads), 1)

    def test_every_corrupt_download_prevents_latest_switch(self):
        for name in ('video-tools-2.0.1.tar.gz', 'install.sh', 'releases.json', 'SHA256SUMS'):
            with self.subTest(name=name):
                self.state_path.write_text(json.dumps({'commit': self.commit, 'calls': [],
                    'latest': self.old, 'release': None, 'old_bytes': 'old-complete-assets', 'bytes': {}}))
                self.assertNotEqual(self.release(MOCK_CORRUPT_DOWNLOAD=name).returncode, 0)
                self.assert_old_intact()

    def test_download_command_success_without_file_is_not_verified(self):
        self.assertNotEqual(self.release(MOCK_MISSING_DOWNLOAD='install.sh').returncode, 0)
        self.assert_old_intact()

    def test_publish_failure_leaves_verified_draft_and_previous_latest(self):
        self.assertNotEqual(self.release(MOCK_FAIL_PUBLISH='1').returncode, 0)
        state = self.state()
        self.assertEqual(state['latest'], self.old)
        self.assertTrue(state['release']['draft'])
        self.assertEqual(len(state['release']['assets']), 4)

    def test_dirty_tree_and_old_tag_with_new_head_reject_before_network(self):
        (self.repo / 'VERSION').write_text('3.0.0\n')
        self.assertNotEqual(self.release().returncode, 0)
        self.assertEqual(self.state()['calls'], [])
        self.git('add', 'VERSION')
        self.git('commit', '-qm', 'new working commit')
        self.assertNotEqual(self.release().returncode, 0)
        self.assertEqual(self.state()['calls'], [])

    def test_archived_version_mismatch_rejects_even_when_head_matches_tag(self):
        (self.repo / 'vtx/__init__.py').write_text('_FALLBACK_VERSION = "9.9.9"\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'mismatched version')
        self.git('tag', '-f', 'v2.0.1')
        self.assertNotEqual(self.release().returncode, 0)
        self.assertEqual(self.state()['calls'], [])

    def test_wrong_target_repository_and_remote_tag_commit_reject(self):
        self.assertNotEqual(self.release('--repo', 'elsewhere/framivra').returncode, 0)
        self.assertEqual(self.state()['calls'], [])
        self.assertNotEqual(self.release(MOCK_REMOTE_COMMIT='0' * 40).returncode, 0)
        self.assertFalse(any(call[:2] == ['release', 'create'] for call in self.state()['calls']))
        self.assertEqual(self.state()['latest'], self.old)

    def test_network_error_is_not_treated_as_nonexistent_release(self):
        self.assertNotEqual(self.release(MOCK_API_FAILURE='1').returncode, 0)
        self.assertFalse(any(call[:2] == ['release', 'create'] for call in self.state()['calls']))

    def test_graphql_missing_repository_or_invalid_id_never_creates_release(self):
        for env in ({'MOCK_LOOKUP_NO_REPO': '1'}, {'MOCK_LOOKUP_INVALID_ID': '1'}):
            result = self.release(**env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(call[:2] == ['release', 'create'] for call in self.state()['calls']))
            self.assertEqual(self.state()['latest'], self.old)

    def test_local_dry_run_has_no_gh_calls_or_tree_changes(self):
        before = self.git('status', '--porcelain').stdout
        result = self.release('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()['calls'], [])
        self.assertEqual(self.git('status', '--porcelain').stdout, before)

    def write_local_attributes(self, text):
        info = self.repo / '.git/info'
        info.mkdir(exist_ok=True)
        (info / 'attributes').write_text(text)
        # Local Git metadata edits must stay invisible to status checks.
        self.assertEqual(self.git('status', '--porcelain').stdout, '')

    def retag(self, message):
        self.git('add', '.')
        self.git('commit', '-qm', message)
        self.git('tag', '-f', 'v2.0.1', '-m', 'fixture')
        self.commit = self.git('rev-parse', 'HEAD').stdout.strip()
        state = self.state()
        state['commit'] = self.commit
        self.state_path.write_text(json.dumps(state))

    def assert_no_remote_writes(self):
        state = self.state()
        self.assertFalse(any(call[:2] in (['release', 'create'], ['release', 'upload'])
                             or 'PATCH' in call for call in state['calls']))
        self.assertEqual(state['latest'], self.old)

    def test_hidden_archive_attributes_reject_versions_installer_and_runtime(self):
        # .git/info/attributes is untracked Git metadata: status stays clean,
        # yet export-ignore removes entries from the real git archive.
        self.write_local_attributes(
            'VERSION export-ignore\nvtx/__init__.py export-ignore\n'
            'pyproject.toml export-ignore\nscripts/install.sh export-ignore\n'
            'vt.py export-ignore\n')
        result = self.release()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('archive payload differs from tagged tree', result.stderr)
        self.assert_no_remote_writes()
        self.assertTrue(self.state()['release'] is None)

    def test_export_subst_byte_rewrite_rejects_even_with_all_names_present(self):
        # export-subst keeps every entry but rewrites bytes in flight.
        (self.repo / 'buildstamp').write_text('$Format:%H$\n')
        self.retag('add buildstamp')
        self.write_local_attributes('buildstamp export-subst\n')
        result = self.release()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('archive payload differs from tagged tree', result.stderr)
        self.assert_no_remote_writes()
        self.assertTrue(self.state()['release'] is None)

    def test_legal_archive_with_modes_symlink_and_nested_paths_publishes(self):
        (self.repo / 'vtx/core').mkdir(parents=True)
        (self.repo / 'vtx/core/engine.py').write_text('# runtime\n')
        tool = self.repo / 'scripts/tool.sh'
        tool.write_text('#!/bin/sh\necho tool\n')
        tool.chmod(0o755)
        (self.repo / 'current').symlink_to('VERSION')
        self.retag('rich tree')
        result = self.release()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.state()
        self.assertFalse(state['release']['draft'])
        data = base64.b64decode(state['bytes']['video-tools-2.0.1.tar.gz'])
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            members = {m.name: m for m in archive if not m.isdir()}
            self.assertIn('vtx/core/engine.py', members)
            self.assertIn('scripts/tool.sh', members)
            self.assertTrue(members['scripts/tool.sh'].mode & 0o111)
            self.assertTrue(members['current'].issym())
            self.assertEqual(members['current'].linkname, 'VERSION')
            self.assertEqual(archive.extractfile('VERSION').read(), b'2.0.1\n')
            # Payload must cover the whole tracked tree, not a key-file subset.
            tracked = {}
            for entry in self.git('ls-tree', '-r', '-z', 'v2.0.1').stdout.split('\0'):
                if entry:
                    meta, path = entry.split('\t', 1)
                    mode, kind, sha = meta.split(' ', 2)
                    tracked[path] = (mode, kind, sha)
            self.assertEqual(set(members), set(tracked))

    def test_global_gitattributes_export_ignore_also_rejects(self):
        config = self.root / 'global-attributes'
        config.write_text('vt.py export-ignore\n')
        self.git('config', 'core.attributesFile', str(config))
        result = self.release()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('archive payload differs from tagged tree', result.stderr)
        self.assert_no_remote_writes()

    def test_existing_draft_mismatched_asset_refuses_mutation(self):
        self.assertNotEqual(self.release(MOCK_FAIL_UPLOAD='install.sh').returncode, 0)
        state = self.state()
        state['bytes']['video-tools-2.0.1.tar.gz'] = base64.b64encode(b'wrong bytes').decode()
        self.state_path.write_text(json.dumps(state))
        self.assertNotEqual(self.release().returncode, 0)
        self.assert_old_intact()

    def test_invalid_version_refuses_before_network(self):
        for version in ('../oops', 'v2.0.1;echo', 'v2.0', '', 'v٢.٠.١'):
            result = subprocess.run(['bash', str(self.repo / 'scripts/release.sh'), version],
                cwd=self.repo, env=self.env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.state()['calls'], [])



# fake gh 双打(#16/#41):记录完整 argv(策略参数可断言),输出最小合法证词
# (certificate + transparency-log 见证)。仅测试;真实链路看 release-attest.yml。
FAKE_GH_ATTEST = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
log = os.environ.get('MOCK_GH_LOG')
if log:
    pathlib.Path(log).write_text(json.dumps(sys.argv) + "\n")
if os.environ.get('MOCK_GH_FAIL'):
    sys.stderr.write("attestation verification failed\n"); sys.exit(1)
print(json.dumps([{"verificationResult": {
    "signature": {"certificate": {"SAN": "fixture"}},
    "verifiedTimestamps": [{"timestamp": "fixture"}]}}]))
'''

class GithubInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='framivra-install-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixtures = self.root / 'fixtures'
        self.fixtures.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'curl').write_text(FAKE_CURL)
        (self.bin / 'curl').chmod(0o755)
        (self.bin / 'gh').write_text(FAKE_GH_ATTEST)
        (self.bin / 'gh').chmod(0o755)
        self.target = self.root / 'install'
        self.env = {'PATH': os.environ.get('PATH', os.defpath),
                    'LANG': 'C.UTF-8', 'PYTHONIOENCODING': 'utf-8'}
        self.env.update(HOME=str(self.root / 'home'),
                        PATH=str(self.bin) + os.pathsep + self.env['PATH'],
                        MOCK_CURL_FIXTURES=str(self.fixtures),
                        MOCK_CURL_LOG=str(self.root / 'curl.log'),
                        VTX_GH_BIN=str(self.bin / 'gh'))
        archive_path = self.fixtures / 'video-tools-2.0.1.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            entries = {'VERSION': b'2.0.1\n', 'vtx/__init__.py': b'# fixture\n',
                'vt.py': b'from pathlib import Path\nimport sys\nwith Path("calls").open("a") as s: s.write(sys.argv[1]+"\\n")\n'}
            for name, data in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        self.digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        (self.fixtures / 'SHA256SUMS').write_text(self.digest + '  ' + archive_path.name + '\n')
        (self.fixtures / 'releases.json').write_text('{"latest":"2.0.1"}')

    def install(self, *args, **env):
        extra = env.pop('args', [])
        # 发版通道直连 install.py(install.sh 已重构为 Linux/macOS 克隆引导,
        # 不再薄转发 install.py);UTF-8 显式解码与安装器口径一致。
        return subprocess.run([sys.executable, str(REPO / 'scripts/install.py'),
                               '--dir', str(self.target), *extra, *args],
            cwd=self.root, env={**self.env, **env}, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=30)

    def calls(self):
        log = self.root / 'curl.log'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_clean_third_party_install_uses_same_tag_checksums_and_follows_redirects(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / 'calls').read_text(), 'init\ndoctor\n')
        calls = self.calls()
        urls = [arg for call in calls for arg in call if arg.startswith('https://')]
        self.assertEqual(urls, [
            'https://github.com/CongliangK/framivra/releases/latest/download/releases.json',
            'https://github.com/CongliangK/framivra/releases/download/v2.0.1/video-tools-2.0.1.tar.gz',
            'https://github.com/CongliangK/framivra/releases/download/v2.0.1/SHA256SUMS'])
        self.assertTrue(all('-fsSL' in call for call in calls))
        before = (self.target / 'calls').read_bytes()
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual((self.target / 'calls').read_bytes(), before)

    def test_user_mirror_retains_discovery_and_version_scoped_assets(self):
        result = self.install(VIDEO_TOOLS_DL_BASE='https://mirror.invalid/base/',
                              MOCK_EXPECT_BASE='https://mirror.invalid/base/',
                              **{'args': ['--no-provenance']})
        self.assertEqual(result.returncode, 0, result.stderr)
        urls = [arg for call in self.calls() for arg in call if arg.startswith('https://')]
        self.assertEqual(urls[0], 'https://mirror.invalid/base/releases.json')
        self.assertEqual(urls[-1], 'https://mirror.invalid/base/v2.0.1/SHA256SUMS')

    def test_fork_github_base_uses_its_own_latest_and_version_assets(self):
        result = self.install(VIDEO_TOOLS_DL_BASE='https://github.com/example/framivra/releases/download',
                              MOCK_EXPECT_BASE='https://github.com/example/framivra/releases/')
        self.assertEqual(result.returncode, 0, result.stderr)
        urls = [arg for call in self.calls() for arg in call if arg.startswith('https://')]
        self.assertEqual(urls[0], 'https://github.com/example/framivra/releases/latest/download/releases.json')
        self.assertEqual(urls[-1], 'https://github.com/example/framivra/releases/download/v2.0.1/SHA256SUMS')

    def test_default_and_existing_checkout_dry_run_are_zero_download(self):
        self.assertEqual(self.install('--dry-run').returncode, 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.calls(), [])
        self.target.mkdir()
        (self.target / 'vt.py').write_text('# user checkout')
        (self.target / 'vtx').mkdir()
        (self.target / 'VERSION').write_text('2.0.1')
        self.assertEqual(self.install('--dry-run').returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_bad_or_duplicate_checksum_never_installs(self):
        manifest = self.fixtures / 'SHA256SUMS'
        for text in ('0' * 64 + '  video-tools-2.0.1.tar.gz\n',
                     (self.digest + '  video-tools-2.0.1.tar.gz\n') * 2):
            manifest.write_text(text)
            self.assertNotEqual(self.install('--version', '2.0.1').returncode, 0)
            self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
