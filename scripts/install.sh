#!/usr/bin/env bash
# install.sh —— 一行安装入口(curl | bash 兼容)与本地薄转发双形态。
# 业务权威=install.py(跨平台统一安装器);参数/退出码原样透传;
# 安全契约(归档白名单/staging 事务/SHA256 门)见:python3 scripts/install.py --help
# 校验和仅保证传输完整性,不证明独立发布者身份(#41:正式发布走 keyless
# provenance,验证失败 fail-closed,绝不降级为「仅 hash 通过即装」)。
#
# 形态一·checkout 内运行(bash scripts/install.sh …):零下载,直接转发同目录
#   install.py(BASH_SOURCE 可定位脚本自身)。
# 形态二·curl | bash 管道(BASH_SOURCE 为空——set -u 下取值即炸,红队 pty
#   探针实证):管道里没有本地 install.py,先经 curl 下载到临时文件再执行。
#   下载地址:VTX_INSTALL_PY_URL 整体覆盖;默认 raw GitHub 的
#   VTX_RAW_REF(候选期 README 钉 commit SHA,由入口命令带入;缺省 main)。
#   信任边界:HTTPS 下载通道+仓库当前内容,不自称独立签名验证(#41 另行)。
#
# 交互向导保留(参考 ibus-voice-ime remote-install.sh 范式):管道 stdin 会被
# install.py 判为非 tty 而误降级全默认;有可打开的 /dev/tty 时重挂回终端。
# tty 切换与最终 exec 必须在同一复合语句内:bash 先从管道读完整个复合块再
# 执行,否则切换后 bash 会去终端读脚本的「下一行」导致入口中断。无控制
# 终端(真无人值守)保持原 stdin,由 install.py 自行非终端降级,不追加旗标。
set -uo pipefail

fail() { printf '[install] ✗ %s\n' "$1" >&2; exit 1; }

run_py() {  # $1=install.py 路径,其余为透传参数
  local py_script="$1"; shift
  {
    if [ -e /dev/tty ] && { true < /dev/tty; } 2>/dev/null; then
      exec < /dev/tty
    fi
    exec python3 "$py_script" "$@"
  }
}

if [ "${BASH_SOURCE[0]+x}" ] && [ -n "${BASH_SOURCE[0]}" ]; then
  # 形态一:本地 checkout——零下载直转发。
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [ -f "$here/install.py" ] || fail "checkout 内未找到 install.py(预期与 install.sh 同目录)"
  run_py "$here/install.py" "$@"
else
  # 形态二:curl | bash——下载 install.py 再执行。
  command -v python3 >/dev/null 2>&1 || fail '缺少依赖: python3(≥3.9)'
  command -v curl >/dev/null 2>&1 || fail '缺少依赖: curl'
  ref="${VTX_RAW_REF:-main}"
  url="${VTX_INSTALL_PY_URL:-https://raw.githubusercontent.com/CongliangK/framivra/${ref}/scripts/install.py}"
  tmp="$(mktemp -t vt-install.XXXXXX)" || fail '临时文件创建失败'
  if ! curl -fsSL "$url" -o "$tmp"; then
    rm -f "$tmp"
    fail "install.py 下载失败: $url(可设 VTX_INSTALL_PY_URL 指向可信镜像)"
  fi
  run_py "$tmp" "$@"
fi
