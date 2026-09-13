#!/usr/bin/env bash
# run_all_tests.sh —— v2 统一测试运行器（P1-1，2026-09-13；P2a 起发现 apps/<app>/tests）
# 批次一：tests/ 全量（unittest discover，-t . 以 repo 根为 top-level —— tests 与
# vtx 包同根，`from vtx import ...` 的导入锚定与 clone 即用一致）。
# 批次二（P2a 起）：apps/<app>/tests/ 逐 app 发现（-t apps/<app> 以 app 根为
# top-level——随迁测试以 tests.test_* 导入、app 内 parents 链锚定 repo 根；各 app
# 也可独立跑：cd apps/<app> && env python3 -m unittest discover -s tests -t .）。
# 任一批次失败 → exit 非零（CI 可用）；plugins/ 目录测试发现待 P2b 落地插件时同式扩展。
# 本机纪律：一律 env python3（AppImage argv[0] 劫持，裸 python3 的
# sys.executable 指向错误解释器，见全局 AGENTS.md）。
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
rc=0
env python3 -m unittest discover -s tests -t . -v || rc=$?
for d in apps/*/tests; do
  [ -d "$d" ] || continue
  env python3 -m unittest discover -s "$d" -t "${d%/tests}" -v || rc=$?
done
exit $rc
