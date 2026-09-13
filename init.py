#!/usr/bin/env python3
"""init.py —— v2 薄转发器（转发 vt.py init；ARCHITECTURE §5.1 / §7.1 条目 16）。

原七小节自检 + --fix + --json + config.local.json 幂等生成的全部逻辑已吸收进
vt.py init（含 v2 探测重构：废除对 asr/client.py、imgen/secrets.py 的
spec_from_file_location 复用，asr/imgen 改 config_key 型健康检查）。本文件保留
旧入口兼容（init.sh / 外部脚本 / 文档引用不动），经子进程调用 vt.py init——
参数透传、退出码透传。

子进程纪律（全局 AGENTS.md）：用 shutil.which 定位 python3（本机 AppImage
argv[0] 劫持，sys.executable 指向错误解释器），禁 sys.executable。

用法不变：
  env python3 init.py [--fix] [--json]
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Windows 控制台/管道默认 GBK，✓✗ 中文输出会 UnicodeEncodeError（转发前重配，
# 对齐原 init.py 防护；vt.py 侧同款）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    py = shutil.which("python3") or shutil.which("python")
    if py is None:
        print("✗ 找不到 python3 解释器（PATH）", file=sys.stderr)
        return 1
    r = subprocess.run([py, str(ROOT / "vt.py"), "init", *sys.argv[1:]])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
