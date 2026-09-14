#!/usr/bin/env python3
"""blender-bridge 插件 health 探针（vt doctor 经 manifest health.command 子进程调用）。

探测两项：blender 可执行（json2blend/verify_blend 的运行解释器）+ apps/
blender-bridge/json2blend.py 在盘。口径区分（denoise 同款）：health 允许报
unhealthy 不崩（安装期探测）；渲染链的 fail-hard 在 finish 钩子（键已声明而
blender 缺失 → `vtx all` 收尾 exit 1）。
"""
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_JSON2BLEND = _REPO_ROOT / "apps" / "blender-bridge" / "json2blend.py"


def main() -> int:
    ok = True
    bl = shutil.which("blender")
    print(f"[{'OK' if bl else 'MISS'}] blender " + (bl or "（dnf install blender / brew install --cask blender）"))
    ok = ok and bool(bl)
    j2b = _JSON2BLEND.is_file()
    print(f"[{'OK' if j2b else 'MISS'}] json2blend {_JSON2BLEND}")
    ok = ok and j2b
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
