#!/usr/bin/env python3
"""denoise 插件 health 探针（vt doctor 经 manifest health.command 子进程调用）。

探测三项（denoise_init.sh 同口径的机器自检子集）：ffmpeg / conda denoise
解释器 / Resemble enhancer_stage2 权重。只查文件存在性，不 import torch/
resemble_enhance（秒回，doctor 10s 探测超时内稳定）；缺件打印明确缺什么 +
修复指引，exit 1。口径区分：health 允许报 unhealthy 不崩（安装期探测）；
渲染链的 fail-hard 在 prepare_source（缺件即阶段失败退出，§2.2 缝 A）。
"""
import shutil
import sys

from denoise_voice import conda_python, run_dir   # 同目录模块（脚本模式 sys.path 自含）


def main() -> int:
    ok = True
    ff = shutil.which("ffmpeg")
    print(f"[{'OK' if ff else 'MISS'}] ffmpeg " + (ff or "（系统包管理器安装）"))
    ok = ok and bool(ff)
    py = conda_python()
    print(f"[{'OK' if py.is_file() else 'MISS'}] conda denoise 解释器 {py}"
          + ("" if py.is_file() else "（bash plugins/denoise/denoise_init.sh）"))
    ok = ok and py.is_file()
    weight = run_dir() / "ds/G/default/mp_rank_00_model_states.pt"
    print(f"[{'OK' if weight.is_file() else 'MISS'}] Resemble 权重 {weight}"
          + ("" if weight.is_file() else "（旧 --download 为 BLOCKED；许可见 docs/third-party-licenses.md；离线证据：python3 scripts/model_license_policy.py plan resemble-enhance，不下载、不授予许可）"))
    ok = ok and weight.is_file()
    print("denoise health: " + ("healthy" if ok else "unhealthy（缺件见上方 MISS 行）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
