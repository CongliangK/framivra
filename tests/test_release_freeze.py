"""Fail-closed release freeze policy; no network or renderer fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from scripts import release_github as release


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.asset = self.root / 'asset'; self.asset.write_bytes(b'asset')
        self.commit = 'a' * 40
        self.data = {'schema': 'framivra.freeze/1', 'commit': self.commit, 'tag': 'v2.1.0',
          'assets': {'asset': release.digest(self.asset)}, 'gates': {},
          'acceptance': {'path': 'package', 'manifest_sha256': 'f' * 64}}
        for name in release.REQUIRED_FREEZE_GATES:
            log = self.root / (name + '.log')
            log.write_text(json.dumps({'integrity_checks_passed': True, 'release_blockers': {}}))
            step = {'argv': ['real-command'], 'exit_code': 0, 'expected_exit': 0,
                    'log': log.name, 'sha256': release.digest(log)}
            report = self.root / (name + '.json')
            report.write_text(json.dumps({'schema': 'framivra.freeze-gate/1', 'commit': self.commit,
                    'tag': 'v2.1.0', 'gate': name, 'status': 'passed', 'steps': [step]}))
            self.data['gates'][name] = {'status': 'passed', 'path': report.name, 'sha256': release.digest(report)}
        self.path = self.root / 'freeze.json'

    def verify(self):
        self.path.write_text(json.dumps(self.data))
        return release.verify_freeze_evidence(self.path, self.commit, 'v2.1.0', [self.asset])

    def test_missing_evidence_rejected(self):
        with self.assertRaisesRegex(release.ReleaseError, 'required'):
            release.verify_freeze_evidence(None, self.commit, 'v2.1.0', [self.asset])

    def test_missing_unknown_and_failed_required_gates_rejected(self):
        for status in ('unknown', 'failed', 'skipped'):
            self.data['gates']['local_edit']['status'] = status
            with self.assertRaises(release.ReleaseError): self.verify()
        self.data['gates'].pop('local_edit')
        with self.assertRaises(release.ReleaseError): self.verify()

    def test_wrong_commit_or_asset_rejected(self):
        self.data['commit'] = 'b' * 40
        with self.assertRaises(release.ReleaseError): self.verify()
        self.data['commit'] = self.commit; self.asset.write_bytes(b'tamper')
        with self.assertRaises(release.ReleaseError): self.verify()

    def test_changed_raw_log_rejected(self):
        (self.root / 'local_edit.log').write_bytes(b'changed')
        with self.assertRaises(release.ReleaseError): self.verify()

    def test_independent_required_unknown_rejected(self):
        result = {'gates': {name: {'status': 'passed'} for name in
           ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc', 'process', 'input_stability', 'tool_versions', 'media')}}
        (self.root / 'package').mkdir()
        (self.root / 'package/environment.json').write_text(json.dumps({'git': {'status': 'passed', 'stdout': self.commit}, 'git_status': {'status': 'passed', 'stdout': ''}}))
        with mock.patch('vtx.acceptance.verify', return_value=result):
            self.verify()
            result['gates']['qc']['status'] = 'unknown'
            with self.assertRaisesRegex(release.ReleaseError, 'qc'): self.verify()

    def test_public_summary_never_copies_environment_paths_or_logs(self):
        package = self.root / 'package'; package.mkdir()
        (package / 'environment.json').write_text(json.dumps({'python': '3.13.5',
           'tools': {name: {'stdout': name + ' version 8.1.3 Copyright build secret-private'} for name in ('ffmpeg', 'ffprobe')},
           'account': 'secret-private', 'HOME': '/private/user', 'logs': 'secret-private'}))
        self.path.write_text(json.dumps(self.data))
        summary_path = release.build_public_summary(self.path, self.root)
        raw = summary_path.read_text()
        self.assertNotIn('secret-private', raw)
        self.assertNotIn('/private/user', raw)
        summary = json.loads(raw)
        self.assertEqual(summary['tool_versions'], {'python': '3.13.5', 'ffmpeg': '8.1.3', 'ffprobe': '8.1.3'})
        self.assertEqual(set(summary), {'schema', 'version', 'tag', 'source_commit', 'core_asset_sha256', 'gates',
           'tool_versions', 'support_scope', 'limitations', 'local_freeze_sha256', 'reproduction_guide'})

    def test_rc_accepted_but_unsupported_tag_rejected(self):
        self.assertEqual(release.tag_argument('v2.1.0-rc.1'), 'v2.1.0-rc.1')
        for tag in ('2.1.0', 'v2.1.0-rc.0', 'main'):
            with self.assertRaises(Exception): release.tag_argument(tag)


if __name__ == '__main__': unittest.main()
