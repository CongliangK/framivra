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
            'color=c=blue:s=1920x1080:r=30:d=0.5333333333333333', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=0.5333333333333333',
            '-frames:v', '16', '-shortest', '-c:v', 'ffv1', '-c:a', 'pcm_s16le',
            '-ar', '48000', '-ac', '2', '-threads', '1', str(self.root / 'source.mkv')], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.doc = {'version': 2,
            'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 16},
            'sources': [{'id': 'base', 'path': 'source.mkv'}],
            'video_tracks': [
                {'name': 'base', 'kind': 'aroll', 'clips': [{'src': 'base', 'src_in': 0, 'src_out': 16,
                                            'dst_in': 0, 'dur': 16}]},
                {'name': 'page', 'kind': 'gfx', 'clips': [{'src': self.page.name, 'dst_in': 2, 'dur': 12,
                                          'x': 100, 'y': 80, 'opacity': 0.5}]}],
            'audio_tracks': [{'name': 'tone', 'events': [{'src': 'base', 'in': 0, 'out': 16, 'volume': 0.3}]}],
            'subtitles': [{'in': 7, 'out': 8, 'text': 'COMPLETE COMPOSITE'}]}
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
        result = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 0, {'diagnostics': result.get('diagnostics'), 'stderr': proc.stderr[-2000:]})
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

    def test_same_input_formal_all_audio_qc_and_selected_frame(self):
        # This is a separate explicitly written formal copy, not a warm-cache
        # replacement for the initial empty-cache preview operation.
        original = self.timeline.read_bytes()
        _, preview = self.render(7)
        self.assertEqual(self.timeline.read_bytes(), original)
        formal = self.root / 'formal'
        formal.mkdir()
        for source in (self.timeline, self.page, self.root / 'source.mkv'):
            shutil.copy2(source, formal / source.name)
        env = {**os.environ, 'VTX_CHROME': os.environ['VTX_TEST_CHROME'], 'VTX_GFX': 'cdp',
               'VTX_NO_NVENC': '1', 'VTX_SEG_NVENC': '0', 'VTX_FILTER_THREADS': '1'}
        for operation in (['all'], ['qc', '--strict']):
            proc = subprocess.run([sys.executable, '-m', 'vtx', '-t', str(formal / 'timeline.json'), *operation],
                cwd=str(Path(__file__).resolve().parents[2]), capture_output=True, text=True,
                timeout=180, env=env)
            self.assertEqual(proc.returncode, 0, proc.stdout[-5000:] + proc.stderr[-3000:])
        # Core policy intentionally retires the intermediate audio.wav after a
        # successful all. Verify the actual delivered audio, not an obsolete
        # assumption that temporary stage handoffs survive that operation.
        self.assertTrue((formal / 'out' / 'final.mp4').is_file())
        audio = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(formal / 'out' / 'final.mp4'),
            '-map', '0:a:0', '-ar', '48000', '-ac', '2', '-f', 's16le', '-'],
            capture_output=True, timeout=30)
        self.assertEqual(audio.returncode, 0, audio.stderr)
        self.assertLessEqual(abs(len(audio.stdout) // 4 - 16 * 1600), 1024)
        self.assertTrue(any(audio.stdout), 'delivered audio must contain the synthesized tone')
        self.assertEqual((formal / 'timeline.json').read_bytes(), original)
        decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(formal / 'out' / 'final.mp4'),
            '-vf', 'select=eq(n\\,7)', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-threads', '1', '-'], capture_output=True, timeout=30)
        self.assertEqual(decoded.returncode, 0, decoded.stderr)
        self.assertEqual(len(decoded.stdout), 1920 * 1080 * 3)
        encoded = self.Image.frombytes('RGB', (1920, 1080), decoded.stdout)
        from PIL import ImageChops
        # Existing full-resolution mean-absolute threshold from verify_e2e.py;
        # additionally check graphic and ASS regions so a large flat background
        # cannot conceal a missing/wrong layer. Encoded bytes need not be equal.
        for box in (None, (200, 180, 500, 480), (0, 800, 1920, 1080)):
            a = preview if box is None else preview.crop(box)
            b = encoded if box is None else encoded.crop(box)
            values = ImageChops.difference(a, b).tobytes()
            self.assertLess(sum(values) / len(values), 2, box)


if __name__ == '__main__':
    unittest.main()
