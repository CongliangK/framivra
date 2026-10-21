#!/usr/bin/env python3
'Audio processing contracts: tempo ratios, peak normalization and CLI.'


# atempo contracts
# test_atempo.py · atempo 参数 golden 单测（方向坑钉死，方案 §9.2 / R8）
#
# golden 值来源：voice-ime manifest.json 实测（A1: raw 9.14s / budget 7.62s）
# 与 refit_final.py 修正版公式。
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audio_processing  # noqa: E402


class TestAtempoChain(unittest.TestCase):
    def test_direction_golden(self):
        """raw=9.14 / budget=7.62 → atempo=1.199475（=raw/budget，方向钉死）。"""
        self.assertEqual(audio_processing.atempo_chain(9.14, 7.62), "atempo=1.199475")

    def test_out_of_range_high_4x(self):
        """raw/budget=4 → 双级 √4=2.0：atempo=2.000000,atempo=2.000000。"""
        self.assertEqual(audio_processing.atempo_chain(4.0, 1.0),
                         "atempo=2.000000,atempo=2.000000")

    def test_out_of_range_low_quarter(self):
        """raw/budget=0.25 → 双级 √0.25=0.5。"""
        self.assertEqual(audio_processing.atempo_chain(1.0, 4.0),
                         "atempo=0.500000,atempo=0.500000")

    def test_equal_no_filter(self):
        """budget=raw（|r-1|<1e-9）→ 空串，不加滤镜（口径见 audio_processing.py docstring）。"""
        self.assertEqual(audio_processing.atempo_chain(7.62, 7.62), "")
        self.assertEqual(audio_processing.atempo_chain(2.0, 2.0 + 1e-12), "")

    def test_domain_boundaries_single_level(self):
        """r=0.5 / 2.0 恰在单级域内（[0.5, 2.0] 闭区间）。"""
        self.assertEqual(audio_processing.atempo_chain(1.0, 2.0), "atempo=0.500000")
        self.assertEqual(audio_processing.atempo_chain(2.0, 1.0), "atempo=2.000000")

    def test_extreme_9x_recurse(self):
        """更极端依次再拆：r=9 → 9^(1/4)=1.732051 四级同值串联。"""
        seg = f"atempo={9 ** 0.25:.6f}"
        self.assertEqual(audio_processing.atempo_chain(9.0, 1.0),
                         ",".join([seg] * 4))

    def test_chain_math(self):
        """链条语义：输出时长 = raw / r，多级乘积 = r。"""
        for raw, budget in [(9.14, 7.62), (4.0, 1.0), (1.0, 4.0), (9.0, 1.0)]:
            chain = audio_processing.atempo_chain(raw, budget)
            speed = 1.0
            for part in chain.split(","):
                speed *= float(part.split("=")[1])
            self.assertAlmostEqual(speed, raw / budget, places=5)

    def test_invalid(self):
        for invalid in (0.0, -1.0, float('nan'), float('inf'), -float('inf')):
            for raw, budget in ((invalid, 1.0), (1.0, invalid)):
                with self.subTest(raw=raw, budget=budget):
                    with self.assertRaises(ValueError):
                        audio_processing.atempo_chain(raw, budget)
        for raw, budget in ((1e308, 1e-308), (1e-308, 1e308)):
            with self.subTest(raw=raw, budget=budget):
                with self.assertRaises(ValueError):
                    audio_processing.atempo_chain(raw, budget)

    def test_finite_extremes_still_produce_bounded_factors(self):
        for raw, budget in ((1e308, 1.0), (5e-324, 1.0), (5e-324, 5e-324)):
            with self.subTest(raw=raw, budget=budget):
                chain = audio_processing.atempo_chain(raw, budget)
                if raw == budget:
                    self.assertEqual(chain, '')
                else:
                    factors = [float(part.split('=')[1]) for part in chain.split(',')]
                    self.assertTrue(factors)
                    self.assertLessEqual(len(factors), 1075)
                    self.assertTrue(all(0.5 <= factor <= 2.0 for factor in factors))

    def test_canonical_cli_tempo_from_another_directory(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, audio_processing.__file__, 'tempo', '9.14', '7.62'],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('atempo=1.199475', result.stdout)
            self.assertEqual(os.listdir(directory), [])


# levelnorm contracts
# test_levelnorm.py · 峰值归一 -6dBFS 单测（方案 §9.2）
#
# ffmpeg 合成 KB 级 -20dBFS wav → 归一到 -6±0.5；幂等（二次运行增益=0）。
import subprocess
import tempfile


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
