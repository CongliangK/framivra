#!/usr/bin/env python3
"""tests/test_quality_report.py —— quality.report(schema 与输出)单元测试。

覆盖:预注册字段路径(修订十一)/计数口径(每记录恰 1 处 "sample_id",修订十)/
真实段非配对字段 n-a 规则(修订十五)/排除率 30% 整轮无效门(修订七.2)/
规范序列化确定性+sha256。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from plugins.denoise.quality import report as R  # noqa: E402


def mk_report():
    return R.new_report({"report_kind": "pair_quality", "stage": "P0-selftest",
                         "roster_sha256": "0" * 64,
                         "chains": [{"chain_id": "v1", "chain_family": "生成式"},
                                    {"chain_id": "cand", "chain_family": "生成式"}]})


def add_pair(rep, sid, segSNR_b, segSNR_u, stoi_b, stoi_u, pesq_b, pesq_u, suspicious=False):
    conf = {"suspicious": suspicious, "reason": "nonlinear_drift" if suspicious else ""}
    R.add_record(rep, sample_id=sid, chain_id="v1", chain_family="生成式",
                 family_type="synthetic", paired=True,
                 metrics={"segSNR": segSNR_b, "stoi": stoi_b, "pesq": pesq_b},
                 confidence={"suspicious": False, "reason": ""}, chain_ref="baseline")
    R.add_record(rep, sample_id=sid, chain_id="cand", chain_family="生成式",
                 family_type="synthetic", paired=True,
                 metrics={"segSNR": segSNR_u, "stoi": stoi_u, "pesq": pesq_u},
                 confidence=conf, chain_ref="upgrade")


class TestSchema(unittest.TestCase):
    def test_chain_family_required(self):
        with self.assertRaises(ValueError):
            R.new_report({"report_kind": "x", "roster_sha256": "0" * 64,
                          "chains": [{"chain_id": "no_fam"}]})

    def test_preregistered_paths_present(self):
        rep = mk_report()
        add_pair(rep, "S-01", 10.0, 18.0, 0.80, 0.90, 2.0, 3.0)
        R.finalize(rep)
        self.assertEqual(R.validate(rep), [])

    def test_real_a_na_rule(self):
        rep = mk_report()
        R.add_record(rep, sample_id="A-01", chain_id="v1", chain_family="生成式",
                     family_type="real_a", paired=False, metrics={"lsd": 3.2},
                     chain_ref="baseline")
        with self.assertRaises(ValueError):  # 真实段 STOI/PESQ/segSNR 恒 null
            R.add_record(rep, sample_id="A-02", chain_id="v1", chain_family="生成式",
                         family_type="real_a", paired=False,
                         metrics={"stoi": 0.5}, chain_ref="baseline")
        rec = rep["records"][0]
        self.assertIsNone(rec["stoi"])
        self.assertIn("stoi", rec["na_fields"])

    def test_missing_path_flagged(self):
        rep = mk_report()
        add_pair(rep, "S-01", 10.0, 18.0, 0.8, 0.9, 2.0, 3.0)
        R.finalize(rep)
        del rep["summary"]["best_relative_gain"]
        errs = R.validate(rep)
        self.assertTrue(any("best_relative_gain" in e for e in errs))


class TestSummary(unittest.TestCase):
    def test_gain_median_and_family(self):
        rep = mk_report()
        vals = [(12.0, 19.0), (14.0, 15.0), (16.0, 25.0), (18.0, 28.0)]
        for i, (b, u) in enumerate(vals):
            add_pair(rep, f"S-{i+1:02d}", b, u, 0.8, 0.9, 2.0, 2.5)
        R.finalize(rep)
        # gains = [7,1,9,10] → 中位 (7+9)/2 = 8.0
        self.assertEqual(rep["summary"]["segSNR_gain_median"], 8.0)
        self.assertIn("生成式", rep["summary"]["by_family"])
        self.assertIsNotNone(rep["summary"]["by_family"]["生成式"]["segSNR_gain_median"])

    def test_exclusion_gate(self):
        rep = mk_report()
        for i in range(4):
            add_pair(rep, f"S-{i+1:02d}", 12.0, 18.0, 0.8, 0.9, 2.0, 2.5)
        for i in range(4, 7):  # 3/7 段嫌疑 >30%
            add_pair(rep, f"S-{i+1:02d}", 12.0, 18.0, 0.8, 0.9, 2.0, 2.5, suspicious=True)
        R.finalize(rep)
        self.assertFalse(rep["summary"]["measurement_valid"])
        errs = R.validate(rep)
        self.assertTrue(any("整轮无效" in e for e in errs))
        # 原始值永在档(排除只影响汇总);嫌疑标记只落升级链记录(3 条)
        self.assertEqual(len([r for r in rep["records"] if r["confidence"]["suspicious"]]), 3)
        self.assertEqual(sorted(set(rep["summary"]["excluded_segments"])),
                         ["S-05", "S-06", "S-07"])

    def test_excluded_not_in_median(self):
        rep = mk_report()
        add_pair(rep, "S-01", 12.0, 100.0, 0.8, 0.99, 2.0, 4.0, suspicious=True)
        add_pair(rep, "S-02", 12.0, 14.0, 0.8, 0.82, 2.0, 2.1)
        R.finalize(rep)
        self.assertEqual(rep["summary"]["segSNR_gain_median"], 2.0)  # 仅 S-02 计入


class TestWriteout(unittest.TestCase):
    def test_dump_deterministic_and_count(self):
        rep = mk_report()
        add_pair(rep, "S-01", 12.0, 18.0, 0.8, 0.9, 2.0, 2.5)
        R.finalize(rep)
        b1, b2 = R.dump_bytes(rep), R.dump_bytes(rep)
        self.assertEqual(b1, b2)
        self.assertEqual(b1.decode("utf-8").count('"sample_id"'),
                         len(rep["records"]))  # 修订十:每记录恰 1 处字面
        self.assertEqual(len(rep["records"]), 2)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            h = R.write_report(rep, p)
            import hashlib
            self.assertEqual(h, hashlib.sha256(p.read_bytes()).hexdigest())
            self.assertEqual(R.count_sample_ids(p), 2)


if __name__ == "__main__":
    unittest.main()
