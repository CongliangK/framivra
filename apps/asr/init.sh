#!/usr/bin/env bash
# asr 模块检查：已有 Framivra 专属解释器环境与模型探测 + 本地 sidecar 冒烟。
# issue #141：与语音输入法（IME）彻底解耦——不探测/复用/借用 IME 的任何解释器、
# 模型或路径；遗留 VT_ASR_IME_ROOT / asr.ime_root 直接报迁移错误退出。
# 幂等：检查已有专属资产（探测+引用）；缺失项失败汇总，不补齐。
# 用法：bash apps/asr/init.sh [--download|--help]。
# 默认不建 venv、不安装包、不下载模型；--download 在配置/venv/pip 前 BLOCKED。
# 单资产固定快照下载不安装运行环境；本入口只检查已有资产。
set -euo pipefail

usage() {
  echo "用法：bash apps/asr/init.sh [--download|--help]"
  echo "  默认：仅本地探测和 sidecar 冒烟，不创建 venv、不联网；缺失项告警并汇总。"
  echo "  --download：旧 ModelScope 混合下载链未审查，前置明确 BLOCKED，不建环境或联网。"
  echo "              许可边界见 docs/third-party-licenses.md；离线预检：python3 scripts/policy/model_license_policy.py plan <asset>。"
  echo "              另行批准的固定快照下载不安装运行环境，也不解除本入口门禁。"
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
  python3 "$LICENSE_ROOT/scripts/policy/model_license_policy.py" legacy-download asr || exit "$?"
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
  echo "    请 unset VT_ASR_IME_ROOT 后重试；本脚本仅检查已有独立资产，准备边界见 apps/asr/README.md「开启步骤」。" >&2
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
  echo "    请从 config 删除该键；仅配置已独立准备的 asr.model_dir，准备边界见 apps/asr/README.md「开启步骤」。" >&2
  exit 2
fi

FAIL=0
ok()   { echo "✓ $*"; }
warn() { echo "⚠ $*"; }
bad()  { echo "✗ $*"; FAIL=1; }

echo "== asr 模块 init（幂等；Framivra 专属资产，绝不借用输入法）=="
echo "本地探测模式：不联网、不安装；旧 --download 为 BLOCKED。许可边界见 docs/third-party-licenses.md；离线预检：python3 scripts/policy/model_license_policy.py plan <asset>。"

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
  bad "无 Framivra 专属解释器（config.asr.python → apps/asr/.venv；绝不借用输入法环境）；本地模式不创建环境、不安装包。旧 --download 为 BLOCKED；独立运行环境须另行审核和准备，见 apps/asr/README.md「开启步骤」。"
fi

if [[ -n "$PY" ]]; then
  if "$PY" -I -c "import qwen_asr" 2>/dev/null; then
    QVER="$("$PY" -I -c "import qwen_asr; print(getattr(qwen_asr, '__version__', 'unknown'))" 2>/dev/null || echo unknown)"
    # 依赖来源验证（issue #141）：qwen_asr 必须装在解释器自身环境根内
    QORIGIN="$("$PY" -I -c 'import qwen_asr,os;print(os.path.dirname(os.path.dirname(qwen_asr.__file__)))' 2>/dev/null || true)"
    VENV_ROOT="$(cd "$(dirname "$PY")/.." && pwd)"
    if [[ -n "$QORIGIN" && "$QORIGIN" == "$VENV_ROOT"* ]]; then
      ok "qwen-asr 可导入且来源为专属环境：${QVER}（$("$PY" --version 2>&1)）"
    else
      bad "qwen_asr 来源异常（${QORIGIN}，应位于 $VENV_ROOT 内）——疑似外部注入，已拒绝"
    fi
  else
    bad "专属解释器缺 qwen_asr 包：${PY}；绝不借用输入法环境。旧 --download 为 BLOCKED；独立依赖须另行审核和准备，固定快照模型下载不安装运行环境。"
    printf '    未运行安装：解释器 %q；旧源 %q。许可边界见 docs/third-party-licenses.md。\n' "$PY" "$TUNA"
  fi
  if "$PY" -I -c "import modelscope" 2>/dev/null; then
    ok "modelscope 可导入（仅旧下载链使用，当前 --download 入口为 BLOCKED）"
  else
    warn "缺 modelscope 包（仅旧下载链需要）；旧 --download 为 BLOCKED，本地检查不补装。许可边界见 docs/third-party-licenses.md。"
  fi
fi

# ---------------------------------------------------------------- 2. 模型探测（项目专属根）
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

for name in Qwen3-ASR-1.7B Qwen3-ASR-0.6B Qwen3-ForcedAligner-0.6B; do
  root="$(find_model "$name")"
  if [[ -n "$root" ]]; then
    ok "模型 ${name}（复用专属资产）：$root/$name"
  else
    bad "缺少模型 ${name}（专属根 ${MODELS_HOME}）；本地探测不联网、绝不借用输入法模型。旧 --download 为 BLOCKED；请核对 docs/third-party-licenses.md 与离线 plan <asset>，或配置已独立准备的 asr.model_dir。"
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
      bad "sidecar 冒烟失败（exit ${code}）"
    fi
  fi
fi

# ---------------------------------------------------------------- 4. 汇总
echo
echo "== init 汇总 =="
if [[ "$FAIL" == "0" ]]; then
  echo "✓ 已有独立资产检查与 sidecar 冒烟通过（未验真实转写）。建议把解释器路径写进仓库根 config.local.json（不自动改用户配置）："
  [[ -n "$PY" ]] && echo "    \"asr\": { \"python\": \"$PY\", \"model_dir\": \"$MODELS_HOME\" }"
  echo "  独立环境就绪且显式启用 asr.enabled 后：python3 apps/asr/asr_cli.py sidecar start → srt/timeline 子命令自动复用。"
  echo "  注：18081 为语音输入法 sidecar 专用端口，本模块绝不绑定/探测；默认 18085。"
else
  echo "✗ 存在失败项（见上）；修复后重跑 bash apps/asr/init.sh（幂等，已就绪项不会重做）。"
fi
exit "$FAIL"
