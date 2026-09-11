#!/usr/bin/env python3
"""segline 单测（方案 §9.1：floor/ceil、行宽 18、行首无标点硬断言、min_dur≥18、
去重叠不破下限 + 极冲突保留重叠、越界整行丢弃、rate≤9 含标点口径 8.5/9.5 两例、
[in,out) 左闭右开、0s 起音、超 24 字无标点、拉丁词切分、末字贴尾）。零模型零网络。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
