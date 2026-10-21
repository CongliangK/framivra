#!/usr/bin/env python3
"""health_check.py · aroll app 健康探针（app.json health.command 消费）。

提取与裁剪 CLI 已具 argparse --help 面，但运行需 numpy/真实素材；探针对
它们与共享 WAV 实现用 compile() 做纯语法检查（零依赖、零副作用、不写
__pycache__，任何解释器 10s 探测超时内稳定通过）。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
names = ("aup3_extract.py", "trim_takes.py", "wav_float32.py")
for name in names:
    compile((HERE / name).read_text(encoding="utf-8"), name, "exec")
print("aroll health: " + " / ".join(names) + " 语法 OK")
sys.exit(0)
