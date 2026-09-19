#!/usr/bin/env python3
"""tests/test_quality_pkg.py —— quality 包级测试:修订六依赖版本自检 fail-hard、
域常量、预注册路径常量与 versions.json 互证。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from plugins.denoise import quality                          # noqa: E402
from plugins.denoise.quality import report as R              # noqa: E402


class TestPkg(unittest.TestCase):
    def test_domain_constants_locked(self):
        self.assertEqual(quality.MEASURE_FS, 16000)   # 修订五:统一测量域
        self.assertEqual(quality.RESAMPLER, "soxr")   # 修订五:soxr 单一实现

    def test_versions_file_matches_env(self):
        pinned = json.loads(quality.VERSIONS_FILE.read_text(encoding="utf-8"))["dependencies"]
        self.assertEqual(quality.check_dependencies(), pinned)  # 不符即 fail-hard

    def test_check_dependencies_fail_hard(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.json"
            pinned = json.loads(quality.VERSIONS_FILE.read_text(encoding="utf-8"))
            pinned["dependencies"]["numpy"] = "0.0.0-bad"
            bad.write_text(json.dumps(pinned), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                quality.check_dependencies(bad)

    def test_collect_versions_importlib_metadata_form(self):
        vers = quality.collect_versions()
        self.assertEqual(set(vers), set(quality.DEPENDENCIES))
        self.assertEqual(vers["pesq"], "0.0.4")       # 修订六:红队实测值互证

    def test_preregistered_paths_constant(self):
        for p in ("summary.segSNR_gain_median", "summary.stoi_median",
                  "summary.cer_median", "summary.cer_ins_median",
                  "summary.pesq_median", "summary.best_relative_gain",
                  "summary.ab_pref_rate", "summary.likert_gap", "summary.session_count"):
            self.assertIn(p, R.PRE_REGISTERED_PATHS)


if __name__ == "__main__":
    unittest.main()
