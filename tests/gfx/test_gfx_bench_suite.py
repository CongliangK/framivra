#!/usr/bin/env python3
"""tests/gfx/test_gfx_bench_suite.py —— gfx 基准套件冻结完整性门（无 Chrome，秒级）

被测对象 tests/gfx_bench_suite/suite_manifest.json（issue #106 冻结件）：本测试是
冻结契约的常驻守卫，任何对场景定义的漂移（帧数/序列/页面/指纹/禁 API）在这里
显形失败。重负载验证（真实 Chrome CDP 渲染 + DoD 幂等门）在
tests/gfx_bench_suite/smoke.py（env VTX_TEST_CHROME 门控，手动/战役期执行），
本文件刻意零外部依赖、零 skip 门（ci_checks static 媒体清单无需注册）。

守护面：
  ① manifest 骨架：S1–S8 齐、冻结标记、全局规格 1920x1080@30fps；
  ② issue #106 表格口径逐场景断言：S1=90+100 / S2=180×2 / S3=300 / S4a=150×3 /
     S4b=120 / S5=300 / S6=600；
  ③ 页面存在性 + sha256_16 指纹全等（上游模板演化 ⇒ 指纹失配 ⇒ 冻结破裂显形）；
  ④ S6 分段/延迟注入/调度序列与 issue 原文逐项全等（random_jump 原样冻结、
     reverse_100 可由 python_expr 重建）；
  ⑤ S7 工作负载：14 页枚举、轮转 1920 帧、5000 帧单 Worker 分配表精确求和、
     4/7 两并发腿、RSS 预算 ≤16GiB；
  ⑥ S8 双轨不变量：28 页 / 12024 帧 Micro、16746 帧 Macro、bench_p2 存档口径；
  ⑦ 帧驱动铁律静态扫描（全部 19 页）：禁 CSS animation/transition 属性、
     Date.now(、Math.random(、requestAnimationFrame、@keyframes；
  ⑧ 钩子契约：仅 S5/S6 实现 window.__vtx_render 赋值；全部页面禁止
     __vtx_static_spans / __vtx_export_png 赋值（防绕过被测代码路径）；
  ⑨ DoD 幂等门方法冻结：run_a=[0,1,2,3] vs run_b=[99,2]，Tier-1 max_abs_diff==0。

运行：python3 -m unittest tests.gfx.test_gfx_bench_suite
"""
import contextlib
import hashlib
import importlib.util
import io
import os
import sys
import tempfile
import json
import re
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUITE = REPO / "tests" / "gfx_bench_suite"
MANIFEST = SUITE / "suite_manifest.json"

BANNED_PATTERNS = (
    re.compile(r"animation\s*:"),
    re.compile(r"transition\s*:"),
    re.compile(r"Date\.now\s*\("),
    re.compile(r"Math\.random\s*\("),
    re.compile(r"requestAnimationFrame"),
    re.compile(r"@keyframes"),
)


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def scenario(man, sid):
    for s in man["scenarios"]:
        if s["id"] == sid:
            return s
    raise KeyError(sid)


class TestManifestSkeleton(unittest.TestCase):
    def test_frozen_header(self):
        man = load_manifest()
        self.assertEqual(man["suite"], "gfx_bench_suite")
        self.assertEqual(man["version"], "1.0.0")
        self.assertTrue(man["frozen"])
        self.assertEqual(man["freeze"]["baseline_commit"], "70b6853")

    def test_global_spec(self):
        g = load_manifest()["global_spec"]
        self.assertEqual((g["width"], g["height"], g["fps"]), (1920, 1080, 30))

    def test_scenario_ids_exact(self):
        self.assertEqual([s["id"] for s in load_manifest()["scenarios"]],
                         ["S1", "S2", "S3", "S4a", "S4b", "S5", "S6", "S7", "S8"])


class TestIssueTableSpec(unittest.TestCase):
    """issue #106 执行任务表：帧数/页面来源逐场景断言（冻结口径）。"""

    def test_s1_basic_throughput(self):
        s = scenario(load_manifest(), "S1")
        pages = {p["role"]: p for p in s["pages"]}
        self.assertEqual(pages["gfxA"]["path"], "templates/skill-demo/gfxA.html")
        self.assertEqual(pages["gfxA"]["frames"], 90)
        self.assertIn("s1_solid_static.html", pages["solid_static"]["path"])
        self.assertEqual(pages["solid_static"]["frames"], 100)
        self.assertEqual(s["total_frames"], 190)

    def test_s2_cjk_svg(self):
        s = scenario(load_manifest(), "S2")
        paths = {p["path"] for p in s["pages"]}
        self.assertIn("templates/tech-talk/assets/gfx-templates/data_table_grouped.html", paths)
        self.assertIn("templates/tech-talk/assets/gfx-templates/nav_card.html", paths)
        self.assertEqual([p["frames"] for p in s["pages"]], [180, 180])
        self.assertEqual(s["total_frames"], 360)

    def test_s3_heavy_blur(self):
        s = scenario(load_manifest(), "S3")
        p = s["pages"][0]
        self.assertIn("s3_heavy_blur.html", p["path"])
        self.assertEqual(p["frames"], 300)
        self.assertEqual(s["total_frames"], 300)
        feats = " ".join(s["features"])
        self.assertIn("backdrop-filter: blur(80px)", feats)
        self.assertIn("drop-shadow()", feats)
        self.assertIn("径向渐变", feats)

    def test_s4a_low_alpha(self):
        s = scenario(load_manifest(), "S4a")
        paths = {p["path"] for p in s["pages"]}
        for rel in ("overlay_card_bottom.html", "overlay_card_top.html", "lower-third.html"):
            self.assertTrue(any(rel in p for p in paths), rel)
        self.assertEqual([p["frames"] for p in s["pages"]], [150, 150, 150])
        self.assertEqual(s["total_frames"], 450)

    def test_s4b_body_opacity(self):
        s = scenario(load_manifest(), "S4b")
        p = s["pages"][0]
        self.assertIn("s4_body_opacity_fade.html", p["path"])
        self.assertEqual(p["frames"], 120)
        self.assertEqual(s["total_frames"], 120)
        self.assertIn("mask-image: linear-gradient", " ".join(s["features"]))
        self.assertIn("opacity 0.0→1.0→0.0", " ".join(s["features"]))

    def test_s5_webgl(self):
        s = scenario(load_manifest(), "S5")
        p = s["pages"][0]
        self.assertIn("s5_webgl_particles.html", p["path"])
        self.assertEqual(p["frames"], 300)
        feats = " ".join(s["features"])
        self.assertIn("2,000", feats)
        self.assertIn("Mulberry32", feats)

    def test_s6_timing(self):
        s = scenario(load_manifest(), "S6")
        p = s["pages"][0]
        self.assertIn("s6_timing_adversarial.html", p["path"])
        self.assertEqual(p["frames"], 600)
        self.assertEqual(s["total_frames"], 600)


class TestPagesExistAndFrozen(unittest.TestCase):
    """③ 页面存在 + sha256_16 指纹全等：上游模板任何改动 ⇒ 冻结破裂显形。"""

    def _all_page_entries(self):
        man = load_manifest()
        entries = []
        for s in man["scenarios"]:
            for p in s.get("pages", []):
                entries.append((s["id"], p))
        return entries

    def test_repo_pages_exist_with_matching_hashes(self):
        bad = []
        for sid, p in self._all_page_entries():
            path = REPO / p["path"]
            self.assertTrue(path.is_file(), f"{sid}: {p['path']} 不存在")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            if digest != p["sha256_16"]:
                bad.append(f"{sid}:{p['path']} manifest={p['sha256_16']} disk={digest}")
        self.assertEqual(bad, [], "页面指纹失配（冻结破裂，须开新 issue 重冻结）：\n" +
                         "\n".join(bad))

    def test_s8_is_external_only(self):
        s = scenario(load_manifest(), "S8")
        self.assertNotIn("pages", s)          # S8 无仓内页面：外部项目双轨
        self.assertIn("external", s)


class TestS6SegmentsAndSequences(unittest.TestCase):
    def test_segments_match_issue(self):
        segs = scenario(load_manifest(), "S6")["segments"]
        self.assertEqual([[a, b] for a, b in (x["range"] for x in segs)],
                         [[0, 120], [120, 240], [240, 300], [300, 600]])
        self.assertEqual([x["label"] for x in segs],
                         ["PHASE A", "PHASE B", "PHASE C", "PHASE D"])

    def test_delay_injection_spec(self):
        d = scenario(load_manifest(), "S6")["delay_injection"]
        self.assertEqual(d["ms"], 55)
        self.assertEqual(d["frames"], [240, 250, 260, 270, 280, 290])
        # 注入帧 = PHASE C ∧ f≡0 (mod 10) 的精确集合
        self.assertEqual(d["frames"], [f for f in range(240, 300) if f % 10 == 0])
        self.assertTrue(d["default_mode"].startswith("sync"))

    def test_random_jump_frozen_verbatim(self):
        seq = scenario(load_manifest(), "S6")["sequences"]["random_jump"]["frames"]
        self.assertEqual(seq, [0, 450, 12, 12, 899, 13, 200, 200, 201, 599, 0])

    def test_reverse_100_rebuildable(self):
        rj = scenario(load_manifest(), "S6")["sequences"]["reverse_100"]
        rebuilt = list(range(99, -1, -1))
        self.assertEqual(rj["python_expr"], "list(range(99, -1, -1))")
        self.assertEqual(rj["length"], len(rebuilt))
        self.assertEqual(rj["head"], rebuilt[:3])
        self.assertEqual(rj["tail"], rebuilt[-3:])


class TestS7Workload(unittest.TestCase):
    def test_fourteen_pages_exact_order(self):
        s = scenario(load_manifest(), "S7")
        self.assertEqual(len(s["pages"]), 14)
        self.assertEqual(s["pages"][0]["path"], "templates/skill-demo/gfxA.html")
        self.assertIn("placeholder_gfx.html", s["pages"][-1]["path"])

    def test_round_frames_1920(self):
        s = scenario(load_manifest(), "S7")
        self.assertEqual(sum(p["visit_frames"] for p in s["pages"]), 1920)

    def test_concurrency_legs_4_and_7(self):
        runs = scenario(load_manifest(), "S7")["legs"]["alternating_concurrency"]["runs"]
        self.assertEqual(sorted(r["VTX_GFX_PAGES"] for r in runs), [4, 7])

    def test_single_worker_allocation_sums_to_5000(self):
        sw = scenario(load_manifest(), "S7")["legs"]["single_worker_5000"]
        self.assertEqual(sw["total_frames"], 5000)
        self.assertEqual(sw["browser_restarts_allowed"], 0)
        total = 0
        for alloc in sw["allocation"]:
            if "frames_per_page" in alloc:      # 整轮：14 页 × visit_frames
                man = scenario(load_manifest(), "S7")
                total += alloc["rounds"] * sum(p["visit_frames"] for p in man["pages"])
            else:                               # 部分轮：显式分配表
                total += sum(e["frames"] for e in alloc["partial"])
        self.assertEqual(total, 5000)

    def test_rss_budget(self):
        rss = scenario(load_manifest(), "S7")["rss_budget"]
        self.assertEqual(rss["peak_gib"], 16)
        self.assertTrue(rss["rationale"])


class TestS8Invariants(unittest.TestCase):
    def test_micro_track(self):
        inv = scenario(load_manifest(), "S8")["tracks"]["micro"]["invariants"]
        self.assertEqual((inv["pages"], inv["frames"]), (28, 12024))

    def test_macro_track_and_bench_p2_archived(self):
        s8 = scenario(load_manifest(), "S8")
        macro = s8["tracks"]["macro"]
        self.assertEqual(macro["invariants"]["frames"], 16746)
        self.assertEqual(macro["invariants"]["fps"], 30)
        self.assertTrue(macro["calibers"]["bench_p2"]["status"].startswith("存档"))
        self.assertTrue(macro["calibers"]["voice_ime_r07a"]["status"].startswith("现行"))


class TestIronLawStaticScan(unittest.TestCase):
    """⑦⑧ 帧驱动铁律静态扫描：19 页全量禁 token + 钩子赋值契约。"""

    def _suite_pages(self):
        man = load_manifest()
        paths = []
        for s in man["scenarios"]:
            for p in s.get("pages", []):
                paths.append((s["id"], p["path"]))
        for p in scenario(man, "S7")["pages"]:
            paths.append(("S7", p["path"]))
        return sorted(set(paths))

    def test_no_banned_animation_apis(self):
        violations = []
        for sid, rel in self._suite_pages():
            text = (REPO / rel).read_text(encoding="utf-8")
            for pat in BANNED_PATTERNS:
                if pat.search(text):
                    violations.append(f"{sid}:{rel} 含禁 token {pat.pattern}")
        self.assertEqual(violations, [])

    def test_hooks_contract(self):
        for sid, rel in self._suite_pages():
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertNotIn("window.__vtx_static_spans", text.replace(
                "刻意不声明 __vtx_static_spans", "").replace(
                "__vtx_static_spans / __vtx_export_png", ""),
                f"{sid}:{rel} 不得声明静态跨度")
            self.assertNotIn("window.__vtx_export_png =", text,
                             f"{sid}:{rel} 不得实现 canvas 导出")
            has_render_hook = "window.__vtx_render" in text and "= function" in text
            if rel.rsplit("/", 1)[-1] in ("s5_webgl_particles.html",
                                          "s6_timing_adversarial.html"):
                self.assertTrue(has_render_hook, f"{sid}:{rel} 必须实现 __vtx_render")
            else:
                self.assertNotIn("window.__vtx_render =",
                                  text, f"{sid}:{rel} 不应实现 __vtx_render")

    def test_new_pages_declare_frozen_fps_and_n(self):
        for fname, n in (("s1_solid_static.html", 100), ("s3_heavy_blur.html", 300),
                         ("s4_body_opacity_fade.html", 120),
                         ("s5_webgl_particles.html", 300),
                         ("s6_timing_adversarial.html", 600)):
            text = (SUITE / "pages" / fname).read_text(encoding="utf-8")
            self.assertIn("FPS = 30", text, fname)
            self.assertIn(f"N = {n}", text, fname)


class TestIdempotencyDoD(unittest.TestCase):
    def test_dod_method_frozen(self):
        dod = load_manifest()["idempotency_dod"]["method"]
        self.assertEqual(dod["run_a"], "以顺序序列 [0,1,2,3] 渲染（单 tab，逐帧导航），取 f0002.png")
        self.assertEqual(dod["run_b"], "以跳帧序列 [99,2] 渲染（单 tab，先渲 f0099 再渲 f0002），取 f0002.png")
        self.assertIn("max_abs_diff 必须 == 0（Tier-1）", dod["compare"])

    def test_tier1_definition(self):
        tier1 = load_manifest()["comparison_tiers"]["Tier-1"]
        self.assertIn("max_abs_diff == 0", tier1["definition"])


def load_smoke():
    spec = importlib.util.spec_from_file_location("smoke_unit", SUITE / "smoke.py")
    module = importlib.util.module_from_spec(spec)
    # The runner's command defaults must not mutate the unit test process.
    with mock.patch.dict(os.environ), mock.patch.object(sys, "path", list(sys.path)):
        spec.loader.exec_module(module)
    return module


class TestS6SequenceVerifier(unittest.TestCase):
    """In-process control-flow proofs, not real-browser/pixel acceptance.

    Use the shared stdlib PNG fixture and real artifact validation. The renderer,
    watchdog and pixel comparator are explicit fakes; no browser/fork is allowed.
    """

    def setUp(self):
        self.mod = load_smoke()
        from tests.gfx._png import png
        self.png = png
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.case = 0
        for patch in (mock.patch.object(self.mod.gfx_render, "render_page", self.render),
                      mock.patch.object(self.mod.gfx_render, "Browser", side_effect=AssertionError("real browser")),
                      mock.patch.object(self.mod.multiprocessing, "get_context", side_effect=AssertionError("real fork")),
                      mock.patch.object(self.mod.gfx_render.subprocess, "Popen", side_effect=AssertionError("real subprocess"))):
            patch.start()
            self.addCleanup(patch.stop)
        self.reset()

    def reset(self):
        self.case += 1
        self.smoke = self.mod.Smoke("unit-only", self.root / str(self.case))
        self.smoke.man["global_spec"].update(width=6, height=4)
        self.smoke.note = lambda message: None
        self.smoke.run_guarded = self.guarded
        self.smoke.frame_diff = lambda a, b: (0 if a.read_bytes() == b.read_bytes() else 1, None)
        self.config, self.calls, self.attempts = {}, [], []
        self.random = [0, 450, 12, 12, 899, 13, 200, 200, 201, 599, 0]

    def render(self, html, outdir, n_frames, *, chrome_path, tabs, frames):
        d = Path(outdir)
        d.mkdir(parents=True, exist_ok=True)
        leg = d.parent.name if d.name == "frames" else "catchup"
        cfg = self.config.get(leg, {})
        self.calls.append((leg, list(frames), tabs, os.environ.get("VTX_GFX_STATIC"),
                           os.environ.get("VTX_GFX_NAVFREE")))
        failed = list(cfg.get("failed", []))
        for index, fi in enumerate(cfg.get("frames", frames)):
            data = self.png(value=min(fi, 599) % 256)
            data = cfg.get("data", lambda i, f, data: data)(index, fi, data)
            # Model existing engine retry behavior, without invoking the engine.
            for attempt in range(3):
                self.attempts.append((leg, index, attempt))
                try:
                    self.mod.gfx_render._atomic_write_bytes(d / f"f{fi:04d}.png", data)
                    break
                except OSError:
                    if attempt == 2:
                        failed.append(fi)
        if cfg.get("foreign"):
            self.mod.gfx_render._atomic_write_bytes(d / "notes.txt", b"not a frame")
            other = d.parent / "other"
            other.mkdir()
            self.mod.gfx_render._atomic_write_bytes(other / "f9999.png", b"other scene")
        return {"failed": failed, "rendered": len(frames) - len(failed), "tabs": tabs}

    def guarded(self, fn, tag, outdir, work_n, tabs, phase):
        cfg = self.config.get(tag.split("__", 1)[1], {})
        try:
            payload = fn()
        except Exception as error:
            return "exc", f"{type(error).__name__}: {error}", {"wall_s": 0}
        if "after" in cfg:
            cfg["after"](Path(outdir))
        status = cfg.get("status", "ok")
        if "payload" in cfg:
            payload = cfg["payload"]
        return status, payload if status == "ok" else None, {
            "wall_s": 0, "reason": "budget", "elapsed_s": 1,
            "budget_s": 1, "frames_done": len(list(Path(outdir).glob("*.png")))}

    def run_smoke(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = self.smoke.run(["s6seq"])
        return result, self.smoke.report["phases"]["s6seq"]

    def test_complete_occurrences_preserve_order_duplicates_and_clamp(self):
        d = self.smoke.ev / "s6seq" / "random_jump"
        d.mkdir(parents=True)
        (d / "step9999_f0000.png").write_bytes(b"stale")
        self.config["random_jump"] = {"foreign": True}
        with mock.patch.dict(os.environ, {"VTX_GFX_STATIC": "1", "VTX_GFX_NAVFREE": "1"}):
            code, report = self.run_smoke()
            self.assertEqual(os.environ["VTX_GFX_STATIC"], "1")
            self.assertEqual(os.environ["VTX_GFX_NAVFREE"], "1")
        self.assertEqual(code, self.mod.EXIT_OK)
        self.assertEqual([c[1] for c in self.calls[1:]], [self.random, list(range(99, -1, -1))])
        self.assertTrue(all(c[2:] == (1, "0", "0") for c in self.calls))
        self.assertEqual(len(self.calls[0][1]), 104)
        self.assertEqual(sum(report[n]["compared"] for n in ("random_jump", "reverse_100")), 111)
        rows = report["random_jump"]["occurrences"]
        self.assertEqual([r["observed_frame"] for r in rows], self.random)
        self.assertEqual((rows[4]["requested_frame"], rows[4]["observed_frame"], rows[4]["reference_frame"]), (899, 899, 599))
        self.assertEqual(len(list(d.glob("step*.png"))), 11)
        self.assertNotEqual(rows[2]["artifact"], rows[3]["artifact"])
        self.assertTrue(all(r["candidate_bytes_sha256"] == r["reference_bytes_sha256"] for r in rows))

    def test_incomplete_reordered_or_extra_occurrences_fail(self):
        for frames in (self.random[:3] + self.random[4:], sorted(self.random), self.random + [0]):
            with self.subTest(frames=frames):
                self.reset()
                self.config["random_jump"] = {"frames": frames}
                code, report = self.run_smoke()
                self.assertEqual(code, self.mod.EXIT_FAIL)
                self.assertEqual(report["random_jump"]["status"], "FAIL")
                self.assertEqual(report["random_jump"]["captured"], len(frames))

    def test_every_occurrence_requires_valid_reference_and_comparison(self):
        for fault in ("first_duplicate", "candidate_corrupt", "candidate_size", "reference_corrupt",
                      "reference_size", "reference_missing", "diff_none", "diff_exception", "hash_exception"):
            with self.subTest(fault=fault):
                self.reset()
                if fault == "first_duplicate":
                    self.config["random_jump"] = {"data": lambda i, f, data: self.png(value=42) if i == 2 else data}
                elif fault.startswith(("candidate_", "reference_")):
                    leg = "random_jump" if fault.startswith("candidate_") else "catchup"
                    if fault.endswith("missing"):
                        self.config[leg] = {"frames": []}
                    else:
                        bad = b"bad png" if fault.endswith("corrupt") else self.png(w=5)
                        self.config[leg] = {"data": lambda i, f, data: bad if i == 0 else data}
                elif fault == "hash_exception":
                    self.smoke.frame_sha = mock.Mock(side_effect=OSError("read failed"))
                elif fault == "diff_none":
                    self.smoke.frame_diff = lambda a, b: (None, None)
                else:
                    self.smoke.frame_diff = mock.Mock(side_effect=ValueError("decode failed"))
                code, report = self.run_smoke()
                self.assertEqual(code, self.mod.EXIT_FAIL)
                if fault == "first_duplicate":
                    rows = report["random_jump"]["occurrences"]
                    self.assertEqual([rows[i]["status"] for i in (2, 3)], ["FAIL", "PASS"])
                    self.assertEqual((self.smoke.ev / "s6seq/random_jump/frames/f0012.png").read_bytes(), self.png(value=12))
                if fault.startswith("reference_") or fault == "hash_exception":
                    self.assertEqual(report["references"]["status"], "FAIL")
                if fault in ("reference_missing", "diff_none", "diff_exception", "hash_exception"):
                    self.assertEqual(report["random_jump"]["compared"], 0)
                    self.assertIsNone(report["random_jump"]["max_diff"])
                    self.assertIsNone(report["reverse_100"]["max_diff"])

    def test_renderer_completion_and_failure_payload_are_not_optional(self):
        for leg, cfg in (("catchup", {"frames": [], "failed": list(range(104))}),
                         ("catchup", {"failed": [0]}), ("random_jump", {"failed": [12]}),
                         ("random_jump", {"frames": []}),
                         ("catchup", {"payload": {}}), ("random_jump", {"payload": {}}),
                         ("catchup", {"status": "unknown"}), ("random_jump", {"status": "unknown"}),
                         ("catchup", {"data": mock.Mock(side_effect=RuntimeError("render failed"))})):
            with self.subTest(leg=leg, cfg=cfg):
                self.reset()
                self.config[leg] = cfg
                self.assertEqual(self.run_smoke()[0], self.mod.EXIT_FAIL)

    def test_observer_failure_is_sticky_but_original_writer_error_propagates(self):
        write = self.mod.gfx_render._atomic_write_bytes
        def fail_archive(path, data, *, root=None):
            if Path(path).name == "step0002_f0012.png":
                raise OSError("archive unavailable")
            return write(path, data, root=root)
        with mock.patch.object(self.mod.gfx_render, "_atomic_write_bytes", fail_archive):
            code, report = self.run_smoke()
        self.assertEqual(code, self.mod.EXIT_FAIL)
        self.assertEqual(len([a for a in self.attempts if a[0] == "random_jump"]), 11)
        self.assertEqual(report["random_jump"]["captured"], 10)
        self.assertEqual(report["random_jump"]["occurrences"][3]["observed_frame"], 12)
        self.assertTrue(any("archive unavailable" in error for error in self.smoke.failures))
        self.reset()
        match, capture = self.mod.re.fullmatch, self.smoke._capture_s6_sequence
        observed_stats = []
        raised = False
        def fail_classification(pattern, value):
            nonlocal raised
            if pattern == r"f([0-9]+)\.png" and not raised:
                raised = True
                raise OSError("classification failure " + "x" * 10000)
            return match(pattern, value)
        def capture_stats(*args):
            stats = capture(*args)
            observed_stats.append(stats)
            return stats
        self.smoke._capture_s6_sequence = capture_stats
        with mock.patch.object(self.mod.re, "fullmatch", fail_classification):
            self.assertEqual(self.run_smoke()[0], self.mod.EXIT_FAIL)
        self.assertEqual(len([a for a in self.attempts if a[0] == "random_jump"]), 11)
        self.assertEqual(observed_stats[0]["observation_error_count"], 1)
        self.assertLessEqual(len(observed_stats[0]["observation_errors"][0]), 200)
        failure = OSError("engine write unavailable")
        def render_once(*args, **kwargs):
            self.mod.gfx_render._atomic_write_bytes(self.root / "raw.png", b"data")
        with mock.patch.object(self.smoke, "render", render_once), \
                mock.patch.object(self.mod.gfx_render, "_atomic_write_bytes", side_effect=failure):
            with self.assertRaises(OSError) as caught:
                self.smoke._capture_s6_sequence("unused", self.root, [0])
        self.assertIs(caught.exception, failure)

    def test_timeout_is_partial_unless_observable_failure_also_exists(self):
        for fault in ("empty", "prefix", "missing_references", "corrupt_prefix", "failed_other_leg"):
            with self.subTest(fault=fault):
                self.reset()
                self.config["random_jump"] = {"status": "timeout", "frames": [] if fault == "empty" else self.random[:3]}
                if fault == "missing_references":
                    self.config["catchup"] = {"status": "timeout", "frames": []}
                elif fault == "corrupt_prefix":
                    self.config["random_jump"]["data"] = lambda i, f, data: b"bad" if i == 0 else data
                elif fault == "failed_other_leg":
                    self.config["reverse_100"] = {"failed": [0]}
                code, report = self.run_smoke()
                self.assertEqual(code, self.mod.EXIT_FAIL if fault in ("corrupt_prefix", "failed_other_leg") else self.mod.EXIT_TIMEOUT)
                self.assertLess(report["random_jump"]["compared"], 11)
                self.assertNotEqual(self.smoke.report["result"], "PASS")


class TestSmokeVerdicts(unittest.TestCase):
    """Synthetic control flow and PNGs only; no native/browser acceptance."""

    def setUp(self):
        self.mod = load_smoke()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.smoke = self.mod.Smoke("unit-only", self.root / "evidence")
        self.smoke.note = lambda message: None
        self.smoke.man["global_spec"].update(width=6, height=4)
        self.external = self.root / "external"
        (self.external / "gfx").mkdir(parents=True)
        self.pages = ("g6a_title.html", "g6b_endboard.html", "c5a_main_path.html")
        for page in self.pages:
            (self.external / "gfx" / page).write_text("synthetic fixture")
        (self.external / "timeline.json").write_text(json.dumps({"video_tracks": [
            {"kind": "gfx", "clips": [{"src": page, "dur": 1, "dst_in": i}
                                      for i, page in enumerate(self.pages)]}]}))
        scenario = self.smoke.scenario("S8")
        scenario["external"].update(voice_ime_root=str(self.external),
                                    bench_p2_root=str(self.root / "absent"))
        scenario["tracks"]["micro"]["invariants"].update(pages=3, frames=3)
        scenario["tracks"]["macro"]["invariants"]["frames"] = 3
        for patch in (mock.patch.object(self.mod.gfx_render, "Browser", side_effect=AssertionError("native browser")),
                      mock.patch.object(self.mod.multiprocessing, "get_context", side_effect=AssertionError("native fork"))):
            patch.start()
            self.addCleanup(patch.stop)
        self.smoke.render_guarded = self.fake_render

    def fake_render(self, html, outdir, *args, **kwargs):
        from tests.gfx._png import write_png
        outdir.mkdir(parents=True, exist_ok=True)
        write_png(outdir / "f0000.png", width=6, height=4)
        return "ok", {"failed": []}, {}

    def run_smoke(self, phases):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return self.smoke.run(phases)

    def test_unknown_empty_or_report_only_selection_fails_without_work(self):
        for phases in ([], ["report"], ["unknown"], ["manifest", "unknown"]):
            with self.subTest(phases=phases):
                self.smoke.failures.clear()
                with mock.patch.object(self.smoke, "phase_manifest") as run:
                    self.assertEqual(self.run_smoke(phases), self.mod.EXIT_FAIL)
                    run.assert_not_called()
                self.assertEqual(self.smoke.report["result"], "FAIL")

    def test_phase_exception_is_archived_as_failure_before_propagation(self):
        for phase in ("manifest", "render", "idempotency", "s6seq", "s7", "s8"):
            with self.subTest(phase=phase):
                self.smoke.report["phases"].clear()
                with mock.patch.object(self.smoke, f"phase_{phase}", side_effect=RuntimeError("injected")):
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        self.run_smoke([phase])
                report = json.loads((self.smoke.ev / "report.json").read_text())
                self.assertEqual(report["result"], "FAIL")
                self.assertEqual(report["phases"][phase]["status"], "FAIL")

    def test_phase_system_exit_zero_becomes_failure(self):
        for code in (None, 0, 7):
            with self.subTest(code=code):
                with mock.patch.object(self.smoke, "phase_manifest", side_effect=SystemExit(code)):
                    with self.assertRaises(SystemExit) as raised:
                        self.run_smoke(["manifest"])
                self.assertEqual(raised.exception.code, self.mod.EXIT_FAIL)
                self.assertEqual(self.smoke.report["result"], "FAIL")

    def test_s8_success_and_explicit_skip_are_not_confused(self):
        self.assertEqual(self.run_smoke(["s8"]), self.mod.EXIT_OK)
        self.assertEqual(self.smoke.report["phases"]["s8"]["status"], "PASS")
        self.smoke.skip_external = True
        self.smoke.report["phases"].clear()
        self.assertEqual(self.run_smoke(["s8"]), self.mod.EXIT_FAIL)
        self.assertEqual(self.smoke.report["phases"]["s8"]["status"], "SKIP")
        self.smoke.failures.clear()
        def manifest():
            self.smoke.report["phases"]["manifest"] = {"checks": 1}
        with mock.patch.object(self.smoke, "phase_manifest", side_effect=manifest):
            self.assertEqual(self.run_smoke(["manifest", "s8"]), self.mod.EXIT_OK)
        self.assertEqual(self.smoke.report["result"], "PARTIAL_SKIPPED")

    def test_s8_missing_or_invalid_proof_is_phase_failure(self):
        for fault in ("invariants", "missing_page", "missing_frame", "invalid_payload", "failed_payload", "archived_dir"):
            with self.subTest(fault=fault):
                self.smoke.failures.clear()
                scenario = self.smoke.scenario("S8")
                scenario["tracks"]["micro"]["invariants"]["pages"] = 4 if fault == "invariants" else 3
                page = self.external / "gfx" / self.pages[0]
                page.write_text("fixture")
                if fault == "missing_page":
                    page.unlink()
                archived = self.root / "absent"
                if fault == "archived_dir":
                    archived.mkdir()
                if fault == "missing_frame":
                    fake = lambda *a, **k: ("ok", {"failed": []}, {})
                elif fault == "invalid_payload":
                    fake = lambda *a, **k: ("ok", {}, {})
                elif fault == "failed_payload":
                    fake = lambda *a, **k: ("ok", {"failed": [0]}, {})
                else:
                    fake = self.fake_render
                with mock.patch.object(self.smoke, "render_guarded", side_effect=fake):
                    self.assertEqual(self.run_smoke(["s8"]), self.mod.EXIT_FAIL)
                self.assertEqual(self.smoke.report["phases"]["s8"]["status"], "FAIL")
                self.assertEqual(self.smoke.report["result"], "FAIL")

    def test_s6_absent_static_hashes_are_not_equal_frame_proof(self):
        hashes = {str(i): "static" if 120 <= i < 240 else f"frame{i}" for i in range(600)}
        self.assertTrue(all(self.smoke._assert_s6_segments(hashes).values()))
        for i in range(120, 240):
            del hashes[str(i)]
        result = self.smoke._assert_s6_segments(hashes)
        self.assertFalse(result["b_static_all_equal"])
        self.assertEqual(result["b_equals_120_frames"], 0)
        self.assertTrue(self.smoke.failures)

    def test_idempotency_requires_both_renderers_to_complete(self):
        for status, stats in (("ok", {"failed": [0]}), ("unknown", {"failed": []}), ("ok", {})):
            with self.subTest(status=status, stats=stats), \
                    mock.patch.object(self.smoke, "_unique_pages", return_value=[("S1", "fake.html")]), \
                    mock.patch.object(self.smoke, "render_guarded", return_value=(status, stats, {})), \
                    mock.patch.object(self.smoke, "frame_diff", return_value=(0, True)) as compare:
                self.assertEqual(self.run_smoke(["idempotency"]), self.mod.EXIT_FAIL)
                compare.assert_not_called()
                self.assertEqual(self.smoke.report["phases"]["idempotency"]["tier1_pass"], 0)

    def test_s8_timeout_not_phase_pass(self):
        info = {"reason": "budget", "elapsed_s": 1, "budget_s": 1, "frames_done": 0}
        with mock.patch.object(self.smoke, "render_guarded", return_value=("timeout", None, info)):
            self.assertEqual(self.run_smoke(["s8"]), self.mod.EXIT_TIMEOUT)
        self.assertEqual(self.smoke.report["phases"]["s8"]["status"], "PARTIAL_TIMEOUT")
        self.assertEqual(self.smoke.report["result"], "PARTIAL_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
