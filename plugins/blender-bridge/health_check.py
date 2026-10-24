#!/usr/bin/env python3
"""blender-bridge 插件 health 探针（vt doctor 经 manifest health.command 子进程调用）。

探测 Blender 版本前置（不启动 bpy）+ apps/blender-bridge/json2blend.py 在盘。
版本满足不代表运行时 API 或业务验证通过。口径区分（denoise 同款）：health 允许报
unhealthy 不崩（安装期探测）；渲染链的 fail-hard 在 finish 钩子（键已声明而
blender 缺失 → `vtx all` 收尾 exit 1）。
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_JSON2BLEND = _REPO_ROOT / "apps" / "blender-bridge" / "json2blend.py"
sys.path.insert(0, str(_JSON2BLEND.parent))


def main() -> int:
    ok = True
    bl = shutil.which("blender")
    print(f"[{'OK' if bl else 'MISS'}] blender " + (bl or "（dnf install blender / brew install --cask blender）"))
    ok = ok and bool(bl)
    if bl:
        try:
            from bundle_session import blender_version_error
            result = subprocess.run([bl, "--version"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=5)
            version = re.search(r"^Blender (\d+)\.(\d+)\.(\d+)\b", result.stdout, re.MULTILINE)
            error = (blender_version_error(tuple(map(int, version.groups())))
                     if result.returncode == 0 and version else "Blender 版本探测失败")
        except (ImportError, OSError, subprocess.TimeoutExpired) as exc:
            error = f"Blender 版本探测失败: {exc}"
        print(f"[{'MISS' if error else 'OK'}] " +
              (error or f"{version.group(0)}：仅版本前置满足；运行时 API 未验证"))
        ok = ok and not error
    j2b = _JSON2BLEND.is_file()
    print(f"[{'OK' if j2b else 'MISS'}] json2blend {_JSON2BLEND}")
    ok = ok and j2b
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
