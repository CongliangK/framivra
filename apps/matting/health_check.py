#!/usr/bin/env python3
"""health_check.py · matting app 健康探针（app.json health.command 消费）。

本目录与 resolve_matte/ 下的 .py 包含位置参数/快照脚本、
无 --help 面；且重依赖（torch/cv2/transformers）在 conda env `comfyui`，
系统解释器 import 即炸。探针用 compile() 做纯语法检查
（零依赖、零副作用、不写 __pycache__，参照 apps/aroll/health_check.py）。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
files = sorted(HERE.glob("*.py")) + sorted((HERE / "resolve_matte").glob("*.py"))
if not files:
    print("matting health: 未找到待检查脚本", file=sys.stderr)
    sys.exit(1)
for f in files:
    compile(f.read_text(encoding="utf-8"), f.name, "exec")
print(f"matting health: {len(files)} 个 .py 语法 OK")
sys.exit(0)
