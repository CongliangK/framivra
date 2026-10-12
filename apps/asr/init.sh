#!/usr/bin/env bash
# asr 模块初始化：qwen-asr 解释器与模型探测/下载（ModelScope 国内直连）+ sidecar 冒烟。
# 幂等：已有资产一律复用（探测+引用，不重复下载）；只补缺失项。
# 用法：bash apps/asr/init.sh [--download|--help]（旧路径 bash asr/init.sh 同样可达）。
# 默认只做本地探测/sidecar 冒烟，不建 venv、不联网；--download 才安装缺失包/下载缺失模型。
# 网络命令执行前完整打印；pip 单次连接超时 60 秒、最多重试 2 次（无需系统 timeout）。
# 设计：docs/plans/2026-09-11-三大模块整合方案.md v2.1 §6.2/§6.5（复用优先链）。
set -euo pipefail

usage() {
  echo "用法：bash apps/asr/init.sh [--download|--help]"
  echo "  默认：仅本地探测和 sidecar 冒烟，不创建 venv、不联网；缺失项告警并汇总。"
  echo "  --download：显式允许创建 venv、pip 安装缺失包及 ModelScope 下载缺失模型（可能数 GB）。"
  echo "              复用已有资产；执行前打印完整网络命令，pip --timeout 60 --retries 2。"
  echo "  --help, -h：显示本帮助（不探测、不联网）。"
}
DOWNLOAD=0
for arg in "$@"; do
  case "$arg" in
    --download) DOWNLOAD=1 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "✗ 未知参数：$arg" >&2; usage >&2; exit 2 ;;
  esac
done

FAIL=0
ok()   { echo "✓ $*"; }
warn() { echo "⚠ $*"; }
bad()  { echo "✗ $*"; FAIL=1; }
# 防御式门禁：所有网络入口统一收敛；命令打印到 stderr，避免污染 curl 的 JSON 管道。
run_network() {
  if [[ "$DOWNLOAD" != "1" ]]; then
    bad "网络操作已阻止：请显式运行 bash apps/asr/init.sh --download"
    return 1
  fi
  printf '… 执行网络命令：' >&2
  printf ' %q' "$@" >&2
  printf '\n' >&2
  "$@"
}

echo "== asr 模块 init（幂等；复用本机已有资产优先）=="
[[ "$DOWNLOAD" == "1" ]] || echo "本地探测模式：不联网；需要安装/下载时请运行 bash apps/asr/init.sh --download。"

# v2 迁移后本脚本在 apps/asr/ 内：app 根 = 脚本所在目录，仓根 = 上两级（config.local.json）
ASR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$ASR/../.." && pwd)"
MODELS_HOME="$HOME/models/qwen3-asr"
TUNA="https://pypi.tuna.tsinghua.edu.cn/simple"
# IME 仓根覆盖链（P2a 参数化收编）：env VT_ASR_IME_ROOT > config asr.ime_root >
# ~ 展开型默认（与 client.py IME_ROOT_DEFAULT 同一值）
IME_DEFAULT="$HOME/文档/01_活跃项目/type/ibus-voice-ime-github"
CFG_IME="$(python3 - "$ROOT/config.local.json" <<'PY' 2>/dev/null || true
import json, sys, os
try:
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    v = cfg.get("asr", {}).get("ime_root")
    print(os.path.expanduser(v) if v else "")
except Exception:
    print("")
PY
)"
IME="${VT_ASR_IME_ROOT:-${CFG_IME:-$IME_DEFAULT}}"

# ---------------------------------------------------------------- 1. 解释器探测（§6.2 步骤 1）
CFG_PY="$(python3 - "$ROOT/config.local.json" <<'PY' 2>/dev/null || true
import json, sys, os
try:
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    v = cfg.get("asr", {}).get("python")
    print(os.path.expanduser(v) if v else "")
except Exception:
    print("")
PY
)"

PY=""
if [[ -n "$CFG_PY" && -x "$CFG_PY" ]]; then
  PY="$CFG_PY"
  ok "解释器（config.asr.python）：$PY"
elif [[ -x "$IME/.venv-qwen-asr/bin/python" ]]; then
  PY="$IME/.venv-qwen-asr/bin/python"
  ok "解释器（复用 IME .venv-qwen-asr）：$PY"
elif [[ -x "$ASR/.venv/bin/python" ]]; then
  PY="$ASR/.venv/bin/python"
  ok "解释器（本模块 asr/.venv）：$PY"
fi

if [[ -z "$PY" ]]; then
  if [[ "$DOWNLOAD" != "1" ]]; then
    bad "探测链无解释器（config → IME .venv-qwen-asr → asr/.venv）；本地模式不创建环境、不安装包。请运行 bash apps/asr/init.sh --download，或配置已有 asr.python。"
  else
    warn "探测链无解释器 → 自建 asr/.venv（已显式 --download）"
    # Windows/Git Bash 无 python3 别名 → 回退 python；失败仍走汇总。
    SYS_PY="$(command -v python3 || command -v python || true)"
    if [[ -z "$SYS_PY" ]]; then
      bad "无 python3/python 解释器——Windows 装 Python 勾选 Add to PATH 后重试"
    else
      PYVER="$("$SYS_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
      if [[ "${PYVER%%.*}" -lt 3 || "$PYVER" < "3.10" ]]; then
        bad "系统 python3 版本 $PYVER < 3.10，无法自建 venv（官方建议 3.12）"
      elif "$SYS_PY" -m venv "$ASR/.venv"; then
        PY="$ASR/.venv/bin/python"; [[ -x "$ASR/.venv/Scripts/python.exe" ]] && PY="$ASR/.venv/Scripts/python.exe"
        ok "解释器（新建 asr/.venv）：$PY"
      else
        bad "自建 asr/.venv 失败；请修复 Python venv 支持后重跑 --download"
      fi
    fi
  fi
fi

HAS_MS=0
if [[ -n "$PY" ]]; then
  # venv 整体搬家自愈：IME venv 历史上从 ~/文档/type/ibus-ime 移动过，其 python
  # 解析不到自身 site-packages —— 显式补 PYTHONPATH（幂等，已含则跳过）
  VENV_SITE="$(dirname "$(dirname "$PY")")/lib/python3.14/site-packages"
  if [[ -d "$VENV_SITE" && ":${PYTHONPATH:-}:" != *":$VENV_SITE:"* ]]; then
    export PYTHONPATH="$VENV_SITE${PYTHONPATH:+:$PYTHONPATH}"
  fi
  if [[ "$DOWNLOAD" == "1" ]]; then
    NEED_PACKAGES=0
    "$PY" -c "import qwen_asr" 2>/dev/null || NEED_PACKAGES=1
    "$PY" -c "import modelscope" 2>/dev/null || NEED_PACKAGES=1
    if [[ "$NEED_PACKAGES" == "1" ]]; then
      # 用已选解释器调用 pip，兼容 venv 搬家后失效的 pip shebang。
      if ! run_network "$PY" -m pip install --timeout 60 --retries 2 -i "$TUNA" -U qwen-asr modelscope; then
        bad "pip 安装失败；已限制连接超时/重试，请检查网络后重跑 --download"
      fi
    fi
  fi
  if "$PY" -c "import qwen_asr" 2>/dev/null; then
    QVER="$("$PY" -c "import qwen_asr; print(getattr(qwen_asr, '__version__', 'unknown'))" 2>/dev/null || echo unknown)"
    ok "qwen-asr 可导入：$QVER（$("$PY" --version 2>&1)）"
  else
    bad "解释器缺 qwen_asr 包：$PY；未授权时绝不联网安装。请运行 bash apps/asr/init.sh --download，或自行执行："
    printf '    %q -m pip install --timeout 60 --retries 2 -i %q -U qwen-asr modelscope\n' "$PY" "$TUNA"
  fi
  if "$PY" -c "import modelscope" 2>/dev/null; then
    HAS_MS=1
    ok "modelscope 可导入（模型下载通道）"
  else
    warn "缺 modelscope 包（仅下载模型时需要）；请运行 bash apps/asr/init.sh --download（当前未自动联网补装）。"
  fi
fi

# ---------------------------------------------------------------- 2. 模型探测/下载（§6.2 步骤 2）
CFG_MDIR="$(python3 - "$ROOT/config.local.json" <<'PY' 2>/dev/null || true
import json, sys, os
try:
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    v = cfg.get("asr", {}).get("model_dir")
    print(os.path.expanduser(v) if v else "")
except Exception:
    print("")
PY
)"

# 探测顺序：config.asr.model_dir → IME vendor/models/qwen3-asr → ~/models/qwen3-asr
model_roots=()
[[ -n "$CFG_MDIR" ]] && model_roots+=("$CFG_MDIR")
model_roots+=("$IME/vendor/models/qwen3-asr" "$MODELS_HOME")

find_model() {  # $1=目录名 → 输出已就位根目录（空=缺）
  local name="$1" root
  for root in "${model_roots[@]}"; do
    if [[ -f "$root/$name/model.safetensors" || -f "$root/$name/model.safetensors.index.json" ]]; then
      echo "$root"; return
    fi
  done
  echo ""
}

download_model() {  # $1=目录名（Qwen3-ASR-1.7B 等）→ 下载到 ~/models/qwen3-asr/<name>
  local name="$1" ms_bin rc=0
  mkdir -p "$MODELS_HOME"
  # modelscope 包无 __main__（python -m modelscope 不可执行）→ 用 venv 的 CLI 脚本；
  # 其 shebang 可能因 venv 搬家失效 → 一律用 "$PY" 显式重解释
  ms_bin="$(dirname "$PY")/modelscope"
  if [[ -f "$ms_bin" ]]; then
    run_network "$PY" "$ms_bin" download --model "Qwen/$name" --local_dir "$MODELS_HOME/$name" || rc=$?
  else
    run_network "$PY" -m modelscope download --model "Qwen/$name" --local_dir "$MODELS_HOME/$name" || rc=$?
  fi
  if [[ "$rc" -ne 0 ]]; then
    bad "下载失败：Qwen/$name（ModelScope 国内直连，exit $rc）"
    echo "  备选链：a) 重试（modelscope 偶发抖动）  b) hf-mirror：export HF_ENDPOINT=https://hf-mirror.com 后 huggingface-cli download Qwen/$name --local-dir $MODELS_HOME/$name"
    return 1
  fi
}

for name in Qwen3-ASR-1.7B Qwen3-ASR-0.6B Qwen3-ForcedAligner-0.6B; do
  root="$(find_model "$name")"
  if [[ -n "$root" ]]; then
    ok "模型 $name（复用）：$root/$name"
  elif [[ "$DOWNLOAD" != "1" ]]; then
    bad "缺少模型 $name；本地探测不联网。请运行 bash apps/asr/init.sh --download，或配置已有 asr.model_dir。"
  elif [[ -z "$PY" || "$HAS_MS" != "1" ]]; then
    bad "无法下载 $name：解释器/modelscope 未就绪（见上）；修复后重跑 --download。"
  else
    echo "… 下载 Qwen/$name → $MODELS_HOME/$name（ForcedAligner 约 2GB，国内直连）"
    if download_model "$name"; then
      rev="$(run_network curl -fSL --connect-timeout 10 --max-time 10 "https://modelscope.cn/api/v1/models/Qwen/$name" \
             | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Data",{}).get("Revision","unknown"))' 2>/dev/null || echo unknown)"
      ok "模型 $name（新下载）：$MODELS_HOME/$name（ModelScope revision $rev —— 请抄录进 asr/README.md「已验证版本」节）"
    fi
  fi
done

# ---------------------------------------------------------------- 3. sidecar 冒烟（§6.2 步骤 3）
if [[ -n "$PY" ]]; then
  PORT="${VT_ASR_PORT:-18085}"
  if "$PY" "$ASR/sidecar_server.py" --port "$PORT" --once-health; then
    ok "sidecar 冒烟通过（127.0.0.1:$PORT /health，模型未加载、秒级）"
  else
    code=$?
    if [[ "$code" == "2" ]]; then
      bad "端口 $PORT 被占用——换 --port / config asr.base_url（18081 是 IME 输入法 sidecar 专用，绝不复用）"
    else
      bad "sidecar 冒烟失败（exit $code）"
    fi
  fi
fi

# ---------------------------------------------------------------- 4. 汇总
echo
echo "== init 汇总 =="
if [[ "$FAIL" == "0" ]]; then
  echo "✓ asr 模块就绪。建议把解释器路径写进仓库根 config.local.json（不自动改用户配置）："
  [[ -n "$PY" ]] && echo "    \"asr\": { \"python\": \"$PY\", \"model_dir\": \"$MODELS_HOME\" }"
  echo "  之后：python3 asr/asr_cli.py sidecar start → srt/timeline 子命令自动复用。"
  echo "  注：18081 为 IME 输入法 sidecar 既有端口；本模块 sidecar 默认 18085。"
else
  echo "✗ 存在失败项（见上）；修复后重跑 bash asr/init.sh（幂等，已就绪项不会重做）。"
fi
exit "$FAIL"
