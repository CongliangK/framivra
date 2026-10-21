"""Archive evidence and adversarial regressions, offline synthetic fixtures."""
import copy
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

import sys

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.policy import distribution_license_audit as audit
from scripts.policy import model_license_policy as policy

ROOT = Path(__file__).resolve().parents[2]


class ArchiveInventory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'fixture.tar.gz'
        self.data = policy.load_inventory()

    def tar(self, files, links=None):
        with tarfile.open(self.path, 'w:gz') as archive:
            for name, content in files.items():
                entry = tarfile.TarInfo('source/' + name)
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))
            for name, target in (links or {}).items():
                entry = tarfile.TarInfo('source/' + name)
                entry.type = tarfile.SYMTYPE
                entry.linkname = target
                archive.addfile(entry)
        return self.path

    def source_files(self):
        paths = {'LICENSE'}
        for asset in self.data['assets'].values():
            if asset['default_payload']:
                paths.update(asset.get('files_sha256', {}))
            if asset.get('version_home') and asset.get('lock_sha256'):
                paths.add(asset['version_home'])
        return {p: (ROOT / p).read_bytes() for p in paths}

    def test_source_reports_resolved_payload_and_vendored_notices(self):
        report = audit.inspect_archive(self.tar(self.source_files()), 'git-source')
        self.assertTrue(report['integrity_checks_passed'])
        self.assertFalse(report['legal_clearance'])
        self.assertEqual({}, report['release_blockers'])
        self.assertIn('vendored-libspng', report['inventoried_assets_present'])

    def test_declared_source_resolution_does_not_hide_future_blocker(self):
        data = copy.deepcopy(self.data)
        data['assets']['electron-worker-source']['distribution_blocker'] = 'fixture unresolved rights'
        report = audit.inspect_archive(self.tar(self.source_files()), 'git-source', inventory=data)
        self.assertEqual(report['release_blockers'], {'electron-worker-source': 'fixture unresolved rights'})
        self.assertFalse(report['legal_clearance'])

    def test_payload_blocker_resolution_keeps_adaptation_warnings_visible(self):
        report = policy.audit_payload()
        self.assertTrue(report['known_payload_blockers_absent'])
        self.assertEqual({}, report['release_blockers'])
        self.assertFalse(report['release_eligible'])
        self.assertFalse(report['legal_clearance'])
        self.assertEqual(3, len(report['provenance_warnings']))
        for ident in ('placeholder-media', 'electron-worker-source'):
            self.assertEqual('DECLARED', self.data['assets'][ident]['license_status'])
            self.assertIn(self.data['assets'][ident]['evidence'], self.data['evidence'])

    def test_missing_vendor_notice_or_changed_embedded_notice_fails(self):
        files = self.source_files()
        target = 'experiments/gfx_cef_worker/third_party/spng/LICENSE'
        del files[target]
        with self.assertRaises(policy.LicensePolicyError):
            audit.inspect_archive(self.tar(files), 'git-source')
        files = self.source_files()
        files['experiments/gfx_cef_worker/third_party/spng/spng.c'] += b'changed'
        with self.assertRaisesRegex(policy.LicensePolicyError, 'changed inventoried'):
            audit.inspect_archive(self.tar(files), 'git-source')

    def test_sdist_is_not_required_to_bundle_git_source_vendors(self):
        report = audit.inspect_archive(self.tar({'LICENSE': (ROOT / 'LICENSE').read_bytes()}), 'sdist')
        self.assertEqual(report['inventoried_assets_present'], [])
        self.assertFalse(report['legal_clearance'])

    def test_unknown_model_and_traversal_are_rejected(self):
        for name in ('weights.safetensors', '../escape'):
            with self.subTest(name=name), self.assertRaises(policy.LicensePolicyError):
                audit.inspect_archive(self.tar({'LICENSE': (ROOT / 'LICENSE').read_bytes(), name: b'bad'}), 'sdist')

    def test_relative_source_links_are_recorded_never_followed(self):
        report = audit.inspect_archive(self.tar({'LICENSE': (ROOT / 'LICENSE').read_bytes()}, {'scripts/link': '../apps/missing'}), 'sdist')
        self.assertEqual(report['members']['scripts/link']['symlink_target'], '../apps/missing')
        with self.assertRaises(policy.LicensePolicyError):
            audit.inspect_archive(self.tar({'LICENSE': b''}, {'link': '../../outside'}), 'sdist')

    def test_wheel_rejects_non_kernel_payload_and_requires_license(self):
        path = Path(self.tmp.name) / 'fixture.whl'
        for extra in ('apps/tool.py', 'vtx/weights.onnx', 'vtx/font.ttf', 'vtx/image.png', 'vtx/audio.wav', None):
            with zipfile.ZipFile(path, 'w') as archive:
                if extra:
                    archive.writestr('framivra-2.0.1.dist-info/licenses/LICENSE', (ROOT / 'LICENSE').read_bytes())
                    archive.writestr(extra, b'fixture')
                else:
                    archive.writestr('vt.py', b'fixture')
            with self.subTest(extra=extra), self.assertRaises(policy.LicensePolicyError):
                audit.inspect_archive(path, 'wheel')

    def test_required_evidence_cannot_be_symlink_target_text(self):
        # Seven reproduced false positives: minimal sdist plus six source paths.
        paths = ('LICENSE',
                 'experiments/gfx_cef_worker/third_party/fpng/LICENSE',
                 'experiments/gfx_cef_worker/third_party/spng/LICENSE',
                 'experiments/gfx_cef_worker/cef_lock.json',
                 'experiments/gfx_electron_worker/package-lock.json',
                 'experiments/gfx_electron_worker/package.json')
        for kind, name in [('sdist', 'LICENSE')] + [('git-source', p) for p in paths]:
            files = self.source_files() if kind == 'git-source' else {'LICENSE': (ROOT / 'LICENSE').read_bytes()}
            target = files.pop(name).decode('utf-8')
            with self.subTest(kind=kind, name=name), self.assertRaises(policy.LicensePolicyError):
                audit.inspect_archive(self.tar(files, {name: target}), kind)

    def test_present_sdist_lock_and_inventory_evidence_must_be_regular(self):
        for name in ('experiments/gfx_cef_worker/cef_lock.json',
                     'experiments/gfx_electron_worker/package.json'):
            with self.subTest(name=name), self.assertRaises(policy.LicensePolicyError):
                audit.inspect_archive(self.tar({'LICENSE': (ROOT / 'LICENSE').read_bytes()},
                                               {name: (ROOT / name).read_text()}), 'sdist')

    def test_archive_member_type_and_malformed_boundaries(self):
        for member_type in (tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            with tarfile.open(self.path, 'w') as archive:
                entry = tarfile.TarInfo('source/LICENSE')
                entry.type = member_type
                entry.linkname = 'source/notice'
                archive.addfile(entry)
            with self.subTest(member_type=member_type), self.assertRaises(policy.LicensePolicyError):
                audit.inspect_archive(self.path, 'sdist')
        self.path.write_bytes(b'not an archive')
        with self.assertRaises(tarfile.TarError):
            audit.inspect_archive(self.path, 'sdist')
        with self.assertRaises(zipfile.BadZipFile):
            audit.inspect_archive(self.path, 'wheel')

    def test_notice_case_duplicate_and_link_boundaries(self):
        license_bytes = (ROOT / 'LICENSE').read_bytes()
        for name in ('license', 'License'):
            with self.subTest(name=name), self.assertRaises(policy.LicensePolicyError):
                audit.inspect_archive(self.tar({name: license_bytes}), 'sdist')
        for target in ('/outside', '../../outside', 'bad\\target'):
            with self.subTest(target=target), self.assertRaises(policy.LicensePolicyError):
                audit.inspect_archive(self.tar({'LICENSE': license_bytes}, {'link': target}), 'sdist')
        with self.assertRaisesRegex(policy.LicensePolicyError, 'duplicate'):
            audit.inspect_archive(self.tar({'LICENSE': license_bytes}, {'LICENSE': 'notice'}), 'sdist')
        path = Path(self.tmp.name) / 'symlink.whl'
        with zipfile.ZipFile(path, 'w') as archive:
            entry = zipfile.ZipInfo('framivra-2.0.1.dist-info/licenses/LICENSE')
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            archive.writestr(entry, license_bytes)
        with self.assertRaisesRegex(policy.LicensePolicyError, 'symlink'):
            audit.inspect_archive(path, 'wheel')

    def test_external_locks_must_be_present_and_match_source_inventory(self):
        for ident in ('cef-runtime', 'electron-runtime'):
            name = self.data['assets'][ident]['version_home']
            for replacement in (None, b'unrelated'):
                files = self.source_files()
                if replacement is None:
                    del files[name]
                else:
                    files[name] = replacement
                with self.subTest(ident=ident, replacement=replacement), self.assertRaisesRegex(policy.LicensePolicyError, 'dependency lock'):
                    audit.inspect_archive(self.tar(files), 'git-source')

    def test_direct_optional_dependencies_are_explicit_unresolved_records(self):
        for ident in ('keyring', 'librosa', 'soxr', 'pesq', 'pystoi', 'opencc-runtime',
                      'opencc-dictionaries', 'zlib-runtime', 'davinci-resolve-sdk', 'native-platform-libraries',
                      'node-runtime', 'xdotool', 'matanyone-legacy-code', 'nvidia-external-tooling', 'host-build-toolchain'):
            entry = self.data['assets'][ident]
            self.assertEqual(entry['license_status'], 'UNKNOWN')
            self.assertFalse(entry['default_payload'])
            self.assertTrue(entry['source_references'])
            self.assertIn('BLOCKER', entry['notes'])

    def test_mixed_music_tools_has_explicit_api_boundary(self):
        self.assertIn('external-api', self.data['assets']['music-tools-assets']['distribution'])
        self.assertIn('matanyone-legacy-code', self.data['legacy_downloads']['matanyone-runtime']['assets'])

    def test_source_media_additions_require_individual_inventory(self):
        for name in ('extra/font.woff2', 'extra/picture.png', 'extra/sound.wav'):
            files = self.source_files()
            files[name] = b'new'
            with self.subTest(name=name), self.assertRaisesRegex(policy.LicensePolicyError, 'media/font'):
                audit.inspect_archive(self.tar(files), 'git-source')
            with self.assertRaisesRegex(policy.LicensePolicyError, 'media/font'):
                policy.audit_payload(tracked=[name])

    def test_source_unknowns_do_not_enable_unknown_default_weights(self):
        data = copy.deepcopy(self.data)
        data['assets']['matanyone2-weights'].update(default_payload=True, distribution=['git-source'], distribution_blocker='fixture')
        path = Path(self.tmp.name) / 'inventory.json'
        import json
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(policy.LicensePolicyError, 'unreviewed default'):
            policy.load_inventory(path)


if __name__ == '__main__':
    unittest.main()
