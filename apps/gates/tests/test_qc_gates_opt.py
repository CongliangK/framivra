#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_qc_gates_opt.py —— 方案三阶段1（qc/gates 精修）行为改动测试。

覆盖：
  ① loudness_cache 回执：measure_and_store 后 lookup 命中；文件变动（同尺寸换
     内容 / 改 size）→ 键失配 miss；测量失败（i/tp=None）不入回执；
  ② G1 批量化数值等价：lufs_batch 与逐窗 lufs 对同一批窗的 I 值集合一致
     （排序后比较，容差 0.05——批量完成序不保证，G1 只用中位/极差）；
  ③ G10 计数口径等值：同一合成 h264 mp4 的 -count_packets 与 -count_frames
     帧数相等（默认口径切换的等价性前提）；
  ④ qc 命中段免检（合成项目实跑 vtx prepare→video→audio→mux→qc，构造参考
     tests/execution/test_pipeline.py）：
     a) 首轮全过→记录生成；第二轮 qc 检查项数/顺序不变、4 项像素探测免检；
     b) 改写一个 vseg 段文件（同名字不同内容，stat 失配）→ 该段探测项实测、
        其他段项仍免检；
     c) final.mp4 stat 变（utime）→ 记录整体失效全检；
     d) --strict 免检禁用，检查项数与全检一致；
  ⑤ G7 黑场灵敏度回归：含 ≥0.03s 全黑段视频两口径（无回执合并 pass / 有回执
     blackdetect 单链）都须检出 ≥1 处——G7 逻辑未动，验证响度回执命中路径
     不吞黑场结论。

运行：repo 根 `bash scripts/run_all_tests.sh`（P2a 起统一运行器含 apps/*/tests 发现）；
本 app 内独立跑：`cd apps/gates && env python3 -m unittest discover -s tests -t . -v`
（本机 ZCode 环境必须 env python3 前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md；
unittest 非 pytest）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]  # apps/gates/（app 根；tests 随 app 迁入 P2a）
REPO = APP.parent.parent                   # repo 根（apps/gates → apps → repo）
sys.path.insert(0, str(REPO))              # repo 根（v2 §1.4 包导入，不再注入 repo/vtx）
sys.path.insert(0, str(APP))               # content_gates.py 非包成员，按路径导入

import content_gates              # noqa: E402（main() 有 __main__ 守卫，导入无副作用）
from vtx import loudness_cache   # noqa: E402


# ---------------------------------------------------------------- ① 响度回执
class TestLoudnessReceipt(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.media = self.root / "out" / "final.mp4"   # out/ 布局→回执落 root/cache/audio/
        self.media.parent.mkdir(parents=True)
        self.media.write_bytes(b"\0" * 100)

    def test_store_then_hit_and_miss_on_change(self):
        ran = {"n": 0}
        def run_fn():
            ran["n"] += 1
            return (-14.2, -1.3)
        r = loudness_cache.measure_and_store(self.media, run_fn)
        self.assertEqual((r["i_lufs"], r["tp_dbfs"]), (-14.2, -1.3))
        hit = loudness_cache.lookup(self.media)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["i_lufs"], -14.2)
        self.assertEqual(hit["tp_dbfs"], -1.3)
        self.assertEqual(ran["n"], 1)   # lookup 命中免测，run_fn 不再执行
        # 回执文件落在 <项目>/cache/audio/loudness_receipts.json，结构含全部声明字段
        rp = self.root / "cache" / "audio" / "loudness_receipts.json"
        self.assertTrue(rp.exists())
        ent = json.loads(rp.read_text(encoding="utf-8"))["entries"]
        self.assertEqual(len(ent), 1)
        e = next(iter(ent.values()))
        self.assertEqual({"path", "size", "mtime_ns", "digest",
                          "i_lufs", "tp_dbfs", "when"}, set(e))
        # 同尺寸换内容：首尾 64KB 采样哈希变 → 键失配 → miss
        self.media.write_bytes(b"\1" * 100)
        self.assertIsNone(loudness_cache.lookup(self.media))
        # 改 size：stat 变 → 键失配 → miss
        self.media.write_bytes(b"\2" * 128)
        self.assertIsNone(loudness_cache.lookup(self.media))

    def test_failed_measure_not_stored(self):
        r = loudness_cache.measure_and_store(self.media, lambda: (None, None))
        self.assertIsNone(r["i_lufs"])
        self.assertIsNone(loudness_cache.lookup(self.media))

    def test_missing_file_lookup_none(self):
        self.media.unlink()
        self.assertIsNone(loudness_cache.lookup(self.media))


# --------------------------------------------- ①b 跨进程读-改-写互斥（方案三阶段3）
class TestLoudnessCrossProcessLock(unittest.TestCase):
    """qc 与 content_gates 两进程并发 measure_and_store 不同键（方案三.9 发布链
    场景的 M4 实质风险）：无 flock 时后写者基于旧快照回写丢对方的笔（threading.Lock
    管不到跨进程，os.replace 只保证不撕裂不保证串行化）；持锁后两键必在。
    子进程经 `env python3` 拉起（argv[0] 劫持纪律，全局 AGENTS.md）。"""

    CHILD = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])   # repo 根（v2 §1.4 包导入）
from vtx import loudness_cache
root = Path(sys.argv[2]); tag = sys.argv[3]
for _ in range(2000):            # 起跑门：等对端 import/构造完（go 文件由测试侧落）
    if (root / "go").exists(): break
    time.sleep(0.005)
media = root / "out" / ("media_%s.bin" % tag)
for j in range(150):             # 高频读-改-写，最大化丢笔窗口
    loudness_cache.measure_and_store(media, lambda: (-14.0, -3.0), root=root)
"""

    def test_two_processes_no_lost_entry(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "out").mkdir()
        procs = []
        for tag in ("0", "1"):
            (root / "out" / f"media_{tag}.bin").write_bytes(b"\1" * 64)
            procs.append(subprocess.Popen(
                ["env", "python3", "-c", self.CHILD, str(REPO), str(root), tag],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(0.3)           # 两子进程都过 import 后放行
        (root / "go").write_text("go")
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0, "子进程异常退出")
        rp = root / "cache" / "audio" / "loudness_receipts.json"
        ent = json.loads(rp.read_text(encoding="utf-8"))["entries"]
        self.assertEqual(len(ent), 2, f"并发写丢笔（flock 失效？）：{sorted(ent)}")
        for e in ent.values():
            self.assertEqual((e["i_lufs"], e["tp_dbfs"]), (-14.0, -3.0))
        # 锁文件随回执目录存在（机制痕迹；fd 关闭即释放，不残留锁态）
        self.assertTrue((rp.parent / "loudness_receipts.json.lock").exists())


# --------------------------------------------- ② G1 批量化数值等价 + ③ 口径等值
@unittest.skipUnless(shutil.which("ffmpeg"), "G1 等价测试需要 ffmpeg")
class TestG1BatchEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.root = Path(cls._tmp.name)
        # 16s wav：响/轻 3.2s 交替（speech 窗 ≥2.5s、gap 窗 ≥3.0s，对齐 G1 筛选口径）
        cls.media = cls.root / "a.wav"
        fc = ("sine=frequency=330:duration=3.2,volume=-1dB[s0];"
              "sine=frequency=200:duration=3.2,volume=-22dB[s1];"
              "sine=frequency=440:duration=3.2,volume=-2dB[s2];"
              "sine=frequency=220:duration=3.2,volume=-21dB[s3];"
              "sine=frequency=550:duration=3.2,volume=-1.5dB[s4];"
              "[s0][s1][s2][s3][s4]concat=n=5:v=0:a=1[a]")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", fc,
                        "-map", "[a]", "-c:a", "pcm_s16le", str(cls.media)],
                       check=True)

    def test_batch_equals_per_window(self):
        speech = [(0.0, 3.2), (6.4, 9.6), (12.8, 16.0)]
        gaps = [(3.2, 6.4), (9.6, 12.8)]
        for windows in (speech, gaps):
            b = content_gates.lufs_batch(windows, self.media)
            self.assertIsNotNone(b, "批量路径不应失败（ffmpeg 正常）")
            s = [content_gates.lufs(a, bb, self.media) for a, bb in windows]
            self.assertNotIn(None, s)
            self.assertEqual(len(b), len(windows))
            # 完成序不保证：按值排序后比对（G1 只用中位/极差，与窗序无关）
            for x, y in zip(sorted(b), sorted(s)):
                self.assertAlmostEqual(x, y, delta=0.05)

    def test_batch_failure_returns_none(self):
        """任一批 ffmpeg 失败 → 整体 None（回落逐窗旧路径的触发条件）。"""
        self.assertIsNone(content_gates.lufs_batch([(0, 1)],
                                                    self.root / "nope.wav"))

    def test_empty_windows_return_empty(self):
        self.assertEqual(content_gates.lufs_batch([], self.media), [])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "G10 口径等值测试需要 ffmpeg+ffprobe")
class TestCountPacketsEquivalence(unittest.TestCase):
    def test_packets_equals_frames(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                            "-i", "testsrc2=s=320x180:r=30:d=2",
                            "-c:v", "libx264", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", str(p)], check=True)
            def cnt(flag, field):
                r = subprocess.run(["ffprobe", "-v", "error", flag,
                                    "-select_streams", "v:0",
                                    "-show_entries", f"stream={field}",
                                    "-of", "csv=p=0", str(p)],
                                   capture_output=True, text=True)
                return int(r.stdout.strip())
            pk = cnt("-count_packets", "nb_read_packets")
            fr = cnt("-count_frames", "nb_read_frames")
            self.assertGreater(pk, 0)
            self.assertEqual(pk, fr, "h264 每包恰一帧：两计数口径必须同值")


# ---------------------------------------------------------------- ④ qc 免检
def _run_vtx(tl: Path, *args, extra_env=None):
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run(
        ["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
         "-t", str(tl), *args],
        capture_output=True, text=True, cwd=str(REPO), env=env)


@unittest.skipUnless(shutil.which("ffmpeg"), "qc 免检集成需要 ffmpeg（真实渲染合成项目）")
class TestQcSegmentExemption(unittest.TestCase):
    """合成项目（180 帧，两源两 clip 底轨 + 后半段 pip 窗口 + 字幕 + voice 轨——
    qc 可全过形态，构造对齐 tests/execution/test_pipeline.py；pip 放 130-160 使 2 段切点
    落在 ~69 帧的静默带）实跑全链，再按序验证免检四性质。方法名 01-04 强制执行
    顺序（unittest 按名排序，状态跨用例递进）。"""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        root = Path(tmp.name)
        assets = root / "assets"; assets.mkdir(parents=True)
        for name, hz in (("a", 330), ("b", 550)):
            subprocess.run(["ffmpeg", "-v", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
                            "-f", "lavfi", "-i", f"sine=frequency={hz}:duration=2",
                            "-c:v", "libx264", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                            str(assets / f"{name}.mp4")], check=True)
        dur = 180   # 6s；pip 放后半段（130-160）使 2 段切点在 ~69 帧找到静默帧
        cls.proj = root / "proj"; cls.proj.mkdir()
        cls.tl = cls.proj / "timeline.json"
        cls.tl.write_text(json.dumps({
            "version": 2,
            "project": {"fps": 30, "width": 640, "height": 360,
                        "duration_frames": dur},
            "sources": [{"id": n, "path": str(assets / f"{n}.mp4")}
                        for n in ("a", "b")],
            "video_tracks": [
                {"name": "base", "kind": "aroll", "clips": [
                    {"src": "a", "src_in": 0, "src_out": dur // 2,
                     "dst_in": 0, "dur": dur // 2},
                    {"src": "b", "src_in": 0, "src_out": dur - dur // 2,
                     "dst_in": dur // 2, "dur": dur - dur // 2}]},
                {"name": "pip", "kind": "pip", "clips": [
                    {"src": "b", "src_in": 0, "src_out": 30,
                     "dst_in": 130, "dur": 30, "scale": 0.3, "x": 10, "y": 10}]}],
            "subtitles": [{"in": 10, "out": 40, "text": "免检集成测试"}],
            "audio_tracks": [
                {"name": "voice", "events": [
                    {"src": "a", "in": 0, "out": dur // 2, "volume": 0.6},
                    {"src": "b", "in": dur // 2, "out": dur, "volume": 0.6}]}]
        }, ensure_ascii=False), encoding="utf-8")
        # 全链（无 gfx 轨 → 不需要 Chrome）：VTX_VIDEO_SEGMENTS=2 → cache/vseg 两段
        for cmd in ("prepare", "video", "audio", "mux", "qc"):
            r = _run_vtx(cls.tl, cmd, extra_env={"VTX_VIDEO_SEGMENTS": "2"})
            assert r.returncode == 0, f"{cmd} 阶段失败:\n{r.stdout}\n{r.stderr}"
        rep = json.loads((cls.proj / "out" / "qc_report.json")
                         .read_text(encoding="utf-8"))
        assert all(x["pass"] for x in rep["checks"]), \
            f"首轮 qc 须全过（免检记录前提）: {[x for x in rep['checks'] if not x['pass']]}"
        cls.first_names = [x["name"] for x in rep["checks"]]
        cls.first_report = rep

    def _qc(self, *extra):
        return _run_vtx(self.tl, "qc", *extra)

    def _report(self):
        return json.loads((self.proj / "out" / "qc_report.json")
                          .read_text(encoding="utf-8"))

    def test_01_record_written_and_warm_exempt(self):
        # 首轮全过 → 记录已生成（segs ≥2、checks 全 True）
        rec = json.loads((self.proj / "cache" / "qc_pass_segs.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(rec["version"], 2)
        self.assertGreaterEqual(len(rec["segs"]), 2)
        self.assertTrue(all(rec["checks"].values()))
        self.assertEqual(self.first_report.get("skipped_probes"), [])
        # 第二轮：检查项数/顺序/名称不变，4 项像素探测（cut×2+win+free）免检
        r = self._qc()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = self._report()
        self.assertEqual([x["name"] for x in rep["checks"]], self.first_names)
        ex = rep["skipped_probes"]
        self.assertEqual(len(ex), 4, f"应免检 4 项像素探测: {ex}")
        self.assertTrue(all("免检" in next(c for c in rep["checks"]
                                           if c["name"] == e["name"])["detail"]
                            for e in ex))
        self.assertIn("⚠ 免检 4 项", r.stdout)
        # 响度走 loudness_cache 回执（首轮 qc 已归档）：命中免测
        loud = next(x for x in rep["checks"] if x["name"].startswith("响度归一"))
        self.assertIn("（回执命中，免测）", loud["detail"])

    def test_02_seg_tamper_disables_that_segs_exempt(self):
        rec = json.loads((self.proj / "cache" / "qc_pass_segs.json")
                         .read_text(encoding="utf-8"))
        seg0 = rec["segs"][0]
        self.assertEqual(seg0["a"], 0)
        # 本时间线的探测帧位：cut dst=0→帧 2（应落在段0）；
        # cut dst=90→帧 92、win→帧 145、free→帧 179（应落在段1）
        self.assertLess(2, seg0["b"])
        self.assertLessEqual(seg0["b"], 92)
        p = self.proj / "cache" / "vseg" / seg0["name"]
        p.write_bytes(b"TAMPERED" + p.read_bytes()[:64])   # 同名不同内容，stat 变
        r = self._qc()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = self._report()
        self.assertEqual([x["name"] for x in rep["checks"]], self.first_names)
        ex_names = {x["name"] for x in rep["skipped_probes"]}
        self.assertNotIn("底轨切点帧比对 dst=0 src=a:2", ex_names)   # 段0 失配 → 实测
        self.assertIn("底轨切点帧比对 dst=90 src=b:2", ex_names)     # 段1 命中 → 仍免检
        self.assertIn("窗口 clip pip @frame 145 生效", ex_names)
        self.assertIn("空窗无叠加泄漏 @frame 179", ex_names)
        cut0 = next(x for x in rep["checks"] if "dst=0" in x["name"])
        self.assertIn("平均像素差", cut0["detail"])   # 实测路径 detail 恢复旧格式

    def test_03_final_stat_change_invalidates_all(self):
        final = self.proj / "out" / "final.mp4"
        os.utime(final, times=(1600000000, 1600000000))   # mtime_ns 必变
        r = self._qc()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = self._report()
        self.assertEqual(rep["skipped_probes"], [], "final stat 失配 → 记录整体失效全检")
        self.assertEqual([x["name"] for x in rep["checks"]], self.first_names)
        self.assertFalse(any("免检" in x["detail"] for x in rep["checks"]))

    def test_04_strict_disables_exempt(self):
        r = self._qc("--strict")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = self._report()
        self.assertEqual(rep["skipped_probes"], [])
        # 验收计数断言：--strict 检查项数与全检一致
        self.assertEqual(len(rep["checks"]), len(self.first_names))
        self.assertFalse(any("免检" in x["detail"] for x in rep["checks"]))


# ---------------------------------------------------------------- ⑤ G7 黑场
@unittest.skipUnless(shutil.which("ffmpeg"), "G7 黑场回归需要 ffmpeg")
class TestG7BlackSensitivity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.root = Path(tmp.name)
        cls.media = cls.root / "out" / "final.mp4"   # out/ 布局→回执落 root/cache/audio/
        cls.media.parent.mkdir(parents=True)
        # testsrc 1.5s + 全黑 0.5s（≥0.03s 门）+ testsrc 1.5s，带 aac 音轨
        subprocess.run(["ffmpeg", "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1.5",
                        "-f", "lavfi", "-i", "color=black:s=320x180:r=30:d=0.5",
                        "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1.5",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3.5",
                        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                        "-map", "[v]", "-map", "3:a",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                        str(cls.media)], check=True)

    def _gates(self):
        return subprocess.run(
            ["env", "python3", "-X", "utf8",
             str(APP / "content_gates.py"),
             "-t", str(self.root / "no_timeline.json"),   # 无 timeline：只跑 G2/G3/G7
             "--media", str(self.media)],
            capture_output=True, text=True, cwd=str(self.root))

    def test_no_receipt_merged_pass_detects_black(self):
        r = self._gates()
        self.assertEqual(r.returncode, 1, "含黑段 → G7 ✗ → exit 1")
        self.assertIn("[timing] 全片解码合并 pass（G2/G3+G7）", r.stdout)
        self.assertIn("✗ G7 零黑帧", r.stdout)
        self.assertIn("命中 1 处", r.stdout)

    def test_receipt_hit_blackdetect_only_still_detects_black(self):
        # 归档一份（伪）响度回执 → gates 走「回执命中」黑场单链路径，G7 结论不丢
        loudness_cache.measure_and_store(self.media, lambda: (-14.2, -2.0))
        r = self._gates()
        self.assertEqual(r.returncode, 1, "含黑段 → G7 ✗ → exit 1（不受回执影响）")
        self.assertIn("[timing] 全片解码黑场 pass（G7，响度回执命中免音频测量）",
                      r.stdout)
        self.assertIn("✗ G7 零黑帧", r.stdout)
        self.assertIn("命中 1 处", r.stdout)
        self.assertIn("I=-14.2", r.stdout)   # G2/G3 直接用回执值（命中免测）


# ---------------------------------------------------------------- ⑥ --out JSON 留档
class TestOutJsonArchive(unittest.TestCase):
    """--out PATH（2026-09-14）：门结论/skip/实测/输入指纹/时间戳落 JSON；
    默认不写（兼容不变）。无成片 + --skip G1 场景免 ffmpeg 走纯留档路径。"""

    def _gates(self, root: Path, *extra):
        return subprocess.run(
            ["env", "python3", "-X", "utf8", str(APP / "content_gates.py"),
             "-t", str(root / "timeline.json"),
             "--media", str(root / "nope.mp4"), "--skip", "G1", *extra],
            capture_output=True, text=True, cwd=str(root))

    def test_out_writes_structured_report(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "timeline.json").write_text(json.dumps(
                {"version": 2, "project": {"fps": 30, "duration_frames": 90,
                                            "width": 640, "height": 360},
                 "sources": [], "video_tracks": [], "subtitles": [],
                 "audio_tracks": []}, ensure_ascii=False), encoding="utf-8")
            out = root / "out" / "content_gates.json"
            r = self._gates(root, "--out", str(out))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("[out] gates 留档", r.stdout)
            rep = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(rep["pass"])
            self.assertEqual(rep["tool"], "apps/gates/content_gates.py")
            self.assertRegex(rep["timestamp"], r"^\d{4}-\d{2}-\d{2} ")
            # skip 留档（G1 显式 skip + G2/G3/G7/G10 无成片 skip）
            joined = " ".join(rep["skipped"])
            self.assertIn("SKIP G1", joined)
            self.assertIn("SKIP G2/G3", joined)
            # 输入指纹：timeline md5/size/mtime + frames/fps；media 不存在如实记录
            self.assertEqual(rep["timeline"]["frames"], 90)
            self.assertEqual(rep["timeline"]["fps"], 30)
            self.assertTrue(rep["timeline"]["exists"])
            self.assertEqual(len(rep["timeline"]["md5"]), 32)
            self.assertFalse(rep["media"]["exists"])
            # 参数快照（复跑可追溯阈值口径）
            self.assertEqual(rep["params"]["g3_tp"], -1.0)
            self.assertEqual(rep["params"]["skip"], ["G1"])

    def test_default_no_out_file(self):
        # 默认（无 --out）不落任何留档件——既有行为兼容
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "timeline.json").write_text(json.dumps(
                {"version": 2, "project": {"fps": 30, "duration_frames": 90,
                                            "width": 640, "height": 360},
                 "sources": [], "video_tracks": [], "subtitles": [],
                 "audio_tracks": []}, ensure_ascii=False), encoding="utf-8")
            r = self._gates(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("gates 留档", r.stdout)
            self.assertEqual(list(root.glob("**/*.json")), [root / "timeline.json"])


if __name__ == "__main__":
    unittest.main()
