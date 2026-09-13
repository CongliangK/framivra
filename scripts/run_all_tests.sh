#!/usr/bin/env bash
# run_all_tests.sh —— v2 统一测试运行器（P1-1，2026-09-13）
# 现阶段：tests/ 全量（unittest discover，-t . 以 repo 根为 top-level —— tests 与
# vtx 包同根，`from vtx import ...` 的导入锚定与 clone 即用一致）。
# P2 起扩展 apps/ / plugins/ 目录的测试发现。exit 码透传（CI 可用）。
# 本机纪律：一律 env python3（AppImage argv[0] 劫持，裸 python3 的
# sys.executable 指向错误解释器，见全局 AGENTS.md）。
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
exec env python3 -m unittest discover -s tests -t . -v
