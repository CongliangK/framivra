#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_atempo.py · atempo 参数 golden 单测（方向坑钉死，方案 §9.2 / R8）

golden 值来源：voice-ime manifest.json 实测（A1: raw 9.14s / budget 7.62s）
与 refit_final.py 修正版公式。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import atempo  # noqa: E402


class TestAtempoChain(unittest.TestCase):
    def test_direction_golden(self):
        """raw=9.14 / budget=7.62 → atempo=1.199475（=raw/budget，方向钉死）。"""
        self.assertEqual(atempo.atempo_chain(9.14, 7.62), "atempo=1.199475")

    def test_out_of_range_high_4x(self):
        """raw/budget=4 → 双级 √4=2.0：atempo=2.000000,atempo=2.000000。"""
        self.assertEqual(atempo.atempo_chain(4.0, 1.0),
                         "atempo=2.000000,atempo=2.000000")

    def test_out_of_range_low_quarter(self):
        """raw/budget=0.25 → 双级 √0.25=0.5。"""
        self.assertEqual(atempo.atempo_chain(1.0, 4.0),
                         "atempo=0.500000,atempo=0.500000")

    def test_equal_no_filter(self):
        """budget=raw（|r-1|<1e-9）→ 空串，不加滤镜（口径见 atempo.py docstring）。"""
        self.assertEqual(atempo.atempo_chain(7.62, 7.62), "")
        self.assertEqual(atempo.atempo_chain(2.0, 2.0 + 1e-12), "")

    def test_domain_boundaries_single_level(self):
        """r=0.5 / 2.0 恰在单级域内（[0.5, 2.0] 闭区间）。"""
        self.assertEqual(atempo.atempo_chain(1.0, 2.0), "atempo=0.500000")
        self.assertEqual(atempo.atempo_chain(2.0, 1.0), "atempo=2.000000")

    def test_extreme_9x_recurse(self):
        """更极端依次再拆：r=9 → 9^(1/4)=1.732051 四级同值串联。"""
        seg = f"atempo={9 ** 0.25:.6f}"
        self.assertEqual(atempo.atempo_chain(9.0, 1.0),
                         ",".join([seg] * 4))

    def test_chain_math(self):
        """链条语义：输出时长 = raw / r，多级乘积 = r。"""
        for raw, budget in [(9.14, 7.62), (4.0, 1.0), (1.0, 4.0), (9.0, 1.0)]:
            chain = atempo.atempo_chain(raw, budget)
            speed = 1.0
            for part in chain.split(","):
                speed *= float(part.split("=")[1])
            self.assertAlmostEqual(speed, raw / budget, places=5)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            atempo.atempo_chain(0.0, 1.0)
        with self.assertRaises(ValueError):
            atempo.atempo_chain(1.0, 0.0)


if __name__ == "__main__":
    unittest.main()
