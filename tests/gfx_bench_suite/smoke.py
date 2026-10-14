#!/usr/bin/env python3
"""tests/gfx_bench_suite/smoke.py —— gfx 基准套件 S1–S8 套件自检（Chrome CDP 实渲）

issue #106 验证门 2 的执行器：S1–S8 每个场景在 Chrome CDP 下真实渲染出帧，
帧数/尺寸与冻结规格一致；并执行 DoD 同帧异序幂等门（Tier-1 max_abs_diff == 0）。

阶段（--phase 选择，默认 all 按序执行）：
  manifest     冻结件自一致性（页面存在 + sha256_16 指纹 + 帧数口径）；
  render       S1–S6 全量 2,320 帧实渲（tabs=4 生产默认）＋逐帧尺寸校验
               ＋帧清单 sha256 落盘＋S6 分段断言（B 段 120 帧逐字节全等等）；
  idempotency  DoD：19 页（S1–S6 场景页 ∪ S7 14 模板）每页以 [0,1,2,3] 与
               [99,2] 两个调度序列各渲一遍（tabs=1 保序），f0002 像素比对
               max_abs_diff 必须 == 0（Tier-1）；
  s6seq        S6 配套非顺序调度：random_jump（含 f=899 越界钳制）与
               reverse_100 倒序 100 帧，与 render 阶段产物逐帧 Tier-1 比对；
  s7           14 模板页可渲性抽样（每页 3 帧）＋单浏览器多页不重启 mini 腿
               （3 页 × 10 帧，一个 Browser 会话内连续导航截图——5,000 帧
               单 Worker 腿（#107 执行）的机制证明）；
  s8           外部生产基线不变量机检（voice-ime 28 页/12024 帧 gfx、全片
               16746 帧；bench_p2 目录应不存在=存档口径）＋3 个代表页抽样渲；
  report       汇总 evidence/report.json + report.md。

用法（仓根；本机 python 须 env 前缀）：
  env VTX_TEST_CHROME=/usr/bin/google-chrome python3 tests/gfx_bench_suite/smoke.py \
      [--evidence /tmp/framivra-evidence/issue-106] [--phase all|manifest,render,...]
      [--keep-frames] [--skip-external]

退出码：0=全部执行阶段 PASS（--skip-external 时 S8 记 SKIP 不计败）；
        1=任一门 FAIL；2=环境缺置（chrome/numpy）。
帧产物默认在 hashing 后清理（保留每页首/中/尾 3 帧样本），--keep-frames 保留全量。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from vtx import gfx_render                      # noqa: E402
from vtx.gfx_cli import verify_frames           # noqa: E402

MANIFEST_PATH = REPO / "tests" / "gfx_bench_suite" / "suite_manifest.json"
DEFAULT_EVIDENCE = Path("/tmp/framivra-evidence/issue-106")

# 引擎环境口径（suite_manifest.json protocol.engine_env_recommendations）
os.environ.setdefault("VTX_GFX_STATIC", "0")
os.environ.setdefault("VTX_GFX_NAVFREE", "0")


class Smoke:
    def __init__(self, chrome, evidence, keep_frames=False, skip_external=False):
        self.chrome = chrome
        self.ev = Path(evidence)
        self.keep = keep_frames
        self.skip_external = skip_external
        self.man = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.report = {"phases": {}, "env": {
            "chrome": chrome, "evidence": str(self.ev),
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}}
        self.failures = []

    # ---------------------------------------------------------- 通用工具
    def scenario(self, sid):
        for s in self.man["scenarios"]:
            if s["id"] == sid:
                return s
        raise KeyError(sid)

    def note(self, msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def fail(self, phase, msg):
        self.failures.append(f"[{phase}] {msg}")
        self.note(f"FAIL {phase}: {msg}")

    def render(self, html, outdir, n_frames, frames=None, tabs=4):
        """render_page 薄封装：相对路径按仓根解析；异常上抛由调用方判 FAIL。"""
        html = html if Path(html).is_absolute() else str(REPO / html)
        return gfx_render.render_page(html, str(outdir), n_frames,
                                       chrome_path=self.chrome, tabs=tabs,
                                       frames=frames)

    def frame_sha(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def frame_diff(self, pa, pb):
        """Tier-1 比对：RGBA 逐像素 max_abs_diff + 像素域 sha（gfx_golden 同口径）。"""
        a = gfx_render.png_decode_rgba(Path(pa).read_bytes())
        b = gfx_render.png_decode_rgba(Path(pb).read_bytes())
        if a.shape != b.shape:
            return None, None
        diff = int(abs(a.astype(int) - b.astype(int)).max())
        ha = hashlib.sha256(a.tobytes()).hexdigest()
        hb = hashlib.sha256(b.tobytes()).hexdigest()
        return diff, (ha == hb)

    # ---------------------------------------------------------- phase: manifest
    def phase_manifest(self):
        out = {"checks": 0, "hash_mismatches": []}
        for s in self.man["scenarios"]:
            for p in s.get("pages", []):
                path = REPO / p["path"]
                if not path.is_file():
                    self.fail("manifest", f"{s['id']}: {p['path']} 缺失")
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
                out["checks"] += 1
                if digest != p["sha256_16"]:
                    out["hash_mismatches"].append(
                        {"page": p["path"], "manifest": p["sha256_16"], "disk": digest})
        for p in self.scenario("S7")["pages"]:
            path = REPO / p["path"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.is_file() else "?"
            out["checks"] += 1
            if not path.is_file() or digest != p["sha256_16"]:
                out["hash_mismatches"].append(
                    {"page": p["path"], "manifest": p["sha256_16"], "disk": digest})
        if out["hash_mismatches"]:
            self.fail("manifest", f"{len(out['hash_mismatches'])} 页指纹失配（冻结破裂）")
        self.report["phases"]["manifest"] = out
        self.note(f"manifest: {out['checks']} 页指纹核验，"
                  f"失配 {len(out['hash_mismatches'])}")

    # ---------------------------------------------------------- phase: render
    def phase_render(self):
        out = {"scenarios": {}, "total_frames": 0, "total_ms": 0.0}
        root = self.ev / "smoke"
        root.mkdir(parents=True, exist_ok=True)
        for sid in ("S1", "S2", "S3", "S4a", "S4b", "S5", "S6"):
            sc = self.scenario(sid)
            sc_out = {"pages": []}
            for p in sc["pages"]:
                tag = f"{sid}__{p['role']}"
                d = root / tag
                if d.exists():
                    shutil.rmtree(d)            # 冷跑口径：不留旧帧
                n = p["frames"]
                t0 = time.time()
                try:
                    st = self.render(p["path"], d, n, tabs=4)
                except Exception as e:                          # noqa: BLE001
                    self.fail("render", f"{tag}: 渲染异常 {type(e).__name__}: {e}")
                    continue
                wall = time.time() - t0
                bad = verify_frames(str(d), range(n),
                                    width=self.man["global_spec"]["width"],
                                    height=self.man["global_spec"]["height"])
                if st["failed"] or bad:
                    self.fail("render", f"{tag}: failed={st['failed']} 坏帧={bad[:5]}")
                shas = {}
                for i in range(n):
                    fp = d / f"f{i:04d}.png"
                    shas[str(i)] = self.frame_sha(fp) if fp.is_file() else None
                missing = [i for i, v in shas.items() if v is None]
                if missing:
                    self.fail("render", f"{tag}: 缺帧 {missing[:5]}")
                sc_out["pages"].append({
                    "role": p["role"], "path": p["path"], "frames": n,
                    "rendered": st["rendered"], "ms_total": round(st["ms_total"], 1),
                    "ms_per_frame": round(st["ms_per_frame"], 2),
                    "wall_s": round(wall, 1), "transparent": st["transparent"],
                    "path_counts": {k: v for k, v in st["path_counts"].items() if v},
                    "frame_sha256": shas})
                out["total_frames"] += n
                out["total_ms"] += st["ms_total"]
                # S6 分段断言（必须在清帧前——用刚采集的哈希清单，不回读文件）
                if sid == "S6":
                    sc_out["segments"] = self._assert_s6_segments(shas)
                # 样本保留 + 清帧（除非 --keep-frames）
                if not self.keep:
                    mid = n // 2
                    for i in range(n):
                        if i not in (0, mid, n - 1):
                            (d / f"f{i:04d}.png").unlink(missing_ok=True)
                self.note(f"render {tag}: {n} 帧 {st['ms_per_frame']:.1f}ms/帧 "
                          f"paths={ {k: v for k, v in st['path_counts'].items() if v} }")
            out["scenarios"][sid] = sc_out
        self.report["phases"]["render"] = out
        self.note(f"render: 合计 {out['total_frames']} 帧，"
                  f"引擎耗时 {out['total_ms'] / 1000:.1f}s")

    def _assert_s6_segments(self, shas_by_idx):
        """shas_by_idx: {帧号str: sha256}（render 阶段清帧前采集）。"""
        shas = [shas_by_idx.get(str(i)) for i in range(600)]
        seg_b = shas[120:240]
        res = {
            "b_static_all_equal": len(set(seg_b)) == 1,
            "b_equals_120_frames": len(seg_b),
            "a_tail_ne_b_head": shas[119] != shas[120],
            "c_frames_present": all(shas[f] is not None for f in
                                    (240, 250, 260, 270, 280, 290)),
            "d_frames_evolve": shas[300] != shas[599],
        }
        for k, v in res.items():
            if not v:
                self.fail("render", f"S6 分段断言失败: {k}")
        return res

    # ---------------------------------------------------------- phase: idempotency
    def _unique_pages(self):
        seen, pages = set(), []
        for sid in ("S1", "S2", "S3", "S4a", "S4b", "S5", "S6"):
            for p in self.scenario(sid)["pages"]:
                if p["path"] not in seen:
                    seen.add(p["path"])
                    pages.append((sid, p["path"]))
        for p in self.scenario("S7")["pages"]:
            if p["path"] not in seen:
                seen.add(p["path"])
                pages.append(("S7", p["path"]))
        return pages

    def phase_idempotency(self):
        out = {"pages": [], "tier1_pass": 0, "tier1_fail": 0}
        root = self.ev / "idempotency"
        if root.exists():
            shutil.rmtree(root)
        for sid, path in self._unique_pages():
            stem = Path(path).stem
            da = root / "a" / f"{sid}__{stem}"      # sid 前缀防同名页冲突（两份 gfxA.html）
            db = root / "b" / f"{sid}__{stem}"
            try:
                self.render(path, da, 4, frames=[0, 1, 2, 3], tabs=1)
                self.render(path, db, 100, frames=[99, 2], tabs=1)
                diff, pixel_equal = self.frame_diff(da / "f0002.png", db / "f0002.png")
            except Exception as e:                              # noqa: BLE001
                self.fail("idempotency", f"{path}: {type(e).__name__}: {e}")
                continue
            ok = diff == 0
            out["pages"].append({"scenario": sid, "path": path,
                                 "max_abs_diff": diff, "pixel_sha_equal": pixel_equal,
                                 "tier1": "PASS" if ok else "FAIL"})
            out["tier1_pass" if ok else "tier1_fail"] += 1
            if not ok:
                self.fail("idempotency",
                          f"{path}: f0002 异序 max_abs_diff={diff}（Tier-1 要求 0）")
        self.report["phases"]["idempotency"] = out
        self.note(f"idempotency: {out['tier1_pass']} 页 Tier-1 PASS / "
                  f"{out['tier1_fail']} FAIL")

    # ---------------------------------------------------------- phase: s6seq
    def phase_s6seq(self):
        out = {}
        s6p = None
        for p in self.scenario("S6")["pages"]:
            s6p = p
        full = self.ev / "smoke" / "S6__timing_adversarial"
        if not (full / "f0599.png").is_file():
            # render 阶段清帧后仅存样本——random_jump/reverse 的对账帧需在场：
            # f0000/f0300 样本在，其余对账帧重渲缺哪些补哪些（保持 Tier-1 同口径）
            need = {0, 12, 13, 200, 201, 450, 599}
            need |= set(range(100))
            have = {int(fp.stem[1:]) for fp in full.glob("f*.png")}
            missing = sorted(need - have)
            if missing:
                self.render(s6p["path"], full, 900, frames=missing, tabs=1)
        seqs = self.scenario("S6")["sequences"]
        # random_jump（含重复帧去重渲染）
        rj = seqs["random_jump"]["frames"]
        d = self.ev / "s6seq" / "random_jump"
        if d.exists():
            shutil.rmtree(d)
        st = self.render(s6p["path"], d, 900, frames=sorted(set(rj)), tabs=1)
        if st["failed"]:
            self.fail("s6seq", f"random_jump failed={st['failed']}")
        diffs = {}
        for fi in sorted(set(rj)):
            ref = fi if fi < 600 else 599       # 页内钳制：f≥N 定格终态
            diff, _ = self.frame_diff(d / f"f{fi:04d}.png", full / f"f{ref:04d}.png")
            diffs[str(fi)] = diff
            if diff != 0:
                self.fail("s6seq", f"random_jump f{fi:04d} vs 全量渲 f{ref:04d} "
                                   f"max_abs_diff={diff}")
        out["random_jump"] = {"frames": rj, "diffs_vs_full": diffs,
                              "max_diff": max(diffs.values()) if diffs else None}
        # reverse_100
        rev = list(range(99, -1, -1))
        d2 = self.ev / "s6seq" / "reverse_100"
        if d2.exists():
            shutil.rmtree(d2)
        st2 = self.render(s6p["path"], d2, 100, frames=rev, tabs=1)
        if st2["failed"]:
            self.fail("s6seq", f"reverse_100 failed={st2['failed']}")
        md = 0
        for fi in rev:
            diff, _ = self.frame_diff(d2 / f"f{fi:04d}.png", full / f"f{fi:04d}.png")
            md = max(md, diff if diff is not None else 9999)
            if diff != 0:
                self.fail("s6seq", f"reverse_100 f{fi:04d} max_abs_diff={diff}")
        out["reverse_100"] = {"length": 100, "max_diff_vs_full": md}
        self.report["phases"]["s6seq"] = out
        self.note(f"s6seq: random_jump max_diff={out['random_jump']['max_diff']} "
                  f"reverse_100 max_diff={md}")

    # ---------------------------------------------------------- phase: s7
    def phase_s7(self):
        out = {"pages": [], "mini_run": {}}
        root = self.ev / "s7"
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        for p in self.scenario("S7")["pages"]:
            tag = Path(p["path"]).stem
            d = root / "sample" / tag
            visit = p["visit_frames"]
            probe = sorted({0, visit // 2, visit - 1})
            try:
                st = self.render(p["path"], d, visit, frames=probe, tabs=1)
            except Exception as e:                              # noqa: BLE001
                self.fail("s7", f"{p['path']}: {type(e).__name__}: {e}")
                continue
            bad = verify_frames(str(d), probe,
                                width=self.man["global_spec"]["width"],
                                height=self.man["global_spec"]["height"])
            if bad:
                self.fail("s7", f"{p['path']}: 探针帧坏 {bad}")
            out["pages"].append({"path": p["path"], "probe_frames": probe,
                                 "ok": not bad})
        # 单浏览器多页不重启 mini 腿（机制证明：3 页 × 10 帧同一 Browser 会话）
        mini_pages = [self.scenario("S7")["pages"][0]["path"],
                      self.scenario("S7")["pages"][7]["path"],
                      self.scenario("S7")["pages"][13]["path"]]
        md = root / "mini_run"
        md.mkdir(parents=True)
        browser = gfx_render.Browser(self.chrome, 1)
        t0 = time.time()
        shots = 0
        try:
            browser.start()
            tab = browser.open_tabs()[0]
            tab.call("Emulation.setDefaultBackgroundColorOverride",
                     color={"r": 0, "g": 0, "b": 0, "a": 0})
            for pi, path in enumerate(mini_pages):
                uri = (REPO / path).resolve().as_uri()
                for fi in range(10):
                    tab.navigate(f"{uri}?f={fi}")
                    data = tab.shot()
                    (md / f"p{pi}_f{fi:02d}.png").write_bytes(data)
                    if not data.startswith(b"\x89PNG"):
                        self.fail("s7", f"mini_run p{pi} f{fi} 非 PNG")
                    shots += 1
            tab.close()
        finally:
            browser.stop()
        out["mini_run"] = {"pages": mini_pages, "frames": shots,
                           "browser_restarts": 0,
                           "wall_s": round(time.time() - t0, 1)}
        self.report["phases"]["s7"] = out
        self.note(f"s7: {len(out['pages'])} 页抽样 OK；mini_run {shots} 帧 "
                  f"单会话 {out['mini_run']['wall_s']}s")

    # ---------------------------------------------------------- phase: s8
    def phase_s8(self):
        out = {"status": "SKIP", "checks": {}}
        ext = self.scenario("S8")["external"]
        root = Path(ext["voice_ime_root"])
        if self.skip_external:
            out["reason"] = "--skip-external"
            self.note("s8: SKIP（--skip-external）")
            self.report["phases"]["s8"] = out
            return
        if not root.is_dir():
            out["reason"] = f"外部项目缺席：{root}"
            self.fail("s8", out["reason"])
            self.report["phases"]["s8"] = out
            return
        tl = json.loads((root / "timeline.json").read_text(encoding="utf-8"))
        pages, frames = {}, 0
        for tr in tl.get("video_tracks", []):
            if tr.get("kind") == "gfx":
                for cl in tr.get("clips", []):
                    pages[cl["src"]] = pages.get(cl["src"], 0) + 1
                    frames += cl["dur"]
        total = 0
        for tr in tl.get("video_tracks", []):
            for cl in tr.get("clips", []):
                total = max(total, cl.get("dst_in", 0) + cl.get("dur", 0))
        inv = self.scenario("S8")["tracks"]
        ok_micro = (len(pages) == inv["micro"]["invariants"]["pages"]
                    and frames == inv["micro"]["invariants"]["frames"])
        ok_macro = total == inv["macro"]["invariants"]["frames"]
        out["checks"] = {"gfx_unique_pages": len(pages), "gfx_frames": frames,
                         "timeline_total_frames": total,
                         "micro_invariants_match": ok_micro,
                         "macro_invariants_match": ok_macro}
        if not (ok_micro and ok_macro):
            self.fail("s8", f"不变量失配 {out['checks']}")
        # bench_p2 存档口径核验：目录应不存在
        out["checks"]["bench_p2_dir_absent"] = not Path(ext["bench_p2_root"]).is_dir()
        # 代表页抽样渲（3 页 × 1 帧）
        d = self.ev / "s8"
        if d.exists():
            shutil.rmtree(d)
        for page in ("g6a_title.html", "g6b_endboard.html", "c5a_main_path.html"):
            src = root / "gfx" / page
            if not src.is_file():
                self.fail("s8", f"voice-ime gfx/{page} 缺席")
                continue
            try:
                st = self.render(src, d / Path(page).stem, 1, frames=[0], tabs=1)
                if st["failed"]:
                    self.fail("s8", f"{page} 抽样渲 failed={st['failed']}")
                out["checks"][f"sample_{page}"] = "ok"
            except Exception as e:                              # noqa: BLE001
                self.fail("s8", f"{page}: {type(e).__name__}: {e}")
        out["status"] = "PASS"
        self.report["phases"]["s8"] = out
        self.note(f"s8: {out['checks']}")

    # ---------------------------------------------------------- report + main
    def write_report(self):
        self.report["env"]["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.report["result"] = "PASS" if not self.failures else "FAIL"
        self.report["failures"] = self.failures
        self.ev.mkdir(parents=True, exist_ok=True)
        (self.ev / "report.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=1), encoding="utf-8")
        lines = [f"# gfx_bench_suite smoke 报告（{self.report['result']}）", "",
                 f"- chrome: `{self.chrome}`",
                 f"- evidence: `{self.ev}`",
                 f"- failures: {len(self.failures)}", ""]
        ph = self.report["phases"]
        if "render" in ph:
            lines.append("## render（S1–S6 全量实渲）")
            lines.append("")
            lines.append("| 场景 | 页 | 帧数 | ms/帧 | 路径 |")
            lines.append("|---|---|---|---|---|")
            for sid, sc in ph["render"]["scenarios"].items():
                for p in sc["pages"]:
                    lines.append(f"| {sid} | {p['role']} | {p['frames']} | "
                                 f"{p['ms_per_frame']} | {p['path_counts']} |")
            lines.append("")
        if "idempotency" in ph:
            lines.append("## idempotency（DoD Tier-1）")
            lines.append("")
            for p in ph["idempotency"]["pages"]:
                lines.append(f"- {p['scenario']} `{p['path']}`: "
                             f"max_abs_diff={p['max_abs_diff']} → {p['tier1']}")
            lines.append("")
        if "s6seq" in ph:
            rj = ph["s6seq"]["random_jump"]
            lines.append("## s6seq")
            lines.append("")
            lines.append(f"- random_jump diffs_vs_full: {rj['diffs_vs_full']}")
            lines.append(f"- reverse_100 max_diff_vs_full: "
                         f"{ph['s6seq']['reverse_100']['max_diff_vs_full']}")
            lines.append("")
        if "s8" in ph:
            lines.append("## s8")
            lines.append("")
            lines.append(f"- status={ph['s8']['status']} checks={ph['s8'].get('checks')}")
            lines.append("")
        (self.ev / "report.md").write_text("\n".join(lines), encoding="utf-8")

    def run(self, phases):
        try:
            if "manifest" in phases:
                self.phase_manifest()
            if "render" in phases:
                self.phase_render()
            if "idempotency" in phases:
                self.phase_idempotency()
            if "s6seq" in phases:
                self.phase_s6seq()
            if "s7" in phases:
                self.phase_s7()
            if "s8" in phases:
                self.phase_s8()
        finally:
            self.write_report()
        if self.failures:
            print(f"\nRESULT: FAIL（{len(self.failures)} 项）", file=sys.stderr)
            for f in self.failures:
                print(f"  - {f}", file=sys.stderr)
            return 1
        print("\nRESULT: PASS")
        return 0


def find_chrome():
    for cand in (os.environ.get("VTX_TEST_CHROME"), os.environ.get("VTX_CHROME"),
                 "google-chrome", "/usr/bin/google-chrome"):
        if cand and Path(cand).is_file():
            return cand
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    ap.add_argument("--phase", default="all",
                    help="all 或逗号分隔：manifest,render,idempotency,s6seq,s7,s8,report")
    ap.add_argument("--keep-frames", action="store_true",
                    help="保留全部帧产物（默认 hashing 后清理，仅留每页首/中/尾样本）")
    ap.add_argument("--skip-external", action="store_true",
                    help="S8 外部生产基线缺席时记 SKIP 不计败")
    args = ap.parse_args()
    if not gfx_render.available():
        print("numpy 不可用（vtx.gfx_render.available()=False）", file=sys.stderr)
        return 2
    chrome = find_chrome()
    if not chrome:
        print("chrome 未找到：设 VTX_TEST_CHROME=/usr/bin/google-chrome", file=sys.stderr)
        return 2
    phases = (["manifest", "render", "idempotency", "s6seq", "s7", "s8"]
              if args.phase == "all" else args.phase.split(","))
    smoke = Smoke(chrome, args.evidence, keep_frames=args.keep_frames,
                  skip_external=args.skip_external)
    return smoke.run(phases)


if __name__ == "__main__":
    sys.exit(main())
