#!/usr/bin/env python3
"""health_check.py · aroll app 健康探针（app.json health.command 消费）。

两件本体（aup3_extract.py / trim_takes.py）已具 argparse --help 面（A4），但运行
需 numpy/真实素材；探针仍用 compile() 做纯语法检查（零依赖、零副作用、不写
__pycache__，任何解释器 10s 探测超时内稳定通过）。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for name in ("aup3_extract.py", "trim_takes.py"):
    compile((HERE / name).read_text(encoding="utf-8"), name, "exec")
print("aroll health: aup3_extract.py / trim_takes.py 语法 OK")
sys.exit(0)
