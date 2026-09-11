#!/usr/bin/env python3
"""textnorm 单测（方案 §9.1：golden ≥8 条，覆盖 N1-N7 各分支）。零模型零网络。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import textnorm  # noqa: E402


class TestTextnormGolden(unittest.TestCase):
    def test_n1_head_punct(self):
        self.assertEqual(textnorm.normalize_text("，今天天气。"), "今天天气。")
        self.assertEqual(textnorm.normalize_text("第一句。\n，第二行。"), "第一句。\n第二行。")
        # N1+N5 叠加：行首「…」被 N1 删后，「嗯」变句首语气词被 N5 删（行为符合规则表）
        self.assertEqual(textnorm.normalize_text("…嗯从省略号开始"), "从省略号开始")

    def test_n2_fold_strongest(self):
        self.assertEqual(textnorm.normalize_text("真的吗！！！"), "真的吗！")
        self.assertEqual(textnorm.normalize_text("好，。收尾"), "好。收尾")
        self.assertEqual(textnorm.normalize_text("什么？！看"), "什么？看")
        self.assertEqual(textnorm.normalize_text("，，，逗号串"), "逗号串")
        # ↑ N2 折叠「，，，」→「，」后行首逗号再被 N1 删除（叠加行为符合规则表）

    def test_n3_space_around_cjk_punct(self):
        self.assertEqual(textnorm.normalize_text("你好 ， 世界。"), "你好，世界。")

    def test_n3_cjk_cjk_space(self):
        self.assertEqual(textnorm.normalize_text("我 希望 你"), "我希望你")

    def test_n3_cjk_latin_space_kept(self):
        # 方案 N3：CJK↔拉丁边界空格保留（与 IME 原实现不同，方案为权威规格）
        self.assertEqual(textnorm.normalize_text("配合 opencode 编辑"), "配合 opencode 编辑")

    def test_n5_inline_fillers(self):
        self.assertEqual(textnorm.normalize_text("嗯今天呃天气啊。"), "今天天气。")

    def test_n5_discourse_boundary(self):
        self.assertEqual(textnorm.normalize_text("嗯对吧，我们开始"), "我们开始")
        # 正文同形词不删：「就是」两侧无标点/空白/边界
        self.assertEqual(textnorm.normalize_text("就是这个方案"), "就是这个方案")

    def test_n5_disable(self):
        self.assertEqual(textnorm.normalize_text("嗯今天天气。", remove_fillers=False),
                         "嗯今天天气。")

    def test_stream_matches_text_and_drops_time(self):
        tokens = [("嗯", 0.0, 0.2), ("今", 0.2, 0.4), ("天", 0.4, 0.6),
                  ("，", 0.6, 0.6), ("。", 0.6, 0.6)]
        out = textnorm.normalize_stream(tokens)
        self.assertEqual("".join(t[0] for t in out), "今天。")
        # 口水词删除即删其字元与时间；首字时间锚顺移不虚增
        self.assertEqual(out[0], ("今", 0.2, 0.4))

    def test_n7_rstrip_mid_punct(self):
        self.assertEqual(textnorm.rstrip_mid_punct("你好，"), "你好")
        self.assertEqual(textnorm.rstrip_mid_punct("真的？"), "真的？")   # 句末类保留
        self.assertEqual(textnorm.rstrip_mid_punct("是这样："), "是这样")

    def test_n4_soft_split_candidates(self):
        seg = "这一段话完全没有标点而且已经超过了二十四个字符了呢再加几个字凑数"
        cands = textnorm.soft_split_candidates(seg)
        self.assertTrue(cands, "30 字无标点段应有软切候选")
        self.assertTrue(all(0 < c < len(seg) for c in cands))
        short = "短句。"
        self.assertEqual(textnorm.soft_split_candidates(short), [])

    def test_n6_t2s_soft_dependency(self):
        # 软依赖：无论本机有无 libopencc，都不 crash、返回 (str, bool)
        out, ok = textnorm.t2s_convert("简体字测试")
        self.assertIsInstance(out, str)
        self.assertIsInstance(ok, bool)
        self.assertEqual(textnorm.t2s_convert("")[0], "")


if __name__ == "__main__":
    unittest.main()
