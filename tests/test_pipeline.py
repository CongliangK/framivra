#!/usr/bin/env python3
"""tests/test_pipeline.py —— `vtx all` 依赖图流水线（方案二阶段2）单元测试。

覆盖：
  ① _stage_receipt_name：draft 模式 video/mux → video_draft/mux_draft，其余原名；
  ② _pipeline_enabled：缺省 True、"1"→True、"0"→False、"false"→True——开关语义是
     「仅显式 "0" 回退线性序列」，其余任意值走依赖图（env 参数注入，不污染进程环境）；
  ③ receipt 并发锁：ThreadPoolExecutor 8 线程并发各调 c.receipt("s0"…"s7") × 10 次，
     join 后 receipt.json 必须含全部 8 个 stage 且 ok——receipt/begin_stage 的读-改-写
     现持 Ctx._receipt_lock，无锁时并发写互相覆盖会丢笔（真丢数据，非理论洁癖）；
  ④ 流水线集成（可选，VTX_PIPE_INTEG=1 门控，对齐 VTX_RANGE_INTEG 惯例；需 ffmpeg
     与 Chrome）：合成项目实跑 `vtx all` 三遍——首遍全冷断言 receipt 全阶段 ok、
     final.mp4 帧数精确、cache/manifest.json 合法且含 prepare/gfx/video 各阶段；
     随后两遍温态断言无 seg 预编码任务行与 gfx「渲染」行（全缓存命中 = 无重复渲染
     同内容键）且退出码 0。
运行：env python3 -m unittest discover tests -v（本机 ZCode 环境必须 env python3 前缀：
AppImage 劫持 argv[0]，裸 python3 的 sys.executable 指向错误解释器，见全局 AGENTS.md）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "vtx"))

from vtx import _stage_receipt_name, _pipeline_enabled


class TestStageReceiptName(unittest.TestCase):
    def test_draft_video_mux(self):
        self.assertEqual(_stage_receipt_name("video", True), "video_draft")
        self.assertEqual(_stage_receipt_name("mux", True), "mux_draft")

    def test_nondraft_and_other_stages(self):
        self.assertEqual(_stage_receipt_name("video", False), "video")
        self.assertEqual(_stage_receipt_name("mux", False), "mux")
        # audio 不分 draft（draft 只影响视频封装产物），qc 同理
        self.assertEqual(_stage_receipt_name("audio", True), "audio")
        self.assertEqual(_stage_receipt_name("qc", False), "qc")
        self.assertEqual(_stage_receipt_name("gfx", True), "gfx")


class TestPipelineEnabled(unittest.TestCase):
    def test_default_on(self):
        self.assertTrue(_pipeline_enabled({}))
        self.assertTrue(_pipeline_enabled())   # 缺省读进程环境（测试机未设即 True）

    def test_explicit_zero_falls_back(self):
        self.assertFalse(_pipeline_enabled({"VTX_PIPELINE": "0"}))

    def test_other_values_are_pipeline(self):
        # 开关语义单值精确：仅 "0" 回退，"false"/空串等一律走依赖图（避免布尔猜测）
        self.assertTrue(_pipeline_enabled({"VTX_PIPELINE": "1"}))
        self.assertTrue(_pipeline_enabled({"VTX_PIPELINE": "false"}))
        self.assertTrue(_pipeline_enabled({"VTX_PIPELINE": ""}))


def _synth_assets(root: Path):
    """ffmpeg testsrc+sine 生成两个 640x360@30 2s mp4（含 aac 音轨），返回路径表。"""
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    assets = root / "assets"; assets.mkdir(parents=True, exist_ok=True)
    for name, hz in (("a", 330), ("b", 550)):
        p = assets / f"{name}.mp4"
        subprocess.run([ff, "-v", "error", "-y",
                        "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=30:d=2",
                        "-f", "lavfi", "-i", f"sine=frequency={hz}:duration=2",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(p)], check=True)
    return assets


def _synth_timeline(root: Path, assets: Path, dur=90) -> dict:
    """最小合成项目：两源两 clip 底轨 + pip + 一条字幕 + 一条音轨（qc 可过的形态）。"""
    return {"version": 2,
            "project": {"fps": 30, "width": 640, "height": 360, "duration_frames": dur},
            "sources": [{"id": n, "path": str(assets / f"{n}.mp4")} for n in ("a", "b")],
            "video_tracks": [
                {"name": "base", "kind": "aroll", "clips": [
                    {"src": "a", "src_in": 0, "src_out": dur // 2, "dst_in": 0, "dur": dur // 2},
                    {"src": "b", "src_in": 0, "src_out": dur - dur // 2,
                     "dst_in": dur // 2, "dur": dur - dur // 2}]},
                {"name": "pip", "kind": "pip", "clips": [
                    {"src": "b", "src_in": 0, "src_out": 30, "dst_in": 20, "dur": 30,
                     "scale": 0.3, "x": 10, "y": 10}]}],
            "subtitles": [{"in": 10, "out": 40, "text": "流水线集成测试"}],
            "audio_tracks": [
                {"name": "voice", "events": [
                    {"src": "a", "in": 0, "out": dur // 2, "volume": 0.6},
                    {"src": "b", "in": dur // 2, "out": dur, "volume": 0.6}]}]}


def _run_vtx(tl: Path, *args, extra_env=None):
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run(
        ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
         "-t", str(tl), *args],
        capture_output=True, text=True, cwd=str(REPO), env=env)


@unittest.skipUnless(shutil.which("ffmpeg"),
                     "receipt 并发测试需要 ffmpeg（生成带媒体的合成项目）")
class TestReceiptConcurrentLock(unittest.TestCase):
    """Ctx._receipt_lock：8 线程 × 10 次 receipt 读-改-写，全部 8 笔必须都在。

    receipt() 是 receipt.json 的「读-改-写」，波内 gfx/audio 两路并发回执若无锁，
    后写者会用自己读到的旧快照覆盖前写者的笔——丢笔是真实数据风险。锁合入前
    本测试在大并发下间歇性失败（少于 8 个 stage）。
    """

    def test_8_threads_no_lost_receipts(self):
        from infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            assets = _synth_assets(root)
            tl_path = root / "timeline.json"
            tl_path.write_text(json.dumps(_synth_timeline(root, assets, dur=30)),
                               encoding="utf-8")
            c = Ctx(tl_path)
            def hammer(i):
                for _ in range(10):
                    c.receipt(f"s{i}", True, "并发锁测试")
            with ThreadPoolExecutor(8) as ex:
                list(ex.map(hammer, range(8)))
            rec = json.loads((c.out / "receipt.json").read_text(encoding="utf-8"))
            stages = rec["stages"]
            for i in range(8):
                self.assertIn(f"s{i}", stages,
                              f"s{i} 的回执被并发写覆盖丢失（锁失效？）")
                self.assertTrue(stages[f"s{i}"]["ok"])


@unittest.skipUnless(os.environ.get("VTX_PIPE_INTEG") == "1",
                     "可选集成测试：VTX_PIPE_INTEG=1 且需 ffmpeg + Chrome（真实全链渲染）")
class TestPipelineIntegration(unittest.TestCase):
    """合成项目实跑 `vtx all` 三遍：冷遍验证产物/回执/manifest，温态两遍验证
    全缓存命中（无 seg 预编码任务行、无 gfx「渲染」行 = 无重复渲染同内容键）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        assets = _synth_assets(root)
        assert assets is not None, "集成测试需要 ffmpeg"
        cls.proj = root / "proj"; cls.proj.mkdir()
        # 一块极简 gfx 页（纯色全屏，qc gfx 窗口检查可过）；html 相对项目根解析
        gfxdir = cls.proj / "gfx"; gfxdir.mkdir()
        (gfxdir / "page.html").write_text(
            "<html><body style='margin:0;background:#f80'></body></html>", encoding="utf-8")
        tl = _synth_timeline(root, assets, dur=90)
        tl["video_tracks"].append(
            {"name": "ov", "kind": "gfx",
             "clips": [{"src": "gfx/page.html", "dst_in": 30, "dur": 30}]})
        cls.tl = cls.proj / "timeline.json"
        cls.tl.write_text(json.dumps(tl), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _all(self, extra_env=None):
        env = {"VTX_VIDEO_SEGMENTS": "2"}
        env.update(extra_env or {})
        return _run_vtx(self.tl, "all", extra_env=env)

    def _assert_receipt_all_ok(self):
        rec = json.loads((self.proj / "out" / "receipt.json").read_text(encoding="utf-8"))
        expect = {"validate", "audit", "prepare", "gfx", "video", "audio", "mux", "qc"}
        self.assertTrue(expect <= set(rec["stages"]),
                        f"回执缺阶段：{expect - set(rec['stages'])}")
        bad = {k: v for k, v in rec["stages"].items() if not v.get("ok")}
        self.assertFalse(bad, f"回执存在失败阶段：{bad}")

    def test_cold_then_two_warm_runs(self):
        # —— 第一遍：冷跑（全链渲染）——
        r1 = self._all()
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        self._assert_receipt_all_ok()
        fp = shutil.which("ffprobe")
        self.assertIsNotNone(fp)
        pr = subprocess.run([fp, "-v", "error", "-print_format", "json",
                             "-select_streams", "v:0", "-show_entries",
                             "stream=nb_frames", "-show_entries", "format=duration",
                             str(self.proj / "out" / "final.mp4")],
                            capture_output=True, text=True)
        d = json.loads(pr.stdout)["streams"][0]
        nb = int(d.get("nb_frames") or 0) or round(float(
            json.loads(pr.stdout)["format"]["duration"]) * 30)
        self.assertEqual(nb, 90, "final.mp4 帧数必须精确等于 duration_frames")
        man_p = self.proj / "cache" / "manifest.json"
        man = json.loads(man_p.read_text(encoding="utf-8"))   # 合法 JSON
        stages_seen = {e.get("stage") for e in man.get("entries", [])}
        self.assertTrue({"prepare", "gfx", "video"} <= stages_seen,
                        f"manifest entries 缺阶段，实见：{stages_seen}")
        # —— 第二/三遍：温态（全缓存命中，无重复渲染同内容键）——
        for i in (2, 3):
            rw = self._all()
            self.assertEqual(rw.returncode, 0, rw.stdout + rw.stderr)
            self.assertNotIn("seg 预编码", rw.stdout,
                             "温态不应再有 seg 预编码任务行（段缓存全命中）")
            self.assertNotIn(": 渲染 ", rw.stdout,
                             "温态不应再有 gfx「渲染」行（页缓存全命中）")
            json.loads((self.proj / "cache" / "manifest.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
