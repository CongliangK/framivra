#!/usr/bin/env python3
"""tests/cache/test_fstxn.py —— vtx/runtime/fstxn 共享文件事务工具验收（B05+B06；issue #29/#30 /
F-08 / #8 的 receipt 部分）。

覆盖：
  ① locked_write / locked_update / atomic_write 基础语义：原子替换、锁文件落位、
     异常中止保持旧值且清残件、mutator 返回 None 中止不写、损坏文件回 default；
  ② 崩溃安全（「要么旧值要么新值，永不为截断 JSON」）：mutator 注入异常；
     子进程写一半 SIGKILL 自己（replace 永不发生 → 目标保持旧值，且 flock 随
     fd 生命周期被内核回收，后续事务不被陈旧锁卡死）；
  ③ 多进程并发（subprocess 2-4 进程）：4 进程 × 25 次 locked_update 计数器
     不丢笔（丢失更新探测器：无 flock 时两进程基于旧快照回写，终值 < 100）；
     4 进程并发 Ctx.receipt 更新同一 out/receipt.json，全部阶段状态都在；
  ④ 进程内 8 线程并发（与 test_pipeline.TestReceiptConcurrentLock 对齐，经
     fstxn 直测计数器）。
运行：env python3 -m unittest tests.cache.test_fstxn -v（本机 ZCode 环境必须 env
python3 前缀：AppImage 劫持 argv[0]，裸 python3 的 sys.executable 指向错误
解释器，见全局 AGENTS.md）。
"""
import json
import shutil
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入）

from tests._support import small_timeline as _minimal_timeline

from vtx.runtime import fstxn

PYTHON = os.environ.get('VTX_TEST_PYTHON') or (sys.executable if not sys.executable.endswith('.AppImage') else shutil.which('python3'))

# ---- 子进程 worker 脚本（-c 注入；argv[1]=repo 根，其余见各脚本）----
# 计数器 worker：n 次「读-改-写」自增——多进程并发下的丢失更新探测器。
WORKER_COUNTER = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vtx.runtime import fstxn
target, n = Path(sys.argv[2]), int(sys.argv[3])
for _ in range(n):
    fstxn.locked_update(target, lambda d: {"n": d.get("n", 0) + 1}, default={"n": 0})
'''

# receipt worker：构造 Ctx 后对同一 receipt.json 反复落笔自己的阶段。
WORKER_RECEIPT = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vtx.pipeline.infra import Ctx
c = Ctx(Path(sys.argv[2]))
stage, iters = sys.argv[3], int(sys.argv[4])
for k in range(iters):
    c.receipt(stage, True, f"iter {k}")
'''

# 崩溃 worker：持锁拿到 tmp 后写半截 JSON，随后 SIGKILL 自己——replace 永不发生。
WORKER_KILL = '''
import os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vtx.runtime import fstxn
with fstxn.locked_write(Path(sys.argv[2])) as tmp:
    tmp.write_text('{"stages": {"half": tru', encoding="utf-8")
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        if not kernel.TerminateProcess(kernel.GetCurrentProcess(), 99):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.kill(os.getpid(), signal.SIGKILL)
'''


def _run_py(code: str, *args, timeout=120, expect_rc=0):
    """repo 约定的 env python3 前缀（见模块头）；失败时回显 stdout/stderr。
    expect_rc：预期退出码（SIGKILL worker 的 -9 也合法）。"""
    r = subprocess.run([PYTHON, "-X", "utf8", "-c", code, *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), timeout=timeout)
    if r.returncode != expect_rc:
        raise AssertionError(f"worker 退出码 {r.returncode}（预期 {expect_rc}）:"
                             f"\n{r.stdout}\n{r.stderr}")
    return r


def _spawn(code: str, *args):
    return subprocess.Popen([PYTHON, "-X", "utf8", "-c", code, *map(str, args)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd=str(REPO))


class LockedWriteBasics(unittest.TestCase):
    """① 基础语义：原子替换、锁文件落位、异常中止保持旧值且无残件。"""

    def test_write_replaces_content_and_creates_lock_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            with fstxn.locked_write(p) as tmp:
                # The public handle pins the already-open inode, not a path
                # that an attacker could exchange before its second open.
                actual = tmp.resolve()
                self.assertTrue(actual.parent.samefile(p.parent))
                self.assertTrue(actual.name.startswith(".state.json."))
                self.assertTrue(actual.name.endswith(".tmp"))
                self.assertEqual(tmp.stat().st_ino, actual.stat().st_ino)
                tmp.write_text('{"v": 1}', encoding="utf-8")
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"v": 1})
            with fstxn.locked_write(p) as tmp:
                tmp.write_text('{"v": 2}', encoding="utf-8")
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"v": 2})
            # 锁文件 = <目标名>.lock，随目标目录存在；无 .tmp 残件
            self.assertTrue((p.parent / "state.json.lock").is_file())
            residue = [x for x in p.parent.iterdir() if x.name.endswith(".tmp")]
            self.assertEqual(residue, [])

    def test_exception_aborts_keeps_old_value_no_residue(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            with fstxn.locked_write(p) as tmp:
                tmp.write_text('{"v": "old"}', encoding="utf-8")

            def _boom(rec):
                raise RuntimeError("写一半注入异常")

            with self.assertRaises(RuntimeError):
                fstxn.locked_update(p, _boom, default={})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"v": "old"})
            residue = [x for x in p.parent.iterdir() if x.name.endswith(".tmp")]
            self.assertEqual(residue, [], "异常中止后不得残留 tmp 残件")

    def test_atomic_write_str_and_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "side.json"   # 目录不存在时自动建
            fstxn.atomic_write(p, '{"k": 1}')
            self.assertEqual(p.read_text(encoding="utf-8"), '{"k": 1}')
            fstxn.atomic_write(p, b'{"k": 2}')
            self.assertEqual(p.read_bytes(), b'{"k": 2}')
            self.assertEqual([x.name for x in p.parent.iterdir()
                              if x.name.endswith(".tmp")], [])


class LockedUpdateSemantics(unittest.TestCase):
    """① 续：default 回落、None 中止、缺省 dump 格式（与 infra 回执同款）。"""

    def test_missing_and_corrupt_fall_back_to_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            fstxn.locked_update(p, lambda x: dict(x, seen=True), default={"n": 0})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")),
                             {"n": 0, "seen": True})
            p.write_text('{"broken": tru', encoding="utf-8")   # 截断 JSON
            fstxn.locked_update(p, lambda x: dict(x, recovered=True), default={"n": 7})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")),
                             {"n": 7, "recovered": True})

    def test_default_not_shared_between_calls(self):
        # default 深拷贝：mutator 原地改写读入对象，也不得串台到共享 default
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            shared = {"n": 0}

            def _pollute(x):
                x["polluted"] = True   # 原地改写读入对象（拷贝体，非共享 default）
                return x

            fstxn.locked_update(p, _pollute, default=shared)
            self.assertEqual(shared, {"n": 0})
            p.unlink()   # 再来一次：文件缺失 → 再次回 default，仍是全新拷贝
            fstxn.locked_update(p, _pollute, default=shared)
            self.assertEqual(shared, {"n": 0})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")),
                             {"n": 0, "polluted": True})

    def test_mutator_none_aborts_without_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            p.write_text('{"v": "old"}', encoding="utf-8")
            self.assertIsNone(fstxn.locked_update(p, lambda x: None))
            self.assertEqual(p.read_text(encoding="utf-8"), '{"v": "old"}')

    def test_default_dump_matches_receipt_format(self):
        # infra 回执落盘格式 = json.dumps(..., ensure_ascii=False, indent=2)——
        # 缺省 dump 必须逐字节同款，否则 receipt.json 历史消费者（diff/黄金档）漂移
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            fstxn.locked_update(p, lambda x: {"k": "中文值"}, default={})
            self.assertEqual(p.read_text(encoding="utf-8"),
                             json.dumps({"k": "中文值"}, ensure_ascii=False, indent=2))


class CrashSafety(unittest.TestCase):
    """② 崩溃安全：目标要么旧值要么新值，永不为截断 JSON。"""

    def test_mutator_exception_keeps_old_value(self):
        # 注入异常形态的「写一半」：mutator 变更进行中抛错 → 目标保持旧值
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "receipt.json"
            old = {"input_fingerprint": "FP", "stages": {"validate": {"ok": True}}}
            p.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")

            def _mutate(rec):
                rec["stages"]["audio"] = {"ok": True}
                raise KeyboardInterrupt("^C 模拟")

            with self.assertRaises(KeyboardInterrupt):
                fstxn.locked_update(p, _mutate, default={})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), old)
            self.assertEqual([x for x in p.parent.iterdir()
                              if x.name.endswith(".tmp")], [])

    def test_sigkill_mid_write_leaves_old_value_and_lock_released(self):
        # 硬杀形态：子进程持锁拿到 tmp、写入半截 JSON 后 SIGKILL 自己——
        # replace 永不发生 → 目标保持旧值；flock 随 fd 被内核回收，后续事务不卡死
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "receipt.json"
            old = '{"input_fingerprint": "FP", "stages": {"validate": {"ok": true}}}'
            p.write_text(old, encoding="utf-8")
            _run_py(WORKER_KILL, REPO, p, expect_rc=99 if os.name == "nt" else -9)   # 被 SIGKILL 是预期结局
            # 旧值原样、可解析（永不截断）
            self.assertEqual(p.read_text(encoding="utf-8"), old)
            json.loads(p.read_text(encoding="utf-8"))
            # 残件只可能是 . 前缀 .tmp 后缀的旁名文件，绝不占目标名
            for x in p.parent.iterdir():
                if x != p and not x.name.endswith(".lock"):
                    self.assertTrue(x.name.startswith(".receipt.json.")
                                    and x.name.endswith(".tmp"),
                                    f"意外残件: {x.name}")
            # 陈旧锁不卡后续事务（子进程跑，防回归时卡死本测试进程）
            _run_py(WORKER_COUNTER, REPO, p, 1)
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"n": 1})

    def test_kill_after_replace_leaves_new_value(self):
        # 「新值」一侧的边界：正常完成后目标即新值（old-or-new 不变量的另一半）
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "receipt.json"
            p.write_text('{"n": 0}', encoding="utf-8")
            _run_py(WORKER_COUNTER, REPO, p, 3)
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"n": 3})


class MultiProcessConcurrency(unittest.TestCase):
    """③ 多进程并发：flock 串行化「读-改-写」，不丢笔（issue #29/#30 场景）。"""

    def test_4_processes_counter_no_lost_updates(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "counter.json"
            procs = [_spawn(WORKER_COUNTER, REPO, p, 25) for _ in range(4)]
            for i, pr in enumerate(procs):
                self.assertEqual(pr.wait(timeout=120), 0, f"worker{i} 异常退出")
            # 无 flock 时两进程基于旧快照回写互相覆盖，终值几乎必然 < 100
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["n"], 100)

    def test_2_processes_counter_no_lost_updates(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "counter.json"
            procs = [_spawn(WORKER_COUNTER, REPO, p, 50) for _ in range(2)]
            for pr in procs:
                self.assertEqual(pr.wait(timeout=120), 0)
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["n"], 100)

    def test_parallel_ctx_receipt_no_lost_stages(self):
        """4 个 vtx 进程并发对同一 out/receipt.json 各落笔自己的阶段——join 后
        4 个阶段全部在、指纹一致（丢笔=某阶段消失或整份回执被旧快照覆盖）。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tl = _minimal_timeline(root)
            procs = [_spawn(WORKER_RECEIPT, REPO, tl, f"s{i}", 5) for i in range(4)]
            for i, pr in enumerate(procs):
                self.assertEqual(pr.wait(timeout=120), 0, f"receipt worker{i} 异常退出")
            rec = json.loads((root / "out" / "receipt.json").read_text(encoding="utf-8"))
            for i in range(4):
                self.assertIn(f"s{i}", rec["stages"],
                              f"s{i} 的阶段状态被并发写覆盖丢失（flock 失效？）")
                self.assertTrue(rec["stages"][f"s{i}"]["ok"])
                self.assertEqual(rec["stages"][f"s{i}"]["detail"], "iter 4")
            # 身份门完好：跨进程指纹一致（全部 worker 与终态回执同一 input_fingerprint）
            from vtx.pipeline.infra import Ctx
            self.assertEqual(rec["input_fingerprint"], Ctx(tl).input_fingerprint)
            # 主回执本体是合法整体 JSON（非撕裂半写件）
            json.loads((root / "out" / "receipt.json").read_text(encoding="utf-8"))


class MultiThreadConcurrency(unittest.TestCase):
    """④ 进程内并发（per-path 线程锁层）：8 线程 × 10 次自增不丢笔。"""

    def test_8_threads_counter_no_lost_updates(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "counter.json"

            def hammer(_i):
                for _ in range(10):
                    fstxn.locked_update(p, lambda x: {"n": x.get("n", 0) + 1},
                                        default={"n": 0})

            with ThreadPoolExecutor(8) as ex:
                list(ex.map(hammer, range(8)))
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["n"], 80)


if __name__ == "__main__":
    unittest.main()
