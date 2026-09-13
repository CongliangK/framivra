#!/usr/bin/env python3
"""vtx 薄启动器（v2 §1.4 包化，2026-09-13）：`python3 vtx/vtx.py ...` 直接可跑。

主体在 vtx/cli.py（包内相对导入）。本文件在 import 任何 vtx 模块前，先把脚本
所在目录（resolve 后）移出 sys.path、把 repo 根插到 sys.path[0]——否则 vtx/
目录留在 path 上时 `import vtx` 会命中本目录里的 vtx.py 文件（文件名与包名同名
自遮蔽），包与平铺模块形成双实例（双模块裂分：infra 的 receipt 锁 / hygiene 的
manifest 锁 / loudness_cache 的 flock 落在两个模块对象上，锁形同虚设）。

等价入口：`python3 -m vtx`（vtx/__main__.py）、pip 安装后的 console script `vtx`
（vtx.cli:main）——三通同码同义（ARCHITECTURE §5.2）。
"""
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent          # repo/vtx
for _p in list(sys.path):                         # 移除脚本目录（含相对写法，resolve 比对）
    try:
        if _p and Path(_p).resolve() == _here:
            sys.path.remove(_p)
    except (OSError, ValueError):
        pass                                      # sys.path 里可能有非路径串，跳过
_root = _here.parent                              # repo 根
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from vtx.cli import main

if __name__ == "__main__":
    sys.exit(main())
