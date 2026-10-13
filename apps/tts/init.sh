#!/usr/bin/env bash
# tts/init.sh · TTS 模块幂等初始化探测（方案 §6.3；2026-09-11）
#
# 探测顺序（复用优先，绝不重复下载 11GB 模型）：
#   1. repo        config.tts.repo ?: ~/AI/IndexTTS/repo（校验 checkpoints 五件齐）
#   2. 解释器      config.tts.python ?: conda indextts(3.10) ?: <repo>/.venv(3.11)
#   3. 音色参考    config.tts.voice_ref ?: ~/音乐/5_G_Resemble仅降噪.wav
#   4. edge-tts    config.tts.edge_bin ?: ~/miniconda3/bin/edge-tts ?: PATH
#   5. GPU         nvidia-smi
# 幂等：只探测打印，不写任何文件、不下载任何模型。缺 repo 时打印两条路线
# 不自动下载。重复运行结果一致。
set -u

# Fail before probing user configuration or cloning/downloading the unknown chain.
for license_arg in "$@"; do
  if [ "$license_arg" = "--download" ]; then
    LICENSE_ROOT="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().parents[2])' "${BASH_SOURCE[0]}")"
    python3 "$LICENSE_ROOT/scripts/model_license_policy.py" legacy-download indextts || exit "$?"
  fi
done

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"   # v2 迁移后 tts 在 apps/ 下，仓根 = 上两级
CFG="$ROOT/config.local.json"
DEFAULT_REPO="$HOME/AI/IndexTTS/repo"
DEFAULT_PY="$HOME/miniconda3/envs/indextts/bin/python"
DEFAULT_VOICE_REF="$HOME/音乐/5_G_Resemble仅降噪.wav"
DEFAULT_EDGE="$HOME/miniconda3/bin/edge-tts"

OK=0; WARN=0; FAIL=0

# config.local.json 容错读键（缺文件/缺键/坏 JSON → 默认值；~ 展开；方案 §7.2）
cfg() { # cfg <点路径> <默认值>
  python3 - "$CFG" "$1" "$2" <<'PYEOF'
import json, os, sys
cfg_path, path, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    cfg = json.load(open(cfg_path, encoding="utf-8"))
except Exception:
    cfg = {}
cur = cfg
for k in path.split("."):
    if not isinstance(cur, dict) or k not in cur:
        cur = None; break
    cur = cur[k]
val = default if cur is None else cur
print(os.path.expanduser(val) if isinstance(val, str) else val)
PYEOF
}

mark() { # mark <ok|warn|fail> <条目> <详情>
  case "$1" in
    ok)   OK=$((OK+1));   echo "  ✓ $2 — $3" ;;
    warn) WARN=$((WARN+1)); echo "  ⚠ $2 — $3" ;;
    fail) FAIL=$((FAIL+1)); echo "  ✗ $2 — $3" ;;
  esac
}

echo "=== tts/init.sh（幂等探测，只打印不安装）==="

# ---- 1. repo（IndexTTS 代码 + 权重，复用优先）----
REPO="$(cfg tts.repo "$DEFAULT_REPO")"
CKPT="$REPO/checkpoints"
if [ -d "$CKPT" ]; then
  MISS=""
  for n in config.yaml gpt.pth qwen0.6bemo4-merge codec.pth s2mel.pth; do
    [ -e "$CKPT/$n" ] || MISS="$MISS $n"
  done
  if [ -z "$MISS" ]; then
    mark ok "repo" "复用 $CKPT（五件权重齐）"
  else
    mark fail "repo" "$CKPT 缺：$MISS —— 路线 a) modelscope download --model IndexTeam/IndexTTS-2.5 --local_dir <repo>/checkpoints（国内直连）+ 代码 git clone https://github.com/index-tts/index-tts（走代理；gitclone/ghproxy 镜像 2026-09-11 实测不可用）；路线 b) 只用兜底引擎 pip install -i https://pypi.tuna.tsinghua.edu.cn/simple edge-tts（注：许可见 docs/third-party-licenses.md 许可，仅作独立 CLI 兜底，装否自定）&& tts_cli.py gen --engine edge（不下载 11GB）"
  fi
else
  mark fail "repo" "$REPO 不存在 —— 路线 a) git clone https://github.com/index-tts/index-tts（走代理）到 $REPO && modelscope download --model IndexTeam/IndexTTS-2.5 --local_dir $REPO/checkpoints；路线 b) 只用兜底引擎 pip install -i https://pypi.tuna.tsinghua.edu.cn/simple edge-tts（注：许可见 docs/third-party-licenses.md 许可，仅作独立 CLI 兜底，装否自定）&& tts_cli.py gen --engine edge（不下载 11GB）"
fi

# ---- 2. 解释器（indextts conda env / repo venv，官方支持 3.10-3.11）----
PY="$(cfg tts.python "$DEFAULT_PY")"
if [ -x "$PY" ]; then
  mark ok "解释器" "$($PY --version 2>&1) @ $PY"
else
  PY2="$REPO/.venv/bin/python"
  if [ -x "$PY2" ]; then
    mark ok "解释器" "$($PY2 --version 2>&1) @ $PY2（config tts.python 缺省回退 repo venv）"
  else
    mark fail "解释器" "$PY 与 $PY2 均不可用 —— conda create -n indextts python=3.10 && conda activate indextts && cd $REPO && pip install -e .（按 repo 说明装依赖），或只用 --engine edge"
  fi
fi

# ---- 3. 音色参考（本人干声 5-10s，克隆音色用）----
VR="$(cfg tts.voice_ref "$DEFAULT_VOICE_REF")"
if [ -f "$VR" ]; then
  mark ok "音色参考" "$VR（$(du -h "$VR" | cut -f1)）"
else
  mark warn "音色参考" "$VR 缺 —— 放任意 5-10s 单一说话人干声（电平正常），改 config tts.voice_ref 指向它；仅 edge 引擎可无此文件"
fi

# ---- 4. edge-tts（免 GPU 兜底）----
EB="$(cfg tts.edge_bin "$DEFAULT_EDGE")"
if [ ! -e "$EB" ] && command -v edge-tts >/dev/null 2>&1; then
  EB="$(command -v edge-tts)"
fi
if [ -e "$EB" ]; then
  mark ok "edge-tts" "$EB"
else
  mark warn "edge-tts" "未找到（$DEFAULT_EDGE / PATH 均无）—— pip install -i https://pypi.tuna.tsinghua.edu.cn/simple edge-tts（许可见 docs/third-party-licenses.md，独立 CLI 兜底，装否自定）"
fi

# ---- 5. GPU ----
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  GPU_INFO="$(nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader | head -1)"
  mark ok "GPU" "$GPU_INFO"
else
  mark warn "GPU" "无 nvidia-smi —— indextts 引擎不可用，建议 gen --engine edge"
fi

echo "--- 汇总：✓ $OK / ⚠ $WARN / ✗ $FAIL ---"
if [ "$OK" -ge 4 ]; then
  echo "✅ indextts 主引擎就绪（bash tts/init.sh 幂等，可重复跑）"
elif [ "$OK" -ge 1 ]; then
  echo "⚠ 部分就绪——edge 兜底可用性见上；配置后把 config.local.json tts.enabled 置 true"
fi

# ---- 可选 --download：补齐 repo / checkpoints（11GB 权重，确认后再跑）----
# 用法：bash tts/init.sh --download
# 渠道定案（2026-09-11）：权重走 ModelScope 国内镜像直连；代码克隆 github 直连失败自动换代理
#（公共 gitclone/ghproxy 镜像实测不可用）。
if [ "${1:-}" = "--download" ] || [ "${2:-}" = "--download" ]; then
  REPO="$(cfg tts.repo "$DEFAULT_REPO")"
  echo "--- tts --download：目标 $REPO ---"
  if [ ! -d "$REPO/.git" ] && [ ! -d "$REPO" ]; then
    echo "[1/2] 克隆 IndexTTS 代码（直连失败自动走代理 127.0.0.1:7890）..."
    git clone --depth 1 https://github.com/index-tts/index-tts "$REPO" \
      || https_proxy=http://127.0.0.1:7890 git clone --depth 1 https://github.com/index-tts/index-tts "$REPO" \
      || { echo "克隆失败；edge 兜底路线：pip install -i https://pypi.tuna.tsinghua.edu.cn/simple edge-tts（许可见 docs/third-party-licenses.md，装否自定）"; exit 1; }
    echo "✓ 代码 → $REPO"
  else
    echo "[1/2] 代码已存在，跳过克隆"
  fi
  CKPT="$REPO/checkpoints"
  if [ ! -e "$CKPT/config.yaml" ]; then
    echo "[2/2] 下载权重（ModelScope 国内直连，约 11GB，数十分钟）..."
    command -v modelscope >/dev/null 2>&1 \
      || pip install -i https://pypi.tuna.tsinghua.edu.cn/simple modelscope
    modelscope download --model IndexTeam/IndexTTS-2.5 --local_dir "$CKPT" \
      && echo "✓ 权重 → $CKPT（重跑 tts/init.sh 复核）" \
      || { echo "下载失败，手动：modelscope download --model IndexTeam/IndexTTS-2.5 --local_dir $CKPT"; exit 1; }
  else
    echo "[2/2] checkpoints 五件已齐，跳过"
  fi
fi
exit 0
