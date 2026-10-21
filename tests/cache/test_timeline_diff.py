#!/usr/bin/env python3
"""tests/cache/test_timeline_diff.py —— 时间线语义 diff（方案五阶段2）单元测试。

覆盖（键/切点/段内容键均为纯函数，零渲染免费测）：
  ① 配对逻辑：恒等五元组直配、同源 clip 移窗/变长二段配对、字幕/音频事件配对、轨道增删；
  ② 字段级变化：dur/scale/volume/duck_params 等逐字段捕获，无遗漏无虚构；
  ③ seg 键失效纯函数判定：改 1 个 clip 时长 → 失效键集合 == 逐 clip 按公式重算的集合
     （真值比对），不多报（无关 clip 改动不产生受影响段）；
  ④ 段级失效（vseg）：时长变化失效相交段且不多报；纯音量改动 0 段失效；
  ⑤ 推荐路径：几何改动→生成器、音频参数→幂等补丁、_meta→白名单手改；
  ⑥ CLI：timeline 两参自动分流（报告 JSON 不分流）、PASS 退出码 0、--json 落盘结构完整。
测试用自建最小 timeline fixture（不触碰 voice-ime 项目）。
运行：env python3 -m unittest discover tests -v（本机 ZCode 环境必须 env python3 前缀，
裸 python3 的 sys.executable 被 AppImage 劫持，见全局 AGENTS.md）
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入，不再注入 repo/vtx）

from vtx.composite import SEG_CUT_GUARD, choose_cutpoints
from vtx.timeline_diff import DiffCtx, _clip_seg_key, _edges, diff_timelines, is_timeline

VTX = REPO / "vtx" / "vtx.py"


def _tl(**over):
    """最小 timeline fixture：2 轨 3 clip（aroll×2 + gfx×1）+ 1 字幕 + 1 duck 音轨。"""
    tl = {
        "version": 2,
        "_meta": {"note": "fixture"},
        "project": {"fps": 30, "width": 1920, "height": 1080, "duration_frames": 360},
        "sources": [
            {"id": "a", "path": "a.mp4"},
            {"id": "b", "path": "b.mp4"},
            {"id": "bgm", "path": "bgm.wav"},
        ],
        "video_tracks": [
            {"name": "aroll", "kind": "aroll", "clips": [
                {"src": "a", "src_in": 0, "src_out": 180, "dst_in": 0, "dur": 180},
                {"src": "b", "src_in": 0, "src_out": 180, "dst_in": 180, "dur": 180}]},
            {"name": "gfx-full", "kind": "gfx", "clips": [
                {"src": "page.html", "dst_in": 30, "dur": 60}]},
        ],
        "subtitles": [{"in": 30, "out": 90, "text": "你好"}],
        "audio_tracks": [
            {"name": "voice", "events": [
                {"src": "a", "src_in": 0, "in": 0, "out": 180, "volume": 1.0}]},
            {"name": "bed", "duck": True,
             "duck_params": {"threshold": 0.0126, "ratio": 4},
             "events": [{"src": "bgm", "src_in": 0, "in": 0, "out": 360, "volume": 0.1}]},
        ],
    }
    tl.update(over)
    return tl


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        # seg/gfx 键要求源文件存在（_fp/stat / html read_bytes）：造占位小文件
        (root / "a.mp4").write_bytes(b"a" * 1024)
        (root / "b.mp4").write_bytes(b"b" * 1024)
        (root / "bgm.wav").write_bytes(b"m" * 256)
        (root / "page.html").write_bytes(b"<html></html>")
        self.root = root

    def tearDown(self):
        self._tmp.cleanup()

    def diff(self, a, b):
        return diff_timelines(a, self.root, b, self.root)

    @staticmethod
    def edit(tl, fn):
        fn(tl)
        return tl


class TestIsTimeline(unittest.TestCase):
    def test_yes_no(self):
        self.assertTrue(is_timeline(_tl()))
        self.assertFalse(is_timeline({"checks": []}))          # qc 报告
        self.assertFalse(is_timeline({"results": [], "summary": {}}))  # subs 报告
        self.assertFalse(is_timeline({}))


class TestPairing(_Base):
    def test_identical_no_entries(self):
        res = self.diff(_tl(), _tl())
        self.assertEqual(res["n_entries"], 0)
        self.assertEqual(res["advice"]["recommended_path"], "none（无实质差异，或仅白名单字段——可直接手改）")

    def test_same_src_window_change_paired(self):
        """同源 clip 改 src_in（窗口移动）→ 配对为 change 而非 add+del。"""
        def fn(tl):
            tl["video_tracks"][0]["clips"][0]["src_in"] = 10
            tl["video_tracks"][0]["clips"][0]["src_out"] = 190
        res = self.diff(_tl(), self.edit(_tl(), fn))
        paths = [e["path"] for e in res["entries"]]
        self.assertEqual([p for p in paths if ".clips[" in p],
                         ["video_tracks[aroll].clips[a@0].src_in",
                          "video_tracks[aroll].clips[a@0].src_out"])

    def test_add_del_clip(self):
        def fn(tl):
            tl["video_tracks"][0]["clips"].append(
                {"src": "b", "src_in": 200, "src_out": 260, "dst_in": 360, "dur": 60})
        res = self.diff(_tl(), self.edit(_tl(), fn))
        self.assertEqual([e["kind"] for e in res["entries"]], ["add"])
        self.assertIn("clips[b@360]", res["entries"][0]["path"])

    def test_track_delete(self):
        def fn(tl):
            del tl["video_tracks"][1]   # gfx 轨
        res = self.diff(_tl(), self.edit(_tl(), fn))
        self.assertEqual(res["entries"][0]["path"], "video_tracks[gfx-full]")

    def test_subtitle_change_paired_not_reindexed(self):
        def fn(tl):
            tl["subtitles"][0]["text"] = "改了"
            tl["subtitles"].append({"in": 100, "out": 120, "text": "新增"})
        res = self.diff(_tl(), self.edit(_tl(), fn))
        kinds = sorted((e["kind"], e["path"]) for e in res["entries"])
        self.assertIn(("change", "subtitles[30-90].text"), kinds)
        self.assertIn(("add", "subtitles[100-120]"), kinds)

    def test_audio_event_volume_change(self):
        def fn(tl):
            tl["audio_tracks"][1]["events"][0]["volume"] = 0.2
        res = self.diff(_tl(), self.edit(_tl(), fn))
        self.assertEqual([e["path"] for e in res["entries"]],
                         ["audio_tracks[bed].events[bgm@0].volume"])
        self.assertEqual(res["entries"][0]["category"], "patch")


class TestSegKeyImpact(_Base):
    def test_one_clip_dur_change_invalidates_exactly_one_seg_key(self):
        """硬门槛（不多报）：只改 1 个 clip 时长 → 失效 seg 键集合 == 逐 clip 公式重算集合。"""
        def fn(tl):
            tl["video_tracks"][0]["clips"][1]["dur"] += 15
        old, new = _tl(), self.edit(_tl(), fn)
        res = self.diff(old, new)
        inv = res["impact"]["seg_invalid"]
        self.assertEqual(len(inv), 1)
        self.assertEqual(inv[0]["clip"], "b@180")
        # 真值：对两份 timeline 全量逐 clip 重算 seg 键，差集必须与报告一致
        ca, cb = DiffCtx(old, self.root), DiffCtx(new, self.root)
        truth = set()
        for tr in old["video_tracks"]:
            if tr.get("kind") == "gfx":
                continue
            for cl in tr["clips"]:
                truth.add(_clip_seg_key(ca, tr, 0, cl))
        truth_new = set()
        for tr in new["video_tracks"]:
            if tr.get("kind") == "gfx":
                continue
            for cl in tr["clips"]:
                truth_new.add(_clip_seg_key(cb, tr, 0, cl))
        reported = {x["old_key"] for x in inv} | {x["new_key"] for x in inv}
        self.assertEqual(reported, truth ^ truth_new)

    def test_unrelated_clip_change_no_extra_invalid(self):
        """改 gfx 页 dur → 不产生任何 seg 失效（gfx 键失效单独报）。"""
        def fn(tl):
            tl["video_tracks"][1]["clips"][0]["dur"] += 15
        res = self.diff(_tl(), self.edit(_tl(), fn))
        self.assertEqual(res["impact"]["seg_invalid"], [])
        self.assertEqual(len(res["impact"]["gfx_invalid"]), 1)

    def test_audio_only_change_no_video_invalid(self):
        """纯音量改动：seg/gfx/vseg 全部零失效，video 阶段不需重跑。"""
        def fn(tl):
            tl["audio_tracks"][1]["events"][0]["volume"] = 0.2
        res = self.diff(_tl(), self.edit(_tl(), fn))
        imp = res["impact"]
        self.assertEqual(imp["seg_invalid"], [])
        self.assertEqual(imp["gfx_invalid"], [])
        self.assertEqual(imp["vseg_invalid"]["count"], 0)
        self.assertFalse(imp["stages"]["video"])
        self.assertTrue(imp["stages"]["audio"] and imp["stages"]["mux"] and imp["stages"]["qc"])

    def test_vseg_invalidates_intersecting_segments_only(self):
        """改 1 clip 时长（中段）：失效段必与该 clip 相交，且 ≤ 全部段。"""
        def fn(tl):
            tl["video_tracks"][0]["clips"][1]["dur"] += 15   # [180,375)
        res = self.diff(_tl(), self.edit(_tl(), fn))
        v = res["impact"]["vseg_invalid"]
        self.assertGreater(v["count"], 0)
        self.assertLess(v["count"], v["total"])
        clip = (180, 195 + 180)   # 编辑后 clip dst 区间 [180,375)
        for a, b in v["segments"]:
            self.assertTrue(a < clip[1] and b > clip[0], f"段 [{a},{b}) 不与编辑 clip 相交")

    def test_meta_only_is_whitelist_no_stages(self):
        """_meta 是唯一白名单：只改 _meta → 除 validate（输入变化必跑，秒级）外零阶段。"""
        def fn(tl):
            tl["_meta"]["note"] = "改注释"
        res = self.diff(_tl(), self.edit(_tl(), fn))
        self.assertEqual(res["entries"][0]["category"], "white")
        st = res["impact"]["stages"]
        self.assertTrue(all(not v for k, v in st.items() if k != "validate"))
        self.assertTrue(st["validate"])   # 输入变即建议 validate（~3s，与 R1/R2 口径一致）


class TestEmptyVideoTracks(_Base):
    """The legal empty-track canvas has the renderer's real segment layout."""

    def timeline(self, duration=240):
        return _tl(
            project={"fps": 30, "width": 1920, "height": 1080,
                     "duration_frames": duration},
            sources=[{"id": "bgm", "path": "bgm.wav"}],
            video_tracks=[],
            subtitles=[{"in": min(58, duration - 1), "out": min(62, duration), "text": "CROSS"}],
            audio_tracks=[{"name": "bed", "events": [
                {"src": "bgm", "in": 0, "out": duration, "volume": 1.0}]}])

    def test_edges_share_real_cutpoints_and_guard_coalescing(self):
        cases = [(240, 1, [0, 240]), (240, 4, [0, 60, 120, 180, 240]),
                 (60, 4, [0, 60]), (120, 4, [0, 41, 120])]
        for duration, requested, expected in cases:
            with self.subTest(duration=duration, requested=requested):
                renderer = [0] + choose_cutpoints([], duration, requested) + [duration]
                self.assertEqual(renderer, expected)
                self.assertEqual(_edges(self.timeline(duration), requested), renderer)

    def test_clip_based_cutpoints_unchanged(self):
        timeline = _tl()
        bounds = [(cl["dst_in"], cl["dst_in"] + cl["dur"])
                  for track in timeline["video_tracks"] for cl in track["clips"]]
        for requested in (1, 4, 8):
            with self.subTest(requested=requested):
                expected = [0] + choose_cutpoints(bounds, 360, requested) + [360]
                self.assertEqual(_edges(timeline, requested), expected)

    def test_endpoint_edit_reports_one_of_four_and_frame_fraction(self):
        old = self.timeline()
        new = copy.deepcopy(old)
        new["subtitles"][0]["out"] = 63
        impact = diff_timelines(old, self.root, new, self.root, nseg=4)["impact"]
        self.assertEqual(impact["vseg_invalid"], {
            "count": 1, "total": 4, "segments": [[60, 120]],
            "nseg": 4, "guard": SEG_CUT_GUARD})
        self.assertEqual(impact["estimated_seconds"]["video"], 37.5)
        self.assertIn("60/240", impact["estimate_basis"])
        self.assertTrue(impact["stages"]["video"])

    def test_no_change_reports_zero_of_four_and_no_stages(self):
        old = self.timeline()
        impact = diff_timelines(old, self.root, copy.deepcopy(old), self.root, nseg=4)["impact"]
        self.assertEqual(impact["vseg_invalid"]["count"], 0)
        self.assertEqual(impact["vseg_invalid"]["total"], 4)
        self.assertEqual(impact["vseg_invalid"]["segments"], [])
        self.assertFalse(any(impact["stages"].values()))
        self.assertNotIn("video", impact["estimated_seconds"])

    def test_audio_only_reports_zero_of_four_without_video_work(self):
        old = self.timeline()
        new = copy.deepcopy(old)
        new["audio_tracks"][0]["events"][0]["volume"] = 0.5
        impact = diff_timelines(old, self.root, new, self.root, nseg=4)["impact"]
        self.assertEqual(impact["vseg_invalid"]["count"], 0)
        self.assertEqual(impact["vseg_invalid"]["total"], 4)
        self.assertEqual(impact["vseg_invalid"]["segments"], [])
        self.assertFalse(impact["stages"]["video"])
        self.assertTrue(impact["stages"]["audio"])
        self.assertNotIn("video", impact["estimated_seconds"])

    def test_subtitle_right_open_segment_boundary(self):
        old = self.timeline()
        old["subtitles"][0]["out"] = 60
        new = copy.deepcopy(old)
        new["subtitles"][0]["text"] = "EDIT"
        impact = diff_timelines(old, self.root, new, self.root, nseg=4)["impact"]
        self.assertEqual(impact["vseg_invalid"]["segments"], [[0, 60]])
        new = copy.deepcopy(old)
        new["subtitles"][0]["out"] = 61
        impact = diff_timelines(old, self.root, new, self.root, nseg=4)["impact"]
        self.assertEqual(impact["vseg_invalid"]["segments"], [[60, 120]])

    def test_short_duration_reports_actual_count_not_requested_count(self):
        old = self.timeline(60)
        old["subtitles"] = [{"in": 2, "out": 4, "text": "SHORT"}]
        new = copy.deepcopy(old)
        new["subtitles"][0]["out"] = 5
        impact = diff_timelines(old, self.root, new, self.root, nseg=4)["impact"]
        self.assertEqual(impact["vseg_invalid"]["total"], 1)
        self.assertEqual(impact["vseg_invalid"]["segments"], [[0, 60]])
        self.assertEqual(impact["estimated_seconds"]["video"], 150.0)


class TestAdvice(_Base):
    def test_geometry_goes_generator(self):
        def fn(tl):
            tl["video_tracks"][0]["clips"][0]["dur"] += 10
        res = self.diff(_tl(), self.edit(_tl(), fn))
        self.assertTrue(res["advice"]["generator"]["items"])
        self.assertIn("generator", res["advice"]["recommended_path"])

    def test_volume_goes_patch(self):
        def fn(tl):
            tl["audio_tracks"][1]["duck_params"]["ratio"] = 6
        res = self.diff(_tl(), self.edit(_tl(), fn))
        self.assertIn("patch_script", res["advice"]["recommended_path"])
        self.assertEqual(res["entries"][0]["category"], "patch")

    def test_same_id_audio_source_swap_prepare_not_video(self):
        """P1：同 id 换纯音频源 path → prepare/audio 置位、video 不误报。"""
        def fn(tl):
            next(s for s in tl["sources"] if s["id"] == "bgm")["path"] = "bgm2.wav"
        res = self.diff(_tl(), self.edit(_tl(), fn))
        imp = res["impact"]
        self.assertEqual(imp["sources_changed"]["audio_only"], ["bgm"])
        self.assertTrue(imp["stages"]["prepare"] and imp["stages"]["audio"])
        self.assertFalse(imp["stages"]["video"])
        self.assertEqual(imp["seg_invalid"], [])

    def test_same_id_visual_source_swap_video_invalid(self):
        """P1 对照：同 id 换画面源 path（不被音频事件引用）→ video 置位（seg 键含路径
        指纹）、audio 不置位。"""
        (self.root / "b2.mp4").write_bytes(b"b2" * 512)
        def fn(tl):
            next(s for s in tl["sources"] if s["id"] == "b")["path"] = "b2.mp4"
        res = self.diff(_tl(), self.edit(_tl(), fn))
        imp = res["impact"]
        self.assertEqual(imp["sources_changed"]["visual"], ["b"])
        self.assertTrue(imp["stages"]["video"] and imp["stages"]["prepare"])
        self.assertFalse(imp["stages"]["audio"])
        # seg 键路径入指纹：引用该源的 clip 键必须失效（真值=重算差集）
        self.assertEqual({x["clip"] for x in imp["seg_invalid"]}, {"b@180"})

    def test_estimate_declares_basis(self):
        def fn(tl):
            tl["video_tracks"][0]["clips"][0]["dur"] += 10
        res = self.diff(_tl(), self.edit(_tl(), fn))
        self.assertIn("voice-ime", res["impact"]["estimate_basis"])
        self.assertGreater(res["impact"]["estimated_seconds"].get("video", 0), 0)


class TestCli(unittest.TestCase):
    """CLI 分流：timeline 两参自动走语义 diff；报告 JSON 维持 diffcmd；--json 落盘。"""

    def _run(self, *args):
        return subprocess.run(["env", "python3", str(VTX), "diff", *args],
                              capture_output=True, text=True, cwd=self.tmp.name)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp
        root = Path(self.tmp.name)
        (root / "a.mp4").write_bytes(b"a" * 64)
        (root / "b.mp4").write_bytes(b"b" * 64)
        (root / "bgm.wav").write_bytes(b"m" * 64)
        (root / "page.html").write_bytes(b"<html></html>")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, obj):
        p = Path(self.tmp.name) / name
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def test_timeline_auto_route_and_pass_code(self):
        a = self._write("a.json", _tl())
        b = self._write("b.json", _tl())
        r = self._run(a, b)
        self.assertEqual(r.returncode, 0)
        self.assertIn("语义 diff", r.stdout)
        self.assertIn("PASS｜两份 timeline 无实质差异", r.stdout)

    def test_timeline_diff_exit_1_and_json(self):
        old, new = _tl(), _tl()
        new["audio_tracks"][1]["events"][0]["volume"] = 0.2
        a, b = self._write("a.json", old), self._write("b.json", new)
        out = str(Path(self.tmp.name) / "d.json")
        r = self._run(a, b, "--json", out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("DIFF｜", r.stdout)
        d = json.loads(Path(out).read_text(encoding="utf-8"))
        for key in ("entries", "impact", "advice"):
            self.assertIn(key, d)
        self.assertEqual(d["impact"]["vseg_invalid"]["count"], 0)

    def test_report_diff_not_rerouted(self):
        a = self._write("qa.json", {"checks": [{"name": "x", "pass": True, "detail": "1"}]})
        b = self._write("qb.json", {"checks": [{"name": "x", "pass": False, "detail": "2"}]})
        r = self._run(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("类型：qc / qc", r.stdout)   # diffcmd 报告对比路径
        self.assertNotIn("语义 diff", r.stdout)

    def test_bad_json_distinct_error(self):
        """P2c：坏 JSON 报「两类对比都要求合法 JSON」，不静默回落报告对比。"""
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        ok = self._write("ok.json", _tl())
        r = self._run(str(bad), ok)
        self.assertEqual(r.returncode, 2)
        self.assertIn("都要求两份合法 JSON", r.stdout)

    def test_mixed_type_distinct_error(self):
        """P2c：timeline 混报告 → 类型不一致文案区分两类输入。"""
        tl = self._write("t.json", _tl())
        rep = self._write("r.json", {"checks": []})
        for x, y in ((tl, rep), (rep, tl)):
            r = self._run(x, y)
            self.assertEqual(r.returncode, 2)
            self.assertIn("两参类型不一致", r.stdout)

    def test_json_nested_dir_created(self):
        """P2b：--json 落盘路径父目录不存在时自动创建。"""
        old, new = _tl(), _tl()
        new["audio_tracks"][1]["events"][0]["volume"] = 0.2
        a, b = self._write("a.json", old), self._write("b.json", new)
        out = str(Path(self.tmp.name) / "deep" / "nest" / "d.json")
        r = self._run(a, b, "--json", out)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(Path(out).exists())

    def test_nseg_env_followed(self):
        """P2a：VTX_VIDEO_SEGMENTS 与 composite 同源取值（切点集随 env 变化）。"""
        from vtx.timeline_diff import _nseg
        self.assertEqual(_nseg(), 8)
        os.environ["VTX_VIDEO_SEGMENTS"] = "4"
        try:
            self.assertEqual(_nseg(), 4)
        finally:
            del os.environ["VTX_VIDEO_SEGMENTS"]


if __name__ == "__main__":
    unittest.main()
