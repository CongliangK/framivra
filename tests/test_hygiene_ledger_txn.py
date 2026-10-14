#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_hygiene_ledger_txn.py —— manifest.json / VERSIONS.json 账本事务回归
（issue #31 / B07，关联 F-09/#9：线程锁≠跨进程锁、写中断可损坏 JSON）。

hygiene 的三处「读-改-写」（manifest_record / versions_check / versions_record）
自 B07 起统一走 vtx/fstxn.locked_update（B05/B06 引入的共享底层：进程内
per-path 线程锁 → <目标名>.lock 的 flock 跨进程互斥 → tmp→os.replace 原子替换）。
本文件按工单验收「并发写入和模拟崩溃后记录保持一致或可恢复」对账：

  ① 损坏语义（LedgerCorruptSemantics）：账本损坏/形状不符 → 显式失败不静默
     重建（manifest_record 降级 ⚠ 且文件原样、versions_* 上抛 ValueError），
     与 receipt 的 default 恢复惯例刻意不同（取舍见 hygiene._load_ledger 注）；
     缺失 → 全新空账本；df 纯读路径容忍缺失+损坏并明确提示；
  ② 崩溃安全（CrashSafety）：SIGKILL 子进程法打点三时机——mutator 返回后
     tmp 写盘前 / tmp 落盘后 replace 前 / replace 完成后——目标要么旧值要么
     新值、永不为截断 JSON；残件只以 . 前缀 + .tmp 后缀旁名存在；陈旧 flock
     随 fd 被内核回收，后续事务不卡死；
  ③ 跨进程并发（MultiProcessConcurrency）：2 进程 × 4 线程并发
     manifest_record / versions_record（不同键），终态全键在、无交叉丢失；
  ④ gc 联动（GcResidueAndConcurrency）：fstxn 残件（.manifest.json.*.tmp /
     .VERSIONS.json.*.tmp）被 gc 清扫、账本本体与 .lock 伴生件存活；记账与
     gc 并发时账本不被破坏（已落账的笔不丢）。

运行：env python3 -m unittest tests.test_hygiene_ledger_txn -v（本机 ZCode 环境
必须 env python3 前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md）。
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入）

from vtx import hygiene                  # noqa: E402
from vtx.infra import Ctx                # noqa: E402

# ---- 子进程 worker（-c 注入；argv[1]=repo 根，其余见各脚本）----

# 并发 worker：threads 线程 × iters 笔「读-改-写」，stage/cmd 标签带
# worker/线程/序号唯一编号（不同键）——终态逐键对账：丢笔 = 某键缺失。
WORKER_LEDGER = '''
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vtx.infra import Ctx
from vtx import hygiene
c = Ctx(Path(sys.argv[2]))
mode, wid, iters, threads = sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])

def hammer(t):
    for j in range(iters):
        tag = f"w{wid}t{t}k{j}"
        if mode == "manifest":
            # Keep input fixtures outside cache/out: GC owns those directories
            # and may remove them between mkdir and write. Ledger writes and GC
            # still run concurrently against the same cache/manifest.json.
            fixture = c.root / "fixtures" / f"{tag}.bin"
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_bytes(tag.encode())
            hygiene.manifest_record(c, fixture, tag, tag)
        else:
            c.out.mkdir(parents=True, exist_ok=True)
            prod = c.out / f"final_{tag}.mp4"
            prod.write_bytes(tag.encode())
            hygiene.versions_record(c, prod, tag)

with ThreadPoolExecutor(threads) as ex:
    list(ex.map(hammer, range(threads)))
'''

# 崩溃打点公共尾：对 manifest / VERSIONS 各记一笔（被各时机 worker 拼接）。
CRASH_RUN = '''
def _run(c, mode):
    if mode == "manifest":
        fixture = c.cache / "victim.bin"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(b"victim-bytes")
        hygiene.manifest_record(c, fixture, "crash-stage", "crash-key")
    else:
        c.out.mkdir(parents=True, exist_ok=True)
        prod = c.out / "final.mp4"
        prod.write_bytes(b"final-bytes")
        hygiene.versions_record(c, prod, "crash-mux")

_run(c, sys.argv[3])
'''

# 时机①：mutator 已返回、tmp 写盘前自杀（包裹 fstxn.locked_update 注入）。
WORKER_KILL_PRE_TMP = '''
import os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vtx.infra import Ctx
from vtx import fstxn, hygiene
c = Ctx(Path(sys.argv[2]))
real_update = fstxn.locked_update

def killer(path, mutator, **kw):
    def wrap(rec):
        new = mutator(rec)
        os.kill(os.getpid(), signal.SIGKILL)   # 时机①：新值已算出、tmp 未写
        return new
    return real_update(path, wrap, **kw)

fstxn.locked_update = killer
''' + CRASH_RUN

# 时机②：tmp 已写满、os.replace 将发未发（补 os.replace 前自杀）。
WORKER_KILL_PRE_REPLACE = '''
import os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vtx.infra import Ctx
from vtx import hygiene
c = Ctx(Path(sys.argv[2]))
real_replace = os.replace

def kill_before(a, b, **kwargs):
    os.kill(os.getpid(), signal.SIGKILL)   # 时机②：tmp 落盘后、replace 前
    real_replace(a, b, **kwargs)

os.replace = kill_before
''' + CRASH_RUN

# 时机③：os.replace 已完成后自杀（old-or-new 不变量的「新值」一侧）。
WORKER_KILL_POST_REPLACE = '''
import os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vtx.infra import Ctx
from vtx import hygiene
c = Ctx(Path(sys.argv[2]))
real_replace = os.replace

def kill_after(a, b, **kwargs):
    real_replace(a, b, **kwargs)
    os.kill(os.getpid(), signal.SIGKILL)   # 时机③：replace 已完成

os.replace = kill_after
''' + CRASH_RUN


def _run_py(code: str, *args, timeout=120, expect_rc=0):
    """repo 约定的 env python3 前缀（见模块头）；PYTHONDONTWRITEBYTECODE 防
    时机②/③ worker 在 import 期写 .pyc 触发被补的 os.replace。失败回显输出。
    expect_rc：预期退出码（SIGKILL worker 的 -9 也合法）。"""
    r = subprocess.run(["env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-X", "utf8",
                        "-c", code, *map(str, args)],
                       capture_output=True, text=True, cwd=str(REPO), timeout=timeout)
    if r.returncode != expect_rc:
        raise AssertionError(f"worker 退出码 {r.returncode}（预期 {expect_rc}）:"
                             f"\n{r.stdout}\n{r.stderr}")
    return r


def _minimal_timeline(root: Path) -> Path:
    """最小合法 timeline（无源无轨——Ctx 构造不触 ffmpeg；对齐 test_fstxn 形态）。"""
    tl = {"project": {"fps": 30, "duration_frames": 90, "width": 640, "height": 360},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


class _ProjectCase(unittest.TestCase):
    """隔离临时项目基类：建 timeline + Ctx，回收临时目录。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tl = _minimal_timeline(self.root)
        self.ctx = Ctx(self.tl)

    def manifest(self) -> Path:
        return self.root / "cache" / "manifest.json"

    def versions(self) -> Path:
        return self.root / "out" / "VERSIONS.json"

    def tmp_residues(self, target: Path) -> list:
        """fstxn 事务残件（. 前缀 + .tmp 后缀旁名件，issue #31 命名形）审计。"""
        return [x for x in target.parent.iterdir()
                if x.name.startswith(f".{target.name}.") and x.name.endswith(".tmp")]

    def assert_only_sanctioned_tmp(self, target: Path):
        """同目录一切 .tmp 条目都必须是本目标的旁名残件——绝不占目标名。"""
        for x in target.parent.iterdir():
            if x.name.endswith(".tmp"):
                self.assertTrue(x.name.startswith(f".{target.name}.")
                                and x != target, f"意外残件: {x.name}")

    def quiet_gc(self):
        """gc 直调（nvenc 探测关闭防 ffmpeg 依赖；输出重定向保持输出干净）。"""
        with mock.patch.dict(os.environ, {"VTX_NO_NVENC": "1"}), \
                contextlib.redirect_stdout(io.StringIO()):
            hygiene.gc(self.ctx, do=True)

    def _product(self) -> Path:
        v = self.versions()
        v.parent.mkdir(parents=True, exist_ok=True)
        prod = v.parent / "final.mp4"
        prod.write_bytes(b"final-bytes")
        return prod


class LedgerCorruptSemantics(_ProjectCase):
    """① 损坏语义：账本不静默重建（issue #31 / B07，与 receipt 惯例的取舍）。"""

    def test_missing_ledger_starts_fresh(self):
        # 缺失 = 首笔全新账本（正常路径，非损坏）
        f = self.root / "a.bin"
        f.write_bytes(b"a")
        hygiene.manifest_record(self.ctx, f, "video", "k")
        self.assertEqual(json.loads(self.manifest().read_text(encoding="utf-8"))
                         ["entries"][0]["stage"], "video")
        hygiene.versions_record(self.ctx, self._product(), "mux")
        self.assertEqual(json.loads(self.versions().read_text(encoding="utf-8"))
                         ["versions"][0]["cmd"], "mux")

    def test_corrupt_manifest_record_warns_and_keeps_bytes(self):
        # manifest_record：损坏 → 显式 ⚠ 降级（渲染不受影响），账本字节原样留证
        f = self.manifest()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"entries": [trunc', encoding="utf-8")
        before = f.read_bytes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hygiene.manifest_record(self.ctx, self.root / "a.bin", "video", "k")
        self.assertIn("记账失败", out.getvalue())
        self.assertIn("issue #31", out.getvalue())
        self.assertEqual(f.read_bytes(), before, "损坏账本不得被空账本覆盖")
        self.assertEqual(self.tmp_residues(f), [], "失败路径不留 tmp 残件")

    def test_manifest_shape_mismatch_warns_and_keeps_bytes(self):
        # 合法 JSON 但形状不符（顶层非对象 / 缺 entries 列表）同按损坏处理
        for bad in ('{"versions": []}', '[1, 2]', '{"entries": {}}'):
            with self.subTest(bad=bad):
                f = self.manifest()
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(bad, encoding="utf-8")
                before = f.read_bytes()
                with contextlib.redirect_stdout(io.StringIO()):
                    hygiene.manifest_record(self.ctx, self.root / "a.bin", "s", "k")
                self.assertEqual(f.read_bytes(), before)

    def test_corrupt_versions_record_raises_and_keeps_bytes(self):
        # versions_record：损坏 → 显式 ValueError 上抛（中断 mux 让人看见），
        # 终片已落盘不受影响；账本字节原样留证。
        v = self.versions()
        prod = self._product()
        for bad in ('{"versions": [trunc', '{"entries": []}', '[]'):
            with self.subTest(bad=bad):
                v.write_text(bad, encoding="utf-8")
                before = v.read_bytes()
                with self.assertRaisesRegex(ValueError, "issue #31"):
                    hygiene.versions_record(self.ctx, prod, "mux")
                self.assertEqual(v.read_bytes(), before)
                self.assertEqual(self.tmp_residues(v), [])

    def test_corrupt_versions_check_raises_and_keeps_bytes(self):
        v = self.versions()
        prod = self._product()
        v.write_text('{"versions": tru', encoding="utf-8")
        before = v.read_bytes()
        with self.assertRaisesRegex(ValueError, "损坏"):
            hygiene.versions_check(self.ctx, prod)
        self.assertEqual(v.read_bytes(), before)

    def test_versions_check_superseded_and_abort_paths(self):
        # 健康路径：md5 不符 → 末条标 superseded、返回告警（单事务完成）；
        # 与末条 md5 一致 → 中止事务不写（VERSIONS.json 字节不动）。
        v = self.versions()
        prod = self._product()
        prod.write_bytes(b"old-content")
        hygiene.versions_record(self.ctx, prod, "mux")
        prod.write_bytes(b"tampered")          # 模拟外部改动
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            warn = hygiene.versions_check(self.ctx, prod)
        self.assertTrue(warn)
        self.assertIn("覆盖告警", out.getvalue())
        rec = json.loads(v.read_text(encoding="utf-8"))
        self.assertTrue(rec["versions"][0]["superseded"])
        prod.write_bytes(b"old-content")       # 恢复与末条 md5 一致的内容
        before = v.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            warn2 = hygiene.versions_check(self.ctx, prod)
        self.assertFalse(warn2)
        self.assertEqual(v.read_bytes(), before, "无不符时不得写 VERSIONS.json")

    def test_df_tolerates_missing_and_corrupt_manifest(self):
        # df 纯读路径：缺失 → 不报错；损坏 → 明确提示不中断；健康 → 展示最近一笔
        sizes = hygiene.df(self.ctx, quiet=True)
        self.assertEqual(sorted(sizes), ["cache", "out", "total"])
        f = self.manifest()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"entries": [trunc', encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hygiene.df(self.ctx)
        self.assertIn("不可解析", out.getvalue())
        self.assertIn("issue #31", out.getvalue())
        f.write_text(json.dumps({"entries": [{"mtime": "t", "stage": "video",
                                              "path": "a.bin"}]}), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hygiene.df(self.ctx)
        self.assertIn("最近一笔记账", out.getvalue())

    def test_ledger_dump_format_and_lock_artifacts(self):
        # B07 只统一锁与原子性，不改落盘格式：manifest=indent 1、VERSIONS=indent 2；
        # flock 锁文件随目标目录存在（issue #30：永不回收）。
        f = self.root / "a.bin"
        f.write_bytes(b"a")
        hygiene.manifest_record(self.ctx, f, "video", "k")
        m = self.manifest()
        self.assertEqual(m.read_text(encoding="utf-8"),
                         json.dumps(json.loads(m.read_text(encoding="utf-8")),
                                    ensure_ascii=False, indent=1))
        v = self.versions()
        hygiene.versions_record(self.ctx, self._product(), "mux")
        self.assertEqual(v.read_text(encoding="utf-8"),
                         json.dumps(json.loads(v.read_text(encoding="utf-8")),
                                    ensure_ascii=False, indent=2))
        self.assertTrue((m.parent / "manifest.json.lock").is_file())
        self.assertTrue((v.parent / "VERSIONS.json.lock").is_file())


class CrashSafety(_ProjectCase):
    """② 崩溃安全：kill -9 三时机——目标要么旧值要么新值，永不为截断 JSON。"""

    def _seed(self, mode) -> Path:
        """先落一笔种子账，拿到旧值锚点。"""
        if mode == "manifest":
            target = self.manifest()
            seed = self.root / "seed.bin"
            seed.write_bytes(b"seed")
            hygiene.manifest_record(self.ctx, seed, "seed-stage", "seed-key")
        else:
            target = self.versions()
            hygiene.versions_record(self.ctx, self._product(), "seed-mux")
        return target

    def test_kill_before_tmp_write_keeps_old_value_manifest(self):
        target = self._seed("manifest")
        old = target.read_text(encoding="utf-8")
        _run_py(WORKER_KILL_PRE_TMP, REPO, self.tl, "manifest", expect_rc=-9)
        # 时机①：新值未落 tmp、replace 永不发生 → 旧值原样、可解析
        self.assertEqual(target.read_text(encoding="utf-8"), old)
        json.loads(target.read_text(encoding="utf-8"))
        self.assert_only_sanctioned_tmp(target)

    def test_kill_before_replace_keeps_old_value_and_recovers(self):
        # 时机②：tmp 已写满、replace 未发生 → 旧值原样；残件旁名 .tmp 可被 gc
        # 清扫；陈旧 flock 随 fd 被内核回收，新进程照常记账（不被陈旧锁卡死）。
        for mode in ("manifest", "versions"):
            with self.subTest(mode=mode):
                target = self._seed(mode)
                old = target.read_text(encoding="utf-8")
                _run_py(WORKER_KILL_PRE_REPLACE, REPO, self.tl, mode, expect_rc=-9)
                self.assertEqual(target.read_text(encoding="utf-8"), old)
                json.loads(target.read_text(encoding="utf-8"))
                self.assert_only_sanctioned_tmp(target)
                self.assertTrue(self.tmp_residues(target),
                                "时机②被杀应留下 tmp 残件（旁名、不占目标名）")
                _run_py(WORKER_LEDGER, REPO, self.tl, mode, "w9", 1, 1)
                data = json.loads(target.read_text(encoding="utf-8"))
                items = data["entries" if mode == "manifest" else "versions"]
                self.assertEqual(len(items), 2, "新进程应能继续追加（不被陈旧锁卡死）")
                self.quiet_gc()
                self.assertEqual(self.tmp_residues(target), [],
                                 "gc 应清扫 fstxn 残件（issue #31）")
                self.assertTrue(target.is_file(), "gc 绝不回收账本本体")
                lock = target.parent / (target.name + ".lock")
                self.assertTrue(lock.is_file(), "锁文件伴生件永不回收（issue #30）")

    def test_kill_after_replace_leaves_new_value(self):
        # 时机③：replace 已完成 → 目标为新值（old-or-new 不变量的另一半）
        target = self._seed("manifest")
        _run_py(WORKER_KILL_POST_REPLACE, REPO, self.tl, "manifest", expect_rc=-9)
        rec = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual([e["stage"] for e in rec["entries"]],
                         ["seed-stage", "crash-stage"])
        target = self._seed("versions")
        _run_py(WORKER_KILL_POST_REPLACE, REPO, self.tl, "versions", expect_rc=-9)
        rec = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual([e["cmd"] for e in rec["versions"]],
                         ["seed-mux", "crash-mux"])


class MultiProcessConcurrency(_ProjectCase):
    """③ 跨进程并发：2 进程 × 多线程不同键齐写，终态全键在、无交叉丢失。"""

    _EXPECTED = Counter(f"w{w}t{t}k{j}"
                        for w in range(2) for t in range(4) for j in range(25))

    def _spawn(self, mode, wid):
        return subprocess.Popen(
            ["env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-X", "utf8", "-c",
             WORKER_LEDGER, str(REPO), str(self.tl), mode, str(wid), "25", "4"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(REPO))

    def test_2_processes_4_threads_manifest_no_lost_records(self):
        procs = [self._spawn("manifest", w) for w in range(2)]
        for i, pr in enumerate(procs):
            out, err = pr.communicate(timeout=120)
            self.assertEqual(pr.returncode, 0, f"worker{i} 异常退出:\n{out}\n{err}")
            self.assertNotIn("⚠", out, f"worker{i} 出现记账失败告警:\n{out}")
        rec = json.loads(self.manifest().read_text(encoding="utf-8"))
        self.assertEqual(Counter(e["stage"] for e in rec["entries"]),
                         self._EXPECTED,
                         "并发读-改-写丢笔或重复（flock/线程锁失效？）")

    def test_2_processes_4_threads_versions_no_lost_records(self):
        procs = [self._spawn("versions", w) for w in range(2)]
        for i, pr in enumerate(procs):
            out, err = pr.communicate(timeout=120)
            self.assertEqual(pr.returncode, 0, f"worker{i} 异常退出:\n{out}\n{err}")
        rec = json.loads(self.versions().read_text(encoding="utf-8"))
        self.assertEqual(Counter(e["cmd"] for e in rec["versions"]),
                         self._EXPECTED, "并发 VERSIONS 记账丢笔或重复")


class GcResidueAndConcurrency(_ProjectCase):
    """④ gc 联动：残件可清扫（issue #31 验收点）、账本与锁存活、并发 gc 不破坏账本。"""

    def test_gc_sweeps_fstxn_residue_keeps_ledger_and_lock(self):
        # 预置崩溃残件（fstxn mkstemp 命名形：. 前缀 + .tmp 后缀）与账本/锁种子
        m, v = self.manifest(), self.versions()
        m.parent.mkdir(parents=True, exist_ok=True)
        v.parent.mkdir(parents=True, exist_ok=True)
        (m.parent / ".manifest.json.k4t7x9p2.tmp").write_text('{"entries": ha')
        (v.parent / ".VERSIONS.json.q1w2e9r5.tmp").write_text('{"versions": ha')
        seed = self.root / "seed.bin"
        seed.write_bytes(b"seed")
        hygiene.manifest_record(self.ctx, seed, "seed", "k")
        hygiene.versions_record(self.ctx, self._product(), "seed-mux")
        lock_ino = (m.parent / "manifest.json.lock").stat().st_ino
        self.quiet_gc()
        self.assertFalse((m.parent / ".manifest.json.k4t7x9p2.tmp").exists(),
                         "cache 根残件应落 legacy 规则被清扫")
        self.assertFalse((v.parent / ".VERSIONS.json.q1w2e9r5.tmp").exists(),
                         "out 根残件应落 out_intermediate 规则被清扫")
        self.assertTrue(m.is_file() and v.is_file(), "账本本体永不回收")
        # 锁文件存活且 inode 不换新（issue #30：换新 inode 即破跨进程互斥）
        self.assertEqual((m.parent / "manifest.json.lock").stat().st_ino, lock_ino)
        self.assertEqual(json.loads(m.read_text(encoding="utf-8"))
                         ["entries"][0]["stage"], "seed")

    def test_gc_plan_does_not_list_ledger_or_lock(self):
        # 计划阶段断言（零删除侧证）：账本本体与 .lock 不入任何回收类
        m, v = self.manifest(), self.versions()
        m.parent.mkdir(parents=True, exist_ok=True)
        v.parent.mkdir(parents=True, exist_ok=True)
        seed = self.root / "seed.bin"
        seed.write_bytes(b"seed")
        hygiene.manifest_record(self.ctx, seed, "seed", "k")
        hygiene.versions_record(self.ctx, self._product(), "seed-mux")
        with mock.patch.dict(os.environ, {"VTX_NO_NVENC": "1"}), \
                contextlib.redirect_stdout(io.StringIO()):
            plan = hygiene.gc_plan(self.ctx)
        listed = {p for items in plan.values() for p, _, _ in items}
        self.assertNotIn(m, listed)
        self.assertNotIn(v, listed)
        self.assertNotIn(m.parent / "manifest.json.lock", listed)
        self.assertNotIn(v.parent / "VERSIONS.json.lock", listed)

    def test_worker_fixture_creation_survives_gc_between_mkdir_and_write(self):
        # Force the hosted-CI race deterministically. This suite tests ledger
        # transactions, not writing new artifacts into directories owned by GC.
        worker = WORKER_LEDGER.replace(
            "fixture.write_bytes(tag.encode())",
            "hygiene.gc(c, do=True)\n            fixture.write_bytes(tag.encode())")
        with mock.patch.dict(os.environ, {"VTX_NO_NVENC": "1"}):
            run = subprocess.run(
                ["env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-X", "utf8",
                 "-c", worker, str(REPO), str(self.tl), "manifest", "race", "1", "1"],
                capture_output=True, text=True, cwd=str(REPO), timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        entries = json.loads(self.manifest().read_text(encoding="utf-8"))["entries"]
        self.assertEqual([e["stage"] for e in entries], ["wracet0k0"])

    def test_concurrent_gc_does_not_break_ledger(self):
        # 记账进程与 gc 并发：gc 绝不删账本/锁；已落账的笔一笔不丢；账本恒可解析
        seed = self.root / "seed.bin"
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_bytes(b"seed")
        for j in range(30):
            hygiene.manifest_record(self.ctx, seed, f"seedK{j}", "k")
        self.quiet_gc()   # 预热（cli 导入/nvenc memo），保证首轮 gc 与写者真交叠
        writer = subprocess.Popen(
            ["env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-X", "utf8", "-c",
             WORKER_LEDGER, str(REPO), str(self.tl), "manifest", "g", "50", "2"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(REPO))
        rounds = 0
        while writer.poll() is None:
            self.quiet_gc()
            rounds += 1
        out, err = writer.communicate(timeout=120)
        self.assertEqual(writer.returncode, 0, f"writer 异常退出:\n{out}\n{err}")
        self.assertGreater(rounds, 0, "gc 至少与写者交叠一轮（否则本用例空转）")
        m = self.manifest()
        rec = json.loads(m.read_text(encoding="utf-8"))   # 并发全程后仍可解析
        stages = {e["stage"] for e in rec["entries"]}
        for j in range(30):
            self.assertIn(f"seedK{j}", stages, f"已落账的笔被 gc 并发破坏: seedK{j}")
        self.assertTrue(m.is_file(), "gc 不得回收 manifest.json")
        self.assertTrue((m.parent / "manifest.json.lock").is_file())
        # gc 停止后账本事务照常（锁未被并发 gc 破坏）
        hygiene.manifest_record(self.ctx, seed, "after-gc", "k")
        stages = {e["stage"] for e in json.loads(
            m.read_text(encoding="utf-8"))["entries"]}
        self.assertIn("after-gc", stages)


if __name__ == "__main__":
    unittest.main()
