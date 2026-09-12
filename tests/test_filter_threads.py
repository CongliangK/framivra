#!/usr/bin/env python3
"""tests/test_filter_threads.py —— 滤镜线程数可配置（方案二阶段1）单元测试。

覆盖：
  ① filter_threads() env 解析：默认 1（bench_p2 全链 A/B 后维持，见下）/
     VTX_FILTER_THREADS 合法正整数采纳（封顶 cpu 核数）/ 0、负数、非数字回落 1
     （照抄 io_workers 的解析风格）；
  ② 61 层 ±20 帧密集端点复现图，两形态各断言 threads=1 与 4 输出 MD5 逐位相等
     （2026-09-11/12 两轮实验以此证伪 v3.6「多线程 framesync 调度竞态」归因：
     MD5 一致且 4 快 1.9×，26.17s vs 13.54s）：
     形态A 活跃传输中段带——次输入 120 帧=4s，端点带 [1,3] 全程避开次输入头尾，
     贴近 v3.6 病灶记载「病灶在次输入流传输中段」的真几何；
     形态B EOF-repeat 带——次输入 60 帧=2s，同一端点带的后半与全部关闭沿落在
     EOF 之后 repeat 态，即 exp6d 原实验的实际形态（复审勘误：exp6d 的 gfx 序列
     实为 60 帧，其 [4,6] 带整体处于 EOF-repeat 态，并非活跃传输中段）。
     两形态都收编，防未来 ffmpeg 升级引入线程相关输出差异静默漏网。
     耗时只打印不断言（机器负载会抖）；素材刻意选小（640x360@30、主输入 4s），
     单次 ffmpeg ~秒级。需 ffmpeg 与 libx264（缺任一 skip）。
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


def _have_libx264() -> bool:
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    r = subprocess.run([ff, "-hide_banner", "-encoders"],
                       capture_output=True, text=True)
    return "libx264" in (r.stdout + r.stderr)

_HAVE_X264 = _have_libx264()


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

    def test_oversized_capped_to_cpu_count(self):
        # 超大值封顶 cpu 核数：滤镜线程超核数无收益只增调度开销（对齐 io_workers）
        self.assertEqual(self._get("999999"), max(1, os.cpu_count() or 8))


@unittest.skipUnless(shutil.which("ffmpeg") and _HAVE_X264,
                     "需要 ffmpeg 与 libx264 编码器")
class TestDense61Equivalence(unittest.TestCase):
    """61 层密集端点复现图（两形态）：threads=1 与 4 输出 MD5 必须逐位一致。

    公共形态：主输入 color=gray 640x360@30 共 4s，61 路同一 PNG 序列（testsrc）
    经 format=bgra,setpts=PTS-STARTPTS 后逐层 overlay，每层
    enable='between(t,lo,hi)':eof_action=repeat，端点 lo=1.0+k*0.01 /
    hi=3.0+k*0.01（k=1..61，两条各 0.6s 宽 < ±20 帧×2 的密集带）。
    与 exp6d 的差异仅缩放（1920x1080→640x360、主输入 10s→4s，控制单次耗时），
    端点带取 [1,3] 使其落在 4s 主输入流内（exp6d 原带 [4,6] 在 4s 流外、
    enable 永不开启，无法构成有效断言）。两形态靠次输入帧数区分（120 帧 vs 60 帧）。
    不加 -fps_mode cfr（CFR 复制补帧会掩盖丢帧，此处要的就是裸差异检测）。
    """

    N = 61
    W, H, FPS, DUR = 640, 360, 30, 4   # 主输入 4s=120 帧

    def _gen_seq(self, d: Path, frames: int):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"testsrc=size={self.W}x{self.H}:rate={self.FPS}"
                              f":duration={frames / self.FPS}",
                        "-start_number", "0",      # image2 muxer 默认从 1 起，对齐 f0000
                        str(d / "f%04d.png")], check=True)
        self.assertTrue((d / "f0000.png").exists(), "PNG 序列未生成")

    def _dense61_md5_pair(self, frames: int, tag: str):
        """生成 frames 帧序列 → 61 路输入叠密集端点带 → 返回 (threads=1, threads=4) MD5。"""
        with tempfile.TemporaryDirectory(prefix="vtx_ft_") as td:
            d = Path(td)
            self._gen_seq(d, frames)
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
                    ["ffmpeg", "-v", "error", "-y",
                     "-filter_complex_threads", str(threads),
                     "-f", "lavfi", "-i",
                     f"color=c=gray:s={self.W}x{self.H}:r={self.FPS}:d={self.DUR}",
                     *inputs, "-filter_complex", graph, "-map", "[v]",
                     "-c:v", "libx264", "-preset", "veryfast", "-f", "md5", "-"],
                    capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-1500:])
                md5 = r.stdout.strip().partition("=")[2]
                self.assertTrue(md5, f"MD5 输出解析失败：{r.stdout!r}")
                print(f"    · {tag} threads={threads}: md5={md5}"
                      f"（{time.time() - t0:.1f}s，仅计时不断言）")
                return md5

            return run_md5(1), run_md5(4)

    def _assert_pair(self, frames: int, tag: str):
        m1, m4 = self._dense61_md5_pair(frames, tag)
        self.assertEqual(m1, m4,
                         f"{tag}：threads=1 与 4 输出 MD5 不一致，滤镜多线程差异回归，"
                         "须重查 framesync 调度（VTX_FILTER_THREADS=1 可临时回退）")

    def test_form_a_active_transmission_band(self):
        """形态A：次输入 120 帧=4s，端点带 [1,3] 全程处于活跃传输中段（v3.6 真病灶几何）。"""
        self._assert_pair(120, "dense61A(传输中段带)")

    def test_form_b_eof_repeat_band(self):
        """形态B：次输入 60 帧=2s，带后半（t>2.0）与全部关闭沿落在 EOF-repeat 态
        ——exp6d 原实验的实际形态（其 gfx 序列仅 60 帧，[4,6] 带整体在 EOF 后）。"""
        self._assert_pair(60, "dense61B(EOF-repeat带)")


if __name__ == "__main__":
    unittest.main()
