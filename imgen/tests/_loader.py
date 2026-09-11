#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests 公共装载器：把 imgen/ 目录置于 sys.path 首位并按文件路径加载模块。

用 importlib 按路径加载（而非裸 import），防与 stdlib 同名模块（secrets）
在模块缓存中串味；tests 与 imgen_cli.py 使用同一套加载纪律。
"""
import importlib.util
import os
import sys

IMGEN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if IMGEN_DIR not in sys.path:
    sys.path.insert(0, IMGEN_DIR)


def load(name):
    """按文件路径加载 imgen/<name>.py（缓存命中且路径一致则复用）。"""
    path = os.path.join(IMGEN_DIR, name + ".py")
    cached = sys.modules.get(name)
    if cached is not None and getattr(cached, "__file__", "") == path:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


IMGEN_CLI = os.path.join(IMGEN_DIR, "imgen_cli.py")


def make_png(path, width, height, color="0x336699"):
    """ffmpeg 合成纯色 PNG（KB 级，测试输入不落仓、不依赖 PIL/numpy）。"""
    import subprocess

    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:size={width}x{height}",
         "-frames:v", "1", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 合成测试图失败: {proc.stderr[-300:]}")
    return str(path)
