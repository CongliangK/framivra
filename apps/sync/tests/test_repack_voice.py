"""Real CPU voice replacement with Unicode timeline and redirected streams."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave


@unittest.skipUnless(shutil.which('ffmpeg') and importlib.util.find_spec('numpy'),
                     'voice repack requires FFmpeg and NumPy')
class RepackVoice(unittest.TestCase):
    def test_repack_selects_unicode_window_from_utf8_timeline(self):
        # UTF-8 stdout already works here; this case detects a misread window ID.
        self.assert_repacked('同期🎵', 'utf-8')

    def test_repack_writes_utf8_through_legacy_windows_pipe(self):
        # An ASCII ID isolates the output failure, even in a Unicode project path.
        self.assert_repacked('intro', 'cp936' if os.name == 'nt' else 'utf-8')

    def assert_repacked(self, window, pipe_encoding):
        script = Path(__file__).resolve().parents[1] / 'repack_voice.py'
        with tempfile.TemporaryDirectory(prefix='repack-voice-') as tmp:
            root = Path(tmp) / '录音 🎵'
            takes = root / 'assets/voice_takes'
            final = root / 'assets/voice_tts/final'
            takes.mkdir(parents=True)
            final.mkdir(parents=True)
            leaf = f'{window}.wav'
            samples = b'\xe8\x03' * 4800
            with wave.open(str(takes / leaf), 'wb') as wav:
                wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                wav.writeframes(samples)
            (final / leaf).write_bytes(b'placeholder')
            timeline = root / 'timeline.json'
            doc = {'project': {'fps': 30}, 'audio_tracks': [{'name': 'voice', 'events': [
                {'src': f'vt_{window}', 'in': 0, 'out': 6}]}]}
            original = json.dumps(doc, ensure_ascii=False).encode('utf-8')
            timeline.write_bytes(original)
            env = dict(os.environ, PYTHONUTF8='0' if os.name == 'nt' else '1',
                       PYTHONIOENCODING=f'{pipe_encoding}:strict')
            result = subprocess.run([sys.executable, '-B', script, '-t', timeline, window],
                                    cwd=root, env=env, capture_output=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stderr)
            stdout = result.stdout.decode('utf-8')
            self.assertIn('✓ 已备份占位', stdout)
            self.assertIn(f'✓ {window}:', stdout)
            self.assertIn('替换 1 窗完成', stdout)
            self.assertEqual(timeline.read_bytes(), original)
            self.assertEqual((final.with_name('final_tts_backup') / leaf).read_bytes(), b'placeholder')
            with wave.open(str(final / leaf), 'rb') as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 48000))
                self.assertEqual(wav.getnframes(), 10560)
                self.assertEqual(wav.readframes(10560), samples + b'\0\0' * 5760)
