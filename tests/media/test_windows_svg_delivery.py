"""Opt-in end-to-end Windows delivery using the pinned runtimes and a project font."""
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from vtx.render import gfx_svg


@unittest.skipUnless(sys.platform == 'win32' and all(os.environ.get(key) for key in (
    'VTX_WINDOWS_SVG_PORTABLE', 'VTX_WINDOWS_SVG_SCRATCH', 'VTX_WINDOWS_SVG_MEDIA_BIN',
    'VTX_TEST_WINDOWS_SVG_FONT')), 'requires the pinned Windows runtimes and an explicit JetBrains Mono Regular TTF')
class WindowsSvgDelivery(unittest.TestCase):
    def test_mixed_delivery_with_local_font_duck_static_and_warm_raster(self):
        with tempfile.TemporaryDirectory(prefix='vtx-svg-delivery-') as workspace:
            root = Path(workspace).resolve()
            self._deliver(root)

    def _deliver(self, root):
        def write_json(name, value):
            (root / name).write_text(json.dumps(value), encoding='utf-8')

        tools = Path(os.environ['VTX_WINDOWS_SVG_MEDIA_BIN'])
        ffmpeg = str(tools / 'ffmpeg.exe')
        env = {**os.environ, 'VTX_VIDEO_SEGMENTS': '2', 'VTX_CACHE_MODE': 'core'}

        def run(*argv):
            result = subprocess.run(argv, env=env, capture_output=True, encoding='utf-8',
                                    errors='replace', timeout=360)
            self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
            return result

        run(ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=30',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '4', '-c:v',
            'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(root / 'base.mp4'))
        shutil.copyfile(os.environ['VTX_TEST_WINDOWS_SVG_FONT'], root / 'project.ttf')
        for name, count, contents, fonts in (
            ('red', 46, '<rect width="320" height="180" fill="#f02020"/>', []),
            ('green', 44, '<rect width="320" height="180" fill="#20f020"/>', []),
            ('title', 60, '<rect x="40" y="30" width="240" height="130" fill="#f0e020"/>'
                          '<text x="70" y="100" font-family="JetBrains Mono" font-size="24"'
                          ' fill="black">Native SVG</text>', ['project.ttf']),
        ):
            (root / f'{name}.svg').write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">'
                + contents + '</svg>', encoding='utf-8')
            write_json(f'{name}.json', dict(format=gfx_svg.FORMAT, fps=30, width=320, height=180,
                                           frame_count=count, frames=[f'{name}.svg'] * count, fonts=fonts))

        def clip(name, start, count):
            return dict(format='svg-sequence', src=f'{name}.json', dst_in=start, dur=count)

        write_json('timeline.json', dict(version=2, project=dict(fps=30, width=320, height=180, duration_frames=120),
            sources=[dict(id='base', path='base.mp4')], video_tracks=[
                dict(name='video', kind='aroll', clips=[dict(src='base', src_in=0, src_out=120, dst_in=0, dur=120)]),
                dict(name='graphics', kind='gfx', clips=[clip('red', 4, 46), clip('green', 70, 44)]),
                dict(name='text', kind='gfx', clips=[clip('title', 22, 60)]),
            ], subtitles=[], audio_tracks=[dict(name='voice', events=[]),
                dict(name='music', duck=False, duck_strategy={'mode': 'static', 'global_db': -6}, events=[
                    {'src': 'base', 'src_in': 0, 'in': 0, 'out': 120, 'volume': 1.0}])]))
        baseline = run(ffmpeg, '-hide_banner', '-nostats', '-i', str(root / 'base.mp4'),
                       '-af', 'aformat=channel_layouts=stereo,ebur128=framelog=quiet', '-vn', '-f', 'null', '-')
        raw_i = float(re.findall(r'I:\s+(-?[\d.]+) LUFS', baseline.stderr)[-1])
        cli = (sys.executable, '-X', 'utf8', '-m', 'vtx', '-t', str(root / 'timeline.json'))
        receipt = root / 'out' / 'receipt.json'
        run(*cli, 'all')
        first = json.loads(receipt.read_bytes())
        self.assertTrue(all(first['stages'][stage]['ok'] for stage in ('validate', 'gfx', 'video', 'audio', 'mux', 'qc')))
        self.assertAlmostEqual(first['stages']['audio']['raw_i'], raw_i - 6, delta=0.2)
        self.assertIn('0 cache hits', first['stages']['gfx']['detail'])
        run(*cli, 'all')
        warm = json.loads(receipt.read_bytes())
        self.assertEqual(first['input_fingerprint'], warm['input_fingerprint'])
        self.assertIn('3 cache hits', warm['stages']['gfx']['detail'])
        run(*cli, 'qc', '--strict')
        final = json.loads(receipt.read_bytes())
        self.assertTrue(final['stages']['qc']['ok'])
        media = root / 'out' / 'final.mp4'
        self.assertEqual(final['stages']['qc']['media_sha256']['final.mp4'], hashlib.sha256(media.read_bytes()).hexdigest())
        self.assertFalse((root / 'cache' / 'qc_clean.mp4').exists(), 'opaque bottom uses direct reference')
