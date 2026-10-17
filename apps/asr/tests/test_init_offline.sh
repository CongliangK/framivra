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
export PATH="$TMP/bin:$PATH" CALL_LOG="$TMP/calls" INSTALL_MARKER="$TMP/installed"
export STUB_READY=0 STUB_FAIL_PIP=0
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
  mkdir -p "$3/bin"
  ln -s "$STUB_BIN/fake-python" "$3/bin/python"
  exit 0
fi
exec "$REAL_PY" "$@"
SH
cat > "$TMP/bin/fake-python" <<'SH'
#!/usr/bin/env bash
[[ "${1:-}" == -I ]] && shift
case "${1:-}" in
  -c)
    if [[ "$2" == *qwen_asr* || "$2" == *modelscope* ]]; then
      [[ "$STUB_READY" == 1 || -f "$INSTALL_MARKER" ]] || exit 1
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
cat > "$TMP/bin/pip" <<'SH'
#!/usr/bin/env bash
printf 'pip %s\n' "$*" >> "$CALL_LOG"
[[ "$STUB_FAIL_PIP" == 0 ]] || exit 42
touch "$INSTALL_MARKER"
echo 'stub pip completed (no network)'
SH
cat > "$TMP/bin/modelscope" <<'SH'
#!/usr/bin/env bash
printf 'modelscope %s\n' "$*" >> "$CALL_LOG"
while [[ $# -gt 0 ]]; do
  if [[ "$1" == --local_dir ]]; then
    mkdir -p "$2"; touch "$2/model.safetensors"; break
  fi
  shift
done
echo 'stub model completed (no download)'
SH
cat > "$TMP/bin/curl" <<'SH'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$CALL_LOG"
echo '{"Data":{"Revision":"stub-revision"}}'
SH
chmod +x "$TMP/bin/"*
export STUB_BIN="$TMP/bin"
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

# 1. 无解释器：普通模式不建 venv、不调用任何联网工具，有中文指引和失败汇总；
#    假 IME 资产在场也绝不回退（IME 解释器零调用）。
run 1
no_calls
[[ ! -d "$TMP/repo/apps/asr/.venv" ]]
contains "$TMP/output" '本地探测模式：不联网'
contains "$TMP/output" 'bash apps/asr/init.sh --download'
contains "$TMP/output" '绝不借用输入法'
contains "$TMP/output" '== init 汇总 =='
[[ ! -f "$TMP/ime_calls" ]] || { echo 'FAIL: IME interpreter invoked' >&2; exit 1; }
echo 'PASS: default missing interpreter is local-only, never falls back to IME'

# 2a. 许可门禁不变：--download 在无授权证据时仍 BLOCKED（exit 4），零网络命令。
#     拷贝真实 policy + inventory + 本地证据件到隔离副本（只读复用，不改真实树）。
REAL_ROOT="$(cd "$ASR/../.." && pwd)"
mkdir -p "$TMP/repo/scripts" "$TMP/repo/docs" \
  "$TMP/repo/experiments/gfx_cef_worker/third_party/spng" \
  "$TMP/repo/experiments/gfx_cef_worker/third_party/fpng"
cp "$REAL_ROOT/scripts/model_license_policy.py" "$TMP/repo/scripts/"
cp "$REAL_ROOT/docs/model-license-inventory.json" "$TMP/repo/docs/"
cp "$REAL_ROOT/LICENSE" "$TMP/repo/" 2>/dev/null || true
cp "$REAL_ROOT/experiments/gfx_cef_worker/third_party/spng/spng.c" \
   "$REAL_ROOT/experiments/gfx_cef_worker/third_party/spng/LICENSE" \
   "$TMP/repo/experiments/gfx_cef_worker/third_party/spng/"
cp "$REAL_ROOT/experiments/gfx_cef_worker/third_party/fpng/LICENSE" \
   "$TMP/repo/experiments/gfx_cef_worker/third_party/fpng/"
: > "$CALL_LOG"
run 4 --download
contains "$TMP/output" 'BLOCKED'
no_calls
echo 'PASS: license gate stays closed without authorization (exit 4, no network)'

# 2b. 门禁放行后的安装路径（stub 模拟未来已授权固定快照）：--download 才创建 venv/
#     调用假 pip；下载落点必须是 Framivra 专属根（不是假 IME vendor，不是 ~/models）。
printf 'raise SystemExit(0)  # stub: 模拟已授权固定快照\n' > "$TMP/repo/scripts/model_license_policy.py"
run 0 --download
contains "$CALL_LOG" 'venv -m venv'
contains "$CALL_LOG" 'pip install --timeout 60 --retries 2'
contains "$CALL_LOG" 'modelscope download --model Qwen/Qwen3-ASR-1.7B'
contains "$CALL_LOG" "local_dir $MODELS_HOME/Qwen3-ASR-1.7B"
contains "$CALL_LOG" 'curl -fSL --connect-timeout 10 --max-time 10'
contains "$TMP/output" '执行网络命令：'
contains "$TMP/output" '-m pip install --timeout 60 --retries 2'
contains "$TMP/output" '-m modelscope download'
contains "$TMP/output" 'curl -fSL'
[[ "$(< "$CALL_LOG")" != *"$IME"* ]] || { echo 'FAIL: touched fake IME paths' >&2; exit 1; }
echo 'PASS: explicit download targets project-owned roots only'

# 3. 就绪资产：无论普通还是 --download 都不调用 pip/modelscope/curl（幂等）。
: > "$CALL_LOG"
run 0
no_calls
contains "$TMP/output" '复用专属资产'
run 0 --download
no_calls
echo 'PASS: ready interpreter/models are reused in both modes'

# 4. 已有解释器但缺包：普通模式不调用 pip；--download 才补包。
export INSTALL_MARKER="$TMP/installed-again"
run 1
no_calls
contains "$TMP/output" '缺 qwen_asr 包'
contains "$TMP/output" '缺 modelscope 包'
run 0 --download
contains "$CALL_LOG" 'pip install --timeout 60 --retries 2'
echo 'PASS: existing interpreter missing packages requires explicit download'

# 5. pip 失败仍汇总且不冒然下载模型（使用新的隔离 HOME）。
: > "$CALL_LOG"
export HOME="$TMP/home-empty" INSTALL_MARKER="$TMP/not-installed" STUB_FAIL_PIP=1
run 1 --download
contains "$TMP/output" 'pip 安装失败'
contains "$TMP/output" '== init 汇总 =='
[[ "$(< "$CALL_LOG")" != *'modelscope download'* && "$(< "$CALL_LOG")" != *'curl -'* ]]
echo 'PASS: pip failure has summary and skips model network commands'

# 6. 帮助/未知参数不进入探测。
: > "$CALL_LOG"
export HOME="$TMP/home"
run 0 --help
contains "$TMP/output" '不创建 venv、不联网'
no_calls
run 2 --unknown
contains "$TMP/output" '未知参数'
no_calls
echo 'PASS: help and invalid arguments do not perform initialization'
echo 'All ASR init offline regression cases passed (incl. issue #141 IME decoupling).'
