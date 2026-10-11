#!/usr/bin/env python3
"""tests/test_quality_noise_gen.py —— quality.noise_gen 单元测试(R-20 P0 测量器自测)。

运行(quality 包自身验证,与产品仓测试进程不同,遵 R-20 窗口声明口径;
【WIP 隔离约定】文件名前缀 selftest_ 不入产品仓默认收集模式 test*.py,
run_all_tests.sh 零收集;stable 后再议入收集面——落树申报附此隔离说明):
  cd plugins/denoise && ~/miniconda3/envs/denoise/bin/python \
    -m unittest discover -s tests -t . -p "test_quality_*.py" -v
"""
import sys
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from plugins.denoise.quality import noise_gen as NG           # noqa: E402
from plugins.denoise.quality import MEASURE_FS                # noqa: E402


def band_energy(x, fs, lo, hi):
    spec = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / fs)
    return float(spec[(f >= lo) & (f < hi)].sum())


class TestSeeds(unittest.TestCase):
    def test_seed_deterministic(self):
        self.assertEqual(NG.seed_for("A-01", "white"), NG.seed_for("A-01", "white"))
        self.assertNotEqual(NG.seed_for("A-01", "white"), NG.seed_for("A-01", "pink"))
        self.assertNotEqual(NG.seed_for("A-01", "white"), NG.seed_for("A-02", "white"))

    def test_generation_deterministic(self):
        a = NG.gen_noise("pink", 8000, MEASURE_FS, NG.seed_for("A-01", "pink"))
        b = NG.gen_noise("pink", 8000, MEASURE_FS, NG.seed_for("A-01", "pink"))
        np.testing.assert_array_equal(a, b)

    def test_family_values_locked(self):
        self.assertEqual(NG.FAMILIES, ("white", "pink", "hum"))
        self.assertEqual(NG.SNR_DB, 10.0)  # 修订一:统一 10dB 档


class TestFamilies(unittest.TestCase):
    def setUp(self):
        n = 1 << 15
        self.fs = MEASURE_FS
        self.x = {
            f: NG.gen_noise(f, n, self.fs, 42) for f in NG.FAMILIES
        }

    def test_hum_band_concentration(self):
        x = self.x["hum"]
        total = band_energy(x, self.fs, 1.0, self.fs / 2 - 1)
        inband = band_energy(x, self.fs, *NG.HUM_BAND_HZ)
        # 4 阶 Butterworth 带通滚降:带外残留约 5–10%,阈 0.85(口径已成文)
        self.assertGreater(inband / total, 0.85, "hum 族能量应集中于 50–150Hz")

    def test_pink_slope_negative(self):
        # 粉噪:低带/高带能量比应显著高于白噪(-3dB/oct 形态)
        fs = self.fs
        ratio_pink = band_energy(self.x["pink"], fs, 20, 200) / max(band_energy(self.x["pink"], fs, 2000, 8000), 1e-12)
        ratio_white = band_energy(self.x["white"], fs, 20, 200) / max(band_energy(self.x["white"], fs, 2000, 8000), 1e-12)
        self.assertGreater(ratio_pink, ratio_white * 5.0)

    def test_unit_power(self):
        for f, x in self.x.items():
            self.assertAlmostEqual(float(np.mean(x ** 2)), 1.0, places=6, msg=f)


class TestInjectCalibrate(unittest.TestCase):
    def setUp(self):
        fs = MEASURE_FS
        t = np.arange(fs) / fs
        env = 0.5 * (1 + np.sign(np.sin(2 * np.pi * 3 * t)))
        self.fs = fs
        self.base = env * np.sin(2 * np.pi * 1000 * t)

    def test_injection_snr(self):
        for fam in NG.FAMILIES:
            deg = NG.inject_fs(self.base, self.fs, fam, 10.0, 7)
            m = NG.measured_snr(self.base, deg)
            self.assertAlmostEqual(m, 10.0, delta=0.01, msg=fam)

    def test_calibrate_within_tol(self):
        for fam in NG.FAMILIES:
            cal = NG.calibrate(self.base, self.fs, fam)
            self.assertTrue(cal["within_tol"], f"{fam}: {cal}")
            self.assertLessEqual(abs(cal["delta_db"]), NG.CALIB_TOL_DB)


if __name__ == "__main__":
    unittest.main()
