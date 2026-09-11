#!/usr/bin/env bash
# 薄包装：实际初始化逻辑在跨平台 init.py（Linux/macOS/Windows 通用），保留本入口兼容旧用法。
# 用法：bash init.sh [--fix] [--json]；Windows 直接 python init.py
set -euo pipefail
cd "$(dirname "$0")"
# Windows/Git Bash 无 python3 别名（原生 Python 只有 python.exe/py.exe）——回退 python
if command -v python3 >/dev/null 2>&1; then
  exec python3 init.py "$@"
fi
exec python init.py "$@"
