#!/usr/bin/env bash
# apps/matting/init.sh · 抠像链幂等初始化探测（2026-09-11）
#
# 链路：BiRefNet_HR@2048 首帧 mask → MatAnyone2 视频抠像 → guided filter 收边。
# 三项硬依赖（README「依赖与开启步骤」的探测化）：
#   1. conda env `comfyui`（torch GPU 栈 + cv2/omegaconf/hydra/safetensors/transformers）
#   2. 两个代码克隆，必须与本目录平级（runner 用相对路径 sys.path/load）：
#      MatAnyone2/（github.com/pq-yang/MatAnyone2 + HF PeiqingYang/MatAnyone2 权重 141MB）
#      BiRefNet_HR/（HF ZhengPeng7/BiRefNet_HR-matting，transformers 本地加载）
#   3. torch hub ResNet18（MatAnyone2 光流启动静默下载，被墙即假死，须预置）
# 可选：ComfyUI 服务（8188，仅背景图生成用）→ 见 comfyui_init.sh
#
# 不带参数只探测已有资产；旧 --download 在探测前 BLOCKED，不补齐缺件。
# 许可限制与未知项见 docs/third-party-licenses.md；离线 plan 不下载、不授予许可。
set -u

# Do not clone code or fetch unknown weights under a blanket download switch.
for license_arg in "$@"; do
  if [ "$license_arg" = "--download" ]; then
    LICENSE_ROOT="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().parents[2])' "${BASH_SOURCE[0]}")"
    python3 "$LICENSE_ROOT/scripts/policy/model_license_policy.py" legacy-download matting || exit "$?"
  fi
done

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
CPY="${COMFYUI_PY:-$HOME/miniconda3/envs/comfyui/bin/python}"
MA2="$HERE/MatAnyone2"
MA2_W="$MA2/pretrained_models/hf2"
BR="$HERE/BiRefNet_HR"
TORCH_HUB="$HOME/.cache/torch/hub/checkpoints"
RESNET="$TORCH_HUB/resnet18-5c106cde.pth"

OK=0; MISS=0
ok()   { OK=$((OK+1));   echo "  ✓ $*"; }
miss() { MISS=$((MISS+1)); echo "  ✗ $*"; }

echo "=== apps/matting/init.sh（已有资产探测；旧 --download 为 BLOCKED）==="

# ---- 1. conda env comfyui ----
if [ -x "$CPY" ]; then
  DEP_MISS=""
  for m in cv2 omegaconf hydra safetensors transformers torch; do
    "$CPY" -c "import $m" 2>/dev/null || DEP_MISS="$DEP_MISS $m"
  done
  if [ -z "$DEP_MISS" ]; then
    ok "comfyui env：$CPY（六项依赖齐）"
  else
    miss "comfyui env 缺依赖:$DEP_MISS —— $CPY -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple opencv-python omegaconf hydra-core safetensors transformers"
  fi
else
  miss "conda env comfyui 不存在（$CPY）—— conda create -n comfyui python=3.11 && $HOME/miniconda3/envs/comfyui/bin/pip install torch --index-url https://download.pytorch.org/whl/cu130 && $HOME/miniconda3/envs/comfyui/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple opencv-python numpy Pillow omegaconf hydra-core safetensors transformers"
fi

# ---- 2. MatAnyone2 平级克隆（代码 + 权重）----
if [ -d "$MA2/matanyone2" ]; then
  if [ -s "$MA2_W/model.safetensors" ]; then
    ok "MatAnyone2：$MA2（代码+权重齐）"
  else
    miss "MatAnyone2 缺权重 $MA2_W/model.safetensors（141MB，HF PeiqingYang/MatAnyone2）"
  fi
else
  miss "MatAnyone2 克隆缺失 $MA2；旧 --download 为 BLOCKED，许可限制与未知项见 docs/third-party-licenses.md"
fi

# ---- 3. BiRefNet_HR 平级目录 ----
if [ -d "$BR" ] && ls "$BR"/config.json >/dev/null 2>&1; then
  ok "BiRefNet_HR：$BR"
else
  miss "BiRefNet_HR 缺失 $BR（HF ZhengPeng7/BiRefNet_HR-matting 快照，run_birefnet_hr.py local_files_only 加载）"
fi

# ---- 4. torch hub ResNet18（光流隐藏依赖，被墙假死坑）----
if [ -s "$RESNET" ]; then
  ok "ResNet18 torch hub：$RESNET"
elif [ -s "$HERE/resnet18-5c106cde.pth" ]; then
  ok "ResNet18（脚本同目录副本）：$HERE/resnet18-5c106cde.pth"
else
  miss "ResNet18 预置缺失 $RESNET；权重许可未知项见 docs/third-party-licenses.md，旧 --download 为 BLOCKED"
fi

# ---- 5. ComfyUI 服务（可选，仅背景生成）----
if curl -s -m 2 -o /dev/null "http://127.0.0.1:8188" 2>/dev/null; then
  ok "ComfyUI 服务：8188 在线"
elif [ -x "$HOME/AI/ComfyUI/start.sh" ]; then
  echo "  · ComfyUI 未运行但已配置（bash ~/AI/ComfyUI/start.sh；详见 comfyui_init.sh）"
else
  echo "  · ComfyUI 未配置（可选，仅背景图生成需要；探测见 bash $(dirname "$0")/comfyui_init.sh；旧 --install 为 BLOCKED，见 docs/third-party-licenses.md）"
fi

echo "--- 汇总：✓ $OK / ✗ $MISS ---"
[ "$MISS" -eq 0 ] && echo "✅ 抠像链就绪；可选 ComfyUI 背景生成见 comfyui_init.sh"
exit 0
