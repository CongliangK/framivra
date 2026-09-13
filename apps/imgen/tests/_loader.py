#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests 公共装载器：把 imgen app 根置于 sys.path 首位后普通 import 模块。

v2 §1.4 禁 spec_from_file_location（P2a 清除）——与 apps/imgen/imgen_cli.py 同一
纪律：app 根注入 sys.path[0]，`import <name>` 解析确定落到本目录（stdlib 同名
secrets 由 sys.path[0] 优先 + 本测试进程 import 链不预缓存 stdlib secrets 双重
保证）；load() 仍校验解析结果落在 app 根内，串味时 fail-hard 而非静默错模块。
"""
import importlib
import os
import sys

IMGEN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if IMGEN_DIR not in sys.path:
    sys.path.insert(0, IMGEN_DIR)


def load(name):
    """普通 import imgen app 根内模块（缓存复用由 import 机制天然保证）。"""
    module = importlib.import_module(name)
    mod_file = getattr(module, "__file__", "") or ""
    if not mod_file.startswith(IMGEN_DIR + os.sep):
        raise ImportError(
            f"模块 {name} 解析到 app 根之外（串味）：{mod_file!r}——请检查 import 序"
        )
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
