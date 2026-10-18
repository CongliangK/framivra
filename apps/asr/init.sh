#!/usr/bin/env bash
# asr 模块初始化：Framivra 专属解释器环境与模型探测（+受许可门禁的下载）+ 冒烟。
# issue #141：与语音输入法（IME）彻底解耦——不探测/复用/借用 IME 的任何解释器、
# 模型或路径；遗留 VT_ASR_IME_ROOT / asr.ime_root 直接报迁移错误退出。
# 幂等：已有专属资产一律复用（探测+引用，不重复下载）；只补缺失项。
# 用法：bash apps/asr/init.sh [--download|--help]。
# 默认只做本地探测/sidecar 冒烟，不建 venv、不联网；--download 才安装缺失包/下载缺失模型。
# 网络命令执行前完整打印；pip 单次连接超时 60 秒、最多重试 2 次（无需系统 timeout）。
set -euo pipefail

usage() {
  echo "用法：bash apps/asr/init.sh [--download|--help]"
  echo "  默认：仅本地探测和 sidecar 冒烟，不创建 venv、不联网；缺失项告警并汇总。"
  echo "  --download：旧 ModelScope 混合下载链未审查，前置明确 BLOCKED，不建环境或联网。"
  echo "              官方固定快照 opt-in 与证据请看 scripts/model_license_policy.py --help。"
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

# Gate before config/venv/pip: ModelScope latest is not reviewed HF evidence.
if [[ "$DOWNLOAD" == "1" ]]; then
  LICENSE_ROOT="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().parents[2])' "${BASH_SOURCE[0]}")"
  python3 "$LICENSE_ROOT/scripts/model_license_policy.py" legacy-download asr || exit "$?"
fi

# v2 迁移后本脚本在 apps/asr/ 内：app 根 = 脚本所在目录，仓根 = 上两级（config.local.json）
ASR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$ASR/../.." && pwd)"
# Framivra 专属资产根（issue #141：独立于 IME 与共享 ~/models）
MODELS_HOME="$HOME/.local/share/framivra/asr/models"
TUNA="https://pypi.tuna.tsinghua.edu.cn/simple"

# ---------------------------------------------------------------- 0. 遗留配置拒绝（fail-closed）
# 旧键 VT_ASR_IME_ROOT / asr.ime_root 属输入法复用链：出现即迁移错误，
# 绝不静默转成别的路径来源，也不做任何探测/联网。
if [[ -n "${VT_ASR_IME_ROOT:-}" ]]; then
  echo "✗ 环境变量 VT_ASR_IME_ROOT 已废除（ASR 与语音输入法彻底解耦，issue #141）：" >&2
  echo "    请 unset VT_ASR_IME_ROOT 后重试；Framivra 专属资产用本脚本建立。" >&2
  exit 2
fi
# 生效配置文件（issue #141 红队 F3）：VTX_CONFIG 优先（与 vtx/pluginlib config
# 统一加载语义一致），未设置时保持原行为读仓根 config.local.json。
CFG_FILE="${VTX_CONFIG:-$ROOT/config.local.json}"
CFG_IME="$(python3 - "$CFG_FILE" <<'PY' 2>/dev/null || true
import json, sys
try:
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    v = cfg.get("asr", {}).get("ime_root")
    print(v if v else "")
except Exception:
    print("")
PY
)"
if [[ -n "$CFG_IME" ]]; then
  echo "✗ 配置键 asr.ime_root 已废除（ASR 与语音输入法彻底解耦，issue #141）：" >&2
  echo "    请从 config 删除该键；Framivra 专属资产用本脚本建立或配置 asr.model_dir。" >&2
  exit 2
fi

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

echo "== asr 模块 init（幂等；Framivra 专属资产，绝不借用输入法）=="
[[ "$DOWNLOAD" == "1" ]] || echo "本地探测模式：不联网；需要安装/下载时请运行 bash apps/asr/init.sh --download。"

# ---------------------------------------------------------------- 1. 解释器探测（项目专属环境）
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
  ok "解释器（config.asr.python，Framivra 专属）：$PY"
elif [[ -x "$ASR/.venv/bin/python" ]]; then
  PY="$ASR/.venv/bin/python"
  ok "解释器（项目专属 apps/asr/.venv）：$PY"
fi

if [[ -z "$PY" ]]; then
  if [[ "$DOWNLOAD" != "1" ]]; then
    bad "无 Framivra 专属解释器（config.asr.python → apps/asr/.venv；绝不借用输入法环境）；本地模式不创建环境、不安装包。请运行 bash apps/asr/init.sh --download。"
  else
    warn "无专属解释器 → 自建 apps/asr/.venv（已显式 --download）"
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
        ok "解释器（新建 apps/asr/.venv）：$PY"
      else
        bad "自建 apps/asr/.venv 失败；请修复 Python venv 支持后重跑 --download"
      fi
    fi
  fi
fi

HAS_MS=0
if [[ -n "$PY" ]]; then
  if [[ "$DOWNLOAD" == "1" ]]; then
    NEED_PACKAGES=0
    "$PY" -I -c "import qwen_asr" 2>/dev/null || NEED_PACKAGES=1
    "$PY" -I -c "import modelscope" 2>/dev/null || NEED_PACKAGES=1
    if [[ "$NEED_PACKAGES" == "1" ]]; then
      # 用项目解释器调用 pip（-I 隔离外部注入；绝不调用输入法的 pip）。
      if ! run_network "$PY" -m pip install --timeout 60 --retries 2 -i "$TUNA" -U qwen-asr modelscope; then
        bad "pip 安装失败；已限制连接超时/重试，请检查网络后重跑 --download"
      fi
    fi
  fi
  if "$PY" -I -c "import qwen_asr" 2>/dev/null; then
    QVER="$("$PY" -I -c "import qwen_asr; print(getattr(qwen_asr, '__version__', 'unknown'))" 2>/dev/null || echo unknown)"
    # 依赖来源验证（issue #141）：qwen_asr 必须装在解释器自身环境根内
    QORIGIN="$("$PY" -I -c 'import qwen_asr,os;print(os.path.dirname(os.path.dirname(qwen_asr.__file__)))' 2>/dev/null || true)"
    VENV_ROOT="$(cd "$(dirname "$PY")/.." && pwd)"
    if [[ -n "$QORIGIN" && "$QORIGIN" == "$VENV_ROOT"* ]]; then
      ok "qwen-asr 可导入且来源为专属环境：$QVER（$("$PY" --version 2>&1)）"
    else
      bad "qwen_asr 来源异常（$QORIGIN，应位于 $VENV_ROOT 内）——疑似外部注入，已拒绝"
    fi
  else
    bad "专属解释器缺 qwen_asr 包：$PY；未授权时绝不联网安装、绝不借用输入法环境。请运行 bash apps/asr/init.sh --download，或自行执行："
    printf '    %q -I -m pip install --timeout 60 --retries 2 -i %q -U qwen-asr modelscope\n' "$PY" "$TUNA"
  fi
  if "$PY" -I -c "import modelscope" 2>/dev/null; then
    HAS_MS=1
    ok "modelscope 可导入（模型下载通道）"
  else
    warn "缺 modelscope 包（仅下载模型时需要）；请运行 bash apps/asr/init.sh --download（当前未自动联网补装）。"
  fi
fi

# ---------------------------------------------------------------- 2. 模型探测/下载（项目专属根）
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

# 探测顺序：config.asr.model_dir → Framivra 专属根（无 IME vendor、无共享 ~/models）
model_roots=()
[[ -n "$CFG_MDIR" ]] && model_roots+=("$CFG_MDIR")
model_roots+=("$MODELS_HOME")

find_model() {  # $1=目录名 → 输出已就位根目录（空=缺）
  local name="$1" root
  for root in "${model_roots[@]}"; do
    if [[ -f "$root/$name/model.safetensors" || -f "$root/$name/model.safetensors.index.json" ]]; then
      echo "$root"; return
    fi
  done
  echo ""
}

download_model() {  # $1=目录名（Qwen3-ASR-1.7B 等）→ 下载到专属根 $MODELS_HOME/<name>
  local name="$1" ms_bin rc=0
  mkdir -p "$MODELS_HOME"
  # modelscope 包无 __main__（python -m modelscope 不可执行）→ 用 venv 的 CLI 脚本；
  # 其 shebang 可能失效 → 一律用 "$PY" 显式重解释
  ms_bin="$(dirname "$PY")/modelscope"
  if [[ -f "$ms_bin" ]]; then
    run_network "$PY" "$ms_bin" download --model "Qwen/$name" --local_dir "$MODELS_HOME/$name" || rc=$?
  else
    run_network "$PY" -I -m modelscope download --model "Qwen/$name" --local_dir "$MODELS_HOME/$name" || rc=$?
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
    ok "模型 $name（复用专属资产）：$root/$name"
  elif [[ "$DOWNLOAD" != "1" ]]; then
    bad "缺少模型 $name（专属根 $MODELS_HOME）；本地探测不联网、绝不借用输入法模型。请运行 bash apps/asr/init.sh --download，或配置已有 asr.model_dir。"
  elif [[ -z "$PY" || "$HAS_MS" != "1" ]]; then
    bad "无法下载 $name：解释器/modelscope 未就绪（见上）；修复后重跑 --download。"
  else
    echo "… 下载 Qwen/$name → $MODELS_HOME/$name（ForcedAligner 约 2GB，国内直连）"
    if download_model "$name"; then
      rev="$(run_network curl -fSL --connect-timeout 10 --max-time 10 "https://modelscope.cn/api/v1/models/Qwen/$name" \
             | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Data",{}).get("Revision","unknown"))' 2>/dev/null || echo unknown)"
      ok "模型 $name（新下载）：$MODELS_HOME/$name（ModelScope revision $rev —— 请抄录进 apps/asr/README.md「已验证版本」节）"
    fi
  fi
done

# ---------------------------------------------------------------- 3. sidecar 冒烟（拒绝绑定 18081）
if [[ -n "$PY" ]]; then
  PORT="${VT_ASR_PORT:-18085}"
  if [[ "$PORT" == "18081" ]]; then
    bad "端口 18081 是语音输入法 sidecar 专用端口——任何情况下禁止绑定（issue #141）"
  elif "$PY" -I "$ASR/sidecar_server.py" --port "$PORT" --once-health; then
    ok "sidecar 冒烟通过（127.0.0.1:$PORT /health，模型未加载、秒级）"
  else
    code=$?
    if [[ "$code" == "2" ]]; then
      bad "端口 $PORT 被占用——换 --port / config asr.base_url"
    else
      bad "sidecar 冒烟失败（exit $code）"
    fi
  fi
fi

# ---------------------------------------------------------------- 4. 汇总
echo
echo "== init 汇总 =="
if [[ "$FAIL" == "0" ]]; then
  echo "✓ asr 模块就绪（Framivra 专属环境与模型）。建议把解释器路径写进仓库根 config.local.json（不自动改用户配置）："
  [[ -n "$PY" ]] && echo "    \"asr\": { \"python\": \"$PY\", \"model_dir\": \"$MODELS_HOME\" }"
  echo "  之后：python3 apps/asr/asr_cli.py sidecar start → srt/timeline 子命令自动复用。"
  echo "  注：18081 为语音输入法 sidecar 专用端口，本模块绝不绑定/探测；默认 18085。"
else
  echo "✗ 存在失败项（见上）；修复后重跑 bash apps/asr/init.sh（幂等，已就绪项不会重做）。"
fi
exit "$FAIL"
