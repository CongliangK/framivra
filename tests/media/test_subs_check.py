"""subs check 纯函数单元测试（零渲染）：check_subtitles(subs, voice_windows, fps)。

构造最小帧坐标输入直接调用 vtx/subs.py 的纯函数，不触 ffmpeg/文件系统。
运行：env python3 -X utf8 tests/media/test_subs_check.py
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入，不再注入 repo/vtx）

from vtx import subs as subs_mod   # noqa: E402（包导入）

FPS = 30


def mk(subs, wins):
    return subs_mod.check_subtitles(subs, [(a, b) for a, b in wins], FPS)


class NormalAllGreen(unittest.TestCase):
    def test_aligned_reasonable_subs_pass(self):
        # voice 窗口 [60,150)（2-5s），字幕 60-120 帧（30 条字 /2s = 15 CPS？不——
        # 8 字/2s = 4 CPS），对齐偏移 0
        rep = mk([{'in': 60, 'out': 120, 'text': '大家好今天讲剪映'}], [(60, 150)])
        self.assertEqual(rep['summary'], 'PASS')
        self.assertEqual(rep['coverage'], '1/1')
        r = rep['results'][0]
        self.assertEqual(r['align']['status'], 'ok')
        self.assertEqual(r['cps']['status'], 'ok')
        self.assertEqual(r['overlap']['status'], 'ok')
        self.assertEqual(r['width']['status'], 'ok')


class Alignment(unittest.TestCase):
    def test_drift_minus_4s_fails_red(self):
        # 字幕滞后 voice 4.3s：|偏移| ≥ 1.5 → FAIL
        rep = mk([{'in': 60 + 129, 'out': 60 + 189, 'text': '大家好'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['align']['status'], 'fail')
        self.assertEqual(rep['summary'], 'FAIL')

    def test_early_2s_fails(self):
        rep = mk([{'in': 60 - 60, 'out': 30, 'text': '大家好'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['align']['status'], 'fail')

    def test_late_2s_fails(self):
        rep = mk([{'in': 60 + 60, 'out': 150, 'text': '大家好'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['align']['status'], 'fail')

    def test_small_drift_warns(self):
        rep = mk([{'in': 60 + 18, 'out': 120, 'text': '大家好'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['align']['status'], 'warn')

    def test_max_overlap_window_wins(self):
        # 两个窗口：字幕与第二窗口重叠更大，偏移相对第二窗口计算 → ok
        rep = mk([{'in': 300, 'out': 360, 'text': '大家好'}], [(60, 240), (300, 480)])
        r = rep['results'][0]['align']
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['offset_s'], 0.0)

    def test_unmatched_far_window_warns_honest(self):
        # 最近 voice 窗口距离 > 5s → WARN「无法检查对齐」，覆盖率如实扣减
        rep = mk([{'in': 0, 'out': 30, 'text': '大家好'}], [(300, 600)])
        r = rep['results'][0]['align']
        self.assertEqual(r['status'], 'warn')
        self.assertIn('无法检查对齐', r['detail'])
        self.assertEqual(rep['coverage'], '0/1')

    def test_no_voice_track_warns_all(self):
        rep = mk([{'in': 60, 'out': 120, 'text': '大家好'},
                  {'in': 180, 'out': 240, 'text': '第二条'}], [])
        self.assertTrue(all(r['align']['status'] == 'warn' for r in rep['results']))
        self.assertEqual(rep['coverage'], '0/2')
        # 无 voice 轨本身不算 FAIL（诚实条款），但其他检查仍生效
        self.assertEqual(rep['summary'], 'PASS')

    def test_align_threshold_cli_override(self):
        # --align-threshold 放宽到 3s：原 2s 偏移从 FAIL 变 WARN
        rep = subs_mod.check_subtitles(
            [{'in': 120, 'out': 180, 'text': '大家好'}], [(60, 300)], FPS, align_fail=3.0)
        self.assertEqual(rep['results'][0]['align']['status'], 'warn')


class SpeedOverlapWidth(unittest.TestCase):
    def test_cps_over_fail_threshold(self):
        # 19 字 / 2s = 9.5 CPS > 9 → FAIL
        rep = mk([{'in': 60, 'out': 120, 'text': '这行字幕特别长两秒之内完全读不完呀朋友'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['cps']['status'], 'fail')

    def test_cps_over_warn_threshold(self):
        # 16 字 / 2s = 8 CPS：>7.5 且 ≤9 → WARN
        rep = mk([{'in': 60, 'out': 120, 'text': '这行字幕的阅读速度稍微偏快了一点'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['cps']['status'], 'warn')

    def test_too_short_duration_warns(self):
        # 4 字 / 0.5s = 8 CPS 但时长 <0.6s → WARN
        rep = mk([{'in': 60, 'out': 75, 'text': '大家好'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['cps']['status'], 'warn')

    def test_overlapping_subs_both_fail(self):
        rep = mk([{'in': 60, 'out': 120, 'text': '大家好'},
                  {'in': 90, 'out': 150, 'text': '插进来了'}], [(60, 300)])
        self.assertEqual([r['overlap']['status'] for r in rep['results']], ['fail', 'fail'])

    def test_three_lines_fails(self):
        rep = mk([{'in': 60, 'out': 120, 'text': '第一行\\N第二行\\N第三行'}], [(60, 300)])
        self.assertEqual(rep['results'][0]['width']['status'], 'fail')

    def test_wide_line_fails(self):
        rep = mk([{'in': 60, 'out': 120, 'text': '一' * 29}], [(60, 300)])
        self.assertEqual(rep['results'][0]['width']['status'], 'fail')

    def test_width_boundary_28_ok(self):
        rep = mk([{'in': 60, 'out': 120, 'text': '一' * 28}], [(60, 300)])
        self.assertEqual(rep['results'][0]['width']['status'], 'ok')




class ReviewS1PostWindow(unittest.TestCase):
    """红队 S1 回归：字幕在 voice 窗口结束后开始，偏移参照最近外沿（非窗口起点）。"""
    def test_short_gap_after_window_ok(self):
        # voice 0-5s，字幕 5.5s 开始（滞后外沿 0.5s）：不得再报 +5.5s FAIL
        rep = mk([{'in': 165, 'out': 210, 'text': '大家好'}], [(0, 150)])
        r = rep['results'][0]['align']
        # 0.5s 恰落 WARN 下限（>= 语义）——S1 修复前此处是 +5.0s FAIL
        self.assertEqual(r['status'], 'warn')
        self.assertEqual(r['offset_s'], 0.5)
        self.assertIn('滞后窗口结束', r['detail'])

    def test_window_switch_boundary_zero_distance(self):
        # 切窗（CLI 真实可达形态：_union 合并后剩余间隙单窗）字幕起点 ==
        # 窗口末端（真实距离 0）：偏移 0 而非被夸大成 FAIL
        rep = mk([{'in': 300, 'out': 360, 'text': '大家好'}], [(0, 300)])
        r = rep['results'][0]['align']
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['offset_s'], 0.0)

    def test_pair_radius_boundary_inclusive(self):
        # 距离恰 == 5.0s 半径（>5 严格语义）：仍参与对齐检查（FAIL 而非 WARN）
        rep = mk([{'in': 300, 'out': 330, 'text': '大家好'}], [(0, 150)])
        r = rep['results'][0]['align']
        self.assertEqual(r['status'], 'fail')   # offset +5.0 ≥ 1.5
        # 略超半径 → WARN 无法检查，coverage 如实扣减
        rep2 = mk([{'in': 306, 'out': 336, 'text': '大家好'}], [(0, 150)])
        self.assertEqual(rep2['results'][0]['align']['status'], 'warn')
        self.assertEqual(rep2['coverage'], '0/1')

    def test_lead_before_window_negative_offset(self):
        # 字幕整体在窗口开始前 1s（无重叠）：负偏移（超前语义），参照窗口起点
        rep = mk([{'in': 0, 'out': 30, 'text': '大家好'}], [(30, 150)])
        r = rep['results'][0]['align']
        self.assertEqual(r['status'], 'warn')
        self.assertEqual(r['offset_s'], -1.0)
        self.assertIn('超前窗口开始', r['detail'])

    def test_align_fail_boundary_inclusive(self):
        # |偏移| == 1.5s 精确落在 FAIL 阈值（>= 语义锁定）
        rep = mk([{'in': 165, 'out': 210, 'text': '大家好'}], [(0, 150)])
        rep2 = subs_mod.check_subtitles([{'in': 240, 'out': 285, 'text': '大家好'}],
                                        [(0, 150)], FPS, align_fail=3.0)
        self.assertEqual(rep['results'][0]['align']['status'], 'warn')   # 0.5 ≤ 1.5 边界内
        # 偏移 3.0s == align_fail 3.0：>= 含边界 → FAIL（语义锁定）
        self.assertEqual(rep2['results'][0]['align']['status'], 'fail')


class ReviewS2ActiveSetFrameIntent(unittest.TestCase):
    """Issue178: preview follows integer intent, not the old rounded ASS bug."""

    def test_half_open_edges_and_single_frames(self):
        from types import SimpleNamespace
        for start, end in ((310, 329), (310, 311), (311, 312), (312, 313)):
            sub = {'in': start, 'out': end, 'text': '边界'}
            c = SimpleNamespace(fps=30, tl={"subtitles": [sub]})
            for f in range(start - 1, end + 2):
                self.assertEqual(subs_mod._active_subs(c, f),
                                 [sub] if start <= f < end else [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
