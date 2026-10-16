"""Real Blender regression for content-addressed gfx (opt in VTX_TEST_BLENDER=1).

Synthetic PNGs use the production completion writer; no browser/CEF rendering.
The worker fixture is an identity-only executable and is never launched.
"""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from vtx.api import gfx_dir, gfx_key
from vtx import gfx_cli


def png_bytes(value=0):
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
    return (gfx_cli.PNG_SIGNATURE
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 8, 6, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + bytes([value, 40, 80, 255]) * 8) * 6))
            + chunk(b'IEND', b''))


class CacheFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('VTX_')}
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'
        self.worker = self.root / 'worker'
        self.worker.write_bytes(b'identity-only-worker-v1')
        self.worker.chmod(0o700)
        self.env.update(VTX_GFX='cef', VTX_CEF_MODE='a0',
                        VTX_CEF_WORKER=str(self.worker), VTX_GFX_CACHE_NS='selected-ns')
        self.patch = mock.patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        (self.root / 'gfx').mkdir()
        (self.root / 'gfx' / 'page.html').write_text('<html>frame driven</html>')
        self.clip = {'src': 'gfx/page.html', 'dst_in': 0, 'dur': 3}
        self.tl = {'version': 2,
                   'project': {'fps': 30, 'width': 8, 'height': 6, 'duration_frames': 3},
                   'sources': [], 'video_tracks': [
                       {'name': 'gfx', 'kind': 'gfx', 'clips': [self.clip]}],
                   'audio_tracks': [], 'subtitles': []}
        self.timeline = self.root / 'timeline.json'
        self.timeline.write_text(json.dumps(self.tl))
        self.c = SimpleNamespace(root=self.root, cache=self.root / 'cache',
                                 tl=self.tl, fps=30, w=8, h=6)

    def seed(self, directory=None, ns='selected-ns', marker=True):
        directory = directory or gfx_dir(self.c, self.clip)
        directory.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (directory / f'f{i:04d}.png').write_bytes(png_bytes(i))
        if marker:
            gfx_cli.complete_page(directory, 3, gfx_key(self.c, self.clip), 8, 6,
                                  verify_inputs=lambda: None, backend_ns=ns)
        return directory


@unittest.skipUnless(os.environ.get('VTX_TEST_BLENDER') == '1' and shutil.which('blender'),
                     'requires VTX_TEST_BLENDER=1 and blender')
class RealBlenderCache(CacheFixture):
    def export(self, expected=0):
        out = self.root / 'preview.blend'
        cmd = [shutil.which('blender'), '--background', '--factory-startup',
               '--python', str(REPO / 'apps/blender-bridge/json2blend.py'), '--',
               str(self.timeline), str(out)]
        result = subprocess.run(cmd, env=dict(os.environ), cwd=self.root,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return out

    def test_current_cef_namespace_exports_and_verifies(self):
        self.seed()
        out = self.export()
        result = subprocess.run([shutil.which('blender'), '--background', '--factory-startup',
            '--python', str(REPO / 'apps/blender-bridge/verify_blend.py'), '--',
            str(self.timeline), str(out)], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('失败 0 项', result.stdout)

    def test_other_namespace_cannot_satisfy_selected_namespace(self):
        d = gfx_dir(self.c, self.clip)
        self.seed(d.parent.parent / 'other-ns' / d.name, ns='other-ns')
        self.export(expected=1)

    def test_missing_complete_rejected(self):
        self.seed(marker=False)
        self.export(expected=1)

    def test_missing_frame_not_replaced_by_extra_png(self):
        d = self.seed()
        frame = d / 'f0001.png'
        self.assertEqual(frame.resolve().parent, d.resolve())
        frame.unlink()
        (d / 'f9999.png').write_bytes(png_bytes())
        self.export(expected=1)

    def test_other_worker_cannot_reuse_selected_namespace_cache(self):
        self.seed()
        self.worker.write_bytes(b'identity-only-worker-v2')
        self.export(expected=1)


if __name__ == '__main__':
    unittest.main()
