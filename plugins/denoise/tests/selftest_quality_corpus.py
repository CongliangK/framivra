#!/usr/bin/env python3
"""tests/test_quality_corpus.py —— quality.corpus(名册与谱系核查)单元测试。

覆盖:PCM 归一哈希身份/区域指纹匹配(含于 take/无关)/只读滤镜法噪声底
(真 ffmpeg 集成)/分层择段成文规则(并列哈希序、三档序首 2)/池去重/
「原始」不足 6 段的拒绝冻结路径。
"""
import hashlib
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from plugins.denoise.quality import corpus as C            # noqa: E402
from plugins.denoise.quality import MEASURE_FS             # noqa: E402


def write_wav16(path, x, fs=MEASURE_FS):
    import soundfile as sf

    q = np.clip(np.round(np.asarray(x) * 32767), -32768, 32767).astype(np.int16)
    sf.write(str(path), q, fs, subtype="PCM_16")


def tone_burst(fs=MEASURE_FS, dur=1.5):
    t = np.arange(int(fs * dur)) / fs
    gate = ((t > 0.1) & (t < 0.5)) | ((t > 0.7) & (t < 1.2))
    return np.where(gate, 0.6 * np.sin(2 * np.pi * 700 * t), 0.0)


class TestPcmIdentity(unittest.TestCase):
    def test_pcm_sha_stable_and_discriminative(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "a.wav", Path(td) / "b.wav"
            write_wav16(a, tone_burst())
            write_wav16(b, tone_burst())
            h1, h2 = C.pcm_sha256(a), C.pcm_sha256(b)
            self.assertEqual(h1, h2)                      # 同内容同容器 → 同哈希
            write_wav16(b, tone_burst() * 0.5)
            self.assertNotEqual(h1, C.pcm_sha256(b))      # 幅度变 → 哈希变


class TestRegionMatch(unittest.TestCase):
    def test_contained_vs_unrelated(self):
        fs = MEASURE_FS
        seg = tone_burst(fs)
        noise = np.random.RandomState(9).standard_normal(fs * 6) * 0.1
        take = np.concatenate([noise[: fs], seg, noise[fs :]])
        m_in = C.region_match(seg, take)
        m_out = C.region_match(seg, np.random.RandomState(10).standard_normal(fs * 4) * 0.5)
        self.assertGreaterEqual(m_in["corr"], 0.98)
        self.assertLess(m_out["corr"], 0.9)


class TestNoiseFloorFFmpeg(unittest.TestCase):
    def test_silence_window_floor(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x.wav"
            fs = MEASURE_FS
            t = np.arange(fs * 3) / fs
            x = np.where((t > 0.5) & (t < 1.5), 0.5 * np.sin(2 * np.pi * 600 * t), 0.001)
            write_wav16(f, x)
            nf = C.noise_floor_ffmpeg(f)
            self.assertTrue(nf["estimable"], nf)
            self.assertGreaterEqual(nf["n_windows"], 1)
            # 静默窗 RMS ≈ -60dBFS(0.001);容差 ±6dB
            self.assertAlmostEqual(nf["noise_floor_astats_rms_dbfs"], -60.0, delta=6.0)


class TestStratifiedSelect(unittest.TestCase):
    def _pool(self, n=9):
        pool = []
        for i in range(n):
            pool.append({"kind": "a_family", "file": f"assets/a_family/D{i:02d}.wav",
                         "pcm_sha256": hashlib.sha256(f"s{i}".encode()).hexdigest(),
                         "canon": True, "aliases": [], "_abs": f"/tmp/{i}.wav"})
        return pool

    def test_tier_first_two_with_hash_tiebreak(self):
        # 受控桩:按文件序给梯度噪声底(噪声底升序 = 文件序)
        def fake_floor(p):
            idx = int(Path(p).stem)
            return {"noise_floor_astats_rms_dbfs": -50.0 - 5 * idx, "n_windows": 2,
                    "windows_used": 2, "detect_pass": "stub", "estimable": True}
        with unittest.mock.patch.object(C, "noise_floor_ffmpeg", side_effect=fake_floor):
            picked = C.stratified_select(self._pool())
        self.assertEqual(len(picked), 6)
        tiers = [e["tier"] for e in picked]
        self.assertEqual(tiers.count("low"), 2)
        self.assertEqual(tiers.count("mid"), 2)
        self.assertEqual(tiers.count("high"), 2)
        # 估计值升序=最静在前:floor=-50-5·idx → 升序 idx=[8..0];
        # 低档序首=8,7;中档=5,4;高档=2,1
        idxs = sorted(int(Path(e["_abs"]).stem) for e in picked)
        self.assertEqual(idxs, [1, 2, 4, 5, 7, 8])

    def test_insufficient_pool_raises(self):
        with unittest.mock.patch.object(C, "noise_floor_ffmpeg",
                                        return_value={"noise_floor_astats_rms_dbfs": -50.0,
                                                      "estimable": True}):
            with self.assertRaises(RuntimeError):
                C.stratified_select(self._pool(4))


class TestPoolDedup(unittest.TestCase):
    def test_duplicate_content_keeps_first(self):
        with tempfile.TemporaryDirectory() as td:
            bench = Path(td)
            (bench / "assets/a_family").mkdir(parents=True)
            (bench / "assets/aup3_extract").mkdir(parents=True)
            write_wav16(bench / "assets/a_family/D1-V1.wav", tone_burst())
            write_wav16(bench / "assets/aup3_extract/track01_x.wav", tone_burst())
            gene = {"files": [{"file": "assets/a_family/D1-V1.wav", "lineage": "原始",
                               "pcm_sha256": C.pcm_sha256(bench / "assets/a_family/D1-V1.wav")}],
                    "takes": {"track01_x.wav": {"sha256": "x", "duration_s": 1.5,
                                                "best_raw_match": None, "best_raw_corr": 0.0}}}
            pool = C.pool_build(bench, gene)
            self.assertEqual(len(pool), 1)                # 同内容去重
            self.assertEqual(pool[0]["file"], "assets/a_family/D1-V1.wav")  # demo 正本优先
            self.assertIn("assets/aup3_extract/track01_x.wav", pool[0]["aliases"])


class TestRosterGuard(unittest.TestCase):
    def test_roster_refuses_without_originals(self):
        with tempfile.TemporaryDirectory() as td:
            bench = Path(td)
            (bench / "assets/a_family").mkdir(parents=True)
            write_wav16(bench / "assets/a_family/D1-V1.wav", tone_burst())
            with self.assertRaises(RuntimeError):
                C.build_roster(bench, bench / "roster.json")  # 无「原始」6 段 → 拒绝冻结


if __name__ == "__main__":
    unittest.main()
