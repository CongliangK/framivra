#!/usr/bin/env bash
# denoise_init.sh · 口播降噪链幂等初始化探测（2026-09-11；P2b 自 scripts/ 迁入 plugins/denoise/）
#
# 对应 plugins/denoise/denoise_voice.py（陷波→Resemble 仅降噪→48kHz）的三项依赖：
#   1. ffmpeg
#   2. conda denoise 环境（resemble-enhance 包 + CPU torch）
#   3. 权重 ~/models/resemble-enhance/enhancer_stage2（681MB）
#
# 模型下载渠道实测（2026-09-11，国内家宽 30MB 分段计速）：
#   hf-mirror.com 直连      2.0 MB/s  ← 默认，无代理可用
#   huggingface.co 走代理   3.9 MB/s  ← 备选，取决于代理节点
#   huggingface.co 直连     不可达
#   ModelScope 无此模型
# → 权重缺失时默认 HF_ENDPOINT=https://hf-mirror.com 下载，--download 实际执行。
#
# 幂等：只探测打印；缺依赖打印修复命令，不自动建环境；仅 --download 会下载权重。
set -u

# Mirror/latest is not the reviewed official immutable model-card snapshot.
for license_arg in "$@"; do
  if [ "$license_arg" = "--download" ]; then
    LICENSE_ROOT="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().parents[2])' "${BASH_SOURCE[0]}")"
    python3 "$LICENSE_ROOT/scripts/model_license_policy.py" legacy-download denoise || exit "$?"
  fi
done

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DENoise_PY="${DENOISE_PY:-$HOME/miniconda3/envs/denoise/bin/python}"
WEIGHTS="${RESEMBLE_WEIGHTS:-$HOME/models/resemble-enhance}"
MIRROR="https://hf-mirror.com"
WEIGHT_FILE="$WEIGHTS/enhancer_stage2/ds/G/default/mp_rank_00_model_states.pt"

OK=0; WARN=0
say()  { printf '%s\n' "$*"; }
ok()   { say "  [OK]   $*"; OK=$((OK+1)); }
warn() { say "  [MISS] $*"; WARN=$((WARN+1)); }

say "== denoise 降噪链依赖探测 =="

# 1) ffmpeg
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"
else
  warn "ffmpeg 未安装（系统包管理器安装）"
fi

# 2) conda denoise 环境
if [ -x "$DENoise_PY" ] && "$DENoise_PY" -c "import resemble_enhance" 2>/dev/null; then
  ok "denoise 环境 $DENoise_PY（resemble_enhance 可导入）"
else
  warn "denoise 环境缺失，修复命令："
  say "    conda create -n denoise python=3.10 -y"
  say "    $HOME/miniconda3/envs/denoise/bin/pip install resemble-enhance \\"
  say "        torch torchaudio --index-url https://download.pytorch.org/whl/cpu"
fi

# 3) 权重
if [ -s "$WEIGHT_FILE" ]; then
  ok "权重 $WEIGHT_FILE ($(du -sh "$WEIGHTS" | awk '{print $1}'))"
else
  warn "权重缺失 $WEIGHT_FILE"
  say "    下载须审查官方固定快照与通知：python3 scripts/model_license_policy.py plan resemble-enhance"
  say "    旧 mirror/latest --download 已阻断；已有资产探测不改变用户文件。"
fi

# --download：权重缺失时走 hf-mirror 实际下载
if [ "${1:-}" = "--download" ]; then
  if [ -s "$WEIGHT_FILE" ]; then
    say "权重已存在，跳过下载"
  elif [ ! -x "$DENoise_PY" ]; then
    say "denoise 环境缺失，先按上面命令建环境再 --download"
    exit 1
  else
    say "开始下载权重（HF_ENDPOINT=$MIRROR）..."
    # heredoc 走 stdin，Git Bash 的 /c/Users/... 路径不经 MSYS 参数转换，Windows
    # Python 会解析到盘根下错位置——cygpath -w 兜底（非 MSYS 环境原样返回）
    DL_WEIGHTS="$(cygpath -w "$WEIGHTS" 2>/dev/null || echo "$WEIGHTS")"
    HF_ENDPOINT="$MIRROR" "$DENoise_PY" - <<PYEOF
from huggingface_hub import snapshot_download
p = snapshot_download("ResembleAI/resemble-enhance", local_dir=r"$DL_WEIGHTS")
print("done ->", p)
PYEOF
    [ -s "$WEIGHT_FILE" ] && ok "权重下载完成" || { warn "下载后仍未找到权重文件"; exit 1; }
  fi
fi

say "== 结果：$OK 项就绪，$WARN 项缺失 =="
[ "$WARN" -eq 0 ] && say "可直接运行：python3 plugins/denoise/denoise_voice.py <in.wav> <out.wav>（管线内经 sources[].denoise 键自动触发）"
exit 0
