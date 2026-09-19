#!/usr/bin/env python3
"""tests/test_quality_cer.py —— quality.cer(CER 计算与讲稿对齐)单元测试。

覆盖:文本归一(全半角/标点/大小写)/CER=(S+D+I)/N 逐型计数/空参考 null/
插入率(修订七.4 联动)/双跑一致性 0.2pp 门(修订八)/讲稿参考提取/哈希在档。
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from plugins.denoise.quality import cer as C  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_fullwidth_punct_case(self):
        self.assertEqual(C.normalize_text("Ｈｅｌｌｏ，世界！"), "hello世界")
        self.assertEqual(C.normalize_text("你好,世界。"), "你好世界")
        self.assertEqual(C.normalize_text("A B\tC"), "abc")


class TestCer(unittest.TestCase):
    def test_substitution(self):
        r = C.cer("你好世界", "你好世态")
        self.assertEqual((r["substitutions"], r["deletions"], r["insertions"]), (1, 0, 0))
        self.assertAlmostEqual(r["cer"], 0.25)

    def test_deletion_insertion(self):
        r = C.cer("大家好", "大家")
        self.assertEqual(r["deletions"], 1)
        self.assertAlmostEqual(r["cer"], 1 / 3, places=5)  # cer 保留 6 位小数
        r2 = C.cer("大家好", "大家们好")
        self.assertEqual(r2["insertions"], 1)
        self.assertAlmostEqual(r2["insertion_rate"], 1 / 3, places=5)

    def test_empty_ref_null(self):
        r = C.cer("。。。", "任何")
        self.assertIsNone(r["cer"])  # 归一后参考空 → null(禁 0/1 凑数)

    def test_decode_params_pinned(self):
        self.assertEqual(C.DECODE_PARAMS, {"temperature": 0, "batch_size": 1})


class TestDualRun(unittest.TestCase):
    def test_tolerance_gate(self):
        self.assertTrue(C.dual_run_consistent(0.1000, 0.1009)["consistent"])
        self.assertFalse(C.dual_run_consistent(0.1000, 0.1050)["consistent"])
        d = C.dual_run_consistent(0.1000, 0.1050)
        self.assertAlmostEqual(d["delta_pp"], 0.5)


class TestReference(unittest.TestCase):
    def test_load_reference_strips_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "06a-teleprompter.md"
            f.write_text("# 讲稿\n\n## 第一段\n\n大家好,(挥手)今天**讲降噪**。\n\n- 要点一\n\n| 表 | 格 |\n|---|---|\n",
                         encoding="utf-8")
            ref = C.load_reference(f)
            self.assertIn("大家好", ref)
            self.assertIn("今天讲降噪", ref.replace(",", ""))
            self.assertNotIn("挥手", ref)          # 括注(舞台指示)剔除
            self.assertIn("要点一", ref)           # 列表正文行保留
            self.assertNotIn("表格", ref.replace(" ", ""))  # 表行/标题行剔除

    def test_ref_sha256_stable(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "a.md"
            f.write_text("内容", encoding="utf-8")
            import hashlib
            self.assertEqual(C.ref_sha256(f), hashlib.sha256(f.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
