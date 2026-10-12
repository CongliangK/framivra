#!/usr/bin/env bash
# init 参数/网络门禁回归：仅使用隔离副本、临时 HOME/PATH 和假 pip/modelscope/curl。
# 运行：bash apps/asr/tests/test_init_offline.sh（不安装包、不下载模型、不修改真实配置）。
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
cp "$ASR/init.sh" "$TMP/repo/apps/asr/init.sh"
export REAL_PY HOME="$TMP/home" VT_ASR_IME_ROOT="$TMP/ime"
export PATH="$TMP/bin:$PATH" CALL_LOG="$TMP/calls" INSTALL_MARKER="$TMP/installed"
export STUB_READY=0 STUB_FAIL_PIP=0
unset PYTHONPATH

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
case "${1:-}" in
  -c)
    if [[ "$2" == *qwen_asr* || "$2" == *modelscope* ]]; then
      [[ "$STUB_READY" == 1 || -f "$INSTALL_MARKER" ]] || exit 1
      [[ "$2" != *print* ]] || echo stub-version
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
: > "$CALL_LOG"
contains() { [[ "$(< "$1")" == *"$2"* ]] || { echo "FAIL: $1 lacks $2" >&2; exit 1; }; }
no_calls() { [[ ! -s "$CALL_LOG" ]] || { echo 'FAIL: unexpected external command' >&2; exit 1; }; }
run() {
  local expected="$1" actual=0; shift
  bash "$SCRIPT" "$@" > "$TMP/output" 2>&1 || actual=$?
  [[ "$actual" == "$expected" ]] || { echo "FAIL: exit $actual != $expected" >&2; exit 1; }
}

# 1. 无解释器：普通模式不建 venv、不调用任何联网工具，有中文指引和失败汇总。
run 1
no_calls
[[ ! -d "$TMP/repo/apps/asr/.venv" ]]
contains "$TMP/output" '本地探测模式：不联网'
contains "$TMP/output" 'bash apps/asr/init.sh --download'
contains "$TMP/output" '== init 汇总 =='
echo 'PASS: default missing interpreter is local-only with summary'

# 2. 新环境：--download 才创建 venv/调用假 pip；缺失模型与 revision 也经过假工具。
run 0 --download
contains "$CALL_LOG" 'venv -m venv'
contains "$CALL_LOG" 'pip install --timeout 60 --retries 2'
contains "$CALL_LOG" 'modelscope download --model Qwen/Qwen3-ASR-1.7B'
contains "$CALL_LOG" 'curl -fSL --connect-timeout 10 --max-time 10'
contains "$TMP/output" '执行网络命令：'
contains "$TMP/output" '-m pip install --timeout 60 --retries 2'
contains "$TMP/output" '-m modelscope download'
contains "$TMP/output" 'curl -fSL'
echo 'PASS: explicit download prints commands and bounds pip timeout/retries'

# 3. 就绪资产：无论普通还是 --download 都不调用 pip/modelscope/curl（幂等）。
: > "$CALL_LOG"
run 0
no_calls
contains "$TMP/output" '（复用）'
run 0 --download
no_calls
echo 'PASS: ready interpreter/models are reused in both modes'

# 4. 已有解释器但缺包：普通模式不调用 pip；--download 才补包。
# 新 marker 路径模拟缺包，无需删除已完成资产。
export INSTALL_MARKER="$TMP/installed-again"
run 1
no_calls
contains "$TMP/output" '解释器缺 qwen_asr 包'
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
run 0 --help
contains "$TMP/output" '不创建 venv、不联网'
no_calls
run 2 --unknown
contains "$TMP/output" '未知参数'
no_calls
echo 'PASS: help and invalid arguments do not perform initialization'
echo 'All 6 ASR init offline regression cases passed.'
