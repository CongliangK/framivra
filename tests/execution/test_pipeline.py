#!/usr/bin/env python3
"""tests/execution/test_pipeline.py —— `vtx all` 依赖图流水线（方案二阶段2）单元测试。

覆盖：
  ① _stage_receipt_name：draft 模式 video/mux → video_draft/mux_draft，其余原名；
  ② _pipeline_enabled：缺省 True、"1"→True、"0"→False、"false"→True——开关语义是
     「仅显式 "0" 回退线性序列」，其余任意值走依赖图（env 参数注入，不污染进程环境）；
  ③ receipt 并发锁：ThreadPoolExecutor 8 线程并发各调 c.receipt("s0"…"s7") × 10 次，
     join 后 receipt.json 必须含全部 8 个 stage 且 ok——receipt/begin_stage 的读-改-写
     现走 fstxn 事务（B06 起：进程内 per-path 锁 + receipt.json.lock 的 flock；
     原 Ctx._receipt_lock 已随锁下沉 fstxn 移除），无锁时并发写互相覆盖会丢笔
     （真丢数据，非理论洁癖）；
  ④ 流水线集成（可选，VTX_PIPE_INTEG=1 门控，对齐 VTX_RANGE_INTEG 惯例；需 ffmpeg
     与 Chrome）：合成项目实跑 `vtx all` 三遍——首遍全冷断言 receipt 全阶段 ok、
     final.mp4 帧数精确、cache/manifest.json 合法且含 prepare/gfx/video 各阶段；
     随后两遍温态断言无 seg 预编码任务行与 gfx「渲染」行（全缓存命中 = 无重复渲染
     同内容键）且退出码 0。
运行：env python3 -m unittest discover tests -v（本机 ZCode 环境必须 env python3 前缀：
AppImage 劫持 argv[0]，裸 python3 的 sys.executable 指向错误解释器，见全局 AGENTS.md）
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入，不再注入 repo/vtx）

from vtx import _stage_receipt_name        # __init__ 再导出（v2 §1.4）
from vtx.cli import _pipeline_enabled


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
    """receipt 并发锁（B06 起在 fstxn：per-path 线程锁 + flock）：8 线程 × 10 次
    receipt 读-改-写，全部 8 笔必须都在。

    receipt() 是 receipt.json 的「读-改-写」，波内 gfx/audio 两路并发回执若无锁，
    后写者会用自己读到的旧快照覆盖前写者的笔——丢笔是真实数据风险。锁合入前
    本测试在大并发下间歇性失败（少于 8 个 stage）。
    """

    def test_8_threads_no_lost_receipts(self):
        from vtx.infra import Ctx
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


def _stage_heads(out: str):
    """从 vtx 输出提取 `== <label> ==` 阶段头行序（波成员 gfx/audio/video 预编码
    由 run_wave 线程体打印，同格式——`--until` 截断断言用）。"""
    return [ln[3:-3] for ln in out.splitlines()
            if ln.startswith("== ") and ln.endswith(" ==")]

@unittest.skipUnless(shutil.which("ffmpeg"),
                     "--until 测试需要 ffmpeg（生成带媒体的合成项目）")
class TestUntilFlag(unittest.TestCase):
    """`all --until <stage>`（方案三阶段3）：三种阶段族的截断序列 + 用法错误。

    流水线映射：{validate,audit,prepare}→只跑串行头；{gfx,video}→串行头+完整并行波
    （audio 是波成员不拆）+（video 时）video 尾；{mux,qc}→与缺省全链同一编排。
    线性模式（VTX_PIPELINE=0）按 STAGES 序字面截断（until=video 不含 audio）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        assets = _synth_assets(root)
        assert assets is not None, "setUpClass 已 skipUnless 保证 ffmpeg 在位"
        self.tl = root / "timeline.json"
        # dur=90：_synth_timeline 的 pip clip（dst_in=20/dur=30）要求 ≥50 帧
        self.tl.write_text(json.dumps(_synth_timeline(root, assets, dur=90)),
                           encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _dry(self, *args, extra_env=None):
        return _run_vtx(self.tl, "all", "--dry-run", *args, extra_env=extra_env)

    def test_until_serial_head_bin(self):
        # until=prepare：只跑串行头，不起波（无并行波行、无 gfx/video 头）
        r = self._dry("--until", "prepare")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_stage_heads(r.stdout), ["validate", "audit", "prepare"])
        self.assertNotIn("并行波", r.stdout)

    def test_until_gfx_stops_after_wave(self):
        # until=gfx：串行头 + 完整波（gfx‖audio‖预编码三路全跑），video 尾不跑
        r = self._dry("--until", "gfx")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        heads = _stage_heads(r.stdout)
        self.assertEqual(heads[:3], ["validate", "audit", "prepare"])
        wave_members = {h for h in heads[3:]}
        self.assertEqual(wave_members, {"gfx", "audio", "video 预编码"},
                         "波内三路必须全跑（audio 是波成员不拆）")
        self.assertNotIn("video", wave_members)   # 尾部 video 不跑（≠「video 预编码」）

    def test_until_video_runs_wave_and_video_tail(self):
        # until=video：串行头 + 波 + video 尾；mux/qc 不跑（R4 冷链在此截断，
        # v2 §1.2 后音频已含限幅，后续直接 mux → qc）
        r = self._dry("--until", "video")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        heads = _stage_heads(r.stdout)
        self.assertEqual(heads[:3], ["validate", "audit", "prepare"])
        self.assertEqual(heads[-1], "video")
        self.assertTrue({("gfx"), ("audio"), ("video 预编码")} <= set(heads[3:-1]))
        self.assertNotIn("mux", heads)
        self.assertNotIn("qc", heads)

    def test_until_mux_qc_same_topology_as_default(self):
        # until=mux：与缺省全链同一编排，止于 mux（无 qc）；until=qc = 缺省全链
        for until, expect_last, has_qc in (("mux", "mux", False), ("qc", "qc", True)):
            r = self._dry("--until", until)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            heads = _stage_heads(r.stdout)
            self.assertEqual(heads[-1], expect_last)
            self.assertIn("qc", heads) if has_qc else self.assertNotIn("qc", heads)

    def test_linear_truncation_follows_stages_order(self):
        # VTX_PIPELINE=0：按 STAGES 序字面截断——until=video 不含 audio（audio 在
        # 线性序中位于 video 之后）；until=mux 自然包含 audio
        r = self._dry("--until", "video", extra_env={"VTX_PIPELINE": "0"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_stage_heads(r.stdout),
                         ["validate", "audit", "prepare", "gfx", "video"])
        r2 = self._dry("--until", "mux", extra_env={"VTX_PIPELINE": "0"})
        self.assertEqual(_stage_heads(r2.stdout),
                         ["validate", "audit", "prepare", "gfx", "video", "audio", "mux"])

    def test_non_all_rejected(self):
        r = _run_vtx(self.tl, "video", "--until", "video", "--dry-run")
        self.assertEqual(r.returncode, 2)
        self.assertIn("仅在 all 命令下可用", r.stdout + r.stderr)

    def test_invalid_stage_rejected(self):
        for bad in ("bogus", "audio"):   # audio 是波内成员，不作独立终点
            r = _run_vtx(self.tl, "all", "--until", bad, "--dry-run")
            self.assertEqual(r.returncode, 2, f"--until {bad} 应退出码 2")
            self.assertIn("非法阶段名", r.stdout + r.stderr)


class TestAllFinishSurvivesQcFailure(unittest.TestCase):
    """2026-09-14 裁定回归：`vtx all` 内 qc 失败不阻断 finish 收尾钩子。

    背景：qc 阶段失败以 `sys.exit(1)` 收尾（qc.py 尾部，报告/回执已先落盘），
    历史上 SystemExit 直接冒出 `main`，跳过缝 D 的 run_finish——存量假阴性项目
    （如 voice-ime c4_mini）永远到不了 finish，blender-bridge 自动导出失效。
    裁定：qc 失败不阻断 finish（产物已就绪），退出码按 qc/finish 各自失败态
    如实汇总（任一失败 exit 1）；更早阶段异常仍即时终止。

    手法：进程内直调 cli.main()，STAGES 整体换成伪阶段（只写 receipt，不渲染），
    run_finish 换成记录调用的伪钩子——纯编排逻辑测试，无 ffmpeg/Chrome 依赖。
    流水线（缺省）与线性回退（VTX_PIPELINE=0）两路径各覆盖。"""

    @staticmethod
    def _mk_stage(name, ok=True):
        def fn(c, *args, **kwargs):
            c.receipt(name, ok, "伪阶段")
            if not ok:
                sys.exit(1)   # 对齐真 qc：报告/回执写完再退
        return fn

    def _run_main(self, tl_path, qc_ok=True, linear=False, argv=None):
        """伪阶段全链跑 cli.main()，返回 (exit_code, finish_calls, stdout, receipt)。"""
        import vtx.cli as cli_mod
        import vtx.pluginlib.hooks as pl_hooks
        calls = []

        def fake_finish(c):
            calls.append(c)
            return []

        stages = [(n, self._mk_stage(n, ok=(qc_ok if n == "qc" else True)))
                  for n in ("validate", "audit", "prepare", "gfx", "video",
                            "audio", "mux", "qc")]
        env = {"VTX_PIPELINE": "0"} if linear else {}
        buf = io.StringIO()
        with mock.patch.object(cli_mod, "STAGES", stages), \
             mock.patch.object(cli_mod, "build_video", self._mk_stage("video")), \
             mock.patch.object(cli_mod, "prepare_layers", lambda c: []), \
             mock.patch.object(pl_hooks, "run_finish", fake_finish), \
             mock.patch.dict(os.environ, env), \
             mock.patch.object(sys, "argv", argv or ["vtx.py", "-t", str(tl_path), "all"]), \
             contextlib.redirect_stdout(buf):
            if not linear:
                os.environ.pop("VTX_PIPELINE", None)   # 消除外层环境污染
            try:
                cli_mod.main()
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        rp = tl_path.parent / "out" / "receipt.json"
        rec = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None
        return code, calls, buf.getvalue(), rec

    @staticmethod
    def _write_tl(root: Path) -> Path:
        tl = {"version": 2,
              "project": {"fps": 30, "duration_frames": 90, "width": 640, "height": 360},
              "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
        p = root / "timeline.json"
        p.write_text(json.dumps(tl), encoding="utf-8")
        return p

    def test_pipeline_qc_fail_still_finishes_exit1(self):
        # (a) 流水线路径：qc 失败 → finish 仍被调用，退出码 1（qc 的码如实汇总）
        with tempfile.TemporaryDirectory() as d:
            tl = self._write_tl(Path(d))
            code, calls, out, rec = self._run_main(tl, qc_ok=False, linear=False)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1, "qc 失败后 finish 钩子必须仍被执行")
        self.assertFalse(rec["stages"]["qc"]["ok"], "qc 失败须如实落回执")
        self.assertIn("不阻断 finish", out)

    def test_pipeline_qc_pass_finishes_normally(self):
        # (b) 防回归：qc 全绿 → finish 照常、退出码 0
        with tempfile.TemporaryDirectory() as d:
            tl = self._write_tl(Path(d))
            code, calls, out, rec = self._run_main(tl, qc_ok=True, linear=False)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(rec["stages"]["qc"]["ok"])

    def test_linear_qc_fail_still_finishes_exit1(self):
        # 线性回退路径（VTX_PIPELINE=0）同裁定
        with tempfile.TemporaryDirectory() as d:
            tl = self._write_tl(Path(d))
            code, calls, out, rec = self._run_main(tl, qc_ok=False, linear=True)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1, "线性路径 qc 失败后 finish 也必须执行")
        self.assertFalse(rec["stages"]["qc"]["ok"])

    def test_single_qc_command_exit_unchanged(self):
        # 单阶段 `vtx qc`：退出码冒泡语义不变（不经 all 的捕获路径），且不触发 finish
        with tempfile.TemporaryDirectory() as d:
            tl = self._write_tl(Path(d))
            code, calls, out, rec = self._run_main(
                tl, qc_ok=False, argv=["vtx.py", "-t", str(tl), "qc"])
        self.assertEqual(code, 1)
        self.assertEqual(calls, [], "单阶段命令不得触发 finish 钩子")


if __name__ == "__main__":
    unittest.main()
