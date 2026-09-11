"""Real FFmpeg regression: python tests/test_post_loudness.py --ffmpeg PATH --output DIR."""
import argparse, hashlib, importlib.util, json, math, os, shutil, struct, sys, tempfile, unittest, wave
from pathlib import Path
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('post_loudness', Path(__file__).resolve().parents[1] / 'scripts/post_loudness.py')
post = importlib.util.module_from_spec(spec)
spec.loader.exec_module(post)
FFMPEG = os.environ.get('VTX_TEST_FFMPEG') or shutil.which('ffmpeg')
OUTPUT = None

class Regression(unittest.TestCase):
    def setUp(self):
        if not FFMPEG or not shutil.which(str(FFMPEG)):
            self.skipTest('Set VTX_TEST_FFMPEG or install ffmpeg on PATH')
        self._temporary = None
        if OUTPUT is None:
            self._temporary = tempfile.TemporaryDirectory(prefix='vtx-post-loudness-')
            self.addCleanup(self._temporary.cleanup)
            self.project = Path(self._temporary.name)
        else:
            self.project = OUTPUT / self._testMethodName
        self.out = self.project / 'out'
        self.out.mkdir(parents=True, exist_ok=True)
        self.audio = self.out / 'audio.wav'
        with wave.open(str(self.audio), 'wb') as stream:
            stream.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b''.join(struct.pack('<h', round(9500 * math.sin(2 * math.pi * 440 * i / 48000))) for i in range(48000 * 4)))
        self.receipt = self.out / 'receipt.json'
        self.receipt.write_text(json.dumps({'input_fingerprint': 'fixture', 'stages': {'audio': {'ok': True, 'raw_i': -35, 'gain_db': 21, 'target': -14}, 'video': {'ok': True}, 'mux': {'ok': True}, 'mux_draft': {'ok': True}, 'qc': {'ok': True}}}), encoding='utf-8')

    def test_actual_measurement_and_repeat(self):
        actual = post.measure(FFMPEG, self.audio)
        old = self.out / 'old-repeated-gain.wav'
        post.run([FFMPEG, '-y', '-v', 'error', '-i', self.audio, '-af', 'volume=21dB,alimiter=limit=0.599:level=false', '-c:a', 'pcm_s16le', old])
        old_i = post.measure(FFMPEG, old)
        self.assertGreater(abs(old_i + 14), 1.5)
        first = post.process(self.project, FFMPEG)
        second = post.process(self.project, FFMPEG)
        self.assertEqual(first['input_i'], actual)
        self.assertLess(abs(first['output_i'] + 14), 0.2)
        self.assertLess(abs(second['output_i'] - first['output_i']), 0.2)
        self.assertLess(abs(second['gain_db']), 0.2)
        self.assertEqual(post.duration(self.audio), 4)
        record = json.loads(self.receipt.read_text())
        self.assertTrue(record['stages']['audio']['ok'])
        self.assertEqual(record['stages']['audio']['post_loudness']['sha256'], hashlib.sha256(self.audio.read_bytes()).hexdigest())
        self.assertEqual(set(record['stages']), {'audio', 'video'})
        (self.project / 'measurements.json').write_text(json.dumps({'actual_input_i': actual, 'raw_i_in_receipt': -35, 'legacy_result_i': old_i, 'first': first, 'second': second}, indent=2), encoding='utf-8')

    def test_failure_preserves_original(self):
        before = self.audio.read_bytes(), self.receipt.read_bytes()
        with patch.object(post, 'measure', side_effect=[-14, -30]):
            with self.assertRaisesRegex(ValueError, 'outside target'):
                post.process(self.project, FFMPEG)
        self.assertEqual(before, (self.audio.read_bytes(), self.receipt.read_bytes()))
        self.assertFalse(list(self.out.glob('.post-*')))

    def test_ffmpeg_failure_preserves_original(self):
        before = self.audio.read_bytes(), self.receipt.read_bytes()
        with patch.object(post, 'measure', return_value=-14), patch.object(post, 'run', side_effect=RuntimeError('encoder failure')):
            with self.assertRaises(RuntimeError):
                post.process(self.project, FFMPEG)
        self.assertEqual(before, (self.audio.read_bytes(), self.receipt.read_bytes()))
        self.assertFalse(list(self.out.glob('.post-*')))

    def test_duration_failure_preserves_original(self):
        before = self.audio.read_bytes(), self.receipt.read_bytes()
        with patch.object(post, 'duration', side_effect=[4, 5]):
            with self.assertRaisesRegex(ValueError, 'duration'):
                post.process(self.project, FFMPEG)
        self.assertEqual(before, (self.audio.read_bytes(), self.receipt.read_bytes()))

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--ffmpeg', default=FFMPEG)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    FFMPEG, OUTPUT = args.ffmpeg, args.output.resolve() if args.output else None
    unittest.main(argv=[sys.argv[0]], verbosity=2)

