#!/usr/bin/env bash
# comfyui_init.sh · ComfyUI 服务快速配置（2026-09-11）
#
# ComfyUI 是独立的图像生成服务（127.0.0.1:8188），video_tools 仅消费其 HTTP API
# （apps/matting/post_h3.sh 取背景图输出）；与 apps/matting/init.sh 探测的 conda env `comfyui`
# （抠像推理环境）是两回事——本脚本负责服务本体。
#
# 用法：
#   bash comfyui_init.sh            # 幂等探测：repo / env / start.sh / 8188 端口
#   bash comfyui_init.sh --install  # 实际安装：克隆 repo + 建 env + 生成 start.sh
#   bash comfyui_init.sh --start    # 启动服务（nohup 后台，日志 ~/AI/ComfyUI/comfyui.log）
#
# 网络说明：github 克隆直连失败自动走代理 http://127.0.0.1:7890；pip 走清华源，
# torch 走 pytorch 官方 cu130 源。模型 checkpoints（flux 等）不在本脚本范围——
# 权重须独立许可审查，不能用服务代码的许可覆盖；见 docs/third-party-licenses.md。
set -u

# Installing service code does not authorize arbitrary checkpoint downloads.
if [ "${1:-}" = "--install" ]; then
  LICENSE_ROOT="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().parents[2])' "${BASH_SOURCE[0]}")"
  python3 "$LICENSE_ROOT/scripts/model_license_policy.py" legacy-download comfyui || exit "$?"
fi

ROOT="${COMFYUI_ROOT:-$HOME/AI/ComfyUI}"
CPY="${COMFYUI_PY:-$HOME/miniconda3/envs/comfyui/bin/python}"
ACTION="${1:-}"

ok()   { echo "  ✓ $*"; }
miss() { echo "  ✗ $*"; }
note() { echo "  · $*"; }

probe() {
  echo "=== comfyui_init.sh 探测 ==="
  [ -d "$ROOT" ] && ok "repo：$ROOT" || miss "repo 缺失：$ROOT"
  if [ -x "$CPY" ]; then
    "$CPY" -c "import torch" 2>/dev/null && ok "env：$CPY（torch 可导入）" \
      || miss "env 存在但 torch 不可导入（$CPY）"
  else
    miss "conda env comfyui 缺失（$CPY）"
  fi
  [ -x "$ROOT/start.sh" ] && ok "start.sh：$ROOT/start.sh" || note "start.sh 未生成"
  if curl -s -m 2 -o /dev/null "http://127.0.0.1:8188" 2>/dev/null; then
    ok "服务：http://127.0.0.1:8188 在线"
  else
    note "服务未运行"
  fi
}

start_() {
  [ -x "$ROOT/start.sh" ] || { echo "start.sh 不存在，先 --install"; exit 1; }
  if curl -s -m 2 -o /dev/null "http://127.0.0.1:8188" 2>/dev/null; then
    echo "8188 已在线，无需启动"; exit 0
  fi
  nohup bash "$ROOT/start.sh" > "$ROOT/comfyui.log" 2>&1 &
  echo "已后台启动（pid $!），日志 $ROOT/comfyui.log；就绪探测：curl http://127.0.0.1:8188"
}

case "$ACTION" in
  --start)   start_ ;;
  *)         probe ;;
esac
