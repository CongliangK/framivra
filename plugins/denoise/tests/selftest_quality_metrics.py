#!/usr/bin/env python3
"""tests/test_quality_metrics.py —— quality.quality(指标)单元测试。

覆盖:测量域唯一入口(16k mono,soxr 单一实现)/恒等退化封顶/线性延迟不变性/
噪声退化方向性/漂移嫌疑检出/48k 原生域带宽收缩/真实轨噪声底口径。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from plugins.denoise.quality import MEASURE_FS, quality as Q  # noqa: E402


def speech_like(fs=MEASURE_FS, dur=2.0):
    t = np.arange(int(fs * dur)) / fs
    env = 0.5 * (1 + np.sign(np.sin(2 * np.pi * 3 * t))) * np.minimum(1, 4 * t) * np.minimum(1, 4 * (dur - t))
    return env * (np.sin(2 * np.pi * 1000 * t) + 0.3 * np.sin(2 * np.pi * 2500 * t))


class TestMeasureDomain(unittest.TestCase):
    def test_downmix_then_resample_order(self):
        fs = 48000
        t = np.arange(fs) / fs
        stereo = np.stack([np.sin(2 * np.pi * 440 * t), -np.sin(2 * np.pi * 440 * t)], axis=1)
        y = Q.to_measure_domain(stereo, fs)
        self.assertEqual(y.ndim, 1)                     # mono
        self.assertEqual(y.size, 16000)                 # 48k→16k
        # 算术均值下混:双反相通道 → 抵消近零(顺序钉死:先下混后重采样)
        self.assertLess(float(np.abs(y).max()), 1e-3)

    def test_measure_domain_guard(self):
        x = np.zeros(1600)
        with self.assertRaises(ValueError):
            Q.seg_snr(x, x, 44100)                      # 非 16k → 门禁拦截
        with self.assertRaises(ValueError):
            Q.stoi(np.zeros((10, 2)), np.zeros((10, 2)), MEASURE_FS)


class TestIdentity(unittest.TestCase):
    def test_identity_caps(self):
        x = speech_like()
        self.assertEqual(Q.seg_snr(x, x), Q.SEGSNR_CAP_DB)
        self.assertGreater(Q.stoi(x, x), 0.999)
        self.assertGreaterEqual(Q.pesq_wb(x, x), 4.5)
        self.assertLess(Q.lsd(x, x), 1e-6)

    def test_linear_delay_invariance(self):
        x = speech_like()
        d = np.concatenate([np.zeros(100), x])[: len(x)]
        r = Q.measure_pair(x, d)
        self.assertEqual(r["lag_samples"], 100)
        self.assertFalse(r["drift"]["suspicious"])       # 纯线性延迟非漂移
        self.assertGreaterEqual(r["stoi"], 0.97)
        self.assertLess(r["lsd"], 5.0)


class TestDegradation(unittest.TestCase):
    def test_noise_degrades_direction(self):
        x = speech_like()
        d = x + 0.3 * np.random.RandomState(1).standard_normal(len(x))
        r = Q.measure_pair(x, d)
        self.assertLess(r["stoi"], 0.99)
        self.assertLess(r["segSNR"], 20.0)
        self.assertGreater(r["lsd"], 1.0)

    def test_drift_suspicion(self):
        x = speech_like()
        n = len(x) // 2
        d = np.concatenate([x[:n], np.concatenate([np.zeros(30), x[n:]])[: len(x) - n]])
        r = Q.measure_pair(x, d)
        self.assertTrue(r["drift"]["suspicious"], "半段时变延迟应触发漂移嫌疑")
        self.assertEqual(r["drift"]["reason"], "nonlinear_drift")


class TestBandwidthNative(unittest.TestCase):
    def test_bandwidth_shrink_lowpass(self):
        fs = 48000
        t = np.arange(fs * 2) / fs
        x = (np.sin(2 * np.pi * 300 * t) + 0.5 * np.sin(2 * np.pi * 12000 * t)
             + 0.3 * np.sin(2 * np.pi * 18000 * t))
        # 简易低通(截 8kHz):频域置零
        X = np.fft.rfft(x)
        f = np.fft.rfftfreq(len(x), 1.0 / fs)
        lp = np.fft.irfft(X * (f <= 8000), len(x))
        bw = Q.bandwidth_shrink(x, lp, fs)
        self.assertGreater(bw["bandwidth_shrink_hz"], 8000.0, "低通版带宽应显著收缩")
        self.assertLess(bw["deg_energy_above_8k_ratio_db"], -40.0)

    def test_bandwidth_expansion_negative_shrink(self):
        fs = 48000
        t = np.arange(fs) / fs
        x = np.sin(2 * np.pi * 440 * t)
        # 加强高频分量(能量可比)→ deg 有效带宽上移 → 收缩为负(带宽扩张)
        d = x + 0.5 * np.sin(2 * np.pi * 12000 * t)
        bw = Q.bandwidth_shrink(x, d, fs)
        self.assertLess(bw["bandwidth_shrink_hz"], 0.0, "高能高频分量=带宽扩张,收缩为负")


class TestNoiseFloor16k(unittest.TestCase):
    def test_floor_below_active(self):
        fs = MEASURE_FS
        t = np.arange(fs * 2) / fs
        x = np.where((t > 0.3) & (t < 1.7), 0.5 * np.sin(2 * np.pi * 800 * t), 0.01 * np.sin(2 * np.pi * 60 * t))
        nf = Q.noise_floor_16k(x, fs)
        self.assertIsNotNone(nf["noise_floor_dbfs"])
        self.assertLess(nf["noise_floor_dbfs"], nf["active_rms_dbfs"] - 10.0)


if __name__ == "__main__":
    unittest.main()
