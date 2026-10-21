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
import hashlib
import json
import re
import unittest
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


if __name__ == "__main__":
    unittest.main()
