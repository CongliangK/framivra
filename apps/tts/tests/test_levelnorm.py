#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_levelnorm.py · 峰值归一 -6dBFS 单测（方案 §9.2）

ffmpeg 合成 KB 级 -20dBFS wav → 归一到 -6±0.5；幂等（二次运行增益=0）。
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audio_processing  # noqa: E402


def synth_wav(path, amp=0.1, dur=1.0, freq=440):
    """ffmpeg 合成精确振幅正弦 wav（KB 级；amp=0.1 → 峰值≈-20dBFS）。"""
    src = f"aevalsrc={amp}*sin(2*PI*{freq}*t):d={dur}:s=48000"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src,
                    "-ac", "1", "-c:a", "pcm_s16le", path], check=True)
    return path


class TestLevelNorm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vt_tts_norm_")
        self.wav = synth_wav(os.path.join(self.tmp, "w.wav"), amp=0.1)

    def test_peak_measure(self):
        p = audio_processing.peak_db(self.wav)
        self.assertAlmostEqual(p, -20.0, delta=0.5)

    def test_normalize_to_minus6(self):
        gain = audio_processing.normalize_file(self.wav)
        self.assertGreater(gain, 13.5)   # -20 → -6 约 +14dB
        self.assertLess(gain, 14.5)
        p2 = audio_processing.peak_db(self.wav)
        self.assertAlmostEqual(p2, audio_processing.TARGET_DB, delta=0.5)

    def test_idempotent(self):
        g1 = audio_processing.normalize_file(self.wav)
        self.assertGreater(g1, 0)
        g2 = audio_processing.normalize_file(self.wav)   # 已在 -6±0.5 → 不动
        self.assertEqual(g2, 0.0)
        self.assertAlmostEqual(audio_processing.peak_db(self.wav),
                               audio_processing.TARGET_DB, delta=0.5)

    def test_already_in_band_noop(self):
        """峰值已在 -6±0.5dB 带宽内 → 不动文件（mtime/内容不变）。"""
        wav2 = synth_wav(os.path.join(self.tmp, "w6.wav"), amp=0.501)  # ≈-6.0dB
        before = audio_processing.peak_db(wav2)
        self.assertEqual(audio_processing.normalize_file(wav2), 0.0)
        self.assertEqual(audio_processing.peak_db(wav2), before)


class TestAudioProcessingCLI(unittest.TestCase):
    def test_normalize_single_file_target_and_output(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        output = StringIO()
        with patch.object(audio_processing.os.path, 'isdir', return_value=False), \
             patch.object(audio_processing, 'normalize_file', return_value=6) as normalize, \
             patch.object(audio_processing, 'peak_db', return_value=-9), redirect_stdout(output):
            audio_processing.main(['normalize', 'input.wav', '--target', '-9'])
        normalize.assert_called_once_with('input.wav', -9)
        self.assertIn('peak -9 dB（增益 6）', output.getvalue())

    def test_normalize_directory_and_errors(self):
        from unittest.mock import patch
        with patch.object(audio_processing.os.path, 'isdir', return_value=True), \
             patch.object(audio_processing, 'normalize_dir') as normalize:
            audio_processing.main(['normalize', 'final', '--target', '-12'])
        normalize.assert_called_once_with('final', target_db=-12)
        with patch.object(audio_processing.os.path, 'isdir', return_value=False), \
             patch.object(audio_processing, 'normalize_file', side_effect=RuntimeError('failure')):
            with self.assertRaisesRegex(RuntimeError, 'failure'):
                audio_processing.main(['normalize', 'input.wav'])


if __name__ == "__main__":
    unittest.main()
