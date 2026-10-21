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

看门狗（2026-10-15 加固，issue #106 续作）：每次 render_page/浏览器会话在
独立子进程（新进程组）内执行；父进程轮询帧产物目录做逐帧进度输出，并以两道
墙钟闸收割任何「单场景拖死整套」的形态——
  · stall 闸：--stall-s 秒（默认 300）内无任何新帧落盘 → 判「停滞」整组击杀；
  · budget 闸：单页墙钟超 budget（默认 120s + 10s×work帧数/tabs，可
    --page-budget 绝对值覆盖）→ 判「超预算」整组击杀。
击杀 = killpg(SIGKILL) 连同 Chrome 子进程一并回收，记录 {reason, budget_s,
elapsed_s, frames_done, est_ms_per_frame} 事实后跳过继续；引擎 gfx_render.py
与冻结规格（帧数/尺寸/页面）零改动。超时不是 FAIL：其缺帧/分段断言降级为
事实记录，最终以退出码 3 区分（见下）。

背景（2026-10-14 23:54 S3 停摆终裁；证据 /tmp/framivra-evidence/issue-106b*）：
  · 物证：S1/S2 正常跑完（39/34/57ms/帧）后 S3 于 23:54:32 进入即零输出，
    产物目录 0 帧落地（issue-106/smoke/S3__heavy_blur/ 空目录为证）；
  · 基线：干净环境 S3 全量 300 帧 98.97s（317ms/帧，304 截 p99=1.45s/
    max=1.49s，0 异常，probe_s3_full300.log）；28 核合成饱和争用下
    6.9s/帧仍 300/300 完成（probe_s3_contention.log）——S3 本身不慢、
    非真挂死；
  · 推证（引擎结构 + 零帧物证收敛）：重度争用（#102 全量测试等并发
    Chrome 舰队）把 SwiftShader 重 blur 单帧光栅推过引擎 WS socket 20s
    硬超时 → socket.timeout → heal 整浏览器冷重启 → 每帧重试≤2 败→帧弃
    （不落盘）→ 下一帧重复，≈75 帧/tab × ~100s/帧 ≈ 2h+ 零帧；旧
    harness 逐页完成才打一行，长静默即「停摆」观感。「单帧>20s」未在
    受控探针中直接复现（28 核合成争用只到 ~7s/帧），该环节属结构推证。
    看门狗以预算/停滞双闸把此形态收割为有界受控超时（exit 3）并继续套件。

用法（仓根；本机 python 须 env 前缀）：
  env VTX_TEST_CHROME=/usr/bin/google-chrome python3 tests/gfx_bench_suite/smoke.py \
      [--evidence /tmp/framivra-evidence/issue-106] [--phase all|manifest,render,...]
      [--keep-frames] [--skip-external]
      [--page-budget 秒] [--per-frame-budget 秒] [--budget-base 秒] [--stall-s 秒]

退出码：0=非空执行范围通过（显式跳过其他阶段时标记 PARTIAL_SKIPPED）；
        1=任一门 FAIL（真实门失败，非超时）；
        2=环境缺置（chrome/numpy）；
        3=完成但存在受控超时（timeout 事实已记录、场景已跳过——「部分超时」）。
帧产物默认在 hashing 后清理（保留每页首/中/尾 3 帧样本），--keep-frames 保留全量。
"""
import argparse
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from vtx import gfx_render                      # noqa: E402
from vtx.gfx_cli import png_invalid_reason, verify_frames           # noqa: E402

MANIFEST_PATH = REPO / "tests" / "gfx_bench_suite" / "suite_manifest.json"
DEFAULT_EVIDENCE = Path("/tmp/framivra-evidence/issue-106")

EXIT_OK, EXIT_FAIL, EXIT_ENV, EXIT_TIMEOUT = 0, 1, 2, 3

# 引擎环境口径（suite_manifest.json protocol.engine_env_recommendations）
os.environ.setdefault("VTX_GFX_STATIC", "0")
os.environ.setdefault("VTX_GFX_NAVFREE", "0")


# ---------------------------------------------------------------- 看门狗执行器
def _deadline_child(q, fn):
    """子进程入口：自建进程组（Chrome 子进程同组），fn() 结果/异常经队列回传。"""
    try:
        os.setpgid(0, 0)
    except OSError:
        pass                                   # 已是组长等边缘：尽力而为
    try:
        q.put(("ok", fn()))
    except BaseException as e:                  # noqa: BLE001
        q.put(("exc", f"{type(e).__name__}: {e}"))


def render_result_error(status, stats):
    """One completion contract for all watchdog consumers; timeout is handled separately."""
    if status != "ok" or not isinstance(stats, dict) or not isinstance(stats.get("failed"), list):
        return f"invalid render result: {status} {stats}"
    return f"render failed={stats['failed']}" if stats["failed"] else None


class Smoke:
    def __init__(self, chrome, evidence, keep_frames=False, skip_external=False,
                 page_budget=None, per_frame_budget=10.0, budget_base=120.0,
                 stall_s=300.0):
        self.chrome = chrome
        self.ev = Path(evidence)
        self.keep = keep_frames
        self.skip_external = skip_external
        self.page_budget = page_budget
        self.per_frame_budget = per_frame_budget
        self.budget_base = budget_base
        self.stall_s = stall_s
        self.man = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.report = {"phases": {}, "env": {
            "chrome": chrome, "evidence": str(self.ev),
            "watchdog": {"page_budget": page_budget,
                         "per_frame_budget": per_frame_budget,
                         "budget_base": budget_base, "stall_s": stall_s},
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}}
        self.failures = []
        self.timeouts = []          # 受控超时登记（退出码 3 的依据，非 FAIL）

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

    def timeout(self, phase, rec):
        self.timeouts.append(f"[{phase}] {rec['page']}: {rec['reason']} "
                             f"{rec['elapsed_s']}s/{rec['budget_s']}s "
                             f"已落 {rec.get('frames_done', '?')} 帧")
        self.note(f"TIMEOUT {phase} {rec['page']}: reason={rec['reason']} "
                  f"elapsed={rec['elapsed_s']}s budget={rec['budget_s']}s "
                  f"frames_done={rec.get('frames_done')}——记录事实，受控跳过")

    def render(self, html, outdir, n_frames, frames=None, tabs=4):
        """render_page 薄封装：相对路径按仓根解析；异常上抛由调用方判 FAIL。"""
        html = html if Path(html).is_absolute() else str(REPO / html)
        return gfx_render.render_page(html, str(outdir), n_frames,
                                       chrome_path=self.chrome, tabs=tabs,
                                       frames=frames)

    def budget_for(self, work_n, tabs):
        """单页墙钟预算：--page-budget 绝对值优先，否则 base+每帧配额/tabs。"""
        if self.page_budget:
            return float(self.page_budget)
        return self.budget_base + self.per_frame_budget * max(1, work_n) / max(1, tabs)

    def run_guarded(self, fn, tag, outdir, work_n, tabs, phase):
        """fn()（通常含 render_page/浏览器会话）置于子进程组执行，两道闸收割。

        返回 (status, payload, info)：
          ("ok", fn 返回值, {"wall_s", "frames_done"})
          ("exc", "异常描述", ...)
          ("timeout", None, {"reason": stall|budget, "budget_s", "elapsed_s",
                             "frames_done", "est_ms_per_frame"})
        超时击杀 = killpg(SIGKILL)（Python 子进程＋其 Chrome 族整组回收）——
        这是「任何单场景挂死不再拖死整套」的硬保证，独立于引擎内部一切超时。
        """
        d = Path(outdir)
        # 计数 *.png（非 f*.png）：s7 mini_run 腿的产物名是 p{pi}_f{fi}.png，
        # 进度/停滞闸若只认 f* 前缀会对该腿失明（健康慢腿被误判停滞）。
        base = len(list(d.glob("*.png"))) if d.is_dir() else 0
        budget = self.budget_for(work_n, tabs)
        self.note(f"{phase} {tag}: 开工 {work_n} 帧·tabs={tabs}·"
                  f"预算 {budget:.0f}s"
                  + (f"·停滞闸 {self.stall_s:.0f}s" if self.stall_s else ""))
        # 轮询步距自适应：预算/停滞闸越紧检查越密（生产缺省钳回 5s）
        poll = max(0.2, min(5.0, budget / 3.0,
                            (self.stall_s / 3.0) if self.stall_s else 5.0))
        ctx = multiprocessing.get_context("fork")
        q = ctx.SimpleQueue()
        p = ctx.Process(target=_deadline_child, args=(q, fn), daemon=True)
        p.start()
        t0 = time.monotonic()
        last_cnt, last_change, last_note = 0, t0, t0
        reason = None
        while True:
            p.join(poll)
            if not p.is_alive():
                break
            now = time.monotonic()
            elapsed = now - t0
            cnt = len(list(d.glob("*.png"))) - base if d.is_dir() else 0
            if cnt != last_cnt:
                last_cnt, last_change = cnt, now
            if work_n >= 16 and elapsed >= 15 and now - last_note >= 15:
                self.note(f"{phase} {tag}: 进度 {last_cnt}/{work_n} 帧 "
                          f"({100 * last_cnt / work_n:.0f}%) · {elapsed:.0f}s")
                last_note = now
            if self.stall_s and elapsed > self.stall_s and \
                    (now - last_change) > self.stall_s:
                reason = "stall"                    # 有预算但零新帧 = 停滞/挂死形态
                break
            if elapsed > budget:
                reason = "budget"                   # 慢但在动 = 超预算形态
                break
        info = {"budget_s": round(budget, 1), "frames_done": last_cnt}
        if reason:
            for kill in (lambda: os.killpg(p.pid, signal.SIGKILL), p.kill):
                try:
                    kill()                      # 双保险：组杀 + 本体杀（setpgid 竞态兜底）
                except (ProcessLookupError, PermissionError, ValueError):
                    pass                        # 刚好自然退出：按结果收
            p.join(10)
            if p.is_alive():
                self.note(f"{phase} {tag}: WARN 击杀后 10s 仍存活"
                          f"（daemon 子进程，父进程退出时强制回收）")
            info.update({"reason": reason,
                         "elapsed_s": round(time.monotonic() - t0, 1),
                         "frames_done": len(list(d.glob("*.png"))) - base
                         if d.is_dir() else 0})
            if info["frames_done"]:
                info["est_ms_per_frame"] = round(
                    info["elapsed_s"] * 1000 / info["frames_done"] / max(1, tabs), 1)
            return ("timeout", None, info)
        p.join()
        if q.empty():
            return ("exc", "子进程无返回（异常死亡）", info)
        kind, payload = q.get()
        info["wall_s"] = round(time.monotonic() - t0, 1)
        info["frames_done"] = len(list(d.glob("*.png"))) - base if d.is_dir() else 0
        return (kind, payload, info)

    def render_guarded(self, html, outdir, n_frames, frames=None, tabs=4,
                       tag="", phase="render"):
        """render_page 的看门狗版本：超时/异常都收割为结构化记录，不上抛。"""
        html_abs = html if Path(html).is_absolute() else str(REPO / html)
        work_n = len(frames) if frames is not None else n_frames
        return self.run_guarded(
            lambda: self.render(html_abs, outdir, n_frames, frames=frames, tabs=tabs),
            tag, outdir, work_n, tabs, phase)

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
        out = {"scenarios": {}, "total_frames": 0, "total_frames_done": 0,
               "total_ms": 0.0, "timeout_wall_s": 0.0}
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
                status, st, info = self.render_guarded(
                    p["path"], d, n, tabs=4, tag=tag, phase="render")
                if status == "timeout":
                    rec = {"phase": "render", "page": tag, **info}
                    self.timeout("render", rec)
                    present = sorted(int(fp.stem[1:]) for fp in d.glob("f*.png")) \
                        if d.exists() else []
                    if present:
                        bad = verify_frames(str(d), present,
                                            width=self.man["global_spec"]["width"],
                                            height=self.man["global_spec"]["height"])
                        rec["bad_frames_present"] = bad[:5]
                    rec["missing_frames"] = n - len(present)
                    sc_out["pages"].append({
                        "role": p["role"], "path": p["path"], "frames": n,
                        "status": "timeout", "reason": info["reason"],
                        "frames_done": len(present),
                        "elapsed_s": info["elapsed_s"], "budget_s": info["budget_s"],
                        "est_ms_per_frame": info.get("est_ms_per_frame"),
                        "missing_frames": n - len(present)})
                    out["total_frames"] += n
                    out["total_frames_done"] += len(present)
                    out["timeout_wall_s"] += info["elapsed_s"]
                    continue
                error = render_result_error(status, st)
                if error:
                    self.fail("render", f"{tag}: {error}")
                    sc_out["pages"].append({"role": p["role"], "path": p["path"],
                                            "frames": n, "status": "exc",
                                            "error": error})
                    continue
                wall = info["wall_s"]
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
                    "status": "fail" if bad or missing else "ok",
                    "error": f"坏帧={bad[:5]} 缺帧={missing[:5]}" if bad or missing else None,
                    "rendered": st["rendered"], "ms_total": round(st["ms_total"], 1),
                    "ms_per_frame": round(st["ms_per_frame"], 2),
                    "wall_s": wall, "transparent": st["transparent"],
                    "path_counts": {k: v for k, v in st["path_counts"].items() if v},
                    "frame_sha256": shas})
                out["total_frames"] += n
                out["total_frames_done"] += n - len(missing)
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
        self.note(f"render: 冻结口径 {out['total_frames']} 帧 / 实落 "
                  f"{out['total_frames_done']} 帧，引擎耗时 "
                  f"{out['total_ms'] / 1000:.1f}s"
                  + (f"，超时墙钟 {out['timeout_wall_s']:.0f}s"
                     if out["timeout_wall_s"] else ""))

    def _assert_s6_segments(self, shas_by_idx):
        """shas_by_idx: {帧号str: sha256}（render 阶段清帧前采集）。"""
        shas = [shas_by_idx.get(str(i)) for i in range(600)]
        seg_b = shas[120:240]
        res = {
            "b_static_all_equal": all(value is not None for value in seg_b) and len(set(seg_b)) == 1,
            "b_equals_120_frames": sum(value is not None for value in seg_b),
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
        out = {"pages": [], "tier1_pass": 0, "tier1_fail": 0, "tier1_skip": 0}
        root = self.ev / "idempotency"
        if root.exists():
            shutil.rmtree(root)
        for sid, path in self._unique_pages():
            stem = Path(path).stem
            da = root / "a" / f"{sid}__{stem}"      # sid 前缀防同名页冲突（两份 gfxA.html）
            db = root / "b" / f"{sid}__{stem}"
            sa, pa, ia = self.render_guarded(path, da, 4, frames=[0, 1, 2, 3], tabs=1,
                                             tag=f"{sid}__{stem}#a", phase="idempotency")
            sb, pb, ib = self.render_guarded(path, db, 100, frames=[99, 2], tabs=1,
                                             tag=f"{sid}__{stem}#b", phase="idempotency")
            timeout_legs = []
            errors = []
            for leg, status, stats, info in (("a", sa, pa, ia), ("b", sb, pb, ib)):
                if status == "timeout":
                    timeout_legs.append(leg)
                    self.timeout("idempotency", {
                        "phase": "idempotency", "page": f"{sid} {path} (leg {leg})", **info})
                elif (error := render_result_error(status, stats)):
                    errors.append(f"leg {leg}: {error}")
            if errors:
                self.fail("idempotency", f"{path}: {' / '.join(errors)}")
                out["pages"].append({"scenario": sid, "path": path,
                                     "tier1": "FAIL", "error": errors,
                                     "timeout_legs": timeout_legs})
                out["tier1_fail"] += 1
                continue
            if timeout_legs:
                out["pages"].append({"scenario": sid, "path": path,
                                     "tier1": "SKIP_TIMEOUT", "leg": "+".join(timeout_legs),
                                     "timeout_legs": timeout_legs})
                out["tier1_skip"] += 1
                continue
            try:
                diff, pixel_equal = self.frame_diff(da / "f0002.png", db / "f0002.png")
            except Exception as e:                              # noqa: BLE001
                self.fail("idempotency", f"{path}: 比对异常 {type(e).__name__}: {e}")
                out["pages"].append({"scenario": sid, "path": path, "tier1": "FAIL"})
                out["tier1_fail"] += 1
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
                  f"{out['tier1_fail']} FAIL / {out['tier1_skip']} SKIP(timeout)")

    # ---------------------------------------------------------- phase: s6seq
    def _capture_s6_sequence(self, html, outdir, frames):
        """Observe successful engine writes, before repeated frame names overwrite.

        This runs only inside the watchdog child, with one worker. Setup probes
        do not write frames. Observation failures are sticky, not renderer retry
        exceptions; errors from the original engine write still propagate.
        """
        raw = (outdir / "frames").resolve()
        write = gfx_render._atomic_write_bytes
        ordinal = 0
        errors = []

        def observe(path, data, *, root=None):
            nonlocal ordinal
            write(path, data, root=root)
            index = None
            try:
                target = Path(path)
                match = re.fullmatch(r"f([0-9]+)\.png", target.name)
                if match is None or target.parent.resolve() != raw:
                    return
                index = ordinal
                ordinal += 1
                write(outdir / f"step{index:04d}_f{int(match[1]):04d}.png", data)
            except Exception as error:                         # noqa: BLE001
                # Classification and archival are both observer work, never a
                # reason to retry a successful render. Retain a failed ordinal.
                label = "classification" if index is None else f"step {index}"
                errors.append(f"{label}: {type(error).__name__}: {error}"[:200])

        with mock.patch.dict(os.environ, {"VTX_GFX_STATIC": "0", "VTX_GFX_NAVFREE": "0"}), \
                mock.patch.object(gfx_render, "_atomic_write_bytes", observe):
            stats = self.render(html, raw, max(frames) + 1, frames=frames, tabs=1)
        # Keep child IPC bounded: per-occurrence evidence is on disk, not in
        # SimpleQueue (the watchdog joins before receiving its final payload).
        return {**stats, "observation_error_count": len(errors),
                "observation_errors": errors[:3]}

    def phase_s6seq(self):
        sc = self.scenario("S6")
        page = sc["pages"][0]
        sequences = {"random_jump": list(sc["sequences"]["random_jump"]["frames"]),
                     "reverse_100": list(range(99, -1, -1))}
        last = page["frames"] - 1
        size = {key: self.man["global_spec"][key] for key in ("width", "height")}
        out = {"engine_env": {"VTX_GFX_STATIC": "0", "VTX_GFX_NAVFREE": "0"},
               "tabs": 1, "skipped_refs": []}
        full = self.ev / "smoke" / "S6__timing_adversarial"
        need = sorted({min(fi, last) for frames in sequences.values() for fi in frames})
        missing = [fi for fi, _ in verify_frames(full, need, **size)]
        ref_status = "ok"
        ref_failures = len(self.failures)
        out["references"] = {"required": len(need), "catchup_frames": missing}
        if missing:
            # Preserve the reference catch-up workload; this is not a shortcut
            # or a claim that historical unique-frame rows proved the sequence.
            with mock.patch.dict(os.environ, out["engine_env"]):
                ref_status, stats, info = self.render_guarded(
                    page["path"], full, 900, frames=missing, tabs=1,
                    tag="S6__catchup", phase="s6seq")
            if ref_status == "timeout":
                self.timeout("s6seq", {"phase": "s6seq", "page": "S6 catch-up", **info})
            elif (error := render_result_error(ref_status, stats)):
                self.fail("s6seq", f"S6 catch-up {error}")
            out["references"]["failed"] = stats.get("failed") if isinstance(stats, dict) else None
        out["references"]["render_status"] = ref_status
        out["references"]["status"] = "FAIL" if len(self.failures) > ref_failures else (
            "SKIP_TIMEOUT" if ref_status == "timeout" else "PASS")
        for name, frames in sequences.items():
            before = len(self.failures)
            d = self.ev / "s6seq" / name
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
            status, stats, info = self.run_guarded(
                lambda: self._capture_s6_sequence(page["path"], d, frames),
                f"S6__{name}", d, len(frames), 1, "s6seq")
            if status == "timeout":
                self.timeout("s6seq", {"phase": "s6seq", "page": name, **info})
            elif (error := render_result_error(status, stats)):
                self.fail("s6seq", f"{name}: {error}")
            elif stats.get("observation_error_count", 1):
                self.fail("s6seq", f"{name} observation_errors="
                          f"{stats.get('observation_errors', 'missing observation result')}")
            # Disk snapshots survive watchdog termination; inspect any completed
            # prefix rather than losing observed corruption with the child.
            observed = {}
            captures = sorted(d.glob("step*.png"))
            for fp in captures:
                match = re.fullmatch(r"step([0-9]+)_f([0-9]+)\.png", fp.name)
                if match is None or int(match[1]) in observed or int(match[1]) >= len(frames):
                    self.fail("s6seq", f"{name} unexpected observation {fp.name}")
                    continue
                observed[int(match[1])] = (int(match[2]), fp)
            rows, diffs = [], []
            for index, fi in enumerate(frames):
                ref_frame = min(fi, last)
                ref = full / f"f{ref_frame:04d}.png"
                actual, fp = observed.get(index, (None, d / f"step{index:04d}_f{fi:04d}.png"))
                row = {"index": index, "requested_frame": fi, "observed_frame": actual,
                       "reference_frame": ref_frame, "artifact": str(fp.relative_to(self.ev)),
                       "reference_artifact": str(ref.relative_to(self.ev)),
                       "max_abs_diff": None}
                errors, skipped = [], []
                if actual is not None and actual != fi:
                    errors.append(f"观察顺序错误：期望 f{fi}，实际 f{actual}")
                for label, path, timed_out in (("candidate", fp, status == "timeout"),
                                               ("reference", ref, ref_status == "timeout")):
                    reason = png_invalid_reason(path, **size)
                    if reason is not None:
                        if timed_out and not path.exists():
                            skipped.append(f"{label}: timeout 未产出")
                        else:
                            errors.append(f"{label}: {reason}")
                            if label == "reference":
                                out["references"]["status"] = "FAIL"
                        if label == "reference":
                            out["skipped_refs"].append(f"{name}[{index}] f{fi} (ref f{ref_frame})")
                    else:
                        try:
                            row[f"{label}_bytes_sha256"] = self.frame_sha(path)
                        except OSError as error:
                            errors.append(f"{label} 读取异常: {error}")
                            if label == "reference":
                                out["references"]["status"] = "FAIL"
                if not errors and not skipped:
                    try:
                        diff, _ = self.frame_diff(fp, ref)
                        if type(diff) is not int or diff < 0:
                            raise ValueError("无有效像素差（尺寸不一致或比较未完成）")
                        row["max_abs_diff"] = diff
                        diffs.append(diff)
                        if diff != 0:
                            errors.append(f"max_abs_diff={diff}")
                    except Exception as error:                 # noqa: BLE001
                        errors.append(f"比对异常 {type(error).__name__}: {error}")
                row["status"] = "FAIL" if errors else "SKIP_TIMEOUT" if skipped else "PASS"
                row["errors"], row["skipped"] = errors, skipped
                for error in errors:
                    self.fail("s6seq", f"{name}[{index}] f{fi}: {error}")
                rows.append(row)
            result = "FAIL" if len(self.failures) > before else (
                "SKIP_TIMEOUT" if status == "timeout" or len(diffs) != len(frames) else "PASS")
            out[name] = {"frames": frames, "render_status": status, "status": result,
                         "expected": len(frames), "captured": len(captures),
                         "compared": len(diffs), "occurrences": rows,
                         "max_diff": max(diffs) if diffs else None}
        out["references"]["validated"] = len({
            row["reference_frame"] for name in sequences for row in out[name]["occurrences"]
            if "reference_bytes_sha256" in row})
        self.report["phases"]["s6seq"] = out
        self.note("s6seq: " + "; ".join(
            f"{name} {out[name]['status']} compared={out[name]['compared']}/{len(frames)} "
            f"max_diff={out[name]['max_diff']}" for name, frames in sequences.items()))

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
            status, st, info = self.render_guarded(
                p["path"], d, visit, frames=probe, tabs=1,
                tag=f"S7__{tag}", phase="s7")
            if status == "timeout":
                self.timeout("s7", {"phase": "s7", "page": p["path"], **info})
                out["pages"].append({"path": p["path"], "probe_frames": probe,
                                     "ok": False, "status": "timeout"})
                continue
            error = render_result_error(status, st)
            if error:
                self.fail("s7", f"{p['path']}: {error}")
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

        def _mini_run():
            shots = 0
            browser = gfx_render.Browser(self.chrome, 1)
            t0 = time.time()
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
                        shots += 1
                tab.close()
            finally:
                browser.stop()
            # 本函数跑在 fork 子进程内：self.fail() 的变更随子进程消亡，
            # 失败事实必须经返回值带回父进程裁决。
            return {"pages": mini_pages, "frames": shots, "failed": [],
                    "browser_restarts": 0,
                    "wall_s": round(time.time() - t0, 1)}

        status, payload, info = self.run_guarded(
            _mini_run, "S7__mini_run", md, 30, 1, "s7")
        if status == "timeout":
            self.timeout("s7", {"phase": "s7", "page": "mini_run", **info})
        elif (render_result_error(status, payload)
              or payload.get("frames") != 30 or payload.get("pages") != mini_pages
              or payload.get("browser_restarts") != 0
              or not isinstance(payload.get("wall_s"), (int, float))):
            self.fail("s7", f"mini_run: invalid completion result: {status} {payload}")
        else:
            # Child counters/signatures are not frame evidence. Validate the exact
            # 3 × 10 output set in the parent with the shared full PNG reader.
            expected = {f"p{pi}_f{fi:02d}.png" for pi in range(3) for fi in range(10)}
            actual = {p.name for p in md.glob("*.png")}
            invalid = {name: reason for name in sorted(expected)
                       if (reason := png_invalid_reason(
                           md / name, width=self.man["global_spec"]["width"],
                           height=self.man["global_spec"]["height"]))}
            extra = sorted(actual - expected)
            out["mini_run"] = {**payload, "non_png": len(invalid),
                               "invalid_frames": invalid, "unexpected_frames": extra,
                               "status": "FAIL" if invalid or extra else "PASS"}
            if invalid or extra:
                self.fail("s7", f"mini_run: invalid frames={invalid}, unexpected frames={extra}")
            self.note(f"s7: {len(out['pages'])} 页抽样；mini_run "
                      f"{payload['frames']} 帧单会话 {payload['wall_s']}s")
        self.report["phases"]["s7"] = out

    # ---------------------------------------------------------- phase: s8
    def phase_s8(self):
        before_failures, before_timeouts = len(self.failures), len(self.timeouts)
        out = {"status": "SKIP", "checks": {}}
        ext = self.scenario("S8")["external"]
        root = Path(ext["voice_ime_root"])
        if self.skip_external:
            out["reason"] = "--skip-external"
            self.note("s8: SKIP（--skip-external）")
            self.report["phases"]["s8"] = out
            return
        if not root.is_dir():
            out["status"] = "FAIL"
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
        if not out["checks"]["bench_p2_dir_absent"]:
            self.fail("s8", "bench_p2 存档目录仍存在")
        # 代表页抽样渲（3 页 × 1 帧）
        d = self.ev / "s8"
        if d.exists():
            shutil.rmtree(d)
        for page in ("g6a_title.html", "g6b_endboard.html", "c5a_main_path.html"):
            src = root / "gfx" / page
            if not src.is_file():
                self.fail("s8", f"voice-ime gfx/{page} 缺席")
                continue
            status, st, info = self.render_guarded(
                src, d / Path(page).stem, 1, frames=[0], tabs=1,
                tag=f"S8__{Path(page).stem}", phase="s8")
            if status == "timeout":
                self.timeout("s8", {"phase": "s8", "page": page, **info})
            elif (error := render_result_error(status, st)):
                self.fail("s8", f"{page}: {error}")
            else:
                bad = verify_frames(d / Path(page).stem, [0],
                                    width=self.man["global_spec"]["width"],
                                    height=self.man["global_spec"]["height"])
                if bad:
                    self.fail("s8", f"{page}: 抽样帧坏 {bad}")
                else:
                    out["checks"][f"sample_{page}"] = "ok"
        out["status"] = "FAIL" if len(self.failures) > before_failures else (
            "PARTIAL_TIMEOUT" if len(self.timeouts) > before_timeouts else "PASS")
        self.report["phases"]["s8"] = out
        self.note(f"s8: {out['checks']}")

    # ---------------------------------------------------------- report + main
    def write_report(self):
        self.report["env"]["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if self.failures:
            result = "FAIL"
        elif self.timeouts:
            result = "PARTIAL_TIMEOUT"
        elif not self.report["phases"] or all(p.get("status") == "SKIP"
                                             for p in self.report["phases"].values()):
            result = "FAIL"
        elif any(p.get("status") == "SKIP" for p in self.report["phases"].values()):
            result = "PARTIAL_SKIPPED"
        else:
            result = "PASS"
        self.report["result"] = result
        self.report["failures"] = self.failures
        self.report["timeouts"] = self.timeouts
        self.ev.mkdir(parents=True, exist_ok=True)
        (self.ev / "report.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=1), encoding="utf-8")
        lines = [f"# gfx_bench_suite smoke 报告（{result}）", "",
                 f"- chrome: `{self.chrome}`",
                 f"- evidence: `{self.ev}`",
                 f"- 看门狗: page_budget={self.page_budget or '公式'} "
                 f"per_frame={self.per_frame_budget}s base={self.budget_base}s "
                 f"stall={self.stall_s}s",
                 f"- failures: {len(self.failures)} · timeouts: {len(self.timeouts)}",
                 ""]
        ph = self.report["phases"]
        if "render" in ph:
            lines.append("## render（S1–S6 全量实渲）")
            lines.append("")
            lines.append("| 场景 | 页 | 状态 | 帧数 | ms/帧 | 备注 |")
            lines.append("|---|---|---|---|---|---|")
            for sid, sc in ph["render"].get("scenarios", {}).items():
                for p in sc["pages"]:
                    if p["status"] == "ok":
                        note = f"paths={p['path_counts']}"
                        ms = p["ms_per_frame"]
                    elif p["status"] == "timeout":
                        ms = p.get("est_ms_per_frame")
                        note = (f"TIMEOUT({p['reason']}) {p['elapsed_s']}s/"
                                f"{p['budget_s']}s 已落 {p['frames_done']}/{p['frames']}"
                                f" 缺 {p['missing_frames']}")
                    else:
                        ms = "-"
                        note = f"EXC {p.get('error', '')}"
                    lines.append(f"| {sid} | {p['role']} | {p['status']} | "
                                 f"{p['frames']} | {ms} | {note} |")
            lines.append("")
        if "idempotency" in ph:
            lines.append("## idempotency（DoD Tier-1）")
            lines.append("")
            for p in ph["idempotency"].get("pages", []):
                lines.append(f"- {p['scenario']} `{p['path']}`: "
                             f"max_abs_diff={p.get('max_abs_diff')} → {p['tier1']}")
            lines.append("")
        if "s6seq" in ph:
            lines.extend(["## s6seq（逐 occurrence；非历史 unique-frame 行数）", ""])
            for name in ("random_jump", "reverse_100"):
                if name not in ph["s6seq"]:
                    continue
                seq = ph["s6seq"][name]
                lines.append(f"- {name}: {seq['status']} · captured={seq['captured']}/"
                             f"{seq['expected']} · compared={seq['compared']}/"
                             f"{seq['expected']} · max_diff={seq['max_diff']}")
            lines.append("- 每次原始请求、观察帧号、钳制参考帧、PNG 字节哈希与裁决见 report.json。")
            lines.append("")
        if "s7" in ph:
            lines.append("## s7")
            lines.append("")
            lines.append(f"- 抽样: {sum(1 for p in ph['s7'].get('pages', []) if p.get('ok'))}"
                         f"/{len(ph['s7'].get('pages', []))} 页 OK")
            if ph["s7"].get("mini_run"):
                mr = ph["s7"]["mini_run"]
                lines.append(f"- mini_run: {mr['frames']} 帧单会话 {mr['wall_s']}s"
                             f"（browser_restarts=0）")
            lines.append("")
        if "s8" in ph:
            lines.append("## s8")
            lines.append("")
            lines.append(f"- status={ph['s8']['status']} checks={ph['s8'].get('checks')}")
            lines.append("")
        if self.timeouts:
            lines.append("## 受控超时登记（退出码 3 依据）")
            lines.append("")
            for t in self.timeouts:
                lines.append(f"- {t}")
            lines.append("")
        (self.ev / "report.md").write_text("\n".join(lines), encoding="utf-8")

    def run(self, phases):
        self.failures.clear()
        self.timeouts.clear()
        self.report["phases"].clear()
        available = ("manifest", "render", "idempotency", "s6seq", "s7", "s8")
        selected = [phase for phase in available if phase in phases]
        try:
            unknown = set(phases) - set(available) - {"report"}
            if unknown or not selected:
                self.fail("selection", f"无有效检查或未知阶段: {sorted(unknown) or phases}")
            else:
                for phase in selected:
                    before_failures, before_timeouts = len(self.failures), len(self.timeouts)
                    try:
                        getattr(self, f"phase_{phase}")()
                    except BaseException as error:
                        self.fail(phase, f"未完成: {type(error).__name__}: {error}")
                        self.report["phases"].setdefault(phase, {})["status"] = "FAIL"
                        if isinstance(error, SystemExit):
                            raise SystemExit(EXIT_FAIL) from error
                        raise
                    row = self.report["phases"].get(phase)
                    if row is None:
                        self.fail(phase, "阶段未产出检查报告")
                        row = self.report["phases"][phase] = {}
                    if len(self.failures) > before_failures:
                        row["status"] = "FAIL"
                    elif len(self.timeouts) > before_timeouts:
                        row["status"] = "PARTIAL_TIMEOUT"
                    else:
                        row.setdefault("status", "PASS")
                if all(p.get("status") == "SKIP" for p in self.report["phases"].values()):
                    self.fail("selection", "所选阶段全部跳过，没有完成检查")
        finally:
            self.write_report()
        if self.failures:
            print(f"\nRESULT: FAIL（{len(self.failures)} 项）", file=sys.stderr)
            for f in self.failures:
                print(f"  - {f}", file=sys.stderr)
            return EXIT_FAIL
        if self.timeouts:
            print(f"\nRESULT: PARTIAL_TIMEOUT（{len(self.timeouts)} 项受控超时，"
                  f"事实已记录）", file=sys.stderr)
            for t in self.timeouts:
                print(f"  - {t}", file=sys.stderr)
            return EXIT_TIMEOUT
        print(f"\nRESULT: {self.report['result']}")
        return EXIT_OK


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
    ap.add_argument("--page-budget", type=float, default=None,
                    help="单页绝对墙钟预算（秒）；缺省=base+per_frame×work/tabs")
    ap.add_argument("--per-frame-budget", type=float, default=10.0,
                    help="每帧墙钟配额（秒，按 tabs 摊薄进预算公式；默认 10）")
    ap.add_argument("--budget-base", type=float, default=120.0,
                    help="单页预算基数（秒；默认 120，覆盖浏览器启动/探针）")
    ap.add_argument("--stall-s", type=float, default=300.0,
                    help="停滞闸：N 秒无新帧落盘即提前击杀（默认 300；0=关）")
    args = ap.parse_args()
    if not gfx_render.available():
        print("numpy 不可用（vtx.gfx_render.available()=False）", file=sys.stderr)
        return EXIT_ENV
    chrome = find_chrome()
    if not chrome:
        print("chrome 未找到：设 VTX_TEST_CHROME=/usr/bin/google-chrome", file=sys.stderr)
        return EXIT_ENV
    phases = (["manifest", "render", "idempotency", "s6seq", "s7", "s8"]
              if args.phase == "all" else args.phase.split(","))
    smoke = Smoke(chrome, args.evidence, keep_frames=args.keep_frames,
                  skip_external=args.skip_external,
                  page_budget=args.page_budget,
                  per_frame_budget=args.per_frame_budget,
                  budget_base=args.budget_base, stall_s=args.stall_s)
    return smoke.run(phases)


if __name__ == "__main__":
    sys.exit(main())
