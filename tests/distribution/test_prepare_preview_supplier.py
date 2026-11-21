import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
