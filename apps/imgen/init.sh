#!/usr/bin/env bash
# imgen/init.sh · 生图 API 生图模块初始化（幂等，秒级完成，无模型下载）
# 探测：requests（系统 python3）→ API key 解析链（本地，不触网）→ 网络连通（可选 --net，仅 GET 不扣费）→ ffmpeg/ffprobe（postprocess 依赖，沿用根 init 语义）
# 用法：bash imgen/init.sh [--net]
set -euo pipefail
cd "$(dirname "$0")"

TUNA="https://pypi.tuna.tsinghua.edu.cn/simple"

echo "== imgen init（生图 API 生图模块）=="

# 1) requests（系统 python3 软依赖：gen/batch 触网时才需要；postprocess 离线不需要）
if python3 -c "import requests" 2>/dev/null; then
    echo "✓ requests：$(python3 -c "import requests; print(requests.__version__)")"
else
    echo "⚠ requests 缺失，尝试安装（清华 PyPI）……"
    if python3 -m pip install --user -i "$TUNA" requests 2>/dev/null; then
        echo "✓ requests：$(python3 -c "import requests; print(requests.__version__)")（已安装）"
    else
        echo "✗ requests 安装失败；手动执行：python3 -m pip install --user -i $TUNA requests"
        echo "  （仅影响 gen/batch 触网生图；postprocess 离线后处理不受影响）"
    fi
fi

# 2) key 解析链本地探测（--key > SC_API_KEY > NANOBANANA2_API_KEY > keyring > ~/.config/vtx-imgen/secrets.json；
#    只本地读文件/keyring，不发起任何网络请求；展示仅尾 4 位）
echo "-- API key 解析链探测（不触网、不扣费）--"
python3 - <<'PY'
import sys
sys.path.insert(0, ".")
# v2 §1.4 禁 spec_from_file_location——app 根注入 sys.path[0] 后普通 import
# （cwd=app 根；与 imgen_cli.py 同一纪律，stdlib 同名 secrets 由 path[0] 优先排除）
import secrets as keychain
if not keychain.__file__.startswith("/"):
    import os
    assert keychain.__file__.startswith(os.getcwd()), keychain.__file__

levels, chosen = keychain.describe_key_chain(None)
for name, value in levels:
    print(f"  {'✓' if value else '✗'} {name}：{'命中 ' + keychain.mask_key(value) if value else '未提供/未命中'}")
if chosen:
    print(f"✓ key 已配置，最终使用：{chosen[0]}（尾 4 位 {keychain.mask_key(chosen[1])}）")
else:
    print("⚠ 未配置任何 key。生成前请配置（任选其一）：")
    print("    export SC_API_KEY=<你的生图 key>        # 推荐，写入 ~/.zshrc / ~/.bashrc")
    print("    python3 apps/imgen/imgen_cli.py gen ... --key <KEY 或 key 文件路径>")
    print("    keyring（service=vtx-imgen, account=api-key）或 ~/.config/vtx-imgen/secrets.json（0600，{\"api_key\": \"...\"}）")
PY

# 3) 网络连通可选探测（默认跳过；--net 时 GET api.wuyinkeji.com，不产生订单不扣费）
if [[ "${1:-}" == "--net" ]]; then
    echo "-- 网络连通探测（GET api.wuyinkeji.com，不扣费）--"
    if python3 - <<'PY'
import requests, sys
try:
    resp = requests.get("https://api.wuyinkeji.com", timeout=5)
    print(f"✓ 生图 API 可达：HTTP {resp.status_code}")
except Exception as exc:
    print(f"✗ 生图 API 不可达：{exc}")
    sys.exit(1)
PY
    then :; else echo "⚠ 网络探测失败（不影响本地功能；付费生成前需可达）"; fi
else
    echo "-- 网络探测默认跳过（加 --net 参数可开启：GET api.wuyinkeji.com，不扣费）"
fi

# 4) ffmpeg / ffprobe（postprocess cover-crop 依赖；根 init 已探测，此处沿用同一语义）
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    echo "✓ ffmpeg：$(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f1-3)"
    echo "✓ ffprobe：$(command -v ffprobe)"
else
    echo "✗ ffmpeg/ffprobe 缺失（postprocess cover-crop 必需）；请先跑根 init.sh 或系统包管理器安装"
fi

echo "== imgen init 完成（探测仅本地，未发生任何 API 提交/扣费）=="
