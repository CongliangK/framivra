"""Opt-in real complete HTML/media/ASS composite, not the 900-frame baseline."""
import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('VTX_TEST_CHROME') and shutil.which('ffmpeg'),
                     'set VTX_TEST_CHROME for real complete CDP composite')
class HtmlPreviewIntegration(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.Image = Image
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.page = self.root / '图层 # demo.html'
        self.timeline = self.root / 'timeline.json'
        process = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
            'color=c=blue:s=1920x1080:r=30', '-frames:v', '16', '-c:v', 'ffv1',
            '-threads', '1', str(self.root / 'source.mkv')], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.doc = {'version': 2,
            'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 16},
            'sources': [{'id': 'base', 'path': 'source.mkv'}],
            'video_tracks': [
                {'name': 'base', 'kind': 'aroll', 'clips': [{'src': 'base', 'src_in': 0, 'src_out': 16,
                                            'dst_in': 0, 'dur': 16}]},
                {'name': 'page', 'kind': 'gfx', 'clips': [{'src': self.page.name, 'dst_in': 2, 'dur': 12,
                                          'x': 100, 'y': 80, 'opacity': 0.5}]}],
            'audio_tracks': [], 'subtitles': [{'in': 7, 'out': 8, 'text': 'COMPLETE COMPOSITE'}]}
        self.timeline.write_text(json.dumps(self.doc), encoding='utf-8')
        self.write_page()

    def write_page(self, invalid_export=False):
        self.page.write_text('''<!doctype html><meta charset="utf-8">
<style>html,body{margin:0;background:transparent;overflow:hidden}</style>
<canvas width="1920" height="1080"></canvas><script>
const f=Number(new URLSearchParams(location.search).get('f')||0);
const canvas=document.querySelector('canvas'),c=canvas.getContext('2d');
c.fillStyle=`rgba(${(f*17)%256},80,180,0.5)`;c.fillRect(100,100,300,300);
window.__vtx_export_png=()=>EXPORT;
</script>'''.replace('EXPORT', "'invalid'" if invalid_export else "canvas.toDataURL('image/png')"), encoding='utf-8')

    def render(self, frame):
        import hashlib
        version = 'sha256:' + hashlib.sha256(self.timeline.read_bytes()).hexdigest()
        proc = subprocess.run([sys.executable, '-m', 'vtx', '-t', str(self.timeline),
            'timeline', 'frame', '--frame', str(frame), '--prepare-gfx',
            '--expected-content-version', version, '--render', '--trust-html', '--include-png'],
            cwd=str(Path(__file__).resolve().parents[2]), capture_output=True, text=True,
            timeout=90, env={**os.environ, 'VTX_CHROME': os.environ['VTX_TEST_CHROME'],
                             'VTX_NO_NVENC': '1', 'VTX_SEG_NVENC': '0', 'VTX_FILTER_THREADS': '1'})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout[:2000])
        result = json.loads(proc.stdout)
        self.assertTrue(result['ok'], result['diagnostics'])
        image = result['image']
        self.assertEqual(image['kind'], 'current-composite')
        self.assertIsNone(image['path'])
        self.assertEqual(image['delivery'], 'inline-only')
        self.assertNotIn('layer', image)
        self.assertFalse(image['cache_hit'])
        data = base64.b64decode(image['inline']['data'], validate=True)
        self.assertEqual(hashlib.sha256(data).hexdigest(), image['sha256'])
        png = self.Image.open(io.BytesIO(data)).convert('RGB')
        self.assertEqual(png.size, (1920, 1080))
        self.assertGreater(png.getpixel((20, 20))[2], 240)  # native media, not page pixels
        self.assertFalse((self.root / 'out').exists())
        self.assertEqual(list((self.root / 'cache').iterdir()), [self.root / 'cache' / 'preview'])
        self.assertEqual(list((self.root / 'cache' / 'preview').iterdir()), [])
        return result, png

    def test_local_first_middle_last_and_fresh_repeated_complete_delivery(self):
        hashes = []
        for frame in (2, 8, 13, 8):
            result, png = self.render(frame)
            # Original page rectangle is translated; native background survives.
            self.assertGreater(png.getpixel((150, 150))[2], 240)
            pixel = png.getpixel((250, 230))
            self.assertTrue(225 <= pixel[2] <= 242, pixel)
            self.assertTrue(15 <= pixel[1] <= 25, pixel)
            self.assertLessEqual(abs(pixel[0] - (((frame - 2) * 17) % 256) * 0.25), 3, pixel)
            hashes.append(result['image']['sha256'])
        self.assertNotEqual(hashes[0], hashes[1])
        self.assertNotEqual(hashes[1], hashes[2])
        self.assertEqual(hashes[1], hashes[3])
        _, with_ass = self.render(7)
        _, without_ass = self.render(8)
        self.assertNotEqual(with_ass.tobytes(), without_ass.tobytes())
        # ASS is bottom-centered, far outside the page rectangle.
        self.assertNotEqual(with_ass.crop((0, 800, 1920, 1080)).tobytes(),
                            without_ass.crop((0, 800, 1920, 1080)).tobytes())

    def test_invalid_canvas_export_retains_complete_composite(self):
        self.write_page(invalid_export=True)
        _, png = self.render(8)
        self.assertGreater(png.getpixel((20, 20))[2], 240)
        self.assertLess(png.getpixel((250, 230))[2], 245)


if __name__ == '__main__':
    unittest.main()
