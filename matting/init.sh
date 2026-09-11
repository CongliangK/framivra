#!/usr/bin/env bash
# matting/init.sh · 抠像链幂等初始化探测（2026-09-11）
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
# 下载渠道：HF 权重默认 hf-mirror.com（国内实测 2.0MB/s）；github 克隆直连失败自动
# 换代理 https_proxy=$PROXY。--download 实际执行缺失项下载；不带参数只探测。
set -u

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
CPY="${COMFYUI_PY:-$HOME/miniconda3/envs/comfyui/bin/python}"
MA2="$HERE/MatAnyone2"
MA2_W="$MA2/pretrained_models/hf2"
BR="$HERE/BiRefNet_HR"
TORCH_HUB="$HOME/.cache/torch/hub/checkpoints"
RESNET="$TORCH_HUB/resnet18-5c106cde.pth"
MIRROR="https://hf-mirror.com"
PROXY="http://127.0.0.1:7890"
PY="${1:-}"
[ "$PY" = "--download" ] && shift 2>/dev/null

OK=0; MISS=0
ok()   { OK=$((OK+1));   echo "  ✓ $*"; }
miss() { MISS=$((MISS+1)); echo "  ✗ $*"; }

echo "=== matting/init.sh（幂等探测；--download 补缺失项）==="

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
    NEED_MA2_W=1
  fi
else
  miss "MatAnyone2 克隆缺失 $MA2 —— git clone https://github.com/pq-yang/MatAnyone2 $MA2（直连失败自动走代理）+ 权重下载"
  NEED_MA2=1; NEED_MA2_W=1
fi

# ---- 3. BiRefNet_HR 平级目录 ----
if [ -d "$BR" ] && ls "$BR"/config.json >/dev/null 2>&1; then
  ok "BiRefNet_HR：$BR"
else
  miss "BiRefNet_HR 缺失 $BR（HF ZhengPeng7/BiRefNet_HR-matting 快照，run_birefnet_hr.py local_files_only 加载）"
  NEED_BR=1
fi

# ---- 4. torch hub ResNet18（光流隐藏依赖，被墙假死坑）----
if [ -s "$RESNET" ]; then
  ok "ResNet18 torch hub：$RESNET"
elif [ -s "$HERE/resnet18-5c106cde.pth" ]; then
  ok "ResNet18（脚本同目录副本）：$HERE/resnet18-5c106cde.pth"
else
  miss "ResNet18 预置缺失 $RESNET —— download.pytorch.org/models/resnet18-5c106cde.pth（约 45MB）"
  NEED_RESNET=1
fi

# ---- 5. ComfyUI 服务（可选，仅背景生成）----
if curl -s -m 2 -o /dev/null "http://127.0.0.1:8188" 2>/dev/null; then
  ok "ComfyUI 服务：8188 在线"
elif [ -x "$HOME/AI/ComfyUI/start.sh" ]; then
  echo "  · ComfyUI 未运行但已配置（bash ~/AI/ComfyUI/start.sh；详见 comfyui_init.sh）"
else
  echo "  · ComfyUI 未配置（可选，仅背景图生成需要——bash $(dirname "$0")/comfyui_init.sh --install）"
fi

# ---- --download：补缺失项 ----
if [ "$PY" = "--download" ]; then
  echo "--- 开始补齐缺失项 ---"
  dl_hf() { # dl_hf <repo_id> <local_dir>
    # heredoc 走 stdin，Git Bash 的 /c/Users/... 路径不经 MSYS 参数转换，Windows
    # Python 会解析到盘根 \c\Users\... 下错位置——cygpath -w 转 Windows 路径兜底
    #（非 MSYS 环境无 cygpath，原样返回，Linux/macOS 行为不变）
    local dl_dir
    dl_dir="$(cygpath -w "$2" 2>/dev/null || echo "$2")"
    HF_ENDPOINT="$MIRROR" "$CPY" - <<PYEOF || return 1
from huggingface_hub import snapshot_download
print(snapshot_download("$1", local_dir=r"$dl_dir"))
PYEOF
  }
  clone_gh() { # clone_gh <url> <dir>
    git clone --depth 1 "$1" "$2" 2>/dev/null || \
      https_proxy="$PROXY" git clone --depth 1 "$1" "$2"
  }
  if [ "${NEED_MA2:-}" = 1 ]; then
    clone_gh "https://github.com/pq-yang/MatAnyone2" "$MA2" && echo "✓ MatAnyone2 代码已克隆"
  fi
  if [ "${NEED_MA2_W:-}" = 1 ] && [ -d "$MA2" ]; then
    dl_hf "PeiqingYang/MatAnyone2" "$MA2_W" && echo "✓ MatAnyone2 权重已下载"
  fi
  if [ "${NEED_BR:-}" = 1 ]; then
    dl_hf "ZhengPeng7/BiRefNet_HR-matting" "$BR" && echo "✓ BiRefNet_HR 已下载"
  fi
  if [ "${NEED_RESNET:-}" = 1 ]; then
    mkdir -p "$TORCH_HUB"
    curl -fL -o "$RESNET" "https://download.pytorch.org/models/resnet18-5c106cde.pth" \
      || https_proxy="$PROXY" curl -fL -o "$RESNET" "https://download.pytorch.org/models/resnet18-5c106cde.pth" \
      && echo "✓ ResNet18 已预置"
  fi
  echo "--- 补齐完成，重跑本脚本（不带参数）复核 ---"
  exec bash "$HERE/init.sh"
fi

echo "--- 汇总：✓ $OK / ✗ $MISS ---"
[ "$MISS" -eq 0 ] && echo "✅ 抠像链就绪；可选 ComfyUI 背景生成见 comfyui_init.sh"
exit 0
