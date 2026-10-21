#!/usr/bin/env python3
'Text normalization, word alignment and subtitle segmentation; no model or network.'


# textnorm contracts
# textnorm 单测（方案 §9.1：golden ≥8 条，覆盖 N1-N7 各分支）。零模型零网络。
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
        # 方案 N3：CJK↔拉丁边界空格保留（与 IME 原实现不同，本模块 README 的 N3 规则为准）
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


# words contracts
# words[] 构造单测（方案 §9.1：text↔items 贪心匹配 golden——数字变形、拉丁跨 -/.
# 吸收、标点零宽锚、失配插值 degraded_ranges、全失配 words=[]）。零模型零网络
# （build_words 为 sidecar_server 内纯函数，stdlib only，系统 python3 直接 import）。
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


# segline contracts
# segline 单测（方案 §9.1：floor/ceil、行宽 18、行首无标点硬断言、min_dur≥18、
# 去重叠不破下限 + 极冲突保留重叠、越界整行丢弃、rate≤9 含标点口径 8.5/9.5 两例、
# [in,out) 左闭右开、0s 起音、超 24 字无标点、拉丁词切分、末字贴尾）。零模型零网络。
import segline  # noqa: E402

PUNCT_HEAD = "，,、。．.!?！？；;：:…」』）)]"


def mkwords(text, start=0.0, per=0.2, duty=0.9):
    """构造 words[]：每字符占 [start+i*per, start+i*per+per*duty]，标点零宽锚=前字。"""
    words = []
    last = None
    for i, ch in enumerate(text):
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff" or ch.isalpha():
            s = start + i * per
            e = s + per * duty
            last = (s, e)
        else:
            s, e = last if last else (start, start)
        words.append({"w": ch, "s": round(s, 3), "e": round(e, 3)})
    return words


class TestSegline(unittest.TestCase):
    def assert_no_head_punct(self, subs):
        for s in subs:
            self.assertTrue(s["text"], "行不得为空")
            self.assertNotIn(s["text"][0], PUNCT_HEAD,
                             "行首出现标点：%r" % s["text"])

    def test_floor_ceil_and_zero_start(self):
        # 6 字 s=0.833…，in=floor(0.833*30)=24；末字 e=0.833+5*0.3+0.27=2.603 → out=ceil=79
        words = mkwords("一二三四五六", start=0.833, per=0.3, duty=0.9)
        subs, _ = segline.build_subtitles(words)
        self.assertEqual(len(subs), 1)
        self.assertEqual((subs[0]["in"], subs[0]["out"]), (24, 79))
        # 0s 起音 → in=0
        subs, _ = segline.build_subtitles(mkwords("开头", start=0.0, per=0.3))
        self.assertEqual(subs[0]["in"], 0)
        # [in, out) 左闭右开：out > in 恒成立
        self.assertGreater(subs[0]["out"], subs[0]["in"])

    def test_line_width_and_head_punct(self):
        text = ("今天我想从两个角度进行分享首先它怎么用其次它怎么做出来的，"
                "我希望最终你能够轻易地使用它！")
        words = mkwords(text, per=0.3)
        subs, _ = segline.build_subtitles(words)
        self.assertGreater(len(subs), 1)
        for s in subs:
            self.assertLessEqual(len(s["text"]), 18, "行宽超限：%r" % s["text"])
        self.assert_no_head_punct(subs)

    def test_min_dur_frames(self):
        # 两个极短字（0.06s 窗）→ out-in 至少 18 帧（0.6s）
        words = mkwords("好。吗？", start=1.0, per=0.03)
        subs, _ = segline.build_subtitles(words)
        for s in subs:
            self.assertGreaterEqual(s["out"] - s["in"], 18)

    def test_overlap_resolution_keeps_min_dur(self):
        # 行1 [0,30) 行2 in=19 → 去重叠 out=max(0+18, 19-2)=18，不破 in+min_dur
        words = [{"w": "好", "s": 0.0, "e": 1.0}, {"w": "。", "s": 1.0, "e": 1.0},
                 {"w": "吗", "s": 0.65, "e": 1.05}, {"w": "？", "s": 1.05, "e": 1.05}]
        subs, summ = segline.build_subtitles(words)
        self.assertEqual(len(subs), 2)
        first = min(subs, key=lambda s: s["in"])
        self.assertEqual(first["in"], 0)
        self.assertEqual(first["out"], 18)
        self.assertGreaterEqual(first["out"] - first["in"], 18)
        self.assertEqual(subs[1]["in"], 19, "下一行起点不受去重叠影响")

    def test_overlap_kept_on_extreme_conflict(self):
        # 行1 [0,30)（≥min_dur）行2 in=5：max(0+18, 5-2)=18 > 5 → 保留重叠 + 告警
        words = [{"w": "好", "s": 0.0, "e": 1.0}, {"w": "。", "s": 1.0, "e": 1.0},
                 {"w": "吗", "s": 0.3, "e": 0.9}, {"w": "？", "s": 0.9, "e": 0.9}]
        subs, summ = segline.build_subtitles(words)
        first = min(subs, key=lambda s: s["in"])
        self.assertEqual((first["in"], first["out"]), (0, 30), "极冲突应保留原重叠")
        self.assertTrue(any("重叠" in w for w in summ["warnings"]), summ["warnings"])

    def test_out_of_range_dropped(self):
        # duration=50 帧；行落在 [60,80) → 夹取后 in>=out → 整行丢弃 + 告警
        words = mkwords("界外行。", start=2.0, per=0.25)
        subs, summ = segline.build_subtitles(words, duration_frames=50)
        self.assertEqual(subs, [])
        self.assertTrue(any("时间轴外" in w for w in summ["warnings"]))

    def test_end_clamped_to_duration(self):
        # 末字贴尾：行末超出 duration → out 夹到 duration（合法保留）
        words = mkwords("贴尾句。", start=0.0, per=3.5)   # 末实字 e≈10.15s≈305 帧 > dur 300
        subs, _ = segline.build_subtitles(words, duration_frames=300)
        self.assertEqual(subs[-1]["out"], 300)

    def test_rate_8_5_passes(self):
        # 18 字 / 2.1176s：帧取整后 rate=18*30/64=8.44 ≤ 9：不触发扩窗、无速率告警
        text = "一二三四五六七八九十一二三四五六七八"
        self.assertEqual(len(text), 18)
        words = mkwords(text, start=0.0, per=2.1176 / 18, duty=1.0)
        subs, summ = segline.build_subtitles(words)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["out"], 64)            # ceil(2.1176*30)=64
        self.assertFalse(any("阅读速度" in w for w in summ["warnings"]))

    def test_rate_9_5_expands(self):
        # 18 字 / 1.79s：rate=18*30/54=10.0 > 9 → 有隙扩隙 out 54→60（=9.0 恰达上限）
        text = "一二三四五六七八九十一二三四五六七八"
        words = mkwords(text, start=0.0, per=0.1)
        subs, summ = segline.build_subtitles(words)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["out"], 60)
        rate = len(subs[0]["text"]) / ((subs[0]["out"] - subs[0]["in"]) / 30)
        self.assertLessEqual(rate, 9.0)

    def test_rate_9_5_no_gap_warns(self):
        # 同上但下一行紧贴（in=54）→ 扩隙上限 52 < 现值 54 → 无隙告警并接受（不为速率制造重叠）
        text = "一二三四五六七八九十一二三四五六七八"
        words = mkwords(text, start=0.0, per=0.1)
        words += mkwords("下一行。", start=1.80, per=0.2)
        subs, summ = segline.build_subtitles(words)
        first = min(subs, key=lambda s: s["in"])
        self.assertEqual(first["out"], 54, "无隙时不得为速率制造重叠")
        self.assertTrue(any("阅读速度" in w for w in summ["warnings"]))

    def test_latin_safe_cut(self):
        # 10 中文 + Qwen3-ASR + 10 中文（29 字无标点）→ 硬切回退词边界，不拆拉丁词
        text = "零一二三四五六七八九" + "Qwen3-ASR" + "十二十三四五六七八九十"
        words = mkwords(text, per=0.3)
        subs, _ = segline.build_subtitles(words)
        for s in subs:
            self.assertLessEqual(len(s["text"]), 18)
        self.assert_no_head_punct(subs)
        joined_lines = [s["text"] for s in subs if "Qwen3" in s["text"] or "ASR" in s["text"]]
        self.assertTrue(joined_lines, "拉丁词应存在")
        for line in joined_lines:
            self.assertIn("Qwen3-ASR", line, "拉丁词被从中间切开：%r" % line)

    def test_soft_split_candidate_used(self):
        # >24 字无标点段含助词「呢」@16 → 候选切点 17 在 [limit-8, limit] 窗口内优先采用
        seg = "零一二三四五六七八九〇一二三四五" + "呢" + "二三四五六七八九〇一二三四五六七八九"
        self.assertEqual(len(seg), 35)
        self.assertEqual(seg[16], "呢")
        lines = segline.split_line(seg, limit=18, soft_cands=[17], base=0)
        self.assertEqual(len(lines[0]), 17)
        self.assertEqual(lines[0][16], "呢")

    def test_no_orphan_punct_line(self):
        # 19 字句（18 汉字+句号）硬切：回退一位让句号随实字，行首无标点且无孤标点行
        text = "一二三四五六七八九十一二三四五六七八。"
        self.assertEqual(len(text), 19)
        subs, _ = segline.build_subtitles(mkwords(text, per=0.3))
        self.assertGreaterEqual(len(subs), 2)
        for s in subs:
            self.assertLessEqual(len(s["text"]), 18)
        self.assert_no_head_punct(subs)
        self.assertTrue(all(len(s["text"]) >= 2 for s in subs), "不得产生孤字/孤标点行")

    def test_offset_frames(self):
        words = mkwords("整体平移。", start=0.5, per=0.3)
        base, _ = segline.build_subtitles(words)
        moved, _ = segline.build_subtitles(words, offset_frames=10)
        self.assertEqual(moved[0]["in"], base[0]["in"] + 10)
        self.assertEqual(moved[0]["out"], base[0]["out"] + 10)

    def test_punct_zero_width_no_inflate(self):
        # 行尾句号零宽锚=末实字 e → 行 out 与无句号版完全一致（不虚增时长）
        with_p = segline.build_subtitles(mkwords("好。", start=0.5, per=0.2))[0]
        without = segline.build_subtitles(
            [{"w": "好", "s": 0.5, "e": 0.7}])[0]
        self.assertEqual(with_p[0]["out"], without[0]["out"])
        self.assertEqual(with_p[0]["in"], without[0]["in"])

    def test_filler_words_removed_with_time(self):
        words = mkwords("嗯今天天气。", start=0.0, per=0.3)
        subs, _ = segline.build_subtitles(words)
        self.assertEqual(subs[0]["text"], "今天天气。")
        self.assert_no_head_punct(subs)

    def test_empty_words(self):
        subs, summ = segline.build_subtitles([])
        self.assertEqual(subs, [])
        self.assertEqual(summ["n_lines"], 0)


if __name__ == "__main__":
    unittest.main()
