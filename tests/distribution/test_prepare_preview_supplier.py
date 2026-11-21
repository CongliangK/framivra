import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import install_source
from scripts.release import prepare_preview_supplier as supplier


@unittest.skipUnless(sys.platform == 'linux', 'Linux ordinary preview supplier')
class PreparePreviewSupplierTest(unittest.TestCase):
    def test_candidate_statement_never_authorizes_bytes(self):
        # Fixed test-only digests; neither is derived from arbitrary input at enrollment.
        launcher_pin = '9425addd014a17ef4302f973f0e83f302b0cc6498061a10b7a692cd26a9f90e0'
        wheel_pin = 'bf03283dd06df5bc98544d3325b3adabf7ba4010f39a3740a862ea562222e548'
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp).resolve()
            (candidate / 'vtx-preview').write_bytes(b'not an approved launcher')
            (candidate / 'kernel.whl').write_bytes(b'not an approved wheel')
            (candidate / 'candidate.json').write_text(json.dumps({
                'launcherSha256': supplier._sha((candidate / 'vtx-preview').read_bytes()),
                'wheelSha256': supplier._sha((candidate / 'kernel.whl').read_bytes()),
            }))
            with patch.object(supplier.os, 'geteuid', return_value=1000), patch.object(supplier, '_run') as run:
                with self.assertRaisesRegex(supplier.SupplierError, 'independently approved'):
                    supplier.qualify_candidate(candidate, wheel_filename='framivra-2.3.0-py3-none-any.whl',
                        launcher_sha256=launcher_pin, wheel_sha256=wheel_pin, work_parent=candidate, python=sys.executable)
                run.assert_not_called()

    def test_enroll_publishes_same_probe_directory_and_recovers_exact_match(self):
        approved = {'launcher_sha256': supplier._sha(b'fixture launcher'),
                    'wheel_sha256': supplier._sha(b'fixture wheel'), 'projection_sha256': 'a' * 64,
                    'wheel_filename': 'framivra-2.3.0-py3-none-any.whl', 'python': sys.executable}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            old = root / 'installation.json'
            old.write_bytes(b'{"status":"ready"}\n')
            launch, wheel = root / 'test-launch', root / 'test-wheel'
            launch.write_bytes(b'fixture launcher')
            wheel.write_bytes(b'fixture wheel')
            seen = []

            def probe(scratch, profile, *, fresh, expected_projection, **pins):
                self.assertEqual(expected_projection, 'a' * 64)
                seen.append(fresh is not None)
                if fresh is not None:
                    profile.mkdir(mode=0o700)
                    supplier._write(profile / 'vtx-preview', fresh[0], 0o500)
                    supplier._write(profile / 'kernel.whl', fresh[1])
                    supplier._write(profile / 'kernel.sha256', (pins['wheel_sha256'] + '\n').encode())
                    (profile / 'runtime').mkdir(mode=0o700)
                    self.assertFalse((root / 'preview').exists())
                    seen.append(profile.stat().st_ino)
                supplier._verified_profile(profile, pins['launcher_sha256'], pins['wheel_sha256'])
                return {'runtimeProjectionSha256': expected_projection}

            with patch.object(supplier, '_qualify_profile', side_effect=probe) as verify:
                result = supplier.enroll_profile(root, launcher_path=launch, fetch_wheel=lambda: wheel, **approved)
                self.assertEqual((root / 'preview').stat().st_ino, seen[1])
                self.assertEqual(old.read_bytes(), b'{"status":"ready"}\n')  # interruption before receipt
                again = supplier.enroll_profile(root, launcher_path=launch,
                    fetch_wheel=lambda: self.fail('adoption must not fetch'), **approved)
                self.assertEqual(again, result)
                self.assertEqual(seen[::2], [True, False])
                self.assertEqual(verify.call_count, 2)

    def test_mismatch_and_failed_probe_never_change_active_bytes(self):
        approved = {'launcher_sha256': supplier._sha(b'fixture launcher'),
                    'wheel_sha256': supplier._sha(b'fixture wheel'), 'projection_sha256': 'a' * 64,
                    'wheel_filename': 'framivra-2.3.0-py3-none-any.whl', 'python': sys.executable}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            launcher, wheel, profile = root / 'test-launch', root / 'test-wheel', root / 'preview'
            launcher.write_bytes(b'fixture launcher')
            wheel.write_bytes(b'fixture wheel')
            active = root / 'installation.json'
            active.write_bytes(b'{"status":"ready"}\n')
            with patch.object(supplier, '_qualify_profile', side_effect=supplier.SupplierError('fixture failed')):
                with self.assertRaisesRegex(supplier.SupplierError, 'fixture failed'):
                    supplier.enroll_profile(root, launcher_path=launcher, fetch_wheel=lambda: wheel, **approved)
            self.assertFalse(profile.exists())
            profile.mkdir(mode=0o700)
            foreign = profile / 'owner-data'
            foreign.write_bytes(b'unrelated')
            with self.assertRaisesRegex(supplier.SupplierError, 'unexpected entries'):
                supplier.enroll_profile(root, launcher_path=launcher,
                    fetch_wheel=lambda: self.fail('conflict must not fetch'), **approved)
            self.assertEqual(foreign.read_bytes(), b'unrelated')
            self.assertEqual(active.read_bytes(), b'{"status":"ready"}\n')

    def test_empty_public_catalog_is_nonprobing(self):
        root = Path('/not-a-real-installation')
        with patch.object(install_source, '_PREVIEW_SUPPLIER_RELEASES', {}), \
                patch.object(install_source, 'git') as git, patch.object(install_source.downloads, 'acquire') as acquire:
            self.assertIsNone(install_source.optional_preview_supplier(root, root, '0' * 40, install_source.REPOSITORY,
                                                                     sys.executable))
            git.assert_not_called()
            acquire.assert_not_called()

    def test_real_output_limit_stops_child_before_accepting_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(supplier.SupplierError, 'output limit'):
                supplier._run([sys.executable, '-IB', '-c', 'import os; os.write(1,b"x"*65536)'],
                              cwd=root, env=supplier._env(root), limit=64, timeout=3)

    def test_rejects_wrong_clock_and_projection_on_genuine_envelope_shape(self):
        media = b'\0\0\0\x10ftypmp42\0\0\0\0'
        pins = {'wheel_sha256': '1' * 64, 'runtime_projection_sha256': '2' * 64}
        version = 'sha256:' + '3' * 64
        response = {'schema': supplier._WINDOW, 'ok': True, 'content_version': version,
                    'launcher': supplier._LAUNCHER, **pins, 'input_epoch': 'sha256:' + '4' * 64,
                    'start': 0, 'end': 30, 'fps': {'num': 30, 'den': 1}, 'width': 1920, 'height': 1080,
                    'mime': 'video/mp4', 'audio_sample_rate': 48000, 'audio_channels': 2,
                    'base64': base64.b64encode(media).decode('ascii'), 'byte_length': len(media),
                    'sha256': hashlib.sha256(media).hexdigest()}
        wire = lambda item: (json.dumps(item) + '\n').encode()
        self.assertEqual(supplier._receipt(wire(response), pins, version), media)
        for update in ({'runtime_projection_sha256': '5' * 64}, {'fps': {'num': 30, 'den': True}}, {'end': 29}):
            with self.subTest(update=update), self.assertRaisesRegex(supplier.SupplierError, 'clock'):
                supplier._receipt(wire(dict(response, **update)), pins, version)
