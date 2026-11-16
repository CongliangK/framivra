"""Opt-in actual HTML layer preview; a mechanism fixture, not the 900-frame baseline."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('VTX_TEST_CHROME'), 'set VTX_TEST_CHROME for real CDP preview')
class HtmlPreviewIntegration(unittest.TestCase):
    def setUp(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow required for decoded pixel assertions')
        self.Image = Image
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.page = self.root / '图层 # demo.html'
        self.timeline = self.root / 'timeline.json'
        self.timeline.write_text(json.dumps({
            'version': 2,
            'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 660},
            'sources': [],
            'video_tracks': [{'kind': 'gfx', 'clips': [
                {'src': self.page.name, 'dst_in': 30, 'dur': 600}]}],
            'audio_tracks': [], 'subtitles': []}), encoding='utf-8')
        self.write_page()

    def write_page(self, invalid_export=False):
        self.page.write_text('''<!doctype html><meta charset="utf-8">
<style>html,body{margin:0;background:transparent;overflow:hidden}</style>
<canvas width="1920" height="1080"></canvas><script>
const f=Number(new URLSearchParams(location.search).get('f')||0);
const canvas=document.querySelector('canvas'),c=canvas.getContext('2d');
c.fillStyle=`rgba(${f%256},80,180,0.5)`;c.fillRect(100,100,300,300);
window.__vtx_export_png=()=>EXPORT;
</script>'''.replace('EXPORT', "'invalid'" if invalid_export else "canvas.toDataURL('image/png')"), encoding='utf-8')

    def render(self, frame):
        import hashlib
        version = 'sha256:' + hashlib.sha256(self.timeline.read_bytes()).hexdigest()
        proc = subprocess.run([
            sys.executable, '-m', 'vtx', '-t', str(self.timeline), 'timeline', 'frame',
            '--frame', str(frame), '--html-layer', '/video_tracks/0/clips/0',
            '--expected-content-version', version, '--render', '--trust-html', '--include-png'],
            cwd=str(Path(__file__).resolve().parents[2]), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=30,
            env={**os.environ, 'VTX_GFX_CHROME': os.environ['VTX_TEST_CHROME']})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout[:1000])
        result = json.loads(proc.stdout)
        self.assertTrue(result['ok'], result['diagnostics'])
        image = result['image']
        self.assertEqual(image['kind'], 'html-layer')
        self.assertEqual(image['layer']['local_frame'], frame - 30)
        self.assertEqual(image['layer']['duration_frames'], 600)
        self.assertFalse(image['cache_hit'])
        data = base64.b64decode(image['inline']['data'], validate=True)
        self.assertEqual(hashlib.sha256(data).hexdigest(), image['sha256'])
        self.assertEqual(len(data), image['bytes'])
        png = self.Image.open(io.BytesIO(data)).convert('RGBA')
        self.assertEqual(png.size, (1920, 1080))
        self.assertEqual(png.getpixel((0, 0))[3], 0)
        self.assertFalse((self.root / 'cache' / 'gfx').exists())
        self.assertFalse((self.root / 'out').exists())
        self.assertFalse(list((self.root / 'cache' / 'preview').rglob('*worker*.json')))
        self.assertFalse(list((self.root / 'cache' / 'preview').rglob('profile')))
        return result, png.getpixel((150, 150))

    def test_first_middle_last_and_repeated_delivery(self):
        fingerprints = []
        for frame in (30, 335, 629, 335):
            result, pixel = self.render(frame)
            self.assertLessEqual(abs(pixel[0] - (frame - 30) % 256), 1)
            self.assertLessEqual(abs(pixel[3] - 128), 1)
            fingerprints.append(result['image']['sha256'])
        self.assertNotEqual(fingerprints[0], fingerprints[1])
        self.assertNotEqual(fingerprints[1], fingerprints[2])
        self.assertEqual(fingerprints[1], fingerprints[3])

    def test_invalid_canvas_export_falls_back_with_alpha(self):
        self.write_page(invalid_export=True)
        _, pixel = self.render(335)
        self.assertLessEqual(abs(pixel[0] - 49), 2)
        self.assertLessEqual(abs(pixel[3] - 128), 1)


if __name__ == '__main__':
    unittest.main()
