#!/usr/bin/env bash
# run_all_tests.sh —— v2 统一测试运行器（P1-1，2026-09-13；P2a 起发现 apps/<app>/tests，
# P2b 起发现 plugins/<name>/tests，P4 起发现 templates/<name>/tests）
# 批次一：tests/ 全量（unittest discover，-t . 以 repo 根为 top-level —— tests 与
# vtx 包同根，`from vtx import ...` 的导入锚定与 clone 即用一致）。
# 批次二（P2a 起）：apps/<app>/tests/ 逐 app 发现（-t apps/<app> 以 app 根为
# top-level——随迁测试以 tests.test_* 导入、app 内 parents 链锚定 repo 根；各 app
# 也可独立跑：cd apps/<app> && env python3 -m unittest discover -s tests -t .）。
# 批次三（P2b 起）：plugins/<name>/tests/ 逐插件发现（同批次二式——插件测试以
# tests.test_* 导入、repo 根/sys.path 锚定由测试文件 parents 链推导；插件包经
# importlib 包导入（plugins.<name>，连字符目录名不碍 import_module），与内核
# hooks._load_plugin_module 同一路径，测试即对账）。
# 批次四（P4 起）：templates/<name>/tests/ 逐范本发现（同批次三式；skill-demo 无
# tests 目录——expected_check.sh 真渲染脚本即其验收，不入 unittest 批次）。
# 任一批次失败 → exit 非零（CI 可用）；本机纪律：一律 env python3（AppImage
# argv[0] 劫持，裸 python3 的 sys.executable 指向错误解释器，见全局 AGENTS.md）。
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
# The default remains a full reconciliation. CI runs fast on every version,
# and an independent mandatory slow job on 3.13; no fast success alone certifies the complete CI run.
mode="${1:---all}"
if [ "$#" -gt 1 ]; then echo "usage: $0 [--all|--fast|--slow|--layered]" >&2; exit 2; fi
case "$mode" in
  --all|--fast|--slow|--layered) ;;
  *) echo "unknown test mode: $mode" >&2; exit 2 ;;
esac
rc=0
if [ "$mode" = --layered ]; then
  # Always wait for both layers, including after a fast failure.
  bash "$0" --fast || rc=$?
  bash "$0" --slow || rc=$?
  exit "$rc"
fi
if [ "$mode" = --all ]; then
  env python3 -m unittest discover -s tests -t . -v || rc=$?
else
  env python3 tests/ci_partition.py "${mode#--}" || rc=$?
fi
# Component batches are fast and run once per supported version.
if [ "$mode" = --slow ]; then exit "$rc"; fi
for d in apps/*/tests plugins/*/tests templates/*/tests; do
  [ -d "$d" ] || continue
  env python3 -m unittest discover -s "$d" -t "${d%/tests}" -v || rc=$?
done
exit $rc
