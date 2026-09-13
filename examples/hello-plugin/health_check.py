#!/usr/bin/env python3
"""hello-plugin 健康探针（doctor health.command 消费，v2 §5.1 / §2.2 缝 A）。

约定：exit 0 = 健康（stdout 首行被 doctor 摘要展示）；非 0 = unhealthy（enabled
件的探针失败计入 doctor FAIL 门）。本插件唯一环境依赖 = ffmpeg。
"""
import shutil
import sys

ffmpeg = shutil.which("ffmpeg")
if ffmpeg:
    print(f"hello-plugin healthy｜ffmpeg {ffmpeg}")
    sys.exit(0)
print("hello-plugin unhealthy：PATH 缺 ffmpeg（渲染期 prepare_source 将 fail-hard）")
sys.exit(1)
