#!/usr/bin/env python3
"""words[] 构造单测（方案 §9.1：text↔items 贪心匹配 golden——数字变形、拉丁跨 -/.
吸收、标点零宽锚、失配插值 degraded_ranges、全失配 words=[]）。零模型零网络
（build_words 为 sidecar_server 内纯函数，stdlib only，系统 python3 直接 import）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sidecar_server  # noqa: E402  （重量 import 已延迟，import 本体不加载模型）


class TestBuildWords(unittest.TestCase):
    def test_digit_variant(self):
        # 库 items：数字串变形 "314"（clean_token 剥 '.'）↔ text "3.14"
        text = "圆周率是3.14。"
        items = [("圆", 0.1, 0.2), ("周", 0.2, 0.3), ("率", 0.3, 0.4),
                 ("是", 0.4, 0.5), ("314", 0.5, 0.8)]
        words, degraded = sidecar_server.build_words(text, items)
        self.assertEqual(degraded, [])
        self.assertEqual("".join(w["w"] for w in words), text)   # 逐字符对齐
        self.assertEqual([w["w"] for w in words[4:8]], ["3", ".", "1", "4"])
        for w in words[4:8]:
            self.assertEqual((w["s"], w["e"]), (0.5, 0.8))       # 拉丁/数字串共享 token 窗
        self.assertEqual(words[8]["w"], "。")
        self.assertEqual((words[8]["s"], words[8]["e"]), (0.5, 0.8))  # 标点零宽锚=前实字

    def test_latin_absorb_across_separators(self):
        # "Qwen3-ASR" → 库输出单 token "Qwen3ASR"（'-' 剥除而非切界）；text 侧吸收
        text = "用了Qwen3-ASR做对齐"
        items = [("用", 0.0, 0.1), ("了", 0.1, 0.2), ("qwen3asr", 0.2, 0.5),
                 ("做", 0.5, 0.6), ("对", 0.6, 0.7), ("齐", 0.7, 0.8)]
        words, degraded = sidecar_server.build_words(text, items)
        self.assertEqual(degraded, [])
        self.assertEqual("".join(w["w"] for w in words), text)
        seg = words[2:11]     # "Qwen3-ASR"
        self.assertEqual("".join(w["w"] for w in seg), "Qwen3-ASR")
        for w in seg:
            self.assertEqual((w["s"], w["e"]), (0.2, 0.5))       # 含 '-'（零宽=前实字，同窗）

    def test_case_fold(self):
        words, degraded = sidecar_server.build_words(
            "OK的", [("ok", 1.0, 1.2), ("的", 1.2, 1.3)])
        self.assertEqual(degraded, [])
        self.assertEqual([w["w"] for w in words], ["O", "K", "的"])
        self.assertEqual((words[0]["s"], words[0]["e"]), (1.0, 1.2))

    def test_punct_zero_width_anchor(self):
        # items 无标点 token；text 中标点继承前一实字 {s, e}
        text = "你好，世界"
        items = [("你", 0.0, 0.1), ("好", 0.1, 0.2),
                 ("世", 0.3, 0.4), ("界", 0.4, 0.5)]
        words, degraded = sidecar_server.build_words(text, items)
        self.assertEqual(degraded, [])
        self.assertEqual((words[2]["s"], words[2]["e"]), (0.1, 0.2))   # "，" = "好" 的锚

    def test_head_punct_defensive_anchor(self):
        # 行首标点无前实字 → 防御值 0（N1 随后删除，此处只验不 crash）
        words, _ = sidecar_server.build_words("，好", [("好", 0.5, 0.6)])
        self.assertEqual(words[0]["w"], "，")
        self.assertEqual((words[0]["s"], words[0]["e"]), (0.0, 0.0))

    def test_mismatch_interpolated_with_degraded_ranges(self):
        # 中段 token 失配（"垃圾xyz" 不在 text）→ 该段线性插值 + degraded_ranges 标注
        text = "一二三四五六七八九十"
        items = [("一", 0.0, 0.1), ("垃圾xyz", 5.0, 5.1), ("十", 9.0, 9.1)]
        words, degraded = sidecar_server.build_words(text, items)
        self.assertEqual(degraded, [[1, 8]])
        self.assertEqual(len(words), 10)
        self.assertEqual((words[0]["s"], words[0]["e"]), (0.0, 0.1))
        self.assertEqual((words[9]["s"], words[9]["e"]), (9.0, 9.1))
        # 插值段单调不减、夹在两端之间
        self.assertGreaterEqual(words[4]["s"], words[0]["e"])
        self.assertLessEqual(words[8]["e"], words[9]["s"])
        for a, b in zip(words, words[1:]):
            self.assertLessEqual(a["e"], b["e"] + 1e-9)

    def test_out_of_order_tokens_partially_matched(self):
        # items 顺序与 text 不符（倒序）→ 贪心只匹配首个可达 token，其余失配插值
        text = "你好"
        items = [("好", 0.0, 0.1), ("你", 0.1, 0.2)]
        words, degraded = sidecar_server.build_words(text, items)
        # "好" 在 norm 流 [0:] 找到位置 1 → 匹配；"你" 在 [2:] 找不到 → 失配
        self.assertEqual("".join(w["w"] for w in words), "你好")
        self.assertEqual(degraded, [[0, 0]])
        self.assertEqual((words[1]["s"], words[1]["e"]), (0.0, 0.1))

    def test_all_mismatch_empty_words(self):
        text = "完全对不上"
        items = [("xyz", 0.0, 1.0)]
        words, degraded = sidecar_server.build_words(text, items)
        self.assertEqual(words, [])
        self.assertEqual(degraded, [[0, len(text) - 1]])

    def test_ms_defense(self):
        # 时间戳量级 > 10000 → 视为 ms 统一转秒（库版本回退防御）
        items = sidecar_server._to_seconds([("好", 50000.0, 80000.0)])
        self.assertEqual(items, [("好", 50.0, 80.0)])
        items = sidecar_server._to_seconds([("好", 0.5, 0.8)])
        self.assertEqual(items, [("好", 0.5, 0.8)])

    def test_empty(self):
        words, degraded = sidecar_server.build_words("", [])
        self.assertEqual((words, degraded), ([], []))


if __name__ == "__main__":
    unittest.main()
