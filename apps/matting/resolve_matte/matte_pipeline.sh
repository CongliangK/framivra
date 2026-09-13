#!/usr/bin/env bash
# 达芬奇 AI 抠像一键流水线：显存清理 → 建工程导9轨 → 鼠标序列抠像 → 逐轨带阿尔法导出
#
# 用法:
#   env bash matte_pipeline.sh <工程名> [视频目录]
#   env bash matte_pipeline.sh matte_auto_v9            # 视频目录默认 centered9
#
# 阶段（可跳过，见环境变量）:
#   SKIP_GUARD=1    跳过显存清理+重启（达芬奇已就绪时）
#   SKIP_OPS=1      跳过鼠标抠像序列（仅重新导出）
#   SKIP_EXPORT=1   跳过导出
set -euo pipefail
cd "$(dirname "$0")"

PROJECT="${1:?用法: matte_pipeline.sh <工程名> [视频目录]}"
VIDEOS="${2:-${VIDEOS:-$HOME/文档/04_bilibili视频/voice-ime/cache/bg_gen/centered9}}"
PY="env python3"

echo "==== [1/4] 显存清理 + 启动达芬奇 ===="
if [[ "${SKIP_GUARD:-0}" != "1" ]]; then
    $PY vram_guard.py --project "$PROJECT"
else
    echo "(跳过)"
fi

echo "==== [2/4] 建工程：29.97fps / 2048x1152 / 9 轨 grid9 ===="
$PY prep.py "$PROJECT"

echo "==== [3/4] 鼠标序列：9 卡 Magic Mask 抠像 ===="
if [[ "${SKIP_OPS:-0}" != "1" ]]; then
    $PY gen_sequence.py
    $PY ops_runner.py op_sequence.json
else
    echo "(跳过)"
fi

echo "==== [4/4] 逐轨导出 ProRes4444 + 阿尔法校验 ===="
if [[ "${SKIP_EXPORT:-0}" != "1" ]]; then
    $PY export_alpha.py "$PROJECT"
else
    echo "(跳过)"
fi

echo "==== 完成 ===="
echo "输出目录: $(pwd)/out/alpha_v8/ （或 export_alpha.py 第二参数指定）"
