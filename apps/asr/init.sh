#!/usr/bin/env bash
# Check independently prepared ASR assets; never install or download implicitly.
set -euo pipefail

usage() {
  echo "用法：bash apps/asr/init.sh [--download|--help]"
  echo "  默认：仅本地探测和 sidecar 冒烟，不创建 venv、不联网；缺失项失败汇总。"
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

# Preserve the legacy license gate before config, interpreter or asset inspection.
if [[ "$DOWNLOAD" == "1" ]]; then
  LICENSE_ROOT="$(python3 -I -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().parents[2])' "${BASH_SOURCE[0]}")"
  python3 -I "$LICENSE_ROOT/scripts/policy/model_license_policy.py" legacy-download asr || exit "$?"
fi

ASR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -I - "$ASR" <<'PY'
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, sys.argv[1])
import client
import independence as ind

print("== asr 模块 init（Framivra 专属资产，绝不借用输入法）==", flush=True)
print("本地探测模式：不联网、不安装；旧 --download 为 BLOCKED。许可边界见 docs/third-party-licenses.md；离线预检：python3 scripts/policy/model_license_policy.py plan <asset>。", flush=True)

try:
    cfg = client.load_config()
    # Validate every configured path/URL before executing a configured interpreter
    # or probing configured assets. VTX_CONFIG has exactly the runtime semantics.
    client.preflight(cfg)
    base_url = client.cfg_get(cfg, "asr.base_url", client.DEFAULT_BASE_URL)
    ind.validate_base_url(base_url)
    port = int(os.environ.get("VT_ASR_PORT") or client._port_of(base_url))
    if not 0 < port < 65536:
        raise ValueError("端口必须位于 1..65535")
    ind.validate_base_url("http://127.0.0.1:%d" % port)
except (ind.AsrIndependenceError, ValueError) as exc:
    print("✗ %s" % exc, file=sys.stderr)
    sys.exit(2)

failed = False
def bad(message):
    global failed
    failed = True
    print("✗ " + message, flush=True)

# Runtime supports either ASR size, but init acceptance requires an aligner too.
model = aligner = py = None
try:
    model = client.pick_model(cfg)
    if model:
        model = ind.verify_model_dir(model, "ASR 模型", check_weights=True)
        print("✓ 模型（复用专属资产）：%s" % model, flush=True)
    else:
        bad("缺少模型 Qwen3-ASR-1.7B 或 Qwen3-ASR-0.6B；本地探测不联网。请配置已独立准备的 asr.model_dir。")
    configured_aligner = client.cfg_get(cfg, "asr.aligner_dir", None)
    aligner = configured_aligner or client.probe_aligner(cfg)
    if aligner:
        aligner = ind.verify_model_dir(aligner, "ForcedAligner", check_weights=True)
        print("✓ 模型（复用专属资产）：%s" % aligner, flush=True)
    else:
        bad("缺少模型 Qwen3-ForcedAligner-0.6B；init 验收需要 ASR + ForcedAligner，未验真实转写。")
except ind.AsrIndependenceError as exc:
    bad(str(exc))

# An asset refusal must not be followed by execution/import of an interpreter.
if not failed:
    py = client.probe_python(cfg)
    if py:
        print("✓ 解释器及 qwen_asr/torch 来源为专属环境：%s" % py, flush=True)
    else:
        bad("无可用 Framivra 专属解释器或缺 qwen_asr 包或 torch；独立运行环境须另行审核和准备，见 apps/asr/README.md「开启步骤」。固定快照模型下载不安装运行环境。")

if not failed:
    cmd = [py, "-I", str(client.ASR_DIR / "sidecar_server.py"),
           "--port", str(port), "--model", model, "--aligner", aligner,
           "--device-map", "cpu", "--once-health"]
    result = subprocess.run(cmd, env=client.py_env(py))
    if result.returncode:
        bad("sidecar 冒烟失败（exit %s）；未验真实转写。" % result.returncode)
    else:
        print("✓ sidecar 冒烟通过（127.0.0.1:%d /health，模型未加载）" % port, flush=True)

print("\n== init 汇总 ==", flush=True)
if failed:
    print("✗ 存在失败项；不创建 venv、不安装包、不下载模型。独立运行环境须另行审核和准备。", flush=True)
else:
    print("✓ 已有独立资产检查与 sidecar 冒烟通过（未验真实转写）。显式启用 asr.enabled 后可运行转写。", flush=True)
sys.exit(1 if failed else 0)
PY
