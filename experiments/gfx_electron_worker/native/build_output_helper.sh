#!/usr/bin/env bash
# build_output_helper.sh — vtx_elec_output_helper 唯一构建入口（issue #113）。
# 纪律对齐 gfx_cef_worker/build_cef_worker.sh：本仓 experiments 面产物全部
# 隔离在本目录 build/（不入库），零网络依赖（#111 源码已随 git 入库）。
# 用法：bash experiments/gfx_electron_worker/native/build_output_helper.sh [--fast]
#   --fast 跳过 selftest（CI 已单独跑时不重复）。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="${HERE}/build"

cmake -S "${HERE}" -B "${BUILD}" -G Ninja -DCMAKE_BUILD_TYPE=Release 1>&2
cmake --build "${BUILD}" -j"$(nproc)" 1>&2

BIN="${BUILD}/bin/vtx_elec_output_helper"
[[ -x "${BIN}" ]] || { echo "✗ 构建产物缺失：${BIN}" >&2; exit 1; }

if [[ "${1:-}" != "--fast" ]]; then
  "${BIN}" --selftest || { echo "✗ canary_v2 selftest 失败" >&2; exit 1; }
fi
echo "${BIN}"
