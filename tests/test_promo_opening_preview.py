"""Preview-only contracts. No browser pixels, playback or Mac proof is claimed."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'templates/promo-demo/v1/preview'


def geometry(svg):
    """Normalize generated SVG primitives to the original 1280x720 screen."""
    root = ET.fromstring('<svg>' + svg + '</svg>')
    result = []
    def points(text):
        return [[float(v) for v in pair.split(',')] for pair in text.split()]
    def visit(node, transform=(1, 0, 0)):
        z, tx, ty = transform
        def point(p):
            return [p[0] * z + tx, p[1] * z + ty]
        if node.tag == 'g':
            m = [float(v) for v in node.attrib['transform'][7:-1].split()]
            assert m[1:3] == [0, 0] and m[0] == m[3]
            for child in node:
                visit(child, (m[0], m[4], m[5]))
        elif node.tag in ('polygon', 'polyline'):
            item = [node.tag, [point(p) for p in points(node.attrib['points'])],
                    node.attrib['fill' if node.tag == 'polygon' else 'stroke']]
            if node.tag == 'polyline':
                item.append(float(node.attrib['stroke-width']) * z)
            result.append(item)
        elif node.tag == 'circle':
            p = point([float(node.attrib['cx']), float(node.attrib['cy'])])
            result.append(['circle', p + [float(node.attrib['r']) * z], node.attrib['fill']])
        elif node.tag == 'path':
            assert node.attrib['data-part'] == 'waste'
            assert node.attrib['fill-rule'] == 'evenodd'
            contours = []
            for text in re.findall(r'M (.*?) Z', node.attrib['d']):
                contours.append([point(p) for p in points(text.replace(' L ', ' '))])
            result.append(['waste', contours])
        else:
            raise AssertionError('Unexpected SVG primitive: ' + node.tag)
    for child in root:
        visit(child)
    return result


def fixed(value):
    if isinstance(value, (float, int)):
        return f'{0 if abs(value) < .00005 else round(value, 8):.4f}'
    if isinstance(value, list):
        return [fixed(v) for v in value]
    return value


class PreviewContracts(unittest.TestCase):
    def test_timeline_real_track_accounting_and_source_hash(self):
        tl = json.loads((PACKAGE / 'timeline.json').read_text())
        self.assertEqual(tl['project'], {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 300})
        self.assertEqual(len(tl['sources']), 1)
        self.assertEqual([t['kind'] for t in tl['video_tracks']], ['broll', 'gfx'])
        self.assertEqual([len(t['clips']) for t in tl['video_tracks']], [1, 1])
        self.assertEqual(tl['audio_tracks'], [])
        self.assertEqual(tl['subtitles'], [])
        for t in tl['video_tracks']:
            self.assertEqual(t['clips'][0]['dur'], 300)
            self.assertEqual(t['clips'][0]['dst_in'], 0)
        recipe = json.loads((PACKAGE / 'paper-recipe.json').read_text())
        self.assertEqual(hashlib.sha256((PACKAGE / tl['sources'][0]['path']).read_bytes()).hexdigest(), recipe['sha256'])

    def test_metadata_matches_assets_and_never_claims_rendered(self):
        meta = json.loads((PACKAGE / 'preview.json').read_text())
        for name, digest in meta['assets'].items():
            self.assertEqual(hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(meta['actual_timeline_tracks'], {
            'video': 2, 'gfx': 1, 'background_video': 1, 'audio': 0, 'subtitles': 0,
            'placeholder_tracks': 0})
        self.assertEqual(meta['expected_output']['duration_frames'], 300)
        for name in ('html_browser_render', 'cdp_pixel_validation', 'framivra_video_render',
                     'macos_runtime', 'real_time_playback'):
            self.assertEqual(meta['validation_limits'][name], 'UNRUN')

    def test_schema_without_changing_existing_contract(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest('Optional jsonschema unavailable; engine validation has a separate test')
        jsonschema.validate(json.loads((PACKAGE / 'timeline.json').read_text()),
                            json.loads((ROOT / 'vtx/timeline.schema.json').read_text()))

    def test_vtx_validate_real_media_offline_no_browser(self):
        if not shutil.which('ffprobe'):
            self.skipTest('Existing ffprobe is required for real source validation')
        # Prevent renderer discovery/probing in this media-only unit check.
        from tests.gfx_environment_fixture import bind_environment
        bind_environment(self)
        from vtx.infra import Ctx
        from vtx.stages import validate
        # Copy only requested inputs to isolate even receipts from versioned assets.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            for name in ('timeline.json', 'opening.html', 'paper-background.mp4'):
                shutil.copyfile(PACKAGE / name, p / name)
            c = Ctx(p / 'timeline.json')
            c.dry = True
            validate(c)
            self.assertFalse((p / 'out').exists())

    def test_page_has_no_external_or_wallclock_resources(self):
        html = (PACKAGE / 'opening.html').read_text()
        class Scanner(HTMLParser):
            def handle_starttag(self, tag, attrs):
                for key, value in attrs:
                    if key in {'src', 'href', 'xlink:href', 'srcset'} or key.startswith('on'):
                        raise AssertionError((tag, key, value))
        Scanner().feed(html)
        for banned in (r'\brequestAnimationFrame\b', r'\bsetTimeout\b', r'\bsetInterval\b',
                       r'\bDate\b', r'\bperformance\b', r'Math\.random', r'\bfetch\s*\(',
                       r'\bXMLHttpRequest\b', r'\bWebSocket\b', r'\bimport\s*\(',
                       r'animation\s*:', r'transition\s*:', r'@import', r'url\s*\(',
                       r'<(?:image|foreignObject|canvas|iframe|animate)\b'):
            self.assertNotRegex(html, banned)
        self.assertIn('viewBox="0 0 1280 720"', html)
        self.assertIn('width="1920" height="1080"', html)
        self.assertNotIn('<text', html)
        self.assertNotIn('__vtx_static_spans', html)

    def test_paper_streams_and_silent_samples(self):
        if not shutil.which('ffprobe') or not shutil.which('ffmpeg'):
            self.skipTest('Existing FFmpeg/ffprobe required for actual media checks')
        video = PACKAGE / 'paper-background.mp4'
        report = json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames',
            '-show_streams','-of','json',str(video)]))
        self.assertEqual(len(report['streams']), 2)
        v, a = report['streams']
        self.assertEqual(v['codec_type'], 'video')
        self.assertEqual((v['width'], v['height'], v['r_frame_rate'], v['nb_read_frames']),
                         (1920, 1080, '30/1', '300'))
        self.assertEqual(float(v['duration']), 10)
        self.assertEqual(a['codec_type'], 'audio')
        audio = subprocess.check_output(['ffmpeg','-v','error','-i',str(video),
            '-map','0:a:0','-f','s16le','-acodec','pcm_s16le','pipe:1'])
        self.assertGreaterEqual(len(audio), 48000 * 10 * 4)
        self.assertFalse(any(audio))
        for frame in (0, 150, 299):
            pixel = subprocess.check_output(['ffmpeg','-v','error','-i',str(video),
                '-vf',f'select=eq(n\\,{frame}),crop=1:1:0:0','-frames:v','1',
                '-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
            self.assertEqual(pixel, bytes.fromhex('F1EDE1'))


@unittest.skipUnless(shutil.which('node'), 'Existing Node required; install is not part of this preview')
class PreviewJavaScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = json.loads(subprocess.check_output(['node', str(ROOT / 'tests/promo_preview_vm.js'),
                            str(PACKAGE / 'opening.html')], timeout=60))
        cls.frames = cls.result['frames']

    def test_all_frames_match_intercepted_original_v03_geometry(self):
        oracle = json.loads((ROOT / 'tests/fixtures/promo_opening_v03_geometry.json').read_text())
        self.assertEqual(oracle['reference_source_sha256'], '92deb5ed8bc5be070c971bf7a8c52120f9ad8350383dbcf6974d9a232b60ad41')
        self.assertEqual(len(self.frames), 300)
        for frame, expected in zip(self.frames, oracle['frames']):
            with self.subTest(frame=expected['frame']):
                actual = geometry(frame['svg'])
                self.assertEqual(len(actual), expected['primitive_count'])
                canonical = json.dumps(fixed(actual), separators=(',', ':'))
                self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(), expected['geometry_sha256_4dp'])
                self.assertAlmostEqual(frame['state']['hx'], expected['state']['hx'], places=9)
                for key in ('x','y','z'):
                    self.assertAlmostEqual(frame['state']['camera'][key], expected['state']['camera'][key], places=9)

    def test_cue_mirror_and_contacts(self):
        cues = json.loads((PACKAGE / 'cues.json').read_text())
        self.assertEqual(self.result['embeddedCues'], cues)
        for stage, f in enumerate(cues['contacts'].values(), 1):
            self.assertEqual(self.frames[f-1]['state']['stage'], stage-1)
            self.assertEqual(self.frames[f]['state']['stage'], stage)
        self.assertEqual(self.frames[18]['state']['presses']['cut'], 332)
        self.assertEqual(self.frames[67]['state']['presses']['body'], 399)
        self.assertEqual(self.frames[109]['state']['presses']['trim'], 1)
        self.assertEqual(self.frames[150]['state']['presses']['eye'], 397)

    def test_causal_waste_and_transition_boundaries(self):
        states = [f['state'] for f in self.frames]
        self.assertFalse(states[17]['waste']['visible'])
        self.assertTrue(states[18]['waste']['visible'])
        self.assertEqual(states[28]['waste']['x'], 350)
        self.assertEqual(states[28]['waste']['y'], 440)
        self.assertGreater(states[29]['waste']['y'], 440)
        self.assertTrue(states[54]['waste']['visible'])
        self.assertFalse(states[55]['waste']['visible'])
        self.assertFalse(states[108]['offcuts']['visible'])
        self.assertTrue(states[109]['offcuts']['visible'])
        self.assertTrue(states[138]['offcuts']['visible'])
        self.assertFalse(states[139]['offcuts']['visible'])
        self.assertFalse(states[263]['approach'])
        self.assertTrue(states[264]['approach'])
        before, after = states[263], states[264]
        camera, b = before['camera'], before['bird']
        self.assertAlmostEqual(640+(b['x']-camera['x'])*camera['z'], after['bird']['x'])
        # V03 itself rounds the approach start from 272.25 to 272, preserved here.
        self.assertAlmostEqual(360+(b['y']-camera['y'])*camera['z']-after['bird']['y'], .25)
        self.assertEqual(after['bird']['scale'], .65)
        self.assertEqual(states[299]['bird']['scale'], 35)

    def test_geometry_oracle_detects_changed_primitive(self):
        expected = self.frames[176]['svg']
        changed = expected.replace('fill="#D9533B"', 'fill="#D9533C"', 1)
        self.assertNotEqual(fixed(geometry(changed)), fixed(geometry(expected)))


if __name__ == '__main__':
    unittest.main()
