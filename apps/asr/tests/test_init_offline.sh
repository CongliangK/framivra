#!/usr/bin/env bash
# init 参数/网络门禁/IME 解耦回归（issue #141）：仅使用隔离副本、临时 HOME/PATH
# 和假 pip/modelscope/curl；假 IME 资产只放在临时 HOME 的默认锚点下。
# 运行：bash apps/asr/tests/test_init_offline.sh（不安装包、不下载模型、不修改真实配置、
# 不接触真实输入法/18081）。
set -euo pipefail
ASR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REAL_PY="$(command -v python3)"
TMP="$(mktemp -d /tmp/vtx-asr-init-test.XXXXXX)"
cleanup() {
  # 删除前核验绝对路径：只能清理本测 mktemp 创建的目录。
  local resolved
  resolved="$(cd "$TMP" && pwd -P)"
  [[ "$resolved" == "$TMP" && "$resolved" == /tmp/vtx-asr-init-test.* ]] || return 1
  rm -rf -- "$resolved"
}
trap cleanup EXIT
mkdir -p "$TMP/repo/apps/asr" "$TMP/bin" "$TMP/home"
cp "$ASR/init.sh" "$ASR/sidecar_server.py" "$TMP/repo/apps/asr/"
export REAL_PY HOME="$TMP/home"
export PATH="$TMP/bin:$PATH" CALL_LOG="$TMP/calls"
export STUB_READY=0
unset PYTHONPATH VT_ASR_IME_ROOT || true

# 假 IME 资产（默认锚点在临时 HOME 内展开）：断言 init 绝不 import/写入它
IME="$HOME/文档/01_活跃项目/type/ibus-voice-ime-github"
mkdir -p "$IME/.venv-qwen-asr/bin" "$IME/vendor/models/qwen3-asr/Qwen3-ASR-1.7B"
printf 'FAKE-IME-WEIGHT' > "$IME/vendor/models/qwen3-asr/Qwen3-ASR-1.7B/model.safetensors"
printf '#!/bin/sh\necho "IME-INTERPRETER-INVOKED $@" >> %s/ime_calls\nexit 0\n' "$TMP" > "$IME/.venv-qwen-asr/bin/python"
chmod +x "$IME/.venv-qwen-asr/bin/python"

cat > "$TMP/bin/python3" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == -m && "${2:-}" == venv ]]; then
  echo "venv $*" >> "$CALL_LOG"
  exit 91
fi
exec "$REAL_PY" "$@"
SH
cat > "$TMP/bin/fake-python" <<'SH'
#!/usr/bin/env bash
[[ "${1:-}" == -I ]] && shift
case "${1:-}" in
  -c)
    if [[ "$2" == *qwen_asr* || "$2" == *modelscope* ]]; then
      [[ "$STUB_READY" == 1 ]] || exit 1
      if [[ "$2" == *dirname* ]]; then
        # 依赖来源探测：报告装在自身 venv 环境根内
        echo "$(cd "$(dirname "$(dirname "$0")")" && pwd)/lib/python3.12/site-packages/qwen_asr/__init__.py"
      else
        echo stub-version
      fi
      exit 0
    fi
    exit 1 ;;
  --version) echo 'Python 3.12.0 (stub)'; exit 0 ;;
  -m)
    case "$2" in
      pip) shift 2; exec pip "$@" ;;
      modelscope) shift 2; exec modelscope "$@" ;;
    esac ;;
  */sidecar_server.py) echo 'stub local sidecar health'; exit 0 ;;
esac
exit 1
SH
for forbidden in pip modelscope curl; do
  cat > "$TMP/bin/$forbidden" <<'SH'
#!/usr/bin/env bash
printf '%s %s\n' "${0##*/}" "$*" >> "$CALL_LOG"
exit 91
SH
done
chmod +x "$TMP/bin/"*
SCRIPT="$TMP/repo/apps/asr/init.sh"
MODELS_HOME="$HOME/.local/share/framivra/asr/models"
: > "$CALL_LOG"
contains() { [[ "$(< "$1")" == *"$2"* ]] || { echo "FAIL: $1 lacks $2" >&2; exit 1; }; }
no_calls() { [[ ! -s "$CALL_LOG" ]] || { echo 'FAIL: unexpected external command' >&2; exit 1; }; }
run() {
  local expected="$1" actual=0; shift
  bash "$SCRIPT" "$@" > "$TMP/output" 2>&1 || actual=$?
  if [[ "$actual" != "$expected" ]]; then
    [[ -z "${T141_DEBUG:-}" ]] || { echo "---- output (expected $expected, got $actual) ----"; cat "$TMP/output"; }
    echo "FAIL: exit $actual != $expected" >&2; exit 1
  fi
}

# 0a. 遗留 env VT_ASR_IME_ROOT → 迁移错误（exit 2），零探测零联网，假 IME 零调用。
export VT_ASR_IME_ROOT="$IME"
run 2
contains "$TMP/output" 'VT_ASR_IME_ROOT 已废除'
no_calls
[[ ! -f "$TMP/ime_calls" ]] || { echo 'FAIL: IME interpreter invoked' >&2; exit 1; }
unset VT_ASR_IME_ROOT
echo 'PASS: legacy env VT_ASR_IME_ROOT rejected before any probing'

# 0b. 遗留配置键 asr.ime_root → 迁移错误（exit 2）。
printf '{"asr": {"ime_root": "%s"}}\n' "$IME" > "$TMP/repo/config.local.json"
run 2
contains "$TMP/output" 'asr.ime_root 已废除'
no_calls
rm -f "$TMP/repo/config.local.json"
echo 'PASS: legacy config key asr.ime_root rejected'

# 0c. VTX_CONFIG 指向的生效配置含 asr.ime_root → 迁移错误（exit 2），
#     优先于仓根 config.local.json（issue #141 红队 F3），零探测零联网。
printf '{"asr": {"ime_root": "/vtx-config/legacy-ime"}}\n' > "$TMP/vtx-config.json"
export VTX_CONFIG="$TMP/vtx-config.json"
run 2
contains "$TMP/output" 'asr.ime_root 已废除'
no_calls
echo 'PASS: VTX_CONFIG effective config with asr.ime_root rejected'
unset VTX_CONFIG

# 0d. VTX_CONFIG 干净而仓根 config.local.json 含 asr.ime_root：遗留键检查读的是
#     生效配置（VTX_CONFIG），不因仓根旧文件误伤（继续走后续探测汇总）。
printf '{"asr": {"ime_root": "%s"}}\n' "$IME" > "$TMP/repo/config.local.json"
printf '{"asr": {}}\n' > "$TMP/vtx-config.json"
export VTX_CONFIG="$TMP/vtx-config.json"
run 1
if grep -q 'asr.ime_root 已废除' "$TMP/output"; then
  echo 'FAIL: legacy check ignored VTX_CONFIG and read repo-root config' >&2; exit 1
fi
no_calls
echo 'PASS: clean VTX_CONFIG takes precedence over stale repo-root legacy key'
unset VTX_CONFIG
rm -f "$TMP/repo/config.local.json"

# 1. 无解释器：普通模式不建 venv、不调用任何联网工具，有中文指引和失败汇总；
#    假 IME 资产在场也绝不回退（IME 解释器零调用）。
run 1
no_calls
[[ ! -d "$TMP/repo/apps/asr/.venv" ]]
contains "$TMP/output" '本地探测模式：不联网'
contains "$TMP/output" '旧 --download 为 BLOCKED'
contains "$TMP/output" 'docs/third-party-licenses.md'
contains "$TMP/output" 'python3 scripts/policy/model_license_policy.py plan <asset>'
contains "$TMP/output" '独立运行环境须另行审核和准备'
if grep -q '请运行 bash apps/asr/init.sh --download' "$TMP/output"; then
  echo 'FAIL: missing assets recommend the blocked downloader' >&2; exit 1
fi
contains "$TMP/output" '绝不借用输入法'
contains "$TMP/output" '== init 汇总 =='
[[ ! -f "$TMP/ime_calls" ]] || { echo 'FAIL: IME interpreter invoked' >&2; exit 1; }
echo 'PASS: default missing interpreter is local-only, never falls back to IME'

# 2a. 许可门禁不变：旧 --download 一律 BLOCKED（exit 4），零网络命令。
#     拷贝真实 policy + inventory + 本地证据件到隔离副本（只读复用，不改真实树）。
REAL_ROOT="$(cd "$ASR/../.." && pwd)"
mkdir -p "$TMP/repo/scripts/policy" "$TMP/repo/docs" \
  "$TMP/repo/experiments/gfx_cef_worker/third_party/spng" \
  "$TMP/repo/experiments/gfx_cef_worker/third_party/fpng" \
  "$TMP/repo/templates/standard-demo/profiles/standard/v1/fonts" \
  "$TMP/repo/experiments/gfx_electron_worker"
cp "$REAL_ROOT/scripts/policy/model_license_policy.py" "$TMP/repo/scripts/policy/"
cp "$REAL_ROOT/docs/model-license-inventory.json" "$TMP/repo/docs/"
cp "$REAL_ROOT/LICENSE" "$TMP/repo/" 2>/dev/null || true
cp "$REAL_ROOT/experiments/gfx_cef_worker/third_party/spng/spng.c" \
   "$REAL_ROOT/experiments/gfx_cef_worker/third_party/spng/LICENSE" \
   "$TMP/repo/experiments/gfx_cef_worker/third_party/spng/"
cp "$REAL_ROOT/experiments/gfx_cef_worker/third_party/fpng/LICENSE" \
   "$TMP/repo/experiments/gfx_cef_worker/third_party/fpng/"
cp "$REAL_ROOT/templates/standard-demo/profiles/standard/v1/fonts/OFL.txt" \
   "$TMP/repo/templates/standard-demo/profiles/standard/v1/fonts/"
cp "$REAL_ROOT/experiments/gfx_electron_worker/SOURCE-LICENSE-NOTICE.txt" \
   "$TMP/repo/experiments/gfx_electron_worker/"
: > "$CALL_LOG"
run 4 --download
contains "$TMP/output" 'BLOCKED'
no_calls
echo 'PASS: legacy download gate stays blocked (exit 4, no network)'

# 3. 就绪资产由 fixture 直接构造；保留真实许可门禁，绝不复活旧安装器。
mkdir -p "$TMP/repo/apps/asr/.venv/bin"
ln -s "$TMP/bin/fake-python" "$TMP/repo/apps/asr/.venv/bin/python"
for name in Qwen3-ASR-1.7B Qwen3-ASR-0.6B Qwen3-ForcedAligner-0.6B; do
  mkdir -p "$MODELS_HOME/$name"
  printf 'FAKE-OWN-WEIGHT' > "$MODELS_HOME/$name/model.safetensors"
done
export STUB_READY=1
: > "$CALL_LOG"
run 0
no_calls
contains "$TMP/output" '复用专属资产'
contains "$TMP/output" 'sidecar 冒烟通过'
[[ ! -f "$TMP/ime_calls" ]] || { echo 'FAIL: IME interpreter invoked' >&2; exit 1; }
run 4 --download
contains "$TMP/output" 'BLOCKED'
no_calls
echo 'PASS: ready fixtures are probed locally; legacy download remains blocked'

# 4. 已有解释器但缺包：普通模式不安装，旧下载请求仍在探测前拒绝。
export STUB_READY=0
run 1
no_calls
contains "$TMP/output" '缺 qwen_asr 包'
contains "$TMP/output" '缺 modelscope 包'
contains "$TMP/output" '旧 --download 为 BLOCKED'
contains "$TMP/output" '固定快照模型下载不安装运行环境'
if grep -q '自行执行：' "$TMP/output"; then
  echo 'FAIL: missing packages recommend an unreviewed manual install' >&2; exit 1
fi
run 4 --download
contains "$TMP/output" 'BLOCKED'
no_calls
echo 'PASS: missing packages never trigger installation in either mode'

# 5. 解释器依赖就绪而专属模型缺失：默认模式失败汇总，零安装/下载。
: > "$CALL_LOG"
export HOME="$TMP/home-empty" STUB_READY=1
mkdir -p "$HOME"
run 1
no_calls
contains "$TMP/output" '缺少模型 Qwen3-ASR-1.7B'
contains "$TMP/output" '== init 汇总 =='
[[ ! -d "$HOME/.local/share/framivra/asr/models" ]] || { echo 'FAIL: default mode created model assets' >&2; exit 1; }
run 4 --download
contains "$TMP/output" 'BLOCKED'
no_calls
echo 'PASS: missing model fixtures fail locally and blocked mode cannot install'

# 6. 帮助/未知参数不进入探测。
: > "$CALL_LOG"
export HOME="$TMP/home"
run 0 --help
contains "$TMP/output" '不创建 venv、不联网'
contains "$TMP/output" 'BLOCKED'
contains "$TMP/output" 'docs/third-party-licenses.md'
contains "$TMP/output" 'plan <asset>'
contains "$TMP/output" '固定快照下载不安装运行环境'
no_calls
run 2 --unknown
contains "$TMP/output" '未知参数'
no_calls
echo 'PASS: help and invalid arguments do not perform initialization'
echo 'All ASR init offline regression cases passed (incl. issue #141 IME decoupling).'
