#!/usr/bin/env python3
"""tests/test_filter_threads.py —— 滤镜线程数可配置（方案二阶段1）单元测试。

覆盖：
  ① filter_threads() env 解析：默认 1（bench_p2 全链 A/B 后维持，见下）/
     VTX_FILTER_THREADS 合法正整数采纳 / 0、负数、非数字一律回落 1（照抄
     io_workers 的解析风格）；
  ② 61 层 ±20 帧密集端点复现图（/tmp/bench/exp6d.sh 收编，v3.6 病灶形态）：
     61 路 PNG 序列叠 overlay 链，全部层 enable 端点落在两条 0.6s 密集带内，
     threads=1 与 threads=4 两次输出 MD5 必须逐位相等——2026-09-11/12 两轮实验
     以此形态证伪 v3.6「多线程 framesync 调度竞态」归因（MD5 一致且 4 快 2.0×），
     本测试把证伪实验固化为回归门：未来 ffmpeg 升级若真引入线程相关输出差异，
     此处立即报警。耗时只打印不断言（机器负载会抖）；素材刻意选小
     （640x360@30、主输入 4s=120 帧），单次 ffmpeg ~秒级。
  A/B 结论（2026-09-12，bench_p2 全链）：合成复现图 MD5 等价、全链 QC 75 项
  字段级一致、5/8 段逐字节全等，但真实项目前 3 段存在与线程数无关的跨运行
  非确定性（同 argv 复渲 4 次 4 个 md5），逐字节等价门不可复现；且 8 段并行下
  threads=4 无墙钟收益（98/99s vs 99s）——默认维持 1，=4 需显式 VTX_FILTER_THREADS。
运行：env python3 -m unittest discover tests -v（本机 ZCode 环境必须 env python3 前缀，
裸 python3 的 sys.executable 被 AppImage 劫持，见全局 AGENTS.md；也可单跑：
env python3 -m unittest tests.test_filter_threads -v）
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "vtx"))

from infra import filter_threads


class TestFilterThreadsEnv(unittest.TestCase):
    """env 解析（纯函数，零渲染）：默认 1（A/B 后维持），合法正整数采纳，非法回落 1。"""

    def _get(self, val):
        old = os.environ.get("VTX_FILTER_THREADS")
        os.environ.pop("VTX_FILTER_THREADS", None)
        if val is not None:
            os.environ["VTX_FILTER_THREADS"] = val
        try:
            return filter_threads()
        finally:
            if old is None:
                os.environ.pop("VTX_FILTER_THREADS", None)
            else:
                os.environ["VTX_FILTER_THREADS"] = old

    def test_default(self):
        self.assertEqual(self._get(None), 1)   # bench_p2 A/B 后默认维持 1

    def test_valid_values(self):
        self.assertEqual(self._get("1"), 1)
        self.assertEqual(self._get("4"), 4)    # 显式解锁多线程
        self.assertEqual(self._get("16"), 16)

    def test_invalid_falls_back_to_1(self):
        for bad in ("0", "-1", "abc", "", "4.5", " 4"):
            self.assertEqual(self._get(bad), 1, repr(bad))


@unittest.skipUnless(shutil.which("ffmpeg"), "需要 ffmpeg")
class TestDense61Equivalence(unittest.TestCase):
    """61 层密集端点复现图：threads=1 与 4 输出 MD5 必须逐位一致。

    复刻 exp6d 形态：主输入 color=gray，61 路同一 PNG 序列经
    format=bgra,setpts=PTS-STARTPTS 后逐层 overlay，每层
    enable='between(t,lo,hi)':eof_action=repeat，全部层端点落在 ±20 帧密集带。
    与 exp6d 的差异仅有两处缩放：分辨率 1920x1080→640x360、主输入 10s→4s
    （控制单次耗时）；端点带随之从 [4,6] 平移到 [1,3]——主输入缩短后原带
    [4.01,6.61] 已在流外（末帧 pts≈3.967，enable 永不开启），平移保持
    「密集端点落在次输入流（120 帧=4s）传输中段」的 v3.6 病灶几何：
    窗口 [1.01,3.61] 全程避开次输入头尾，EOF(4.0s) 在所有窗口关闭沿之后。
    不加 -fps_mode cfr（CFR 复制补帧会掩盖丢帧，此处要的就是裸差异检测）。
    """

    N = 61
    W, H, FPS, DUR = 640, 360, 30, 4   # 主输入 4s=120 帧，与 PNG 序列等长

    def test_dense_endpoints_md5_equal(self):
        ff = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory(prefix="vtx_ft_") as td:
            d = Path(td)
            # 素材：testsrc 640x360@30 共 120 帧 PNG 序列，61 路输入共用
            subprocess.run([ff, "-v", "error", "-y",
                            "-f", "lavfi",
                            "-i", f"testsrc=size={self.W}x{self.H}:rate={self.FPS}"
                                  f":duration={self.DUR}",
                            "-start_number", "0",      # image2 muxer 默认从 1 起，对齐 f0000
                            str(d / "f%04d.png")], check=True)
            self.assertTrue((d / "f0000.png").exists(), "PNG 序列未生成")
            inputs = []
            for _ in range(self.N):
                inputs += ["-framerate", str(self.FPS), "-i", str(d / "f%04d.png")]
            parts, cur = [], "[0:v]"
            for k in range(1, self.N + 1):
                lo, hi = 1.0 + k * 0.01, 3.0 + k * 0.01   # 两条各 0.6s 的密集端点带
                parts.append(f"[{k}:v]format=bgra,setpts=PTS-STARTPTS[L{k}]")
                parts.append(f"{cur}[L{k}]overlay=x={k * 20 % (self.W - 80)}"
                             f":y={k * 13 % (self.H - 40)}:"
                             f"enable='between(t,{lo:.2f},{hi:.2f})':"
                             f"eof_action=repeat[o{k}]")
                cur = f"[o{k}]"
            parts.append(f"{cur}format=yuv420p[v]")
            graph = ";".join(parts)

            def run_md5(threads):
                t0 = time.time()
                r = subprocess.run(
                    [ff, "-v", "error", "-y",
                     "-filter_complex_threads", str(threads),
                     "-f", "lavfi", "-i",
                     f"color=c=gray:s={self.W}x{self.H}:r={self.FPS}:d={self.DUR}",
                     *inputs, "-filter_complex", graph, "-map", "[v]",
                     "-c:v", "libx264", "-preset", "veryfast", "-f", "md5", "-"],
                    capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-1500:])
                md5 = r.stdout.strip().partition("=")[2]
                self.assertTrue(md5, f"MD5 输出解析失败：{r.stdout!r}")
                print(f"    · dense61 threads={threads}: md5={md5}"
                      f"（{time.time() - t0:.1f}s，仅计时不断言）")
                return md5

            m1 = run_md5(1)
            m4 = run_md5(4)
            self.assertEqual(m1, m4,
                             "threads=1 与 4 输出 MD5 不一致：滤镜多线程差异回归，"
                             "须重查 framesync 调度（VTX_FILTER_THREADS=1 可临时回退）")


if __name__ == "__main__":
    unittest.main()
